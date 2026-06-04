import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AudioMergeDialog } from "@/components/meeting/AudioMergeDialog";
import { DeleteMeetingDialog } from "@/components/meeting/DeleteMeetingDialog";
import { MeetingTimeline } from "@/components/meeting/MeetingTimeline";
import { MeetingPropertiesDialog } from "@/components/meeting/MeetingPropertiesDialog";
import { ModelLoadDialog } from "@/components/meeting/ModelLoadDialog";
import { NotesPane } from "@/components/meeting/NotesPane";
import { SettingsDialog } from "@/components/meeting/SettingsDialog";
import { Sidebar } from "@/components/meeting/Sidebar";
import { StartupBanner } from "@/components/meeting/StartupBanner";
import { TopBar } from "@/components/meeting/TopBar";
import { TranscriptPane } from "@/components/meeting/TranscriptPane";
import { clampAppearance, loadAppearance, saveAppearance } from "@/lib/appearance";
import {
  asrBackendForModel,
  asrBackendLabel,
  asrModelName,
  isUnrecognizedTranscriptItem,
  llmProviderLabel,
  transcriptParts,
  transcriptVersionOption,
} from "@/lib/meeting-display";
import { I18nProvider, loadLocale, saveLocale } from "@/lib/i18n";
import { api, apiUrl, fetchTextFile, readSse, wsUrl } from "@/lib/api-client";
import { userFriendlyError } from "@/lib/error-messages";
import {
  DEFAULT_LLM_CONFIG,
  llmDraftFromConfig,
  normalizeLlmConfig,
  promptDraftsFromConfig,
} from "@/lib/llm-config";
import {
  findPlaybackChunkIndex,
  notesOnlyMarkdown,
  playbackBounds,
  titleFromAudioFile,
} from "@/lib/meeting-state";
import { loadSelectedMicId, saveSelectedMicId } from "@/lib/mic-preferences";
import { requestNativeMicrophonePermission, safeDownloadName, saveTextFile } from "@/lib/platform-files";
import {
  LIVE_WAVEFORM_BAR_COUNT,
  FIRST_SPEECH_FLUSH_MS,
  MIN_SPEECH_WINDOW_MS,
  PRE_SPEECH_BUFFER_MS,
  PRE_SPEECH_PROBE_MS,
  SPEECH_END_SILENCE_MS,
  applyInputGain,
  audioBufferToMono,
  compressWaveformBars,
  concatFloat32,
  displayMicLevel,
  encodeWav,
  makeVadChunks,
  shapeLiveWaveLevel,
  speechDetectedByEnergy,
} from "@/lib/audio-processing";
import {
  ASR_MODEL_ORDER,
  DEFAULT_RECORDING_CONFIG,
  MAX_SEGMENT_MS,
  clampRecordingConfig,
  loadRecordingConfig,
  recordingConfigFromServer,
  recordingConfigToServer,
  recordingConfigsEqual,
} from "@/lib/recording-config";

function previewMeetingFromSummary(item) {
  if (!item?.id) return null;
  return {
    id: item.id,
    title: item.title || "今天的会议",
    description: item.description || "",
    status: item.status || "stopped",
    created_at: item.created_at || "",
    updated_at: item.updated_at || item.created_at || "",
    active_version_id: "auto",
    transcript_versions: [],
    summary: {},
    summary_source_hash: "",
    summary_source_version_id: "",
    summary_segment_count: 0,
    final_source_hash: "",
    final_source_version_id: "",
    final_markdown: "",
    segments: [],
    utterances: [],
    chunks: [],
    speakers: [],
  };
}

function globalPlaybackShortcutTarget(target) {
  if (!target || typeof target.closest !== "function") return false;
  if (target.closest(".rail-open")) return false;
  if (target.isContentEditable) return true;
  return Boolean(target.closest([
    "input",
    "textarea",
    "select",
    "button",
    "a",
    "[contenteditable='true']",
    "[role='textbox']",
    "[role='button']",
    "[role='menuitem']",
    "[role='option']",
  ].join(",")));
}

