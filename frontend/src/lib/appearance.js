export const APPEARANCE_STORAGE_KEY = "voice-meeting-appearance";

export const APPEARANCE_THEMES = {
  dark: {
    label: "深色",
    preview: "#0a0b0e",
  },
  light: {
    label: "浅色",
    preview: "#fafafa",
  },
};

export const APPEARANCE_PALETTES = {
  mono: {
    label: "单色",
    description: "极简中性",
    dark: { accent: "#f43f5e", foreground: "#e4e5ea", muted: "#7a7d87", risk: "#f43f5e" },
    light: { accent: "#e11d48", foreground: "#1a1c22", muted: "#85878f", risk: "#e11d48" },
  },
  cobalt: {
    label: "钴蓝",
    description: "冷峻清晰",
    dark: { accent: "#5b8def", foreground: "#5b8def", muted: "#8a8d96", risk: "#f43f5e" },
    light: { accent: "#2952d9", foreground: "#2952d9", muted: "#7c8088", risk: "#e11d48" },
  },
  iris: {
    label: "鸢尾",
    description: "柔和醒目",
    dark: { accent: "#9b87f5", foreground: "#9b87f5", muted: "#85838d", risk: "#f43f5e" },
    light: { accent: "#5e4ed4", foreground: "#5e4ed4", muted: "#7c7d86", risk: "#e11d48" },
  },
  phosphor: {
    label: "荧光",
    description: "终端感",
    dark: { accent: "#22d68a", foreground: "#22d68a", muted: "#7a7d87", risk: "#ff7068" },
    light: { accent: "#0f8a52", foreground: "#0f8a52", muted: "#7c8088", risk: "#e0533e" },
  },
};

export const APPEARANCE_THEME_ORDER = ["dark", "light"];
export const APPEARANCE_PALETTE_ORDER = ["mono", "cobalt", "iris", "phosphor"];

export const DEFAULT_APPEARANCE = {
  theme: "dark",
  palette: "cobalt",
};

export function clampAppearance(value = {}) {
  const theme = APPEARANCE_THEME_ORDER.includes(value.theme) ? value.theme : DEFAULT_APPEARANCE.theme;
  const palette = APPEARANCE_PALETTE_ORDER.includes(value.palette) ? value.palette : DEFAULT_APPEARANCE.palette;
  return { theme, palette };
}

export function loadAppearance() {
  try {
    const raw = window.localStorage.getItem(APPEARANCE_STORAGE_KEY);
    return clampAppearance(raw ? JSON.parse(raw) : DEFAULT_APPEARANCE);
  } catch {
    return clampAppearance(DEFAULT_APPEARANCE);
  }
}

export function saveAppearance(value) {
  try {
    window.localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(clampAppearance(value)));
  } catch {
    // Local persistence is best-effort.
  }
}
