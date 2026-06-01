import { useI18n } from "@/lib/i18n";

export function StartupBanner({ serviceReady, serviceStarting, backendDetail }) {
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
              : t("状态通道正在自动重连。"))}
        </span>
      </div>
    </div>
  );
}