function App() {
  const [meeting, setMeeting] = useState(null);
  const [meetings, setMeetings] = useState([]);
  const [title, setTitle] = useState("今天的会议");
  const [titleSaving, setTitleSaving] = useState(false);
  const [titleSavedAt, setTitleSavedAt] = useState(0);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [finalNotesError, setFinalNotesError] = useState("");
  const [pendingChunks, setPendingChunks] = useState(0);
  const [error, setError] = useState("");
  const [audioMergeState, setAudioMergeState] = useState(null);
  const [modelStatus, setModelStatus] = useState(null);
  const [modelCatalog, setModelCatalog] = useState(null);
  const [recordingAsrOptions, setRecordingAsrOptions] = useState([]);
  const [recordingDiarizationOptions, setRecordingDiarizationOptions] = useState([]);
  const [recordingConfig, setRecordingConfig] = useState(loadRecordingConfig);
  const [recordingConfigDraft, setRecordingConfigDraft] = useState(loadRecordingConfig);
  const [recordingConfigSaving, setRecordingConfigSaving] = useState(false);
  const [recordingConfigError, setRecordingConfigError] = useState("");
  const [pipelineStatus, setPipelineStatus] = useState("待机");
  const [llmStatus, setLlmStatus] = useState({ provider: "VibeAround", transport: "local-api" });
  const [llmConfig, setLlmConfig] = useState(DEFAULT_LLM_CONFIG);
  const [llmConfigDraft, setLlmConfigDraft] = useState(() => llmDraftFromConfig(DEFAULT_LLM_CONFIG));
  const [llmConfigSaving, setLlmConfigSaving] = useState(false);
  const [llmConfigError, setLlmConfigError] = useState("");
  const [promptConfig, setPromptConfig] = useState(null);
  const [promptDrafts, setPromptDrafts] = useState({});
  const [promptConfigSaving, setPromptConfigSaving] = useState(false);
  const [promptConfigError, setPromptConfigError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState("recording");
  const [propertiesOpen, setPropertiesOpen] = useState(false);
  const [propertiesDraft, setPropertiesDraft] = useState({ title: "", description: "" });
  const [propertiesSaving, setPropertiesSaving] = useState(false);
  const [propertiesError, setPropertiesError] = useState("");
  const [micDevices, setMicDevices] = useState([]);
  const [selectedMicId, setSelectedMicId] = useState(loadSelectedMicId);
  const [selectedMicDraftId, setSelectedMicDraftId] = useState(loadSelectedMicId);
  const [vadLevel, setVadLevel] = useState(0);
  const [liveWaveformBars, setLiveWaveformBars] = useState([]);
  const [liveRecordingMs, setLiveRecordingMs] = useState(0);
  const [importingAudio, setImportingAudio] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState("");
  const [modelLoadState, setModelLoadState] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [playbackBusy, setPlaybackBusy] = useState(false);
  const [playbackStatus, setPlaybackStatus] = useState("未播放");
  const [playbackMeetingId, setPlaybackMeetingId] = useState(null);
  const [playbackPositionMs, setPlaybackPositionMs] = useState(null);
  const [playbackDurationMs, setPlaybackDurationMs] = useState(0);
  const [playbackPreview, setPlaybackPreview] = useState({ meetingId: null, positionMs: null });
  const [runtimeStatus, setRuntimeStatus] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [reprocessBusy, setReprocessBusy] = useState(false);
  const [editBusy, setEditBusy] = useState(false);
  const [editingSegmentId, setEditingSegmentId] = useState("");
  const [segmentDrafts, setSegmentDrafts] = useState({});
  const [speakerRenameFrom, setSpeakerRenameFrom] = useState("");
  const [speakerRenameTo, setSpeakerRenameTo] = useState("");
  const [meetingChats, setMeetingChats] = useState({});
  const [askInput, setAskInput] = useState("");
  const [askBusy, setAskBusy] = useState(false);
  const [streamingFinalMarkdown, setStreamingFinalMarkdown] = useState("");
  const [status, setStatus] = useState({ backend: "starting", vibe: "checking" });
  const [appearance, setAppearance] = useState(loadAppearance);
  const [locale, setLocale] = useState(loadLocale);

  const streamRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const sourceRef = useRef(null);
  const processorRef = useRef(null);
  const activeSegmentRef = useRef(null);
  const totalSamplesRef = useRef(0);
  const audioSampleRateRef = useRef(48000);
  const chunkSeqRef = useRef(0);
  const stopRequestedRef = useRef(false);
  const uploadChainRef = useRef(Promise.resolve());
  const activeUploadControllersRef = useRef(new Set());
  const firstSpeechFlushDoneRef = useRef(false);
  const preSpeechFramesRef = useRef([]);
  const preSpeechProbeRef = useRef(null);
  const meetingIdRef = useRef(null);
  const playbackContextRef = useRef(null);
  const playbackSourcesRef = useRef([]);
  const playbackTimerRef = useRef(null);
  const playbackProgressTimerRef = useRef(null);
  const playbackTimelineRef = useRef([]);
  const playbackRunRef = useRef(0);
  const playbackCacheRef = useRef(new Map());
  const meetingLoadRunRef = useRef(0);
  const recordingConfigRef = useRef(recordingConfig);
  const liveWaveformRef = useRef([]);
  const liveWaveformEmitRef = useRef(0);
  const liveWaveformCeilingRef = useRef(0.055);
  const liveWaveformFloorRef = useRef(0.002);
  const previousSettingsOpenRef = useRef(false);
  const serviceReadyOnceRef = useRef(false);
  const autoLoadedMeetingRef = useRef(false);
  const completedReprocessRef = useRef("");

  const finalMarkdown = notesOnlyMarkdown(meeting?.final_markdown);
  const streamingFinalText = String(streamingFinalMarkdown || "").trim();
  const finalMarkdownForDisplay = streamingFinalText || finalMarkdown;
  const finalNotesStreaming = Boolean(finalizing && streamingFinalText);
  const finalNotesReady = Boolean(
    finalNotesStreaming
      || (
        finalMarkdown
        && meeting?.final_source_hash
        && meeting?.final_source_version_id === (meeting?.active_version_id || "auto")
      ),
  );
  const finalNotesPending = Boolean(meeting?.id && !finalNotesReady && !finalizing);
  const segments = meeting?.segments || [];
  const utterances = meeting?.utterances || [];
  const transcriptItems = utterances.length > 0 ? utterances : segments;
  const chunks = meeting?.chunks || [];
  const displayRuntimeStatus = useMemo(() => {
    if (meeting?.status !== "stopped" || !(runtimeStatus?.active_chunks || []).length) {
      return runtimeStatus;
    }
    return {
      ...runtimeStatus,
      active_chunks: [],
      has_active_chunks: false,
    };
  }, [meeting?.status, runtimeStatus]);
  const showingPlaybackMeeting = Boolean(meeting?.id && playbackMeetingId === meeting.id);
  const visiblePlaybackPositionMs = showingPlaybackMeeting
    ? playbackPositionMs
    : playbackPreview.meetingId === meeting?.id
      ? playbackPreview.positionMs
      : null;
  const visiblePlaybackPlaying = showingPlaybackMeeting ? playing : false;
  const visiblePlaybackBusy = showingPlaybackMeeting ? playbackBusy : false;
  const visiblePlaybackStatus = showingPlaybackMeeting ? playbackStatus : "未播放";
  const askMessages = meeting?.id ? meetingChats[meeting.id] || [] : [];
  const activeTranscriptVersion = (meeting?.transcript_versions || []).find(
    (version) => version.id === (meeting?.active_version_id || "auto"),
  ) || null;
  const editableVersion = activeTranscriptVersion?.kind === "manual-edit";
  const speakerOptions = useMemo(() => {
    const labels = new Set();
    for (const item of transcriptItems) {
      if (isUnrecognizedTranscriptItem(item)) continue;
      const label = String(item?.speaker || "").trim();
      if (label) labels.add(label);
    }
    for (const item of meeting?.speakers || []) {
      const label = String(item?.label || "").trim();
      if (label) labels.add(label);
    }
    return [...labels].sort((left, right) => left.localeCompare(right, "zh-CN"));
  }, [meeting?.speakers, transcriptItems]);
  const modelCatalogAsr = Array.isArray(modelCatalog?.asr?.models) ? modelCatalog.asr.models : [];
  const recordingAsrMetaByName = useMemo(() => {
    const map = new Map();
    for (const item of recordingAsrOptions || []) {
      const name = item.name || item.id;
      if (!name) continue;
      map.set(name, {
        kind: "asr",
        id: name,
        name,
        label: item.label || asrModelName(name),
        backend: item.backend || (name.startsWith("mlx-") ? "mlx" : name.startsWith("funasr-") ? "funasr" : "faster-whisper"),
        backend_label: item.backend_label || asrBackendLabel(item.backend),
        installed: Boolean(item.installed),
      });
    }
    return map;
  }, [recordingAsrOptions]);
  const selectableAsrModels = useMemo(() => {
    const installedNames = [...recordingAsrMetaByName.values()]
      .filter((item) => item.installed)
      .map((item) => item.name || item.id);
    return ASR_MODEL_ORDER.filter((name) => installedNames.includes(name));
  }, [recordingAsrMetaByName]);
  const recordingDiarizationMetaByName = useMemo(() => {
    const map = new Map();
    for (const item of recordingDiarizationOptions || []) {
      const name = item.name || item.id;
      if (!name) continue;
      map.set(name, {
        ...item,
        kind: "diarization",
        id: item.id || name,
        name,
        label: item.label || name,
        installed: Boolean(item.installed),
        available: item.available === undefined ? Boolean(item.installed) : Boolean(item.available),
        loaded: Boolean(item.loaded),
        loading: Boolean(item.loading),
      });
    }
    return map;
  }, [recordingDiarizationOptions]);
  const modelCatalogByKey = useMemo(() => {
    const map = new Map();
    for (const item of recordingAsrMetaByName.values()) {
      map.set(`asr:${item.name || item.id}`, item);
    }
    for (const item of recordingDiarizationMetaByName.values()) {
      map.set(`diarization:${item.name || item.id}`, item);
    }
    for (const item of modelCatalogAsr) {
      map.set(`asr:${item.name || item.id}`, item);
    }
    for (const item of modelCatalog?.diarization?.models || []) {
      map.set(`diarization:${item.name || item.id}`, item);
    }
    return map;
  }, [modelCatalog?.diarization?.models, modelCatalogAsr, recordingAsrMetaByName, recordingDiarizationMetaByName]);
  const modelMetaForRequirement = useCallback(
    (item, catalog = modelCatalog) => {
      if (!item?.kind || !item?.model) return null;
      if (item.kind === "asr") {
        return recordingAsrMetaByName.get(item.model)
          || (catalog?.asr?.models || []).find((meta) => (meta.name || meta.id) === item.model)
          || null;
      }
      if (item.kind === "diarization") {
        return recordingDiarizationMetaByName.get(item.model)
          || (catalog?.diarization?.models || []).find((meta) => (meta.name || meta.id) === item.model)
          || null;
      }
      return null;
    },
    [modelCatalog, recordingAsrMetaByName, recordingDiarizationMetaByName],
  );
  const asrModelGroups = useMemo(() => {
    const groups = [];
    for (const backend of ["faster-whisper", "mlx", "funasr"]) {
      const models = ASR_MODEL_ORDER.filter((name) => {
        const meta = modelCatalogByKey.get(`asr:${name}`);
        return (meta?.backend || asrBackendForModel(name)) === backend;
      });
      if (models.length > 0) {
        groups.push({
          label: asrBackendLabel(backend),
          models,
        });
      }
    }
    const grouped = new Set(groups.flatMap((group) => group.models));
    const remaining = ASR_MODEL_ORDER.filter((name) => !grouped.has(name));
    if (remaining.length > 0) {
      groups.push({ label: "其他模型", models: remaining });
    }
    return groups;
  }, [modelCatalogByKey]);
  const modelCatalogAsrGroups = useMemo(() => {
    const groups = [];
    for (const backend of ["faster-whisper", "mlx", "funasr"]) {
      const models = modelCatalogAsr.filter((item) => item.backend === backend);
      if (models.length > 0) {
        groups.push({
          key: backend,
          label: asrBackendLabel(backend),
          models,
        });
      }
    }
    const grouped = new Set(groups.flatMap((group) => group.models.map((item) => item.name || item.id)));
    const remaining = modelCatalogAsr.filter((item) => !grouped.has(item.name || item.id));
    if (remaining.length > 0) {
      groups.push({ key: "other", label: "其他模型", models: remaining });
    }
    return groups;
  }, [modelCatalogAsr]);
  const activeModelDownload = (modelCatalog?.downloads || []).find((job) => (
    job?.status === "queued" || job?.status === "running" || job?.status === "cancelling"
  ));
  const latestModelDownload = (modelCatalog?.downloads || [])[0] || null;
  const activeModelDownloadMeta = activeModelDownload
    ? modelCatalogByKey.get(`${activeModelDownload.kind}:${activeModelDownload.model}`)
    : null;
  const loadingAsrModelMeta = modelCatalogAsr.find((item) => item.loading) || null;
  const activeChunks = displayRuntimeStatus?.active_chunks || [];
  const reprocessRuntime = runtimeStatus?.reprocess || null;
  const notesReprocessWorking = (
    reprocessRuntime?.level === "notes" && ["queued", "running"].includes(reprocessRuntime?.status)
  );
  const finalNotesWorking = finalizing || notesReprocessWorking;
  const reprocessWorking = reprocessBusy || ["queued", "running"].includes(reprocessRuntime?.status);
  const asrWorking = pendingChunks > 0 || activeChunks.length > 0 || reprocessWorking;
  const normalizedRecordingConfig = clampRecordingConfig(recordingConfig);
  const selectedAsrModelMeta = modelCatalogByKey.get(`asr:${normalizedRecordingConfig.asrModel}`);
  const serviceReady = status.backend === "ready";
  const serviceStarting = status.backend === "starting" || status.backend === "checking";
  const modelLoadBusy = modelLoadState?.status === "loading";
  const modelRuntimeBusy = modelLoadBusy || Boolean(loadingAsrModelMeta) || Boolean(modelStatus?.loading);
  const llmReady = status.vibe === "ready";
  const llmUnavailableReason = llmReady ? "" : status.vibeDetail || "会议助手不可用，请在设置里配置纪要大模型。";
  const selectedAsrModelLoaded = Boolean(selectedAsrModelMeta?.loaded)
    || Boolean(
      modelStatus?.loaded
        && [modelStatus.model, modelStatus.runtime_model].filter(Boolean).includes(normalizedRecordingConfig.asrModel),
    );
  const asrReady = serviceReady && selectedAsrModelLoaded;
  const asrUnavailableReason = !serviceReady
    ? "本地语音服务还在启动中，请稍候。"
    : modelRuntimeBusy
      ? "识别模型正在加载，请稍候。"
      : selectableAsrModels.length === 0
        ? "本地还没有可用的识别模型，请先在设置里下载模型。"
        : asrReady
          ? ""
          : selectedAsrModelMeta?.installed
            ? "当前录制模型尚未加载成功，请检查模型配置。"
            : "识别模型尚未加载，请先在设置里加载模型。";
  const servicePillClass = serviceStarting ? "working pulse-pill" : status.backend;
  const trimmedTitle = title.trim();
  const titleDirty = Boolean(meeting?.id && trimmedTitle && trimmedTitle !== meeting.title);

  useEffect(() => {
    const normalized = clampRecordingConfig(recordingConfig);
    recordingConfigRef.current = normalized;
  }, [recordingConfig]);

  useEffect(() => {
    saveAppearance(clampAppearance(appearance));
  }, [appearance]);

  useEffect(() => {
    saveLocale(locale);
  }, [locale]);

  useEffect(() => {
    if (activeModelDownload || pipelineStatus !== "模型下载中") return;
    if (latestModelDownload?.status === "done") {
      setPipelineStatus("模型已安装");
    } else if (latestModelDownload?.status === "error") {
      setPipelineStatus("模型下载失败");
    }
  }, [activeModelDownload, latestModelDownload?.status, pipelineStatus]);

  const refreshMicDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter((device) => device.kind === "audioinput");
      setMicDevices(inputs);
      if (selectedMicId && !inputs.some((device) => device.deviceId === selectedMicId)) {
        setSelectedMicId("");
        saveSelectedMicId("");
      }
    } catch {
      setMicDevices([]);
    }
  }, [selectedMicId]);

  const selectMicDevice = useCallback((deviceId) => {
    const cleanDeviceId = String(deviceId || "");
    setSelectedMicId(cleanDeviceId);
    saveSelectedMicId(cleanDeviceId);
  }, []);

  useEffect(() => {
    refreshMicDevices();
    navigator.mediaDevices?.addEventListener?.("devicechange", refreshMicDevices);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", refreshMicDevices);
  }, [refreshMicDevices]);

  useEffect(() => {
    if (meeting?.id) {
      setTitle(meeting.title || "");
      setTitleSavedAt(0);
    }
    setFinalNotesError("");
  }, [meeting?.id]);

  useEffect(() => {
    setEditingSegmentId("");
    setSegmentDrafts({});
    setSpeakerRenameTo("");
  }, [meeting?.active_version_id, meeting?.id]);

  useEffect(() => {
    if (!speakerOptions.length) {
      setSpeakerRenameFrom("");
      return;
    }
    if (!speakerRenameFrom || !speakerOptions.includes(speakerRenameFrom)) {
      setSpeakerRenameFrom(speakerOptions[0]);
    }
  }, [speakerOptions, speakerRenameFrom]);

  useEffect(() => {
    const wasOpen = previousSettingsOpenRef.current;
    previousSettingsOpenRef.current = settingsOpen;
    if (!settingsOpen || wasOpen) return;
    setRecordingConfigDraft(clampRecordingConfig(recordingConfigRef.current));
    setSelectedMicDraftId(selectedMicId);
    setRecordingConfigError("");
    setLlmConfigDraft(llmDraftFromConfig(llmConfig));
    setLlmConfigError("");
    setPromptConfigError("");
    if (promptConfig) setPromptDrafts(promptDraftsFromConfig(promptConfig));
  }, [llmConfig, promptConfig, selectedMicId, settingsOpen]);

  const applyLlmStatus = useCallback((data) => {
    if (!data) {
      setLlmStatus({ provider: "会议助手", transport: "offline" });
      return;
    }
    if (data.config) {
      setLlmConfig(normalizeLlmConfig(data.config));
    }
    const provider = data.provider === "litellm" ? "LLM 模型接口" : "VibeAround";
    setLlmStatus({
      provider: data.provider_label || data.profile_id || provider,
      transport: data.route || data.transport || data.target_api_type || data.provider || "local-api",
      model: data.model || data.config?.litellm?.model,
    });
  }, []);

  const applyPromptConfig = useCallback((data) => {
    if (!data) return;
    setPromptConfig(data);
    setPromptDrafts(promptDraftsFromConfig(data));
  }, []);

  const applyRecordingConfigStatus = useCallback((data) => {
    if (!data) return;
    if (data.config) {
      const nextConfig = recordingConfigFromServer(data.config);
      recordingConfigRef.current = nextConfig;
      setRecordingConfig(nextConfig);
      if (!settingsOpen) {
        setRecordingConfigDraft(nextConfig);
      }
    }
    if (Array.isArray(data.asr_options)) setRecordingAsrOptions(data.asr_options);
    if (Array.isArray(data.diarization_options)) setRecordingDiarizationOptions(data.diarization_options);
    setModelStatus(data.asr || null);
    if (data.llm) {
      applyLlmStatus(data.llm);
      setStatus((current) => ({
        ...current,
        vibe: data.llm?.ok ? "ready" : "fallback",
        profile: data.llm?.profile_id,
        vibeDetail: data.llm?.error || data.llm?.status_code || "",
      }));
    }
    if (data.load_error) setError(userFriendlyError(data.load_error));
  }, [applyLlmStatus, settingsOpen]);

  const refreshPromptConfig = useCallback(async () => {
    try {
      const data = await api("/api/prompts/config");
      applyPromptConfig(data);
      setPromptConfigError("");
    } catch (err) {
      setPromptConfigError(userFriendlyError(err.message));
    }
  }, [applyPromptConfig]);

  useEffect(() => {
    if (!settingsOpen) return;
    if (!promptConfig) {
      refreshPromptConfig();
    }
  }, [promptConfig, refreshPromptConfig, settingsOpen]);

  const refreshMeetings = useCallback(async () => {
    try {
      const data = await api("/api/meetings");
      setMeetings(data.meetings || []);
    } catch {
      setMeetings([]);
    }
  }, []);

  const refreshMeeting = useCallback(async (id) => {
    if (!id) return;
    try {
      const data = await api(`/api/meetings/${id}`);
      setMeeting(data);
    } catch (err) {
      setError(userFriendlyError(err.message));
    }
  }, []);

  const applyRuntimeStatus = useCallback((data) => {
    if (!data || data.error) {
      if (data?.error === "not_found") setRuntimeStatus(null);
      return;
    }
    setRuntimeStatus(data);
    if (data.llm) {
      setLlmStatus({
        provider: data.llm?.provider_label || llmProviderLabel(data.llm?.provider),
        transport: data.llm?.route || data.llm?.transport || data.llm?.target_api_type || "local-api",
        model: data.llm?.model,
      });
    }
    if (data.asr) setModelStatus(data.asr);
  }, []);

  useEffect(() => {
    let socket = null;
    let reconnectTimer = 0;
    let closed = false;

    const connect = () => {
      socket = new WebSocket(wsUrl("/api/status/ws"));
      socket.onopen = () => {
        setStatus((current) => ({
          ...current,
          backend: serviceReadyOnceRef.current ? "ready" : "checking",
          backendDetail: "",
        }));
      };
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          serviceReadyOnceRef.current = true;
          applyRecordingConfigStatus(data);
          setStatus((current) => ({ ...current, backend: "ready", backendDetail: "" }));
        } catch (err) {
          setStatus((current) => ({
            ...current,
            backend: serviceReadyOnceRef.current ? "offline" : "starting",
            backendDetail: userFriendlyError(err.message),
          }));
        }
      };
      socket.onclose = () => {
        if (closed) return;
        setStatus((current) => ({
          ...current,
          backend: serviceReadyOnceRef.current ? "offline" : "starting",
          backendDetail: serviceReadyOnceRef.current ? "状态通道断开，正在自动重连。" : "等待本地语音服务启动。",
        }));
        reconnectTimer = window.setTimeout(connect, 1000);
      };
      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();
    return () => {
      closed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [applyRecordingConfigStatus]);

  useEffect(() => {
    let cancelled = false;
    api("/api/recording-config")
      .then((data) => {
        if (cancelled) return;
        serviceReadyOnceRef.current = true;
        applyRecordingConfigStatus(data);
        setStatus((current) => ({ ...current, backend: "ready", backendDetail: "" }));
      })
      .catch((err) => {
        if (cancelled) return;
        setStatus((current) => ({
          ...current,
          backend: serviceReadyOnceRef.current ? "offline" : "starting",
          backendDetail: userFriendlyError(err.message) || "等待本地语音服务启动。",
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [applyRecordingConfigStatus]);

  useEffect(() => {
    const streamModelCatalog = (
      settingsOpen && ["models", "recording"].includes(settingsTab)
    ) || Boolean(activeModelDownload);
    if (!streamModelCatalog) return undefined;
    let socket = null;
    let reconnectTimer = 0;
    let closed = false;

    const connect = () => {
      socket = new WebSocket(wsUrl("/api/models/ws"));
      socket.onmessage = (event) => {
        try {
          setModelCatalog(JSON.parse(event.data));
        } catch {
          // Keep the previous catalog; the socket will continue streaming changes.
        }
      };
      socket.onclose = () => {
        if (closed) return;
        reconnectTimer = window.setTimeout(connect, 1500);
      };
      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();
    return () => {
      closed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [activeModelDownload?.id, settingsOpen, settingsTab]);

  useEffect(() => {
    const id = meeting?.id;
    if (!id) {
      setRuntimeStatus(null);
      return undefined;
    }
    let socket = null;
    let reconnectTimer = 0;
    let closed = false;

    const connect = () => {
      socket = new WebSocket(wsUrl(`/api/meetings/${encodeURIComponent(id)}/runtime/ws`));
      socket.onmessage = (event) => {
        try {
          applyRuntimeStatus(JSON.parse(event.data));
        } catch {
          // Runtime updates are continuous; keep the last known state.
        }
      };
      socket.onclose = () => {
        if (closed) return;
        reconnectTimer = window.setTimeout(connect, 1000);
      };
      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();
    return () => {
      closed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [applyRuntimeStatus, meeting?.id]);

  const downloadModel = useCallback(async (kind, model) => {
    if (!kind || !model) return null;
    setError("");
    try {
      const data = await api("/api/models/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, model }),
      });
      if (data.catalog) setModelCatalog(data.catalog);
      setPipelineStatus("模型下载中");
      return data.job || null;
    } catch (err) {
      setError(userFriendlyError(err.message));
      return null;
    }
  }, []);

  const deleteModel = useCallback(async (kind, model) => {
    if (!kind || !model) return;
    const meta = modelCatalogByKey.get(`${kind}:${model}`);
    const active = ["queued", "running", "cancelling"].includes(meta?.job?.status);
    const loading = Boolean(meta?.loading);
    const loaded = Boolean(meta?.loaded);
    if (loading) {
      setError("模型正在加载，请稍后再删除。");
      return;
    }
    const prompt = active
      ? `取消下载并删除已下载部分：${meta?.label || model}？`
      : loaded
        ? `模型正在使用：${meta?.label || model}。删除会先卸载它，之后录音或导入可能需要重新选择/重新加载模型。确定删除？`
      : `删除本地模型：${meta?.label || model}？`;
    if (!window.confirm(prompt)) return;
    setError("");
    try {
      const force = loaded ? "?force=1" : "";
      const catalog = await api(`/api/models/${encodeURIComponent(kind)}/${encodeURIComponent(model)}${force}`, {
        method: "DELETE",
      });
      setModelCatalog(catalog || null);
    } catch (err) {
      setError(userFriendlyError(err.message));
    }
  }, [modelCatalogByKey]);

  const updateRecordingConfigDraft = useCallback((field, value) => {
    setRecordingConfigError("");
    setRecordingConfigDraft((current) => clampRecordingConfig({ ...current, [field]: value }));
  }, []);

  const saveRecordingConfig = useCallback(async (event) => {
    event.preventDefault();
    if (recordingConfigSaving || recording) return;
    const current = clampRecordingConfig(recordingConfigRef.current);
    const next = clampRecordingConfig(recordingConfigDraft);
    const modelChanged = next.asrModel !== current.asrModel;
    setRecordingConfigSaving(true);
    setRecordingConfigError("");
    setError("");
    if (modelChanged) {
      setModelLoadState({
        status: "loading",
        source: "settings",
        targetModel: next.asrModel,
        targetLabel: asrModelName(next.asrModel),
        previousLabel: asrModelName(current.asrModel),
      });
    }
    try {
      if (selectedMicDraftId !== selectedMicId) {
        selectMicDevice(selectedMicDraftId);
      }
      const data = await api("/api/recording-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recordingConfigToServer(next)),
      });
      applyRecordingConfigStatus(data);
      const saved = data.config ? recordingConfigFromServer(data.config) : next;
      setRecordingConfigDraft(saved);
      setPipelineStatus("录制配置已保存");
      if (modelChanged) {
        setModelLoadState({
          status: "success",
          source: "settings",
          targetModel: next.asrModel,
          targetLabel: asrModelName(next.asrModel),
          previousLabel: asrModelName(current.asrModel),
        });
      }
    } catch (err) {
      const message = userFriendlyError(err.message);
      setRecordingConfigError(message);
      setError(message);
      if (modelChanged) {
        setModelLoadState({
          status: "error",
          source: "settings",
          targetModel: next.asrModel,
          targetLabel: asrModelName(next.asrModel),
          previousLabel: asrModelName(current.asrModel),
          error: message,
        });
      }
    } finally {
      setRecordingConfigSaving(false);
    }
  }, [
    applyRecordingConfigStatus,
    recording,
    recordingConfigDraft,
    recordingConfigSaving,
    selectMicDevice,
    selectedMicDraftId,
    selectedMicId,
  ]);

  const updateAppearance = useCallback((field, value) => {
    setAppearance((current) => clampAppearance({ ...current, [field]: value }));
  }, []);

  const updateLlmConfigDraft = useCallback((field, value) => {
    setLlmConfigError("");
    setLlmConfigDraft((current) => (
      typeof field === "object" && field !== null
        ? { ...current, ...field }
        : { ...current, [field]: value }
    ));
  }, []);

  const saveLlmConfig = useCallback(
    async (event) => {
      event.preventDefault();
      if (llmConfigSaving) return;
      setLlmConfigSaving(true);
      setLlmConfigError("");
      setError("");
      try {
        const saved = await api("/api/llm/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: llmConfigDraft.provider,
            litellm: {
              preset: llmConfigDraft.preset,
              api_base: llmConfigDraft.apiBase,
              api_key: llmConfigDraft.apiKey,
              model: llmConfigDraft.model,
            },
          }),
        });
        const normalized = normalizeLlmConfig(saved);
        setLlmConfig(normalized);
        setLlmConfigDraft(llmDraftFromConfig(normalized));
        setLlmStatus({
          provider: llmProviderLabel(normalized.provider),
          transport: normalized.provider === "litellm" ? "litellm" : "local-api",
          model: normalized.litellm.model,
        });
        setStatus((current) => ({
          ...current,
          vibe: "ready",
          vibeDetail: "",
        }));
        setPipelineStatus("会议助手配置已保存");
      } catch (err) {
        setLlmConfigError(userFriendlyError(err.message));
      } finally {
        setLlmConfigSaving(false);
      }
    },
    [llmConfigDraft, llmConfigSaving],
  );

  const updatePromptDraft = useCallback((key, value) => {
    setPromptConfigError("");
    setPromptDrafts((current) => ({
      ...current,
      [key]: value,
    }));
  }, []);

  const resetPromptDraft = useCallback((key) => {
    const item = (promptConfig?.prompts || []).find((entry) => entry.key === key);
    if (!item) return;
    setPromptConfigError("");
    setPromptDrafts((current) => ({
      ...current,
      [key]: item.default || "",
    }));
  }, [promptConfig?.prompts]);

  const savePromptConfig = useCallback(
    async (event) => {
      event.preventDefault();
      if (promptConfigSaving) return;
      setPromptConfigSaving(true);
      setPromptConfigError("");
      setError("");
      try {
        const saved = await api("/api/prompts/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompts: promptDrafts }),
        });
        applyPromptConfig(saved);
        setPipelineStatus("系统提示词已保存");
      } catch (err) {
        setPromptConfigError(userFriendlyError(err.message));
      } finally {
        setPromptConfigSaving(false);
      }
    },
    [applyPromptConfig, promptConfigSaving, promptDrafts],
  );

  const requiredModelsForConfig = useCallback((config) => {
    const normalized = clampRecordingConfig(config);
    const required = [{ kind: "asr", model: normalized.asrModel }];
    if (normalized.speakerMode === "diarization") {
      required.push({ kind: "diarization", model: "pyannote-community-1" });
    }
    return required;
  }, []);

  const missingModelsForConfig = useCallback((config, catalog = modelCatalog) => {
    if (!catalog && recordingAsrMetaByName.size === 0 && recordingDiarizationMetaByName.size === 0) return [];
    return requiredModelsForConfig(config).map((item) => {
      const meta = modelMetaForRequirement(item, catalog);
      const label = meta?.label || item.model;
      if (!meta) {
        const canEvaluateKind = item.kind === "asr"
          ? Boolean(catalog) || recordingAsrMetaByName.size > 0
          : item.kind === "diarization"
            ? Boolean(catalog) || recordingDiarizationMetaByName.size > 0
            : Boolean(catalog);
        if (!canEvaluateKind) return null;
        return { ...item, type: "missing", label };
      }
      if (!meta.installed) {
        return { ...item, type: "missing", label };
      }
      if (item.kind === "diarization" && meta.available === false) {
        return {
          ...item,
          type: "unavailable",
          label,
          reason: meta.unavailable_reason || "高精度分离运行环境未就绪。",
        };
      }
      return null;
    }).filter(Boolean);
  }, [
    modelCatalog,
    modelMetaForRequirement,
    recordingAsrMetaByName.size,
    recordingDiarizationMetaByName.size,
    requiredModelsForConfig,
  ]);

  const ensureRecordingModels = useCallback(async (config = recordingConfigRef.current) => {
    const requiredModels = requiredModelsForConfig(config);
    let catalog = modelCatalog;
    const needsCatalog = !catalog && requiredModels.some((item) => (
      item.kind !== "asr" && !modelMetaForRequirement(item, catalog)
    ));
    if (needsCatalog) {
      try {
        catalog = await api("/api/models");
        setModelCatalog(catalog);
      } catch (err) {
        setError(userFriendlyError(err.message));
        setSettingsTab("models");
        setSettingsOpen(true);
        return false;
      }
    }
    const issues = missingModelsForConfig(config, catalog);
    if (issues.length === 0) return true;
    const unavailable = issues.filter((item) => item.type === "unavailable");
    if (unavailable.length > 0) {
      const message = unavailable.map((item) => item.reason || `${item.label || item.model} 不可用`).join("；");
      setError(message);
      setSettingsTab("models");
      setSettingsOpen(true);
      return false;
    }
    const missing = issues.filter((item) => item.type === "missing");
    const names = missing.map((item) => {
      const meta = modelMetaForRequirement(item, catalog);
      return item.label || meta?.label || item.model;
    }).join("、");
    setError(`当前设置缺少本地模型：${names}。请在设置里下载所需模型，或切换到已有模型。`);
    setSettingsTab("recording");
    setSettingsOpen(true);
    return false;
  }, [missingModelsForConfig, modelCatalog, modelMetaForRequirement, requiredModelsForConfig]);

  const openMeetingProperties = useCallback(() => {
    if (!meeting?.id) return;
    setPropertiesDraft({
      title: meeting.title || "今天的会议",
      description: meeting.description || "",
    });
    setPropertiesError("");
    setPropertiesOpen(true);
  }, [meeting?.description, meeting?.id, meeting?.title]);

  const updatePropertiesDraft = useCallback((field, value) => {
    setPropertiesError("");
    setPropertiesDraft((current) => ({
      ...current,
      [field]: value,
    }));
  }, []);

  const closeMeetingProperties = useCallback(() => {
    if (propertiesSaving) return;
    setPropertiesOpen(false);
  }, [propertiesSaving]);

  const saveMeetingProperties = useCallback(
    async (event) => {
      event.preventDefault();
      const id = meeting?.id;
      if (!id || propertiesSaving) return;
      const cleanTitle = propertiesDraft.title.trim() || "今天的会议";
      const cleanDescription = propertiesDraft.description.trim();
      setPropertiesSaving(true);
      setPropertiesError("");
      setError("");
      try {
        const updated = await api(`/api/meetings/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: cleanTitle,
            description: cleanDescription,
          }),
        });
        setMeeting(updated);
        setTitle(updated.title || cleanTitle);
        setTitleSavedAt(Date.now());
        setPropertiesOpen(false);
        setPipelineStatus("会议属性已保存");
        await refreshMeetings();
      } catch (err) {
        setPropertiesError(userFriendlyError(err.message));
      } finally {
        setPropertiesSaving(false);
      }
    },
    [meeting?.id, propertiesDraft.description, propertiesDraft.title, propertiesSaving, refreshMeetings],
  );

  const saveMeetingTitle = useCallback(async () => {
    const id = meeting?.id;
    const cleanTitle = title.trim();
    if (!id || !cleanTitle || titleSaving) return;
    setTitleSaving(true);
    setError("");
    try {
      const updated = await api(`/api/meetings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: cleanTitle }),
      });
      setMeeting(updated);
      setTitle(updated.title || "");
      setTitleSavedAt(Date.now());
      await refreshMeetings();
    } catch (err) {
      setError(userFriendlyError(err.message));
    } finally {
      setTitleSaving(false);
    }
  }, [meeting?.id, refreshMeetings, title, titleSaving]);

  const activateTranscriptVersion = useCallback(
    async (versionId) => {
      const id = meeting?.id;
      if (!id || !versionId || versionId === meeting.active_version_id) return;
      setError("");
      try {
        const updated = await api(`/api/meetings/${id}/versions/${encodeURIComponent(versionId)}/activate`, {
          method: "POST",
        });
        setMeeting(updated);
      } catch (err) {
        setError(userFriendlyError(err.message));
      }
    },
    [meeting?.active_version_id, meeting?.id],
  );

  const deleteTranscriptVersion = useCallback(
    async (versionId) => {
      const id = meeting?.id;
      if (!id || !versionId || versionId === "auto") return;
      const version = (meeting?.transcript_versions || []).find((item) => item.id === versionId);
      if (["queued", "running"].includes(version?.status)) return;
      const label = transcriptVersionOption(version || { id: versionId });
      if (!window.confirm(`删除稿件：${label}？`)) return;
      setError("");
      try {
        const updated = await api(`/api/meetings/${id}/versions/${encodeURIComponent(versionId)}`, {
          method: "DELETE",
        });
        setMeeting(updated);
        setPipelineStatus("稿件已删除");
        await refreshMeetings();
      } catch (err) {
        setError(userFriendlyError(err.message));
      }
    },
    [meeting?.id, meeting?.transcript_versions, refreshMeetings],
  );

  const startReprocess = useCallback(
    async (level) => {
      const id = meeting?.id;
      if (!id || reprocessBusy) return;
      if (level === "asr" && !asrReady) {
        setError(asrUnavailableReason || "识别模型尚未加载成功，请检查模型配置。");
        setSettingsTab("recording");
        setSettingsOpen(true);
        return;
      }
      if ((level === "repair" || level === "notes") && !llmReady) {
        setError(llmUnavailableReason);
        setSettingsTab("llm");
        setSettingsOpen(true);
        return;
      }
      const labels = {
        asr: "重新整理文字",
        speaker: "重新分离说话人",
        merge: "按人声整理段落",
        repair: "自动校对文字",
        notes: "纪要重写",
      };
      setReprocessBusy(true);
      setError("");
      setPipelineStatus(`准备${labels[level] || "处理"}`);
      try {
        const activeConfig = clampRecordingConfig(recordingConfigRef.current);
        const data = await api(`/api/meetings/${id}/reprocess`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            level,
            language: activeConfig.language,
            asr_model: activeConfig.asrModel,
            speaker_mode: activeConfig.speakerMode,
            make_current: true,
            force_local: false,
            source_version_id: meeting.active_version_id || "auto",
            reset_speakers: level === "speaker" && activeConfig.speakerMode !== "off",
          }),
        });
        if (data.job) {
          setRuntimeStatus((current) => ({ ...(current || {}), reprocess: data.job }));
        }
        setPipelineStatus(`${labels[level] || "处理"}中`);
        if (data.job?.status === "done") {
          await refreshMeeting(id);
          await refreshMeetings();
        }
      } catch (err) {
        setPipelineStatus("处理失败");
        setError(userFriendlyError(err.message));
      } finally {
        setReprocessBusy(false);
      }
    },
    [
      meeting?.active_version_id,
      meeting?.id,
      asrReady,
      asrUnavailableReason,
      llmReady,
      llmUnavailableReason,
      refreshMeeting,
      refreshMeetings,
      reprocessBusy,
    ],
  );

  const createEditableVersion = useCallback(async () => {
    const id = meeting?.id;
    if (!id || editBusy) return;
    setEditBusy(true);
    setError("");
    try {
      const updated = await api(`/api/meetings/${id}/versions/editable`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_version_id: meeting.active_version_id || "auto" }),
      });
      setMeeting(updated);
      setPipelineStatus("已创建可编辑副本");
      await refreshMeetings();
    } catch (err) {
      setError(userFriendlyError(err.message));
    } finally {
      setEditBusy(false);
    }
  }, [editBusy, meeting?.active_version_id, meeting?.id, refreshMeetings]);

  const startEditSegment = useCallback(
    (event, segment) => {
      event.stopPropagation();
      if (isUnrecognizedTranscriptItem(segment)) return;
      if (!editableVersion) {
        setError("请先创建可编辑副本，再修改文字。");
        return;
      }
      const drafts = {};
      for (const part of transcriptParts(segment)) {
        if (part.id) {
          drafts[part.id] = part.raw_text ?? part.text ?? "";
        }
      }
      setEditingSegmentId(segment.id);
      setSegmentDrafts(drafts);
    },
    [editableVersion],
  );

  const updateSegmentDraft = useCallback((segmentId, value) => {
    setSegmentDrafts((current) => ({
      ...current,
      [segmentId]: value,
    }));
  }, []);

  const cancelEditSegment = useCallback((event) => {
    event.stopPropagation();
    setEditingSegmentId("");
    setSegmentDrafts({});
  }, []);

  const saveSegmentEdits = useCallback(
    async (event, segment) => {
      event.stopPropagation();
      const id = meeting?.id;
      if (!id || !editableVersion || editBusy) return;
      const updates = transcriptParts(segment)
        .filter((part) => part.id)
        .map((part) => ({
          id: part.id,
          before: part.raw_text ?? part.text ?? "",
          after: segmentDrafts[part.id] ?? "",
        }))
        .filter((item) => item.after.trim() !== String(item.before || "").trim());

      if (updates.length === 0) {
        setEditingSegmentId("");
        setSegmentDrafts({});
        return;
      }

      setEditBusy(true);
      setError("");
      try {
        let updated = null;
        for (const item of updates) {
          updated = await api(`/api/meetings/${id}/segments/${encodeURIComponent(item.id)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: item.after }),
          });
        }
        if (updated) {
          setMeeting(updated);
          await refreshMeetings();
        }
        setEditingSegmentId("");
        setSegmentDrafts({});
      } catch (err) {
        setError(userFriendlyError(err.message));
      } finally {
        setEditBusy(false);
      }
    },
    [editBusy, editableVersion, meeting?.id, refreshMeetings, segmentDrafts],
  );

  const renameSpeaker = useCallback(async () => {
    const id = meeting?.id;
    const from = speakerRenameFrom.trim();
    const to = speakerRenameTo.trim();
    if (!id || !from || !to || editBusy) return;
    if (!editableVersion) {
      setError("请先创建可编辑副本，再修改说话人名称。");
      return;
    }
    setEditBusy(true);
    setError("");
    try {
      const updated = await api(`/api/meetings/${id}/speakers/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_label: from, new_label: to }),
      });
      setMeeting(updated);
      setSpeakerRenameFrom(to);
      setSpeakerRenameTo("");
      await refreshMeetings();
    } catch (err) {
      setError(userFriendlyError(err.message));
    } finally {
      setEditBusy(false);
    }
  }, [editBusy, editableVersion, meeting?.id, refreshMeetings, speakerRenameFrom, speakerRenameTo]);

  const setMeetingChat = useCallback((meetingId, updater) => {
    setMeetingChats((current) => {
      const previous = current[meetingId] || [];
      return {
        ...current,
        [meetingId]: typeof updater === "function" ? updater(previous) : updater,
      };
    });
  }, []);

  const askMeeting = useCallback(
    async (promptOverride = "") => {
      const id = meeting?.id;
      const prompt = (promptOverride || askInput).trim();
      if (!id || !prompt || askBusy) return;

      const userMessage = {
        id: `${Date.now()}-user`,
        role: "user",
        content: prompt,
      };
      const history = askMessages
        .slice(-8)
        .map((message) => ({ role: message.role, content: message.content }));

      setMeetingChat(id, (previous) => [...previous, userMessage]);
      setAskInput("");
      setAskBusy(true);
      setError("");
      try {
        const data = await api(`/api/meetings/${id}/ask`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt, history }),
        });
        const assistantMessage = {
          id: `${Date.now()}-assistant`,
          role: "assistant",
          content: data.answer || "没有生成可用内容。",
          llm: data.llm,
        };
        setMeetingChat(id, (previous) => [...previous, assistantMessage]);
        if (data.llm) {
          setLlmStatus({
            provider: data.llm.provider_label || llmProviderLabel(data.llm.provider),
            transport: data.llm.route || data.llm.transport || "local-api",
            model: data.llm.model,
          });
        }
      } catch (err) {
        const assistantMessage = {
          id: `${Date.now()}-assistant-error`,
          role: "assistant",
          error: true,
          content: userFriendlyError(err.message),
        };
        setMeetingChat(id, (previous) => [...previous, assistantMessage]);
        setError(userFriendlyError(err.message));
      } finally {
        setAskBusy(false);
      }
    },
    [askBusy, askInput, askMessages, meeting?.id, setMeetingChat],
  );

  useEffect(() => {
    refreshMeetings();
  }, [refreshMeetings]);

  useEffect(() => {
    const job = runtimeStatus?.reprocess;
    const id = meeting?.id;
    if (!id || !job || !["done", "error"].includes(job.status)) return;
    const key = `${id}:${job.id || ""}:${job.version_id || ""}:${job.status || ""}:${job.updated_at || ""}`;
    if (completedReprocessRef.current === key) return;
    completedReprocessRef.current = key;
    refreshMeeting(id);
    refreshMeetings();
    if (job.status === "error") {
      setPipelineStatus("处理失败");
      setError(userFriendlyError(job.error));
      return;
    }
    const doneLabels = {
      asr: "文字整理已完成",
      speaker: "说话人分离已完成",
      merge: "按人声整理已完成",
      repair: "自动校对文字已完成",
      notes: "纪要已更新",
    };
    setPipelineStatus(doneLabels[job.level] || "处理已完成");
  }, [meeting?.id, refreshMeeting, refreshMeetings, runtimeStatus?.reprocess]);

  const uploadChunk = useCallback(
    async (blob, durationMs = MAX_SEGMENT_MS, metadata = {}) => {
      const id = meetingIdRef.current;
      if (!id || !blob || blob.size === 0) return;
      const activeConfig = clampRecordingConfig(recordingConfigRef.current);
      const controller = new AbortController();
      activeUploadControllersRef.current.add(controller);
      setPendingChunks((value) => value + 1);
      setPipelineStatus(asrReady ? "保存音频" : "准备语音识别");
      setError("");
      const form = new FormData();
      const extension = blob.type.includes("wav")
        ? "wav"
        : blob.type.includes("mp4") || blob.type.includes("aac")
          ? "m4a"
          : "webm";
      form.append("audio", blob, `chunk-${Date.now()}.${extension}`);
      form.append("duration_ms", String(Math.max(0, Math.round(durationMs))));
      form.append("language", activeConfig.language);
      form.append("asr_model", activeConfig.asrModel);
      form.append("speaker_mode", activeConfig.speakerMode);
      if (metadata.clientChunkId) form.append("client_chunk_id", metadata.clientChunkId);
      if (Number.isFinite(metadata.startedAtMs)) {
        form.append("started_at_ms", String(Math.max(0, Math.round(metadata.startedAtMs))));
      }
      if (Number.isFinite(metadata.endedAtMs)) {
        form.append("ended_at_ms", String(Math.max(0, Math.round(metadata.endedAtMs))));
      }
      if (metadata.cutReason) form.append("cut_reason", metadata.cutReason);
      try {
        const data = await api(`/api/meetings/${id}/chunks`, {
          method: "POST",
          body: form,
          signal: controller.signal,
        });
        if (data.runtime) setRuntimeStatus(data.runtime);
        setPipelineStatus("文字已更新");
        setMeeting((current) => {
          if (!current || current.id !== id) return current;
          return {
            ...current,
            summary: data.summary,
            chunks: [...(current.chunks || []), data.chunk],
            segments: [...(current.segments || []), ...(data.segments || [])],
            utterances: data.utterances || current.utterances || [],
            speakers: data.speakers || current.speakers || [],
          };
        });
      } catch (err) {
        if (err?.name === "AbortError") {
          setPipelineStatus("已停止");
          return;
        }
        setPipelineStatus("处理失败");
        setError(userFriendlyError(err.message));
      } finally {
        activeUploadControllersRef.current.delete(controller);
        setPendingChunks((value) => Math.max(0, value - 1));
      }
    },
    [asrReady],
  );

  const enqueueChunk = useCallback(
    (blob, durationMs, metadata = {}) => {
      uploadChainRef.current = uploadChainRef.current
        .then(() => uploadChunk(blob, durationMs, metadata))
        .catch(() => uploadChunk(blob, durationMs, metadata));
      return uploadChainRef.current;
    },
    [uploadChunk],
  );

  const enqueueAudioWindow = useCallback(
    (chunks, startedAtMs, endedAtMs, reason) => {
      const durationMs = Math.max(0, endedAtMs - startedAtMs);
      if (durationMs < 300) {
        setPipelineStatus("录音中");
        return;
      }
      const samples = concatFloat32(chunks);
      if (samples.length === 0) {
        setPipelineStatus("录音中");
        return;
      }
      chunkSeqRef.current += 1;
      const clientChunkId = `${meetingIdRef.current || "meeting"}-${chunkSeqRef.current}`;
      const blob = encodeWav(samples, audioSampleRateRef.current);
      enqueueChunk(blob, durationMs, {
        clientChunkId,
        startedAtMs,
        endedAtMs,
        cutReason: reason,
      });
      setPipelineStatus(reason);
    },
    [enqueueChunk],
  );

  const closeActiveSegment = useCallback(
    (reason, endedAtMs) => {
      const segment = activeSegmentRef.current;
      if (!segment) return;
      activeSegmentRef.current = null;
      enqueueAudioWindow(segment.chunks, segment.startedAtMs, endedAtMs, reason);
    },
    [enqueueAudioWindow],
  );

  const closePreSpeechProbe = useCallback(
    (reason, endedAtMs) => {
      const probe = preSpeechProbeRef.current;
      if (!probe) return;
      preSpeechProbeRef.current = null;
      enqueueAudioWindow(probe.chunks, probe.startedAtMs, endedAtMs, reason);
    },
    [enqueueAudioWindow],
  );

  const handleAudioFrame = useCallback(
    (input) => {
      if (stopRequestedRef.current) return;
      const activeConfig = clampRecordingConfig(recordingConfigRef.current);
      const frame = applyInputGain(input, activeConfig.inputGain);
      let sum = 0;
      let peak = 0;
      for (let index = 0; index < frame.length; index += 1) {
        const sample = frame[index];
        const abs = Math.abs(sample);
        peak = Math.max(peak, abs);
        sum += sample * sample;
      }
      const level = Math.sqrt(sum / frame.length);
      setVadLevel(level);

      const startMs = totalSamplesRef.current * 1000 / audioSampleRateRef.current;
      totalSamplesRef.current += frame.length;
      const endMs = totalSamplesRef.current * 1000 / audioSampleRateRef.current;
      const shapedLevel = shapeLiveWaveLevel(level, peak, liveWaveformCeilingRef, liveWaveformFloorRef);
      liveWaveformRef.current.push(shapedLevel);
      if (liveWaveformRef.current.length > LIVE_WAVEFORM_BAR_COUNT * 8) {
        liveWaveformRef.current = liveWaveformRef.current.slice(-LIVE_WAVEFORM_BAR_COUNT * 8);
      }
      const now = performance.now();
      if (now - liveWaveformEmitRef.current > 70) {
        liveWaveformEmitRef.current = now;
        setLiveWaveformBars(compressWaveformBars(liveWaveformRef.current));
        setLiveRecordingMs(endMs);
      }
      const noiseFloor = Math.max(0.0006, Number(liveWaveformFloorRef.current) || 0.002);
      const speechDetected = speechDetectedByEnergy(level, peak, noiseFloor);
      let segment = activeSegmentRef.current;
      if (!segment) {
        preSpeechFramesRef.current.push({ frame, startMs, endMs });
        preSpeechFramesRef.current = preSpeechFramesRef.current.filter(
          (item) => endMs - item.endMs <= PRE_SPEECH_BUFFER_MS,
        );
        if (!speechDetected) {
          if (!preSpeechProbeRef.current) {
            preSpeechProbeRef.current = {
              chunks: [],
              startedAtMs: startMs,
            };
          }
          preSpeechProbeRef.current.chunks.push(frame);
          if (endMs - preSpeechProbeRef.current.startedAtMs >= PRE_SPEECH_PROBE_MS) {
            closePreSpeechProbe("VAD 探测", endMs);
          }
          return;
        }
        preSpeechProbeRef.current = null;
        const buffered = preSpeechFramesRef.current;
        const firstBuffered = buffered[0];
        activeSegmentRef.current = {
          chunks: buffered.map((item) => item.frame),
          startedAtMs: firstBuffered?.startMs ?? startMs,
          firstSpeechAtMs: startMs,
          lastSpeechAtMs: endMs,
        };
        preSpeechFramesRef.current = [];
        segment = activeSegmentRef.current;
      } else {
        segment.chunks.push(frame);
        if (speechDetected) {
          segment.lastSpeechAtMs = endMs;
        }
      }

      const durationMs = endMs - segment.startedAtMs;
      const firstSpeechDurationMs = endMs - (segment.firstSpeechAtMs ?? segment.startedAtMs);
      const silenceDurationMs = endMs - (segment.lastSpeechAtMs ?? segment.firstSpeechAtMs ?? segment.startedAtMs);
      if (
        !firstSpeechFlushDoneRef.current
        && firstSpeechDurationMs >= FIRST_SPEECH_FLUSH_MS
        && durationMs >= MIN_SPEECH_WINDOW_MS
      ) {
        firstSpeechFlushDoneRef.current = true;
        closeActiveSegment("首段快速识别", endMs);
      } else if (silenceDurationMs >= SPEECH_END_SILENCE_MS && durationMs >= MIN_SPEECH_WINDOW_MS) {
        closeActiveSegment("VAD 语音结束", endMs);
      } else if (durationMs >= activeConfig.maxSegmentMs) {
        closeActiveSegment("VAD 最大窗口", endMs);
      }
    },
    [closeActiveSegment, closePreSpeechProbe],
  );

  const startMeeting = useCallback(async () => {
    if (!serviceReady) {
      setError("本地语音服务还在启动中，请稍候。");
      return;
    }
    if (!asrReady) {
      setError(asrUnavailableReason || "识别模型尚未加载成功，请检查模型配置。");
      setSettingsTab("recording");
      setSettingsOpen(true);
      return;
    }
    if (recording || busy || importingAudio) return;
    if (!(await ensureRecordingModels())) return;
    setBusy(true);
    setError("");
    setPipelineStatus("启动录音");
    try {
      await requestNativeMicrophonePermission();
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("当前环境不允许访问麦克风。");
      }
      const created = await api("/api/meetings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      setMeeting(created);
      setTitle(created.title || "今天的会议");
      meetingIdRef.current = created.id;
      stopRequestedRef.current = false;
      setLiveWaveformBars([]);
      setLiveRecordingMs(0);
      liveWaveformRef.current = [];
      liveWaveformEmitRef.current = 0;
      liveWaveformCeilingRef.current = 0.055;
      liveWaveformFloorRef.current = 0.002;
      setPipelineStatus("录音中");
      await refreshMeetings();

      const baseAudioConstraints = {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      };
      const selectedAudioConstraints = selectedMicId
        ? { ...baseAudioConstraints, deviceId: { exact: selectedMicId } }
        : baseAudioConstraints;
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: selectedAudioConstraints });
      } catch (err) {
        if (selectedMicId && (err?.name === "OverconstrainedError" || err?.name === "NotFoundError")) {
          selectMicDevice("");
          stream = await navigator.mediaDevices.getUserMedia({ audio: baseAudioConstraints });
        } else {
          throw err;
        }
      }
      streamRef.current = stream;
      await refreshMicDevices();
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error("当前浏览器不支持录音。");
      }

      activeSegmentRef.current = null;
      totalSamplesRef.current = 0;
      chunkSeqRef.current = 0;
      firstSpeechFlushDoneRef.current = false;
      preSpeechFramesRef.current = [];
      preSpeechProbeRef.current = null;

      const audioContext = new AudioContextCtor();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      const processor = audioContext.createScriptProcessor(2048, 1, 1);
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.2;
      audioSampleRateRef.current = audioContext.sampleRate;
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        const output = event.outputBuffer.getChannelData(0);
        output.fill(0);
        handleAudioFrame(input);
      };
      source.connect(analyser);
      source.connect(processor);
      processor.connect(audioContext.destination);
      audioContextRef.current = audioContext;
      sourceRef.current = source;
      analyserRef.current = analyser;
      processorRef.current = processor;
      if (audioContext.state === "suspended") {
        await audioContext.resume();
      }
      setRecording(true);
    } catch (err) {
      setPipelineStatus("启动失败");
      setError(userFriendlyError(err.message));
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    } finally {
      setBusy(false);
    }
  }, [
    asrReady,
    asrUnavailableReason,
    busy,
    ensureRecordingModels,
    handleAudioFrame,
    importingAudio,
    recording,
    refreshMeetings,
    refreshMicDevices,
    selectMicDevice,
    selectedMicId,
    serviceReady,
  ]);

  const runFinalize = useCallback(async (id, { allowWhileRecording = false } = {}) => {
    if (!id || finalizing) return;
    if (!llmReady) {
      setFinalNotesError(llmUnavailableReason);
      setSettingsTab("llm");
      setSettingsOpen(true);
      return;
    }
    if (recording && !allowWhileRecording) {
      setError("请先停止录音，再生成纪要。");
      return;
    }
    setFinalizing(true);
    setFinalNotesError("");
    setStreamingFinalMarkdown("");
    try {
      await uploadChainRef.current;
      setPipelineStatus("最终纪要生成中");
      const response = await fetch(apiUrl(`/api/meetings/${id}/finalize/stream`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force_local: false }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(userFriendlyError(text || `${response.status} ${response.statusText}`));
      }
      let finalized = null;
      let streamedMarkdown = "";
      await readSse(response, {
        chunk: ({ text }) => {
          const next = String(text || "");
          if (!next) return;
          streamedMarkdown += next;
          setStreamingFinalMarkdown(streamedMarkdown);
        },
        replace: ({ markdown }) => {
          streamedMarkdown = String(markdown || "");
          setStreamingFinalMarkdown(streamedMarkdown);
        },
        done: ({ meeting: updated }) => {
          if (updated) {
            finalized = updated;
            setMeeting(updated);
          }
        },
        error: ({ error }) => {
          throw new Error(error || "纪要生成失败。");
        },
      });
      if (finalized) {
        setMeeting(finalized);
      } else {
        await refreshMeeting(id);
      }
      setStreamingFinalMarkdown("");
      setPipelineStatus("完成");
      await refreshMeetings();
    } catch (err) {
      const message = userFriendlyError(err.message);
      setStreamingFinalMarkdown("");
      setPipelineStatus("生成失败");
      setFinalNotesError(message);
    } finally {
      setFinalizing(false);
    }
  }, [finalizing, llmReady, llmUnavailableReason, recording, refreshMeeting, refreshMeetings]);

  const stopRecording = useCallback(async () => {
    if (!recording) return;
    stopRequestedRef.current = true;
    if (activeSegmentRef.current) {
      const endedAtMs = totalSamplesRef.current * 1000 / audioSampleRateRef.current;
      closeActiveSegment("手动停止", endedAtMs);
    } else if (preSpeechProbeRef.current) {
      const endedAtMs = totalSamplesRef.current * 1000 / audioSampleRateRef.current;
      closePreSpeechProbe("VAD 探测结束", endedAtMs);
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current.onaudioprocess = null;
      processorRef.current = null;
    }
    if (sourceRef.current) {
      sourceRef.current.disconnect();
      sourceRef.current = null;
    }
    if (analyserRef.current) {
      analyserRef.current.disconnect();
      analyserRef.current = null;
    }
    if (audioContextRef.current) {
      await audioContextRef.current.close();
      audioContextRef.current = null;
    }
    setRecording(false);
    setVadLevel(0);
    setPipelineStatus("已停止");
    setRuntimeStatus((current) => (
      current
        ? { ...current, active_chunks: [], has_active_chunks: false }
        : current
    ));
    const stoppedMeetingId = meetingIdRef.current;
    if (stoppedMeetingId) {
      try {
        setPipelineStatus("保存最后音频");
        await uploadChainRef.current;
      } catch (err) {
        setError(userFriendlyError(err.message));
      }
      try {
        const stopped = await api(`/api/meetings/${stoppedMeetingId}/stop`, { method: "POST" });
        setMeeting(stopped);
      } catch (err) {
        setError(userFriendlyError(err.message));
        // The local state still carries the recording outcome.
      }
      try {
        await refreshMeeting(stoppedMeetingId);
        await refreshMeetings();
      } catch (err) {
        setError(userFriendlyError(err.message));
      }
      await runFinalize(stoppedMeetingId, { allowWhileRecording: true });
    }
  }, [closeActiveSegment, closePreSpeechProbe, recording, refreshMeeting, refreshMeetings, runFinalize]);

  const finalize = useCallback(async () => {
    const id = meetingIdRef.current || meeting?.id;
    await runFinalize(id);
  }, [meeting?.id, runFinalize]);

  const loadMeeting = useCallback(
    async (id) => {
      if (recording && id !== meetingIdRef.current) return;
      const targetSummary = meetings.find((item) => item.id === id) || (meeting?.id === id ? meeting : null);
      const runId = meetingLoadRunRef.current + 1;
      meetingLoadRunRef.current = runId;
      meetingIdRef.current = id;
      setError("");
      setAudioMergeState(null);
      setPipelineStatus("加载会议");
      if (targetSummary && meeting?.id !== id) {
        const preview = previewMeetingFromSummary(targetSummary);
        if (preview) {
          setMeeting(preview);
          setRuntimeStatus(null);
          setPlaybackPreview({ meetingId: null, positionMs: null });
        }
      }

      const loadingTitle = targetSummary?.title || meeting?.title || "今天的会议";
      const showMergeTimer = window.setTimeout(() => {
        if (meetingLoadRunRef.current !== runId) return;
        setAudioMergeState({
          meetingId: id,
          title: loadingTitle,
        });
      }, 180);

      try {
        const data = await api(`/api/meetings/${id}`);
        if (meetingLoadRunRef.current !== runId) return;
        setMeeting(data);
        setPipelineStatus("会议已加载");
      } catch (err) {
        if (meetingLoadRunRef.current !== runId) return;
        setError(userFriendlyError(err.message));
      } finally {
        window.clearTimeout(showMergeTimer);
        if (meetingLoadRunRef.current === runId) {
          setAudioMergeState(null);
        }
      }
    },
    [meeting, meetings, recording],
  );

  useEffect(() => {
    if (autoLoadedMeetingRef.current || meeting?.id || recording || meetings.length === 0) return;
    autoLoadedMeetingRef.current = true;
    loadMeeting(meetings[0].id);
  }, [loadMeeting, meeting?.id, meetings, recording]);

  const requestDeleteCurrentMeeting = useCallback((event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!meeting?.id) return;
    setPropertiesOpen(false);
    setDeleteTarget({
      id: meeting.id,
      title: meeting.title || "今天的会议",
    });
  }, [meeting?.id, meeting?.title]);

  const deleteMeeting = useCallback(
    async () => {
      const id = deleteTarget?.id;
      if (!id) return;
      setDeleteBusy(true);
      setError("");
      try {
        await api(`/api/meetings/${id}`, { method: "DELETE" });
        if (meetingIdRef.current === id || playbackMeetingId === id) {
          if (playbackTimerRef.current) {
            window.clearTimeout(playbackTimerRef.current);
            playbackTimerRef.current = null;
          }
          if (playbackProgressTimerRef.current) {
            window.clearInterval(playbackProgressTimerRef.current);
            playbackProgressTimerRef.current = null;
          }
          for (const source of playbackSourcesRef.current) {
            try {
              source.stop();
            } catch {
              // Already ended.
            }
          }
          playbackSourcesRef.current = [];
          if (playbackContextRef.current) {
            await playbackContextRef.current.close();
            playbackContextRef.current = null;
          }
          setPlaying(false);
          setPlaybackBusy(false);
          setPlaybackMeetingId(null);
          setPlaybackPositionMs(null);
          setPlaybackDurationMs(0);
          setPlaybackPreview({ meetingId: null, positionMs: null });
          setPlaybackStatus("未播放");
        }
        if (meetingIdRef.current === id) {
          meetingIdRef.current = null;
          setMeeting(null);
          setTitle("今天的会议");
          setTitleSavedAt(0);
          setRuntimeStatus(null);
          setPipelineStatus("待机");
        }
        await refreshMeetings();
        setDeleteTarget(null);
      } catch (err) {
        setError(userFriendlyError(err.message));
      } finally {
        setDeleteBusy(false);
      }
    },
    [deleteTarget?.id, playbackMeetingId, refreshMeetings],
  );

  const stopPlayback = useCallback(async (nextStatus = "已停止", options = {}) => {
    const preservePosition = Boolean(options.preservePosition);
    const preserveMeeting = Boolean(options.preserveMeeting);
    const clearCache = options.clearCache !== false;
    playbackRunRef.current += 1;
    if (playbackTimerRef.current) {
      window.clearTimeout(playbackTimerRef.current);
      playbackTimerRef.current = null;
    }
    if (playbackProgressTimerRef.current) {
      window.clearInterval(playbackProgressTimerRef.current);
      playbackProgressTimerRef.current = null;
    }
    for (const source of playbackSourcesRef.current) {
      try {
        source.stop();
      } catch {
        // Source may already have ended.
      }
    }
    playbackSourcesRef.current = [];
    if (clearCache) playbackCacheRef.current.clear();
    if (playbackContextRef.current) {
      await playbackContextRef.current.close();
      playbackContextRef.current = null;
    }
    setPlaying(false);
    if (!preservePosition) setPlaybackPositionMs(null);
    if (!preserveMeeting) {
      setPlaybackMeetingId(null);
      setPlaybackDurationMs(0);
    }
    setPlaybackBusy(false);
    setPlaybackStatus(nextStatus);
  }, []);

  useEffect(() => {
    if (!recording) return;
    stopPlayback("录制中不可回放").catch(() => {});
  }, [recording, stopPlayback]);

  const decodePlaybackChunk = useCallback(async (context, chunk) => {
    const key = chunk?.id || chunk?.audio_url;
    if (!key || !chunk?.audio_url) {
      throw new Error("回放信息不完整。");
    }
    const cached = playbackCacheRef.current.get(key);
    if (cached) return cached;

    const response = await fetch(apiUrl(chunk.audio_url));
    if (!response.ok) {
      throw new Error(`回放加载失败：${response.status}`);
    }
    const arrayBuffer = await response.arrayBuffer();
    const audioBuffer = await context.decodeAudioData(arrayBuffer);
    playbackCacheRef.current.set(key, audioBuffer);
    while (playbackCacheRef.current.size > 8) {
      const oldestKey = playbackCacheRef.current.keys().next().value;
      playbackCacheRef.current.delete(oldestKey);
    }
    return audioBuffer;
  }, []);

  const startPlaybackAt = useCallback(async (startAtMs = 0) => {
    const id = meetingIdRef.current || meeting?.id;
    if (!id || playbackBusy || recording) return;
    const targetMs = Math.max(0, Number(startAtMs) || 0);
    setPlaybackPreview({ meetingId: null, positionMs: null });
    await stopPlayback("切换回放", { clearCache: false });
    setPlaybackMeetingId(id);
    setPlaybackPositionMs(targetMs);

    setPlaybackBusy(true);
    setPlaybackStatus("加载回放");
    setError("");
    try {
      const manifest = await api(`/api/meetings/${id}/playback`);
      const playableChunks = manifest.chunks || [];
      if (playableChunks.length === 0) {
        await stopPlayback("暂无音频");
        return;
      }
      const manifestDurationMs = Math.max(0, ...playableChunks.map((chunk) => playbackBounds(chunk).endMs));
      setPlaybackDurationMs(manifestDurationMs);

      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error("当前浏览器不支持回放。");
      }
      const context = new AudioContextCtor();
      playbackContextRef.current = context;
      playbackSourcesRef.current = [];

      if (context.state === "suspended") {
        await context.resume();
      }

      const runId = playbackRunRef.current + 1;
      playbackRunRef.current = runId;
      let firstChunkScheduled = false;

      const scheduleChunk = async (index, requestedStartMs = null) => {
        if (runId !== playbackRunRef.current) return;
        if (index >= playableChunks.length) {
          await stopPlayback("播放完成");
          return;
        }

        const chunk = playableChunks[index];
        try {
          const audioBuffer = await decodePlaybackChunk(context, chunk);
          if (runId !== playbackRunRef.current) return;

          const bounds = playbackBounds(chunk);
          const trimStartMs = Math.max(0, Number(chunk.trim_start_ms) || 0);
          const requestedOffsetMs = Number.isFinite(requestedStartMs)
            ? Math.max(0, requestedStartMs - bounds.startMs)
            : 0;
          const offsetSec = Math.max(0, (trimStartMs + requestedOffsetMs) / 1000);
          if (offsetSec >= audioBuffer.duration) {
            await scheduleChunk(index + 1, null);
            return;
          }

          const manifestDurationMs = Number(chunk.playable_duration_ms);
          const playableRemainingSec = Number.isFinite(manifestDurationMs) && manifestDurationMs > 0
            ? Math.max(0, (manifestDurationMs - requestedOffsetMs) / 1000)
            : audioBuffer.duration - offsetSec;
          const durationSec = Math.min(playableRemainingSec, audioBuffer.duration - offsetSec);
          if (durationSec <= 0.05) {
            await scheduleChunk(index + 1, null);
            return;
          }

          const source = context.createBufferSource();
          const startsAt = context.currentTime + 0.035;
          const timelineStartMs = bounds.startMs + requestedOffsetMs;
          source.buffer = audioBuffer;
          source.connect(context.destination);
          source.onended = () => {
            if (runId === playbackRunRef.current) {
              scheduleChunk(index + 1, null);
            }
          };
          playbackSourcesRef.current = [source];
          playbackTimelineRef.current = [{
            contextStartSec: startsAt,
            timelineStartMs,
            timelineEndMs: timelineStartMs + durationSec * 1000,
          }];
          source.start(startsAt, offsetSec, durationSec);

          const nextChunk = playableChunks[index + 1];
          if (nextChunk) {
            decodePlaybackChunk(context, nextChunk).catch(() => {});
          }

          setPlaying(true);
          setPlaybackBusy(false);
          setPlaybackStatus(`回放中 · 第 ${index + 1}/${playableChunks.length} 段`);
          firstChunkScheduled = true;

          if (!playbackProgressTimerRef.current) {
            playbackProgressTimerRef.current = window.setInterval(() => {
              const range = playbackTimelineRef.current[0];
              if (!range) return;
              const elapsedMs = Math.max(0, (context.currentTime - range.contextStartSec) * 1000);
              setPlaybackPositionMs(Math.min(range.timelineEndMs, range.timelineStartMs + elapsedMs));
            }, 80);
          }
        } catch {
          await scheduleChunk(index + 1, null);
        }
      };

      const startIndex = findPlaybackChunkIndex(playableChunks, targetMs);
      await scheduleChunk(startIndex, targetMs);
      if (!firstChunkScheduled) {
        await stopPlayback("暂无可播放音频");
      }
    } catch (err) {
      setError(userFriendlyError(err.message));
      await stopPlayback("回放出错");
    } finally {
      setPlaybackBusy(false);
    }
  }, [decodePlaybackChunk, meeting?.id, playbackBusy, recording, stopPlayback]);

  const playMeeting = useCallback(async () => {
    if (recording) return;
    const isCurrentPlaybackMeeting = Boolean(meeting?.id && playbackMeetingId === meeting.id);
    if (playing && isCurrentPlaybackMeeting) {
      await stopPlayback("已暂停", { preservePosition: true, preserveMeeting: true, clearCache: false });
      return;
    }
    const resumeMs = isCurrentPlaybackMeeting && Number.isFinite(playbackPositionMs)
      ? playbackPositionMs
      : playbackPreview.meetingId === meeting?.id && Number.isFinite(playbackPreview.positionMs)
        ? playbackPreview.positionMs
        : 0;
    await startPlaybackAt(resumeMs);
  }, [meeting?.id, playbackMeetingId, playbackPositionMs, playbackPreview, playing, recording, startPlaybackAt, stopPlayback]);

  useEffect(() => {
    const handleGlobalPlaybackKey = (event) => {
      if (event.defaultPrevented || event.repeat) return;
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.code !== "Space" && event.key !== " " && event.key !== "Spacebar") return;
      if (
        !meeting?.id
        || recording
        || playbackBusy
        || settingsOpen
        || propertiesOpen
        || Boolean(deleteTarget)
        || Boolean(modelLoadState)
        || Boolean(audioMergeState)
      ) {
        return;
      }
      if (globalPlaybackShortcutTarget(event.target)) return;
      event.preventDefault();
      playMeeting().catch((err) => setError(userFriendlyError(err.message)));
    };
    window.addEventListener("keydown", handleGlobalPlaybackKey);
    return () => window.removeEventListener("keydown", handleGlobalPlaybackKey);
  }, [
    audioMergeState,
    deleteTarget,
    meeting?.id,
    modelLoadState,
    playMeeting,
    playbackBusy,
    propertiesOpen,
    recording,
    settingsOpen,
  ]);

  const previewPlaybackAt = useCallback((startMs) => {
    if (recording) return;
    const value = Number(startMs);
    if (!Number.isFinite(value)) return;
    const next = Math.max(0, value);
    if (meeting?.id && playbackMeetingId === meeting.id) {
      setPlaybackPositionMs(next);
    } else if (meeting?.id) {
      setPlaybackPreview({ meetingId: meeting.id, positionMs: next });
    }
  }, [meeting?.id, playbackMeetingId, recording]);

  const playFromTranscript = useCallback(
    async (event, startMs) => {
      event.stopPropagation();
      if (recording) return;
      const value = Number(startMs);
      if (!Number.isFinite(value)) return;
      await startPlaybackAt(value);
    },
    [recording, startPlaybackAt],
  );

  const uploadAudioFile = useCallback(
    async (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      if (importingAudio) return;
      if (recording) {
        setError("录音中不能导入音频，请先停止当前录音。");
        return;
      }
      if (!serviceReady) {
        setError("本地语音服务还在启动中，请稍候。");
        return;
      }
      if (!asrReady) {
        setError(asrUnavailableReason || "识别模型尚未加载成功，请检查模型配置。");
        setSettingsTab("recording");
        setSettingsOpen(true);
        return;
      }
      setError("");
      setPipelineStatus("准备导入音频");
      try {
        if (!(await ensureRecordingModels())) return;
        setImportingAudio(true);
        stopRequestedRef.current = false;
        const importedTitle = titleFromAudioFile(file);
        let id = null;
        if (!id) {
          const created = await api("/api/meetings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: importedTitle }),
          });
          setMeeting(created);
          setTitle(created.title || importedTitle);
          meetingIdRef.current = created.id;
          id = created.id;
        }

        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextCtor) {
          throw new Error("当前浏览器不支持读取这个音频文件。");
        }
        const context = new AudioContextCtor();
        const audioBuffer = await context.decodeAudioData(await file.arrayBuffer());
        await context.close();
        const samples = audioBufferToMono(audioBuffer);
        const slicedChunks = makeVadChunks(samples, audioBuffer.sampleRate, recordingConfigRef.current);
        chunkSeqRef.current = 0;
        setPipelineStatus(`正在整理音频 · ${slicedChunks.length} 段`);
        if (slicedChunks.length === 0) {
          setPipelineStatus("未检测到人声");
          await refreshMeeting(id);
          await refreshMeetings();
          return;
        }
        for (const item of slicedChunks) {
          chunkSeqRef.current += 1;
          const blob = encodeWav(item.samples, audioBuffer.sampleRate);
          await enqueueChunk(blob, item.endedAtMs - item.startedAtMs, {
            clientChunkId: `${id}-import-${chunkSeqRef.current}`,
            startedAtMs: item.startedAtMs,
            endedAtMs: item.endedAtMs,
            cutReason: item.cutReason,
          });
        }
        await refreshMeeting(id);
        await refreshMeetings();
      } catch (err) {
        setPipelineStatus("导入失败");
        setError(userFriendlyError(err.message));
      } finally {
        setImportingAudio(false);
      }
    },
    [
      asrReady,
      asrUnavailableReason,
      enqueueChunk,
      ensureRecordingModels,
      importingAudio,
      recording,
      refreshMeeting,
      refreshMeetings,
      serviceReady,
    ],
  );

  const downloadMeetingFile = useCallback(
    async (kind) => {
      const id = meeting?.id;
      if (!id || downloadBusy) return;
      const titleBase = safeDownloadName(meeting?.title || "今天的会议");
      const isTranscript = kind === "transcript";
      const url = apiUrl(`/api/meetings/${id}/${isTranscript ? "transcript.md" : "export.md"}`);
      const filename = isTranscript ? `${titleBase}-逐字稿.md` : `${titleBase}.md`;
      setDownloadBusy(kind);
      setError("");
      try {
        const content = await fetchTextFile(url);
        const saved = await saveTextFile(filename, content);
        if (saved) {
          setPipelineStatus(isTranscript ? "逐字稿已保存" : "会议纪要已保存");
        }
      } catch (err) {
        setError(userFriendlyError(err.message));
      } finally {
        setDownloadBusy("");
      }
    },
    [downloadBusy, meeting?.id, meeting?.title],
  );
  const downloadTranscript = useCallback(() => downloadMeetingFile("transcript"), [downloadMeetingFile]);
  const downloadNotes = useCallback(() => downloadMeetingFile("notes"), [downloadMeetingFile]);

  const micLevel = displayMicLevel(vadLevel);
  const normalizedRecordingConfigDraft = clampRecordingConfig(recordingConfigDraft);
  const recordingConfigDirty = !recordingConfigsEqual(recordingConfig, recordingConfigDraft)
    || selectedMicDraftId !== selectedMicId;
  const savedLlmDraft = llmDraftFromConfig(llmConfig);
  const llmConfigDirty = llmConfigDraft.provider !== savedLlmDraft.provider
    || llmConfigDraft.preset !== savedLlmDraft.preset
    || llmConfigDraft.apiBase !== savedLlmDraft.apiBase
    || llmConfigDraft.model !== savedLlmDraft.model
    || Boolean(String(llmConfigDraft.apiKey || "").trim());
  const promptConfigDirty = useMemo(() => {
    const savedDrafts = promptDraftsFromConfig(promptConfig);
    const keys = new Set([...Object.keys(savedDrafts), ...Object.keys(promptDrafts)]);
    return Array.from(keys).some((key) => String(promptDrafts[key] ?? "") !== String(savedDrafts[key] ?? ""));
  }, [promptConfig, promptDrafts]);
  const missingRecordingModels = missingModelsForConfig(recordingConfigDraft);
  const recordingAsrModelValue = normalizedRecordingConfigDraft.asrModel;

  const openRecordingSettings = useCallback(() => {
    setSettingsTab("recording");
    setSettingsOpen(true);
  }, []);

  const toggleAppearanceTheme = useCallback(() => {
    setAppearance((current) => clampAppearance({
      ...current,
      theme: current.theme === "dark" ? "light" : "dark",
    }));
  }, []);

  const toggleLocale = useCallback(() => {
    setLocale((current) => (current === "zh" ? "en" : "zh"));
  }, []);

  return (
    <I18nProvider locale={locale}>
    <div className="app-shell" data-theme={appearance.theme} data-palette={appearance.palette} data-locale={locale}>
      <Sidebar
        meeting={meeting}
        meetings={meetings}
        title={title}
        setTitle={setTitle}
        titleDirty={titleDirty}
        titleSaving={titleSaving}
        titleSavedAt={titleSavedAt}
        saveMeetingTitle={saveMeetingTitle}
        reprocessWorking={reprocessWorking}
        activeTranscriptVersion={activeTranscriptVersion}
        editableVersion={editableVersion}
        activateTranscriptVersion={activateTranscriptVersion}
        editBusy={editBusy}
        onOpenRecordingSettings={openRecordingSettings}
        recording={recording}
        startReprocess={startReprocess}
        transcriptItems={transcriptItems}
        createEditableVersion={createEditableVersion}
        finalizing={finalizing}
        finalize={finalize}
        speakerRenameFrom={speakerRenameFrom}
        setSpeakerRenameFrom={setSpeakerRenameFrom}
        speakerRenameTo={speakerRenameTo}
        setSpeakerRenameTo={setSpeakerRenameTo}
        speakerOptions={speakerOptions}
        renameSpeaker={renameSpeaker}
        serviceReady={serviceReady}
        recognitionReady={asrReady}
        recognitionUnavailableReason={asrUnavailableReason}
        busy={busy}
        importingAudio={importingAudio}
        startMeeting={startMeeting}
        stopRecording={stopRecording}
        uploadAudioFile={uploadAudioFile}
        runtimeStatus={displayRuntimeStatus}
        pendingChunks={pendingChunks}
        refreshMeetings={refreshMeetings}
        loadMeeting={loadMeeting}
        playbackMeetingId={playbackMeetingId}
        playbackPositionMs={playbackPositionMs}
        playbackDurationMs={playbackDurationMs}
        playbackPlaying={playing}
        playbackBusy={playbackBusy}
      />

      <div className="main-area">
        <TopBar
          meeting={meeting}
          transcriptCount={transcriptItems.length}
          status={status}
          llmStatus={llmStatus}
          modelStatus={modelStatus}
          servicePillClass={servicePillClass}
          asrWorking={asrWorking}
          runtimeStatus={displayRuntimeStatus}
          pendingChunks={pendingChunks}
          activeModelDownload={activeModelDownload}
          activeModelDownloadMeta={activeModelDownloadMeta}
          recording={recording}
          speakerMode={normalizedRecordingConfig.speakerMode}
          micLevel={micLevel}
          appearance={appearance}
          onToggleTheme={toggleAppearanceTheme}
          onToggleLanguage={toggleLocale}
          onOpenSettings={openRecordingSettings}
        />

        <StartupBanner
          serviceReady={serviceReady}
          serviceStarting={serviceStarting}
          backendDetail={status.backendDetail}
        />

        <MeetingTimeline
          meeting={meeting}
          transcriptItems={transcriptItems}
          chunks={chunks}
          playbackPositionMs={visiblePlaybackPositionMs}
          playing={visiblePlaybackPlaying}
          playbackBusy={visiblePlaybackBusy}
          recording={recording}
          liveWaveformBars={liveWaveformBars}
          liveRecordingMs={liveRecordingMs}
          onPlayToggle={playMeeting}
          onPreview={previewPlaybackAt}
          onJump={startPlaybackAt}
        />

        <main className="main-workspace">
          <TranscriptPane
            meeting={meeting}
            activeTranscriptVersion={activeTranscriptVersion}
            transcriptVersions={meeting?.transcript_versions || []}
            activeVersionId={meeting?.active_version_id || "auto"}
            activateTranscriptVersion={activateTranscriptVersion}
            deleteTranscriptVersion={deleteTranscriptVersion}
            downloadTranscript={downloadTranscript}
            transcriptDownloading={downloadBusy === "transcript"}
            recording={recording}
            recognitionReady={asrReady}
            recognitionUnavailableReason={asrUnavailableReason}
            reprocessWorking={reprocessWorking}
            startReprocess={startReprocess}
            createEditableVersion={createEditableVersion}
            error={error}
            asrWorking={asrWorking}
            runtimeStatus={displayRuntimeStatus}
            pendingChunks={pendingChunks}
            transcriptItems={transcriptItems}
            onOpenMeetingProperties={openMeetingProperties}
            playbackPositionMs={visiblePlaybackPositionMs}
            editingSegmentId={editingSegmentId}
            editableVersion={editableVersion}
            editBusy={editBusy}
            saveSegmentEdits={saveSegmentEdits}
            cancelEditSegment={cancelEditSegment}
            startEditSegment={startEditSegment}
            segmentDrafts={segmentDrafts}
            updateSegmentDraft={updateSegmentDraft}
            playFromTranscript={playFromTranscript}
          />

          <NotesPane
            finalNotesWorking={finalNotesWorking}
            finalize={finalize}
            meeting={meeting}
            recording={recording}
            assistantReady={llmReady}
            assistantUnavailableReason={llmUnavailableReason}
            finalizing={finalizing}
            downloadNotes={downloadNotes}
            notesDownloading={downloadBusy === "notes"}
            finalNotesReady={finalNotesReady}
            activeTranscriptVersion={activeTranscriptVersion}
            finalMarkdownForDisplay={finalMarkdownForDisplay}
            finalNotesStreaming={finalNotesStreaming}
            finalNotesPending={finalNotesPending}
            finalNotesError={finalNotesError}
            transcriptItems={transcriptItems}
          />
        </main>
      </div>

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        llmConfigSaving={llmConfigSaving}
        settingsTab={settingsTab}
        setSettingsTab={setSettingsTab}
        missingRecordingModels={missingRecordingModels}
        selectedMicId={selectedMicDraftId}
        selectMicDevice={setSelectedMicDraftId}
        recording={recording}
        micDevices={micDevices}
        recordingAsrModelValue={recordingAsrModelValue}
        updateRecordingConfig={updateRecordingConfigDraft}
        saveRecordingConfig={saveRecordingConfig}
        recordingConfigSaving={recordingConfigSaving}
        recordingConfigDirty={recordingConfigDirty}
        recordingConfigError={recordingConfigError}
        modelLoading={modelLoadState?.status === "loading" || recordingConfigSaving}
        selectableAsrModels={selectableAsrModels}
        asrModelGroups={asrModelGroups}
        modelCatalogByKey={modelCatalogByKey}
        recordingConfig={recordingConfigDraft}
        ensureRecordingModels={ensureRecordingModels}
        activeModelDownload={activeModelDownload}
        saveLlmConfig={saveLlmConfig}
        llmConfig={llmConfig}
        llmConfigDraft={llmConfigDraft}
        updateLlmConfigDraft={updateLlmConfigDraft}
        llmConfigDirty={llmConfigDirty}
        llmConfigError={llmConfigError}
        promptConfig={promptConfig}
        promptDrafts={promptDrafts}
        promptConfigSaving={promptConfigSaving}
        promptConfigDirty={promptConfigDirty}
        promptConfigError={promptConfigError}
        updatePromptDraft={updatePromptDraft}
        resetPromptDraft={resetPromptDraft}
        savePromptConfig={savePromptConfig}
        refreshPromptConfig={refreshPromptConfig}
        modelCatalogAsrGroups={modelCatalogAsrGroups}
        modelCatalog={modelCatalog}
        downloadModel={downloadModel}
        deleteModel={deleteModel}
        appearance={appearance}
        updateAppearance={updateAppearance}
      />

      <MeetingPropertiesDialog
        open={propertiesOpen}
        meeting={meeting}
        draft={propertiesDraft}
        saving={propertiesSaving}
        error={propertiesError}
        onChange={updatePropertiesDraft}
        onCancel={closeMeetingProperties}
        onSave={saveMeetingProperties}
        onDelete={requestDeleteCurrentMeeting}
      />

      <DeleteMeetingDialog
        deleteTarget={deleteTarget}
        deleteBusy={deleteBusy}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={deleteMeeting}
      />

      <AudioMergeDialog state={audioMergeState} />

      <ModelLoadDialog
        state={modelLoadState}
        onClose={() => setModelLoadState(null)}
      />
    </div>
    </I18nProvider>
  );
}

export default App;
