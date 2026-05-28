import { Download, FileText } from "lucide-react";

import { MarkdownPreview } from "@/components/meeting/MarkdownPreview";
import { transcriptParts, transcriptVersionName } from "@/lib/meeting-display";
import { useI18n } from "@/lib/i18n";

function flattenTranscript(transcriptItems) {
  return transcriptItems.flatMap((item) => (
    transcriptParts(item).map((part) => ({
      ...part,
      speaker: part.speaker || item.speaker,
      text: part.text || "",
    }))
  )).filter((part) => String(part.text || "").trim());
}

export function NotesPane({
  finalNotesWorking,
  finalize,
  meeting,
  recording,
  assistantReady,
  assistantUnavailableReason,
  finalizing,
  downloadNotes,
  notesDownloading,
  finalNotesReady,
  activeTranscriptVersion,
  finalMarkdownForDisplay,
  finalNotesStreaming,
  transcriptItems,
}) {
  const { t } = useI18n();
  const transcriptPartsList = flattenTranscript(transcriptItems);
  const hasContent = transcriptPartsList.length > 0;
  const finalMarkdownText = String(finalMarkdownForDisplay || "").trim();

  return (
    <aside className={`notes-pane summary ${finalNotesWorking ? "summary-working" : ""}`}>
      <div className="pane-header panel-head">
        <div className="panel-title">
          <h2>{t("会议纪要")}</h2>
          <span className="sub">
            {activeTranscriptVersion
              ? t("基于{version}", { version: transcriptVersionName(activeTranscriptVersion, meeting?.active_version_id) })
              : t("基于原始稿")}
            {" · "}
            {finalNotesReady ? t("AI 已生成") : t("暂未生成")}
          </span>
        </div>
        <div className="header-actions panel-actions">
          <button
            className={`finish-button btn-ghost primary ${finalNotesWorking ? "working" : ""}`}
            onClick={finalize}
            disabled={!meeting || !assistantReady || recording || finalNotesWorking}
            title={assistantUnavailableReason || t("生成完整纪要")}
          >
            {finalNotesWorking ? <span className="notes-spinner" aria-hidden="true" /> : <FileText size={15} />}
            <span>{finalNotesWorking ? t("生成中") : t("生成纪要")}</span>
          </button>
          <button
            type="button"
            className="download-button btn-ghost"
            onClick={downloadNotes}
            disabled={!meeting || notesDownloading}
            title={t("下载纪要")}
          >
            <Download size={15} />
          </button>
        </div>
      </div>

      <div className="summary-body">
        {finalNotesWorking && (
          <div className="activity-line notes-activity" role="status" aria-live="polite">
            <span>{t("完整纪要正在生成")}</span>
            <em aria-hidden="true"><b /><b /><b /></em>
          </div>
        )}

        {finalMarkdownText ? (
          <section className="notes-markdown">
            <MarkdownPreview markdown={finalMarkdownText} streaming={finalNotesStreaming || finalNotesWorking} />
          </section>
        ) : (
          <section className="notes-empty">
            {finalNotesWorking ? (
              <p>{t("正在生成会议纪要...")}</p>
            ) : finalNotesReady ? (
              <p>{t("会议纪要已生成。")}</p>
            ) : assistantUnavailableReason ? (
              <p>{assistantUnavailableReason}</p>
            ) : hasContent ? (
              <p>{t("已捕捉 {count} 条转写内容。点击“生成纪要”后会基于完整文字稿生成 Markdown 纪要。", { count: transcriptPartsList.length })}</p>
            ) : (
              <p>{t("会议纪要将在录音或导入音频后生成。")}</p>
            )}
          </section>
        )}
      </div>
    </aside>
  );
}
