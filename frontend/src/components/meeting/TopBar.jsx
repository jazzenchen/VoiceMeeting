import { Languages, Moon, Settings, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  WAVE_PATTERN,
  assistantStatusText,
  formatOffset,
  meetingStatusName,
  progressPercent,
  runtimeLine,
  serviceStatusText,
} from "@/lib/meeting-display";
import { useI18n } from "@/lib/i18n";

function compactStatusText(value) {
  return String(value || "").replace(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,
    (match) => match.slice(0, 8),
  );
}

function serviceCompactText(value) {
  if (value === "ready") return "已就绪";
  if (value === "starting" || value === "checking") return "启动中";
  if (value === "offline") return "未连接";
  return serviceStatusText(value);
}

export function TopBar({
  meeting,
  transcriptCount,
  chunkCount,
  status,
  llmStatus,
  servicePillClass,
  asrWorking,
  runtimeStatus,
  pendingChunks,
  activeModelDownload,
  activeModelDownloadMeta,
  recording,
  micLevel,
  appearance,
  onToggleTheme,
  onToggleLanguage,
  onOpenSettings,
}) {
  const { locale, t } = useI18n();
  const created = meeting?.created_at ? new Date(meeting.created_at) : null;
  const createdText = created && Number.isFinite(created.getTime())
    ? `${created.getFullYear()}-${String(created.getMonth() + 1).padStart(2, "0")}-${String(created.getDate()).padStart(2, "0")} ${String(created.getHours()).padStart(2, "0")}:${String(created.getMinutes()).padStart(2, "0")}`
    : "";
  const durationMs = Math.max(
    0,
    ...((meeting?.chunks || []).map((chunk) => Number(chunk.ended_at_ms) || Number(chunk.duration_ms) || 0)),
    ...((meeting?.segments || []).map((segment) => Number(segment.end_ms) || Number(segment.start_ms) || 0)),
    ...((meeting?.utterances || []).map((segment) => Number(segment.end_ms) || Number(segment.start_ms) || 0)),
  );
  const speakerCount = meeting?.speakers?.length || new Set(
    [...(meeting?.utterances || []), ...(meeting?.segments || [])]
      .map((item) => item?.speaker)
      .filter(Boolean),
  ).size;

  return (
    <header className="topbar">
      <div className="crumbs">
        <span className="meeting-title">{meeting?.title || t("今天的会议")}</span>
        {createdText && <span className="meeting-meta">{createdText}</span>}
        <span className="crumb-dot">·</span>
        <span className="meeting-meta">{durationMs ? formatOffset(durationMs) : t(meetingStatusName(meeting?.status || "ready"))}</span>
        <span className="crumb-dot">·</span>
        <span className="meeting-meta">{t("{count} 段", { count: transcriptCount })}</span>
        <span className="crumb-dot">·</span>
        <span className="meeting-meta">{t("{count} 说话人", { count: speakerCount || chunkCount || 0 })}</span>
      </div>

      <div className="topbar-actions">
        <div className="status-strip">
          <span className={`pill ${servicePillClass}`}>{t("本地服务")} · {t(serviceCompactText(status.backend))}</span>
          <span className={`pill ${status.vibe}`}>{compactStatusText(assistantStatusText(llmStatus)).replace(/^接口\s*·\s*/u, "接口 ")}</span>
          {asrWorking && <span className="pill working pulse-pill">{runtimeLine(runtimeStatus, pendingChunks, t)}</span>}
          {activeModelDownload && (
            <span className="pill working">
              {activeModelDownload.status === "cancelling"
                ? t("模型取消中")
                : `${activeModelDownloadMeta?.label || t("模型")} ${progressPercent(activeModelDownload)}%`}
            </span>
          )}
          {recording && (
            <span className="pill wave-pill" title={t("麦克风电平")}>
              <span className="mini-wave" style={{ "--level": micLevel }}>
                {WAVE_PATTERN.map((weight, index) => (
                  <span
                    key={`mini-${index}`}
                    style={{ "--bar": Math.max(0.12, weight * micLevel) }}
                  />
                ))}
              </span>
            </span>
          )}
            {pendingChunks > 0 && <span className="pill working">{t("待处理 {count}", { count: pendingChunks })}</span>}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="icon-btn language-btn"
          onClick={onToggleLanguage}
          title={locale === "zh" ? t("切换英文") : t("切换中文")}
        >
          <Languages size={14} />
          <span>{locale === "zh" ? "EN" : "中"}</span>
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="icon-btn"
          onClick={onToggleTheme}
          title={appearance?.theme === "dark" ? t("切换浅色") : t("切换深色")}
        >
          {appearance?.theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="icon-btn"
          onClick={onOpenSettings}
          title={t("打开设置")}
        >
          <Settings size={16} />
        </Button>
      </div>
    </header>
  );
}
