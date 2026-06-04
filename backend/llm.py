from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

try:
    from litellm import acompletion
except Exception:  # pragma: no cover - handled at runtime for packaged installs
    acompletion = None

from .config import DATA_DIR
from .vibearound import VibeAroundClient


LLM_CONFIG_PATH = DATA_DIR / "llm_config.json"
LLM_PROVIDER_VIBEAROUND = "vibearound"
LLM_PROVIDER_LITELLM = "litellm"


class LLMConfigError(ValueError):
    pass


def _llm_error_payload_detail(value: str) -> str:
    text = value.strip()
    parsed: Any = None
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
            break
        except Exception:
            parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error") or parsed.get("message") or parsed.get("detail")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or error.get("code")
            if message:
                return str(message)
        if error:
            return str(error)
    return text


def friendly_llm_error(exc: BaseException) -> str:
    raw = str(exc).strip() or exc.__class__.__name__
    status_match = re.search(r"Error code:\s*(\d+)\s*-\s*(.+)$", raw, flags=re.DOTALL)
    if status_match:
        status = status_match.group(1)
        detail = _llm_error_payload_detail(status_match.group(2))
        return f"模型接口返回 {status}：{detail}"
    if "OpenAIException -" in raw:
        raw = raw.split("OpenAIException -", 1)[1].strip()
    raw = re.sub(r"^(?:litellm\.)?[A-Za-z_]+(?:Error|Exception):\s*", "", raw)
    if raw == "LLM Provider NOT provided.":
        return "没有识别到模型接口类型，请检查模型接口设置。"
    return raw


def _default_config() -> Dict[str, Any]:
    return {
        "provider": LLM_PROVIDER_LITELLM,
        "litellm": {
            "preset": "openai",
            "api_base": "",
            "api_key": "",
            "model": "",
        },
    }


def _clean_provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    if provider in {LLM_PROVIDER_VIBEAROUND, LLM_PROVIDER_LITELLM}:
        return provider
    raise LLMConfigError("LLM 配置解析失败：provider 字段不是当前版本支持的值，请在设置里重新保存。")


