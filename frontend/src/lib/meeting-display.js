export const WAVE_PATTERN = [0.35, 0.62, 0.48, 0.84, 0.58, 1.0, 0.72, 0.42, 0.68, 0.92, 0.54, 0.78];

export const LANGUAGE_OPTIONS = [
  ["auto", "自动多语种"],
  ["mixed", "中英优先"],
  ["zh", "中文"],
  ["en", "English"],
  ["ja", "日本語"],
  ["ko", "한국어"],
  ["fr", "Français"],
  ["de", "Deutsch"],
  ["es", "Español"],
  ["ru", "Русский"],
  ["pt", "Português"],
];

export const INPUT_GAIN_OPTIONS = [
  [1, "原声"],
  [1.3, "轻度增强"],
  [1.6, "会议增强"],
  [2, "远距增强"],
];

export const SPEAKER_MODE_OPTIONS = [
  ["voiceprint", "声纹跟踪"],
  ["diarization", "高精度分离"],
  ["off", "不区分"],
];

export const PIPELINE_STEPS = ["上传", "转码", "识别", "说话人", "纪要"];

export function languageName(value) {
  const option = LANGUAGE_OPTIONS.find(([key]) => key === value);
  return option?.[1] || value || "自动";
}

export function speakerModeName(value) {
  const option = SPEAKER_MODE_OPTIONS.find(([key]) => key === value);
  return option?.[1] || value || "声纹跟踪";
}

export function micDeviceLabel(device, index) {
  if (device?.label) return device.label;
  if (device?.deviceId === "default") return "系统默认麦克风";
  return `麦克风 ${index + 1}`;
}

export function inputGainName(value) {
  const numeric = Number(value);
  const option = INPUT_GAIN_OPTIONS.find(([key]) => key === numeric);
  return option?.[1] || `${numeric.toFixed(1)}x`;
}

