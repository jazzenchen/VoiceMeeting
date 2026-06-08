import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, SkipBack, SkipForward } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatOffset } from "@/lib/meeting-display";
import { useI18n } from "@/lib/i18n";
import {
  GHOST_BARS,
  JUMP_STEPS,
  TIMELINE_EDGE_PAD_PX,
  WAVEFORM_BAR_COUNT,
  chunkEndMs,
  cleanInline,
  decodeMeetingWaveform,
  fetchMeetingAudioWaveform,
  flattenParts,
  orderedSpeakers,
  timelineMarks,
  textHash,
  topicBands,
} from "@/lib/timeline-model";

const TimelineThreeCanvas = lazy(() => (
  import("@/components/meeting/TimelineThreeCanvas").then((module) => ({
    default: module.TimelineThreeCanvas,
  }))
));

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
  const canonicalAudioUrl = liveMode ? "" : String(meeting?.audio?.audio_url || "");
  const canonicalAudioDurationMs = liveMode ? 0 : Number(meeting?.audio?.duration_ms) || 0;
  const hasCanonicalAudio = Boolean(canonicalAudioUrl && canonicalAudioDurationMs > 0);
  const liveBars = Array.isArray(liveWaveformBars) ? liveWaveformBars : [];
  const baseDurationMs = Math.max(
    canonicalAudioDurationMs,
    lastPartEnd,
    lastChunkEnd,
    parts.length || chunks.length || hasCanonicalAudio ? 1000 : 120000,
  );
  const durationMs = liveMode ? Math.max(Number(liveRecordingMs) || 0, lastChunkEnd, 1000) : baseDurationMs;
  const playheadMs = liveMode ? durationMs : Number.isFinite(playbackPositionMs) ? playbackPositionMs : 0;
  const displayPlayheadMs = liveMode ? durationMs : scrubbing && Number.isFinite(scrubMs) ? scrubMs : playheadMs;
  const canNavigate = !liveMode && (hasCanonicalAudio || parts.length > 0 || chunks.length > 0);
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
  const audioSignature = hasCanonicalAudio
    ? `${canonicalAudioUrl}:${canonicalAudioDurationMs}`
    : "";

  useEffect(() => {
    if (liveMode) {
      setAudioWaveform(null);
      setWaveformLoading(false);
      setWaveformProgress(0);
      return undefined;
    }
    if (!meeting?.id || (!audioSignature && !chunkSignature)) {
      setAudioWaveform(null);
      setWaveformLoading(false);
      setWaveformProgress(0);
      return undefined;
    }
    const cacheKey = `${meeting.id}:${audioSignature || chunkSignature}:${durationMs}`;
    const cached = waveformCacheRef.current.get(cacheKey);
    if (cached) {
      setAudioWaveform(cached);
      setWaveformLoading(false);
      setWaveformProgress(1);
      return undefined;
    }

    const controller = new AbortController();
    let progressTimer = 0;
    setAudioWaveform(null);
    setWaveformLoading(true);
    setWaveformProgress(0);
    if (audioSignature) {
      setWaveformProgress(0.06);
      progressTimer = window.setInterval(() => {
        if (controller.signal.aborted) return;
        setWaveformProgress((current) => (
          current >= 0.88
            ? current
            : Math.min(0.88, current + Math.max(0.01, (0.88 - current) * 0.08))
        ));
      }, 180);
    }
    const decode = audioSignature
      ? fetchMeetingAudioWaveform(meeting.id, audioSignature, controller.signal, (progress) => {
        if (!controller.signal.aborted) setWaveformProgress(progress);
      })
      : decodeMeetingWaveform(meeting.id, waveformChunks, durationMs, controller.signal, (progress) => {
        if (!controller.signal.aborted) setWaveformProgress(progress);
      });
    decode
      .then((waveform) => {
        if (progressTimer) window.clearInterval(progressTimer);
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
        if (progressTimer) window.clearInterval(progressTimer);
        if (!controller.signal.aborted) {
          setAudioWaveform(null);
          setWaveformLoading(false);
          setWaveformProgress(0);
        }
      });
    return () => {
      if (progressTimer) window.clearInterval(progressTimer);
      controller.abort();
    };
  }, [
    audioSignature,
    canonicalAudioUrl,
    chunkSignature,
    durationMs,
    liveMode,
    meeting?.id,
    waveformChunks,
  ]);

  const bars = useMemo(() => {
    if (!liveMode && waveformLoading && !audioWaveform) return [];
    if (!liveMode && !audioWaveform && !parts.length) return [];

    return Array.from({ length: WAVEFORM_BAR_COUNT }, (_, index) => {
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
        active: Boolean(part || audioBin?.hasAudio) && amplitude > 0.025,
        hasAudio: Boolean(audioBin?.hasAudio),
        speakerIndex,
      };
    });
  }, [audioWaveform, durationMs, liveBars, liveMode, parts, speakerIndexByName, waveformLoading]);

  const jumpBy = (direction) => {
    if (!canNavigate) return;
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
    if (!canNavigate) return;
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
        <div className="timeline-head-main">
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
            disabled={!canNavigate}
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
            disabled={playbackBusy || !canNavigate}
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
            disabled={!canNavigate}
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
