import { RefreshCcw } from "lucide-react";
import { useI18n } from "@/lib/i18n";

export function StartupBanner({ serviceReady, serviceStarting, backendDetail, onRefresh }) {
  const { t } = useI18n();
  if (serviceReady) return null;

  return (
    <div className={`startup-banner ${serviceStarting ? "working" : "offline"}`}>
      <div className="startup-spinner" />
      <div>
        <strong>{serviceStarting ? t("本地语音服务启动中") : t("本地语音服务未连接")}</strong>
        <span>
          {backendDetail
            || (serviceStarting
              ? t("首次打开会解包运行环境，稍等后自动恢复。")
              : t("正在自动重连，也可以手动刷新状态。"))}
        </span>
      </div>
      <button className="mini-button text-mini" onClick={onRefresh} title={t("刷新本地服务状态")}>
        <RefreshCcw size={13} />
        <span>{t("刷新")}</span>
      </button>
    </div>
  );
}
