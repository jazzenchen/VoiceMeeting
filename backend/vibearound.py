from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
import websockets

from .config import (
    VIBEAROUND_AGENT,
    VIBEAROUND_BASE_URL,
    VIBEAROUND_ENABLE_BRIDGE_FALLBACK,
    VIBEAROUND_MODEL,
    VIBEAROUND_SCOPE,
    VIBEAROUND_TARGET_API_TYPE,
    VIBEAROUND_TRANSPORT,
    VIBEAROUND_WORKSPACE,
    detect_vibearound_model,
    detect_vibearound_profile_id,
    read_vibearound_token,
)


class VibeAroundClient:
    def __init__(
        self,
        base_url: str = VIBEAROUND_BASE_URL,
        profile_id: Optional[str] = None,
        target_api_type: str = VIBEAROUND_TARGET_API_TYPE,
        model: Optional[str] = VIBEAROUND_MODEL,
        scope: str = VIBEAROUND_SCOPE,
        token: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = VIBEAROUND_TRANSPORT.strip() or "web-chat"
        self.agent = VIBEAROUND_AGENT.strip() or "codex"
        self.profile_id = profile_id or (
            detect_vibearound_profile_id() if self.transport == "local-api" else None
        )
        self.target_api_type = target_api_type
        self.scope = scope
        self._model_override = model
        self.model = (
            model
            or detect_vibearound_model(self.profile_id, target_api_type)
            or ("Codex CLI" if self.transport == "web-chat" else "gpt-5.5")
        )
        self.token = token if token is not None else read_vibearound_token()
        self.route = "web-chat.codex" if self.transport == "web-chat" else "directs.codex"
        self.workspace = VIBEAROUND_WORKSPACE
        self._web_session_id: Optional[str] = None
        preferred = self._codex_bridge_candidates() if self.transport == "local-api" else []
        if self.transport == "local-api" and preferred:
            self.profile_id = preferred[0]["profile_id"]
            self.target_api_type = preferred[0]["target_api_type"]
            self.model = preferred[0]["model"]
            self.route = preferred[0]["route"]

    def _with_token(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        if not self.token:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urlencode({'token': self.token})}"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _public_headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json"}

    async def status(self) -> Dict[str, Any]:
        if not self.profile_id and self.transport != "web-chat":
            return {
                "ok": False,
                "base_url": self.base_url,
                "profile_id": None,
                "error": "VibeAround profile id was not detected. Set VIBEAROUND_PROFILE_ID.",
            }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    self._with_token("/va/api/agents"),
                    headers=self._headers(),
                    follow_redirects=False,
                )
            return {
                "ok": response.status_code < 400,
                "base_url": self.base_url,
                "profile_id": self.profile_id,
                "target_api_type": self.target_api_type,
                "scope": self.scope,
                "model": self.model,
                "agent": self.agent,
                "route": self.route,
                "transport": self.transport,
                "provider_label": self.provider_label(),
                "session_id": self._web_session_id,
                "status_code": response.status_code,
            }
        except Exception as exc:
            return {
                "ok": False,
                "base_url": self.base_url,
                "profile_id": self.profile_id,
                "target_api_type": self.target_api_type,
                "scope": self.scope,
                "model": self.model,
                "agent": self.agent,
                "route": self.route,
                "transport": self.transport,
                "provider_label": self.provider_label(),
                "session_id": self._web_session_id,
                "error": str(exc),
            }

    def provider_label(self) -> str:
        if self.transport == "web-chat" and not self.profile_id:
            return "Codex CLI"
        return self.profile_id or "VibeAround"

    def _websocket_url(self, path: str) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, path, "", "", ""))

    def _message_text(self, messages: List[Dict[str, str]]) -> str:
        blocks = [
            "你现在只作为会议纪要生成器运行。",
            "不要调用任何工具，不要读写文件，不要检查本地工程；只根据下面给出的文本直接回答。",
            "如果要求 JSON，就只输出 JSON；如果要求 Markdown，就只输出 Markdown。",
        ]
        for message in messages:
            role = message.get("role") or "user"
            content = message.get("content") or ""
            if not content.strip():
                continue
            label = {"system": "系统指令", "user": "用户输入", "assistant": "已有回复"}.get(role, role)
            blocks.append(f"\n[{label}]\n{content.strip()}")
        return "\n".join(blocks).strip()

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

    def _websocket_payload(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        message_id = str(uuid.uuid4())
        payload: Dict[str, Any] = {
            "type": "message",
            "messageId": message_id,
            "text": self._message_text(messages),
            "agent": self.agent,
            "sessionWorkspace": self.workspace,
        }
        if self.profile_id:
            payload["profileId"] = self.profile_id
        if self._web_session_id:
            payload["sessionAction"] = "resume"
            payload["sessionId"] = self._web_session_id
        else:
            payload["sessionAction"] = "new"
        return payload

    async def _chat_via_websocket_stream(
        self,
        messages: List[Dict[str, str]],
        timeout: float,
    ) -> AsyncIterator[str]:
        url = self._websocket_url("/va/ws/chat")
        payload = self._websocket_payload(messages)
        system_errors: List[str] = []
        emitted = False
        deadline = time.monotonic() + timeout
        async with websockets.connect(url, ping_interval=None, open_timeout=10) as ws:
            try:
                await asyncio.wait_for(
                    ws.recv(), min(10.0, max(1.0, deadline - time.monotonic()))
                )
            except Exception:
                pass
            await ws.send(json.dumps(payload, ensure_ascii=False))

            while time.monotonic() < deadline:
                remaining = max(1.0, deadline - time.monotonic())
                raw = await asyncio.wait_for(ws.recv(), remaining)
                event = json.loads(raw)
                kind = event.get("kind")
                if kind == "session_ready":
                    session_id = event.get("session_id")
                    if isinstance(session_id, str) and session_id:
                        self._web_session_id = session_id
                    continue
                if kind == "system_text":
                    text = str(event.get("text") or "")
                    if text.startswith("❌") or "Internal error" in text:
                        system_errors.append(text)
                    continue
                if kind == "error":
                    raise RuntimeError(str(event.get("error") or "VibeAround web-chat error"))
                if kind == "permission_request":
                    request_id = event.get("request_id")
                    if request_id:
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "permission_response",
                                    "requestId": request_id,
                                    "outcome": "cancelled",
                                }
                            )
                        )
                    continue
                if kind == "acp_notification":
                    payload_event = event.get("payload") or {}
                    session_id = payload_event.get("sessionId")
                    if isinstance(session_id, str) and session_id:
                        self._web_session_id = session_id
                    update = payload_event.get("update") or {}
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        chunk = self._content_to_text(update.get("content"))
                        if chunk:
                            emitted = True
                            self.transport = "web-chat"
                            self.route = "web-chat.codex"
                            self.model = self.model or "Codex CLI"
                            yield chunk
                    continue
                if kind == "prompt_done":
                    break

        if not emitted:
            if system_errors:
                raise RuntimeError(system_errors[-1])
            raise RuntimeError("VibeAround web-chat returned no assistant text.")

    async def _chat_via_websocket(self, messages: List[Dict[str, str]], timeout: float) -> str:
        text_parts: List[str] = []
        async for chunk in self._chat_via_websocket_stream(messages, timeout):
            text_parts.append(chunk)
        result = "".join(text_parts).strip()
        if result:
            return result
        raise RuntimeError("VibeAround web-chat returned no assistant text.")

    def _profile_path(self, profile_id: str) -> Path:
        return Path.home() / ".vibearound" / "profiles" / f"{profile_id}.json"

    def _read_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self._profile_path(profile_id).read_text())
        except Exception:
            return None

    def _profile_provider(self, profile_id: str) -> str:
        profile = self._read_profile(profile_id) or {}
        return str(profile.get("provider") or "").lower()

    def _allow_profile(self, profile_id: str) -> bool:
        blocked_path = Path.home() / ".vibearound" / ".voice-meeting-blocked-providers"
        blocked_source = blocked_path.read_text().splitlines() if blocked_path.exists() else ["deepseek"]
        blocked = {item.strip().lower() for item in blocked_source if item.strip()}
        provider = self._profile_provider(profile_id)
        return provider not in blocked

    def _connection_sources(self) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []
        for path in (
            Path.home() / ".vibearound" / "agents.json",
            Path.home() / ".vibearound" / "launcher.json",
        ):
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            if isinstance(data, dict):
                sources.append(data)
        return sources

    def _add_candidate(
        self,
        candidates: List[Dict[str, str]],
        profile_id: Optional[str],
        target_api_type: Optional[str],
        model: Optional[str],
        route: str,
    ) -> None:
        if not profile_id or not target_api_type or not self._allow_profile(profile_id):
            return
        profile = self._read_profile(profile_id)
        api_types = profile.get("api_types") if isinstance(profile, dict) else []
        if isinstance(api_types, list) and target_api_type not in api_types:
            return
        resolved_model = model or self._model_for(profile_id, target_api_type)
        key = (profile_id, target_api_type, resolved_model)
        for candidate in candidates:
            if (candidate["profile_id"], candidate["target_api_type"], candidate["model"]) == key:
                return
        candidates.append(
            {
                "profile_id": profile_id,
                "target_api_type": target_api_type,
                "model": resolved_model,
                "route": route,
            }
        )

    def _codex_bridge_candidates(self) -> List[Dict[str, str]]:
        candidates: List[Dict[str, str]] = []
        for source in self._connection_sources():
            connections = source.get("profileConnections") or source.get("profile_connections") or {}
            if not isinstance(connections, dict):
                continue
            for profile_id, agents in connections.items():
                if not isinstance(agents, dict):
                    continue
                codex = agents.get("codex") or {}
                if not isinstance(codex, dict):
                    continue

                bridge = codex.get("bridge")
                if isinstance(bridge, dict):
                    selected = codex.get("selectedApiType") or codex.get("selected_api_type")
                    ordered_keys = [selected] + [key for key in bridge.keys() if key != selected]
                    for api_type in ordered_keys:
                        config = bridge.get(api_type) if api_type else None
                        if not isinstance(config, dict) or config.get("enabled") is False:
                            continue
                        self._add_candidate(
                            candidates,
                            str(profile_id),
                            config.get("targetApiType") or config.get("target_api_type") or api_type,
                            config.get("upstreamModel") or config.get("upstream_model"),
                            "directs.codex",
                        )

                if codex.get("bridgeEnabled") is True:
                    self._add_candidate(
                        candidates,
                        str(profile_id),
                        codex.get("targetApiType") or codex.get("target_api_type"),
                        None,
                        "directs.codex",
                    )
        return candidates

    def _candidate_order(self) -> List[str]:
        ordered: List[str] = []

        def add(profile_id: Optional[str]) -> None:
            if profile_id and profile_id not in ordered:
                ordered.append(profile_id)

        add(self.profile_id)

        agents_path = Path.home() / ".vibearound" / "agents.json"
        try:
            agents_data = json.loads(agents_path.read_text())
        except Exception:
            agents_data = {}
        agents = agents_data.get("agents") if isinstance(agents_data, dict) else {}
        for key in ("defaultAgent", "selectedAgent"):
            agent_id = agents_data.get(key) if isinstance(agents_data, dict) else None
            if isinstance(agents, dict) and isinstance(agent_id, str):
                agent = agents.get(agent_id) or {}
                if isinstance(agent, dict):
                    add(agent.get("profileId") or agent.get("profile_id"))

        settings_path = Path.home() / ".vibearound" / "settings.json"
        try:
            settings = json.loads(settings_path.read_text())
        except Exception:
            settings = {}
        profile_order = settings.get("profile_order") if isinstance(settings, dict) else []
        if isinstance(profile_order, list):
            for profile_id in profile_order:
                if isinstance(profile_id, str):
                    add(profile_id)

        profiles_dir = Path.home() / ".vibearound" / "profiles"
        for profile_file in sorted(profiles_dir.glob("*.json")):
            add(profile_file.stem)

        return ordered

    def _target_order(self, profile: Dict[str, Any]) -> List[str]:
        api_types = profile.get("api_types") or []
        ordered = []
        for target in (self.target_api_type, "openai-chat", "openai-responses", "anthropic"):
            if target in api_types and target not in ordered:
                ordered.append(target)
        return ordered

    def _model_for(self, profile_id: str, target_api_type: str) -> str:
        if self._model_override:
            return self._model_override
        return detect_vibearound_model(profile_id, target_api_type) or self.model or "gpt-5.5"

    def _chat_candidates(self) -> List[Dict[str, str]]:
        candidates: List[Dict[str, str]] = []
        for profile_id in self._candidate_order():
            if not self._allow_profile(profile_id):
                continue
            profile = self._read_profile(profile_id)
            if not profile:
                continue
            for target_api_type in self._target_order(profile):
                candidates.append(
                    {
                        "profile_id": profile_id,
                        "target_api_type": target_api_type,
                        "model": self._model_for(profile_id, target_api_type),
                        "route": "local-api",
                    }
                )
        return candidates

    async def _chat_once(
        self,
        candidate: Dict[str, str],
        messages: List[Dict[str, str]],
        timeout: float,
    ) -> str:
        profile_id = candidate["profile_id"]
        target_api_type = candidate["target_api_type"]
        model = candidate["model"]
        path = (
            f"/va/local-api/{profile_id}/{self.scope}/"
            f"{target_api_type}/v1/chat/completions"
        )
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}{path}",
                headers=self._public_headers(),
                content=json.dumps(payload, ensure_ascii=False),
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("VibeAround bridge returned no choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
        return str(content or "")

    async def _chat_once_stream(
        self,
        candidate: Dict[str, str],
        messages: List[Dict[str, str]],
        timeout: float,
    ) -> AsyncIterator[str]:
        profile_id = candidate["profile_id"]
        target_api_type = candidate["target_api_type"]
        model = candidate["model"]
        path = (
            f"/va/local-api/{profile_id}/{self.scope}/"
            f"{target_api_type}/v1/chat/completions"
        )
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}{path}",
                headers=self._public_headers(),
                content=json.dumps(payload, ensure_ascii=False),
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    raw = await response.aread()
                    data = json.loads(raw.decode("utf-8"))
                    choices = data.get("choices") or []
                    if not choices:
                        raise RuntimeError("VibeAround bridge returned no choices.")
                    content = ((choices[0].get("message") or {}).get("content") or "")
                    if isinstance(content, str) and content:
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
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield content

    async def chat(self, messages: List[Dict[str, str]], timeout: float = 90.0) -> str:
        if self.transport == "web-chat":
            try:
                return await self._chat_via_websocket(messages, timeout)
            except Exception:
                if not VIBEAROUND_ENABLE_BRIDGE_FALLBACK:
                    raise

        if not self.profile_id:
            raise RuntimeError("VibeAround profile id was not detected.")

        errors = []
        candidates = self._codex_bridge_candidates() + self._chat_candidates()
        for candidate in candidates:
            try:
                text = await self._chat_once(candidate, messages, timeout)
                self.profile_id = candidate["profile_id"]
                self.target_api_type = candidate["target_api_type"]
                self.model = candidate["model"]
                self.route = candidate.get("route") or "local-api"
                return text
            except httpx.HTTPStatusError as exc:
                errors.append(
                    f"{candidate['profile_id']}/{candidate['target_api_type']}: "
                    f"HTTP {exc.response.status_code}"
                )
            except Exception as exc:
                errors.append(
                    f"{candidate['profile_id']}/{candidate['target_api_type']}: "
                    f"{type(exc).__name__}"
                )
        detail = "; ".join(errors[-6:]) if errors else "no local profiles found"
        raise RuntimeError(f"No usable VibeAround bridge candidate: {detail}")

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        timeout: float = 90.0,
    ) -> AsyncIterator[str]:
        if self.transport == "web-chat":
            try:
                async for chunk in self._chat_via_websocket_stream(messages, timeout):
                    yield chunk
                return
            except Exception:
                if not VIBEAROUND_ENABLE_BRIDGE_FALLBACK:
                    raise

        if not self.profile_id:
            raise RuntimeError("VibeAround profile id was not detected.")

        errors = []
        candidates = self._codex_bridge_candidates() + self._chat_candidates()
        for candidate in candidates:
            try:
                emitted = False
                async for chunk in self._chat_once_stream(candidate, messages, timeout):
                    emitted = True
                    self.profile_id = candidate["profile_id"]
                    self.target_api_type = candidate["target_api_type"]
                    self.model = candidate["model"]
                    self.route = candidate.get("route") or "local-api"
                    yield chunk
                if emitted:
                    return
            except httpx.HTTPStatusError as exc:
                errors.append(
                    f"{candidate['profile_id']}/{candidate['target_api_type']}: "
                    f"HTTP {exc.response.status_code}"
                )
            except Exception as exc:
                errors.append(
                    f"{candidate['profile_id']}/{candidate['target_api_type']}: "
                    f"{type(exc).__name__}"
                )
        detail = "; ".join(errors[-6:]) if errors else "no local profiles found"
        raise RuntimeError(f"No usable VibeAround bridge candidate: {detail}")
