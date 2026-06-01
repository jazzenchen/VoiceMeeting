export function userFriendlyError(message) {
  const raw = String(message || "").trim();
  if (!raw) return "操作没有完成，请稍后重试。";

  let detail = raw;
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed?.detail === "string") {
      detail = parsed.detail;
    } else if (Array.isArray(parsed?.detail)) {
      detail = parsed.detail.map((item) => item?.msg || item).join("；");
    }
  } catch {
    // Keep the raw message.
  }

  const lower = detail.toLowerCase();
  if (lower.includes("meeting not found")) return "找不到这场会议，可能已经被删除。";
  if (lower.includes("transcript version not found") || lower.includes("source transcript version not found")) {
    return "找不到这份稿件，请刷新后再试。";
  }
  if (lower.includes("segment not found")) return "找不到这段文字，请刷新后再试。";
  if (lower.includes("speaker not found")) return "当前稿件里没有找到这个说话人。";
  if (lower.includes("manual edit version") || lower.includes("editable")) {
    return "请先创建可编辑副本，再修改文字或说话人。";
  }
  if (lower.includes("prompt cannot be empty")) return "请输入要生成的内容。";
  if (lower.includes("no transcript or summary")) return "这场会议还没有可用内容，录音或导入音频后再试。";
  if (lower.includes("timed out") || lower.includes("timeout")) return "生成时间太久，已停止等待。请稍后重试。";
  if (lower.includes("empty audio")) return "这段音频为空，请重新录制或导入。";
  if (lower.includes("unsupported asr language")) return "当前语言暂不支持，请换一种语言设置。";
  if (lower.includes("asr model is not available locally")) return "本地还没有这套识别资源，请选择已有的识别方式。";
  if (lower.includes("unsupported asr model")) return "当前识别方式不可用，请换一个选项。";
  if (lower === "not found" || lower.includes('"not found"')) {
    return "当前本地语音服务版本过旧，请完全退出旧版 VoiceMeeting 后重新打开。";
  }
  if (lower.includes("ffmpeg") || lower.includes("invalid data") || lower.includes("error opening input")) {
    return "音频文件无法读取，请换一个常见格式，或重新录制。";
  }
  if (lower.includes("audio file not found")) return "找不到本地音频文件，可能已经被移动或删除。";
  if (lower.includes("chunk not found")) return "找不到这段音频，请刷新后再试。";
  if (lower.includes("web audio") || lower.includes("audio playback") || lower.includes("audio decoding")) {
    return "当前浏览器不支持这个音频操作，请换一个浏览器或重新导入音频。";
  }
  if (
    lower.includes("notallowederror")
    || lower.includes("not allowed by the user agent")
    || lower.includes("permission denied")
    || lower.includes("permission dismissed")
    || lower.includes("麦克风权限")
  ) {
    return "麦克风权限未开启。请到系统设置的麦克风权限里允许 VoiceMeeting，然后重新开始录音。";
  }
  if (lower.includes("playback")) return "回放加载失败，请刷新后再试。";
  if (lower.includes("vibearound") || lower.includes("bridge") || lower.includes("profile")) {
    return "会议助手暂时不可用，请确认 VibeAround 正在运行后再试。";
  }
  if (/^http\s+\d+/i.test(detail) || /^\d{3}\s/.test(detail)) {
    return "本地服务返回异常，请稍后重试。";
  }
  return detail;
}
