import { useEffect, useRef } from "react";
import * as THREE from "three";

const TRACK_TOP = 0.25;
const TRACK_BOTTOM = 0.8;
const BAR_CENTER_Y = (TRACK_TOP + TRACK_BOTTOM) / 2;
const POINTER_HEAD_Y = 0.965;
const POINTER_TAIL_Y = 0.075;
const MARK_Y = 0.86;
const MAX_DPR = 2;
const PLAYHEAD_LINE_PX = 1;
const HOVER_LINE_PX = 0.55;
const PIN_HEAD_PX = 5.5;
const DEFAULT_EDGE_PAD_PX = 8;
const MAX_EDGE_PAD_RATIO = 0.08;
const SPEAKER_GROUP_COUNT = 6;
const BAR_GROUP_KEYS = [
  "ghost",
  "audio",
  ...Array.from({ length: SPEAKER_GROUP_COUNT }, (_, index) => `speaker-${index}`),
  ...Array.from({ length: SPEAKER_GROUP_COUNT }, (_, index) => `synthetic-${index}`),
];

function clamp01(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}

function parseCssColor(value, fallback = "#ffffff") {
  const text = String(value || "").trim();
  if (!text) return fallback;
  if (text.startsWith("#")) return text;

  const rgb = text.match(/rgba?\(([^)]+)\)/i);
  if (rgb) {
    const parts = rgb[1]
      .split(/[\s,\/]+/)
      .map((part) => Number.parseFloat(part))
      .filter((part) => Number.isFinite(part));
    if (parts.length >= 3) {
      return `rgb(${Math.round(parts[0])}, ${Math.round(parts[1])}, ${Math.round(parts[2])})`;
    }
  }

  const srgb = text.match(/color\(\s*srgb\s+([^)]+)\)/i);
  if (srgb) {
    const parts = srgb[1]
      .split(/[\s,\/]+/)
      .map((part) => Number.parseFloat(part))
      .filter((part) => Number.isFinite(part));
    if (parts.length >= 3) {
      return `rgb(${Math.round(parts[0] * 255)}, ${Math.round(parts[1] * 255)}, ${Math.round(parts[2] * 255)})`;
    }
  }

  return text;
}

function colorFromVar(style, name, fallback) {
  return new THREE.Color(parseCssColor(style.getPropertyValue(name), fallback));
}

function mixedColor(base, target, ratio) {
  return base.clone().lerp(target, ratio);
}

function readPalette(host) {
  const shell = host.closest(".app-shell") || document.documentElement;
  const style = getComputedStyle(shell);
  const accent = colorFromVar(style, "--accent", "#e11d48");
  const fg = colorFromVar(style, "--fg", "#ffffff");
  const fg2 = colorFromVar(style, "--fg-2", "#b3b6bf");
  const fg3 = colorFromVar(style, "--fg-3", "#7a7d87");
  const fg4 = colorFromVar(style, "--fg-4", "#4a4d56");
  const surface = colorFromVar(style, "--surface-3", "#1d2026");
  const risk = colorFromVar(style, "--risk", "#f43f5e");
  return {
    accent,
    fg,
    fg2,
    fg3,
    fg4,
    surface,
    risk,
    speakers: [
      accent.clone(),
      mixedColor(accent, fg3, 0.62),
      mixedColor(accent, fg2, 0.38),
      mixedColor(accent, surface, 0.52),
      mixedColor(accent, fg4, 0.64),
      mixedColor(accent, fg4, 0.26),
    ],
  };
}

function disposeObject(object) {
  if (!object) return;
  if (object.geometry) object.geometry.dispose();
  if (object.material) {
    if (Array.isArray(object.material)) {
      object.material.forEach((material) => material.dispose());
    } else {
      object.material.dispose();
    }
  }
}

function render(runtime) {
  if (!runtime) return;
  runtime.renderer.render(runtime.scene, runtime.camera);
}

function resizeRuntime(runtime, host) {
  const rect = host.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  runtime.width = width;
  runtime.height = height;
  runtime.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_DPR));
  runtime.renderer.setSize(width, height, false);
  runtime.camera.left = 0;
  runtime.camera.right = 1;
  runtime.camera.top = 1;
  runtime.camera.bottom = 0;
  runtime.camera.updateProjectionMatrix();
  render(runtime);
}

