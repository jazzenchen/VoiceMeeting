export const DEFAULT_LLM_CONFIG = {
  provider: "vibearound",
  litellm: {
    preset: "openai",
    api_base: "",
    model: "",
    has_api_key: false,
  },
  config_error: "",
};

export const LITELLM_PRESETS = [
  {
    id: "openai",
    label: "OpenAI",
    helper: "使用 LiteLLM 模型名前缀，例如 openai/gpt-4o-mini。",
    modelPrefix: "openai/",
    modelPlaceholder: "openai/gpt-4o-mini",
    apiBasePlaceholder: "留空使用官方地址",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    helper: "使用 Anthropic 模型名前缀，例如 anthropic/claude-sonnet-4-5。",
    modelPrefix: "anthropic/",
    modelPlaceholder: "anthropic/claude-sonnet-4-5",
    apiBasePlaceholder: "留空使用官方地址",
  },
  {
    id: "gemini",
    label: "Google Gemini",
    helper: "使用 Gemini 模型名前缀，例如 gemini/gemini-2.5-pro。",
    modelPrefix: "gemini/",
    modelPlaceholder: "gemini/gemini-2.5-pro",
    apiBasePlaceholder: "留空使用官方地址",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    helper: "使用 OpenRouter 模型名前缀，例如 openrouter/openai/gpt-4o-mini。",
    modelPrefix: "openrouter/",
    modelPlaceholder: "openrouter/openai/gpt-4o-mini",
    apiBasePlaceholder: "留空使用 OpenRouter 默认地址",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    helper: "使用 DeepSeek 模型名前缀，例如 deepseek/deepseek-chat。",
    modelPrefix: "deepseek/",
    modelPlaceholder: "deepseek/deepseek-chat",
    apiBasePlaceholder: "留空使用官方地址",
  },
  {
    id: "openai-compatible",
    label: "OpenAI Compatible",
    helper: "兼容 /v1/chat/completions 的服务一般使用 openai/ 前缀，并填写 API Base URL。",
    modelPrefix: "openai/",
    modelPlaceholder: "openai/your-model",
    apiBasePlaceholder: "https://your-endpoint.example/v1",
  },
  {
    id: "ollama",
    label: "Ollama",
    helper: "本机 Ollama 通常使用 ollama/ 前缀，API Base 可填 http://localhost:11434。",
    modelPrefix: "ollama/",
    modelPlaceholder: "ollama/llama3.1",
    defaultApiBase: "http://localhost:11434",
    apiBasePlaceholder: "http://localhost:11434",
  },
  {
    id: "custom",
    label: "Custom",
    helper: "按 LiteLLM 文档填写完整模型名；API Key 和 API Base 按服务要求填写。",
    modelPrefix: "",
    modelPlaceholder: "provider/model-name",
    apiBasePlaceholder: "可选",
  },
];

export function litellmPresetById(id) {
  return LITELLM_PRESETS.find((item) => item.id === id) || LITELLM_PRESETS[0];
}

export function normalizeLlmConfig(value) {
  const litellm = value?.litellm || {};
  return {
    provider: value?.provider === "litellm" ? "litellm" : "vibearound",
    litellm: {
      preset: String(litellm.preset || "openai"),
      api_base: String(litellm.api_base || ""),
      model: String(litellm.model || ""),
      has_api_key: Boolean(litellm.has_api_key),
    },
    config_error: String(value?.config_error || ""),
  };
}

export function llmDraftFromConfig(value) {
  const config = normalizeLlmConfig(value);
  return {
    provider: config.provider,
    preset: config.litellm.preset,
    apiBase: config.litellm.api_base,
    apiKey: "",
    model: config.litellm.model,
  };
}

export function promptDraftsFromConfig(value) {
  const prompts = Array.isArray(value?.prompts) ? value.prompts : [];
  return Object.fromEntries(prompts.map((item) => [item.key, item.value || item.default || ""]));
}
