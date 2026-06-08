import {
  FileAudio,
  Mic,
  Pause,
  RefreshCcw,
  Square,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  formatOffset,
} from "@/lib/meeting-display";
import { useI18n } from "@/lib/i18n";

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function statusLabel(status) {
  const labels = {
    completed: "已完成",
    interrupted: "已中断",
    recording: "录音中",
    stopped: "已停止",
    ready: "待开始",
  };
  return labels[status] || "本地会议";
}

function meetingMeta(item, t) {
  const speakers = Number(item?.speaker_count || item?.speakers?.length || 0);
  const chunks = Number(item?.chunk_count || item?.chunks?.length || 0);
  const bits = [];
  if (chunks > 0) bits.push(t("{count} 段音频", { count: chunks }));
  if (speakers > 0) bits.push(t("{count} 位说话人", { count: speakers }));
  return bits.join(" · ") || t(statusLabel(item?.status));
}

function activeMeetingMeta(meeting, t) {
  if (!meeting?.id) return "";
  const durationMs = Math.max(
    0,
    Number(meeting.audio?.duration_ms) || 0,
    ...((meeting.chunks || []).map((chunk) => Number(chunk.ended_at_ms) || Number(chunk.duration_ms) || 0)),
    ...((meeting.utterances || []).map((item) => Number(item.end_ms) || Number(item.start_ms) || 0)),
    ...((meeting.segments || []).map((item) => Number(item.end_ms) || Number(item.start_ms) || 0)),
  );
  const speakerLabels = new Set([
    ...((meeting.speakers || []).map((speaker) => speaker?.label)),
    ...((meeting.utterances || []).map((item) => item?.speaker)),
    ...((meeting.segments || []).map((item) => item?.speaker)),
  ].map((item) => String(item || "").trim()).filter(Boolean));
  const bits = [];
  if (durationMs > 0) bits.push(formatOffset(durationMs));
  if (speakerLabels.size > 0) bits.push(t("{count} 位说话人", { count: speakerLabels.size }));
  return bits.join(" · ") || t(statusLabel(meeting.status));
}

export function Sidebar({
  meeting,
  meetings,
  recording,
  serviceReady,
  recognitionReady,
  recognitionUnavailableReason,
  busy,
  importingAudio,
  finalizing,
  processingStopBusy,
  startMeeting,
  stopRecording,
  stopProcessing,
  uploadAudioFile,
  runtimeStatus,
  pendingChunks,
  refreshMeetings,
  loadMeeting,
  loadingMeetingId,
  playbackMeetingId,
  playbackPositionMs,
  playbackDurationMs,
  playbackPlaying,
  playbackBusy,
}) {
  const { t } = useI18n();
  const reprocess = runtimeStatus?.reprocess;
  const reprocessActive = Boolean(reprocess && ["queued", "running", "cancelling"].includes(reprocess.status));
  const processing = importingAudio || finalizing || pendingChunks > 0 || Boolean(runtimeStatus?.active_chunks?.length) || reprocessActive;
  const stoppingProcessing = processingStopBusy || reprocess?.status === "cancelling";
  const interactionLocked = recording || busy || importingAudio || finalizing || processing;
  const recordingUnavailable = !serviceReady
    ? t("本地语音服务还在启动中，请稍候。")
    : !recognitionReady
      ? recognitionUnavailableReason
      : "";

  return (
    <aside className={`sidebar rail${interactionLocked ? " locked" : ""}`}>
      <div className="rail-brand">
        <div className="mark">V</div>
        <div>
          <div className="name">VoiceMeeting</div>
          <div className="sub">{t("本地服务 · MLX")}</div>
        </div>
      </div>

      <div className="rail-cta">
        {recording ? (
          <>
            <div className="rec-controls">
              <Button type="button" variant="outline" className="rec-pause" disabled>
                <Pause size={12} />
                {t("暂停")}
              </Button>
              <Button type="button" variant="outline" className="rec-stop" onClick={stopRecording}>
                <Square size={11} />
                {t("停止")}
              </Button>
            </div>
          </>
        ) : processing ? (
          <Button
            type="button"
            className="new-rec-btn rec-stop"
            onClick={stopProcessing}
            disabled={stoppingProcessing}
            title={stoppingProcessing ? t("停止中") : t("停止处理")}
          >
            <Square size={13} />
            <span>{stoppingProcessing ? t("停止中") : t("停止处理")}</span>
          </Button>
        ) : (
          <>
            <Button
              type="button"
              className="new-rec-btn"
              onClick={startMeeting}
              disabled={!serviceReady || !recognitionReady || interactionLocked}
              title={recordingUnavailable || t("新建录音")}
            >
              <Mic size={15} />
              <span>{busy ? t("启动中") : t("新建录音")}</span>
            </Button>
            <label
              className={`import-btn ${serviceReady && recognitionReady && !interactionLocked ? "" : "disabled"}`}
              title={recordingUnavailable || t("导入音视频文件")}
            >
              <FileAudio size={13} />
              <span>{importingAudio ? t("导入中") : t("导入音视频文件")}</span>
              <input
                type="file"
                accept="audio/*,video/*"
                onChange={uploadAudioFile}
                disabled={!serviceReady || !recognitionReady || interactionLocked}
              />
            </label>
          </>
        )}
      </div>

      <div className="rail-section">
        <span>{t("历史会议 · {count}", { count: meetings.length })}</span>
        <button
          className="rail-search"
          type="button"
          title={interactionLocked ? t("处理中，暂时不能操作会议列表") : t("刷新会议列表")}
          onClick={refreshMeetings}
          disabled={interactionLocked}
        >
          <RefreshCcw size={13} />
        </button>
      </div>

      <ul className="rail-list">
        {meetings.map((item) => {
          const isActive = item.id === meeting?.id;
          const isLoading = item.id === loadingMeetingId;
          const isPlaybackOwner = item.id === playbackMeetingId && (playbackPlaying || playbackBusy);
          const playProgress = playbackDurationMs > 0
            ? Math.max(0, Math.min(1, (Number(playbackPositionMs) || 0) / playbackDurationMs))
            : 0;
          const playbackMeta = playbackBusy
            ? t("加载回放")
            : t("播放中 · {position} / {duration}", {
              position: formatOffset(Number(playbackPositionMs) || 0),
              duration: formatOffset(playbackDurationMs),
            });
          return (
            <li
              key={item.id}
              className={`rail-item ${isActive ? "active" : ""} ${isPlaybackOwner ? "playing" : ""} ${isLoading ? "loading" : ""}`}
              style={{ "--play-progress": `${playProgress * 100}%` }}
            >
              <button
                className="rail-open"
                type="button"
                onClick={(event) => {
                  event.currentTarget.blur();
                  loadMeeting(item.id);
                }}
                disabled={interactionLocked}
                title={interactionLocked ? t("处理中，暂时不能切换会议") : undefined}
              >
                <span className="title">{item.title}</span>
                <span className="meta-line">
                  <span className="meta">{formatDateTime(item.created_at)}</span>
                  <span className="badges">{isLoading ? t("加载中") : isPlaybackOwner ? playbackMeta : isActive ? activeMeetingMeta(meeting, t) : meetingMeta(item, t)}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