function edgePadRatio(runtime) {
  const width = Math.max(1, runtime.width || 1);
  const minimumPadPx = PIN_HEAD_PX / 2 + 2;
  const padPx = Math.max(minimumPadPx, Number(runtime.edgePadPx) || DEFAULT_EDGE_PAD_PX);
  return Math.min(MAX_EDGE_PAD_RATIO, padPx / width);
}

function drawWidthRatio(runtime) {
  return Math.max(0.1, 1 - edgePadRatio(runtime) * 2);
}

function mapTimelineX(runtime, ratio) {
  const pad = edgePadRatio(runtime);
  return pad + clamp01(ratio) * (1 - pad * 2);
}

function createRuntime(host) {
  const renderer = new THREE.WebGLRenderer({
    alpha: true,
    antialias: false,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.className = "tl-three-canvas";
  renderer.domElement.setAttribute("aria-hidden", "true");
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(0, 1, 1, 0, -10, 10);
  camera.position.z = 1;

  const palette = readPalette(host);
  const progressMaterial = new THREE.MeshBasicMaterial({
    color: palette.accent,
    transparent: true,
    opacity: 0.12,
    depthWrite: false,
  });
  const progressMesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), progressMaterial);
  progressMesh.position.set(0.5, BAR_CENTER_Y, -2);
  progressMesh.scale.set(0, TRACK_BOTTOM - TRACK_TOP + 0.18, 1);
  scene.add(progressMesh);

  const barGeometry = new THREE.PlaneGeometry(1, 1);

  const pointerMaterial = new THREE.MeshBasicMaterial({
    color: palette.fg,
    transparent: true,
    opacity: 0.96,
    depthWrite: false,
  });
  const hoverMaterial = new THREE.MeshBasicMaterial({
    color: palette.fg,
    transparent: true,
    opacity: 0.13,
    depthWrite: false,
  });
  const lineGeometry = new THREE.PlaneGeometry(1, 1);
  const playheadLine = new THREE.Mesh(lineGeometry, pointerMaterial);
  const hoverLine = new THREE.Mesh(lineGeometry.clone(), hoverMaterial);
  const knob = new THREE.Mesh(new THREE.CircleGeometry(1, 28), pointerMaterial);
  playheadLine.renderOrder = 10;
  hoverLine.renderOrder = 9;
  knob.renderOrder = 11;
  scene.add(hoverLine);
  scene.add(playheadLine);
  scene.add(knob);

  const markerGroup = new THREE.Group();
  markerGroup.renderOrder = 8;
  scene.add(markerGroup);

  const runtime = {
    renderer,
    scene,
    camera,
    palette,
    barGeometry,
    barMeshes: new Map(),
    barMaterials: new Map(),
    barCapacity: 0,
    progressMesh,
    progressMaterial,
    playheadLine,
    hoverLine,
    knob,
    pointerMaterial,
    hoverMaterial,
    markerGroup,
    markerGeometry: new THREE.CircleGeometry(1, 18),
    markerMaterials: [],
    dummy: new THREE.Object3D(),
    edgePadPx: DEFAULT_EDGE_PAD_PX,
  };
  resizeRuntime(runtime, host);
  return runtime;
}

function updatePalette(runtime, host) {
  runtime.palette = readPalette(host);
  runtime.progressMaterial.color.copy(runtime.palette.accent);
  runtime.pointerMaterial.color.copy(runtime.palette.fg);
  runtime.hoverMaterial.color.copy(runtime.palette.fg);
  for (const [key, material] of runtime.barMaterials.entries()) {
    material.color.copy(barGroupColor(key, runtime.palette));
  }
}

