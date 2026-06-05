import { LANGUAGE_OPTIONS, SPEAKER_MODE_OPTIONS } from "@/lib/meeting-display";

export const MAX_SEGMENT_MS = 15000;
export const FASTER_ASR_MODEL_ORDER = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"];
export const MLX_ASR_MODEL_ORDER = FASTER_ASR_MODEL_ORDER.map((model) => `mlx-${model}`);
export const FUNASR_MODEL_ORDER = ["funasr-sensevoice-small", "funasr-paraformer-zh"];
export const ASR_MODEL_ORDER = [...MLX_ASR_MODEL_ORDER, ...FASTER_ASR_MODEL_ORDER, ...FUNASR_MODEL_ORDER];

export const DEFAULT_RECORDING_CONFIG = {
  language: "mixed",
  asrModel: "small",
  speakerMode: "voiceprint",
  maxSegmentMs: MAX_SEGMENT_MS,
  inputGain: 1,
};

export function clampRecordingConfig(value = {}) {
  const config = { ...DEFAULT_RECORDING_CONFIG, ...value };
  const maxSegmentMs = Number(config.maxSegmentMs);
  const inputGain = Number(config.inputGain);
  return {
    language: LANGUAGE_OPTIONS.some(([key]) => key === config.language) ? config.language : "mixed",
    asrModel: ASR_MODEL_ORDER.includes(config.asrModel) ? config.asrModel : "small",
    speakerMode: SPEAKER_MODE_OPTIONS.some(([key]) => key === config.speakerMode)
      ? config.speakerMode
      : "voiceprint",
    maxSegmentMs: Number.isFinite(maxSegmentMs) ? Math.min(30000, Math.max(8000, maxSegmentMs)) : MAX_SEGMENT_MS,
    inputGain: Number.isFinite(inputGain) ? Math.min(2.5, Math.max(0.8, inputGain)) : DEFAULT_RECORDING_CONFIG.inputGain,
  };
}

export function recordingConfigFromServer(value = {}) {
  return clampRecordingConfig({
    language: value.language,
    asrModel: value.asr_model,
    speakerMode: value.speaker_mode,
    maxSegmentMs: value.max_segment_ms,
    inputGain: value.input_gain,
  });
}

export function recordingConfigToServer(value = {}) {
  const config = clampRecordingConfig(value);
  return {
    language: config.language,
    asr_model: config.asrModel,
    speaker_mode: config.speakerMode,
    max_segment_ms: config.maxSegmentMs,
    input_gain: config.inputGain,
  };
}

export function loadRecordingConfig() {
  return clampRecordingConfig(DEFAULT_RECORDING_CONFIG);
}

export function recordingConfigsEqual(left, right) {
  const a = clampRecordingConfig(left);
  const b = clampRecordingConfig(right);
  return a.language === b.language
    && a.asrModel === b.asrModel
    && a.speakerMode === b.speakerMode
    && a.maxSegmentMs === b.maxSegmentMs
    && a.inputGain === b.inputGain;
}
