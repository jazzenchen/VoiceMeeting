import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  CircleEllipsis,
  Download,
  FilePenLine,
  Pencil,
  Play,
  RefreshCcw,
  Scissors,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import {
  formatOffset,
  formatTime,
  isUnrecognizedTranscriptItem,
  recognizedTranscriptItems,
  runtimeLine,
  transcriptParts,
  UNRECOGNIZED_TEXT,
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
  deleteTranscriptVersion,
  downloadTranscript,
  transcriptDownloading,
  recording,
  recognitionReady,
  recognitionUnavailableReason,
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
  const toolsRef = useRef(null);
  const versionMenuRef = useRef(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [versionMenuOpen, setVersionMenuOpen] = useState(false);
  const versions = useMemo(() => {
    if (Array.isArray(transcriptVersions) && transcriptVersions.length > 0) {
      return transcriptVersions;
    }
    return activeTranscriptVersion ? [activeTranscriptVersion] : [];
  }, [activeTranscriptVersion, transcriptVersions]);
  const selectedVersionId = activeVersionId || meeting?.active_version_id || activeTranscriptVersion?.id || "auto";
  const selectedVersion = versions.find((version) => version.id === selectedVersionId) || activeTranscriptVersion;
  const canDeleteSelectedVersion = Boolean(
    selectedVersion
	      && selectedVersion.id
	      && selectedVersion.id !== "auto"
	      && !["queued", "running", "cancelling"].includes(selectedVersion.status),
  );
  const recognizedItems = useMemo(() => recognizedTranscriptItems(transcriptItems), [transcriptItems]);
  const hasRecognizedTranscript = recognizedItems.length > 0;
  const transcriptDescription = useMemo(() => {
    if (asrWorking) return runtimeLine(runtimeStatus, pendingChunks, t) || t("处理中");
    if (versions.length === 0) return transcriptVersionName(activeTranscriptVersion, meeting?.active_version_id || "auto");
    return "";
  }, [
    activeTranscriptVersion,
    asrWorking,
    meeting?.active_version_id,
    pendingChunks,
    runtimeStatus,
    t,
    versions.length,
  ]);
  const activeSegmentId = useMemo(() => {
    if (!Number.isFinite(playbackPositionMs)) return "";
    const active = transcriptItems.find((segment) => {
      if (isUnrecognizedTranscriptItem(segment)) return false;
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

  useEffect(() => {
    if (!toolsOpen) return undefined;
    const handlePointerDown = (event) => {
      if (toolsRef.current?.contains(event.target)) return;
      setToolsOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") setToolsOpen(false);
    };
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [toolsOpen]);

  useEffect(() => {
    if (!versionMenuOpen) return undefined;
    const handlePointerDown = (event) => {
      if (versionMenuRef.current?.contains(event.target)) return;
      setVersionMenuOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") setVersionMenuOpen(false);
    };
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [versionMenuOpen]);

  useEffect(() => {
    if (recording) {
      setToolsOpen(false);
      setVersionMenuOpen(false);
    }
  }, [recording]);

  const advancedActions = [
    {
      key: "asr",
      label: t("重新识别"),
      icon: <RefreshCcw size={15} />,
      disabled: !meeting || !recognitionReady || reprocessWorking,
      title: recognitionUnavailableReason || t("重新识别"),
      onSelect: () => startReprocess("asr"),
    },
    {
      key: "repair",
      label: t("转写校准"),
      icon: <Sparkles size={15} />,
      disabled: !meeting || reprocessWorking || !hasRecognizedTranscript,
      title: t("转写校准"),
      onSelect: () => startReprocess("repair"),
    },
    {
      key: "editable",
      label: t("手动编辑"),
      icon: <FilePenLine size={15} />,
      disabled: !meeting || editableVersion || reprocessWorking || !hasRecognizedTranscript,
      title: editableVersion ? t("当前已经是可编辑稿") : t("点击自动创建副本进行编辑"),
      onSelect: createEditableVersion,
    },
  ];

  const selectAdvancedAction = (action) => {
    if (action.disabled) return;
    setToolsOpen(false);
    action.onSelect?.();
  };

  const selectVersion = (version) => {
    if (!version?.id || version.id === selectedVersionId) {
      setVersionMenuOpen(false);
      return;
    }
    setVersionMenuOpen(false);
    activateTranscriptVersion?.(version.id);
  };

  return (
    <section className="transcript-pane">
      <div className="pane-header transcript-pane-header">
        <div className="transcript-heading-row">
          <h2>{t("实时文字")}</h2>
          <div className="transcript-meta-line" aria-label={t("选择稿件版本")}>
            {versions.length > 0 && (
              <div className="transcript-version-actions">
                <div className="transcript-version-menu" ref={versionMenuRef}>
                  <button
                    type="button"
                    className="transcript-version-trigger"
                    onClick={() => setVersionMenuOpen((value) => !value)}
                    disabled={!meeting || reprocessWorking || editBusy || versions.length < 2}
                    title={t("选择稿件版本")}
                    aria-haspopup="listbox"
                    aria-expanded={versionMenuOpen}
                  >
                    <span>{transcriptVersionOption(selectedVersion || versions[0], t)}</span>
                    <ChevronDown size={13} />
                  </button>
                  {versionMenuOpen && (
                    <div className="transcript-version-dropdown" role="listbox" aria-label={t("选择稿件版本")}>
                      {versions.map((version) => {
                        const active = version.id === selectedVersionId;
                        return (
                          <button
                            type="button"
                            role="option"
                            aria-selected={active}
                            className={active ? "active" : ""}
                            key={version.id}
                            onClick={() => selectVersion(version)}
                          >
                            <span>{transcriptVersionOption(version, t)}</span>
                            {active && <Check size={13} />}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
                {canDeleteSelectedVersion && (
                  <button
                    type="button"
                    className="version-delete-button"
                    onClick={() => deleteTranscriptVersion?.(selectedVersion.id)}
                    disabled={!meeting || reprocessWorking || editBusy}
                    title={t("删除当前稿件")}
                    aria-label={t("删除当前稿件")}
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            )}
            {transcriptDescription && <span className="transcript-status-text">{transcriptDescription}</span>}
          </div>
        </div>
        <div className="pane-actions transcript-tools">
          {!recording && (
            <>
            <div className="transcript-action-menu" ref={toolsRef}>
              <button
                className="playback-button transcript-menu-trigger"
                type="button"
                onClick={() => setToolsOpen((value) => !value)}
                disabled={!meeting}
                title={t("后处理")}
                aria-label={t("后处理")}
                aria-haspopup="menu"
                aria-expanded={toolsOpen}
              >
                <CircleEllipsis size={15} />
                <span>{t("后处理")}</span>
              </button>
              {toolsOpen && (
                <div className="transcript-action-dropdown" role="menu">
                  {advancedActions.map((action) => (
                    <button
                      key={action.key}
                      type="button"
                      role="menuitem"
                      disabled={action.disabled}
                      title={action.title}
                      onClick={() => selectAdvancedAction(action)}
                    >
                      {action.icon}
                      <span>{action.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
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
            const unrecognized = isUnrecognizedTranscriptItem(segment);
            const rangeText = `${formatOffset(range.start)} - ${formatOffset(range.end)}${UNRECOGNIZED_TEXT}`;
            const isPartActive = (part) => {
              if (unrecognized) return false;
              const startMs = Number(part.start_ms);
              const endMs = Number(part.end_ms);
              return Number.isFinite(playbackPositionMs)
                && Number.isFinite(startMs)
                && Number.isFinite(endMs)
                && playbackPositionMs >= startMs
                && playbackPositionMs <= endMs;
            };
            const active = !unrecognized && (segment.id === activeSegmentId || parts.some(isPartActive));
            const editing = !unrecognized && editingSegmentId === segment.id;
            return (
              <article
                className={`segment ${active ? "playing-now" : ""} ${unrecognized ? "unrecognized" : ""}`}
                key={segment.id}
                ref={(node) => {
                  if (node) segmentRefs.current.set(segment.id, node);
                  else segmentRefs.current.delete(segment.id);
                }}
                onClick={unrecognized ? undefined : (event) => playFromTranscript(event, segment.start_ms)}
                title={unrecognized ? t("这段音频没有识别到文字") : t("从这里开始回放")}
              >
                <div className="segment-content">
                  <div className="segment-meta">
                    <div className="segment-meta-left">
                      <time>{timeText}</time>
                      {!unrecognized && <span>{segment.speaker || "Speaker 1"}</span>}
                      {!unrecognized && <small>{segment.confidence ? `${Math.round(segment.confidence * 100)}%` : ""}</small>}
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
                      {unrecognized ? (
                        <span>{rangeText}</span>
                      ) : (
                        parts.map((part, index) => (
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
                        ))
                      )}
                    </p>
                  )}
                  {!unrecognized && segment.segment_count > 1 && (
                    <div className="segment-detail">{t("{count} 个识别小段已整理", { count: segment.segment_count })}</div>
                  )}
                </div>
                {!unrecognized && (
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
                          title={editableVersion ? t("编辑这段文字") : t("请先进入手动编辑")}
                        >
                          <Pencil size={13} />
                        </button>
                        <button className="segment-action-button muted" title={t("从此处剪辑")}>
                          <Scissors size={13} />
                        </button>
                      </>
                    )}
                  </div>
                )}
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}
