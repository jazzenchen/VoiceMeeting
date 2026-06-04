from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from .asr import ASRUnavailable, FasterWhisperASR, FunASRASR, MlxWhisperASR
from .config import ASR_MODEL, ASR_MODEL_DIR
from .model_registry import (
    ASR_MODEL_CATALOG,
    FUNASR_MODEL_CATALOG,
    MLX_ASR_MODEL_CATALOG,
    SUPPORTED_ASR_MODELS,
    asr_base_model_name,
    asr_model_backend,
    asr_model_repos,
    asr_repo_cache_path,
    discovered_asr_models,
    mark_funasr_model_installed,
)


class ASRRuntime:
    def __init__(self, default_engine: Optional[FasterWhisperASR] = None) -> None:
        self.default_model = ASR_MODEL
        self.default_engine = default_engine or FasterWhisperASR()
        self.engines: Dict[str, FasterWhisperASR] = {}
        self.load_lock = threading.Lock()

    @property
    def audio_converter(self) -> FasterWhisperASR:
        return self.default_engine

    def model_label(self, model_name: str) -> str:
        catalogs = {
            "mlx": MLX_ASR_MODEL_CATALOG,
            "funasr": FUNASR_MODEL_CATALOG,
        }
        meta = catalogs.get(asr_model_backend(model_name), ASR_MODEL_CATALOG).get(model_name)
        return str((meta or {}).get("label") or model_name)

    def engine_for_model(self, model_name: str) -> FasterWhisperASR:
        if model_name == self.default_engine.model_name and asr_model_backend(model_name) == "faster-whisper":
            return self.default_engine

        engine = self.engines.get(model_name)
        if engine is not None:
            return engine

        backend = asr_model_backend(model_name)
        if backend == "mlx":
            engine = MlxWhisperASR(
                model_name=asr_base_model_name(model_name),
                repo_id=asr_model_repos(model_name)[0],
                display_name=model_name,
            )
        elif backend == "funasr":
            meta = FUNASR_MODEL_CATALOG.get(model_name, {})
            engine = FunASRASR(
                model_name=model_name,
                model_id=str(meta.get("model_id") or meta.get("repo_id") or model_name),
                display_name=model_name,
                trust_remote_code=bool(meta.get("trust_remote_code")),
            )
        else:
            runtime_model = model_name
            for repo_id in asr_model_repos(model_name):
                if (asr_repo_cache_path(repo_id, ASR_MODEL_DIR) / "snapshots").exists():
                    runtime_model = repo_id
                    break
            engine = FasterWhisperASR(model_name=runtime_model)
        self.engines[model_name] = engine
        return engine

    def engine_status(self, model_name: str, engine: FasterWhisperASR) -> Dict[str, Any]:
        status = dict(engine.status())
        status["model"] = model_name
        status["label"] = self.model_label(model_name)
        if engine.model_name != model_name:
            status["runtime_model"] = engine.model_name
        return status

    def loaded_engine_items(self) -> list[tuple[str, FasterWhisperASR]]:
        items: list[tuple[str, FasterWhisperASR]] = []
        if self.default_engine.loaded:
            items.append((self.default_model, self.default_engine))
        for model_name, engine in self.engines.items():
            if engine.loaded:
                items.append((model_name, engine))
        return items

    def model_runtime_flags(self, model_name: str) -> Dict[str, bool]:
        cached_engine = self.engines.get(model_name)
        return {
            "loaded": (model_name == self.default_model and self.default_engine.loaded)
            or bool(cached_engine and cached_engine.loaded),
            "loading": (model_name == self.default_model and self.default_engine.loading)
            or bool(cached_engine and cached_engine.loading),
        }

    def active_status(self, include_available: bool = False) -> Dict[str, Any]:
        def with_available(status: Dict[str, Any]) -> Dict[str, Any]:
            if include_available:
                return {**status, "available_models": sorted(discovered_asr_models())}
            return status

        for model_name, engine in self.loaded_engine_items():
            return with_available(self.engine_status(model_name, engine))
        if self.default_engine.loading:
            return with_available(self.engine_status(self.default_model, self.default_engine))
        for model_name, engine in self.engines.items():
            if engine.loading:
                return with_available(self.engine_status(model_name, engine))
        return with_available(self.default_engine.status())

    def require_loaded_engine(self, model_name: str) -> FasterWhisperASR:
        engine = self.engine_for_model(model_name)
        if not engine.loaded:
            raise ASRUnavailable("识别模型尚未加载，请先在设置中加载模型。")
        return engine

    def load_model_sync(self, model_name: str) -> Dict[str, Any]:
        requested_model = self._validate_model(model_name)
        with self.load_lock:
            unloaded = []
            for loaded_model, engine in self.loaded_engine_items():
                if loaded_model == requested_model:
                    continue
                engine.unload()
                unloaded.append({
                    "model": loaded_model,
                    "label": self.model_label(loaded_model),
                })

            engine = self.engine_for_model(requested_model)
            engine.load()
            if asr_model_backend(requested_model) == "funasr":
                mark_funasr_model_installed(requested_model)
            return {
                "kind": "asr",
                "model": requested_model,
                "label": self.model_label(requested_model),
                "unloaded": unloaded,
                "status": self.engine_status(requested_model, engine),
            }

    def is_loaded(self, model_name: str) -> bool:
        return any(loaded_model == model_name for loaded_model, _engine in self.loaded_engine_items())

    def unload_model(self, model_name: str) -> None:
        requested_model = self._validate_model(model_name)
        if requested_model == self.default_model:
            engine = self.default_engine
        else:
            engine = self.engines.get(requested_model)
        if engine is not None:
            engine.unload()

    def _validate_model(self, model_name: str) -> str:
        requested_model = (model_name or self.default_model).strip()
        if requested_model not in SUPPORTED_ASR_MODELS:
            raise ValueError("当前识别模型不可用。")
        return requested_model
