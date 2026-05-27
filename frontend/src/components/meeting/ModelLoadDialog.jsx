import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { useI18n } from "@/lib/i18n";

export function ModelLoadDialog({ state, onClose }) {
  const { t } = useI18n();
  if (!state) return null;
  const loading = state.status === "loading";
  const success = state.status === "success";
  const title = loading ? t("正在准备识别模型") : success ? t("模型加载成功") : t("模型加载失败");
  const targetLabel = t(state.targetLabel);
  const previousLabel = t(state.previousLabel);

  return (
    <div className="confirm-backdrop model-load-backdrop" role="presentation">
      <section
        className="confirm-dialog model-load-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-load-title"
      >
        <div className={`model-load-icon ${success ? "success" : state.status === "error" ? "error" : ""}`}>
          {loading ? <Loader2 size={22} /> : success ? <CheckCircle2 size={22} /> : <AlertCircle size={22} />}
        </div>
        <div className="model-load-copy">
          <h2 id="model-load-title">{title}</h2>
          <p>
            {success
              ? t("{model} 已加载，可以开始识别。", { model: targetLabel })
              : t("正在加载 {model}，请稍候。", { model: targetLabel })}
          </p>
        </div>

        <div className="model-load-steps" aria-live="polite">
          {state.previousLabel && state.previousLabel !== state.targetLabel && (
            <span className={loading || success ? "done" : ""}>{t("卸载 {model}", { model: previousLabel })}</span>
          )}
          <span className={success ? "done" : loading ? "active" : "error"}>{t("加载 {model}", { model: targetLabel })}</span>
          {state.error && <strong>{state.error}</strong>}
        </div>

        {!loading && (
          <div className="confirm-actions">
            <button type="button" className={success ? "confirm-save" : "confirm-cancel"} onClick={onClose}>
              {success ? t("完成") : t("关闭")}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
