import { Fragment } from "react";
import { Check, Download, FileText, Mic, Paintbrush, RefreshCcw, Settings, Trash2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  APPEARANCE_PALETTE_ORDER,
  APPEARANCE_PALETTES,
  APPEARANCE_THEME_ORDER,
  APPEARANCE_THEMES,
} from "@/lib/appearance";
import {
  INPUT_GAIN_OPTIONS,
  LANGUAGE_OPTIONS,
  SPEAKER_MODE_OPTIONS,
  asrModelName,
  bytesLabel,
  downloadStageText,
  llmProviderLabel,
  micDeviceLabel,
  progressPercent,
} from "@/lib/meeting-display";
import { LITELLM_PRESETS, litellmPresetById } from "@/lib/llm-config";
import { useI18n } from "@/lib/i18n";

export function SettingsDialog({
  open,
  onClose,
  llmConfigSaving,
  settingsTab,
  setSettingsTab,
  missingRecordingModels,
  selectedMicId,
  selectMicDevice,
  recording,
  micDevices,
  recordingAsrModelValue,
  updateRecordingConfig,
  saveRecordingConfig,
  recordingConfigSaving,
  recordingConfigDirty,
  recordingConfigError,
  modelLoading,
  selectableAsrModels,
  asrModelGroups,
  modelCatalogByKey,
  recordingConfig,
  ensureRecordingModels,
  activeModelDownload,
  saveLlmConfig,
  llmConfig,
  llmConfigDraft,
  updateLlmConfigDraft,
  llmConfigError,
  promptConfig,
  promptDrafts,
  promptConfigSaving,
  promptConfigError,
  updatePromptDraft,
  resetPromptDraft,
  savePromptConfig,
  refreshPromptConfig,
  modelCatalogAsrGroups,
  modelCatalog,
  downloadModel,
  deleteModel,
  appearance,
  updateAppearance,
}) {
  const { t } = useI18n();
  if (!open) return null;
  const saving = llmConfigSaving || promptConfigSaving || recordingConfigSaving;

  return (
    <div
      className="confirm-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget && !saving) {
          onClose();
        }
      }}
    >
      <section
        className="confirm-dialog settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
      >
        <div className="confirm-head">
          <span className="confirm-icon llm-icon">
            <Settings size={18} />
          </span>
          <div>
            <h2 id="settings-title">{t("设置")}</h2>
            <p>{t("录制、会议助手和本地模型文件")}</p>
          </div>
          <button
            type="button"
            className="confirm-close"
            onClick={onClose}
            disabled={saving}
            aria-label={t("关闭")}
            title={t("关闭")}
          >
            <X size={16} />
          </button>
        </div>

        <div className="settings-layout">
          <nav className="settings-nav" aria-label={t("设置菜单")}>
            <button
              type="button"
              className={settingsTab === "appearance" ? "active" : ""}
              onClick={() => setSettingsTab("appearance")}
            >
              <Paintbrush size={15} />
              <span>{t("外观")}</span>
            </button>
            <button
              type="button"
              className={settingsTab === "recording" ? "active" : ""}
              onClick={() => setSettingsTab("recording")}
            >
              <Mic size={15} />
              <span>{t("录制配置")}</span>
            </button>
            <button
              type="button"
              className={settingsTab === "llm" ? "active" : ""}
              onClick={() => setSettingsTab("llm")}
            >
              <Settings size={15} />
              <span>{t("纪要大模型")}</span>
            </button>
            <button
              type="button"
              className={settingsTab === "prompts" ? "active" : ""}
              onClick={() => setSettingsTab("prompts")}
            >
              <FileText size={15} />
              <span>{t("系统提示词")}</span>
            </button>
            <button
              type="button"
              className={settingsTab === "models" ? "active" : ""}
              onClick={() => setSettingsTab("models")}
            >
              <Download size={15} />
              <span>{t("模型文件")}</span>
            </button>
          </nav>

          <div className="settings-content">
            {settingsTab === "appearance" ? (
              <AppearanceSettingsPanel
                appearance={appearance}
                updateAppearance={updateAppearance}
              />
            ) : settingsTab === "recording" ? (
              <RecordingSettingsPanel
                missingRecordingModels={missingRecordingModels}
                selectedMicId={selectedMicId}
                selectMicDevice={selectMicDevice}
                recording={recording}
                micDevices={micDevices}
                recordingAsrModelValue={recordingAsrModelValue}
                updateRecordingConfig={updateRecordingConfig}
                saveRecordingConfig={saveRecordingConfig}
                recordingConfigSaving={recordingConfigSaving}
                recordingConfigDirty={recordingConfigDirty}
                recordingConfigError={recordingConfigError}
                modelLoading={modelLoading}
                selectableAsrModels={selectableAsrModels}
                asrModelGroups={asrModelGroups}
                modelCatalogByKey={modelCatalogByKey}
                recordingConfig={recordingConfig}
                ensureRecordingModels={ensureRecordingModels}
                activeModelDownload={activeModelDownload}
              />
            ) : settingsTab === "llm" ? (
              <LlmSettingsPanel
                saveLlmConfig={saveLlmConfig}
                llmConfig={llmConfig}
                llmConfigDraft={llmConfigDraft}
                updateLlmConfigDraft={updateLlmConfigDraft}
                llmConfigSaving={llmConfigSaving}
                llmConfigError={llmConfigError}
              />
            ) : settingsTab === "prompts" ? (
              <PromptSettingsPanel
                promptConfig={promptConfig}
                promptDrafts={promptDrafts}
                promptConfigSaving={promptConfigSaving}
                promptConfigError={promptConfigError}
                updatePromptDraft={updatePromptDraft}
                resetPromptDraft={resetPromptDraft}
                savePromptConfig={savePromptConfig}
                refreshPromptConfig={refreshPromptConfig}
              />
            ) : (
              <ModelFilesPanel
                modelCatalogAsrGroups={modelCatalogAsrGroups}
                modelCatalog={modelCatalog}
                downloadModel={downloadModel}
                deleteModel={deleteModel}
              />
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function AppearanceSettingsPanel({ appearance, updateAppearance }) {
  const { t } = useI18n();
  return (
    <section className="settings-panel appearance-panel">
      <div className="settings-section-head">
        <div>
          <h3>{t("外观")}</h3>
          <p>{t("主题使用 shadcn CSS variables，配色来自参考稿的四套 palette。")}</p>
        </div>
        <Badge variant="secondary">{t(APPEARANCE_PALETTES[appearance.palette]?.label)}</Badge>
      </div>

      <div className="appearance-section">
        <span className="appearance-label">{t("明暗")}</span>
        <div className="appearance-segment">
          {APPEARANCE_THEME_ORDER.map((theme) => (
            <Button
              type="button"
              variant={appearance.theme === theme ? "default" : "outline"}
              size="sm"
              key={theme}
              onClick={() => updateAppearance("theme", theme)}
            >
              {t(APPEARANCE_THEMES[theme].label)}
            </Button>
          ))}
        </div>
      </div>

      <div className="appearance-section">
        <span className="appearance-label">{t("配色")}</span>
        <div className="palette-grid">
          {APPEARANCE_PALETTE_ORDER.map((palette) => {
            const item = APPEARANCE_PALETTES[palette];
            const swatch = item[appearance.theme] || item.dark;
            const active = appearance.palette === palette;
            return (
              <Button
                type="button"
                variant="outline"
                className={`palette-option ${active ? "active" : ""}`}
                key={palette}
                onClick={() => updateAppearance("palette", palette)}
              >
                <span
                  className="palette-preview"
                  style={{
                    "--swatch-bg": APPEARANCE_THEMES[appearance.theme].preview,
                    "--swatch-accent": swatch.accent,
                    "--swatch-muted": swatch.muted,
                    "--swatch-risk": swatch.risk,
                  }}
                >
                  <i />
                  <i />
                  <i />
                </span>
                <span className="palette-copy">
                  <strong>{t(item.label)}</strong>
                  <small>{t(item.description)}</small>
                </span>
                {active && <Check size={14} />}
              </Button>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function RecordingSettingsPanel({
  missingRecordingModels,
  selectedMicId,
  selectMicDevice,
  recording,
  micDevices,
  recordingAsrModelValue,
  updateRecordingConfig,
  saveRecordingConfig,
  recordingConfigSaving,
  recordingConfigDirty,
  recordingConfigError,
  modelLoading,
  selectableAsrModels,
  asrModelGroups,
  modelCatalogByKey,
  recordingConfig,
  ensureRecordingModels,
  activeModelDownload,
}) {
  const { t } = useI18n();
  const missingModelDownloads = missingRecordingModels.filter((item) => item.type !== "unavailable");
  const unavailableRecordingModels = missingRecordingModels.filter((item) => item.type === "unavailable");
  const noAsrModels = selectableAsrModels.length === 0;
  const diarizationMeta = modelCatalogByKey.get("diarization:pyannote-community-1");
  return (
    <form className="settings-panel" onSubmit={saveRecordingConfig}>
      <div className="settings-section-head">
        <div>
          <h3>{t("全局录制设置")}</h3>
          <p>
            {noAsrModels
              ? t("没有可用的语音识别模型，请先在模型文件里下载 ASR 模型。")
              : unavailableRecordingModels.length
              ? t("当前设置有不可用的本地能力。")
              : missingModelDownloads.length
                ? t("当前设置缺少本地模型。")
                : t("当前录制配置已就绪。")}
          </p>
        </div>
        <span className={`save-state ${missingRecordingModels.length || recordingConfigDirty ? "dirty" : ""}`}>
          {recordingConfigSaving
            ? t("保存中")
            : recordingConfigDirty
              ? t("未保存")
              : unavailableRecordingModels.length
                ? t("需处理")
                : missingModelDownloads.length
                ? t("需下载")
                : t("已保存")}
        </span>
      </div>

      <div className="config-grid settings-config-grid">
        <label className="wide-config">
          <span>{t("麦克风")}</span>
          <select
            className="select-input compact-select"
            value={selectedMicId}
            onChange={(event) => selectMicDevice(event.target.value)}
            disabled={recording}
          >
            <option value="">{t("系统默认麦克风")}</option>
            {micDevices.map((device, index) => (
              <option key={device.deviceId || `${device.groupId}-${index}`} value={device.deviceId}>
                {micDeviceLabel(device, index)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{t("模型")}</span>
          <select
            className="select-input compact-select"
            value={recordingAsrModelValue}
            onChange={(event) => updateRecordingConfig("asrModel", event.target.value)}
            disabled={recording || modelLoading}
          >
            {asrModelGroups.map((group) => (
              <optgroup key={group.label} label={t(group.label)}>
                {group.models.map((model) => {
                  const meta = modelCatalogByKey.get(`asr:${model}`);
                  const installed = Boolean(meta?.installed || selectableAsrModels.includes(model));
                  return (
                    <option className={installed ? "" : "missing-option"} disabled={!installed} key={model} value={model}>
                      {t(meta?.label || asrModelName(model))}{installed ? "" : ` · ${t("未安装")}`}
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </select>
        </label>
        <label>
          <span>{t("语言")}</span>
          <select
            className="select-input compact-select"
            value={recordingConfig.language}
            onChange={(event) => updateRecordingConfig("language", event.target.value)}
            disabled={recording}
          >
            {LANGUAGE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{t(label)}</option>
            ))}
          </select>
        </label>
        <label>
          <span>{t("说话人")}</span>
          <select
            className="select-input compact-select"
            value={recordingConfig.speakerMode}
            onChange={(event) => updateRecordingConfig("speakerMode", event.target.value)}
            disabled={recording}
          >
            {SPEAKER_MODE_OPTIONS.map(([value, label]) => {
              const unavailable = value === "diarization"
                && diarizationMeta?.installed
                && diarizationMeta?.available === false;
              return (
                <option disabled={unavailable} key={value} value={value}>
                  {t(label)}{unavailable ? ` · ${t("不可用")}` : ""}
                </option>
              );
            })}
          </select>
        </label>
        <label>
          <span>{t("最长语音窗口")}</span>
          <select
            className="select-input compact-select"
            value={recordingConfig.maxSegmentMs}
            onChange={(event) => updateRecordingConfig("maxSegmentMs", Number(event.target.value))}
            disabled={recording}
          >
            {[10000, 15000, 20000, 30000].map((value) => (
              <option key={value} value={value}>{Math.round(value / 1000)} {t("秒")}</option>
            ))}
          </select>
        </label>
        <label>
          <span>{t("人声增益")}</span>
          <select
            className="select-input compact-select"
            value={recordingConfig.inputGain}
            onChange={(event) => updateRecordingConfig("inputGain", Number(event.target.value))}
            disabled={recording}
          >
            {INPUT_GAIN_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{t(label)}</option>
            ))}
          </select>
        </label>
      </div>

      {recordingConfigError && <div className="error-line llm-error">{recordingConfigError}</div>}

      {unavailableRecordingModels.length > 0 && (
        <div className="error-line llm-error">
          {unavailableRecordingModels.map((item) => item.reason || `${item.label || item.model} ${t("不可用")}`).join("；")}
        </div>
      )}

      {missingModelDownloads.length > 0 && (
        <div className="config-actions">
          <button
            type="button"
            className="mini-button text-mini"
            onClick={() => ensureRecordingModels(recordingConfig)}
            disabled={Boolean(activeModelDownload)}
            title={t("下载当前录制配置需要的模型")}
          >
            <Download size={13} />
            <span>{t("下载所需")}</span>
          </button>
        </div>
      )}

      <div className="confirm-actions">
        <button
          type="submit"
          className="confirm-save"
          disabled={recording || recordingConfigSaving || missingRecordingModels.length > 0}
          title={unavailableRecordingModels.length ? t("请先处理不可用项") : missingModelDownloads.length ? t("请先下载模型") : t("保存")}
        >
          <Check size={14} />
          <span>{recordingConfigSaving ? t("保存中") : t("保存")}</span>
        </button>
      </div>
    </form>
  );
}

function LlmSettingsPanel({
  saveLlmConfig,
  llmConfig,
  llmConfigDraft,
  updateLlmConfigDraft,
  llmConfigSaving,
  llmConfigError,
}) {
  const { t } = useI18n();
  const selectedPreset = litellmPresetById(llmConfigDraft.preset);
  const applyLiteLLMPreset = (presetId) => {
    const nextPreset = litellmPresetById(presetId);
    const nextDraft = {
      provider: "litellm",
      preset: nextPreset.id,
    };
    if (!llmConfigDraft.model && nextPreset.modelPrefix) {
      nextDraft.model = nextPreset.modelPrefix;
    }
    if (!llmConfigDraft.apiBase && nextPreset.defaultApiBase) {
      nextDraft.apiBase = nextPreset.defaultApiBase;
    }
    updateLlmConfigDraft(nextDraft);
  };
  const currentModel = llmConfig.provider === "litellm" && llmConfig.litellm.model
    ? ` · ${llmConfig.litellm.model}`
    : "";
  return (
    <form className="settings-panel" onSubmit={saveLlmConfig}>
      <div className="settings-section-head">
        <div>
          <h3>{t("纪要大模型")}</h3>
          <p>{t("当前：{value}", {
            value: `${t(llmProviderLabel(llmConfig.provider))}${currentModel}`,
          })}</p>
        </div>
      </div>

      {llmConfig.config_error && <div className="error-line llm-error">{llmConfig.config_error}</div>}

      <div className="llm-provider-tabs" role="radiogroup" aria-label={t("会议助手来源")}>
        <label className={`llm-provider-option ${llmConfigDraft.provider === "litellm" ? "active" : ""}`}>
          <input
            type="radio"
            name="llm-provider"
            value="litellm"
            checked={llmConfigDraft.provider === "litellm"}
            onChange={() => updateLlmConfigDraft("provider", "litellm")}
            disabled={llmConfigSaving}
          />
          <span>
            <strong>{t("LLM 模型接口")}</strong>
            <small>{t("通用模型 API")}</small>
          </span>
        </label>
        <label className={`llm-provider-option ${llmConfigDraft.provider === "vibearound" ? "active" : ""}`}>
          <input
            type="radio"
            name="llm-provider"
            value="vibearound"
            checked={llmConfigDraft.provider === "vibearound"}
            onChange={() => updateLlmConfigDraft("provider", "vibearound")}
            disabled={llmConfigSaving}
          />
          <span>
            <strong>VibeAround</strong>
            <small>{t("本机通道")}</small>
          </span>
        </label>
      </div>

      {llmConfigDraft.provider === "litellm" && (
        <div className="llm-fields">
          <p className="llm-provider-note">
            {t("选择服务商预设后填写模型名、API Key 和可选 API Base。")}
          </p>
          <label>
            <span>{t("服务商预设")}</span>
            <select
              className="inline-input llm-input"
              value={llmConfigDraft.preset}
              onChange={(event) => applyLiteLLMPreset(event.target.value)}
              disabled={llmConfigSaving}
            >
              {LITELLM_PRESETS.map((preset) => (
                <option key={preset.id} value={preset.id}>{preset.label}</option>
              ))}
            </select>
          </label>
          <p className="llm-provider-note">{t(selectedPreset.helper)}</p>
          <label>
            <span>Model</span>
            <input
              className="inline-input llm-input"
              value={llmConfigDraft.model}
              onChange={(event) => updateLlmConfigDraft("model", event.target.value)}
              placeholder={selectedPreset.modelPlaceholder}
              disabled={llmConfigSaving}
            />
          </label>
          <label>
            <span>API Key</span>
            <input
              className="inline-input llm-input"
              type="password"
              value={llmConfigDraft.apiKey}
              onChange={(event) => updateLlmConfigDraft("apiKey", event.target.value)}
              placeholder={llmConfig.litellm.has_api_key ? "已保存，留空保持不变" : "sk-..."}
              autoComplete="off"
              disabled={llmConfigSaving}
            />
          </label>
          <label>
            <span>API Base URL</span>
            <input
              className="inline-input llm-input"
              value={llmConfigDraft.apiBase}
              onChange={(event) => updateLlmConfigDraft("apiBase", event.target.value)}
              placeholder={t(selectedPreset.apiBasePlaceholder)}
              disabled={llmConfigSaving}
            />
          </label>
        </div>
      )}

      {llmConfigError && <div className="error-line llm-error">{llmConfigError}</div>}

      <div className="confirm-actions">
        <button type="submit" className="confirm-save" disabled={llmConfigSaving}>
          <Check size={14} />
          <span>{llmConfigSaving ? t("保存中") : t("保存")}</span>
        </button>
      </div>
    </form>
  );
}

function PromptSettingsPanel({
  promptConfig,
  promptDrafts,
  promptConfigSaving,
  promptConfigError,
  updatePromptDraft,
  resetPromptDraft,
  savePromptConfig,
  refreshPromptConfig,
}) {
  const { t } = useI18n();
  const prompts = Array.isArray(promptConfig?.prompts) ? promptConfig.prompts : [];

  return (
    <form className="settings-panel prompt-settings-panel" onSubmit={savePromptConfig}>
      <div className="settings-section-head">
        <div>
          <h3>{t("系统提示词")}</h3>
          <p>{t("系统提示词会影响 ASR 上下文、文字校对、纪要生成和问答。")}</p>
        </div>
        <button
          type="button"
          className="mini-button text-mini"
          onClick={refreshPromptConfig}
          disabled={promptConfigSaving}
          title={t("重新读取提示词配置")}
        >
          <RefreshCcw size={13} />
          <span>{t("刷新")}</span>
        </button>
      </div>

      {promptConfigError && <div className="error-line llm-error">{promptConfigError}</div>}

      {prompts.length === 0 ? (
        <div className="notes-empty">
          <p>{t("正在读取提示词配置。")}</p>
        </div>
      ) : (
        <div className="prompt-list">
          {prompts.map((item) => {
            const value = promptDrafts[item.key] ?? item.value ?? item.default ?? "";
            const longPrompt = value.length > 520 || item.key === "final_notes";
            return (
              <label className="prompt-field" key={item.key}>
                <span className="prompt-field-head">
                  <span>
                    <strong>{t(item.label)}</strong>
                    <small>{t(item.description)}</small>
                  </span>
                  <button
                    type="button"
                    className="mini-button text-mini"
                    onClick={() => resetPromptDraft(item.key)}
                    disabled={promptConfigSaving}
                  >
                    {t("默认")}
                  </button>
                </span>
                <textarea
                  className="prompt-textarea"
                  value={value}
                  rows={longPrompt ? 9 : 6}
                  onChange={(event) => updatePromptDraft(item.key, event.target.value)}
                  disabled={promptConfigSaving}
                />
              </label>
            );
          })}
        </div>
      )}

      <div className="confirm-actions">
        <button type="submit" className="confirm-save" disabled={promptConfigSaving || prompts.length === 0}>
          <Check size={14} />
          <span>{promptConfigSaving ? t("保存中") : t("保存提示词")}</span>
        </button>
      </div>
    </form>
  );
}

function ModelFilesPanel({
  modelCatalogAsrGroups,
  modelCatalog,
  downloadModel,
  deleteModel,
}) {
  const { t } = useI18n();
  return (
    <section className="settings-panel">
      <div className="settings-section-head">
        <div>
          <h3>{t("模型文件")}</h3>
          <p>{t("管理语音识别和说话人分离所需的本地模型。")}</p>
        </div>
      </div>

      <div className="model-list settings-model-list">
        {modelCatalogAsrGroups.map((group) => (
          <Fragment key={`asr-group-${group.key}`}>
            <div className="model-group-title">{t(group.label)}</div>
            {group.models.map((item) => (
              <ModelRow
                key={`asr-${item.name}`}
                item={item}
                kind="asr"
                metaLines={modelMetaLines(item, t)}
                downloadModel={downloadModel}
                deleteModel={deleteModel}
              />
            ))}
          </Fragment>
        ))}
        {(modelCatalog?.diarization?.models || []).length > 0 && (
          <>
            <div className="model-group-title">{t("说话人分离模型")}</div>
            {(modelCatalog?.diarization?.models || []).map((item) => (
              <ModelRow
                key={`diarization-${item.name}`}
                item={item}
                kind="diarization"
                metaLines={modelMetaLines(item, t)}
                downloadModel={downloadModel}
                deleteModel={deleteModel}
              />
            ))}
          </>
        )}
      </div>
    </section>
  );
}

function modelFileSizeText(item, t) {
  const localSize = bytesLabel(item.size_bytes);
  if (localSize) return `${localSize} ${t("本地")}`;
  return item.disk || t("未知");
}

function modelMetaLines(item, t) {
  const identity = [
    item.name ? `${t("ID")}：${item.name}` : "",
    item.repo_id ? `${t("来源")}：${item.repo_id}` : "",
  ].filter(Boolean);
  const sizing = [
    item.params ? `${t("参数")}：${item.params}` : "",
    `${t("文件大小")}：${modelFileSizeText(item, t)}`,
  ].filter(Boolean);
  const composition = [
    item.file_breakdown || "",
    item.components ? `${t("模型")}：${item.components}` : "",
  ].filter(Boolean);

  return [
    identity.join(" · "),
    sizing.join(" · "),
    composition.length ? `${t("组成")}：${composition.join("；")}` : "",
  ].filter(Boolean);
}

function ModelRow({ item, kind, metaLines, downloadModel, deleteModel }) {
  const { t } = useI18n();
  const active = ["queued", "running", "cancelling"].includes(item.job?.status);
  const loading = Boolean(item.loading);
  const loaded = Boolean(item.loaded);
  const unavailable = item.installed && item.available === false;

  return (
    <div className="model-row">
      <div className="model-main">
        <strong>{t(item.label)}</strong>
        {(metaLines || []).map((line) => (
          <small key={line}>{line}</small>
        ))}
        {loading && <small>{t("正在加载识别模型")}</small>}
        {loaded && <small>{t("模型正在使用")}</small>}
        {unavailable && <em>{item.unavailable_reason || t("运行环境未就绪")}</em>}
        {active && (
          <>
            <div className="model-progress">
              <span style={{ width: `${progressPercent(item.job)}%` }} />
            </div>
            <small>{downloadStageText(item.job) || t("准备下载")}</small>
          </>
        )}
        {item.job?.status === "error" && <em>{item.job.error}</em>}
      </div>
      <div className="model-actions">
        {loading ? (
          <span className="model-badge working">{t("加载中")}</span>
        ) : loaded ? (
          <span className="model-badge ready"><Check size={12} />{t("使用中")}</span>
        ) : unavailable ? (
          <span className="model-badge blocked">{t("不可用")}</span>
        ) : item.installed ? (
          <span className="model-badge ready"><Check size={12} />{t("本地")}</span>
        ) : active ? (
          <span className="model-badge working">
            {item.job?.status === "cancelling" ? t("取消中") : `${progressPercent(item.job)}%`}
          </span>
        ) : (
          <button
            className="mini-button text-mini"
            onClick={() => downloadModel(kind, item.name)}
            title={`${t("下载")} ${t(item.label)}`}
          >
            <Download size={14} />
            <span>{t("下载")}</span>
          </button>
        )}
        {item.installed && !loading && (
          <button
            className="mini-button"
            onClick={() => deleteModel(kind, item.name)}
            title={`${t("删除")} ${t(item.label)}`}
          >
            <Trash2 size={13} />
          </button>
        )}
        {active && (
          <button
            className="mini-button"
            onClick={() => deleteModel(kind, item.name)}
            title={`${t("取消下载")} ${t(item.label)}`}
          >
            <X size={13} />
          </button>
        )}
      </div>
    </div>
  );
}
