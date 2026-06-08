export function titleFromAudioFile(file) {
  const name = file?.name || "导入音视频";
  return name.replace(/\.[^/.]+$/, "").trim() || name;
}

export function notesOnlyMarkdown(markdown) {
  return String(markdown || "").replace(/\n+##\s*原始转写\s*[\s\S]*$/u, "").trim();
}

export function pipelineStepIndex(runtime, pendingChunks, pipelineStatus, finalizing, finalNotesWorking) {
  const reprocess = runtime?.reprocess;
  if (reprocess && ["queued", "running", "cancelling"].includes(reprocess.status)) {
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

export function playbackBounds(chunk) {
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

export function findPlaybackChunkIndex(chunks, startAtMs) {
  const targetMs = Number.isFinite(startAtMs) ? startAtMs : 0;
  const containing = chunks.findIndex((chunk) => {
    const bounds = playbackBounds(chunk);
    return targetMs >= bounds.startMs && targetMs < bounds.endMs;
  });
  if (containing >= 0) return containing;
  const next = chunks.findIndex((chunk) => playbackBounds(chunk).endMs > targetMs);
  return next >= 0 ? next : 0;
}
