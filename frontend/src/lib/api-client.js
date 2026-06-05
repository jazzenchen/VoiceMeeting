export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8788";

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

export async function api(path, options = {}) {
  const { timeoutMs = 0, ...fetchOptions } = options;
  let timeoutId = 0;
  let timedOut = false;
  let abortController = null;
  let abortOriginal = null;

  if (timeoutMs > 0) {
    abortController = new AbortController();
    abortOriginal = () => abortController.abort();
    if (fetchOptions.signal?.aborted) {
      abortController.abort();
    } else {
      fetchOptions.signal?.addEventListener?.("abort", abortOriginal, { once: true });
    }
    timeoutId = setTimeout(() => {
      timedOut = true;
      abortController.abort();
    }, timeoutMs);
    fetchOptions.signal = abortController.signal;
  }

  let response;
  try {
    response = await fetch(apiUrl(path), fetchOptions);
  } catch (error) {
    if (timedOut) {
      throw new Error("本地服务响应超时，请稍后重试。");
    }
    throw error;
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    if (abortOriginal) {
      options.signal?.removeEventListener?.("abort", abortOriginal);
    }
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

export function wsUrl(path) {
  const base = new URL(API_BASE);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${base.origin}${path}`;
}

export async function fetchTextFile(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.text();
}

export async function readSse(response, handlers) {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const emit = (raw) => {
    const clean = raw.trim();
    if (!clean) return;
    let event = "message";
    const dataLines = [];
    for (const line of clean.split("\n")) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    const payloadText = dataLines.join("\n");
    const payload = payloadText ? JSON.parse(payloadText) : {};
    handlers[event]?.(payload);
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      emit(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
  }
  buffer += decoder.decode();
  emit(buffer);
}
