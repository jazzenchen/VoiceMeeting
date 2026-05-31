export const DEFAULT_LLM_CONFIG = {
  provider: "vibearound",
  openai_chat: {
    base_url: "",
    model: "",
    has_api_key: false,
  },
};

export function normalizeLlmConfig(value) {
  const openaiChat = value?.openai_chat || {};
  return {
    provider: value?.provider === "openai-chat" ? "openai-chat" : "vibearound",
    openai_chat: {
      base_url: String(openaiChat.base_url || ""),
      model: String(openaiChat.model || ""),
      has_api_key: Boolean(openaiChat.has_api_key),
    },
  };
}

export function llmDraftFromConfig(value) {
  const config = normalizeLlmConfig(value);
  return {
    provider: config.provider,
    baseUrl: config.openai_chat.base_url,
    apiKey: "",
    model: config.openai_chat.model,
  };
}

export function promptDraftsFromConfig(value) {
  const prompts = Array.isArray(value?.prompts) ? value.prompts : [];
  return Object.fromEntries(prompts.map((item) => [item.key, item.value || item.default || ""]));
}
