import { useEffect, useMemo, useRef } from "react";
import {
  Check,
  Download,
  FilePenLine,
  ListTree,
  Pencil,
  Play,
  RefreshCcw,
  Scissors,
  SlidersHorizontal,
  Sparkles,
  Users,
  X,
} from "lucide-react";

import {
  formatAsrDisplay,
  formatOffset,
  formatTime,
  runtimeLine,
  transcriptParts,
  transcriptVersionName,
  transcriptVersionOption,
} from "@/lib/meeting-display";
import { useI18n } from "@/lib/i18n";

function segmentRange(segment) {
  const parts = transcriptParts(segment);
  const starts = parts
    .map((part) => Number(part.start_ms))
    .filter(Number.isFinite);
  const ends = parts
    .map((part) => Number(part.end_ms))
    .filter(Number.isFinite);
  const start = Number.isFinite(Number(segment.start_ms))
    ? Number(segment.start_ms)
    : Math.min(...starts);
  const end = Number.isFinite(Number(segment.end_ms))
    ? Number(segment.end_ms)
    : Math.max(...ends, start + 1000);
  return {
    start: Number.isFinite(start) ? start : 0,
    end: Number.isFinite(end) && end > start ? end : start + 1000,
  };
}

export function TranscriptPane({
  meeting,
  activeTranscriptVersion,
  transcriptVersions = [],
  activeVersionId,
  activateTranscriptVersion,
  lastAsr,
  asrLanguage,
  downloadTranscript,
  transcriptDownloading,
  recording,
  reprocessWorking,
  startReprocess,
  createEditableVersion,
  error,
  asrWorking,
  runtimeStatus,
  pendingChunks,
  transcriptItems,
  onOpenMeetingProperties,
  playbackPositionMs,
  editingSegmentId,
  editableVersion,
  editBusy,
  saveSegmentEdits,
  cancelEditSegment,
  startEditSegment,
  segmentDrafts,
  updateSegmentDraft,
  playFromTranscript,
}) {
  const { t } = useI18n();
  const segmentRefs = useRef(new Map());
  const versions = useMemo(() => {
    if (Array.isArray(transcriptVersions) && transcriptVersions.length > 0) {
      return transcriptVersions;
    }
    return activeTranscriptVersion ? [activeTranscriptVersion] : [];
  }, [activeTranscriptVersion, transcriptVersions]);
  const selectedVersionId = activeVersionId || meeting?.active_version_id || activeTranscriptVersion?.id || "auto";
  const transcriptDescription = useMemo(() => {
    const bits = [
      formatAsrDisplay(lastAsr, asrLanguage),
    ];
    if (asrWorking) bits.push(runtimeLine(runtimeStatus, pendingChunks, t) || t("处理中"));
    if (bits.length === 0) {
      bits.push(transcriptVersionName(activeTranscriptVersion, meeting?.active_version_id || "auto"));
    }
    return bits.filter(Boolean).join(" · ");
  }, [
    activeTranscriptVersion,
    asrLanguage,
    asrWorking,
    lastAsr,
    meeting?.active_version_id,
    pendingChunks,
    runtimeStatus,
    t,
  ]);
  const activeSegmentId = useMemo(() => {
    if (!Number.isFinite(playbackPositionMs)) return "";
    const active = transcriptItems.find((segment) => {
      const range = segmentRange(segment);
      return playbackPositionMs >= range.start && playbackPositionMs <= range.end;
    });
    return active?.id || "";
  }, [playbackPositionMs, transcriptItems]);

  useEffect(() => {
    if (!activeSegmentId) return;
    const node = segmentRefs.current.get(activeSegmentId);
    if (!node) return;
    node.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeSegmentId]);

  return (
    <section className="transcript-pane">
      <div className="pane-header">
        <div>
          <h2>{t("实时文字")}</h2>
          <div className="transcript-meta-line">
            {versions.length > 0 && (
              <select
                className="transcript-version-select"
                value={selectedVersionId}
                onChange={(event) => activateTranscriptVersion?.(event.target.value)}
                disabled={!meeting || reprocessWorking || editBusy || versions.length < 2}
                title={t("选择稿件版本")}
              >
                {versions.map((version) => (
                  <option key={version.id} value={version.id}>
                    {transcriptVersionOption(version, t)}
                  </option>
                ))}
              </select>
            )}
            <span>{transcriptDescription}</span>
          </div>
        </div>
        <div className="pane-actions transcript-tools">
          {!recording && (
            <>
            <button
              className="playback-button"
              onClick={() => startReprocess("asr")}
              disabled={!meeting || reprocessWorking}
              title={t("重新识别")}
            >
              <RefreshCcw size={15} />
              <span>{t("重新识别")}</span>
            </button>
            <button
              className="playback-button"
              onClick={() => startReprocess("speaker")}
              disabled={!meeting || reprocessWorking || transcriptItems.length === 0}
              title={t("重新校准说话人")}
            >
              <Users size={15} />
              <span>{t("重新校准说话人")}</span>
            </button>
            <button
              className="playback-button"
              onClick={() => startReprocess("repair")}
              disabled={!meeting || reprocessWorking || transcriptItems.length === 0}
              title={t("自动校对文字")}
            >
              <Sparkles size={15} />
              <span>{t("自动校对文字")}</span>
            </button>
            <button
              className="playback-button"
              onClick={() => startReprocess("merge")}
              disabled={!meeting || reprocessWorking || transcriptItems.length === 0}
              title={t("整理段落")}
            >
              <ListTree size={15} />
              <span>{t("整理段落")}</span>
            </button>
            <button
              className="playback-button"
              onClick={createEditableVersion}
              disabled={!meeting || editableVersion || reprocessWorking || transcriptItems.length === 0}
              title={editableVersion ? t("当前已经是可编辑稿") : t("创建可编辑副本")}
            >
              <FilePenLine size={15} />
              <span>{t("编辑副本")}</span>
            </button>
            <button
              className="playback-button transcript-download"
              onClick={downloadTranscript}
              disabled={!meeting || transcriptDownloading}
              title={t("下载逐字稿")}
            >
              <Download size={15} />
              <span>{transcriptDownloading ? t("下载中") : t("下载逐字稿")}</span>
            </button>
            </>
          )}
          <button
            className="playback-button meeting-properties-button"
            onClick={onOpenMeetingProperties}
            disabled={!meeting}
            title={t("编辑会议标题和本场引导词")}
          >
            <SlidersHorizontal size={15} />
            <span>{t("会议属性")}</span>
          </button>
        </div>
      </div>

      {error && <div className="error-line">{error}</div>}
      {asrWorking && (
        <div className="activity-line notes-activity transcript-activity" role="status" aria-live="polite">
          <span>{runtimeLine(runtimeStatus, pendingChunks, t) || t("语音转文字处理中")}</span>
          <em aria-hidden="true"><b /><b /><b /></em>
        </div>
      )}

      <div className="transcript-list">
        {transcriptItems.length === 0 ? (
          <div className="empty-state">
            <Play size={20} />
            <span>{t("等待第一段文字")}</span>
          </div>
        ) : (
          transcriptItems.map((segment) => {
            const parts = transcriptParts(segment);
            const range = segmentRange(segment);
            const timeText = formatOffset(range.start) || formatTime(segment.created_at);
            const isPartActive = (part) => {
              const startMs = Number(part.start_ms);
              const endMs = Number(part.end_ms);
              return Number.isFinite(playbackPositionMs)
                && Number.isFinite(startMs)
                && Number.isFinite(endMs)
                && playbackPositionMs >= startMs
                && playbackPositionMs <= endMs;
            };
            const active = segment.id === activeSegmentId || parts.some(isPartActive);
            const editing = editingSegmentId === segment.id;
            return (
              <article
                className={`segment ${active ? "playing-now" : ""}`}
                key={segment.id}
                ref={(node) => {
                  if (node) segmentRefs.current.set(segment.id, node);
                  else segmentRefs.current.delete(segment.id);
                }}
                onClick={(event) => playFromTranscript(event, segment.start_ms)}
                title={t("从这里开始回放")}
              >
                <div className="segment-content">
                  <div className="segment-meta">
                    <div className="segment-meta-left">
                      <time>{timeText}</time>
                      <span>{segment.speaker || "Speaker 1"}</span>
                      <small>{segment.confidence ? `${Math.round(segment.confidence * 100)}%` : ""}</small>
                    </div>
                  </div>
                  {editing ? (
                    <div className="segment-editor" onClick={(event) => event.stopPropagation()}>
                      {parts.map((part, index) => (
                        <label className="segment-editor-row" key={part.id || `${segment.id}-edit-${index}`}>
                          <span>{formatOffset(part.start_ms) || t("小段 {count}", { count: index + 1 })}</span>
                          <textarea
                            value={segmentDrafts[part.id] ?? part.raw_text ?? part.text ?? ""}
                            onChange={(event) => updateSegmentDraft(part.id, event.target.value)}
                            rows={Math.max(2, Math.min(5, Math.ceil(String(segmentDrafts[part.id] ?? part.text ?? "").length / 42)))}
                            disabled={editBusy}
                          />
                        </label>
                      ))}
                    </div>
                  ) : (
                    <p className="segment-text">
                      {parts.map((part, index) => (
                        <span
                          className={`segment-part ${isPartActive(part) ? "playing-part" : ""}`}
                          key={part.id || `${segment.id}-${index}`}
                          onClick={(event) => playFromTranscript(event, part.start_ms)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              playFromTranscript(event, part.start_ms);
                            }
                          }}
                          role="button"
                          tabIndex={0}
                          title={formatOffset(part.start_ms)
                            ? t("从 {time} 播放", { time: formatOffset(part.start_ms) })
                            : t("从这里播放")}
                        >
                          {part.text}
                        </span>
                      ))}
                    </p>
                  )}
                  {segment.segment_count > 1 && (
                    <div className="segment-detail">{t("{count} 个识别小段已整理", { count: segment.segment_count })}</div>
                  )}
                </div>
                <div className="segment-actions">
                  <button
                    className="segment-action-button"
                    onClick={(event) => playFromTranscript(event, segment.start_ms)}
                    title={t("从这里播放")}
                  >
                    <Play size={13} />
                  </button>
                    {editing ? (
                      <>
                        <button
                          className="segment-action-button"
                          onClick={(event) => saveSegmentEdits(event, segment)}
                          disabled={editBusy}
                          title={t("保存这段文字")}
                        >
                          <Check size={13} />
                        </button>
                        <button
                          className="segment-action-button"
                          onClick={cancelEditSegment}
                          disabled={editBusy}
                          title={t("取消编辑")}
                        >
                          <X size={13} />
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          className={`segment-action-button ${editableVersion ? "" : "muted"}`}
                          onClick={(event) => startEditSegment(event, segment)}
                          title={editableVersion ? t("编辑这段文字") : t("请先创建可编辑副本")}
                        >
                          <Pencil size={13} />
                        </button>
                        <button className="segment-action-button muted" title={t("从此处剪辑")}>
                          <Scissors size={13} />
                        </button>
                      </>
                    )}
                </div>
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}