def _clean_litellm(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise LLMConfigError("LLM 配置解析失败：模型接口配置必须是对象。")
    return {
        "preset": str(value.get("preset") or "custom").strip(),
        "api_base": str(value.get("api_base") or value.get("apiBase") or "").strip(),
        "api_key": str(value.get("api_key") or value.get("apiKey") or "").strip(),
        "model": str(value.get("model") or "").strip(),
    }


def _merge_config(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise LLMConfigError("LLM 配置解析失败：根节点必须是对象。")
    provider = _clean_provider(raw.get("provider"))
    if "litellm" not in raw:
        raise LLMConfigError("LLM 配置解析失败：缺少模型接口配置。")
    litellm_config = _clean_litellm(raw.get("litellm"))
    if provider == LLM_PROVIDER_LITELLM and not litellm_config["model"]:
        raise LLMConfigError("LLM 配置解析失败：模型接口的模型名不能为空。")
    return {
        "provider": provider,
        "litellm": litellm_config,
    }


class LiteLLMClient:
    def __init__(self, model: str, api_key: str = "", api_base: str = "") -> None:
        self.model = model
        self.api_key = api_key
        self.api_base = self._normalize_api_base(api_base)

    def _normalize_api_base(self, value: str) -> str:
        api_base = value.strip().rstrip("/")
        if api_base.endswith("/chat/completions"):
            api_base = api_base[: -len("/chat/completions")]
        return api_base

    def _completion_kwargs(
        self,
        messages: List[Dict[str, str]],
        timeout: float,
        stream: bool,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "timeout": timeout,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["base_url"] = self.api_base
            if "/" not in self.model:
                kwargs["custom_llm_provider"] = "openai"
        return kwargs

    def _value(self, item: Any, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    def _first_choice(self, data: Any) -> Any:
        choices = self._value(data, "choices") or []
        if not choices:
            return None
        return choices[0]

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
        if acompletion is None:
            raise RuntimeError("模型接口依赖未安装，请重新安装应用依赖。")
        response = await acompletion(**self._completion_kwargs(messages, timeout, stream=False))
        choice = self._first_choice(response)
        if choice is None:
            raise RuntimeError("模型接口没有返回 choices。")
        message = self._value(choice, "message") or {}
        content = self._content_to_text(self._value(message, "content"))
        if content:
            return content
        raise RuntimeError("模型接口没有返回可用内容。")

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        timeout: float = 90.0,
    ) -> AsyncIterator[str]:
        if acompletion is None:
            raise RuntimeError("模型接口依赖未安装，请重新安装应用依赖。")
        response = await acompletion(**self._completion_kwargs(messages, timeout, stream=True))
        async for event in response:
            choice = self._first_choice(event)
            if choice is None:
                continue
            delta = self._value(choice, "delta") or {}
            content = self._content_to_text(self._value(delta, "content"))
            if content:
                yield content


class LLMManager:
    def __init__(self, vibearound: VibeAroundClient, config_path: Path = LLM_CONFIG_PATH) -> None:
        self.vibearound = vibearound
        self.config_path = config_path

    def _read_config(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self.config_path.read_text())
        except FileNotFoundError:
            return _default_config()
        except json.JSONDecodeError as exc:
            raise LLMConfigError(f"LLM 配置解析失败：JSON 格式错误（第 {exc.lineno} 行）。") from exc
        except Exception:
            raise LLMConfigError("LLM 配置解析失败：无法读取配置文件。")
        return _merge_config(raw)

    def _read_config_or_default(self) -> tuple[Dict[str, Any], str]:
        try:
            return self._read_config(), ""
        except LLMConfigError as exc:
            return _default_config(), str(exc)

    def _write_config(self, config: Dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
        try:
            self.config_path.chmod(0o600)
        except Exception:
            pass

    def public_config(self) -> Dict[str, Any]:
        config, error = self._read_config_or_default()
        litellm_config = config["litellm"]
        return {
            "provider": config["provider"],
            "litellm": {
                "preset": litellm_config["preset"],
                "api_base": litellm_config["api_base"],
                "model": litellm_config["model"],
                "has_api_key": bool(litellm_config["api_key"]),
            },
            "config_error": error,
        }

    def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current, _ = self._read_config_or_default()
        provider = _clean_provider(payload.get("provider"))
        incoming_litellm = _clean_litellm(payload.get("litellm"))
        current_litellm = current["litellm"]
        litellm_config = {
            "preset": incoming_litellm["preset"] or current_litellm["preset"],
            "api_base": incoming_litellm["api_base"],
            "api_key": incoming_litellm["api_key"] or current_litellm["api_key"],
            "model": incoming_litellm["model"],
        }

        if provider == LLM_PROVIDER_LITELLM and not litellm_config["model"]:
            raise ValueError("模型接口的模型名不能为空。")

        config = {
            "provider": provider,
            "litellm": litellm_config,
        }
        self._write_config(config)
        return self.public_config()

    def _litellm_client(self) -> LiteLLMClient:
        config = self._read_config()["litellm"]
        if not config["model"]:
            raise RuntimeError("模型接口的模型名不能为空。")
        return LiteLLMClient(config["model"], config["api_key"], config["api_base"])

    def active_provider(self) -> str:
        return self._read_config()["provider"]

    def describe(self) -> Dict[str, Any]:
        config, error = self._read_config_or_default()
        if error:
            return {
                "provider": "",
                "provider_label": "LLM 模型接口",
                "target_api_type": "litellm",
                "model": "",
                "route": "direct.litellm",
                "transport": "litellm",
                "error": error,
            }
        if config["provider"] == LLM_PROVIDER_LITELLM:
            litellm_config = config["litellm"]
            return {
                "provider": LLM_PROVIDER_LITELLM,
                "provider_label": "LLM 模型接口",
                "target_api_type": "litellm",
                "model": litellm_config["model"],
                "route": "direct.litellm",
                "transport": "litellm",
                "preset": litellm_config["preset"],
                "api_base": litellm_config["api_base"],
                "has_api_key": bool(litellm_config["api_key"]),
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
        config, error = self._read_config_or_default()
        if error:
            return {
                "ok": False,
                **self.describe(),
                "config": self.public_config(),
                "error": error,
            }
        if config["provider"] == LLM_PROVIDER_LITELLM:
            litellm_config = config["litellm"]
            missing = [] if litellm_config["model"] else ["model"]
            return {
                "ok": not missing,
                **self.describe(),
                "config": self.public_config(),
                "error": f"模型接口配置缺少：{'、'.join(missing)}。" if missing else "",
            }

        status = await self.vibearound.status()
        return {
            **status,
            "provider": LLM_PROVIDER_VIBEAROUND,
            "provider_label": status.get("provider_label") or self.vibearound.provider_label(),
            "config": self.public_config(),
        }

    async def chat(self, messages: List[Dict[str, str]], timeout: float = 90.0) -> str:
        if self.active_provider() == LLM_PROVIDER_LITELLM:
            return await self._litellm_client().chat(messages, timeout)
        return await self.vibearound.chat(messages, timeout)

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        timeout: float = 90.0,
    ) -> AsyncIterator[str]:
        if self.active_provider() == LLM_PROVIDER_LITELLM:
            async for chunk in self._litellm_client().chat_stream(messages, timeout):
                yield chunk
            return
        async for chunk in self.vibearound.chat_stream(messages, timeout):
            yield chunk
