import { invoke } from "@tauri-apps/api/core";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DeleteMeetingDialog } from "@/components/meeting/DeleteMeetingDialog";
import { MeetingTimeline } from "@/components/meeting/MeetingTimeline";
import { MeetingPropertiesDialog } from "@/components/meeting/MeetingPropertiesDialog";
import { ModelLoadDialog } from "@/components/meeting/ModelLoadDialog";
import { NotesPane } from "@/components/meeting/NotesPane";
import { SettingsDialog } from "@/components/meeting/SettingsDialog";
import { Sidebar } from "@/components/meeting/Sidebar";
import { StartupBanner } from "@/components/meeting/StartupBanner";
import { TopBar } from "@/components/meeting/TopBar";
import { TranscriptPane } from "@/components/meeting/TranscriptPane";
import { clampAppearance, loadAppearance, saveAppearance } from "@/lib/appearance";
import { I18nProvider, loadLocale, saveLocale } from "@/lib/i18n";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8788";
const TAURI_AVAILABLE = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
const MAX_SEGMENT_MS = 15000;
const LIVE_WAVEFORM_BAR_COUNT = 256;
const RECORDING_CONFIG_STORAGE_KEY = "voice-meeting-recording-config";
const MIC_DEVICE_STORAGE_KEY = "voice-meeting-mic-device";
const DEFAULT_LLM_CONFIG = {
  provider: "vibearound",
  openai_chat: {
    base_url: "",
    model: "",
    has_api_key: false,
  },
};
const WAVE_PATTERN = [0.35, 0.62, 0.48, 0.84, 0.58, 1.0, 0.72, 0.42, 0.68, 0.92, 0.54, 0.78];
const LANGUAGE_OPTIONS = [
  ["auto", "自动多语种"],
  ["mixed", "中英优先"],
  ["zh", "中文"],
  ["en", "English"],
  ["ja", "日本語"],
  ["ko", "한국어"],
  ["fr", "Français"],
  ["de", "Deutsch"],
  ["es", "Español"],
  ["ru", "Русский"],
  ["pt", "Português"],
];
const INPUT_GAIN_OPTIONS = [
  [1, "原声"],
  [1.3, "轻度增强"],
  [1.6, "会议增强"],
  [2, "远距增强"],
];
const DEFAULT_RECORDING_CONFIG = {
  language: "mixed",
  asrModel: "small",
  speakerMode: "voiceprint",
  maxSegmentMs: MAX_SEGMENT_MS,
  inputGain: 1,
};
const SPEAKER_MODE_OPTIONS = [
  ["voiceprint", "声纹跟踪"],
  ["diarization", "高精度分离"],
  ["off", "不区分"],
];
const FASTER_ASR_MODEL_ORDER = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"];
const MLX_ASR_MODEL_ORDER = FASTER_ASR_MODEL_ORDER.map((model) => `mlx-${model}`);
const ASR_MODEL_ORDER = [...FASTER_ASR_MODEL_ORDER, ...MLX_ASR_MODEL_ORDER];
const PIPELINE_STEPS = ["上传", "转码", "识别", "说话人", "纪要"];

function languageName(value) {
  const option = LANGUAGE_OPTIONS.find(([key]) => key === value);
  return option?.[1] || value || "自动";
}

function speakerModeName(value) {
  const option = SPEAKER_MODE_OPTIONS.find(([key]) => key === value);
  return option?.[1] || value || "声纹跟踪";
}

function clampRecordingConfig(value = {}) {
  const config = { ...DEFAULT_RECORDING_CONFIG, ...value };
  const maxSegmentMs = Number(config.maxSegmentMs);
  const inputGain = Number(config.inputGain);
  return {
    language: LANGUAGE_OPTIONS.some(([key]) => key === config.language) ? config.language : "mixed",
    asrModel: ASR_MODEL_ORDER.includes(config.asrModel) ? config.asrModel : "small",
    speakerMode: SPEAKER_MODE_OPTIONS.some(([key]) => key === config.speakerMode)
      ? config.speakerMode
      : "voiceprint",
    maxSegmentMs: Number.isFinite(maxSegmentMs) ? Math.min(30000, Math.max(8000, maxSegmentMs)) : MAX_SEGMENT_MS,
    inputGain: Number.isFinite(inputGain) ? Math.min(2.5, Math.max(0.8, inputGain)) : DEFAULT_RECORDING_CONFIG.inputGain,
  };
}

function loadRecordingConfig() {
  try {
    const raw = window.localStorage.getItem(RECORDING_CONFIG_STORAGE_KEY);
    return clampRecordingConfig(raw ? JSON.parse(raw) : DEFAULT_RECORDING_CONFIG);
  } catch {
    return clampRecordingConfig(DEFAULT_RECORDING_CONFIG);
  }
}

function saveRecordingConfig(value) {
  try {
    window.localStorage.setItem(RECORDING_CONFIG_STORAGE_KEY, JSON.stringify(clampRecordingConfig(value)));
  } catch {
    // Local persistence is best-effort.
  }
}