export function bytesLabel(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)}GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)}MB`;
  return `${Math.round(bytes / 1024)}KB`;
}

export function progressPercent(job) {
  const value = Number(job?.progress);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value * 100)));
}

function downloadBytesText(job) {
  const downloaded = Number(job?.downloaded_bytes);
  const total = Number(job?.total_bytes);
  if (Number.isFinite(downloaded) && downloaded > 0 && Number.isFinite(total) && total > 0) {
    return `${bytesLabel(downloaded)} / ${bytesLabel(total)}`;
  }
  if (Number.isFinite(total) && total > 0) return bytesLabel(total);
  return "";
}

export function downloadStageText(job) {
  const parts = [job?.stage, job?.file, downloadBytesText(job)]
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  return parts.join(" · ");
}

export function formatTime(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export function formatOffset(value) {
  if (!Number.isFinite(value)) return "";
  const totalSeconds = Math.max(0, Math.floor(value / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatAsrDisplay(asr, fallback) {
  if (!asr) return languageName(fallback);
  const topLanguages = Array.isArray(asr.top_languages) ? asr.top_languages.slice(0, 3) : [];
  if (topLanguages.length > 0) {
    return topLanguages
      .map((item) => `${languageName(item.language)} ${Math.round((item.probability || 0) * 100)}%`)
      .join(" · ");
  }
  if (asr.language) {
    const confidence = Number.isFinite(asr.language_probability)
      ? ` ${Math.round(asr.language_probability * 100)}%`
      : "";
    return `${languageName(asr.language)}${confidence}`;
  }
  return languageName(fallback);
}

export function transcriptVersionName(version, fallbackId = "auto") {
  const kind = version?.kind || (fallbackId === "auto" ? "initial" : "");
  const names = {
    initial: "原始稿",
    asr: "重新识别稿",
    speaker: "说话人修正版",
    merge: "段落整理稿",
    "llm-repair": "文字精修稿",
    "manual-edit": "手动修改稿",
    manual: "手动稿",
  };
  return names[kind] || version?.label || fallbackId || "当前稿";
}

export function transcriptVersionOption(version, t = (value) => value) {
  const name = transcriptVersionName(version, version?.id);
  const time = formatTime(version?.created_at);
  return time ? `${t(name)} · ${time}` : t(name);
}

export function transcriptVersionHint(version, editable, t = (value, values) => value) {
  if (editable) return t("这份可以直接改文字和人名。");
  const name = transcriptVersionName(version, version?.id);
  if ((version?.kind || "initial") === "initial") {
    return t("{name}会保留不动；需要改字时先创建副本。", { name: t(name) });
  }
  return t("{name}会保留不动；需要手动改时先创建副本。", { name: t(name) });
}

export function asrModelName(model) {
  if (String(model || "").startsWith("mlx-")) {
    return `MLX ${asrModelName(String(model).slice(4))}`;
  }
  const names = {
    tiny: "轻量识别",
    base: "快速识别",
    small: "标准识别",
    medium: "高精度识别",
    "large-v3": "最高精度识别",
    "large-v3-turbo": "高精度加速",
  };
  return names[model] || model;
}

export function meetingStatusName(statusValue) {
  const labels = {
    recording: "录音中",
    stopped: "已停止",
    completed: "已完成",
    ready: "待开始",
  };
  return labels[statusValue] || "待开始";
}

export function serviceStatusText(value) {
  const labels = {
    ready: "已连接",
    starting: "启动中",
    checking: "检查中",
    fallback: "可用",
    offline: "未连接",
    unknown: "未确认",
  };
  return labels[value] || "未确认";
}

export function assistantRouteText(llm) {
  if (!llm?.transport && !llm?.route && !llm?.provider) return "待连接";
  const route = String(llm?.route || llm?.transport || llm?.provider || "");
  if (llm?.provider === "openai-chat" || route.includes("openai-chat")) {
    return llm?.model ? `接口 · ${llm.model}` : "接口";
  }
  if (route.includes("web-chat")) return "Codex 通道";
  return "VibeAround";
}

export function assistantStatusText(llm) {
  const text = assistantRouteText(llm);
  if (!text || text === "待连接") return "待配置";
  return text;
}

export function llmProviderLabel(value) {
  if (value === "openai-chat") return "接口";
  return "VibeAround";
}

function chunkStageLabel(statusValue) {
  const labels = {
    saved: "排队",
    converting: "准备音频",
    transcribing: "识别",
    diarizing: "分辨说话人",
    identifying_speakers: "匹配说话人",
    done: "完成",
    error: "出错",
  };
  return labels[statusValue] || statusValue || "待机";
}

export function runtimeLine(runtime, pendingChunks, t = (value, values) => value) {
  const reprocess = runtime?.reprocess;
  if (reprocess && ["queued", "running"].includes(reprocess.status)) {
    const total = Number(reprocess.total);
    const progress = Number(reprocess.progress);
    const stage = String(reprocess.stage || "处理中");
    const embedded = stage.match(/^(.*?)(\d+)\s*\/\s*(\d+)\s*$/u);
    if (embedded) {
      return t("{stage}：第 {done} / {total} 段", {
        stage: t(embedded[1].trim() || "处理中"),
        done: embedded[2],
        total: embedded[3],
      });
    }
    if (Number.isFinite(total) && total > 1) {
      const done = Math.min(total, Math.max(0, progress || 0));
      return t("{stage}：已完成 {done} / {total} 段", {
        stage: t(stage),
        done,
        total,
      });
    }
    return t(stage);
  }
  const active = runtime?.active_chunks || [];
  if (pendingChunks > 0 && active.length === 0) return t("保存音频中");
  if (active.length > 0) {
    const first = active[0];
    return t("第 {seq} 段 {stage}", {
      seq: first.seq || active.length,
      stage: t(chunkStageLabel(first.status)),
    });
  }
  return "";
}

export function transcriptParts(item) {
  if (Array.isArray(item?.parts) && item.parts.length > 0) {
    return item.parts;
  }
  return [
    {
      id: item?.id,
      text: item?.text || "",
      start_ms: item?.start_ms,
      end_ms: item?.end_ms,
    },
  ];
}