function ensureBarMeshes(runtime, capacity) {
  if (runtime.barCapacity === capacity && runtime.barMeshes.size) return runtime.barMeshes;
  for (const mesh of runtime.barMeshes.values()) {
    runtime.scene.remove(mesh);
    mesh.dispose?.();
  }
  for (const material of runtime.barMaterials.values()) {
    material.dispose();
  }
  runtime.barMeshes.clear();
  runtime.barMaterials.clear();

  for (const key of BAR_GROUP_KEYS) {
    const material = new THREE.MeshBasicMaterial({
      color: barGroupColor(key, runtime.palette),
      transparent: true,
      opacity: key.startsWith("synthetic") ? 0.64 : key === "ghost" ? 0.58 : 0.98,
      depthWrite: false,
    });
    const mesh = new THREE.InstancedMesh(runtime.barGeometry, material, capacity);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.count = 0;
    mesh.renderOrder = 2;
    mesh.visible = false;
    runtime.scene.add(mesh);
    runtime.barMaterials.set(key, material);
    runtime.barMeshes.set(key, mesh);
  }
  runtime.barCapacity = capacity;
  return runtime.barMeshes;
}

function barGroupColor(key, palette) {
  if (key === "audio") return mixedColor(palette.accent, palette.surface, 0.42);
  if (key === "ghost") return mixedColor(palette.fg4, palette.surface, 0.45);
  const synthetic = key.startsWith("synthetic-");
  const speakerIndex = Number(key.split("-").at(-1)) || 0;
  const color = palette.speakers[speakerIndex % palette.speakers.length].clone();
  return synthetic ? mixedColor(color, palette.surface, 0.42) : color;
}

function barGroupKey(bar) {
  if (!bar?.active && bar?.hasAudio) return "audio";
  if (!bar?.active) return "ghost";
  const speakerIndex = Number.isFinite(Number(bar.speakerIndex)) && Number(bar.speakerIndex) >= 0
    ? Number(bar.speakerIndex) % SPEAKER_GROUP_COUNT
    : 0;
  return bar.hasAudio ? `speaker-${speakerIndex}` : `synthetic-${speakerIndex}`;
}

function updateBars(runtime, bars = [], loadingProgress = 0) {
  const meshes = ensureBarMeshes(runtime, bars.length);
  const offsets = new Map(BAR_GROUP_KEYS.map((key) => [key, 0]));
  const count = Math.max(1, bars.length);
  const laneWidth = drawWidthRatio(runtime);
  const gap = Math.min(0.0024, laneWidth * 0.16 / count);
  const width = Math.max(1 / Math.max(1, runtime.width || 1), laneWidth / count - gap);
  const maxHeight = TRACK_BOTTOM - TRACK_TOP;

  for (let index = 0; index < bars.length; index += 1) {
    const bar = bars[index] || {};
    const amp = clamp01(bar.amplitude);
    const height = Math.max(0.014, 0.025 + amp * maxHeight);
    const x = mapTimelineX(runtime, (index + 0.5) / count);
    runtime.dummy.position.set(x, BAR_CENTER_Y, 0);
    runtime.dummy.scale.set(width, height, 1);
    runtime.dummy.updateMatrix();
    const key = barGroupKey(bar);
    const mesh = meshes.get(key);
    const slot = offsets.get(key) || 0;
    mesh.setMatrixAt(slot, runtime.dummy.matrix);
    offsets.set(key, slot + 1);
  }

  for (const [key, mesh] of meshes.entries()) {
    const used = offsets.get(key) || 0;
    mesh.count = used;
    mesh.visible = used > 0;
    mesh.instanceMatrix.needsUpdate = true;
  }

  const progress = clamp01(loadingProgress);
  runtime.progressMesh.visible = progress > 0 && progress < 1;
  runtime.progressMesh.position.x = mapTimelineX(runtime, progress / 2);
  runtime.progressMesh.scale.x = laneWidth * progress;
}

function clearMarkers(runtime) {
  for (const child of [...runtime.markerGroup.children]) {
    runtime.markerGroup.remove(child);
    if (child.material && !runtime.markerMaterials.includes(child.material)) {
      child.material.dispose();
    }
  }
  runtime.markerMaterials.forEach((material) => material.dispose());
  runtime.markerMaterials = [];
}

function updateMarkers(runtime, marks = []) {
  clearMarkers(runtime);
  for (const mark of marks.slice(0, 14)) {
    const type = mark?.type || "gap";
    const material = new THREE.MeshBasicMaterial({
      color: type === "question"
        ? runtime.palette.risk
        : type === "speaker"
          ? runtime.palette.speakers[1]
          : runtime.palette.fg4,
      transparent: true,
      opacity: type === "gap" ? 0.55 : 0.88,
      depthWrite: false,
    });
    runtime.markerMaterials.push(material);
    const marker = new THREE.Mesh(runtime.markerGeometry, material);
    const x = mapTimelineX(runtime, mark?.ratio);
    marker.position.set(x, MARK_Y, 4);
    marker.scale.set(type === "gap" ? 0.0048 : 0.0068, type === "gap" ? 0.0048 : 0.0068, 1);
    runtime.markerGroup.add(marker);
  }
}

