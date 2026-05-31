const MIC_DEVICE_STORAGE_KEY = "voice-meeting-mic-device";

export function loadSelectedMicId() {
  try {
    return window.localStorage.getItem(MIC_DEVICE_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function saveSelectedMicId(value) {
  try {
    if (value) {
      window.localStorage.setItem(MIC_DEVICE_STORAGE_KEY, value);
    } else {
      window.localStorage.removeItem(MIC_DEVICE_STORAGE_KEY);
    }
  } catch {
    // Local persistence is best-effort.
  }
}
