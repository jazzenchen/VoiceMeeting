from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any, Dict

from .config import ASR_MODEL, DATA_DIR


RECORDING_CONFIG_PATH = DATA_DIR / "recording_config.json"

DEFAULT_RECORDING_CONFIG: Dict[str, Any] = {
    "language": "mixed",
    "asr_model": "mlx-small" if platform.system() == "Darwin" else ASR_MODEL,
    "speaker_mode": "voiceprint",
    "max_segment_ms": 15000,
    "input_gain": 1.0,
}

SUPPORTED_LANGUAGES = {
    "mixed",
    "auto",
    "multilingual",
    "zh",
    "en",
    "ja",
    "ko",
    "fr",
    "de",
    "es",
    "ru",
    "pt",
}
SUPPORTED_SPEAKER_MODES = {"voiceprint", "diarization", "off", "auto"}


def clamp_recording_config(value: Dict[str, Any]) -> Dict[str, Any]:
    raw = {**DEFAULT_RECORDING_CONFIG, **(value or {})}
    language = str(raw.get("language") or DEFAULT_RECORDING_CONFIG["language"]).strip().lower()
    speaker_mode = str(raw.get("speaker_mode") or DEFAULT_RECORDING_CONFIG["speaker_mode"]).strip().lower()
    asr_model = str(raw.get("asr_model") or DEFAULT_RECORDING_CONFIG["asr_model"]).strip()
    try:
        max_segment_ms = int(round(float(raw.get("max_segment_ms"))))
    except Exception:
        max_segment_ms = int(DEFAULT_RECORDING_CONFIG["max_segment_ms"])
    try:
        input_gain = float(raw.get("input_gain"))
    except Exception:
        input_gain = float(DEFAULT_RECORDING_CONFIG["input_gain"])
    return {
        "language": language if language in SUPPORTED_LANGUAGES else DEFAULT_RECORDING_CONFIG["language"],
        "asr_model": asr_model or DEFAULT_RECORDING_CONFIG["asr_model"],
        "speaker_mode": speaker_mode if speaker_mode in SUPPORTED_SPEAKER_MODES else DEFAULT_RECORDING_CONFIG["speaker_mode"],
        "max_segment_ms": min(30000, max(8000, max_segment_ms)),
        "input_gain": min(2.5, max(0.8, input_gain)),
    }


class RecordingConfigStore:
    def __init__(self, path: Path = RECORDING_CONFIG_PATH) -> None:
        self.path = path

    def read(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        return clamp_recording_config(raw)

    def save(self, value: Dict[str, Any]) -> Dict[str, Any]:
        config = clamp_recording_config(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self.path.chmod(0o600)
        except Exception:
            pass
        return config
