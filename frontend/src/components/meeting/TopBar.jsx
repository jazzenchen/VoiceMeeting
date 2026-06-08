import { Languages, Moon, Settings, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  WAVE_PATTERN,
  asrModelName,
  assistantStatusText,
  formatOffset,
  meetingStatusName,
  serviceStatusText,
  speakerModeName,
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

function compactPillValue(value) {
  return String(value || "")
    .replace(/识别$/u, "")
    .replace(/^声纹跟踪$/u, "声纹")
    .replace(/^高精度分离$/u, "高精度")
    .trim();
}

function asrStatusText(modelStatus, recognitionUnavailableReason, t) {
  if (recognitionUnavailableReason) return "";
  if (!modelStatus) return "";
  const model = modelStatus.label
    || asrModelName(modelStatus.model || modelStatus.runtime_model || modelStatus.configured_model || "");
  if (modelStatus.loading) {
    return "";
  }
  if (modelStatus.loaded) {
    return model ? `${t("文字识别")} · ${t(model)}` : "";
  }
  if (modelStatus.last_error || modelStatus.load_error) {
    return `${t("文字识别")} · ${t("模型加载失败")}`;
  }
  return model ? `${t("文字识别")} · ${t(model)}` : "";
}

function assistantCompactText(llmStatus, t) {
  const text = compactStatusText(assistantStatusText(llmStatus));
  const litellm = text.match(/^LLM 模型接口\s*·\s*(.*)$/u);
  if (litellm) {
    const suffix = litellm[1] === "需要配置" ? t("需配置") : litellm[1];
    return `${t("LLM 模型接口")} · ${suffix}`.trim();
  }
  return t(text);
}

function assistantPillValue(llmStatus, t) {
  const route = String(llmStatus?.route || llmStatus?.transport || llmStatus?.provider || "");
  if (llmStatus?.provider === "litellm" || route.includes("litellm")) {
    return llmStatus?.model ? t("LLM") : t("待配置");
  }
  if (route.includes("web-chat")) return "Codex";
  if (!llmStatus?.transport && !llmStatus?.route && !llmStatus?.provider) return t("待配置");
  return "VibeAround";
}

function assistantPillClass(llmStatus, fallbackClass) {
  const route = String(llmStatus?.route || llmStatus?.transport || llmStatus?.provider || "");
  if ((llmStatus?.provider === "litellm" || route.includes("litellm")) && !llmStatus?.model) {
    return "warning";
  }
  return fallbackClass || "unknown";
}

function asrPillValue(modelStatus, recognitionUnavailableReason, t) {
  if (recognitionUnavailableReason) return t("不可用");
  if (!modelStatus) return "";
  if (modelStatus.loading) return t("加载中");
  if (modelStatus.last_error || modelStatus.load_error) return t("失败");
  const model = modelStatus.label
    || asrModelName(modelStatus.model || modelStatus.runtime_model || modelStatus.configured_model || "");
  return model ? t(compactPillValue(model)) : "";
}

function StatusPill({ className = "", label, value, tooltip }) {
  const text = [label, value].filter(Boolean).join(" · ");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={`pill status-pill ${className}`} aria-label={tooltip || text}>
          <span className="pill-label">{label}</span>
          {value && (
            <>
              <span className="pill-separator">·</span>
              <span className="pill-value">{value}</span>
            </>
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent>{tooltip || text}</TooltipContent>
    </Tooltip>
  );
}

function releaseMouseFocus(event, action) {
  action?.();
  if (event.detail > 0) {
    event.currentTarget.blur();
  }
}

export function TopBar({
  meeting,
  transcriptCount,
  status,
  llmStatus,
  modelStatus,
  recognitionUnavailableReason,
  servicePillClass,
  recording,
  speakerMode,
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
    Number(meeting?.audio?.duration_ms) || 0,
    ...((meeting?.chunks || []).map((chunk) => Number(chunk.ended_at_ms) || Number(chunk.duration_ms) || 0)),
    ...((meeting?.segments || []).map((segment) => Number(segment.end_ms) || Number(segment.start_ms) || 0)),
    ...((meeting?.utterances || []).map((segment) => Number(segment.end_ms) || Number(segment.start_ms) || 0)),
  );
  const speakerCount = meeting?.speakers?.length || new Set(
    [...(meeting?.utterances || []), ...(meeting?.segments || [])]
      .map((item) => item?.speaker)
      .filter(Boolean),
  ).size;
  const serviceReady = status?.backend === "ready";
  const asrPillText = serviceReady ? asrStatusText(modelStatus, recognitionUnavailableReason, t) : "";
  const servicePillValue = t(serviceCompactText(status?.backend));
  const servicePillTitle = [
    `${t("本地服务")} · ${servicePillValue}`,
    status?.backendDetail ? compactStatusText(status.backendDetail) : "",
  ].filter(Boolean).join("\n");
  const asrPillClass = recognitionUnavailableReason
    ? "warning"
    : modelStatus?.loading
    ? "working pulse-pill"
    : modelStatus?.loaded
      ? "ready"
      : (modelStatus?.last_error || modelStatus?.load_error)
        ? "offline"
        : "unknown";
  const speakerModeText = `${t("说话人")} · ${t(speakerModeName(speakerMode))}`;
  const assistantPillTitle = assistantCompactText(llmStatus, t);
  const assistantValue = assistantPillValue(llmStatus, t);

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
        <span className="meeting-meta">{t("{count} 说话人", { count: speakerCount })}</span>
      </div>

      <div className="topbar-actions">
        <TooltipProvider>
          <div className="status-strip">
            <StatusPill
              className={servicePillClass}
              label={t("服务")}
              value={servicePillValue}
              tooltip={servicePillTitle}
            />
            {asrPillText && (
              <StatusPill
                className={asrPillClass}
                label={t("识别")}
                value={asrPillValue(modelStatus, recognitionUnavailableReason, t)}
                tooltip={asrPillText}
              />
            )}
            <StatusPill
              className="ready"
              label={t("说话人")}
              value={t(compactPillValue(speakerModeName(speakerMode)))}
              tooltip={speakerModeText}
            />
            <StatusPill
              className={assistantPillClass(llmStatus, status.vibe)}
              label={t("纪要")}
              value={assistantValue}
              tooltip={assistantPillTitle}
            />
            {recording && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="pill wave-pill" aria-label={t("麦克风电平")}>
                    <span className="mini-wave" style={{ "--level": micLevel }}>
                      {WAVE_PATTERN.map((weight, index) => (
                        <span
                          key={`mini-${index}`}
                          style={{ "--bar": Math.max(0.12, weight * micLevel) }}
                        />
                      ))}
                    </span>
                  </span>
                </TooltipTrigger>
                <TooltipContent>{t("麦克风电平")}</TooltipContent>
              </Tooltip>
            )}
          </div>
        </TooltipProvider>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="icon-btn language-btn"
          onClick={(event) => releaseMouseFocus(event, onToggleLanguage)}
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
          onClick={(event) => releaseMouseFocus(event, onToggleTheme)}
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
