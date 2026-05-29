import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, SkipBack, SkipForward } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatOffset, transcriptParts } from "@/lib/meeting-display";
import { useI18n } from "@/lib/i18n";

const TimelineThreeCanvas = lazy(() => (
  import("@/components/meeting/TimelineThreeCanvas").then((module) => ({
    default: module.TimelineThreeCanvas,
  }))
));

const GHOST_BARS = [
  0.38, 0.54, 0.42, 0.76, 0.48, 0.88, 0.58, 0.34, 0.68, 0.9, 0.5, 0.72,
  0.44, 0.62, 0.82, 0.36, 0.66, 0.78, 0.46, 0.92, 0.56, 0.4, 0.74, 0.52,
];
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8788";
const WAVEFORM_BAR_COUNT = 256;
const TIMELINE_EDGE_PAD_PX = 8;
const JUMP_STEPS = [5000, 10000, 20000];
const TOPIC_COMMON_TERMS = new Set([
  "一个",
  "这个",
  "那个",
  "这里",
  "那里",
  "是否",
  "关于",
  "以及",
  "可能",
  "进行",
  "讨论",
  "会议",
  "关键",
  "议题",
]);

function cleanInline(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function excerpt(text, length = 20) {
  const clean = cleanInline(text).replace(/^[-*]\s*/, "");
  if (clean.length <= length) return clean;
  return `${clean.slice(0, length).trim()}...`;
}

function escapeRegex(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function chunkEndMs(chunk) {
  const ended = Number(chunk?.ended_at_ms);
  if (Number.isFinite(ended)) return ended;
  const started = Number(chunk?.started_at_ms) || 0;
  const duration = Number(chunk?.duration_ms) || 0;
  return started + duration;
}

function chunkStartMs(chunk, fallback = 0) {
  const started = Number(chunk?.started_at_ms);
  if (Number.isFinite(started)) return started;
  return fallback;
}

function resolvePartMs(value, part, chunkById) {
  const raw = Number(value);
  if (!Number.isFinite(raw)) return raw;
  const chunk = part?.chunk_id ? chunkById.get(part.chunk_id) : null;
  const chunkStart = Number(chunk?.started_at_ms);
  const chunkDuration = Number(chunk?.duration_ms);
  if (
    chunk
    && Number.isFinite(chunkStart)
    && chunkStart > 0
    && raw < chunkStart - 250
    && (!Number.isFinite(chunkDuration) || raw <= chunkDuration + 1000)
  ) {
    return chunkStart + raw;
  }
  return raw;
}

function flattenParts(transcriptItems, chunks) {
  const chunkById = new Map(chunks.map((chunk) => [chunk.id, chunk]));
  return transcriptItems
    .flatMap((item) => (
      transcriptParts(item).map((part) => {
        const source = {
          ...part,
          chunk_id: part.chunk_id || item.chunk_id,
        };
        const startMs = resolvePartMs(source.start_ms, source, chunkById);
        const endMs = resolvePartMs(source.end_ms, source, chunkById);
        return {
          ...source,
          text: cleanInline(source.text || item.text),
          speaker: cleanInline(source.speaker || item.speaker || "Speaker 1"),
          start_ms: startMs,
          end_ms: Number.isFinite(endMs) && endMs > startMs ? endMs : startMs + 1200,
        };
      })
    ))
    .filter((part) => Number.isFinite(Number(part.start_ms)))
    .sort((left, right) => Number(left.start_ms) - Number(right.start_ms));
}

function orderedSpeakers(parts) {
  const labels = [];
  for (const part of parts) {
    if (part.speaker && !labels.includes(part.speaker)) labels.push(part.speaker);
  }
  return labels;
}

function markdownSectionItems(markdown, heading) {
  const text = String(markdown || "");
  const pattern = new RegExp(`^##\\s*${escapeRegex(heading)}\\s*$([\\s\\S]*?)(?=^##\\s+|$)`, "mu");
  const match = text.match(pattern);
  if (!match) return [];
  return match[1]
    .split("\n")
    .map((line) => line.replace(/^[-*]\s*/, "").trim())
    .filter(Boolean)
    .filter((line) => !/^(暂无|待确认)$/u.test(line));
}

function textHash(text) {
  let hash = 0;
  for (const char of String(text || "")) {
    hash = (hash * 31 + char.charCodeAt(0)) % 9973;
  }
  return hash;
}

function topicLabelsFromMeeting(meeting) {
  const summaryTopics = Array.isArray(meeting?.summary?.topics) ? meeting.summary.topics : [];
  const labels = summaryTopics.map(cleanInline).filter(Boolean);
  if (labels.length) return labels;
  return markdownSectionItems(meeting?.final_markdown, "关键议题").slice(0, 6);
}

function topicTokens(label) {
  const clean = cleanInline(label)
    .replace(/[“”"'《》（）()、，。；;：:]/g, " ");
  const tokens = new Set();
  for (const token of clean.split(/\s+/u)) {
    const trimmed = token.trim();
    if (trimmed.length >= 2 && !TOPIC_COMMON_TERMS.has(trimmed)) tokens.add(trimmed);
  }
  const cjkRuns = clean.match(/[\u4e00-\u9fff]{2,}/g) || [];
  for (const run of cjkRuns) {
    for (let index = 0; index < run.length - 1; index += 1) {
      const token = run.slice(index, index + 2);
      if (!TOPIC_COMMON_TERMS.has(token)) tokens.add(token);
    }
  }
  return [...tokens].slice(0, 18);
}

function findTopicStart(label, parts, minStartMs) {
  const tokens = topicTokens(label);
  if (!tokens.length) return null;
  let best = null;
  for (const part of parts) {
    const start = Number(part.start_ms) || 0;
    if (start < minStartMs) continue;
    const text = cleanInline(part.text);
    let score = 0;
    for (const token of tokens) {
      if (text.includes(token)) score += Math.min(8, token.length);
    }
    if (score > 0 && (!best || score > best.score)) {
      best = { start, score };
    }
  }
  return best?.start ?? null;
}

function fallbackTopicBands(parts, durationMs) {
  if (!parts.length) {
    return [{ label: "等待第一段文字", start: 0, end: durationMs }];
  }
  const groups = [];
  let current = [];
  for (const part of parts) {
    const previous = current[current.length - 1];
    const gap = previous ? Number(part.start_ms) - Number(previous.end_ms) : 0;
    const groupStart = current[0] ? Number(current[0].start_ms) : Number(part.start_ms);
    const groupDuration = Number(part.end_ms) - groupStart;
    if (current.length && (gap > 6500 || (groups.length < 4 && groupDuration > durationMs / 4))) {
      groups.push(current);
      current = [];
    }
    current.push(part);
  }
  if (current.length) groups.push(current);
  return groups.slice(0, 5).map((group, index, visibleGroups) => {
    const start = Number(group[0].start_ms) || 0;
    const naturalEnd = Number(group[group.length - 1].end_ms) || start;
    const nextStart = visibleGroups[index + 1] ? Number(visibleGroups[index + 1][0].start_ms) : durationMs;
    const longest = group.reduce((best, part) => (
      cleanInline(part.text).length > cleanInline(best.text).length ? part : best
    ), group[0]);
    return {
      label: excerpt(longest.text, 18),
      start,
      end: Math.max(naturalEnd, nextStart),
    };
  });
}

function topicBands(parts, durationMs, meeting) {
  const labels = topicLabelsFromMeeting(meeting).slice(0, 5);
  if (!labels.length) return fallbackTopicBands(parts, durationMs);
  const estimatedStep = durationMs / labels.length;
  let previousStart = -1;
  const starts = labels.map((label, index) => {
    const matched = findTopicStart(label, parts, Math.max(0, previousStart + 1000));
    const fallback = Math.max(previousStart + 1000, estimatedStep * index);
    const start = Math.min(durationMs, matched ?? fallback);
    previousStart = start;
    return start;
  });
  return labels
    .map((label, index) => ({
      label: excerpt(label, 18),
      start: starts[index],
      end: starts[index + 1] ?? durationMs,
    }))
    .filter((band) => band.end > band.start);
}

function timelineMarks(parts) {
  const marks = [];
  let previous = null;
  for (const part of parts) {
    const start = Number(part.start_ms) || 0;
    const text = cleanInline(part.text);
    if (previous && part.speaker !== previous.speaker) {
      marks.push({ at: start, type: "speaker" });
    } else if (previous && start - Number(previous.end_ms) > 3500) {
      marks.push({ at: start, type: "gap" });
    }
    if (/[?？]|(吗|是否|有没有|怎么|如何|什么)/u.test(text)) {
      marks.push({ at: start, type: "question" });
    }
    previous = part;
  }
  const filtered = [];
  for (const mark of marks) {
    if (filtered.every((item) => Math.abs(item.at - mark.at) > 2500)) filtered.push(mark);
    if (filtered.length >= 8) break;
  }
  return filtered;
}

function percentile(values, ratio) {
  const sorted = values
    .filter((value) => Number.isFinite(value) && value > 0)
    .sort((left, right) => left - right);
  if (!sorted.length) return 1;
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * ratio)));
  return sorted[index] || sorted[sorted.length - 1] || 1;
}

function smoothAmplitudes(values) {
  return values.map((value, index) => {
    const prev = values[index - 1] ?? value;
    const next = values[index + 1] ?? value;
    return prev * 0.1 + value * 0.8 + next * 0.1;
  });
}

function clamp01(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}

function shapeAudioAmplitude(value, floor, ceiling) {
  if (!Number.isFinite(value) || value <= 0) return 0;
  const range = Math.max(ceiling - floor, ceiling * 0.25, 0.000001);
  const normalized = clamp01((value - floor) / range);
  if (normalized <= 0) return 0;
  const dbNorm = clamp01((20 * Math.log10(Math.max(normalized, 0.0001)) + 46) / 46);
  const powerNorm = Math.pow(normalized, 1.55);
  return Math.max(0.018, Math.min(1, powerNorm * 0.86 + dbNorm * 0.14));
}

async function decodeMeetingWaveform(meetingId, chunks, durationMs, signal, onProgress) {
  if (!meetingId || !chunks.length || durationMs <= 0) return null;
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) return null;

  const bins = Array.from({ length: WAVEFORM_BAR_COUNT }, () => ({
    peak: 0,
    rms: 0,
    samples: 0,
    hasAudio: false,
  }));
  const context = new AudioContextCtor();
  let fallbackStartMs = 0;
  let completed = 0;
  const total = Math.max(1, chunks.filter((chunk) => chunk?.id).length);
  const markProgress = () => {
    completed += 1;
    onProgress?.(Math.max(0, Math.min(1, completed / total)));
  };
  try {
    for (const chunk of chunks) {
      if (signal?.aborted) return null;
      if (!chunk?.id) continue;
      try {
        const startMs = chunkStartMs(chunk, fallbackStartMs);
        const endMs = Math.max(startMs, chunkEndMs(chunk) || startMs);
        fallbackStartMs = endMs;
        if (endMs <= startMs) continue;

        const response = await fetch(`${API_BASE}/api/meetings/${meetingId}/chunks/${chunk.id}/audio`, { signal });
        if (!response.ok) continue;
        const audioBuffer = await context.decodeAudioData(await response.arrayBuffer());
        if (signal?.aborted) return null;
        const channel = audioBuffer.getChannelData(0);
        const sampleRate = audioBuffer.sampleRate;
        const binStart = Math.max(0, Math.floor(startMs * WAVEFORM_BAR_COUNT / durationMs));
        const binEnd = Math.min(
          WAVEFORM_BAR_COUNT - 1,
          Math.ceil(endMs * WAVEFORM_BAR_COUNT / durationMs),
        );

        for (let index = binStart; index <= binEnd; index += 1) {
          const windowStartMs = durationMs * index / WAVEFORM_BAR_COUNT;
          const windowEndMs = durationMs * (index + 1) / WAVEFORM_BAR_COUNT;
          const localStartSec = Math.max(0, (windowStartMs - startMs) / 1000);
          const localEndSec = Math.min(audioBuffer.duration, (windowEndMs - startMs) / 1000);
          if (localEndSec <= localStartSec) continue;

          const startSample = Math.max(0, Math.floor(localStartSec * sampleRate));
          const endSample = Math.min(channel.length, Math.ceil(localEndSec * sampleRate));
          const stride = Math.max(1, Math.floor((endSample - startSample) / 240));
          let peak = 0;
          let sum = 0;
          let count = 0;
          for (let sample = startSample; sample < endSample; sample += stride) {
            const value = Math.abs(channel[sample] || 0);
            if (value > peak) peak = value;
            sum += value * value;
            count += 1;
          }
          if (!count) continue;
          const rms = Math.sqrt(sum / count);
          bins[index].peak = Math.max(bins[index].peak, peak);
          bins[index].rms = Math.max(bins[index].rms, rms);
          bins[index].samples += count;
          bins[index].hasAudio = true;
        }
      } finally {
        markProgress();
      }
    }
  } finally {
    context.close().catch(() => {});
  }

  const raw = bins.map((bin) => (bin.hasAudio ? Math.max(bin.peak * 0.82, bin.rms * 3.2) : 0));
  const floor = percentile(raw, 0.16) * 0.9;
  const ceiling = percentile(raw, 0.965);
  const normalized = smoothAmplitudes(raw.map((value) => shapeAudioAmplitude(value, floor, ceiling)));
  return normalized.map((amplitude, index) => ({
    amplitude,
    hasAudio: bins[index].hasAudio,
  }));
}

