import { AlertTriangle, Trash2, X } from "lucide-react";
import { useI18n } from "@/lib/i18n";

export function DeleteMeetingDialog({
  deleteTarget,
  deleteBusy,
  onCancel,
  onConfirm,
}) {
  const { t } = useI18n();
  if (!deleteTarget) return null;

  return (
    <div
      className="confirm-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget && !deleteBusy) {
          onCancel();
        }
      }}
    >
      <section
        className="confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-confirm-title"
      >
        <div className="confirm-head">
          <span className="confirm-icon">
            <AlertTriangle size={18} />
          </span>
          <div>
            <h2 id="delete-confirm-title">{t("删除这场会议？")}</h2>
            <p>{deleteTarget.title}</p>
          </div>
          <button
            className="confirm-close"
            onClick={onCancel}
            disabled={deleteBusy}
            aria-label={t("取消")}
            title={t("取消")}
          >
            <X size={16} />
          </button>
        </div>
        <p className="confirm-copy">{t("删除后会移除文字稿、音频片段和纪要，无法恢复。")}</p>
        <div className="confirm-actions">
          <button className="confirm-cancel" onClick={onCancel} disabled={deleteBusy}>
            {t("取消")}
          </button>
          <button className="confirm-delete" onClick={onConfirm} disabled={deleteBusy}>
            <Trash2 size={14} />
            <span>{deleteBusy ? t("删除中") : t("确认删除")}</span>
          </button>
        </div>
      </section>
    </div>
  );
}
