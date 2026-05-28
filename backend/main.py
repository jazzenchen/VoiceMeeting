from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .asr import ASRUnavailable, FasterWhisperASR, MLX_MODEL_REPOS, MlxWhisperASR
from .config import (
    ALLOW_MODEL_DOWNLOAD,
    ASR_MODEL,
    ASR_MODEL_DIR,
    MLX_ASR_MODEL_DIR,
    MODELS_DIR,
    PROJECT_DIR,
    ensure_runtime_dirs,
)
from .diarization import DiarizationUnavailable, PyannoteDiarizer, assign_speakers
from .llm import LLMManager
from .media_tools import ffprobe_path
from .prompt_settings import PromptConfigStore
from .speaker_tracker import SpeakerTracker, SpeakerTrackingUnavailable
from .storage import MeetingStore
from .summarizer import (
    DEFAULT_PROMPT_META,
    DEFAULT_PROMPTS,
    MeetingSummarizer,
    build_local_markdown,
    build_transcript_markdown,
    fallback_incremental_summary,
    notes_only_markdown,
)
from .transcript import build_utterances
from .vibearound import VibeAroundClient


ensure_runtime_dirs()

app = FastAPI(title="VoiceMeeting", version="0.0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = MeetingStore()
asr = FasterWhisperASR()
asr_engines: Dict[str, FasterWhisperASR] = {}
diarizer = PyannoteDiarizer()
local_pyannote_diarizer: Optional[PyannoteDiarizer] = None
speaker_tracker = SpeakerTracker()
vibearound = VibeAroundClient()
llm = LLMManager(vibearound)
prompt_settings = PromptConfigStore(DEFAULT_PROMPTS, DEFAULT_PROMPT_META)
summarizer = MeetingSummarizer(llm, prompt_getter=prompt_settings.get)
summary_lock = asyncio.Lock()
summary_states: Dict[str, Dict[str, Any]] = {}
reprocess_states: Dict[str, Dict[str, Any]] = {}
REALTIME_SUMMARY_ENABLED = False
model_download_states: Dict[str, Dict[str, Any]] = {}
model_load_lock = threading.Lock()
SUMMARY_TASK_TIMEOUT_SECONDS = 300.0
SUMMARY_STALE_SECONDS = 300.0

SUPPORTED_ASR_LANGUAGES = {
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

ASR_MODEL_CATALOG: Dict[str, Dict[str, Any]] = {
    "tiny": {
        "label": "轻量识别",
        "params": "39M",
        "disk": "约 75MB",
        "profile": "最快，适合试录和低配机器",
    },
    "base": {
        "label": "快速识别",
        "params": "74M",
        "disk": "约 141MB",
        "profile": "快，准确度基础",
    },
    "small": {
        "label": "标准识别",
        "params": "244M",
        "disk": "约 464MB",
        "profile": "当前默认，实时余量充足",
    },
    "medium": {
        "label": "高精度识别",
        "params": "769M",
        "disk": "约 1.5GB",
        "profile": "更适合中文/混合语音",
    },
    "large-v3-turbo": {
        "label": "高精度加速",
        "params": "809M",
        "disk": "约 1.6GB",
        "profile": "会后重识别优先试它",
    },
    "large-v3": {
        "label": "最高精度识别",
        "params": "1550M",
        "disk": "约 3GB",
        "profile": "最重，适合离线精修",
    },
}

MAC_MLX_ENABLED = platform.system() == "Darwin"
MLX_ASR_MODEL_CATALOG: Dict[str, Dict[str, Any]] = {
    f"mlx-{name}": {
        "label": f"MLX {meta['label']}",
        "params": meta["params"],
        "disk": meta["disk"],
        "profile": f"Apple Silicon 加速 · {meta['profile']}",
        "base_model": name,
    }
    for name, meta in ASR_MODEL_CATALOG.items()
}
SUPPORTED_ASR_MODELS = set(ASR_MODEL_CATALOG) | (
    set(MLX_ASR_MODEL_CATALOG) if MAC_MLX_ENABLED else set()
)
ASR_MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "dropbox-dash/faster-whisper-large-v3-turbo",
}
PYANNOTE_COMMUNITY_MODEL_ID = "pyannote-community-1"
PYANNOTE_COMMUNITY_REPO_ID = "pyannote/speaker-diarization-community-1"
PYANNOTE_MODEL_DIR = MODELS_DIR / "pyannote" / "speaker-diarization-community-1"


def discovered_asr_models() -> set[str]:
    models: set[str] = set()
    for model_name in SUPPORTED_ASR_MODELS:
        if any(asr_model_cache_ready(path) for path in asr_model_cache_paths(model_name)):
            models.add(model_name)
    return models


def asr_model_backend(model_name: str) -> str:
    return "mlx" if model_name.startswith("mlx-") else "faster-whisper"


def asr_base_model_name(model_name: str) -> str:
    return model_name.removeprefix("mlx-")


def asr_model_cache_dir(model_name: str) -> Path:
    return MLX_ASR_MODEL_DIR if asr_model_backend(model_name) == "mlx" else ASR_MODEL_DIR


def asr_model_repos(model_name: str) -> list[str]:
    base_name = asr_base_model_name(model_name)
    if asr_model_backend(model_name) == "mlx":
        return [MLX_MODEL_REPOS.get(base_name, f"mlx-community/whisper-{base_name}-mlx")]
    return [ASR_MODEL_REPOS.get(base_name, f"Systran/faster-whisper-{base_name}")]


def asr_repo_cache_path(repo_id: str, cache_dir: Path = ASR_MODEL_DIR) -> Path:
    return cache_dir / f"models--{repo_id.replace('/', '--')}"


def asr_model_cache_paths(model_name: str) -> list[Path]:
    cache_dir = asr_model_cache_dir(model_name)
    return [asr_repo_cache_path(repo_id, cache_dir) for repo_id in asr_model_repos(model_name)]


def asr_model_cache_ready(path: Path) -> bool:
    snapshots = path / "snapshots"
    if not snapshots.exists():
        return False
    return any(
        (snapshot / "model.bin").is_file()
        or (
            (snapshot / "config.json").is_file()
            and (
                (snapshot / "weights.safetensors").is_file()
                or (snapshot / "weights.npz").is_file()
            )
        )
        for snapshot in snapshots.iterdir()
        if snapshot.is_dir()
    )


def asr_model_cache_path(model_name: str) -> Path:
    paths = asr_model_cache_paths(model_name)
    for path in paths:
        if asr_model_cache_ready(path):
            return path
    return paths[0]


def directory_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def local_pyannote_model_path() -> Optional[Path]:
    return PYANNOTE_MODEL_DIR if (PYANNOTE_MODEL_DIR / "config.yaml").exists() else None


def diarizer_for_mode(mode: str) -> Optional[PyannoteDiarizer]:
    global local_pyannote_diarizer
    if mode == "diarization":
        if local_pyannote_diarizer is None:
            local_pyannote_diarizer = PyannoteDiarizer(
                backend="pyannote",
                model=str(PYANNOTE_MODEL_DIR),
            )
        return local_pyannote_diarizer
    if mode == "auto" and diarizer.enabled:
        return diarizer
    return None


def hf_token_available() -> bool:
    return bool(
        os.environ.get("VOICE_MEETING_HF_TOKEN")
        or os.environ.get("HUGGINGFACE_TOKEN")
        or os.environ.get("HF_TOKEN")
    )


def read_hf_token() -> Optional[str]:
    return (
        os.environ.get("VOICE_MEETING_HF_TOKEN")
        or os.environ.get("HUGGINGFACE_TOKEN")
        or os.environ.get("HF_TOKEN")
    )


def local_asr_models() -> list[str]:
    return sorted(discovered_asr_models())


def resolve_asr_model(value: Optional[str]) -> str:
    model_name = (value or ASR_MODEL).strip()
    if model_name not in SUPPORTED_ASR_MODELS:
        raise HTTPException(status_code=400, detail="当前识别方式不可用，请换一个选项。")
    if model_name not in discovered_asr_models():
        raise HTTPException(status_code=400, detail="本地还没有这套识别资源，请选择已有的识别方式。")
    return model_name


def model_job_key(kind: str, model: str) -> str:
    return f"{kind}:{model}"


def set_model_download_state(job_id: str, **fields: Any) -> Dict[str, Any]:
    current = dict(model_download_states.get(job_id) or {})
    current.update(fields)
    current["updated_at"] = now_iso()
    model_download_states[job_id] = current
    return current


def active_model_job(kind: str, model: str) -> Optional[Dict[str, Any]]:
    key = model_job_key(kind, model)
    jobs = [
        state
        for state in model_download_states.values()
        if state.get("key") == key and state.get("status") in {"queued", "running"}
    ]
    if not jobs:
        return None
    return sorted(jobs, key=lambda item: str(item.get("updated_at") or ""))[-1]


def latest_model_job(kind: str, model: str) -> Optional[Dict[str, Any]]:
    key = model_job_key(kind, model)
    jobs = [state for state in model_download_states.values() if state.get("key") == key]
    if not jobs:
        return None
    return sorted(jobs, key=lambda item: str(item.get("updated_at") or ""))[-1]


def model_catalog() -> Dict[str, Any]:
    installed_asr = discovered_asr_models()
    asr_models = []
    catalog_groups: list[tuple[str, str, Dict[str, Dict[str, Any]]]] = [
        ("faster-whisper", "通用", ASR_MODEL_CATALOG),
    ]
    if MAC_MLX_ENABLED:
        catalog_groups.append(("mlx", "Apple MLX", MLX_ASR_MODEL_CATALOG))
    for backend, backend_label, catalog in catalog_groups:
        for name, meta in catalog.items():
            path = asr_model_cache_path(name)
            installed = name in installed_asr
            cached_engine = asr_engines.get(name)
            asr_models.append(
                {
                    "kind": "asr",
                    "id": name,
                    "name": name,
                    "label": meta["label"],
                    "params": meta["params"],
                    "disk": meta["disk"],
                    "profile": meta["profile"],
                    "installed": installed,
                    "repo_id": asr_model_repos(name)[0],
                    "path": str(path),
                    "size_bytes": directory_size_bytes(path) if installed else 0,
                    "current": name == ASR_MODEL,
                    "loaded": (name == asr.model_name and asr.loaded) or bool(cached_engine and cached_engine.loaded),
                    "job": latest_model_job("asr", name),
                    "backend": backend,
                    "backend_label": backend_label,
                    "base_model": meta.get("base_model") or name,
                }
        )

    pyannote_path = local_pyannote_model_path()
    pyannote_installed = pyannote_path is not None
    return {
        "asr": {
            "model_dir": str(ASR_MODEL_DIR),
            "mlx_model_dir": str(MLX_ASR_MODEL_DIR) if MAC_MLX_ENABLED else "",
            "models": asr_models,
        },
        "diarization": {
            "model_dir": str(MODELS_DIR / "pyannote"),
            "models": [
                {
                    "kind": "diarization",
                    "id": PYANNOTE_COMMUNITY_MODEL_ID,
                    "name": PYANNOTE_COMMUNITY_MODEL_ID,
                    "repo_id": PYANNOTE_COMMUNITY_REPO_ID,
                    "label": "Pyannote Community-1",
                    "params": "pipeline",
                    "disk": "约 32MB",
                    "profile": "高精度说话人分离",
                    "installed": pyannote_installed,
                    "path": str(PYANNOTE_MODEL_DIR),
                    "size_bytes": directory_size_bytes(PYANNOTE_MODEL_DIR) if pyannote_installed else 0,
                    "requires_token": True,
                    "token_available": hf_token_available(),
                    "enabled": diarizer.enabled,
                    "available": diarizer.status().get("available") or (
                        pyannote_installed and not diarizer.enabled
                    ),
                    "job": latest_model_job("diarization", PYANNOTE_COMMUNITY_MODEL_ID),
                }
            ],
            "runtime": diarizer.status(),
        },
        "downloads": sorted(
            model_download_states.values(),
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )[:24],
    }


def _friendly_model_error(exc: Exception, kind: str = "") -> str:
    text = str(exc)
    lowered = text.lower()
    if kind == "asr" and (
        "repository not found" in lowered
        or "401" in text
        or "403" in text
        or "gated" in lowered
        or "token" in lowered
    ):
        return "ASR 模型下载失败，可能是下载源不可用或网络访问被拦截。请刷新模型列表后重试。"
    if "401" in text or "403" in text or "gated" in lowered or "token" in lowered:
        return "模型需要授权。请先配置 VOICE_MEETING_HF_TOKEN/HF_TOKEN，并确认已接受模型条款。"
    if "connection" in lowered or "timeout" in lowered or "network" in lowered:
        return "联网下载失败，请检查网络后重试。"
    return text or type(exc).__name__


def model_download_cancel_requested(job_id: str) -> bool:
    return bool(model_download_states.get(job_id, {}).get("cancel_requested"))


def raise_if_model_download_cancelled(job_id: str) -> None:
    if model_download_cancel_requested(job_id):
        raise ModelDownloadCancelled("model download cancelled")


def cleanup_model_files(kind: str, model: str) -> None:
    paths: list[Path] = []
    lock_paths: list[Path] = []
    if kind == "asr":
        paths = asr_model_cache_paths(model)
        cache_dir = asr_model_cache_dir(model)
        lock_paths = [cache_dir / ".locks" / path.name for path in paths]
    elif kind == "diarization" and model == PYANNOTE_COMMUNITY_MODEL_ID:
        paths = [PYANNOTE_MODEL_DIR]

    for path in paths + lock_paths:
        try:
            if path.exists():
                shutil.rmtree(path)
        except Exception:
            continue


def _download_hf_repo_files(
    job_id: str,
    repo_id: str,
    *,
    cache_dir: Optional[Path] = None,
    local_dir: Optional[Path] = None,
    token: Optional[str] = None,
) -> None:
    from huggingface_hub import HfApi, hf_hub_download, snapshot_download
    from tqdm.auto import tqdm

    class CancelAwareTqdm(tqdm):
        current_file = ""
        file_index = 0
        total_files = 1
        base_downloaded_bytes = 0
        total_repo_bytes = 0
        last_emit_at = 0.0

        @classmethod
        def configure(
            cls,
            *,
            filename: str = "",
            file_index: int = 0,
            total_files: int = 1,
            base_downloaded_bytes: int = 0,
            total_repo_bytes: int = 0,
        ) -> None:
            cls.current_file = filename
            cls.file_index = file_index
            cls.total_files = max(1, total_files)
            cls.base_downloaded_bytes = max(0, base_downloaded_bytes)
            cls.total_repo_bytes = max(0, total_repo_bytes)
            cls.last_emit_at = 0.0

        def update(self, n: int = 1):
            raise_if_model_download_cancelled(job_id)
            result = super().update(n)
            total_repo_bytes = type(self).total_repo_bytes
            if total_repo_bytes <= 0:
                return result

            now = time.monotonic()
            current_file_bytes = max(0, int(getattr(self, "n", 0) or 0))
            downloaded = min(
                total_repo_bytes,
                type(self).base_downloaded_bytes + current_file_bytes,
            )
            if now - type(self).last_emit_at >= 0.25 or downloaded >= total_repo_bytes:
                type(self).last_emit_at = now
                set_model_download_state(
                    job_id,
                    stage=f"下载 {type(self).file_index}/{type(self).total_files}",
                    progress=min(0.98, downloaded / total_repo_bytes),
                    file=type(self).current_file,
                    downloaded_bytes=downloaded,
                    total_bytes=total_repo_bytes,
                )
            return result

    def current_incomplete_bytes() -> int:
        if cache_dir is None:
            return 0
        blobs_dir = Path(cache_dir) / f"models--{repo_id.replace('/', '--')}" / "blobs"
        if not blobs_dir.exists():
            return 0
        sizes: list[int] = []
        for path in blobs_dir.glob("*.incomplete"):
            try:
                sizes.append(path.stat().st_size)
            except OSError:
                continue
        return max(sizes, default=0)

    def start_cache_progress_poll(
        *,
        filename: str,
        file_index: int,
        total_files: int,
        base_downloaded_bytes: int,
        total_repo_bytes: int,
    ) -> tuple[threading.Event, Optional[threading.Thread]]:
        stop_event = threading.Event()
        if cache_dir is None or total_repo_bytes <= 0:
            return stop_event, None

        def poll() -> None:
            last_downloaded = -1
            while not stop_event.wait(0.35):
                if model_download_cancel_requested(job_id):
                    return
                current_file_bytes = current_incomplete_bytes()
                downloaded = min(
                    total_repo_bytes,
                    max(0, base_downloaded_bytes) + max(0, current_file_bytes),
                )
                if downloaded <= last_downloaded:
                    continue
                last_downloaded = downloaded
                set_model_download_state(
                    job_id,
                    stage=f"下载 {file_index}/{total_files}",
                    progress=min(0.98, downloaded / total_repo_bytes),
                    file=filename,
                    downloaded_bytes=downloaded,
                    total_bytes=total_repo_bytes,
                )

        thread = threading.Thread(target=poll, daemon=True)
        thread.start()
        return stop_event, thread

    raise_if_model_download_cancelled(job_id)
    try:
        api = HfApi()
        info = api.model_info(repo_id, files_metadata=True, token=token)
        siblings = [
            sibling
            for sibling in (getattr(info, "siblings", None) or [])
            if getattr(sibling, "rfilename", None)
        ]
    except Exception:
        siblings = []

    if not siblings:
        set_model_download_state(job_id, stage="下载模型文件", progress=0.1)
        raise_if_model_download_cancelled(job_id)
        kwargs: Dict[str, Any] = {"repo_id": repo_id, "token": token}
        if cache_dir is not None:
            kwargs["cache_dir"] = str(cache_dir)
        if local_dir is not None:
            local_dir.mkdir(parents=True, exist_ok=True)
            kwargs["local_dir"] = str(local_dir)
        CancelAwareTqdm.configure()
        kwargs["tqdm_class"] = CancelAwareTqdm
        snapshot_download(**kwargs)
        raise_if_model_download_cancelled(job_id)
        set_model_download_state(job_id, stage="整理模型文件", progress=0.95)
        return

    files = [
        sibling
        for sibling in siblings
        if not str(getattr(sibling, "rfilename", "")).endswith("/")
    ]
    total_files = max(1, len(files))
    total_bytes = sum(int(getattr(item, "size", 0) or 0) for item in files)
    downloaded_bytes = 0
    for index, item in enumerate(files, start=1):
        raise_if_model_download_cancelled(job_id)
        filename = str(getattr(item, "rfilename"))
        size = int(getattr(item, "size", 0) or 0)
        progress = (
            min(0.98, downloaded_bytes / total_bytes)
            if total_bytes > 0
            else min(0.98, (index - 1) / total_files)
        )
        set_model_download_state(
            job_id,
            stage=f"下载 {index}/{total_files}",
            progress=progress,
            file=filename,
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
        )
        CancelAwareTqdm.configure(
            filename=filename,
            file_index=index,
            total_files=total_files,
            base_downloaded_bytes=downloaded_bytes,
            total_repo_bytes=total_bytes,
        )
        kwargs = {
            "repo_id": repo_id,
            "filename": filename,
            "token": token,
        }
        if cache_dir is not None:
            kwargs["cache_dir"] = str(cache_dir)
        if local_dir is not None:
            local_dir.mkdir(parents=True, exist_ok=True)
            kwargs["local_dir"] = str(local_dir)
        kwargs["tqdm_class"] = CancelAwareTqdm
        stop_event, poll_thread = start_cache_progress_poll(
            filename=filename,
            file_index=index,
            total_files=total_files,
            base_downloaded_bytes=downloaded_bytes,
            total_repo_bytes=total_bytes,
        )
        try:
            hf_hub_download(**kwargs)
        finally:
            stop_event.set()
            if poll_thread is not None:
                poll_thread.join(timeout=1.0)
        raise_if_model_download_cancelled(job_id)
        downloaded_bytes += size
        set_model_download_state(
            job_id,
            stage=f"下载 {index}/{total_files}",
            progress=(
                min(0.98, downloaded_bytes / total_bytes)
                if total_bytes > 0
                else min(0.98, index / total_files)
            ),
            file=filename,
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
        )


async def download_model_job(job_id: str, kind: str, model: str) -> None:
    set_model_download_state(job_id, status="running", stage="准备下载", progress=0.0)
    try:
        if kind == "asr":
            if model not in SUPPORTED_ASR_MODELS:
                raise ValueError("当前识别模型不可用。")
            cache_dir = asr_model_cache_dir(model)
            cache_dir.mkdir(parents=True, exist_ok=True)
            repo_id = asr_model_repos(model)[0]
            set_model_download_state(job_id, repo_id=repo_id, stage="连接下载源")
            await asyncio.to_thread(
                _download_hf_repo_files,
                job_id,
                repo_id,
                cache_dir=cache_dir,
            )
        elif kind == "diarization" and model == PYANNOTE_COMMUNITY_MODEL_ID:
            await asyncio.to_thread(
                _download_hf_repo_files,
                job_id,
                PYANNOTE_COMMUNITY_REPO_ID,
                local_dir=PYANNOTE_MODEL_DIR,
                token=read_hf_token(),
            )
        else:
            raise ValueError("当前模型不可用。")
        set_model_download_state(
            job_id,
            status="done",
            stage="已安装",
            progress=1.0,
            error="",
            finished_at=now_iso(),
        )
    except ModelDownloadCancelled:
        cleanup_model_files(kind, model)
        set_model_download_state(
            job_id,
            status="cancelled",
            stage="已取消",
            progress=0.0,
            error="",
            cancel_requested=True,
            finished_at=now_iso(),
        )
    except Exception as exc:
        set_model_download_state(
            job_id,
            status="error",
            stage="下载失败",
            progress=0.0,
            error=_friendly_model_error(exc, kind),
            finished_at=now_iso(),
        )


def parse_optional_ms(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0, int(round(float(text))))
    except ValueError:
        raise HTTPException(status_code=400, detail="音频时间信息无效，请重新录制或导入。")


def existing_audio_path(*values: Optional[str]) -> Optional[Path]:
    for value in values:
        if not value:
            continue
        path = Path(str(value))
        if path.is_file():
            return path
    return None


def audio_duration_ms(path: Path) -> Optional[int]:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
                if rate > 0:
                    return int(round(frames * 1000 / rate))
        except Exception:
            pass
    try:
        ffprobe = ffprobe_path()
        if ffprobe is None:
            return None
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(round(float(result.stdout.strip()) * 1000))
    except Exception:
        pass
    return None


def clean_identifier(value: str, fallback: str = "job") -> str:
    cleaned = "".join(ch for ch in (value or fallback).strip().lower() if ch.isalnum() or ch in "-_")
    return cleaned or fallback


def resolve_speaker_mode(value: Optional[str]) -> str:
    mode = clean_identifier(value or "voiceprint", "voiceprint")
    if mode not in SUPPORTED_SPEAKER_MODES:
        raise HTTPException(status_code=400, detail="当前说话人配置不可用。")
    return mode


def clear_segment_speakers(segments: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [{**segment, "speaker": ""} for segment in segments]


def asr_context_prompt(
    meeting: Dict[str, Any],
    recent_context: str = "",
    configured_prompt: str = "",
) -> str:
    title = re.sub(r"\s+", " ", str(meeting.get("title") or "")).strip()
    description = re.sub(r"\s+", " ", str(meeting.get("description") or "")).strip()
    generic_titles = {"今天的会议", "新会议", "新会议标题", "untitled meeting", "meeting"}
    parts: list[str] = []
    custom = re.sub(r"\s+", " ", str(configured_prompt or "")).strip()
    if custom:
        parts.append(custom[:600])
    if title and title.lower() not in generic_titles:
        parts.append(f"会议标题：{title[:80]}")
    if description:
        parts.append(f"会议引导词：{description[:180]}")
    recent = re.sub(r"\s+", " ", str(recent_context or "")).strip()
    if recent:
        parts.append(f"前文：{recent[-240:]}")
    return " ".join(parts)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sse_event(event: str, data: Dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


def transcript_source(meeting: Dict[str, Any], segments: Optional[list[Dict[str, Any]]] = None) -> Dict[str, Any]:
    segment_rows = list(segments if segments is not None else meeting.get("segments") or [])
    payload = [
        {
            "speaker": str(segment.get("speaker") or ""),
            "text": str(segment.get("text") or "").strip(),
            "start_ms": int(segment.get("start_ms") or 0),
            "end_ms": int(segment.get("end_ms") or 0),
            "chunk_id": str(segment.get("chunk_id") or ""),
        }
        for segment in segment_rows
        if str(segment.get("text") or "").strip()
    ]
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "version_id": str(meeting.get("active_version_id") or "auto"),
        "hash": digest,
        "segment_count": len(payload),
    }


def transcript_source_without_segments(
    meeting: Dict[str, Any],
    removed_segment_ids: set[str],
) -> Dict[str, Any]:
    segments = [
        segment
        for segment in meeting.get("segments") or []
        if str(segment.get("id") or "") not in removed_segment_ids
    ]
    return transcript_source(meeting, segments)


def summary_is_current(meeting: Dict[str, Any], source: Optional[Dict[str, Any]] = None) -> bool:
    current_source = source or transcript_source(meeting)
    if int(current_source.get("segment_count") or 0) == 0:
        summary = meeting.get("summary") or {}
        has_summary = bool(summary.get("summary")) or any(
            bool(summary.get(key))
            for key in ("topics", "decisions", "action_items", "open_questions", "risks")
        )
        return not has_summary
    return bool(current_source.get("hash")) and meeting.get("summary_source_hash") == current_source.get("hash")


def final_notes_current(meeting: Dict[str, Any], source: Optional[Dict[str, Any]] = None) -> bool:
    current_source = source or transcript_source(meeting)
    return bool(meeting.get("final_markdown")) and meeting.get("final_source_hash") == current_source.get("hash")


def set_summary_state(meeting_id: str, status: str, error: str = "") -> None:
    summary_states[meeting_id] = {
        "status": status,
        "error": error,
        "updated_at": now_iso(),
    }


def set_reprocess_state(job_id: str, **fields: Any) -> Dict[str, Any]:
    current = dict(reprocess_states.get(job_id) or {})
    current.update(fields)
    current["updated_at"] = now_iso()
    reprocess_states[job_id] = current
    return current


def latest_reprocess_state(meeting_id: str) -> Optional[Dict[str, Any]]:
    jobs = [
        state
        for state in reprocess_states.values()
        if state.get("meeting_id") == meeting_id
    ]
    if not jobs:
        return None
    return sorted(jobs, key=lambda item: str(item.get("updated_at") or ""))[-1]


def get_summary_state(meeting_id: str) -> Dict[str, Any]:
    state = summary_states.get(meeting_id) or {"status": "idle", "error": "", "updated_at": ""}
    if state.get("status") not in {"queued", "updating"}:
        return state

    updated_at = state.get("updated_at") or ""
    try:
        stamp = datetime.fromisoformat(updated_at)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
    except Exception:
        age = SUMMARY_STALE_SECONDS + 1

    if age > SUMMARY_STALE_SECONDS:
        set_summary_state(
            meeting_id,
            "error",
            f"Summary task exceeded {int(SUMMARY_STALE_SECONDS)} seconds.",
        )
        return summary_states[meeting_id]
    return state


def meeting_runtime(meeting_id: str) -> Dict[str, Any]:
    meeting = store.get_meeting(meeting_id)
    counts: Dict[str, int] = {}
    active_chunks = []
    for chunk in meeting.get("chunks") or []:
        status = chunk.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
        if status in {"saved", "converting", "transcribing", "diarizing", "identifying_speakers"}:
            active_chunks.append(
                {
                    "id": chunk.get("id"),
                    "seq": chunk.get("seq"),
                    "status": status,
                    "duration_ms": chunk.get("duration_ms"),
                    "started_at_ms": chunk.get("started_at_ms"),
                    "ended_at_ms": chunk.get("ended_at_ms"),
                    "cut_reason": chunk.get("cut_reason"),
                }
            )
    if REALTIME_SUMMARY_ENABLED:
        summary_state = get_summary_state(meeting_id)
        current_source = transcript_source(meeting)
        if summary_state.get("status") not in {"queued", "updating"} and not summary_is_current(meeting, current_source):
            summary_state = {
                **summary_state,
                "status": "stale",
                "error": "",
                "source": current_source,
            }
    else:
        summary_state = {"status": "idle", "error": "", "updated_at": ""}
    return {
        "meeting_id": meeting_id,
        "chunk_counts": counts,
        "active_chunks": active_chunks,
        "has_active_chunks": bool(active_chunks),
        "summary": summary_state,
        "reprocess": latest_reprocess_state(meeting_id),
        "asr": {**asr.status(), "available_models": local_asr_models()},
        "diarization": diarizer.status(),
        "speaker_tracking": speaker_tracker.status(),
        "llm": llm.describe(),
    }


def queue_summary_rebuild(
    meeting_id: str,
    background_tasks: Optional[BackgroundTasks] = None,
    reason: str = "",
) -> None:
    if not REALTIME_SUMMARY_ENABLED:
        summary_states.pop(meeting_id, None)
        return
    set_summary_state(meeting_id, "queued", reason)
    if background_tasks is not None:
        background_tasks.add_task(rebuild_summary_for_meeting, meeting_id, reason)
    else:
        asyncio.create_task(rebuild_summary_for_meeting(meeting_id, reason))


async def rebuild_summary_for_meeting(meeting_id: str, reason: str = "", force: bool = False) -> None:
    if not REALTIME_SUMMARY_ENABLED:
        summary_states.pop(meeting_id, None)
        return
    async with summary_lock:
        set_summary_state(meeting_id, "updating", reason)
        try:
            meeting = store.get_meeting(meeting_id)
            source = transcript_source(meeting)
            if not force and summary_is_current(meeting, source):
                set_summary_state(meeting_id, "done")
                return
            if source["segment_count"] == 0:
                store.set_summary(meeting_id, {}, source)
                set_summary_state(meeting_id, "done")
                return

            rebuilt = await asyncio.wait_for(
                summarizer.rebuild(meeting),
                timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
            )
            latest_meeting = store.get_meeting(meeting_id)
            latest_source = transcript_source(latest_meeting)
            if latest_source["hash"] != source["hash"]:
                rebuilt = await asyncio.wait_for(
                    summarizer.rebuild(latest_meeting),
                    timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
                )
            store.set_summary(meeting_id, rebuilt, latest_source)
            set_summary_state(meeting_id, "done")
        except asyncio.TimeoutError:
            meeting = store.get_meeting(meeting_id)
            fallback = fallback_incremental_summary(
                {},
                meeting.get("utterances") or meeting.get("segments") or [],
                f"summary task timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds",
            )
            store.set_summary(meeting_id, fallback, transcript_source(meeting))
            set_summary_state(
                meeting_id,
                "error",
                f"Summary task timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds.",
            )
        except Exception as exc:
            set_summary_state(meeting_id, "error", str(exc))


async def ensure_summary_current(meeting_id: str) -> Dict[str, Any]:
    meeting = store.get_meeting(meeting_id)
    if not REALTIME_SUMMARY_ENABLED:
        return meeting
    if summary_is_current(meeting):
        return meeting
    await rebuild_summary_for_meeting(meeting_id, "当前稿件已更新", force=True)
    return store.get_meeting(meeting_id)


class CreateMeetingRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class UpdateMeetingRequest(BaseModel):
    title: str
    description: Optional[str] = None


class CreateVersionRequest(BaseModel):
    version_id: Optional[str] = None
    label: Optional[str] = None
    kind: str = "manual"
    parent_version_id: Optional[str] = None
    settings: Dict[str, Any] = {}
    make_current: bool = False


class CreateEditableVersionRequest(BaseModel):
    source_version_id: Optional[str] = None


class UpdateSegmentRequest(BaseModel):
    text: Optional[str] = None
    speaker: Optional[str] = None


class RenameSpeakerRequest(BaseModel):
    old_label: str
    new_label: str


class AskMessage(BaseModel):
    role: str = "user"
    content: str


class MeetingAskRequest(BaseModel):
    prompt: str
    history: list[AskMessage] = []


class ReprocessRequest(BaseModel):
    level: str = "asr"
    language: Optional[str] = "auto"
    asr_model: Optional[str] = None
    speaker_mode: Optional[str] = "voiceprint"
    make_current: bool = True
    force_local: bool = False
    source_version_id: Optional[str] = None
    reset_speakers: bool = False


class FinalizeRequest(BaseModel):
    force_local: bool = False


class ModelDownloadRequest(BaseModel):
    kind: str = "asr"
    model: str


class LLMOpenAIChatRequest(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class LLMConfigRequest(BaseModel):
    provider: str = "vibearound"
    openai_chat: LLMOpenAIChatRequest = Field(default_factory=LLMOpenAIChatRequest)


class PromptConfigRequest(BaseModel):
    prompts: Dict[str, str] = {}


class ModelDownloadCancelled(RuntimeError):
    pass


@app.get("/api/models")
async def list_models() -> Dict[str, Any]:
    return model_catalog()


@app.post("/api/models/download")
async def start_model_download(payload: ModelDownloadRequest) -> Dict[str, Any]:
    kind = clean_identifier(payload.kind or "asr", "asr")
    model = (payload.model or "").strip()
    if kind == "asr" and model not in SUPPORTED_ASR_MODELS:
        raise HTTPException(status_code=400, detail="当前识别模型不可用。")
    if kind == "diarization" and model != PYANNOTE_COMMUNITY_MODEL_ID:
        raise HTTPException(status_code=400, detail="当前说话人分离模型不可用。")
    running = active_model_job(kind, model)
    if running:
        return {"job": running, "catalog": model_catalog()}

    job_id = uuid.uuid4().hex
    state = set_model_download_state(
        job_id,
        id=job_id,
        key=model_job_key(kind, model),
        kind=kind,
        model=model,
        status="queued",
        stage="queued",
        progress=0.0,
        error="",
        created_at=now_iso(),
    )
    asyncio.create_task(download_model_job(job_id, kind, model))
    return {"job": state, "catalog": model_catalog()}


@app.delete("/api/models/{kind}/{model}")
async def delete_model(kind: str, model: str) -> Dict[str, Any]:
    clean_kind = clean_identifier(kind, "asr")
    clean_model = model.strip()
    running = active_model_job(clean_kind, clean_model)
    if running:
        set_model_download_state(
            str(running["id"]),
            status="cancelling",
            stage="正在取消",
            cancel_requested=True,
            error="",
        )
        cleanup_model_files(clean_kind, clean_model)
        return model_catalog()

    if clean_kind == "asr":
        if clean_model not in SUPPORTED_ASR_MODELS:
            raise HTTPException(status_code=400, detail="当前识别模型不可用。")
        if clean_model == asr.model_name and asr.loaded:
            raise HTTPException(status_code=409, detail="当前识别模型已加载，请重启服务后再删除。")
        cached_engine = asr_engines.get(clean_model)
        if cached_engine is not None and cached_engine.loaded:
            raise HTTPException(status_code=409, detail="这个识别模型已加载，请重启服务后再删除。")
    elif clean_kind == "diarization" and clean_model == PYANNOTE_COMMUNITY_MODEL_ID:
        if diarizer.loaded or bool(local_pyannote_diarizer and local_pyannote_diarizer.loaded):
            raise HTTPException(status_code=409, detail="说话人分离模型已加载，请重启服务后再删除。")
    else:
        raise HTTPException(status_code=400, detail="当前模型不可用。")

    cleanup_model_files(clean_kind, clean_model)
    return model_catalog()


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "app": "VoiceMeeting",
        "version": "0.0.1",
        "api_revision": 2,
        "features": ["models.load", "native-save", "i18n"],
        "project_dir": str(PROJECT_DIR),
        "asr_model": ASR_MODEL,
        "asr": {**asr.status(), "available_models": local_asr_models()},
        "diarization": diarizer.status(),
        "speaker_tracking": speaker_tracker.status(),
    }


@app.get("/api/vibearound/status")
async def vibearound_status() -> Dict[str, Any]:
    return await vibearound.status()


@app.get("/api/llm/status")
async def llm_status() -> Dict[str, Any]:
    return await llm.status()


@app.get("/api/llm/config")
async def get_llm_config() -> Dict[str, Any]:
    return llm.public_config()


@app.put("/api/llm/config")
async def update_llm_config(payload: LLMConfigRequest) -> Dict[str, Any]:
    try:
        return llm.save_config(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/prompts/config")
async def get_prompt_config() -> Dict[str, Any]:
    return prompt_settings.public_config()


@app.put("/api/prompts/config")
async def update_prompt_config(payload: PromptConfigRequest) -> Dict[str, Any]:
    return prompt_settings.save(payload.model_dump())


@app.get("/api/meetings")
async def list_meetings() -> Dict[str, Any]:
    return {"meetings": store.list_meetings()}


@app.post("/api/meetings")
async def create_meeting(payload: CreateMeetingRequest) -> Dict[str, Any]:
    return store.create_meeting(payload.title, payload.description)


@app.patch("/api/meetings/{meeting_id}")
async def update_meeting(meeting_id: str, payload: UpdateMeetingRequest) -> Dict[str, Any]:
    try:
        return store.update_meeting_title(meeting_id, payload.title, payload.description)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.get("/api/meetings/{meeting_id}")
async def get_meeting(meeting_id: str) -> Dict[str, Any]:
    try:
        return store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.get("/api/meetings/{meeting_id}/versions")
async def list_meeting_versions(meeting_id: str) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
        return {
            "meeting_id": meeting_id,
            "active_version_id": meeting.get("active_version_id") or "auto",
            "versions": store.list_transcript_versions(meeting_id),
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.post("/api/meetings/{meeting_id}/versions")
async def create_meeting_version(
    meeting_id: str,
    payload: CreateVersionRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    kind = "".join(ch for ch in (payload.kind or "manual").strip().lower() if ch.isalnum() or ch in "-_")
    kind = kind or "manual"
    version_id = payload.version_id or f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    label = payload.label or version_id
    try:
        meeting = store.create_transcript_version(
            meeting_id=meeting_id,
            version_id=version_id,
            label=label,
            kind=kind,
            settings=payload.settings,
            parent_version_id=payload.parent_version_id,
            make_current=payload.make_current,
        )
        if payload.make_current:
            queue_summary_rebuild(meeting_id, background_tasks, "当前稿件已切换")
        return meeting
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/meetings/{meeting_id}/versions/{version_id}/activate")
async def activate_meeting_version(
    meeting_id: str,
    version_id: str,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    try:
        meeting = store.set_active_transcript_version(meeting_id, version_id)
        queue_summary_rebuild(meeting_id, background_tasks, "当前稿件已切换")
        return meeting
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这份稿件，请刷新后再试。")


@app.post("/api/meetings/{meeting_id}/versions/editable")
async def create_editable_meeting_version(
    meeting_id: str,
    payload: CreateEditableVersionRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    try:
        meeting = store.create_editable_version(meeting_id, payload.source_version_id)
        queue_summary_rebuild(meeting_id, background_tasks, "已创建可编辑稿")
        return meeting
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到会议或稿件，请刷新后再试。")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def editable_active_version_id(meeting_id: str) -> str:
    try:
        version = store.get_active_transcript_version(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    if version.get("kind") != "manual-edit":
        raise HTTPException(
            status_code=409,
            detail="请先创建可编辑副本，再修改文字或说话人。",
        )
    return str(version.get("id") or "auto")


@app.patch("/api/meetings/{meeting_id}/segments/{segment_id}")
async def update_meeting_segment(
    meeting_id: str,
    segment_id: str,
    payload: UpdateSegmentRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    if payload.text is None and payload.speaker is None:
        raise HTTPException(status_code=400, detail="没有可保存的修改。")
    version_id = editable_active_version_id(meeting_id)
    try:
        meeting = store.update_segment(
            meeting_id=meeting_id,
            version_id=version_id,
            segment_id=segment_id,
            text=payload.text,
            speaker=payload.speaker,
        )
        queue_summary_rebuild(meeting_id, background_tasks, "文字已修改")
        return meeting
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这段文字，请刷新后再试。")


@app.post("/api/meetings/{meeting_id}/speakers/rename")
async def rename_meeting_speaker(
    meeting_id: str,
    payload: RenameSpeakerRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    version_id = editable_active_version_id(meeting_id)
    try:
        meeting = store.rename_speaker_in_version(
            meeting_id=meeting_id,
            version_id=version_id,
            old_label=payload.old_label,
            new_label=payload.new_label,
        )
        queue_summary_rebuild(meeting_id, background_tasks, "说话人已修改")
        return meeting
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="当前稿件里没有找到这个说话人。")


@app.get("/api/meetings/{meeting_id}/reprocess")
async def list_reprocess_jobs(meeting_id: str) -> Dict[str, Any]:
    jobs = [
        state
        for state in reprocess_states.values()
        if state.get("meeting_id") == meeting_id
    ]
    jobs = sorted(jobs, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
    return {"meeting_id": meeting_id, "jobs": jobs}


@app.get("/api/meetings/{meeting_id}/reprocess/{job_id}")
async def get_reprocess_job(meeting_id: str, job_id: str) -> Dict[str, Any]:
    state = reprocess_states.get(job_id)
    if not state or state.get("meeting_id") != meeting_id:
        raise HTTPException(status_code=404, detail="找不到这个处理任务，请刷新后再试。")
    return state


@app.post("/api/meetings/{meeting_id}/reprocess")
async def start_reprocess_job(
    meeting_id: str,
    payload: ReprocessRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    level = clean_identifier(payload.level or "asr", "asr")
    requested_speaker_mode = resolve_speaker_mode(payload.speaker_mode)
    job_id = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    if level in {"asr", "rerun-asr", "full-asr"}:
        requested_language = (payload.language or "auto").strip().lower()
        if requested_language not in SUPPORTED_ASR_LANGUAGES:
            raise HTTPException(status_code=400, detail="当前语言暂不支持，请换一种语言设置。")
        requested_model = resolve_asr_model(payload.asr_model)
        try:
            require_loaded_asr_engine(requested_model)
        except ASRUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        source_version_id = payload.source_version_id or meeting.get("active_version_id") or "auto"
        try:
            store.get_transcript_version(meeting_id, source_version_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到源稿件，请刷新后再试。")
        version_id = f"asr-{requested_model}-{stamp}-{uuid.uuid4().hex[:6]}"
        store.create_transcript_version(
            meeting_id=meeting_id,
            version_id=version_id,
            label=f"ASR {requested_model} {stamp}",
            kind="asr",
            settings={
                "model": requested_model,
                "language": requested_language,
                "speaker_mode": requested_speaker_mode,
                "source_version_id": source_version_id,
            },
            parent_version_id=source_version_id,
            make_current=False,
        )
        store.update_transcript_version_status(meeting_id, version_id, "queued")
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="asr",
            status="queued",
            stage="queued",
            version_id=version_id,
            source_version_id=source_version_id,
            progress=0,
            total=len(meeting.get("chunks") or []),
            error="",
            created_at=now_iso(),
        )
        background_tasks.add_task(
            reprocess_asr_version,
            meeting_id,
            job_id,
            version_id,
            requested_language,
            requested_model,
            requested_speaker_mode,
            payload.make_current,
        )
        return {"job": state}

    if level in {"speaker", "voiceprint", "diarization", "speakers"}:
        source_version_id = payload.source_version_id or meeting.get("active_version_id") or "auto"
        try:
            store.get_transcript_version(meeting_id, source_version_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到源稿件，请刷新后再试。")
        source_segments = store.get_segments_for_version(meeting_id, source_version_id)
        version_id = f"speaker-{stamp}-{uuid.uuid4().hex[:6]}"
        store.create_transcript_version(
            meeting_id=meeting_id,
            version_id=version_id,
            label=f"说话人 {stamp}",
            kind="speaker",
            settings={
                "source_version_id": source_version_id,
                "reset_speakers": payload.reset_speakers,
                "speaker_mode": requested_speaker_mode,
            },
            parent_version_id=source_version_id,
            make_current=False,
        )
        store.update_transcript_version_status(meeting_id, version_id, "queued")
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="speaker",
            status="queued",
            stage="queued",
            version_id=version_id,
            source_version_id=source_version_id,
            progress=0,
            total=len({segment.get("chunk_id") for segment in source_segments if segment.get("chunk_id")}),
            error="",
            created_at=now_iso(),
        )
        background_tasks.add_task(
            reprocess_speaker_version,
            meeting_id,
            job_id,
            version_id,
            source_version_id,
            requested_speaker_mode,
            payload.reset_speakers,
            payload.make_current,
        )
        return {"job": state}

    if level in {"repair", "llm-repair", "llm_repair", "polish"}:
        source_version_id = payload.source_version_id or meeting.get("active_version_id") or "auto"
        try:
            store.get_transcript_version(meeting_id, source_version_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到源稿件，请刷新后再试。")
        source_segments = store.get_segments_for_version(meeting_id, source_version_id)
        version_id = f"repair-{stamp}-{uuid.uuid4().hex[:6]}"
        store.create_transcript_version(
            meeting_id=meeting_id,
            version_id=version_id,
            label=f"LLM 精修 {stamp}",
            kind="llm-repair",
            settings={"source_version_id": source_version_id},
            parent_version_id=source_version_id,
            make_current=False,
        )
        store.update_transcript_version_status(meeting_id, version_id, "queued")
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="repair",
            status="queued",
            stage="queued",
            version_id=version_id,
            source_version_id=source_version_id,
            progress=0,
            total=max(1, (len(source_segments) + 23) // 24),
            error="",
            created_at=now_iso(),
        )
        background_tasks.add_task(
            reprocess_llm_repair_version,
            meeting_id,
            job_id,
            version_id,
            source_version_id,
            payload.make_current,
        )
        return {"job": state}

    if level in {"merge", "remerge", "utterances"}:
        source_version_id = payload.source_version_id or meeting.get("active_version_id") or "auto"
        version_id = f"merge-{stamp}-{uuid.uuid4().hex[:6]}"
        try:
            store.get_transcript_version(meeting_id, source_version_id)
            source_segments = store.get_segments_for_version(meeting_id, source_version_id)
            meeting_snapshot = store.get_meeting(meeting_id)
            chunks = meeting_snapshot.get("chunks") or []
            chunk_map = {
                str(chunk.get("id") or ""): chunk
                for chunk in chunks
                if chunk.get("id")
            }
            utterances = build_utterances(source_segments, chunks)
            store.create_transcript_version(
                meeting_id=meeting_id,
                version_id=version_id,
                label=f"重新合并 {stamp}",
                kind="merge",
                settings={"source_version_id": source_version_id},
                parent_version_id=source_version_id,
                make_current=False,
            )
            inserted_total = 0
            for utterance in utterances:
                parts = utterance.get("parts") or []
                first_part = next(
                    (part for part in parts if part.get("chunk_id")),
                    None,
                )
                chunk_id = str(
                    (first_part or {}).get("chunk_id")
                    or (source_segments[0].get("chunk_id") if source_segments else "")
                    or ""
                )
                if not chunk_id:
                    continue
                base_ms = int((chunk_map.get(chunk_id) or {}).get("started_at_ms") or 0)
                start_ms = max(0, int(utterance.get("start_ms") or 0) - base_ms)
                end_ms = max(start_ms + 1, int(utterance.get("end_ms") or start_ms) - base_ms)
                inserted = store.add_segments(
                    meeting_id,
                    chunk_id,
                    [
                        {
                            "speaker": utterance.get("speaker") or "Speaker",
                            "text": utterance.get("text") or "",
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "confidence": None,
                        }
                    ],
                    version_id=version_id,
                )
                inserted_total += len(inserted)
            if inserted_total == 0:
                inserted_total = store.copy_segments_to_version(meeting_id, source_version_id, version_id)
            store.update_transcript_version_status(
                meeting_id,
                version_id,
                "ready",
                {
                    "source_version_id": source_version_id,
                    "inserted_segments": inserted_total,
                    "source_segments": len(source_segments),
                },
            )
            if payload.make_current:
                store.set_active_transcript_version(meeting_id, version_id)
                queue_summary_rebuild(meeting_id, background_tasks, "段落已整理")
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到源稿件，请刷新后再试。")
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="merge",
            status="done",
            stage="done",
            version_id=version_id,
            source_version_id=source_version_id,
            progress=1,
            total=1,
            inserted_segments=inserted_total,
            error="",
            created_at=now_iso(),
        )
        return {"job": state}

    if level in {"notes", "summary", "final-notes", "final_notes"}:
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="notes",
            status="queued",
            stage="queued",
            version_id=meeting.get("active_version_id") or "auto",
            progress=0,
            total=1,
            error="",
            created_at=now_iso(),
        )
        background_tasks.add_task(
            reprocess_final_notes,
            meeting_id,
            job_id,
            payload.force_local,
        )
        return {"job": state}

    raise HTTPException(status_code=400, detail="当前处理方式不可用，请刷新后再试。")


@app.get("/api/meetings/{meeting_id}/runtime")
async def get_meeting_runtime(meeting_id: str) -> Dict[str, Any]:
    try:
        return meeting_runtime(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.post("/api/meetings/{meeting_id}/ask")
async def ask_meeting(meeting_id: str, payload: MeetingAskRequest) -> Dict[str, Any]:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请输入要生成的内容。")
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    summary = meeting.get("summary") or {}
    has_summary = bool(summary.get("summary")) or any(
        bool(summary.get(key))
        for key in ("topics", "decisions", "action_items", "open_questions", "risks")
    )
    if not (meeting.get("utterances") or meeting.get("segments") or meeting.get("final_markdown") or has_summary):
        raise HTTPException(status_code=400, detail="这场会议还没有可用内容，录音或导入音频后再试。")

    try:
        answer = await asyncio.wait_for(
            summarizer.ask(
                meeting,
                prompt,
                [item.model_dump() for item in payload.history],
            ),
            timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="生成时间太久，已停止等待。请稍后重试。",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {
        "answer": answer,
        "llm": llm.describe(),
    }


@app.delete("/api/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str) -> Dict[str, Any]:
    try:
        store.delete_meeting(meeting_id)
        summary_states.pop(meeting_id, None)
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.get("/api/meetings/{meeting_id}/playback")
async def playback_manifest(meeting_id: str) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    chunks = []
    last_end_ms: Optional[int] = None
    sorted_chunks = sorted(
        meeting.get("chunks") or [],
        key=lambda item: (
            item.get("started_at_ms") is None,
            item.get("started_at_ms") or item.get("seq") or 0,
        ),
    )
    for chunk in sorted_chunks:
        audio_path = existing_audio_path(chunk.get("wav_path"), chunk.get("audio_path"))
        if audio_path is None:
            continue

        duration_ms = chunk.get("duration_ms")
        if not duration_ms or int(duration_ms) <= 0:
            duration_ms = audio_duration_ms(audio_path)
        started_at_ms = chunk.get("started_at_ms")
        ended_at_ms = chunk.get("ended_at_ms")
        if started_at_ms is None:
            started_at_ms = last_end_ms or 0
        if (ended_at_ms is None or int(ended_at_ms or 0) <= int(started_at_ms or 0)) and duration_ms is not None:
            ended_at_ms = int(started_at_ms) + int(duration_ms)

        trim_start_ms = 0
        if last_end_ms is not None and started_at_ms is not None:
            trim_start_ms = max(0, int(last_end_ms) - int(started_at_ms))

        playable_duration_ms = None
        if ended_at_ms is not None and started_at_ms is not None:
            playable_duration_ms = max(0, int(ended_at_ms) - int(started_at_ms) - trim_start_ms)
            last_end_ms = max(last_end_ms or int(ended_at_ms), int(ended_at_ms))
        elif duration_ms is not None:
            playable_duration_ms = max(0, int(duration_ms) - trim_start_ms)
            last_end_ms = max(
                last_end_ms or int(started_at_ms) + int(duration_ms),
                int(started_at_ms) + int(duration_ms),
            )

        chunks.append(
            {
                "id": chunk["id"],
                "seq": chunk.get("seq"),
                "client_chunk_id": chunk.get("client_chunk_id"),
                "started_at_ms": started_at_ms,
                "ended_at_ms": ended_at_ms,
                "duration_ms": duration_ms,
                "trim_start_ms": trim_start_ms,
                "playable_duration_ms": playable_duration_ms,
                "cut_reason": chunk.get("cut_reason"),
                "audio_url": f"/api/meetings/{meeting_id}/chunks/{chunk['id']}/audio",
            }
        )

    return {"meeting_id": meeting_id, "chunks": chunks}


@app.get("/api/meetings/{meeting_id}/chunks/{chunk_id}/audio")
async def chunk_audio(meeting_id: str, chunk_id: str) -> FileResponse:
    try:
        chunk = store.get_chunk(chunk_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这段音频，请刷新后再试。")
    if chunk.get("meeting_id") != meeting_id:
        raise HTTPException(status_code=404, detail="找不到这段音频，请刷新后再试。")

    path = existing_audio_path(chunk.get("wav_path"), chunk.get("audio_path"))
    if path is None:
        raise HTTPException(status_code=404, detail="找不到本地音频文件，可能已经被移动或删除。")
    media_type = mimetypes.guess_type(path.name)[0] or chunk.get("mime_type") or "application/octet-stream"
    return FileResponse(str(path), media_type=media_type, filename=path.name)


@app.post("/api/meetings/{meeting_id}/chunks")
async def upload_chunk(
    meeting_id: str,
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    duration_ms: Optional[str] = Form(None),
    language: Optional[str] = Form("mixed"),
    asr_model: Optional[str] = Form(None),
    speaker_mode: str = Form("voiceprint"),
    client_chunk_id: str = Form(""),
    started_at_ms: Optional[str] = Form(None),
    ended_at_ms: Optional[str] = Form(None),
    cut_reason: str = Form(""),
) -> Dict[str, Any]:
    try:
        meeting_before = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    requested_language = (language or "zh").strip().lower()
    if requested_language not in SUPPORTED_ASR_LANGUAGES:
        raise HTTPException(status_code=400, detail="当前语言暂不支持，请换一种语言设置。")
    requested_model = resolve_asr_model(asr_model)
    requested_speaker_mode = resolve_speaker_mode(speaker_mode)
    try:
        asr_engine = require_loaded_asr_engine(requested_model)
    except ASRUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="这段音频为空，请重新录制或导入。")

    parsed_duration_ms = parse_optional_ms(duration_ms)
    parsed_started_at_ms = parse_optional_ms(started_at_ms)
    parsed_ended_at_ms = parse_optional_ms(ended_at_ms)

    chunk = store.create_chunk(
        meeting_id=meeting_id,
        audio_bytes=audio_bytes,
        filename=audio.filename or "chunk.webm",
        mime_type=audio.content_type or "",
        duration_ms=parsed_duration_ms,
        client_chunk_id=client_chunk_id,
        started_at_ms=parsed_started_at_ms,
        ended_at_ms=parsed_ended_at_ms,
        cut_reason=cut_reason,
    )
    audio_path = Path(chunk["audio_path"])
    if audio_path.suffix.lower() == ".wav":
        wav_path = audio_path.with_name(f"{audio_path.stem}_16k.wav")
    else:
        wav_path = audio_path.with_suffix(".wav")
    try:
        store.update_chunk(chunk["id"], status="converting")
        asr.convert_to_wav(audio_path, wav_path)
        store.update_chunk(chunk["id"], wav_path=str(wav_path), status="transcribing")
        meeting_for_prompt = store.get_meeting(meeting_id)
        recent_context = "\n".join(
            segment.get("text", "")
            for segment in (meeting_for_prompt.get("segments") or [])[-6:]
            if segment.get("text")
        )
        result = await asyncio.to_thread(
            asr_engine.transcribe,
            wav_path,
            requested_language,
            asr_context_prompt(
                meeting_for_prompt,
                recent_context,
                prompt_settings.get("asr_context", ""),
            ),
        )
        diarization_result = {
            "status": "disabled",
            "mode": requested_speaker_mode,
            "turns": [],
            "error": "",
        }
        active_diarizer = diarizer_for_mode(requested_speaker_mode)
        if active_diarizer is not None:
            try:
                store.update_chunk(chunk["id"], status="diarizing")
                turns = await asyncio.to_thread(active_diarizer.diarize, wav_path)
                if requested_speaker_mode == "diarization" or not speaker_tracker.enabled:
                    result["segments"] = assign_speakers(result["segments"], turns)
                diarization_result = {
                    "status": "done",
                    "mode": requested_speaker_mode,
                    "turns": turns,
                    "error": "",
                }
            except DiarizationUnavailable as exc:
                diarization_result = {
                    "status": "error",
                    "mode": requested_speaker_mode,
                    "turns": [],
                    "error": str(exc),
                }
        speaker_result = {
            "status": "disabled",
            "mode": requested_speaker_mode,
            "assigned": 0,
            "created": 0,
            "error": "",
        }
        use_speaker_tracking = speaker_tracker.enabled and requested_speaker_mode in {
            "voiceprint",
            "auto",
        }
        if use_speaker_tracking:
            try:
                store.update_chunk(chunk["id"], status="identifying_speakers")
                result["segments"], speaker_result = await asyncio.to_thread(
                    speaker_tracker.assign_segments,
                    store,
                    meeting_id,
                    wav_path,
                    result["segments"],
                )
                speaker_result["mode"] = requested_speaker_mode
            except SpeakerTrackingUnavailable as exc:
                speaker_result = {
                    "status": "error",
                    "mode": requested_speaker_mode,
                    "assigned": 0,
                    "created": 0,
                    "error": str(exc),
                }
        inserted = store.add_segments(meeting_id, chunk["id"], result["segments"])
        store.update_chunk(chunk["id"], status="done")
    except ASRUnavailable as exc:
        store.update_chunk(chunk["id"], status="error", error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        store.update_chunk(chunk["id"], status="error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    meeting = store.get_meeting(meeting_id)
    if inserted:
        store.clear_final_markdown(meeting_id)

    return {
        "chunk": store.get_chunk(chunk["id"]),
        "segments": inserted,
        "utterances": meeting.get("utterances") or [],
        "summary": meeting["summary"],
        "runtime": meeting_runtime(meeting_id),
        "asr": {
            "requested_language": result.get("requested_language"),
            "model": requested_model,
            "language": result.get("language"),
            "language_probability": result.get("language_probability"),
            "top_languages": result.get("top_languages") or [],
            "multilingual": result.get("multilingual"),
            "vad_segments": result.get("vad_segments") or [],
        },
        "diarization": diarization_result,
        "speaker_tracking": speaker_result,
        "summary_status": "idle",
    }


def prepare_chunk_wav(chunk: Dict[str, Any]) -> Path:
    existing_wav = existing_audio_path(chunk.get("wav_path"))
    if existing_wav is not None:
        return existing_wav

    audio_path = existing_audio_path(chunk.get("audio_path"))
    if audio_path is None:
        raise RuntimeError("Audio file not found")

    if audio_path.suffix.lower() == ".wav":
        wav_path = audio_path.with_name(f"{audio_path.stem}_16k.wav")
    else:
        wav_path = audio_path.with_suffix(".wav")
    asr.convert_to_wav(audio_path, wav_path)
    store.update_chunk(chunk["id"], wav_path=str(wav_path))
    return wav_path


def asr_engine_for_model(model_name: str) -> FasterWhisperASR:
    if model_name == asr.model_name:
        return asr
    engine = asr_engines.get(model_name)
    if engine is None:
        if asr_model_backend(model_name) == "mlx":
            engine = MlxWhisperASR(
                model_name=asr_base_model_name(model_name),
                repo_id=asr_model_repos(model_name)[0],
                display_name=model_name,
            )
        else:
            runtime_model = model_name
            for repo_id in asr_model_repos(model_name):
                if (asr_repo_cache_path(repo_id, ASR_MODEL_DIR) / "snapshots").exists():
                    runtime_model = repo_id
                    break
            engine = FasterWhisperASR(model_name=runtime_model)
        asr_engines[model_name] = engine
    return engine


def asr_model_label(model_name: str) -> str:
    meta = (MLX_ASR_MODEL_CATALOG if asr_model_backend(model_name) == "mlx" else ASR_MODEL_CATALOG).get(model_name)
    return str((meta or {}).get("label") or model_name)


def loaded_asr_engine_items() -> list[tuple[str, FasterWhisperASR]]:
    items: list[tuple[str, FasterWhisperASR]] = []
    if asr.loaded:
        items.append((ASR_MODEL, asr))
    for model_name, engine in asr_engines.items():
        if engine.loaded:
            items.append((model_name, engine))
    return items


def require_loaded_asr_engine(model_name: str) -> FasterWhisperASR:
    engine = asr_engine_for_model(model_name)
    if not engine.loaded:
        raise ASRUnavailable("识别模型尚未加载，请先在设置中加载模型。")
    return engine


@app.post("/api/models/load")
async def load_model(payload: ModelDownloadRequest) -> Dict[str, Any]:
    kind = clean_identifier(payload.kind or "asr", "asr")
    if kind != "asr":
        raise HTTPException(status_code=400, detail="当前只支持加载语音识别模型。")
    requested_model = resolve_asr_model(payload.model)

    def load_in_thread() -> Dict[str, Any]:
        with model_load_lock:
            unloaded = []
            for loaded_model, engine in loaded_asr_engine_items():
                if loaded_model == requested_model:
                    continue
                engine.unload()
                unloaded.append({
                    "model": loaded_model,
                    "label": asr_model_label(loaded_model),
                })

            engine = asr_engine_for_model(requested_model)
            engine.load()
            return {
                "kind": "asr",
                "model": requested_model,
                "label": asr_model_label(requested_model),
                "unloaded": unloaded,
                "status": engine.status(),
                "catalog": model_catalog(),
            }

    try:
        return await asyncio.to_thread(load_in_thread)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"模型加载失败：{exc}")


async def reprocess_asr_version(
    meeting_id: str,
    job_id: str,
    version_id: str,
    language: str,
    model_name: str,
    speaker_mode: str,
    make_current: bool,
) -> None:
    inserted_total = 0
    assigned_total = 0
    created_total = 0
    try:
        asr_engine = require_loaded_asr_engine(model_name)
        meeting = store.get_meeting(meeting_id)
        chunks = [
            chunk
            for chunk in (meeting.get("chunks") or [])
            if existing_audio_path(chunk.get("wav_path"), chunk.get("audio_path")) is not None
        ]
        store.delete_segments_for_version(meeting_id, version_id)
        store.update_transcript_version_status(meeting_id, version_id, "running")
        set_reprocess_state(
            job_id,
            status="running",
            stage="文字校准",
            progress=0,
            total=len(chunks),
        )

        recent_context = ""
        for index, chunk in enumerate(chunks, start=1):
            meeting_for_prompt = store.get_meeting(meeting_id)
            set_reprocess_state(
                job_id,
                stage=f"文字校准 {index}/{len(chunks)}",
                progress=index - 1,
                total=len(chunks),
                chunk_id=chunk.get("id"),
            )
            wav_path = await asyncio.to_thread(prepare_chunk_wav, chunk)
            result = await asyncio.to_thread(
                asr_engine.transcribe,
                wav_path,
                language,
                asr_context_prompt(
                    meeting_for_prompt,
                    recent_context,
                    prompt_settings.get("asr_context", ""),
                ),
            )

            active_diarizer = diarizer_for_mode(speaker_mode)
            if active_diarizer is not None:
                try:
                    turns = await asyncio.to_thread(active_diarizer.diarize, wav_path)
                    if speaker_mode == "diarization" or not speaker_tracker.enabled:
                        result["segments"] = assign_speakers(result["segments"], turns)
                        assigned_total += len(result["segments"])
                except DiarizationUnavailable:
                    pass

            if speaker_mode == "off":
                result["segments"] = clear_segment_speakers(result.get("segments") or [])

            use_speaker_tracking = speaker_tracker.enabled and speaker_mode in {
                "voiceprint",
                "auto",
            }
            if use_speaker_tracking:
                try:
                    result["segments"], speaker_result = await asyncio.to_thread(
                        speaker_tracker.assign_segments,
                        store,
                        meeting_id,
                        wav_path,
                        result["segments"],
                    )
                    assigned_total += int(speaker_result.get("assigned") or 0)
                    created_total += int(speaker_result.get("created") or 0)
                except SpeakerTrackingUnavailable:
                    pass

            inserted = store.add_segments(
                meeting_id,
                chunk["id"],
                result.get("segments") or [],
                version_id=version_id,
            )
            inserted_total += len(inserted)
            recent_context = "\n".join(
                segment.get("text", "")
                for segment in inserted[-8:]
                if segment.get("text")
            ) or recent_context
            set_reprocess_state(
                job_id,
                progress=index,
                inserted_segments=inserted_total,
            )

        settings = {
            "model": model_name,
            "language": language,
            "speaker_mode": speaker_mode,
            "inserted_segments": inserted_total,
            "assigned_segments": assigned_total,
            "created_speakers": created_total,
        }
        store.update_transcript_version_status(meeting_id, version_id, "ready", settings)
        if make_current:
            store.set_active_transcript_version(meeting_id, version_id)
        set_reprocess_state(
            job_id,
            status="done",
            stage="done",
            progress=len(chunks),
            inserted_segments=inserted_total,
            assigned_segments=assigned_total,
            created_speakers=created_total,
        )
    except Exception as exc:
        try:
            store.update_transcript_version_status(
                meeting_id,
                version_id,
                "error",
                {"error": str(exc), "inserted_segments": inserted_total},
            )
        except Exception:
            pass
        set_reprocess_state(job_id, status="error", stage="error", error=str(exc))


async def reprocess_speaker_version(
    meeting_id: str,
    job_id: str,
    version_id: str,
    source_version_id: str,
    speaker_mode: str,
    reset_speakers: bool,
    make_current: bool,
) -> None:
    inserted_total = 0
    created_total = 0
    assigned_total = 0
    errors: list[str] = []
    try:
        store.delete_segments_for_version(meeting_id, version_id)
        store.update_transcript_version_status(meeting_id, version_id, "running")
        if reset_speakers:
            store.delete_speakers(meeting_id)

        meeting = store.get_meeting(meeting_id)
        source_segments = store.get_segments_for_version(meeting_id, source_version_id)
        chunks_by_id = {chunk.get("id"): chunk for chunk in (meeting.get("chunks") or [])}
        segments_by_chunk: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
        for segment in source_segments:
            if segment.get("chunk_id"):
                segments_by_chunk[str(segment["chunk_id"])].append(segment)

        chunk_items = [
            (chunk_id, chunks_by_id.get(chunk_id), segments)
            for chunk_id, segments in segments_by_chunk.items()
            if chunks_by_id.get(chunk_id) is not None
        ]
        set_reprocess_state(
            job_id,
            status="running",
            stage="说话人校准",
            progress=0,
            total=len(chunk_items),
        )

        for index, (chunk_id, chunk, segments) in enumerate(chunk_items, start=1):
            set_reprocess_state(
                job_id,
                stage=f"说话人校准 {index}/{len(chunk_items)}",
                progress=index - 1,
                total=len(chunk_items),
                chunk_id=chunk_id,
            )
            assigned_segments = segments
            try:
                wav_path = await asyncio.to_thread(prepare_chunk_wav, chunk)
                active_diarizer = diarizer_for_mode(speaker_mode)
                if active_diarizer is not None:
                    turns = await asyncio.to_thread(active_diarizer.diarize, wav_path)
                    if speaker_mode == "diarization" or not speaker_tracker.enabled:
                        assigned_segments = assign_speakers(segments, turns)
                        assigned_total += len(assigned_segments)

                if speaker_mode == "off":
                    assigned_segments = clear_segment_speakers(segments)

                use_speaker_tracking = speaker_tracker.enabled and speaker_mode in {
                    "voiceprint",
                    "auto",
                }
                if use_speaker_tracking:
                    assigned_segments, speaker_result = await asyncio.to_thread(
                        speaker_tracker.assign_segments,
                        store,
                        meeting_id,
                        wav_path,
                        segments,
                    )
                    assigned_total += int(speaker_result.get("assigned") or 0)
                    created_total += int(speaker_result.get("created") or 0)
            except Exception as exc:
                errors.append(str(exc))

            inserted = store.add_segments(
                meeting_id,
                chunk_id,
                assigned_segments,
                version_id=version_id,
            )
            inserted_total += len(inserted)
            set_reprocess_state(
                job_id,
                progress=index,
                inserted_segments=inserted_total,
                assigned_segments=assigned_total,
                created_speakers=created_total,
            )

        settings = {
            "source_version_id": source_version_id,
            "speaker_mode": speaker_mode,
            "reset_speakers": reset_speakers,
            "inserted_segments": inserted_total,
            "assigned_segments": assigned_total,
            "created_speakers": created_total,
            "errors": errors[:8],
        }
        store.update_transcript_version_status(meeting_id, version_id, "ready", settings)
        if make_current:
            store.set_active_transcript_version(meeting_id, version_id)
        set_reprocess_state(
            job_id,
            status="done",
            stage="done",
            progress=len(chunk_items),
            inserted_segments=inserted_total,
            assigned_segments=assigned_total,
            created_speakers=created_total,
            error="; ".join(errors[:2]) if errors else "",
        )
    except Exception as exc:
        try:
            store.update_transcript_version_status(
                meeting_id,
                version_id,
                "error",
                {"error": str(exc), "inserted_segments": inserted_total},
            )
        except Exception:
            pass
        set_reprocess_state(job_id, status="error", stage="error", error=str(exc))


def repair_batches(segments: list[Dict[str, Any]]) -> list[list[Dict[str, Any]]]:
    batches: list[list[Dict[str, Any]]] = []
    current: list[Dict[str, Any]] = []
    current_chars = 0
    for segment in segments:
        text = str(segment.get("text") or "")
        if current and (len(current) >= 24 or current_chars + len(text) > 2600):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += len(text)
    if current:
        batches.append(current)
    return batches


async def reprocess_llm_repair_version(
    meeting_id: str,
    job_id: str,
    version_id: str,
    source_version_id: str,
    make_current: bool,
) -> None:
    inserted_total = 0
    changed_total = 0
    repair_errors: list[str] = []
    try:
        store.delete_segments_for_version(meeting_id, version_id)
        store.update_transcript_version_status(meeting_id, version_id, "running")
        source_segments = store.get_segments_for_version(meeting_id, source_version_id)
        batches = repair_batches(source_segments)
        set_reprocess_state(
            job_id,
            status="running",
            stage="LLM 精修",
            progress=0,
            total=len(batches),
        )

        for index, batch in enumerate(batches, start=1):
            set_reprocess_state(
                job_id,
                stage=f"精修 {index}/{len(batches)}",
                progress=index - 1,
                total=len(batches),
            )
            repair_map: Dict[str, str] = {}
            try:
                meeting_for_prompt = store.get_meeting(meeting_id)
                repair_map = await asyncio.wait_for(
                    summarizer.repair_segments(batch, meeting_for_prompt),
                    timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                repair_errors.append(str(exc))

            repaired_segments = []
            for segment in batch:
                repaired_text = repair_map.get(str(segment.get("id") or ""))
                if repaired_text and repaired_text.strip() != str(segment.get("text") or "").strip():
                    changed_total += 1
                    repaired_segments.append({**segment, "text": repaired_text.strip()})
                else:
                    repaired_segments.append(segment)

            by_chunk: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
            for segment in repaired_segments:
                if segment.get("chunk_id"):
                    by_chunk[str(segment["chunk_id"])].append(segment)
            for chunk_id, chunk_segments in by_chunk.items():
                inserted = store.add_segments(
                    meeting_id,
                    chunk_id,
                    chunk_segments,
                    version_id=version_id,
                )
                inserted_total += len(inserted)
            set_reprocess_state(
                job_id,
                progress=index,
                inserted_segments=inserted_total,
                changed_segments=changed_total,
            )

        settings = {
            "source_version_id": source_version_id,
            "inserted_segments": inserted_total,
            "changed_segments": changed_total,
            "repair_errors": repair_errors[:8],
        }
        store.update_transcript_version_status(meeting_id, version_id, "ready", settings)
        if make_current:
            store.set_active_transcript_version(meeting_id, version_id)
        set_reprocess_state(
            job_id,
            status="done",
            stage="done",
            progress=len(batches),
            inserted_segments=inserted_total,
            changed_segments=changed_total,
            error="; ".join(repair_errors[:2]) if repair_errors else "",
        )
    except Exception as exc:
        try:
            store.update_transcript_version_status(
                meeting_id,
                version_id,
                "error",
                {"error": str(exc), "inserted_segments": inserted_total},
            )
        except Exception:
            pass
        set_reprocess_state(job_id, status="error", stage="error", error=str(exc))


async def reprocess_final_notes(meeting_id: str, job_id: str, force_local: bool) -> None:
    set_reprocess_state(job_id, status="running", stage="生成纪要", progress=0, total=1)
    try:
        meeting = store.get_meeting(meeting_id)
        source = transcript_source(meeting)
        if force_local:
            markdown = build_local_markdown(meeting)
        else:
            markdown = await asyncio.wait_for(
                summarizer.finalize(meeting),
                timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
            )
        store.set_final_markdown(meeting_id, markdown, source)
        set_reprocess_state(job_id, status="done", stage="done", progress=1)
    except asyncio.TimeoutError:
        meeting = store.get_meeting(meeting_id)
        markdown = build_local_markdown(meeting)
        store.set_final_markdown(meeting_id, markdown, transcript_source(meeting))
        set_reprocess_state(
            job_id,
            status="error",
            stage="timeout",
            progress=1,
            error=f"Final notes timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds; local notes were saved.",
        )
    except Exception as exc:
        set_reprocess_state(job_id, status="error", stage="error", error=str(exc))


async def update_summary_for_segments(meeting_id: str, inserted: list[Dict[str, Any]]) -> None:
    if not REALTIME_SUMMARY_ENABLED:
        summary_states.pop(meeting_id, None)
        return
    async with summary_lock:
        set_summary_state(meeting_id, "updating")
        meeting: Optional[Dict[str, Any]] = None
        try:
            meeting = store.get_meeting(meeting_id)
            inserted_ids = {str(segment.get("id") or "") for segment in inserted}
            previous_source = transcript_source_without_segments(meeting, inserted_ids)
            current_source = transcript_source(meeting)
            existing_hash = str(meeting.get("summary_source_hash") or "")
            can_update_incrementally = (
                existing_hash == previous_source["hash"]
                or (
                    not existing_hash
                    and str(meeting.get("active_version_id") or "auto") == "auto"
                    and bool((meeting.get("summary") or {}).get("summary"))
                )
            )
            if existing_hash == current_source["hash"]:
                updated_summary = meeting["summary"]
            elif can_update_incrementally:
                updated_summary = await asyncio.wait_for(
                    summarizer.update(meeting["summary"], inserted, meeting),
                    timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
                )
            else:
                updated_summary = await asyncio.wait_for(
                    summarizer.rebuild(meeting),
                    timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
                )
            store.set_summary(meeting_id, updated_summary, current_source)
            set_summary_state(meeting_id, "done")
        except asyncio.TimeoutError:
            if meeting is None:
                meeting = store.get_meeting(meeting_id)
            fallback = fallback_incremental_summary(
                meeting["summary"],
                inserted,
                f"summary task timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds",
            )
            store.set_summary(meeting_id, fallback, transcript_source(meeting))
            set_summary_state(
                meeting_id,
                "error",
                f"Summary task timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds.",
            )
        except Exception as exc:
            set_summary_state(meeting_id, "error", str(exc))


@app.post("/api/meetings/{meeting_id}/finalize/stream")
async def finalize_meeting_stream(meeting_id: str, payload: FinalizeRequest) -> StreamingResponse:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    source = transcript_source(meeting)

    async def event_stream() -> Any:
        markdown = ""
        try:
            if payload.force_local:
                markdown = build_local_markdown(meeting)
                yield sse_event("replace", {"markdown": markdown})
            else:
                async for item in summarizer.finalize_stream(meeting):
                    kind = item.get("type")
                    if kind == "chunk":
                        text = str(item.get("text") or "")
                        if text:
                            yield sse_event("chunk", {"text": text})
                    elif kind == "replace":
                        markdown = str(item.get("markdown") or "")
                        yield sse_event("replace", {"markdown": markdown})
                    elif kind == "done":
                        if item.get("markdown"):
                            markdown = str(item["markdown"])

            if not markdown.strip():
                markdown = build_local_markdown(meeting)
                yield sse_event("replace", {"markdown": markdown})
            store.set_final_markdown(meeting_id, markdown, source)
            yield sse_event("done", {"meeting": store.get_meeting(meeting_id)})
        except Exception as exc:
            fallback = build_local_markdown(meeting)
            store.set_final_markdown(meeting_id, fallback, source)
            yield sse_event("replace", {"markdown": fallback})
            yield sse_event(
                "done",
                {
                    "meeting": store.get_meeting(meeting_id),
                    "warning": str(exc),
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/meetings/{meeting_id}/finalize")
async def finalize_meeting(meeting_id: str, payload: FinalizeRequest) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    source = transcript_source(meeting)
    if payload.force_local:
        markdown = build_local_markdown(meeting)
    else:
        try:
            markdown = await asyncio.wait_for(
                summarizer.finalize(meeting),
                timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            markdown = build_local_markdown(meeting)
    store.set_final_markdown(meeting_id, markdown, source)
    return store.get_meeting(meeting_id)


@app.post("/api/meetings/{meeting_id}/stop")
async def stop_meeting(meeting_id: str) -> Dict[str, Any]:
    try:
        store.update_meeting_status(meeting_id, "stopped")
        return store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.get("/api/meetings/{meeting_id}/export.md", response_class=PlainTextResponse)
async def export_markdown(meeting_id: str) -> str:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    if final_notes_current(meeting):
        return notes_only_markdown(meeting["final_markdown"]) + "\n"
    return build_local_markdown(meeting)


@app.get("/api/meetings/{meeting_id}/transcript.md", response_class=PlainTextResponse)
async def export_transcript_markdown(meeting_id: str) -> str:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    return build_transcript_markdown(meeting)
