import { useCallback } from "react";
import { api } from "@/lib/api-client";
import { audioBufferToMono } from "@/lib/audio-processing";
import { userFriendlyError } from "@/lib/error-messages";
import { titleFromAudioFile } from "@/lib/meeting-state";
import { waveformFromSamples } from "@/lib/timeline-model";

async function decodeAudioFile(file) {
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) {
    throw new Error("当前浏览器不支持读取这个音视频文件。");
  }
  const context = new AudioContextCtor();
  try {
    return await context.decodeAudioData(await file.arrayBuffer());
  } finally {
    await context.close();
  }
}

async function stopImportedMeeting(id, { refreshMeeting, refreshMeetings, setMeeting }) {
  const stopped = await api(`/api/meetings/${id}/stop`, { method: "POST" });
  setMeeting(stopped);
  await refreshMeeting(id);
  await refreshMeetings();
  return stopped;
}

export function useAudioFileImport({
  asrReady,
  asrUnavailableReason,
  chunkSeqRef,
  enqueueChunk,
  ensureRecordingModels,
  importingAudio,
  meetingIdRef,
  recording,
  refreshMeeting,
  refreshMeetings,
  serviceReady,
  setError,
  setImportingAudio,
  setImportWaveformPreview,
  setMeeting,
  setPipelineStatus,
  setProcessingStopBusy,
  setSettingsOpen,
  setSettingsTab,
  setTitle,
  stopRequestedRef,
}) {
  return useCallback(
    async (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      if (importingAudio) return;
      if (recording) {
        setError("录音中不能导入音视频，请先停止当前录音。");
        return;
      }
      if (!serviceReady) {
        setError("本地语音服务还在启动中，请稍候。");
        return;
      }
      if (!asrReady) {
        setError(asrUnavailableReason || "识别模型尚未加载成功，请检查模型配置。");
        setSettingsTab("recording");
        setSettingsOpen(true);
        return;
      }

      setError("");
      setPipelineStatus("准备导入音视频");
      setImportWaveformPreview?.(null);
      stopRequestedRef.current = false;
      try {
        if (!(await ensureRecordingModels())) return;
        setImportingAudio(true);
        const importedTitle = titleFromAudioFile(file);
        const created = await api("/api/meetings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: importedTitle }),
        });
        setMeeting(created);
        setTitle(created.title || importedTitle);
        meetingIdRef.current = created.id;
        const id = created.id;

        const audioBuffer = await decodeAudioFile(file);
        const samples = audioBufferToMono(audioBuffer);
        const durationMs = Math.max(1, Math.round((audioBuffer.duration || 0) * 1000));
        const previewWaveform = waveformFromSamples(samples);
        const updatePreview = (progress, loading = true) => {
          setImportWaveformPreview?.((current) => {
            if (current?.meetingId && current.meetingId !== id) return current;
            return {
              meetingId: id,
              durationMs,
              waveform: previewWaveform,
              progress: Math.max(Number(current?.progress) || 0, Math.max(0, Math.min(1, progress))),
              loading,
            };
          });
        };
        if (previewWaveform) updatePreview(0.035, true);
        if (stopRequestedRef.current) {
          setPipelineStatus("已停止");
          await stopImportedMeeting(id, { refreshMeeting, refreshMeetings, setMeeting });
          updatePreview(1, false);
          return;
        }

        chunkSeqRef.current = 0;
        setPipelineStatus("正在识别完整音视频");
        if (stopRequestedRef.current) {
          setPipelineStatus("已停止");
          await stopImportedMeeting(id, { refreshMeeting, refreshMeetings, setMeeting });
          updatePreview(1, false);
          return;
        }

        chunkSeqRef.current = 1;
        await enqueueChunk(file, durationMs, {
          clientChunkId: `${id}-import-full`,
          filename: file.name,
          startedAtMs: 0,
          endedAtMs: durationMs,
          cutReason: "导入完整音视频",
        });
        updatePreview(1, !stopRequestedRef.current);

        if (stopRequestedRef.current) {
          setPipelineStatus("已停止");
        } else {
          setPipelineStatus("已完成");
        }
        await stopImportedMeeting(id, { refreshMeeting, refreshMeetings, setMeeting });
        updatePreview(1, false);
      } catch (err) {
        setImportWaveformPreview?.(null);
        if (err?.name === "AbortError") {
          setPipelineStatus("已停止");
          return;
        }
        setPipelineStatus("导入失败");
        setError(userFriendlyError(err.message));
      } finally {
        setImportingAudio(false);
        setProcessingStopBusy(false);
      }
    },
    [
      asrReady,
      asrUnavailableReason,
      enqueueChunk,
      ensureRecordingModels,
      importingAudio,
      recording,
      refreshMeeting,
      refreshMeetings,
      serviceReady,
      setError,
      setImportingAudio,
      setImportWaveformPreview,
      setMeeting,
      setPipelineStatus,
      setProcessingStopBusy,
      setSettingsOpen,
      setSettingsTab,
      setTitle,
    ],
  );
}
