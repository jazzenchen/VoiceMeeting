import { Loader2 } from "lucide-react";
import { useI18n } from "@/lib/i18n";

export function AudioMergeDialog({ state }) {
  const { t } = useI18n();
  if (!state) return null;
  const title = state.title || t("当前会议");

  return (
    <div className="confirm-backdrop audio-merge-backdrop" role="presentation">
      <section
        className="confirm-dialog model-load-dialog audio-merge-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="audio-merge-title"
      >
        <div className="model-load-icon">
          <Loader2 size={22} />
        </div>
        <div className="model-load-copy">
          <h2 id="audio-merge-title">{t("正在合并音频文件")}</h2>
          <p>{t("正在准备 {title} 的完整会议音频，完成后会自动加载会议内容。", { title })}</p>
        </div>
        <div className="model-load-steps" aria-live="polite">
          <span className="active">{t("合并临时音频切片")}</span>
          <span>{t("加载会议内容")}</span>
        </div>
      </section>
    </div>
  );
}
