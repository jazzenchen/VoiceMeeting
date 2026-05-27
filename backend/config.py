from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


PROJECT_DIR = Path(__file__).resolve().parents[1]


def load_project_env() -> None:
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text().splitlines()
    except Exception:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


load_project_env()

DATA_DIR = Path(os.environ.get("VOICE_MEETING_DATA_DIR", PROJECT_DIR / "data"))
CHUNKS_DIR = DATA_DIR / "chunks"
MEETINGS_DIR = DATA_DIR / "meetings"
MODELS_DIR = Path(os.environ.get("VOICE_MEETING_MODELS_DIR", PROJECT_DIR / "models"))
DB_PATH = Path(os.environ.get("VOICE_MEETING_DB", DATA_DIR / "voice_meeting.sqlite3"))

ASR_MODEL = os.environ.get("VOICE_MEETING_ASR_MODEL", "small")
ASR_MODEL_DIR = Path(os.environ.get("VOICE_MEETING_ASR_MODEL_DIR", MODELS_DIR / "faster-whisper"))
MLX_ASR_MODEL_DIR = Path(os.environ.get("VOICE_MEETING_MLX_ASR_MODEL_DIR", MODELS_DIR / "mlx-whisper"))
ASR_DEVICE = os.environ.get("VOICE_MEETING_ASR_DEVICE", "cpu")
ASR_COMPUTE_TYPE = os.environ.get("VOICE_MEETING_ASR_COMPUTE_TYPE", "int8")
ASR_LANGUAGE = os.environ.get("VOICE_MEETING_ASR_LANGUAGE") or None
ALLOW_MODEL_DOWNLOAD = os.environ.get("VOICE_MEETING_ALLOW_MODEL_DOWNLOAD", "0").strip() in {"1", "true", "yes"}

SPEAKER_TRACKING_ENABLED = os.environ.get("VOICE_MEETING_SPEAKER_TRACKING", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
SPEAKER_TRACKING_BACKEND = os.environ.get("VOICE_MEETING_SPEAKER_BACKEND", "resemblyzer")
SPEAKER_TRACKING_DEVICE = os.environ.get("VOICE_MEETING_SPEAKER_DEVICE", "cpu")
SPEAKER_MATCH_THRESHOLD = float(os.environ.get("VOICE_MEETING_SPEAKER_THRESHOLD", "0.74"))
SPEAKER_CONTINUITY_THRESHOLD = float(os.environ.get("VOICE_MEETING_SPEAKER_CONTINUITY_THRESHOLD", "0.64"))
SPEAKER_MIN_AUDIO_MS = int(os.environ.get("VOICE_MEETING_SPEAKER_MIN_AUDIO_MS", "850"))
SPEAKER_MAX_AUDIO_MS = int(os.environ.get("VOICE_MEETING_SPEAKER_MAX_AUDIO_MS", "12000"))

VIBEAROUND_BASE_URL = os.environ.get("VIBEAROUND_BASE_URL", "http://127.0.0.1:12358")
VIBEAROUND_TRANSPORT = os.environ.get("VIBEAROUND_TRANSPORT", "web-chat")
VIBEAROUND_AGENT = os.environ.get("VIBEAROUND_AGENT", "codex")
VIBEAROUND_WORKSPACE = os.environ.get("VIBEAROUND_WORKSPACE", str(PROJECT_DIR))
VIBEAROUND_ENABLE_BRIDGE_FALLBACK = os.environ.get("VIBEAROUND_ENABLE_BRIDGE_FALLBACK", "0").strip() in {
    "1",
    "true",
    "yes",
}
VIBEAROUND_TARGET_API_TYPE = os.environ.get("VIBEAROUND_TARGET_API_TYPE", "openai-chat")
VIBEAROUND_SCOPE = os.environ.get("VIBEAROUND_SCOPE", "voice-meeting-openai-chat")
VIBEAROUND_MODEL = os.environ.get("VIBEAROUND_MODEL")


def _home_file(*parts: str) -> Path:
    return Path.home().joinpath(*parts)


def read_vibearound_token() -> Optional[str]:
    env_token = os.environ.get("VIBEAROUND_TOKEN")
    if env_token:
        return env_token

    auth_path = _home_file(".vibearound", "auth.json")
    if not auth_path.exists():
        return None

    try:
        data = json.loads(auth_path.read_text())
    except Exception:
        return None

    candidates = [
        "token",
        "auth_token",
        "authToken",
        "access_token",
        "accessToken",
        "server_token",
        "serverToken",
    ]
    for key in candidates:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value

    def find_token(node: Any) -> Optional[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                if "token" in str(key).lower() and isinstance(value, str) and value:
                    return value
                nested = find_token(value)
                if nested:
                    return nested
        elif isinstance(node, list):
            for item in node:
                nested = find_token(item)
                if nested:
                    return nested
        return None

    return find_token(data)


def detect_vibearound_profile_id() -> Optional[str]:
    env_profile = os.environ.get("VIBEAROUND_PROFILE_ID")
    if env_profile:
        return env_profile

    agents_path = _home_file(".vibearound", "agents.json")
    if not agents_path.exists():
        return None

    try:
        data = json.loads(agents_path.read_text())
    except Exception:
        return None

    preferred_names = []
    for key in ("selected", "selectedAgent", "selected_agent", "activeAgent", "active_agent"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str):
            preferred_names.append(value)

    agents = data.get("agents") if isinstance(data, dict) else data
    if isinstance(agents, dict):
        items = []
        for key, value in agents.items():
            if isinstance(value, dict):
                enriched = dict(value)
                enriched.setdefault("id", key)
                items.append(enriched)
        agents = items

    if not isinstance(agents, list):
        return None

    def profile_from_agent(agent: Any) -> Optional[str]:
        if not isinstance(agent, dict):
            return None
        for key in ("profileId", "profile_id", "profile", "connectionProfileId"):
            value = agent.get(key)
            if isinstance(value, str) and value:
                return value
        connection = agent.get("connection")
        if isinstance(connection, dict):
            for key in ("profileId", "profile_id", "profile"):
                value = connection.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    for name in preferred_names:
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            if name in {agent.get("id"), agent.get("name"), agent.get("agentId")}:
                profile = profile_from_agent(agent)
                if profile:
                    return profile

    for agent in agents:
        profile = profile_from_agent(agent)
        if profile:
            return profile

    return None


def detect_vibearound_model(profile_id: Optional[str], target_api_type: str) -> Optional[str]:
    if VIBEAROUND_MODEL:
        return VIBEAROUND_MODEL
    if not profile_id:
        return None

    profile_path = _home_file(".vibearound", "profiles", f"{profile_id}.json")
    if not profile_path.exists():
        return None

    try:
        data = json.loads(profile_path.read_text())
    except Exception:
        return None

    overrides = data.get("overrides") if isinstance(data, dict) else None
    target_overrides = overrides.get(target_api_type) if isinstance(overrides, dict) else None
    if isinstance(target_overrides, dict):
        model = target_overrides.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    MEETINGS_DIR.mkdir(parents=True, exist_ok=True)
    ASR_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    MLX_ASR_MODEL_DIR.mkdir(parents=True, exist_ok=True)