export function MeetingTimeline({
  meeting,
  transcriptItems,
  chunks,
  playbackPositionMs,
  playing,
  playbackBusy,
  recording = false,
  liveWaveformBars = [],
  liveRecordingMs = 0,
  onPlayToggle,
  onPreview,
  onJump,
}) {
  const { t } = useI18n();
  const [jumpStepMs, setJumpStepMs] = useState(10000);
  const [audioWaveform, setAudioWaveform] = useState(null);
  const [waveformLoading, setWaveformLoading] = useState(false);
  const [waveformProgress, setWaveformProgress] = useState(0);
  const [scrubbing, setScrubbing] = useState(false);
  const [scrubMs, setScrubMs] = useState(null);
  const [hoverMs, setHoverMs] = useState(null);
  const waveformCacheRef = useRef(new Map());
  const parts = useMemo(() => flattenParts(transcriptItems, chunks), [chunks, transcriptItems]);
  const lastPartEnd = Math.max(0, ...parts.map((part) => Number(part.end_ms) || Number(part.start_ms) || 0));
  const lastChunkEnd = Math.max(0, ...chunks.map(chunkEndMs));
  const liveMode = Boolean(recording);
  const liveBars = Array.isArray(liveWaveformBars) ? liveWaveformBars : [];
  const baseDurationMs = Math.max(lastPartEnd, lastChunkEnd, parts.length || chunks.length ? 1000 : 120000);
  const durationMs = liveMode ? Math.max(Number(liveRecordingMs) || 0, lastChunkEnd, 1000) : baseDurationMs;
  const playheadMs = liveMode ? durationMs : Number.isFinite(playbackPositionMs) ? playbackPositionMs : 0;
  const displayPlayheadMs = liveMode ? durationMs : scrubbing && Number.isFinite(scrubMs) ? scrubMs : playheadMs;
  const pct = (value) => Math.max(0, Math.min(100, (Number(value) || 0) * 100 / durationMs));
  const speakerNames = useMemo(() => orderedSpeakers(parts), [parts]);
  const speakerIndexByName = useMemo(
    () => new Map(speakerNames.map((speaker, index) => [speaker, index])),
    [speakerNames],
  );
  const topics = useMemo(() => (
    liveMode && !parts.length
      ? [{ label: "等待第一段文字", start: 0, end: durationMs }]
      : topicBands(parts, durationMs, meeting)
  ), [durationMs, liveMode, meeting, parts]);
  const marks = useMemo(() => (
    timelineMarks(parts).map((mark) => ({
      ...mark,
      ratio: durationMs > 0 ? (Number(mark.at) || 0) / durationMs : 0,
    }))
  ), [durationMs, parts]);
  const chunkSignature = useMemo(() => (
    chunks.map((chunk) => `${chunk.id}:${chunk.started_at_ms || 0}:${chunk.ended_at_ms || chunk.duration_ms || 0}`).join("|")
  ), [chunks]);
  const waveformChunks = useMemo(() => (
    chunks.map((chunk) => ({
      id: chunk.id,
      started_at_ms: chunk.started_at_ms,
      ended_at_ms: chunk.ended_at_ms,
      duration_ms: chunk.duration_ms,
    }))
  ), [chunkSignature]);

  useEffect(() => {
    if (liveMode) {
      setAudioWaveform(null);
      setWaveformLoading(false);
      setWaveformProgress(0);
      return undefined;
    }
    if (!meeting?.id || !chunkSignature) {
      setAudioWaveform(null);
      setWaveformLoading(false);
      setWaveformProgress(0);
      return undefined;
    }
    const cacheKey = `${meeting.id}:${chunkSignature}:${durationMs}`;
    const cached = waveformCacheRef.current.get(cacheKey);
    if (cached) {
      setAudioWaveform(cached);
      setWaveformLoading(false);
      setWaveformProgress(1);
      return undefined;
    }

    const controller = new AbortController();
    setAudioWaveform(null);
    setWaveformLoading(true);
    setWaveformProgress(0);
    decodeMeetingWaveform(meeting.id, waveformChunks, durationMs, controller.signal, (progress) => {
      if (!controller.signal.aborted) setWaveformProgress(progress);
    })
      .then((waveform) => {
        if (!controller.signal.aborted) {
          if (waveform) {
            if (waveformCacheRef.current.size > 8) {
              waveformCacheRef.current.delete(waveformCacheRef.current.keys().next().value);
            }
            waveformCacheRef.current.set(cacheKey, waveform);
          }
          setAudioWaveform(waveform);
          setWaveformProgress(waveform ? 1 : 0);
          setWaveformLoading(false);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setAudioWaveform(null);
          setWaveformLoading(false);
          setWaveformProgress(0);
        }
      });
    return () => controller.abort();
  }, [chunkSignature, durationMs, liveMode, meeting?.id, waveformChunks]);

  const bars = useMemo(() => (
    Array.from({ length: WAVEFORM_BAR_COUNT }, (_, index) => {
      if (liveMode) {
        const amplitude = Number(liveBars[index]);
        const active = Number.isFinite(amplitude) && amplitude > 0;
        return {
          amplitude: active ? amplitude : 0.035,
          active,
          hasAudio: active,
          speakerIndex: 0,
        };
      }
      const centerMs = durationMs * (index + 0.5) / WAVEFORM_BAR_COUNT;
      const part = parts.find((item) => {
        const start = Number(item.start_ms) || 0;
        const end = Number(item.end_ms) || start + 1200;
        return centerMs >= start && centerMs <= end;
      });
      const audioBin = audioWaveform?.[index];
      let amplitude = audioBin?.amplitude;
      if (!Number.isFinite(amplitude)) {
        const ghost = GHOST_BARS[index % GHOST_BARS.length];
        const start = Number(part?.start_ms) || 0;
        const end = Number(part?.end_ms) || start + 1200;
        const progress = part ? Math.max(0, Math.min(1, (centerMs - start) / Math.max(1, end - start))) : 0;
        const envelope = part ? Math.sin(progress * Math.PI) : 0;
        const phraseHash = textHash(part?.text);
        const localWave = Math.abs(Math.sin((progress * 7.5 + phraseHash * 0.001) * Math.PI));
        const pulse = Math.abs(Math.sin(index * 1.91 + phraseHash * 0.013)) * 0.13;
        const textWeight = part ? Math.min(0.16, cleanInline(part.text).length / 320) : 0;
        amplitude = Math.max(
          0.06,
          Math.min(1, part ? 0.1 + envelope * (0.2 + localWave * 0.72) + pulse + textWeight : ghost * 0.18),
        );
      }
      const speakerIndex = part?.speaker ? speakerIndexByName.get(part.speaker) ?? 0 : -1;
      return {
        amplitude,
        active: Boolean(part) && amplitude > 0.025,
        hasAudio: Boolean(audioBin?.hasAudio),
        speakerIndex,
      };
    })
  ), [audioWaveform, durationMs, liveBars, liveMode, parts, speakerIndexByName]);

  const jumpBy = (direction) => {
    onJump(Math.max(0, Math.min(durationMs, playheadMs + direction * jumpStepMs)));
  };
  const pointerToMs = (event) => {
    const track = event.currentTarget.querySelector(".tl-track");
    const rect = (track || event.currentTarget).getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / Math.max(1, rect.width);
    return Math.max(0, Math.min(durationMs, ratio * durationMs));
  };
  const previewAt = (value) => {
    const next = Math.max(0, Math.min(durationMs, Number(value) || 0));
    setScrubMs(next);
    onPreview?.(next);
    return next;
  };
  const startScrub = (event) => {
    if (liveMode || (!parts.length && !chunks.length)) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setScrubbing(true);
    previewAt(pointerToMs(event));
  };
  const movePointer = (event) => {
    if (liveMode) return;
    const next = pointerToMs(event);
    setHoverMs(next);
    if (!scrubbing) return;
    event.preventDefault();
    previewAt(next);
  };
  const endScrub = (event) => {
    if (!scrubbing) return;
    event.preventDefault();
    const next = previewAt(pointerToMs(event));
    setScrubbing(false);
    setScrubMs(null);
    onJump(next);
  };

  return (
    <section className="timeline-strip">
      <div className="timeline-head">
        <div>
          <span className="timeline-label">Acoustic timeline</span>
          <span className="tab-tag">{topics.length} topics</span>
        </div>
        {liveMode ? (
          <div className="tl-live-clock" aria-label={t("录制时长")}>
            <span>{t("录制中")}</span>
            <strong>{formatOffset(durationMs)}</strong>
          </div>
        ) : (
        <div className="tl-playback">
          <div className="tl-step-group" aria-label={t("跳转步长")}>
            {JUMP_STEPS.map((step) => (
              <button
                type="button"
                className={jumpStepMs === step ? "active" : ""}
                key={step}
                onClick={() => setJumpStepMs(step)}
                title={t("设置跳转 {seconds} 秒", { seconds: step / 1000 })}
              >
                {step / 1000}s
              </button>
            ))}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="tl-pb"
            onClick={() => jumpBy(-1)}
            disabled={!parts.length && !chunks.length}
            title={t("后退 {seconds} 秒", { seconds: jumpStepMs / 1000 })}
          >
            <SkipBack size={15} />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="tl-pb play"
            onClick={onPlayToggle}
            disabled={playbackBusy || (!parts.length && !chunks.length)}
            title={playing ? t("暂停回放") : t("开始回放")}
          >
            {playing ? <Pause size={14} /> : <Play size={14} />}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="tl-pb"
            onClick={() => jumpBy(1)}
            disabled={!parts.length && !chunks.length}
            title={t("快进 {seconds} 秒", { seconds: jumpStepMs / 1000 })}
          >
            <SkipForward size={15} />
          </Button>
          <span className="now">
            <span>{formatOffset(displayPlayheadMs)}</span>
            <span className="of"> / {formatOffset(durationMs)}</span>
          </span>
        </div>
        )}
      </div>

      <div
        className={`tl-canvas ${scrubbing ? "scrubbing" : ""} ${waveformLoading && !liveMode ? "loading-waveform" : ""} ${liveMode ? "live-recording" : ""}`}
        onPointerDown={startScrub}
        onPointerMove={movePointer}
        onPointerUp={endScrub}
        onPointerLeave={() => {
          if (!scrubbing) setHoverMs(null);
        }}
        onPointerCancel={() => {
          setScrubbing(false);
          setScrubMs(null);
          setHoverMs(null);
        }}
      >
        <div className="tl-topics" aria-hidden="true">
          {topics.map((topic) => (
            <span
              className="tl-topic"
              key={`${topic.start}-${topic.label}`}
              style={{
                "--left": `${pct(topic.start)}%`,
                "--width": `${Math.max(4, pct(topic.end) - pct(topic.start))}%`,
              }}
            >
              <span className="lbl">{topic.label}</span>
            </span>
          ))}
        </div>
        <div
          className={`tl-track ${waveformLoading && !liveMode ? "loading-waveform" : ""} ${liveMode ? "live-recording" : ""}`}
          style={{
            "--wave-progress": `${Math.round(waveformProgress * 100)}%`,
            "--tl-edge-pad": `${TIMELINE_EDGE_PAD_PX}px`,
          }}
        >
          <Suspense fallback={<div className="tl-three-host" aria-hidden="true" />}>
            <TimelineThreeCanvas
              bars={bars}
              marks={marks}
              playheadRatio={pct(displayPlayheadMs) / 100}
              hoverRatio={Number.isFinite(hoverMs) ? pct(hoverMs) / 100 : null}
              loadingProgress={waveformLoading && !liveMode ? waveformProgress : 0}
              edgePadPx={TIMELINE_EDGE_PAD_PX}
            />
          </Suspense>
          {waveformLoading && !liveMode && (
            <div className="tl-wave-loading" role="status" aria-live="polite">
              <span className="tl-load-dot" aria-hidden="true" />
              <span>{t("加载音频波形 {progress}%", { progress: Math.round(waveformProgress * 100) })}</span>
            </div>
          )}
        </div>
        <div className="tl-ruler">
          {[0, 0.25, 0.5, 0.75, 1].map((point) => (
            <span className="tl-ruler-tick" key={point} style={{ left: `${point * 100}%` }}>
              {formatOffset(durationMs * point)}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}