function loadSelectedMicId() {
  try {
    return window.localStorage.getItem(MIC_DEVICE_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function saveSelectedMicId(value) {
  try {
    if (value) {
      window.localStorage.setItem(MIC_DEVICE_STORAGE_KEY, value);
    } else {
      window.localStorage.removeItem(MIC_DEVICE_STORAGE_KEY);
    }
  } catch {
    // Local persistence is best-effort.
  }
}

function micDeviceLabel(device, index) {
  if (device?.label) return device.label;
  if (device?.deviceId === "default") return "系统默认麦克风";
  return `麦克风 ${index + 1}`;
}

function inputGainName(value) {
  const numeric = Number(value);
  const option = INPUT_GAIN_OPTIONS.find(([key]) => key === numeric);
  return option?.[1] || `${numeric.toFixed(1)}x`;
}

function bytesLabel(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)}GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)}MB`;
  return `${Math.round(bytes / 1024)}KB`;
}

function progressPercent(job) {
  const value = Number(job?.progress);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value * 100)));
}

function downloadBytesText(job) {
  const downloaded = Number(job?.downloaded_bytes);
  const total = Number(job?.total_bytes);
  if (Number.isFinite(downloaded) && downloaded > 0 && Number.isFinite(total) && total > 0) {
    return `${bytesLabel(downloaded)} / ${bytesLabel(total)}`;
  }
  if (Number.isFinite(total) && total > 0) return bytesLabel(total);
  return "";
}

function downloadStageText(job) {
  const parts = [job?.stage, job?.file, downloadBytesText(job)]
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  return parts.join(" · ");
}

function formatTime(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function titleFromAudioFile(file) {
  const name = file?.name || "导入音频";
  return name.replace(/\.[^/.]+$/, "").trim() || name;
}

function notesOnlyMarkdown(markdown) {
  return String(markdown || "").replace(/\n+##\s*原始转写\s*[\s\S]*$/u, "").trim();
}

function formatOffset(value) {
  if (!Number.isFinite(value)) return "";
  const totalSeconds = Math.max(0, Math.floor(value / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatAsrDisplay(asr, fallback) {
  if (!asr) return languageName(fallback);
  const topLanguages = Array.isArray(asr.top_languages) ? asr.top_languages.slice(0, 3) : [];
  if (topLanguages.length > 0) {
    return topLanguages
      .map((item) => `${languageName(item.language)} ${Math.round((item.probability || 0) * 100)}%`)
      .join(" · ");
  }
  if (asr.language) {
    const confidence = Number.isFinite(asr.language_probability)
      ? ` ${Math.round(asr.language_probability * 100)}%`
      : "";
    return `${languageName(asr.language)}${confidence}`;
  }
  return languageName(fallback);
}

function transcriptVersionName(version, fallbackId = "auto") {
  const kind = version?.kind || (fallbackId === "auto" ? "initial" : "");
  const names = {
    initial: "原始稿",
    asr: "重新识别稿",
    speaker: "说话人修正版",
    merge: "段落整理稿",
    "llm-repair": "文字精修稿",
    "manual-edit": "手动修改稿",
    manual: "手动稿",
  };
  return names[kind] || version?.label || fallbackId || "当前稿";
}

function transcriptVersionOption(version) {
  const name = transcriptVersionName(version, version?.id);
  const time = formatTime(version?.created_at);
  return time ? `${name} · ${time}` : name;
}

function transcriptVersionHint(version, editable) {
  if (editable) return "这份可以直接改文字和人名。";
  const name = transcriptVersionName(version, version?.id);
  if ((version?.kind || "initial") === "initial") return `${name}会保留不动；需要改字时先创建副本。`;
  return `${name}会保留不动；需要手动改时先创建副本。`;
}

function asrModelName(model) {
  if (String(model || "").startsWith("mlx-")) {
    return `MLX ${asrModelName(String(model).slice(4))}`;
  }
  const names = {
    tiny: "轻量识别",
    base: "快速识别",
    small: "标准识别",
    medium: "高精度识别",
    "large-v3": "最高精度识别",
    "large-v3-turbo": "高精度加速",
  };
  return names[model] || model;
}

function asrBackendLabel(value) {
  if (value === "mlx") return "Mac MLX 模型";
  if (value === "faster-whisper") return "通用模型";
  return value || "识别模型";
}

function meetingStatusName(statusValue) {
  const labels = {
    recording: "录音中",
    stopped: "已停止",
    completed: "已完成",
    ready: "待开始",
  };
  return labels[statusValue] || "待开始";
}

function serviceStatusText(value) {
  const labels = {
    ready: "已连接",
    starting: "启动中",
    checking: "检查中",
    fallback: "可用",
    offline: "未连接",
    unknown: "未确认",
  };
  return labels[value] || "未确认";
}

function recognitionStatusText(modelStatus) {
  if (modelStatus?.loading) return "准备中";
  if (modelStatus?.loaded) return "已就绪";
  return "待启用";
}

function assistantRouteText(llm) {
  if (!llm?.transport && !llm?.route && !llm?.provider) return "待连接";
  const route = String(llm?.route || llm?.transport || llm?.provider || "");
  if (llm?.provider === "openai-chat" || route.includes("openai-chat")) {
    return llm?.model ? `接口 · ${llm.model}` : "接口";
  }
  if (route.includes("web-chat")) return "Codex 通道";
  return "VibeAround";
}

function assistantStatusText(llm) {
  const text = assistantRouteText(llm);
  if (!text || text === "待连接") return "待配置";
  return text;
}

function normalizeLlmConfig(value) {
  const openaiChat = value?.openai_chat || {};
  return {
    provider: value?.provider === "openai-chat" ? "openai-chat" : "vibearound",
    openai_chat: {
      base_url: String(openaiChat.base_url || ""),
      model: String(openaiChat.model || ""),
      has_api_key: Boolean(openaiChat.has_api_key),
    },
  };
}

function llmDraftFromConfig(value) {
  const config = normalizeLlmConfig(value);
  return {
    provider: config.provider,
    baseUrl: config.openai_chat.base_url,
    apiKey: "",
    model: config.openai_chat.model,
  };
}

function promptDraftsFromConfig(value) {
  const prompts = Array.isArray(value?.prompts) ? value.prompts : [];
  return Object.fromEntries(prompts.map((item) => [item.key, item.value || item.default || ""]));
}

function llmProviderLabel(value) {
  if (value === "openai-chat") return "接口";
  return "VibeAround";
}

function speakerStatusText(runtime) {
  if (runtime?.speaker_tracking?.available) return "可识别";
  if (runtime?.diarization?.available) return "可区分";
  return "未启用";
}

function userFriendlyError(message) {
  const raw = String(message || "").trim();
  if (!raw) return "操作没有完成，请稍后重试。";

  let detail = raw;
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed?.detail === "string") {
      detail = parsed.detail;
    } else if (Array.isArray(parsed?.detail)) {
      detail = parsed.detail.map((item) => item?.msg || item).join("；");
    }
  } catch {
    // Keep the raw message.
  }

  const lower = detail.toLowerCase();
  if (lower.includes("meeting not found")) return "找不到这场会议，可能已经被删除。";
  if (lower.includes("transcript version not found") || lower.includes("source transcript version not found")) {
    return "找不到这份稿件，请刷新后再试。";
  }
  if (lower.includes("segment not found")) return "找不到这段文字，请刷新后再试。";
  if (lower.includes("speaker not found")) return "当前稿件里没有找到这个说话人。";
  if (lower.includes("manual edit version") || lower.includes("editable")) {
    return "请先创建可编辑副本，再修改文字或说话人。";
  }
  if (lower.includes("prompt cannot be empty")) return "请输入要生成的内容。";
  if (lower.includes("no transcript or summary")) return "这场会议还没有可用内容，录音或导入音频后再试。";
  if (lower.includes("timed out") || lower.includes("timeout")) return "生成时间太久，已停止等待。请稍后重试。";
  if (lower.includes("empty audio")) return "这段音频为空，请重新录制或导入。";
  if (lower.includes("unsupported asr language")) return "当前语言暂不支持，请换一种语言设置。";
  if (lower.includes("asr model is not available locally")) return "本地还没有这套识别资源，请选择已有的识别方式。";
  if (lower.includes("unsupported asr model")) return "当前识别方式不可用，请换一个选项。";
  if (lower.includes("ffmpeg") || lower.includes("invalid data") || lower.includes("error opening input")) {
    return "音频文件无法读取，请换一个常见格式，或重新录制。";
  }
  if (lower.includes("audio file not found")) return "找不到本地音频文件，可能已经被移动或删除。";
  if (lower.includes("chunk not found")) return "找不到这段音频，请刷新后再试。";
  if (lower.includes("web audio") || lower.includes("audio playback") || lower.includes("audio decoding")) {
    return "当前浏览器不支持这个音频操作，请换一个浏览器或重新导入音频。";
  }
  if (
    lower.includes("notallowederror")
    || lower.includes("not allowed by the user agent")
    || lower.includes("permission denied")
    || lower.includes("permission dismissed")
    || lower.includes("麦克风权限")
  ) {
    return "麦克风权限未开启。请到系统设置的麦克风权限里允许 VoiceMeeting，然后重新开始录音。";
  }
  if (lower.includes("playback")) return "回放加载失败，请刷新后再试。";
  if (lower.includes("vibearound") || lower.includes("bridge") || lower.includes("profile")) {
    return "会议助手暂时不可用，请确认 VibeAround 正在运行后再试。";
  }
  if (/^http\s+\d+/i.test(detail) || /^\d{3}\s/.test(detail)) {
    return "本地服务返回异常，请稍后重试。";
  }
  return detail;
}

function chunkStageLabel(statusValue) {
  const labels = {
    saved: "排队",
    converting: "准备音频",
    transcribing: "识别",
    diarizing: "分辨说话人",
    identifying_speakers: "匹配说话人",
    done: "完成",
    error: "出错",
  };
  return labels[statusValue] || statusValue || "待机";
}

function runtimeLine(runtime, pendingChunks) {
  const reprocess = runtime?.reprocess;
  if (reprocess && ["queued", "running"].includes(reprocess.status)) {
    const total = Number(reprocess.total);
    const progress = Number(reprocess.progress);
    const stage = String(reprocess.stage || "处理中");
    const embedded = stage.match(/^(.*?)(\d+)\s*\/\s*(\d+)\s*$/u);
    if (embedded) {
      return `${embedded[1].trim() || "处理中"}：第 ${embedded[2]} / ${embedded[3]} 段`;
    }
    if (Number.isFinite(total) && total > 1) {
      const done = Math.min(total, Math.max(0, progress || 0));
      return `${stage}：已完成 ${done} / ${total} 段`;
    }
    return stage;
  }
  const active = runtime?.active_chunks || [];
  if (pendingChunks > 0 && active.length === 0) return "保存音频中";
  if (active.length > 0) {
    const first = active[0];
    return `第 ${first.seq || active.length} 段 ${chunkStageLabel(first.status)}`;
  }
  return "";
}

function pipelineStepIndex(runtime, pendingChunks, pipelineStatus, finalizing, finalNotesWorking) {
  const reprocess = runtime?.reprocess;
  if (reprocess && ["queued", "running"].includes(reprocess.status)) {
    const level = String(reprocess.level || "");
    if (level === "notes") return 4;
    if (level === "speaker") return 3;
    if (level === "asr") return 2;
    return -1;
  }

  const active = runtime?.active_chunks || [];
  if (active.length > 0) {
    const statusValue = active[0]?.status;
    if (statusValue === "saved") return 0;
    if (statusValue === "converting") return 1;
    if (statusValue === "transcribing") return 2;
    if (statusValue === "diarizing" || statusValue === "identifying_speakers") return 3;
  }
  if (pendingChunks > 0) return 0;

  if (finalizing || finalNotesWorking) return 4;

  const text = String(pipelineStatus || "");
  if (text.includes("纪要") || text.includes("生成")) return 4;
  if (text.includes("说话人")) return 3;
  if (text.includes("识别")) return 2;
  if (text.includes("转码") || text.includes("准备音频")) return 1;
  if (text.includes("上传") || text.includes("整理") || text.includes("录音") || text.includes("保存音频")) return 0;
  return -1;
}

function concatFloat32(chunks) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const output = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.length;
  }
  return output;
}

function writeAscii(view, offset, value) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: "audio/wav" });
}

function applyInputGain(input, gain) {
  const normalizedGain = Number.isFinite(gain) ? Math.min(2.5, Math.max(0.8, gain)) : 1;
  const frame = new Float32Array(input.length);
  if (Math.abs(normalizedGain - 1) < 0.001) {
    frame.set(input);
    return frame;
  }
  for (let index = 0; index < input.length; index += 1) {
    frame[index] = Math.max(-1, Math.min(1, input[index] * normalizedGain));
  }
  return frame;
}

function compressWaveformBars(values, targetCount = LIVE_WAVEFORM_BAR_COUNT) {
  if (values.length <= targetCount) return values.slice();
  return Array.from({ length: targetCount }, (_, index) => {
    const start = Math.floor(index * values.length / targetCount);
    const end = Math.max(start + 1, Math.floor((index + 1) * values.length / targetCount));
    let peak = 0;
    for (let item = start; item < end; item += 1) {
      peak = Math.max(peak, values[item] || 0);
    }
    return peak;
  });
}

function audioBufferToMono(audioBuffer) {
  const length = audioBuffer.length;
  const channelCount = audioBuffer.numberOfChannels;
  const output = new Float32Array(length);
  for (let channel = 0; channel < channelCount; channel += 1) {
    const data = audioBuffer.getChannelData(channel);
    for (let index = 0; index < length; index += 1) {
      output[index] += data[index] / channelCount;
    }
  }
  return output;
}

function makeFixedChunks(samples, sampleRate, config = DEFAULT_RECORDING_CONFIG) {
  const normalized = clampRecordingConfig(config);
  const maxSamples = Math.round(sampleRate * normalized.maxSegmentMs / 1000);
  const chunks = [];
  for (let start = 0; start < samples.length; start += maxSamples) {
    const end = Math.min(samples.length, start + maxSamples);
    chunks.push({
      samples: samples.slice(start, end),
      startedAtMs: start * 1000 / sampleRate,
      endedAtMs: end * 1000 / sampleRate,
      cutReason: "导入固定切片",
    });
  }
  return chunks;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(userFriendlyError(text || `${response.status} ${response.statusText}`));
  }
  return response.json();
}

function safeDownloadName(value, fallback = "meeting") {
  return (String(value || fallback)
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim() || fallback);
}

async function fetchTextFile(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(userFriendlyError(text || `${response.status} ${response.statusText}`));
  }
  return response.text();
}

async function saveTextFile(filename, content) {
  if (TAURI_AVAILABLE) {
    const result = await invoke("save_markdown_file", {
      defaultFilename: filename,
      content,
    });
    return Boolean(result?.saved);
  }

  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 1000);
  return true;
}

async function requestNativeMicrophonePermission() {
  if (!TAURI_AVAILABLE) return;
  try {
    await invoke("request_microphone_permission");
  } catch (err) {
    throw new Error(userFriendlyError(err));
  }
}

async function nativeBackendStatus() {
  if (!TAURI_AVAILABLE) return null;
  try {
    return await invoke("backend_status");
  } catch {
    return null;
  }
}

async function readSse(response, handlers) {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const emit = (raw) => {
    const clean = raw.trim();
    if (!clean) return;
    let event = "message";
    const dataLines = [];
    for (const line of clean.split("\n")) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    const payloadText = dataLines.join("\n");
    const payload = payloadText ? JSON.parse(payloadText) : {};
    handlers[event]?.(payload);
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      emit(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
  }
  buffer += decoder.decode();
  emit(buffer);
}

function transcriptParts(item) {
  if (Array.isArray(item?.parts) && item.parts.length > 0) {
    return item.parts;
  }
  return [
    {
      id: item?.id,
      text: item?.text || "",
      start_ms: item?.start_ms,
      end_ms: item?.end_ms,
    },
  ];
}

function playbackBounds(chunk) {
  const startedAtMs = Number(chunk?.started_at_ms);
  const trimStartMs = Number(chunk?.trim_start_ms);
  const endedAtMs = Number(chunk?.ended_at_ms);
  const playableDurationMs = Number(chunk?.playable_duration_ms);
  const startMs = (Number.isFinite(startedAtMs) ? startedAtMs : 0)
    + (Number.isFinite(trimStartMs) ? trimStartMs : 0);
  let endMs = Number.isFinite(playableDurationMs) && playableDurationMs > 0
    ? startMs + playableDurationMs
    : endedAtMs;
  if (!Number.isFinite(endMs) || endMs < startMs) {
    const durationMs = Number(chunk?.duration_ms);
    endMs = startMs + (Number.isFinite(durationMs) ? durationMs : 0);
  }
  return { startMs, endMs };
}

function findPlaybackChunkIndex(chunks, startAtMs) {
  const targetMs = Number.isFinite(startAtMs) ? startAtMs : 0;
  const containing = chunks.findIndex((chunk) => {
    const bounds = playbackBounds(chunk);
    return targetMs >= bounds.startMs && targetMs < bounds.endMs;
  });
  if (containing >= 0) return containing;
  const next = chunks.findIndex((chunk) => playbackBounds(chunk).endMs > targetMs);
  return next >= 0 ? next : 0;
}

function App() {
  const [meeting, setMeeting] = useState(null);
  const [meetings, setMeetings] = useState([]);
  const [title, setTitle] = useState("今天的会议");
  const [titleSaving, setTitleSaving] = useState(false);
  const [titleSavedAt, setTitleSavedAt] = useState(0);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [pendingChunks, setPendingChunks] = useState(0);
  const [error, setError] = useState("");
  const [asrLanguage, setAsrLanguage] = useState(DEFAULT_RECORDING_CONFIG.language);
  const [lastAsr, setLastAsr] = useState(null);
  const [modelStatus, setModelStatus] = useState(null);
  const [modelCatalog, setModelCatalog] = useState(null);
  const [recordingConfig, setRecordingConfig] = useState(loadRecordingConfig);
  const [pipelineStatus, setPipelineStatus] = useState("待机");
  const [llmStatus, setLlmStatus] = useState({ provider: "VibeAround", transport: "local-api" });
  const [llmConfig, setLlmConfig] = useState(DEFAULT_LLM_CONFIG);
  const [llmConfigDraft, setLlmConfigDraft] = useState(() => llmDraftFromConfig(DEFAULT_LLM_CONFIG));
  const [llmConfigSaving, setLlmConfigSaving] = useState(false);
  const [llmConfigError, setLlmConfigError] = useState("");
  const [promptConfig, setPromptConfig] = useState(null);
  const [promptDrafts, setPromptDrafts] = useState({});
  const [promptConfigSaving, setPromptConfigSaving] = useState(false);
  const [promptConfigError, setPromptConfigError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState("recording");
  const [propertiesOpen, setPropertiesOpen] = useState(false);
  const [propertiesDraft, setPropertiesDraft] = useState({ title: "", description: "" });
  const [propertiesSaving, setPropertiesSaving] = useState(false);
  const [propertiesError, setPropertiesError] = useState("");
  const [micDevices, setMicDevices] = useState([]);
  const [selectedMicId, setSelectedMicId] = useState(loadSelectedMicId);
  const [activeMicLabel, setActiveMicLabel] = useState("");
  const [vadLevel, setVadLevel] = useState(0);
  const [liveWaveformBars, setLiveWaveformBars] = useState([]);
  const [liveRecordingMs, setLiveRecordingMs] = useState(0);
  const [importingAudio, setImportingAudio] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState("");
  const [modelLoadState, setModelLoadState] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [playbackBusy, setPlaybackBusy] = useState(false);
  const [playbackStatus, setPlaybackStatus] = useState("未播放");
  const [playbackMeetingId, setPlaybackMeetingId] = useState(null);
  const [playbackPositionMs, setPlaybackPositionMs] = useState(null);
  const [playbackDurationMs, setPlaybackDurationMs] = useState(0);
  const [playbackPreview, setPlaybackPreview] = useState({ meetingId: null, positionMs: null });
  const [runtimeStatus, setRuntimeStatus] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [reprocessBusy, setReprocessBusy] = useState(false);
  const [editBusy, setEditBusy] = useState(false);
  const [editingSegmentId, setEditingSegmentId] = useState("");
  const [segmentDrafts, setSegmentDrafts] = useState({});
  const [speakerRenameFrom, setSpeakerRenameFrom] = useState("");
  const [speakerRenameTo, setSpeakerRenameTo] = useState("");
  const [meetingChats, setMeetingChats] = useState({});
  const [askInput, setAskInput] = useState("");
  const [askBusy, setAskBusy] = useState(false);
  const [streamingFinalMarkdown, setStreamingFinalMarkdown] = useState("");
  const [status, setStatus] = useState({ backend: "starting", vibe: "checking" });
  const [appearance, setAppearance] = useState(loadAppearance);
  const [locale, setLocale] = useState(loadLocale);

  const streamRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const sourceRef = useRef(null);
  const processorRef = useRef(null);
  const activeSegmentRef = useRef(null);
  const totalSamplesRef = useRef(0);
  const audioSampleRateRef = useRef(48000);
  const chunkSeqRef = useRef(0);
  const stopRequestedRef = useRef(false);
  const uploadChainRef = useRef(Promise.resolve());
  const meetingIdRef = useRef(null);
  const playbackContextRef = useRef(null);
  const playbackSourcesRef = useRef([]);
  const playbackTimerRef = useRef(null);
  const playbackProgressTimerRef = useRef(null);
  const playbackTimelineRef = useRef([]);
  const playbackRunRef = useRef(0);
  const playbackCacheRef = useRef(new Map());
  const recordingConfigRef = useRef(recordingConfig);
  const liveWaveformRef = useRef([]);
  const liveWaveformEmitRef = useRef(0);
  const previousSettingsOpenRef = useRef(false);
  const llmStatusLoadedRef = useRef(false);
  const serviceReadyOnceRef = useRef(false);
  const autoLoadedMeetingRef = useRef(false);
  const completedReprocessRef = useRef("");

  const finalMarkdown = notesOnlyMarkdown(meeting?.final_markdown);
  const streamingFinalText = String(streamingFinalMarkdown || "").trim();
  const finalMarkdownForDisplay = streamingFinalText || finalMarkdown;
  const finalNotesStreaming = Boolean(finalizing && streamingFinalText);
  const finalNotesReady = Boolean(
    finalNotesStreaming
      || (
        finalMarkdown
        && meeting?.final_source_hash
        && meeting?.final_source_version_id === (meeting?.active_version_id || "auto")
      ),
  );
  const finalNotesPending = Boolean(meeting?.id && !finalNotesReady && !finalizing);
  const segments = meeting?.segments || [];
  const utterances = meeting?.utterances || [];
  const transcriptItems = utterances.length > 0 ? utterances : segments;
  const chunks = meeting?.chunks || [];
  const showingPlaybackMeeting = Boolean(meeting?.id && playbackMeetingId === meeting.id);
  const visiblePlaybackPositionMs = showingPlaybackMeeting
    ? playbackPositionMs
    : playbackPreview.meetingId === meeting?.id
      ? playbackPreview.positionMs
      : null;
  const visiblePlaybackPlaying = showingPlaybackMeeting ? playing : false;
  const visiblePlaybackBusy = showingPlaybackMeeting ? playbackBusy : false;
  const visiblePlaybackStatus = showingPlaybackMeeting ? playbackStatus : "未播放";
  const askMessages = meeting?.id ? meetingChats[meeting.id] || [] : [];
  const activeTranscriptVersion = (meeting?.transcript_versions || []).find(
    (version) => version.id === (meeting?.active_version_id || "auto"),
  ) || null;
  const editableVersion = activeTranscriptVersion?.kind === "manual-edit";
  const speakerOptions = useMemo(() => {
    const labels = new Set();
    for (const item of transcriptItems) {
      const label = String(item?.speaker || "").trim();
      if (label) labels.add(label);
    }
    for (const item of meeting?.speakers || []) {
      const label = String(item?.label || "").trim();
      if (label) labels.add(label);
    }
    return [...labels].sort((left, right) => left.localeCompare(right, "zh-CN"));
  }, [meeting?.speakers, transcriptItems]);
  const modelCatalogAsr = Array.isArray(modelCatalog?.asr?.models) ? modelCatalog.asr.models : [];
  const selectableAsrModels = useMemo(() => {
    if (!modelCatalogAsr.length) return [];
    const installedNames = modelCatalogAsr
      .filter((item) => item.installed)
      .map((item) => item.name || item.id)
      .filter(Boolean);
    return ASR_MODEL_ORDER.filter((name) => installedNames.includes(name));
  }, [modelCatalogAsr]);
  const modelCatalogByKey = useMemo(() => {
    const map = new Map();
    for (const item of modelCatalogAsr) {
      map.set(`asr:${item.name || item.id}`, item);
    }
    for (const item of modelCatalog?.diarization?.models || []) {
      map.set(`diarization:${item.name || item.id}`, item);
    }
    return map;
  }, [modelCatalog?.diarization?.models, modelCatalogAsr]);
  const asrModelGroups = useMemo(() => {
    if (!modelCatalogAsr.length) {
      return [];
    }
    const groups = [];
    for (const backend of ["faster-whisper", "mlx"]) {
      const models = selectableAsrModels.filter((name) => modelCatalogByKey.get(`asr:${name}`)?.backend === backend);
      if (models.length > 0) {
        groups.push({
          label: asrBackendLabel(backend),
          models,
        });
      }
    }
    const grouped = new Set(groups.flatMap((group) => group.models));
    const remaining = selectableAsrModels.filter((name) => !grouped.has(name));
    if (remaining.length > 0) {
      groups.push({ label: "其他模型", models: remaining });
    }
    return groups;
  }, [modelCatalogAsr.length, modelCatalogByKey, selectableAsrModels]);
  const modelCatalogAsrGroups = useMemo(() => {
    const groups = [];
    for (const backend of ["faster-whisper", "mlx"]) {
      const models = modelCatalogAsr.filter((item) => item.backend === backend);
      if (models.length > 0) {
        groups.push({
          key: backend,
          label: asrBackendLabel(backend),
          models,
        });
      }
    }
    const grouped = new Set(groups.flatMap((group) => group.models.map((item) => item.name || item.id)));
    const remaining = modelCatalogAsr.filter((item) => !grouped.has(item.name || item.id));
    if (remaining.length > 0) {
      groups.push({ key: "other", label: "其他模型", models: remaining });
    }
    return groups;
  }, [modelCatalogAsr]);
  const activeModelDownload = (modelCatalog?.downloads || []).find((job) => (
    job?.status === "queued" || job?.status === "running" || job?.status === "cancelling"
  ));
  const latestModelDownload = (modelCatalog?.downloads || [])[0] || null;
  const activeModelDownloadMeta = activeModelDownload
    ? modelCatalogByKey.get(`${activeModelDownload.kind}:${activeModelDownload.model}`)
    : null;
  const loadedAsrModelMeta = modelCatalogAsr.find((item) => item.loaded) || null;
  const activeChunks = runtimeStatus?.active_chunks || [];
  const reprocessRuntime = runtimeStatus?.reprocess || null;
  const notesReprocessWorking = (
    reprocessRuntime?.level === "notes" && ["queued", "running"].includes(reprocessRuntime?.status)
  );
  const finalNotesWorking = finalizing || notesReprocessWorking;
  const reprocessWorking = reprocessBusy || ["queued", "running"].includes(reprocessRuntime?.status);
  const asrWorking = pendingChunks > 0 || activeChunks.length > 0 || reprocessWorking;
  const activePipelineStep = pipelineStepIndex(runtimeStatus, pendingChunks, pipelineStatus, finalizing, finalNotesWorking);
  const liveRefreshActive = Boolean(
    recording
      || pendingChunks > 0
      || activeChunks.length > 0
      || reprocessWorking
      || activeModelDownload,
  );
  const serviceReady = status.backend === "ready";
  const serviceStarting = status.backend === "starting" || status.backend === "checking";
  const servicePillClass = serviceStarting ? "working pulse-pill" : status.backend;
  const trimmedTitle = title.trim();
  const titleDirty = Boolean(meeting?.id && trimmedTitle && trimmedTitle !== meeting.title);

  useEffect(() => {
    const normalized = clampRecordingConfig(recordingConfig);
    recordingConfigRef.current = normalized;
    setAsrLanguage(normalized.language);
    saveRecordingConfig(normalized);
  }, [recordingConfig]);

  useEffect(() => {
    saveAppearance(clampAppearance(appearance));
  }, [appearance]);

  useEffect(() => {
    saveLocale(locale);
  }, [locale]);

  useEffect(() => {
    if (!modelCatalogAsr.length || selectableAsrModels.length === 0) return;
    const normalized = clampRecordingConfig(recordingConfigRef.current);
    if (selectableAsrModels.includes(normalized.asrModel)) return;
    const fallbackModel = selectableAsrModels.includes(DEFAULT_RECORDING_CONFIG.asrModel)
      ? DEFAULT_RECORDING_CONFIG.asrModel
      : selectableAsrModels[0];
    setRecordingConfig({ ...normalized, asrModel: fallbackModel });
  }, [modelCatalogAsr.length, selectableAsrModels]);

  useEffect(() => {
    if (activeModelDownload || pipelineStatus !== "模型下载中") return;
    if (latestModelDownload?.status === "done") {
      setPipelineStatus("模型已安装");
    } else if (latestModelDownload?.status === "error") {
      setPipelineStatus("模型下载失败");
    }
  }, [activeModelDownload, latestModelDownload?.status, pipelineStatus]);

  const refreshMicDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter((device) => device.kind === "audioinput");
      setMicDevices(inputs);
      if (selectedMicId && !inputs.some((device) => device.deviceId === selectedMicId)) {
        setSelectedMicId("");
        saveSelectedMicId("");
      }
    } catch {
      setMicDevices([]);
    }
  }, [selectedMicId]);

  const selectMicDevice = useCallback((deviceId) => {
    const cleanDeviceId = String(deviceId || "");
    setSelectedMicId(cleanDeviceId);
    saveSelectedMicId(cleanDeviceId);
  }, []);

  useEffect(() => {
    refreshMicDevices();
    navigator.mediaDevices?.addEventListener?.("devicechange", refreshMicDevices);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", refreshMicDevices);
  }, [refreshMicDevices]);

  useEffect(() => {
    if (meeting?.id) {
      setTitle(meeting.title || "");
      setTitleSavedAt(0);
    }
  }, [meeting?.id]);

  useEffect(() => {
    setEditingSegmentId("");
    setSegmentDrafts({});
    setSpeakerRenameTo("");
  }, [meeting?.active_version_id, meeting?.id]);

  useEffect(() => {
    if (!speakerOptions.length) {
      setSpeakerRenameFrom("");
      return;
    }
    if (!speakerRenameFrom || !speakerOptions.includes(speakerRenameFrom)) {
      setSpeakerRenameFrom(speakerOptions[0]);
    }
  }, [speakerOptions, speakerRenameFrom]);

  useEffect(() => {
    const wasOpen = previousSettingsOpenRef.current;
    previousSettingsOpenRef.current = settingsOpen;
    if (!settingsOpen || wasOpen) return;
    setLlmConfigDraft(llmDraftFromConfig(llmConfig));
    setLlmConfigError("");
    setPromptConfigError("");
    if (promptConfig) setPromptDrafts(promptDraftsFromConfig(promptConfig));
  }, [llmConfig, promptConfig, settingsOpen]);

  const applyLlmStatus = useCallback((data) => {
    if (!data) {
      setLlmStatus({ provider: "会议助手", transport: "offline" });
      return;
    }
    if (data.config) {
      setLlmConfig(normalizeLlmConfig(data.config));
    }
    const provider = data.provider === "openai-chat" ? "接口" : "VibeAround";
    setLlmStatus({
      provider: data.provider_label || data.profile_id || provider,
      transport: data.route || data.transport || data.target_api_type || data.provider || "local-api",
      model: data.model || data.config?.openai_chat?.model,
    });
  }, []);

  const applyPromptConfig = useCallback((data) => {
    if (!data) return;
    setPromptConfig(data);
    setPromptDrafts(promptDraftsFromConfig(data));
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const health = await api("/api/health");
      serviceReadyOnceRef.current = true;
      setModelStatus(health.asr || null);

      const [catalogResult] = await Promise.allSettled([api("/api/models")]);
      const catalog = catalogResult.status === "fulfilled" ? catalogResult.value : null;
      if (catalog) setModelCatalog(catalog);
	      setStatus((current) => ({
	        ...current,
	        backend: "ready",
	        backendDetail: "",
	      }));
	    } catch (err) {
	      const nativeStatus = await nativeBackendStatus();
	      const nativeError = String(nativeStatus?.error || "").trim();
	      const recentLogs = Array.isArray(nativeStatus?.logs)
	        ? nativeStatus.logs.filter(Boolean).slice(-3).join(" / ")
	        : "";
	      const backendDetail = nativeError || recentLogs || err.message;
	      const backendState = nativeStatus?.status === "error"
	        ? "offline"
	        : serviceReadyOnceRef.current
	          ? "offline"
	          : "starting";
	      setStatus((current) => ({
	        ...current,
	        backend: backendState,
	        backendDetail,
	      }));
	      setModelStatus(null);
	      setModelCatalog(null);
	    }
  }, []);

  const loadLlmStatus = useCallback(async () => {
    try {
      const assistant = await api("/api/llm/status");
      applyLlmStatus(assistant);
      setStatus((current) => ({
        ...current,
        vibe: assistant?.ok ? "ready" : "fallback",
        profile: assistant?.profile_id,
        vibeDetail: assistant?.error || assistant?.status_code,
      }));
    } catch (err) {
      setStatus((current) => ({
        ...current,
        vibe: "unknown",
        vibeDetail: err.message,
      }));
      setLlmStatus({ provider: "会议助手", transport: "offline" });
    }
  }, [applyLlmStatus]);

  const refreshPromptConfig = useCallback(async () => {
    try {
      const data = await api("/api/prompts/config");
      applyPromptConfig(data);
      setPromptConfigError("");
    } catch (err) {
      setPromptConfigError(userFriendlyError(err.message));
    }
  }, [applyPromptConfig]);

  useEffect(() => {
    if (!settingsOpen) return;
    if (!promptConfig) {
      refreshPromptConfig();
    }
  }, [promptConfig, refreshPromptConfig, settingsOpen]);

  const refreshMeetings = useCallback(async () => {
    try {
      const data = await api("/api/meetings");
      setMeetings(data.meetings || []);
    } catch {
      setMeetings([]);
    }
  }, []);

  const refreshMeeting = useCallback(async (id) => {
    if (!id) return;
    try {
      const data = await api(`/api/meetings/${id}`);
      setMeeting(data);
    } catch (err) {
      setError(userFriendlyError(err.message));
    }
  }, []);

  const refreshRuntime = useCallback(async (id) => {
    if (!id) return;
    try {
      const data = await api(`/api/meetings/${id}/runtime`);
      setRuntimeStatus(data);
      setLlmStatus({
        provider: data.llm?.provider_label || llmProviderLabel(data.llm?.provider),
        transport: data.llm?.route || data.llm?.transport || data.llm?.target_api_type || "local-api",
        model: data.llm?.model,
      });
      setModelStatus(data.asr || null);
    } catch {
      setRuntimeStatus(null);
    }
  }, []);

  const downloadModel = useCallback(async (kind, model) => {
    if (!kind || !model) return null;
    setError("");
    try {
      const data = await api("/api/models/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, model }),
      });
      if (data.catalog) setModelCatalog(data.catalog);
      setPipelineStatus("模型下载中");
      return data.job || null;
    } catch (err) {
      setError(userFriendlyError(err.message));
      return null;
    }
  }, []);

  const deleteModel = useCallback(async (kind, model) => {
    if (!kind || !model) return;
    const meta = modelCatalogByKey.get(`${kind}:${model}`);
    const active = ["queued", "running", "cancelling"].includes(meta?.job?.status);
    const prompt = active
      ? `取消下载并删除已下载部分：${meta?.label || model}？`
      : `删除本地模型：${meta?.label || model}？`;
    if (!window.confirm(prompt)) return;
    setError("");
    try {
      const catalog = await api(`/api/models/${encodeURIComponent(kind)}/${encodeURIComponent(model)}`, {
        method: "DELETE",
      });
      setModelCatalog(catalog || null);
      await refreshStatus();
    } catch (err) {
      setError(userFriendlyError(err.message));
    }
  }, [modelCatalogByKey, refreshStatus]);

  const updateRecordingConfig = useCallback((field, value) => {
    setRecordingConfig((current) => {
      const next = clampRecordingConfig({ ...current, [field]: value });
      return next;
    });
  }, []);

  const loadRecordingAsrModel = useCallback(
    async (model) => {
      const cleanModel = String(model || "").trim();
      if (!cleanModel || recording || busy || importingAudio) return;
      const targetMeta = modelCatalogByKey.get(`asr:${cleanModel}`);
      const targetLabel = targetMeta?.label || asrModelName(cleanModel);
      const previousLabel = loadedAsrModelMeta && loadedAsrModelMeta.name !== cleanModel
        ? loadedAsrModelMeta.label || asrModelName(loadedAsrModelMeta.name)
        : "";
      setError("");
      setModelLoadState({
        status: "loading",
        targetModel: cleanModel,
        targetLabel,
        previousLabel,
      });
      try {
        const result = await api("/api/models/load", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind: "asr", model: cleanModel }),
        });
        if (result.catalog) setModelCatalog(result.catalog);
        setRecordingConfig((current) => clampRecordingConfig({ ...current, asrModel: cleanModel }));
        setModelStatus(result.status || null);
        setPipelineStatus(`${targetLabel} 已加载`);
        setModelLoadState({
          status: "success",
          targetModel: cleanModel,
          targetLabel: result.label || targetLabel,
          previousLabel: (result.unloaded || [])[0]?.label || previousLabel,
        });
        await refreshStatus();
      } catch (err) {
        const message = userFriendlyError(err.message);
        setError(message);
        setModelLoadState({
          status: "error",
          targetModel: cleanModel,
          targetLabel,
          previousLabel,
          error: message,
        });
      }
    },
    [busy, importingAudio, loadedAsrModelMeta, modelCatalogByKey, recording, refreshStatus],
  );

  const updateAppearance = useCallback((field, value) => {
    setAppearance((current) => clampAppearance({ ...current, [field]: value }));
  }, []);

  const updateLlmConfigDraft = useCallback((field, value) => {
    setLlmConfigError("");
    setLlmConfigDraft((current) => ({
      ...current,
      [field]: value,
    }));
  }, []);

  const saveLlmConfig = useCallback(
    async (event) => {
      event.preventDefault();
      if (llmConfigSaving) return;
      setLlmConfigSaving(true);
      setLlmConfigError("");
      setError("");
      try {
        const saved = await api("/api/llm/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: llmConfigDraft.provider,
            openai_chat: {
              base_url: llmConfigDraft.baseUrl,
              api_key: llmConfigDraft.apiKey,
              model: llmConfigDraft.model,
            },
          }),
        });
        const normalized = normalizeLlmConfig(saved);
        setLlmConfig(normalized);
        setLlmConfigDraft(llmDraftFromConfig(normalized));
        setLlmStatus({
          provider: llmProviderLabel(normalized.provider),
          transport: normalized.provider === "openai-chat" ? "openai-chat" : "local-api",
          model: normalized.openai_chat.model,
        });
        setStatus((current) => ({
          ...current,
          vibe: "ready",
          vibeDetail: "",
        }));
        setPipelineStatus("会议助手配置已保存");
      } catch (err) {
        setLlmConfigError(userFriendlyError(err.message));
      } finally {
        setLlmConfigSaving(false);
      }
    },
    [llmConfigDraft, llmConfigSaving],
  );

  const updatePromptDraft = useCallback((key, value) => {
    setPromptConfigError("");
    setPromptDrafts((current) => ({
      ...current,
      [key]: value,
    }));
  }, []);

  const resetPromptDraft = useCallback((key) => {
    const item = (promptConfig?.prompts || []).find((entry) => entry.key === key);
    if (!item) return;
    setPromptConfigError("");
    setPromptDrafts((current) => ({
      ...current,
      [key]: item.default || "",
    }));
  }, [promptConfig?.prompts]);

  const savePromptConfig = useCallback(
    async (event) => {
      event.preventDefault();
      if (promptConfigSaving) return;
      setPromptConfigSaving(true);
      setPromptConfigError("");
      setError("");
      try {
        const saved = await api("/api/prompts/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompts: promptDrafts }),
        });
        applyPromptConfig(saved);
        setPipelineStatus("系统提示词已保存");
      } catch (err) {
        setPromptConfigError(userFriendlyError(err.message));
      } finally {
        setPromptConfigSaving(false);
      }
    },
    [applyPromptConfig, promptConfigSaving, promptDrafts],
  );

  const requiredModelsForConfig = useCallback((config) => {
    const normalized = clampRecordingConfig(config);
    const required = [{ kind: "asr", model: normalized.asrModel }];
    if (normalized.speakerMode === "diarization") {
      required.push({ kind: "diarization", model: "pyannote-community-1" });
    }
    return required;
  }, []);

  const missingModelsForConfig = useCallback((config) => (
    modelCatalog
      ? requiredModelsForConfig(config).filter((item) => {
        const meta = modelCatalogByKey.get(`${item.kind}:${item.model}`);
        return !meta?.installed;
      })
      : []
  ), [modelCatalog, modelCatalogByKey, requiredModelsForConfig]);

  const ensureRecordingModels = useCallback(async () => {
    const missing = missingModelsForConfig(recordingConfigRef.current);
    if (missing.length === 0) return true;
    const names = missing.map((item) => {
      const meta = modelCatalogByKey.get(`${item.kind}:${item.model}`);
      return meta?.label || item.model;
    }).join("、");
    const ok = window.confirm(`当前设置缺少模型：${names}。是否现在下载？`);
    if (!ok) {
      setError("当前设置缺少本地模型。");
      return false;
    }
    for (const item of missing) {
      await downloadModel(item.kind, item.model);
    }
    return false;
  }, [downloadModel, missingModelsForConfig, modelCatalogByKey]);

  const openMeetingProperties = useCallback(() => {
    if (!meeting?.id) return;
    setPropertiesDraft({
      title: meeting.title || "今天的会议",
      description: meeting.description || "",
    });
    setPropertiesError("");
    setPropertiesOpen(true);
  }, [meeting?.description, meeting?.id, meeting?.title]);

  const updatePropertiesDraft = useCallback((field, value) => {
    setPropertiesError("");
    setPropertiesDraft((current) => ({
      ...current,
      [field]: value,
    }));
  }, []);

  const closeMeetingProperties = useCallback(() => {
    if (propertiesSaving) return;
    setPropertiesOpen(false);
  }, [propertiesSaving]);

  const saveMeetingProperties = useCallback(
    async (event) => {
      event.preventDefault();
      const id = meeting?.id;
      if (!id || propertiesSaving) return;
      const cleanTitle = propertiesDraft.title.trim() || "今天的会议";
      const cleanDescription = propertiesDraft.description.trim();
      setPropertiesSaving(true);
      setPropertiesError("");
      setError("");
      try {
        const updated = await api(`/api/meetings/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: cleanTitle,
            description: cleanDescription,
          }),
        });
        setMeeting(updated);
        setTitle(updated.title || cleanTitle);
        setTitleSavedAt(Date.now());
        setPropertiesOpen(false);
        setPipelineStatus("会议属性已保存");
        await refreshMeetings();
      } catch (err) {
        setPropertiesError(userFriendlyError(err.message));
      } finally {
        setPropertiesSaving(false);
      }
    },
    [meeting?.id, propertiesDraft.description, propertiesDraft.title, propertiesSaving, refreshMeetings],
  );

  const saveMeetingTitle = useCallback(async () => {
    const id = meeting?.id;
    const cleanTitle = title.trim();
    if (!id || !cleanTitle || titleSaving) return;
    setTitleSaving(true);
    setError("");
    try {
      const updated = await api(`/api/meetings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: cleanTitle }),
      });
      setMeeting(updated);
      setTitle(updated.title || "");
      setTitleSavedAt(Date.now());
      await refreshMeetings();
    } catch (err) {
      setError(userFriendlyError(err.message));
    } finally {
      setTitleSaving(false);
    }
  }, [meeting?.id, refreshMeetings, title, titleSaving]);

  const activateTranscriptVersion = useCallback(
    async (versionId) => {
      const id = meeting?.id;
      if (!id || !versionId || versionId === meeting.active_version_id) return;
      setError("");
      try {
        const updated = await api(`/api/meetings/${id}/versions/${encodeURIComponent(versionId)}/activate`, {
          method: "POST",
        });
        setMeeting(updated);
        await refreshRuntime(id);
      } catch (err) {
        setError(userFriendlyError(err.message));
      }
    },
    [meeting?.active_version_id, meeting?.id, refreshRuntime],
  );

  const startReprocess = useCallback(
    async (level) => {
      const id = meeting?.id;
      if (!id || reprocessBusy) return;
      const labels = {
        asr: "重新识别",
        speaker: "说话人校准",
        merge: "整理段落",
        repair: "自动校对文字",
        notes: "纪要重写",
      };
      setReprocessBusy(true);
      setError("");
      setPipelineStatus(`准备${labels[level] || "处理"}`);
      try {
        const activeConfig = clampRecordingConfig(recordingConfigRef.current);
        const data = await api(`/api/meetings/${id}/reprocess`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            level,
            language: activeConfig.language,
            asr_model: activeConfig.asrModel,
            speaker_mode: activeConfig.speakerMode,
            make_current: true,
            force_local: false,
            source_version_id: meeting.active_version_id || "auto",
            reset_speakers: level === "speaker" && activeConfig.speakerMode !== "off",
          }),
        });
        if (data.job) {
          setRuntimeStatus((current) => ({ ...(current || {}), reprocess: data.job }));
        }
        setPipelineStatus(`${labels[level] || "处理"}中`);
        if (data.job?.status === "done") {
          await refreshMeeting(id);
          await refreshMeetings();
        }
        await refreshRuntime(id);
      } catch (err) {
        setPipelineStatus("处理失败");
        setError(userFriendlyError(err.message));
      } finally {
        setReprocessBusy(false);
      }
    },
    [
      meeting?.active_version_id,
      meeting?.id,
      refreshMeeting,
      refreshMeetings,
      refreshRuntime,
      reprocessBusy,
    ],
  );

  const createEditableVersion = useCallback(async () => {
    const id = meeting?.id;
    if (!id || editBusy) return;
    setEditBusy(true);
    setError("");
    try {
      const updated = await api(`/api/meetings/${id}/versions/editable`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_version_id: meeting.active_version_id || "auto" }),
      });
      setMeeting(updated);
      setPipelineStatus("已创建可编辑副本");
      await refreshRuntime(id);
      await refreshMeetings();
    } catch (err) {
      setError(userFriendlyError(err.message));
    } finally {
      setEditBusy(false);
    }
  }, [editBusy, meeting?.active_version_id, meeting?.id, refreshMeetings, refreshRuntime]);

  const startEditSegment = useCallback(
    (event, segment) => {
      event.stopPropagation();
      if (!editableVersion) {
        setError("请先创建可编辑副本，再修改文字。");
        return;
      }
      const drafts = {};
      for (const part of transcriptParts(segment)) {
        if (part.id) {
          drafts[part.id] = part.raw_text ?? part.text ?? "";
        }
      }
      setEditingSegmentId(segment.id);
      setSegmentDrafts(drafts);
    },
    [editableVersion],
  );

  const updateSegmentDraft = useCallback((segmentId, value) => {
    setSegmentDrafts((current) => ({
      ...current,
      [segmentId]: value,
    }));
  }, []);

  const cancelEditSegment = useCallback((event) => {
    event.stopPropagation();
    setEditingSegmentId("");
    setSegmentDrafts({});
  }, []);

  const saveSegmentEdits = useCallback(
    async (event, segment) => {
      event.stopPropagation();
      const id = meeting?.id;
      if (!id || !editableVersion || editBusy) return;
      const updates = transcriptParts(segment)
        .filter((part) => part.id)
        .map((part) => ({
          id: part.id,
          before: part.raw_text ?? part.text ?? "",
          after: segmentDrafts[part.id] ?? "",
        }))
        .filter((item) => item.after.trim() !== String(item.before || "").trim());

      if (updates.length === 0) {
        setEditingSegmentId("");
        setSegmentDrafts({});
        return;
      }

      setEditBusy(true);
      setError("");
      try {
        let updated = null;
        for (const item of updates) {
          updated = await api(`/api/meetings/${id}/segments/${encodeURIComponent(item.id)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: item.after }),
          });
        }
        if (updated) {
          setMeeting(updated);
          await refreshMeetings();
        }
        setEditingSegmentId("");
        setSegmentDrafts({});
      } catch (err) {
        setError(userFriendlyError(err.message));
      } finally {
        setEditBusy(false);
      }
    },
    [editBusy, editableVersion, meeting?.id, refreshMeetings, segmentDrafts],
  );

  const renameSpeaker = useCallback(async () => {
    const id = meeting?.id;
    const from = speakerRenameFrom.trim();
    const to = speakerRenameTo.trim();
    if (!id || !from || !to || editBusy) return;
    if (!editableVersion) {
      setError("请先创建可编辑副本，再修改说话人名称。");
      return;
    }
    setEditBusy(true);
    setError("");
    try {
      const updated = await api(`/api/meetings/${id}/speakers/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_label: from, new_label: to }),
      });
      setMeeting(updated);
      setSpeakerRenameFrom(to);
      setSpeakerRenameTo("");
      await refreshMeetings();
    } catch (err) {
      setError(userFriendlyError(err.message));
    } finally {
      setEditBusy(false);
    }
  }, [editBusy, editableVersion, meeting?.id, refreshMeetings, speakerRenameFrom, speakerRenameTo]);

  const setMeetingChat = useCallback((meetingId, updater) => {
    setMeetingChats((current) => {
      const previous = current[meetingId] || [];
      return {
        ...current,
        [meetingId]: typeof updater === "function" ? updater(previous) : updater,
      };
    });
  }, []);

  const askMeeting = useCallback(
    async (promptOverride = "") => {
      const id = meeting?.id;
      const prompt = (promptOverride || askInput).trim();
      if (!id || !prompt || askBusy) return;

      const userMessage = {
        id: `${Date.now()}-user`,
        role: "user",
        content: prompt,
      };
      const history = askMessages
        .slice(-8)
        .map((message) => ({ role: message.role, content: message.content }));

      setMeetingChat(id, (previous) => [...previous, userMessage]);
      setAskInput("");
      setAskBusy(true);
      setError("");
      try {
        const data = await api(`/api/meetings/${id}/ask`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt, history }),
        });
        const assistantMessage = {
          id: `${Date.now()}-assistant`,
          role: "assistant",
          content: data.answer || "没有生成可用内容。",
          llm: data.llm,
        };
        setMeetingChat(id, (previous) => [...previous, assistantMessage]);
        if (data.llm) {
          setLlmStatus({
            provider: data.llm.provider_label || llmProviderLabel(data.llm.provider),
            transport: data.llm.route || data.llm.transport || "local-api",
            model: data.llm.model,
          });
        }
      } catch (err) {
        const assistantMessage = {
          id: `${Date.now()}-assistant-error`,
          role: "assistant",
          error: true,
          content: userFriendlyError(err.message),
        };
        setMeetingChat(id, (previous) => [...previous, assistantMessage]);
        setError(userFriendlyError(err.message));
      } finally {
        setAskBusy(false);
      }
    },
    [askBusy, askInput, askMessages, meeting?.id, setMeetingChat],
  );

  useEffect(() => {
    refreshStatus();
    refreshMeetings();
  }, [refreshMeetings, refreshStatus]);

  useEffect(() => {
    if (!liveRefreshActive) return undefined;
    const tick = () => {
      refreshStatus();
      refreshMeetings();
      if (meetingIdRef.current) {
        refreshMeeting(meetingIdRef.current);
        refreshRuntime(meetingIdRef.current);
      }
    };
    tick();
    const interval = window.setInterval(() => {
      tick();
    }, 1000);
    return () => window.clearInterval(interval);
  }, [liveRefreshActive, refreshMeeting, refreshMeetings, refreshRuntime, refreshStatus]);

  useEffect(() => {
    const job = runtimeStatus?.reprocess;
    const id = meeting?.id;
    if (!id || !job || job.status !== "done") return;
    const key = `${id}:${job.id || ""}:${job.version_id || ""}:${job.updated_at || ""}`;
    if (completedReprocessRef.current === key) return;
    completedReprocessRef.current = key;
    refreshMeeting(id);
    refreshMeetings();
    const doneLabels = {
      asr: "重新识别已完成",
      speaker: "说话人校准已完成",
      merge: "段落整理已完成",
      repair: "自动校对文字已完成",
      notes: "纪要已更新",
    };
    setPipelineStatus(doneLabels[job.level] || "处理已完成");
  }, [meeting?.id, refreshMeeting, refreshMeetings, runtimeStatus?.reprocess]);

  useEffect(() => {
    if (llmStatusLoadedRef.current) return;
    llmStatusLoadedRef.current = true;
    loadLlmStatus();
  }, [loadLlmStatus]);

  const uploadChunk = useCallback(
    async (blob, durationMs = MAX_SEGMENT_MS, metadata = {}) => {
      const id = meetingIdRef.current;
      if (!id || !blob || blob.size === 0) return;
      const activeConfig = clampRecordingConfig(recordingConfigRef.current);
      setPendingChunks((value) => value + 1);
      setPipelineStatus(modelStatus?.loaded ? "保存音频" : "准备语音识别");
      setError("");
      const form = new FormData();
      const extension = blob.type.includes("wav")
        ? "wav"
        : blob.type.includes("mp4") || blob.type.includes("aac")
          ? "m4a"
          : "webm";
      form.append("audio", blob, `chunk-${Date.now()}.${extension}`);
      form.append("duration_ms", String(Math.max(0, Math.round(durationMs))));
      form.append("language", activeConfig.language);
      form.append("asr_model", activeConfig.asrModel);
      form.append("speaker_mode", activeConfig.speakerMode);
      if (metadata.clientChunkId) form.append("client_chunk_id", metadata.clientChunkId);
      if (Number.isFinite(metadata.startedAtMs)) {
        form.append("started_at_ms", String(Math.max(0, Math.round(metadata.startedAtMs))));
      }
      if (Number.isFinite(metadata.endedAtMs)) {
        form.append("ended_at_ms", String(Math.max(0, Math.round(metadata.endedAtMs))));
      }
      if (metadata.cutReason) form.append("cut_reason", metadata.cutReason);
      try {
        const data = await api(`/api/meetings/${id}/chunks`, {
          method: "POST",
          body: form,
        });
        setLastAsr(data.asr || null);
        if (data.runtime) setRuntimeStatus(data.runtime);
        setPipelineStatus("文字已更新");
        setMeeting((current) => {
          if (!current || current.id !== id) return current;
          return {
            ...current,
            summary: data.summary,
            chunks: [...(current.chunks || []), data.chunk],
            segments: [...(current.segments || []), ...(data.segments || [])],
            utterances: data.utterances || current.utterances || [],
          };
        });
      } catch (err) {
        setPipelineStatus("处理失败");
        setError(userFriendlyError(err.message));
      } finally {
        setPendingChunks((value) => Math.max(0, value - 1));
      }
    },
    [modelStatus?.loaded],
  );

  const enqueueChunk = useCallback(
    (blob, durationMs, metadata = {}) => {
      uploadChainRef.current = uploadChainRef.current
        .then(() => uploadChunk(blob, durationMs, metadata))
        .catch(() => uploadChunk(blob, durationMs, metadata));
      return uploadChainRef.current;
    },
    [uploadChunk],
  );

  const closeActiveSegment = useCallback(
    (reason, endedAtMs) => {
      const segment = activeSegmentRef.current;
      if (!segment) return;
      activeSegmentRef.current = null;
      const durationMs = Math.max(0, endedAtMs - segment.startedAtMs);
      if (durationMs < 300) {
        setPipelineStatus("录音中");
        return;
      }
      const samples = concatFloat32(segment.chunks);
      if (samples.length === 0) {
        setPipelineStatus("录音中");
        return;
      }
      chunkSeqRef.current += 1;
      const clientChunkId = `${meetingIdRef.current || "meeting"}-${chunkSeqRef.current}`;
      const blob = encodeWav(samples, audioSampleRateRef.current);
      enqueueChunk(blob, durationMs, {
        clientChunkId,
        startedAtMs: segment.startedAtMs,
        endedAtMs,
        cutReason: reason,
      });
      setPipelineStatus(reason);
    },
    [enqueueChunk],
  );

  const handleAudioFrame = useCallback(
    (input) => {
      if (stopRequestedRef.current) return;
      const activeConfig = clampRecordingConfig(recordingConfigRef.current);
      const frame = applyInputGain(input, activeConfig.inputGain);
      let sum = 0;
      for (let index = 0; index < frame.length; index += 1) {
        sum += frame[index] * frame[index];
      }
      const level = Math.sqrt(sum / frame.length);
      setVadLevel(level);

      const startMs = totalSamplesRef.current * 1000 / audioSampleRateRef.current;
      totalSamplesRef.current += frame.length;
      const endMs = totalSamplesRef.current * 1000 / audioSampleRateRef.current;
      const shapedLevel = Math.max(0.025, Math.min(1, Math.pow(Math.max(0, level * 24), 0.72)));
      liveWaveformRef.current.push(shapedLevel);
      if (liveWaveformRef.current.length > LIVE_WAVEFORM_BAR_COUNT * 8) {
        liveWaveformRef.current = liveWaveformRef.current.slice(-LIVE_WAVEFORM_BAR_COUNT * 8);
      }
      const now = performance.now();
      if (now - liveWaveformEmitRef.current > 70) {
        liveWaveformEmitRef.current = now;
        setLiveWaveformBars(compressWaveformBars(liveWaveformRef.current));
        setLiveRecordingMs(endMs);
      }
      let segment = activeSegmentRef.current;
      if (!segment) {
        activeSegmentRef.current = {
          chunks: [],
          startedAtMs: startMs,
        };
        segment = activeSegmentRef.current;
      }

      segment.chunks.push(frame);
      const durationMs = endMs - segment.startedAtMs;
      if (durationMs >= activeConfig.maxSegmentMs) {
        closeActiveSegment("定时上传", endMs);
      }
    },
    [closeActiveSegment],
  );

  const startMeeting = useCallback(async () => {
    if (!serviceReady) {
      setError("本地语音服务还在启动中，请稍候。");
      return;
    }
    if (recording || busy || importingAudio) return;
    if (!(await ensureRecordingModels())) return;
    setBusy(true);
    setError("");
    setPipelineStatus("启动录音");
    try {
      await requestNativeMicrophonePermission();
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("当前环境不允许访问麦克风。");
      }
      const created = await api("/api/meetings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      setMeeting(created);
      setTitle(created.title || "今天的会议");
      meetingIdRef.current = created.id;
      stopRequestedRef.current = false;
      setLastAsr(null);
      setLiveWaveformBars([]);
      setLiveRecordingMs(0);
      liveWaveformRef.current = [];
      liveWaveformEmitRef.current = 0;
      setPipelineStatus("录音中");
      await refreshMeetings();

      const baseAudioConstraints = {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      };
      const selectedAudioConstraints = selectedMicId
        ? { ...baseAudioConstraints, deviceId: { exact: selectedMicId } }
        : baseAudioConstraints;
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: selectedAudioConstraints });
      } catch (err) {
        if (selectedMicId && (err?.name === "OverconstrainedError" || err?.name === "NotFoundError")) {
          selectMicDevice("");
          stream = await navigator.mediaDevices.getUserMedia({ audio: baseAudioConstraints });
        } else {
          throw err;
        }
      }
      streamRef.current = stream;
      const [audioTrack] = stream.getAudioTracks();
      setActiveMicLabel(audioTrack?.label || "");
      await refreshMicDevices();
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error("当前浏览器不支持录音。");
      }

      activeSegmentRef.current = null;
      totalSamplesRef.current = 0;
      chunkSeqRef.current = 0;

      const audioContext = new AudioContextCtor();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      const processor = audioContext.createScriptProcessor(2048, 1, 1);
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.2;
      audioSampleRateRef.current = audioContext.sampleRate;
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        const output = event.outputBuffer.getChannelData(0);
        output.fill(0);
        handleAudioFrame(input);
      };
      source.connect(analyser);
      source.connect(processor);
      processor.connect(audioContext.destination);
      audioContextRef.current = audioContext;
      sourceRef.current = source;
      analyserRef.current = analyser;
      processorRef.current = processor;
      if (audioContext.state === "suspended") {
        await audioContext.resume();
      }
      setRecording(true);
    } catch (err) {
      setPipelineStatus("启动失败");
      setError(userFriendlyError(err.message));
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
      setActiveMicLabel("");
    } finally {
      setBusy(false);
    }
  }, [
    busy,
    ensureRecordingModels,
    handleAudioFrame,
    importingAudio,
    recording,
    refreshMeetings,
    refreshMicDevices,
    selectMicDevice,
    selectedMicId,
    serviceReady,
  ]);

  const runFinalize = useCallback(async (id, { allowWhileRecording = false } = {}) => {
    if (!id || finalizing) return;
    if (recording && !allowWhileRecording) {
      setError("请先停止录音，再生成纪要。");
      return;
    }
    setFinalizing(true);
    setError("");
    setStreamingFinalMarkdown("");
    try {
      await uploadChainRef.current;
      setPipelineStatus("最终纪要生成中");
      const response = await fetch(`${API_BASE}/api/meetings/${id}/finalize/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force_local: false }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(userFriendlyError(text || `${response.status} ${response.statusText}`));
      }
      let finalized = null;
      let streamedMarkdown = "";
      await readSse(response, {
        chunk: ({ text }) => {
          const next = String(text || "");
          if (!next) return;
          streamedMarkdown += next;
          setStreamingFinalMarkdown(streamedMarkdown);
        },
        replace: ({ markdown }) => {
          streamedMarkdown = String(markdown || "");
          setStreamingFinalMarkdown(streamedMarkdown);
        },
        done: ({ meeting: updated }) => {
          if (updated) {
            finalized = updated;
            setMeeting(updated);
          }
        },
      });
      if (finalized) {
        setMeeting(finalized);
      } else {
        await refreshMeeting(id);
      }
      setStreamingFinalMarkdown("");
      setPipelineStatus("完成");
      await refreshMeetings();
    } catch (err) {
      setPipelineStatus("生成失败");
      setError(userFriendlyError(err.message));
    } finally {
      setFinalizing(false);
    }
  }, [finalizing, recording, refreshMeeting, refreshMeetings]);

  const stopRecording = useCallback(async () => {
    if (!recording) return;
    stopRequestedRef.current = true;
    if (activeSegmentRef.current) {
      const endedAtMs = totalSamplesRef.current * 1000 / audioSampleRateRef.current;
      closeActiveSegment("手动停止", endedAtMs);
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current.onaudioprocess = null;
      processorRef.current = null;
    }
    if (sourceRef.current) {
      sourceRef.current.disconnect();
      sourceRef.current = null;
    }
    if (analyserRef.current) {
      analyserRef.current.disconnect();
      analyserRef.current = null;
    }
    if (audioContextRef.current) {
      await audioContextRef.current.close();
      audioContextRef.current = null;
    }
    setRecording(false);
    setActiveMicLabel("");
    setVadLevel(0);
    setPipelineStatus("已停止");
    const stoppedMeetingId = meetingIdRef.current;
    if (stoppedMeetingId) {
      try {
        await api(`/api/meetings/${stoppedMeetingId}/stop`, { method: "POST" });
      } catch {
        // The local state still carries the recording outcome.
      }
      await runFinalize(stoppedMeetingId, { allowWhileRecording: true });
    }
  }, [closeActiveSegment, recording, runFinalize]);

  const finalize = useCallback(async () => {
    const id = meetingIdRef.current || meeting?.id;
    await runFinalize(id);
  }, [meeting?.id, runFinalize]);

  const loadMeeting = useCallback(
    async (id) => {
      meetingIdRef.current = id;
      await refreshMeeting(id);
      await refreshRuntime(id);
    },
    [refreshMeeting, refreshRuntime],
  );

  useEffect(() => {
    if (autoLoadedMeetingRef.current || meeting?.id || recording || meetings.length === 0) return;
    autoLoadedMeetingRef.current = true;
    loadMeeting(meetings[0].id);
  }, [loadMeeting, meeting?.id, meetings, recording]);

  const requestDeleteCurrentMeeting = useCallback((event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!meeting?.id) return;
    setPropertiesOpen(false);
    setDeleteTarget({
      id: meeting.id,
      title: meeting.title || "今天的会议",
    });
  }, [meeting?.id, meeting?.title]);

  const deleteMeeting = useCallback(
    async () => {
      const id = deleteTarget?.id;
      if (!id) return;
      setDeleteBusy(true);
      setError("");
      try {
        await api(`/api/meetings/${id}`, { method: "DELETE" });
        if (meetingIdRef.current === id || playbackMeetingId === id) {
          if (playbackTimerRef.current) {
            window.clearTimeout(playbackTimerRef.current);
            playbackTimerRef.current = null;
          }
          if (playbackProgressTimerRef.current) {
            window.clearInterval(playbackProgressTimerRef.current);
            playbackProgressTimerRef.current = null;
          }
          for (const source of playbackSourcesRef.current) {
            try {
              source.stop();
            } catch {
              // Already ended.
            }
          }
          playbackSourcesRef.current = [];
          if (playbackContextRef.current) {
            await playbackContextRef.current.close();
            playbackContextRef.current = null;
          }
          setPlaying(false);
          setPlaybackBusy(false);
          setPlaybackMeetingId(null);
          setPlaybackPositionMs(null);
          setPlaybackDurationMs(0);
          setPlaybackPreview({ meetingId: null, positionMs: null });
          setPlaybackStatus("未播放");
        }
        if (meetingIdRef.current === id) {
          meetingIdRef.current = null;
          setMeeting(null);
          setTitle("今天的会议");
          setTitleSavedAt(0);
          setRuntimeStatus(null);
          setLastAsr(null);
          setPipelineStatus("待机");
        }
        await refreshMeetings();
        setDeleteTarget(null);
      } catch (err) {
        setError(userFriendlyError(err.message));
      } finally {
        setDeleteBusy(false);
      }
    },
    [deleteTarget?.id, playbackMeetingId, refreshMeetings],
  );

  const stopPlayback = useCallback(async (nextStatus = "已停止", options = {}) => {
    const preservePosition = Boolean(options.preservePosition);
    const preserveMeeting = Boolean(options.preserveMeeting);
    const clearCache = options.clearCache !== false;
    playbackRunRef.current += 1;
    if (playbackTimerRef.current) {
      window.clearTimeout(playbackTimerRef.current);
      playbackTimerRef.current = null;
    }
    if (playbackProgressTimerRef.current) {
      window.clearInterval(playbackProgressTimerRef.current);
      playbackProgressTimerRef.current = null;
    }
    for (const source of playbackSourcesRef.current) {
      try {
        source.stop();
      } catch {
        // Source may already have ended.
      }
    }
    playbackSourcesRef.current = [];
    if (clearCache) playbackCacheRef.current.clear();
    if (playbackContextRef.current) {
      await playbackContextRef.current.close();
      playbackContextRef.current = null;
    }
    setPlaying(false);
    if (!preservePosition) setPlaybackPositionMs(null);
    if (!preserveMeeting) {
      setPlaybackMeetingId(null);
      setPlaybackDurationMs(0);
    }
    setPlaybackBusy(false);
    setPlaybackStatus(nextStatus);
  }, []);

  const decodePlaybackChunk = useCallback(async (context, chunk) => {
    const key = chunk?.id || chunk?.audio_url;
    if (!key || !chunk?.audio_url) {
      throw new Error("回放信息不完整。");
    }
    const cached = playbackCacheRef.current.get(key);
    if (cached) return cached;

    const response = await fetch(`${API_BASE}${chunk.audio_url}`);
    if (!response.ok) {
      throw new Error(`回放加载失败：${response.status}`);
    }
    const arrayBuffer = await response.arrayBuffer();
    const audioBuffer = await context.decodeAudioData(arrayBuffer);
    playbackCacheRef.current.set(key, audioBuffer);
    while (playbackCacheRef.current.size > 8) {
      const oldestKey = playbackCacheRef.current.keys().next().value;
      playbackCacheRef.current.delete(oldestKey);
    }
    return audioBuffer;
  }, []);

  const startPlaybackAt = useCallback(async (startAtMs = 0) => {
    const id = meetingIdRef.current || meeting?.id;
    if (!id || playbackBusy) return;
    const targetMs = Math.max(0, Number(startAtMs) || 0);
    setPlaybackPreview({ meetingId: null, positionMs: null });
    await stopPlayback("切换回放", { clearCache: false });
    setPlaybackMeetingId(id);
    setPlaybackPositionMs(targetMs);

    setPlaybackBusy(true);
    setPlaybackStatus("加载回放");
    setError("");
    try {
      const manifest = await api(`/api/meetings/${id}/playback`);
      const playableChunks = manifest.chunks || [];
      if (playableChunks.length === 0) {
        await stopPlayback("暂无音频");
        return;
      }
      const manifestDurationMs = Math.max(0, ...playableChunks.map((chunk) => playbackBounds(chunk).endMs));
      setPlaybackDurationMs(manifestDurationMs);

      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error("当前浏览器不支持回放。");
      }
      const context = new AudioContextCtor();
      playbackContextRef.current = context;
      playbackSourcesRef.current = [];

      if (context.state === "suspended") {
        await context.resume();
      }

      const runId = playbackRunRef.current + 1;
      playbackRunRef.current = runId;
      let firstChunkScheduled = false;

      const scheduleChunk = async (index, requestedStartMs = null) => {
        if (runId !== playbackRunRef.current) return;
        if (index >= playableChunks.length) {
          await stopPlayback("播放完成");
          return;
        }

        const chunk = playableChunks[index];
        try {
          const audioBuffer = await decodePlaybackChunk(context, chunk);
          if (runId !== playbackRunRef.current) return;

          const bounds = playbackBounds(chunk);
          const trimStartMs = Math.max(0, Number(chunk.trim_start_ms) || 0);
          const requestedOffsetMs = Number.isFinite(requestedStartMs)
            ? Math.max(0, requestedStartMs - bounds.startMs)
            : 0;
          const offsetSec = Math.max(0, (trimStartMs + requestedOffsetMs) / 1000);
          if (offsetSec >= audioBuffer.duration) {
            await scheduleChunk(index + 1, null);
            return;
          }

          const manifestDurationMs = Number(chunk.playable_duration_ms);
          const playableRemainingSec = Number.isFinite(manifestDurationMs) && manifestDurationMs > 0
            ? Math.max(0, (manifestDurationMs - requestedOffsetMs) / 1000)
            : audioBuffer.duration - offsetSec;
          const durationSec = Math.min(playableRemainingSec, audioBuffer.duration - offsetSec);
          if (durationSec <= 0.05) {
            await scheduleChunk(index + 1, null);
            return;
          }

          const source = context.createBufferSource();
          const startsAt = context.currentTime + 0.035;
          const timelineStartMs = bounds.startMs + requestedOffsetMs;
          source.buffer = audioBuffer;
          source.connect(context.destination);
          source.onended = () => {
            if (runId === playbackRunRef.current) {
              scheduleChunk(index + 1, null);
            }
          };
          playbackSourcesRef.current = [source];
          playbackTimelineRef.current = [{
            contextStartSec: startsAt,
            timelineStartMs,
            timelineEndMs: timelineStartMs + durationSec * 1000,
          }];
          source.start(startsAt, offsetSec, durationSec);

          const nextChunk = playableChunks[index + 1];
          if (nextChunk) {
            decodePlaybackChunk(context, nextChunk).catch(() => {});
          }

          setPlaying(true);
          setPlaybackBusy(false);
          setPlaybackStatus(`回放中 · 第 ${index + 1}/${playableChunks.length} 段`);
          firstChunkScheduled = true;

          if (!playbackProgressTimerRef.current) {
            playbackProgressTimerRef.current = window.setInterval(() => {
              const range = playbackTimelineRef.current[0];
              if (!range) return;
              const elapsedMs = Math.max(0, (context.currentTime - range.contextStartSec) * 1000);
              setPlaybackPositionMs(Math.min(range.timelineEndMs, range.timelineStartMs + elapsedMs));
            }, 80);
          }
        } catch {
          await scheduleChunk(index + 1, null);
        }
      };

      const startIndex = findPlaybackChunkIndex(playableChunks, targetMs);
      await scheduleChunk(startIndex, targetMs);
      if (!firstChunkScheduled) {
        await stopPlayback("暂无可播放音频");
      }
    } catch (err) {
      setError(userFriendlyError(err.message));
      await stopPlayback("回放出错");
    } finally {
      setPlaybackBusy(false);
    }
  }, [decodePlaybackChunk, meeting?.id, playbackBusy, stopPlayback]);

  const playMeeting = useCallback(async () => {
    const isCurrentPlaybackMeeting = Boolean(meeting?.id && playbackMeetingId === meeting.id);
    if (playing && isCurrentPlaybackMeeting) {
      await stopPlayback("已暂停", { preservePosition: true, preserveMeeting: true, clearCache: false });
      return;
    }
    const resumeMs = isCurrentPlaybackMeeting && Number.isFinite(playbackPositionMs)
      ? playbackPositionMs
      : playbackPreview.meetingId === meeting?.id && Number.isFinite(playbackPreview.positionMs)
        ? playbackPreview.positionMs
        : 0;
    await startPlaybackAt(resumeMs);
  }, [meeting?.id, playbackMeetingId, playbackPositionMs, playbackPreview, playing, startPlaybackAt, stopPlayback]);

  const previewPlaybackAt = useCallback((startMs) => {
    const value = Number(startMs);
    if (!Number.isFinite(value)) return;
    const next = Math.max(0, value);
    if (meeting?.id && playbackMeetingId === meeting.id) {
      setPlaybackPositionMs(next);
    } else if (meeting?.id) {
      setPlaybackPreview({ meetingId: meeting.id, positionMs: next });
    }
  }, [meeting?.id, playbackMeetingId]);

  const playFromTranscript = useCallback(
    async (event, startMs) => {
      event.stopPropagation();
      const value = Number(startMs);
      if (!Number.isFinite(value)) return;
      await startPlaybackAt(value);
    },
    [startPlaybackAt],
  );

  const uploadAudioFile = useCallback(
    async (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      if (importingAudio) return;
      if (recording) {
        setError("录音中不能导入音频，请先停止当前录音。");
        return;
      }
      if (!serviceReady) {
        setError("本地语音服务还在启动中，请稍候。");
        return;
      }
      setError("");
      setImportingAudio(true);
      setPipelineStatus("准备导入音频");
      try {
        if (!(await ensureRecordingModels())) return;
        const importedTitle = titleFromAudioFile(file);
        let id = null;
        if (!id) {
          const created = await api("/api/meetings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: importedTitle }),
          });
          setMeeting(created);
          setTitle(created.title || importedTitle);
          meetingIdRef.current = created.id;
          id = created.id;
        }

        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextCtor) {
          throw new Error("当前浏览器不支持读取这个音频文件。");
        }
        const context = new AudioContextCtor();
        const audioBuffer = await context.decodeAudioData(await file.arrayBuffer());
        await context.close();
        const samples = audioBufferToMono(audioBuffer);
        const slicedChunks = makeFixedChunks(samples, audioBuffer.sampleRate, recordingConfigRef.current);
        chunkSeqRef.current = 0;
        setPipelineStatus(`正在整理音频 · ${slicedChunks.length} 段`);
        for (const item of slicedChunks) {
          chunkSeqRef.current += 1;
          const blob = encodeWav(item.samples, audioBuffer.sampleRate);
          await enqueueChunk(blob, item.endedAtMs - item.startedAtMs, {
            clientChunkId: `${id}-import-${chunkSeqRef.current}`,
            startedAtMs: item.startedAtMs,
            endedAtMs: item.endedAtMs,
            cutReason: item.cutReason,
          });
        }
        await refreshMeeting(id);
        await refreshRuntime(id);
        await refreshMeetings();
      } catch (err) {
        setPipelineStatus("导入失败");
        setError(userFriendlyError(err.message));
      } finally {
        setImportingAudio(false);
      }
    },
    [enqueueChunk, ensureRecordingModels, importingAudio, recording, refreshMeeting, refreshMeetings, refreshRuntime, serviceReady],
  );

  const downloadMeetingFile = useCallback(
    async (kind) => {
      const id = meeting?.id;
      if (!id || downloadBusy) return;
      const titleBase = safeDownloadName(meeting?.title || "今天的会议");
      const isTranscript = kind === "transcript";
      const url = `${API_BASE}/api/meetings/${id}/${isTranscript ? "transcript.md" : "export.md"}`;
      const filename = isTranscript ? `${titleBase}-逐字稿.md` : `${titleBase}.md`;
      setDownloadBusy(kind);
      setError("");
      try {
        const content = await fetchTextFile(url);
        const saved = await saveTextFile(filename, content);
        if (saved) {
          setPipelineStatus(isTranscript ? "逐字稿已保存" : "会议纪要已保存");
        }
      } catch (err) {
        setError(userFriendlyError(err.message));
      } finally {
        setDownloadBusy("");
      }
    },
    [downloadBusy, meeting?.id, meeting?.title],
  );
  const downloadTranscript = useCallback(() => downloadMeetingFile("transcript"), [downloadMeetingFile]);
  const downloadNotes = useCallback(() => downloadMeetingFile("notes"), [downloadMeetingFile]);

  const micLevel = Math.min(1, Math.max(0.08, vadLevel * 18));
  const missingRecordingModels = missingModelsForConfig(recordingConfig);
  const normalizedRecordingConfig = clampRecordingConfig(recordingConfig);
  const maxSegmentSeconds = Math.round(normalizedRecordingConfig.maxSegmentMs / 1000);
  const selectedMicDeviceIndex = micDevices.findIndex((device) => device.deviceId === selectedMicId);
  const selectedMicLabel = selectedMicDeviceIndex >= 0
    ? micDeviceLabel(micDevices[selectedMicDeviceIndex], selectedMicDeviceIndex)
    : selectedMicId
      ? "已选麦克风"
      : "系统默认麦克风";
  const currentMicLabel = activeMicLabel || selectedMicLabel;
  const currentInputGainLabel = inputGainName(normalizedRecordingConfig.inputGain);
  const activeAsrModelMeta = modelCatalogByKey.get(`asr:${normalizedRecordingConfig.asrModel}`);
  const recordingAsrModelValue = selectableAsrModels.includes(recordingConfig.asrModel)
    ? recordingConfig.asrModel
    : "";
  const currentRecordingSummary = [
    activeAsrModelMeta?.label || asrModelName(normalizedRecordingConfig.asrModel),
    languageName(normalizedRecordingConfig.language),
    speakerModeName(normalizedRecordingConfig.speakerMode),
  ].join(" · ");

  const openRecordingSettings = useCallback(() => {
    setSettingsTab("recording");
    setSettingsOpen(true);
  }, []);

  const toggleAppearanceTheme = useCallback(() => {
    setAppearance((current) => clampAppearance({
      ...current,
      theme: current.theme === "dark" ? "light" : "dark",
    }));
  }, []);

  const toggleLocale = useCallback(() => {
    setLocale((current) => (current === "zh" ? "en" : "zh"));
  }, []);

  return (
    <I18nProvider locale={locale}>
    <div className="app-shell" data-theme={appearance.theme} data-palette={appearance.palette} data-locale={locale}>
      <Sidebar
        meeting={meeting}
        meetings={meetings}
        title={title}
        setTitle={setTitle}
        titleDirty={titleDirty}
        titleSaving={titleSaving}
        titleSavedAt={titleSavedAt}
        saveMeetingTitle={saveMeetingTitle}
        reprocessWorking={reprocessWorking}
        activeTranscriptVersion={activeTranscriptVersion}
        editableVersion={editableVersion}
        activateTranscriptVersion={activateTranscriptVersion}
        editBusy={editBusy}
        onOpenRecordingSettings={openRecordingSettings}
        recording={recording}
        currentRecordingSummary={currentRecordingSummary}
        maxSegmentSeconds={maxSegmentSeconds}
        startReprocess={startReprocess}
        transcriptItems={transcriptItems}
        createEditableVersion={createEditableVersion}
        finalizing={finalizing}
        finalize={finalize}
        speakerRenameFrom={speakerRenameFrom}
        setSpeakerRenameFrom={setSpeakerRenameFrom}
        speakerRenameTo={speakerRenameTo}
        setSpeakerRenameTo={setSpeakerRenameTo}
        speakerOptions={speakerOptions}
        renameSpeaker={renameSpeaker}
        serviceReady={serviceReady}
        busy={busy}
        importingAudio={importingAudio}
        startMeeting={startMeeting}
        stopRecording={stopRecording}
        uploadAudioFile={uploadAudioFile}
        pipelineStatus={pipelineStatus}
        runtimeStatus={runtimeStatus}
        pendingChunks={pendingChunks}
        micLevel={micLevel}
        currentMicLabel={currentMicLabel}
        currentInputGainLabel={currentInputGainLabel}
        activePipelineStep={activePipelineStep}
        refreshMeetings={refreshMeetings}
        loadMeeting={loadMeeting}
        playbackMeetingId={playbackMeetingId}
        playbackPositionMs={playbackPositionMs}
        playbackDurationMs={playbackDurationMs}
        playbackPlaying={playing}
        playbackBusy={playbackBusy}
      />

      <div className="main-area">
        <TopBar
          meeting={meeting}
          transcriptCount={transcriptItems.length}
          chunkCount={chunks.length}
          status={status}
          llmStatus={llmStatus}
          servicePillClass={servicePillClass}
          asrWorking={asrWorking}
          runtimeStatus={runtimeStatus}
          pendingChunks={pendingChunks}
          activeModelDownload={activeModelDownload}
          activeModelDownloadMeta={activeModelDownloadMeta}
          recording={recording}
          micLevel={micLevel}
          appearance={appearance}
          onToggleTheme={toggleAppearanceTheme}
          onToggleLanguage={toggleLocale}
          onOpenSettings={openRecordingSettings}
        />

        <StartupBanner
          serviceReady={serviceReady}
          serviceStarting={serviceStarting}
          backendDetail={status.backendDetail}
          onRefresh={refreshStatus}
        />

        <MeetingTimeline
          meeting={meeting}
          transcriptItems={transcriptItems}
          chunks={chunks}
          playbackPositionMs={visiblePlaybackPositionMs}
          playing={visiblePlaybackPlaying}
          playbackBusy={visiblePlaybackBusy}
          recording={recording}
          liveWaveformBars={liveWaveformBars}
          liveRecordingMs={liveRecordingMs}
          onPlayToggle={playMeeting}
          onPreview={previewPlaybackAt}
          onJump={startPlaybackAt}
        />

        <main className="main-workspace">
          <TranscriptPane
            meeting={meeting}
            activeTranscriptVersion={activeTranscriptVersion}
            transcriptVersions={meeting?.transcript_versions || []}
            activeVersionId={meeting?.active_version_id || "auto"}
            activateTranscriptVersion={activateTranscriptVersion}
            lastAsr={lastAsr}
            asrLanguage={asrLanguage}
            downloadTranscript={downloadTranscript}
            transcriptDownloading={downloadBusy === "transcript"}
            recording={recording}
            reprocessWorking={reprocessWorking}
            startReprocess={startReprocess}
            createEditableVersion={createEditableVersion}
            error={error}
            asrWorking={asrWorking}
            runtimeStatus={runtimeStatus}
            pendingChunks={pendingChunks}
            transcriptItems={transcriptItems}
            onOpenMeetingProperties={openMeetingProperties}
            playbackPositionMs={visiblePlaybackPositionMs}
            editingSegmentId={editingSegmentId}
            editableVersion={editableVersion}
            editBusy={editBusy}
            saveSegmentEdits={saveSegmentEdits}
            cancelEditSegment={cancelEditSegment}
            startEditSegment={startEditSegment}
            segmentDrafts={segmentDrafts}
            updateSegmentDraft={updateSegmentDraft}
            playFromTranscript={playFromTranscript}
          />

          <NotesPane
            finalNotesWorking={finalNotesWorking}
            finalize={finalize}
            meeting={meeting}
            recording={recording}
            finalizing={finalizing}
            downloadNotes={downloadNotes}
            notesDownloading={downloadBusy === "notes"}
            finalNotesReady={finalNotesReady}
            activeTranscriptVersion={activeTranscriptVersion}
            finalMarkdownForDisplay={finalMarkdownForDisplay}
            finalNotesStreaming={finalNotesStreaming}
            finalNotesPending={finalNotesPending}
            transcriptItems={transcriptItems}
          />
        </main>
      </div>

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        llmConfigSaving={llmConfigSaving}
        settingsTab={settingsTab}
        setSettingsTab={setSettingsTab}
        missingRecordingModels={missingRecordingModels}
        selectedMicId={selectedMicId}
        selectMicDevice={selectMicDevice}
        recording={recording}
        micDevices={micDevices}
        recordingAsrModelValue={recordingAsrModelValue}
        updateRecordingConfig={updateRecordingConfig}
        loadRecordingAsrModel={loadRecordingAsrModel}
        modelLoading={modelLoadState?.status === "loading"}
        selectableAsrModels={selectableAsrModels}
        asrModelGroups={asrModelGroups}
        modelCatalogByKey={modelCatalogByKey}
        recordingConfig={recordingConfig}
        ensureRecordingModels={ensureRecordingModels}
        activeModelDownload={activeModelDownload}
        saveLlmConfig={saveLlmConfig}
        llmConfig={llmConfig}
        llmConfigDraft={llmConfigDraft}
        updateLlmConfigDraft={updateLlmConfigDraft}
        llmConfigError={llmConfigError}
        promptConfig={promptConfig}
        promptDrafts={promptDrafts}
        promptConfigSaving={promptConfigSaving}
        promptConfigError={promptConfigError}
        updatePromptDraft={updatePromptDraft}
        resetPromptDraft={resetPromptDraft}
        savePromptConfig={savePromptConfig}
        refreshPromptConfig={refreshPromptConfig}
        refreshStatus={refreshStatus}
        modelCatalogAsrGroups={modelCatalogAsrGroups}
        modelCatalog={modelCatalog}
        downloadModel={downloadModel}
        deleteModel={deleteModel}
        appearance={appearance}
        updateAppearance={updateAppearance}
      />

      <MeetingPropertiesDialog
        open={propertiesOpen}
        meeting={meeting}
        draft={propertiesDraft}
        saving={propertiesSaving}
        error={propertiesError}
        onChange={updatePropertiesDraft}
        onCancel={closeMeetingProperties}
        onSave={saveMeetingProperties}
        onDelete={requestDeleteCurrentMeeting}
      />

      <DeleteMeetingDialog
        deleteTarget={deleteTarget}
        deleteBusy={deleteBusy}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={deleteMeeting}
      />

      <ModelLoadDialog
        state={modelLoadState}
        onClose={() => setModelLoadState(null)}
      />
    </div>
    </I18nProvider>
  );
}

export default App;
