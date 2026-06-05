import { RefreshCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

export function StartupSplash({ backendStatus, backendDetail, dataLoading, modelLoading, error, onRetry }) {
  const { t } = useI18n();
  const waitingForService = backendStatus !== "ready";
  const message = error
    ? t("启动数据加载失败")
    : waitingForService
      ? t("正在启动本地语音服务")
      : modelLoading
        ? t("正在加载识别模型")
        : dataLoading
          ? t("正在加载会议数据")
          : t("正在准备工作区");
  const detail = error
    || backendDetail
    || (waitingForService
      ? t("首次打开会解包运行环境，稍等后自动恢复。")
      : modelLoading
        ? t("模型加载完成后进入工作区。")
        : t("正在同步本地配置和历史会议。"));

  return (
    <div className="startup-splash" role="status" aria-live="polite">
      <div className="splash-mark">V</div>
      <div className="splash-copy">
        <h1>VoiceMeeting</h1>
        <p>{message}</p>
        <span>{detail}</span>
      </div>
      {error ? (
        <Button type="button" variant="outline" className="splash-retry" onClick={onRetry}>
          <RefreshCcw size={14} />
          <span>{t("重试")}</span>
        </Button>
      ) : (
        <div className="splash-spinner" />
      )}
    </div>
  );
}
