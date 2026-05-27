from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import DATA_DIR
from .vibearound import VibeAroundClient


LLM_CONFIG_PATH = DATA_DIR / "llm_config.json"
LLM_PROVIDER_VIBEAROUND = "vibearound"
LLM_PROVIDER_OPENAI_CHAT = "openai-chat"


def _default_config() -> Dict[str, Any]:
    return {
        "provider": LLM_PROVIDER_VIBEAROUND,
        "openai_chat": {
            "base_url": "",
            "api_key": "",
            "model": "",
        },
    }


def _clean_provider(value: Any) -> str:
    provider = str(value or LLM_PROVIDER_VIBEAROUND).strip().lower()
    if provider in {"openai", "openai_chat", "interface", "api"}:
        return LLM_PROVIDER_OPENAI_CHAT
    if provider == LLM_PROVIDER_OPENAI_CHAT:
        return LLM_PROVIDER_OPENAI_CHAT
    return LLM_PROVIDER_VIBEAROUND


def _clean_openai_chat(value: Any) -> Dict[str, str]:
    data = value if isinstance(value, dict) else {}
    return {
        "base_url": str(data.get("base_url") or data.get("baseUrl") or "").strip(),
        "api_key": str(data.get("api_key") or data.get("apiKey") or "").strip(),
        "model": str(data.get("model") or "").strip(),
    }


def _merge_config(raw: Any) -> Dict[str, Any]:
    defaults = _default_config()
    data = raw if isinstance(raw, dict) else {}
    openai_chat = {**defaults["openai_chat"], **_clean_openai_chat(data.get("openai_chat"))}
    return {
        "provider": _clean_provider(data.get("provider")),
        "openai_chat": openai_chat,
    }


class OpenAIChatClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def _chat_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages: List[Dict[str, str]], stream: bool) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if isinstance(content.get("text"), str):
                return content["text"]
            if isinstance(content.get("content"), str):
                return content["content"]
        if isinstance(content, list):
            return "".join(self._content_to_text(item) for item in content)
        return ""

    async def chat(self, messages: List[Dict[str, str]], timeout: float = 90.0) -> str:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self._chat_url(),
                headers=self._headers(),
                content=json.dumps(self._payload(messages, stream=False), ensure_ascii=False),
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI Chat 接口没有返回 choices。")
        message = choices[0].get("message") or {}
        content = self._content_to_text(message.get("content"))
        if content:
            return content
        raise RuntimeError("OpenAI Chat 接口没有返回可用内容。")

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        timeout: float = 90.0,
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                self._chat_url(),
                headers=self._headers(),
                content=json.dumps(self._payload(messages, stream=True), ensure_ascii=False),
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    raw = await response.aread()
                    data = json.loads(raw.decode("utf-8"))
                    choices = data.get("choices") or []
                    if not choices:
                        raise RuntimeError("OpenAI Chat 接口没有返回 choices。")
                    content = self._content_to_text((choices[0].get("message") or {}).get("content"))
                    if content:
                        yield content
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    event = json.loads(data)
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = self._content_to_text(delta.get("content"))
                    if content:
                        yield content


class LLMManager:
    def __init__(self, vibearound: VibeAroundClient, config_path: Path = LLM_CONFIG_PATH) -> None:
        self.vibearound = vibearound
        self.config_path = config_path

    def _read_config(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self.config_path.read_text())
        except Exception:
            raw = {}
        return _merge_config(raw)

    def _write_config(self, config: Dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
        try:
            self.config_path.chmod(0o600)
        except Exception:
            pass

    def public_config(self) -> Dict[str, Any]:
        config = self._read_config()
        openai_chat = config["openai_chat"]
        return {
            "provider": config["provider"],
            "openai_chat": {
                "base_url": openai_chat["base_url"],
                "model": openai_chat["model"],
                "has_api_key": bool(openai_chat["api_key"]),
            },
        }

    def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current = self._read_config()
        provider = _clean_provider(payload.get("provider"))
        incoming_openai = _clean_openai_chat(payload.get("openai_chat"))
        openai_chat = {
            "base_url": incoming_openai["base_url"] or current["openai_chat"]["base_url"],
            "api_key": incoming_openai["api_key"] or current["openai_chat"]["api_key"],
            "model": incoming_openai["model"] or current["openai_chat"]["model"],
        }

        if provider == LLM_PROVIDER_OPENAI_CHAT:
            missing = [
                label
                for label, value in (
                    ("baseurl", openai_chat["base_url"]),
                    ("api key", openai_chat["api_key"]),
                    ("model", openai_chat["model"]),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"接口配置缺少：{'、'.join(missing)}。")

        config = {
            "provider": provider,
            "openai_chat": openai_chat,
        }
        self._write_config(config)
        return self.public_config()

    def _openai_client(self) -> OpenAIChatClient:
        config = self._read_config()["openai_chat"]
        missing = [
            label
            for label, value in (
                ("baseurl", config["base_url"]),
                ("api key", config["api_key"]),
                ("model", config["model"]),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"接口配置缺少：{'、'.join(missing)}。")
        return OpenAIChatClient(config["base_url"], config["api_key"], config["model"])

    def active_provider(self) -> str:
        return self._read_config()["provider"]

    def describe(self) -> Dict[str, Any]:
        config = self._read_config()
        if config["provider"] == LLM_PROVIDER_OPENAI_CHAT:
            openai_chat = config["openai_chat"]
            return {
                "provider": LLM_PROVIDER_OPENAI_CHAT,
                "provider_label": "接口",
                "target_api_type": "openai-chat",
                "model": openai_chat["model"],
                "route": "direct.openai-chat",
                "transport": "openai-chat",
                "base_url": openai_chat["base_url"],
                "has_api_key": bool(openai_chat["api_key"]),
            }
        return {
            "provider": self.vibearound.profile_id,
            "provider_label": self.vibearound.provider_label(),
            "target_api_type": self.vibearound.target_api_type,
            "model": self.vibearound.model,
            "route": self.vibearound.route,
            "transport": self.vibearound.transport,
            "agent": self.vibearound.agent,
            "session_id": getattr(self.vibearound, "_web_session_id", None),
        }

    async def status(self) -> Dict[str, Any]:
        config = self._read_config()
        if config["provider"] == LLM_PROVIDER_OPENAI_CHAT:
            openai_chat = config["openai_chat"]
            missing = [
                label
                for label, value in (
                    ("baseurl", openai_chat["base_url"]),
                    ("api key", openai_chat["api_key"]),
                    ("model", openai_chat["model"]),
                )
                if not value
            ]
            return {
                "ok": not missing,
                **self.describe(),
                "config": self.public_config(),
                "error": f"接口配置缺少：{'、'.join(missing)}。" if missing else "",
            }

        status = await self.vibearound.status()
        return {
            **status,
            "provider": LLM_PROVIDER_VIBEAROUND,
            "provider_label": status.get("provider_label") or self.vibearound.provider_label(),
            "config": self.public_config(),
        }

    async def chat(self, messages: List[Dict[str, str]], timeout: float = 90.0) -> str:
        if self.active_provider() == LLM_PROVIDER_OPENAI_CHAT:
            return await self._openai_client().chat(messages, timeout)
        return await self.vibearound.chat(messages, timeout)

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        timeout: float = 90.0,
    ) -> AsyncIterator[str]:
        if self.active_provider() == LLM_PROVIDER_OPENAI_CHAT:
            async for chunk in self._openai_client().chat_stream(messages, timeout):
                yield chunk
            return
        async for chunk in self.vibearound.chat_stream(messages, timeout):
            yield chunk
