import { invoke } from "@tauri-apps/api/core";

const TAURI_AVAILABLE = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export function safeDownloadName(value, fallback = "meeting") {
  return (String(value || fallback)
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim() || fallback);
}

export async function saveTextFile(filename, content) {
  if (TAURI_AVAILABLE) {
    const result = await invoke("save_markdown_file", {
      defaultFilename: filename,
      content,
    });
    return Boolean(result?.saved);
  }

  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 1000);
  return true;
}

export async function requestNativeMicrophonePermission() {
  if (!TAURI_AVAILABLE) return;
  try {
    await invoke("request_microphone_permission");
  } catch (err) {
    throw new Error(String(err?.message || err || ""));
  }
}
