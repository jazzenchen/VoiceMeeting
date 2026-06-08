import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiUrl } from "@/lib/api-client";
import { userFriendlyError } from "@/lib/error-messages";
import { findPlaybackChunkIndex, playbackBounds } from "@/lib/meeting-state";

function globalPlaybackShortcutTarget(target) {
  if (!target || typeof target.closest !== "function") return false;
  if (target.closest(".rail-open")) return false;
  if (target.isContentEditable) return true;
  return Boolean(target.closest([
    "input",
    "textarea",
    "select",
    "button",
    "a",
    "[contenteditable='true']",
    "[role='textbox']",
    "[role='button']",
    "[role='menuitem']",
    "[role='option']",
  ].join(",")));
}

function waitForAudio(audio, eventName, isReady) {
  return new Promise((resolve, reject) => {
    if (isReady()) {
      resolve();
      return;
    }
    const cleanup = () => {
      audio.removeEventListener(eventName, handleReady);
      audio.removeEventListener("error", handleError);
    };
    const handleReady = () => {
      cleanup();
      resolve();
    };
    const handleError = () => {
      cleanup();
      reject(new Error("回放加载失败。"));
    };
    audio.addEventListener(eventName, handleReady);
    audio.addEventListener("error", handleError);
  });
}

export function useMeetingPlayback({
  audioMergeState,
  deleteTarget,
  meeting,
  meetingIdRef,
  propertiesOpen,
  recording,
  settingsOpen,
  setError,
}) {
  const [playing, setPlaying] = useState(false);
  const [playbackBusy, setPlaybackBusy] = useState(false);
  const [playbackStatus, setPlaybackStatus] = useState("未播放");
  const [playbackMeetingId, setPlaybackMeetingId] = useState(null);
  const [playbackPositionMs, setPlaybackPositionMs] = useState(null);
  const [playbackDurationMs, setPlaybackDurationMs] = useState(0);
  const [playbackPreview, setPlaybackPreview] = useState({ meetingId: null, positionMs: null });

  const playbackContextRef = useRef(null);
  const playbackSourcesRef = useRef([]);
  const playbackTimerRef = useRef(null);
  const playbackProgressTimerRef = useRef(null);
  const playbackTimelineRef = useRef([]);
  const playbackRunRef = useRef(0);
  const playbackCacheRef = useRef(new Map());
  const playbackAudioRef = useRef(null);

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
    if (playbackAudioRef.current) {
      try {
        playbackAudioRef.current.pause();
        playbackAudioRef.current.removeAttribute("src");
        playbackAudioRef.current.load();
      } catch {
        // Native media may already be detached.
      }
      playbackAudioRef.current = null;
    }
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

  const resetPlaybackState = useCallback(async (nextStatus = "未播放") => {
    await stopPlayback(nextStatus);
    setPlaybackPreview({ meetingId: null, positionMs: null });
  }, [stopPlayback]);

  const clearPlaybackPreview = useCallback(() => {
    setPlaybackPreview({ meetingId: null, positionMs: null });
  }, []);

  useEffect(() => {
    if (!recording) return;
    stopPlayback("录制中不可回放").catch(() => {});
  }, [recording, stopPlayback]);

  const decodePlaybackChunk = useCallback(async (context, chunk) => {
    const key = chunk?.id || chunk?.audio_url;
    if (!key || !chunk?.audio_url) {
      throw new Error("回放信息不完整。");
    }
    const cached = playbackCacheRef.current.get(key);
    if (cached) return cached;

    const response = await fetch(apiUrl(chunk.audio_url));
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
    if (!id || playbackBusy || recording) return;
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

      const fullAudioUrl = manifest.audio?.audio_url || (
        playableChunks.length === 1 && String(playableChunks[0]?.id || "").startsWith("meeting-audio-")
          ? playableChunks[0].audio_url
          : ""
      );
      if (fullAudioUrl && playableChunks.length === 1) {
        const runId = playbackRunRef.current + 1;
        playbackRunRef.current = runId;
        const audio = new Audio(apiUrl(fullAudioUrl));
        audio.preload = "auto";
        playbackAudioRef.current = audio;

        audio.load();
        const playPromise = audio.play().catch((exc) => exc);
        await waitForAudio(audio, "loadedmetadata", () => audio.readyState >= 1);
        if (runId !== playbackRunRef.current) return;
        const targetSec = Math.max(0, targetMs / 1000);
        try {
          audio.currentTime = Number.isFinite(audio.duration) && audio.duration > 0
            ? Math.min(targetSec, Math.max(0, audio.duration - 0.05))
            : targetSec;
        } catch {
          // Some browsers delay seekability until canplay; playback can still start at 0.
        }
        await waitForAudio(audio, "canplay", () => audio.readyState >= 3);
        if (runId !== playbackRunRef.current) return;
        const playResult = await playPromise;
        if (playResult) throw playResult;

        audio.addEventListener("ended", () => {
          if (runId === playbackRunRef.current) {
            stopPlayback("播放完成").catch(() => {});
          }
        });
        playbackProgressTimerRef.current = window.setInterval(() => {
          if (runId !== playbackRunRef.current) return;
          setPlaybackPositionMs(Math.min(manifestDurationMs, Math.max(0, audio.currentTime * 1000)));
        }, 80);
        if (runId !== playbackRunRef.current) return;
        setPlaying(true);
        setPlaybackBusy(false);
        setPlaybackStatus("回放中 · 完整会议音频");
        return;
      }

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

          const chunkDurationMs = Number(chunk.playable_duration_ms);
          const playableRemainingSec = Number.isFinite(chunkDurationMs) && chunkDurationMs > 0
            ? Math.max(0, (chunkDurationMs - requestedOffsetMs) / 1000)
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
  }, [decodePlaybackChunk, meeting?.id, meetingIdRef, playbackBusy, recording, setError, stopPlayback]);

  const playMeeting = useCallback(async () => {
    if (recording) return;
    const isCurrentPlaybackMeeting = Boolean(meeting?.id && playbackMeetingId === meeting.id);
    if ((playing || playbackBusy) && isCurrentPlaybackMeeting) {
      await stopPlayback("已暂停", { preservePosition: true, preserveMeeting: true, clearCache: false });
      return;
    }
    const resumeMs = isCurrentPlaybackMeeting && Number.isFinite(playbackPositionMs)
      ? playbackPositionMs
      : playbackPreview.meetingId === meeting?.id && Number.isFinite(playbackPreview.positionMs)
        ? playbackPreview.positionMs
        : 0;
    await startPlaybackAt(resumeMs);
  }, [meeting?.id, playbackBusy, playbackMeetingId, playbackPositionMs, playbackPreview, playing, recording, startPlaybackAt, stopPlayback]);

  useEffect(() => {
    const handleGlobalPlaybackKey = (event) => {
      if (event.defaultPrevented || event.repeat) return;
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.code !== "Space" && event.key !== " " && event.key !== "Spacebar") return;
      const canPauseCurrentPlayback = Boolean(
        meeting?.id
        && playbackMeetingId === meeting.id
        && (playing || playbackBusy)
      );
      if (
        !meeting?.id
        || recording
        || (playbackBusy && !canPauseCurrentPlayback)
        || settingsOpen
        || propertiesOpen
        || Boolean(deleteTarget)
        || Boolean(audioMergeState)
      ) {
        return;
      }
      if (globalPlaybackShortcutTarget(event.target)) return;
      event.preventDefault();
      playMeeting().catch((err) => setError(userFriendlyError(err.message)));
    };
    window.addEventListener("keydown", handleGlobalPlaybackKey);
    return () => window.removeEventListener("keydown", handleGlobalPlaybackKey);
  }, [
    audioMergeState,
    deleteTarget,
    meeting?.id,
    playMeeting,
    playbackBusy,
    playbackMeetingId,
    playing,
    propertiesOpen,
    recording,
    setError,
    settingsOpen,
  ]);

  const previewPlaybackAt = useCallback((startMs) => {
    if (recording) return;
    const value = Number(startMs);
    if (!Number.isFinite(value)) return;
    const next = Math.max(0, value);
    if (meeting?.id && playbackMeetingId === meeting.id) {
      setPlaybackPositionMs(next);
    } else if (meeting?.id) {
      setPlaybackPreview({ meetingId: meeting.id, positionMs: next });
    }
  }, [meeting?.id, playbackMeetingId, recording]);

  const playFromTranscript = useCallback(
    async (event, startMs) => {
      event.stopPropagation();
      if (recording) return;
      const value = Number(startMs);
      if (!Number.isFinite(value)) return;
      await startPlaybackAt(value);
    },
    [recording, startPlaybackAt],
  );

  const showingPlaybackMeeting = Boolean(meeting?.id && playbackMeetingId === meeting.id);
  const visiblePlaybackPositionMs = showingPlaybackMeeting
    ? playbackPositionMs
    : playbackPreview.meetingId === meeting?.id
      ? playbackPreview.positionMs
      : null;
  const visiblePlaybackPlaying = showingPlaybackMeeting ? playing : false;
  const visiblePlaybackBusy = showingPlaybackMeeting ? playbackBusy : false;

  return {
    clearPlaybackPreview,
    playbackBusy,
    playbackDurationMs,
    playbackMeetingId,
    playbackPositionMs,
    playFromTranscript,
    playing,
    playMeeting,
    previewPlaybackAt,
    resetPlaybackState,
    startPlaybackAt,
    stopPlayback,
    visiblePlaybackBusy,
    visiblePlaybackPlaying,
    visiblePlaybackPositionMs,
    playbackStatus,
  };
}
