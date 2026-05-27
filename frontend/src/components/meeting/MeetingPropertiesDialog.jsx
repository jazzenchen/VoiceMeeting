import { Save, SlidersHorizontal, Trash2, X } from "lucide-react";
import { useI18n } from "@/lib/i18n";

export function MeetingPropertiesDialog({
  open,
  meeting,
  draft,
  saving,
  error,
  onChange,
  onCancel,
  onSave,
  onDelete,
}) {
  const { t } = useI18n();
  if (!open || !meeting) return null;

  return (
    <div
      className="confirm-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget && !saving) {
          onCancel();
        }
      }}
    >
      <section
        className="confirm-dialog meeting-properties-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="meeting-properties-title"
      >
        <div className="confirm-head">
          <span className="confirm-icon llm-icon">
            <SlidersHorizontal size={18} />
          </span>
          <div>
            <h2 id="meeting-properties-title">{t("会议属性")}</h2>
            <p>{t("标题和本场引导词会用于后续识别、校对和纪要生成。")}</p>
          </div>
          <button
            type="button"
            className="confirm-close"
            onClick={onCancel}
            disabled={saving}
            aria-label={t("关闭会议属性")}
            title={t("关闭")}
          >
            <X size={16} />
          </button>
        </div>

        <form className="meeting-properties-form" onSubmit={onSave}>
          <label className="properties-field">
            <span>{t("会议标题")}</span>
            <input
              value={draft.title}
              onChange={(event) => onChange("title", event.target.value)}
              placeholder={t("今天的会议")}
              disabled={saving}
              autoFocus
            />
          </label>

          <label className="properties-field">
            <span>{t("本场引导词")}</span>
            <textarea
              value={draft.description}
              onChange={(event) => onChange("description", event.target.value)}
              placeholder={t("写清会议目标、关键背景、需保留的术语/人名/产品名、语言保留偏好，以及纪要输出时应关注的重点。")}
              rows={7}
              disabled={saving}
            />
          </label>

          {error && <div className="error-line">{error}</div>}

          <div className="confirm-actions properties-actions">
            <button type="button" className="confirm-delete properties-delete" onClick={onDelete} disabled={saving}>
              <Trash2 size={14} />
              <span>{t("删除会议")}</span>
            </button>
            <button type="button" className="confirm-cancel" onClick={onCancel} disabled={saving}>
              {t("取消")}
            </button>
            <button type="submit" className="confirm-save" disabled={saving}>
              <Save size={14} />
              <span>{saving ? t("保存中") : t("保存属性")}</span>
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
