from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .config import DATA_DIR


PROMPT_CONFIG_PATH = DATA_DIR / "prompt_config.json"


class PromptConfigStore:
    def __init__(self, defaults: Dict[str, str], meta: List[Dict[str, str]], path: Path = PROMPT_CONFIG_PATH) -> None:
        self.defaults = defaults
        self.meta = meta
        self.path = path

    def _read(self) -> Dict[str, str]:
        try:
            raw = json.loads(self.path.read_text())
        except Exception:
            raw = {}
        values = raw.get("prompts") if isinstance(raw, dict) else raw
        if not isinstance(values, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in values.items()
            if key in self.defaults and isinstance(value, str)
        }

    def _write(self, prompts: Dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"prompts": prompts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self.path.chmod(0o600)
        except Exception:
            pass

    def get(self, key: str, fallback: str = "") -> str:
        value = self._read().get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        default = self.defaults.get(key)
        return default if isinstance(default, str) else fallback

    def public_config(self) -> Dict[str, Any]:
        saved = self._read()
        prompts = []
        for item in self.meta:
            key = item["key"]
            default = self.defaults.get(key, "")
            value = saved.get(key, default)
            prompts.append({
                **item,
                "default": default,
                "value": value,
                "customized": key in saved and saved[key].strip() != default.strip(),
            })
        return {"prompts": prompts}

    def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw_prompts = payload.get("prompts") if isinstance(payload, dict) else {}
        if not isinstance(raw_prompts, dict):
            raw_prompts = {}
        current = self._read()
        next_prompts = dict(current)
        for key, value in raw_prompts.items():
            clean_key = str(key)
            if clean_key not in self.defaults:
                continue
            text = str(value or "").strip()
            default = self.defaults[clean_key].strip()
            if not text or text == default:
                next_prompts.pop(clean_key, None)
            else:
                next_prompts[clean_key] = text
        self._write(next_prompts)
        return self.public_config()