function updatePointers(runtime, playheadRatio, hoverRatio) {
  const pointerHeight = POINTER_HEAD_Y - POINTER_TAIL_Y;
  const pointerCenterY = POINTER_TAIL_Y + pointerHeight / 2;
  const lineWidth = PLAYHEAD_LINE_PX / Math.max(1, runtime.width || 1);
  const hoverWidth = HOVER_LINE_PX / Math.max(1, runtime.width || 1);
  const knobWidth = PIN_HEAD_PX / Math.max(1, runtime.width || 1);
  const knobHeight = PIN_HEAD_PX / Math.max(1, runtime.height || 1);
  const playheadX = mapTimelineX(runtime, playheadRatio);
  runtime.playheadLine.position.set(playheadX, pointerCenterY, 7);
  runtime.playheadLine.scale.set(lineWidth, pointerHeight, 1);
  runtime.knob.position.set(playheadX, POINTER_HEAD_Y, 8);
  runtime.knob.scale.set(knobWidth, knobHeight, 1);

  const hoverNumber = Number(hoverRatio);
  runtime.hoverLine.visible = Number.isFinite(hoverNumber);
  if (runtime.hoverLine.visible) {
    const hoverX = mapTimelineX(runtime, hoverNumber);
    runtime.hoverLine.position.set(hoverX, pointerCenterY, 6);
    runtime.hoverLine.scale.set(hoverWidth, pointerHeight, 1);
  }
}

function destroyRuntime(runtime, host) {
  clearMarkers(runtime);
  for (const mesh of runtime.barMeshes.values()) {
    runtime.scene.remove(mesh);
    mesh.dispose?.();
  }
  for (const material of runtime.barMaterials.values()) {
    material.dispose();
  }
  runtime.barGeometry.dispose();
  runtime.markerGeometry.dispose();
  disposeObject(runtime.progressMesh);
  disposeObject(runtime.playheadLine);
  disposeObject(runtime.hoverLine);
  disposeObject(runtime.knob);
  runtime.renderer.dispose();
  runtime.renderer.domElement.remove();
  host.replaceChildren();
}

export function TimelineThreeCanvas({
  bars,
  marks,
  playheadRatio,
  hoverRatio,
  loadingProgress = 0,
  edgePadPx = DEFAULT_EDGE_PAD_PX,
}) {
  const hostRef = useRef(null);
  const runtimeRef = useRef(null);
  const latestPropsRef = useRef({ bars, marks, playheadRatio, hoverRatio, loadingProgress, edgePadPx });

  useEffect(() => {
    latestPropsRef.current = { bars, marks, playheadRatio, hoverRatio, loadingProgress, edgePadPx };
  }, [bars, edgePadPx, hoverRatio, loadingProgress, marks, playheadRatio]);

  const applyProps = (runtime, host, props) => {
    runtime.edgePadPx = props.edgePadPx;
    updatePalette(runtime, host);
    updateBars(runtime, props.bars, props.loadingProgress);
    updateMarkers(runtime, props.marks);
    updatePointers(runtime, props.playheadRatio, props.hoverRatio);
    render(runtime);
  };

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const runtime = createRuntime(host);
    runtimeRef.current = runtime;

    const resizeObserver = new ResizeObserver(() => {
      resizeRuntime(runtime, host);
      applyProps(runtime, host, latestPropsRef.current);
    });
    resizeObserver.observe(host);

    return () => {
      resizeObserver.disconnect();
      destroyRuntime(runtime, host);
      runtimeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    const runtime = runtimeRef.current;
    if (!host || !runtime) return;
    applyProps(runtime, host, latestPropsRef.current);
  }, [bars, edgePadPx, hoverRatio, loadingProgress, marks, playheadRatio]);

  return <div className="tl-three-host" ref={hostRef} aria-hidden="true" />;
}
