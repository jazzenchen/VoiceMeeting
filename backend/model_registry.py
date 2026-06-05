from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .asr import MLX_MODEL_REPOS
from .config import APPLE_MLX_PLATFORM, ASR_BACKENDS, ASR_MODEL_DIR, FUNASR_MODEL_DIR, MLX_ASR_MODEL_DIR, MODELS_DIR


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

MAC_MLX_ENABLED = APPLE_MLX_PLATFORM and "mlx" in ASR_BACKENDS
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
FUNASR_MODEL_CATALOG: Dict[str, Dict[str, Any]] = {
    "funasr-sensevoice-small": {
        "label": "FunASR SenseVoice",
        "params": "主模型 234M",
        "disk": "约 1.2GB",
        "file_breakdown": "主模型约 897MB + VAD约 4MB + 标点约 283MB",
        "components": "SenseVoiceSmall + fsmn-vad + ct-punc-c",
        "profile": "实验选项，适合中文和中英混合会议；辅助使用 fsmn-vad 与 ct-punc-c",
        "model_id": "iic/SenseVoiceSmall",
        "repo_id": "iic/SenseVoiceSmall",
    },
    "funasr-paraformer-zh": {
        "label": "FunASR Paraformer",
        "params": "主模型 220M",
        "disk": "约 1.2GB",
        "file_breakdown": "主模型约 953MB + VAD约 4MB + 标点约 283MB",
        "components": "paraformer-zh + fsmn-vad + ct-punc-c",
        "profile": "实验选项，中文会议速度优先；辅助使用 fsmn-vad 与 ct-punc-c",
        "model_id": "paraformer-zh",
        "repo_id": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    },
}
SUPPORTED_ASR_MODELS = (
    (set(ASR_MODEL_CATALOG) if "faster-whisper" in ASR_BACKENDS else set())
    | (set(MLX_ASR_MODEL_CATALOG) if MAC_MLX_ENABLED else set())
    | (set(FUNASR_MODEL_CATALOG) if "funasr" in ASR_BACKENDS else set())
)
ASR_MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "dropbox-dash/faster-whisper-large-v3-turbo",
}
FUNASR_MODEL_MARKER_DIR = FUNASR_MODEL_DIR / ".installed"
PYANNOTE_COMMUNITY_MODEL_ID = "pyannote-community-1"
PYANNOTE_COMMUNITY_REPO_ID = "pyannote/speaker-diarization-community-1"
PYANNOTE_MODEL_DIR = MODELS_DIR / "pyannote" / "speaker-diarization-community-1"


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


def discovered_asr_models() -> set[str]:
    models: set[str] = set()
    for model_name in SUPPORTED_ASR_MODELS:
        if asr_model_backend(model_name) == "funasr":
            if funasr_model_cache_ready(model_name):
                models.add(model_name)
        elif any(asr_model_cache_ready(path) for path in asr_model_cache_paths(model_name)):
            models.add(model_name)
    return models


def asr_model_backend(model_name: str) -> str:
    if model_name.startswith("funasr-"):
        return "funasr"
    return "mlx" if model_name.startswith("mlx-") else "faster-whisper"


def asr_base_model_name(model_name: str) -> str:
    return model_name.removeprefix("mlx-").removeprefix("funasr-")


def asr_model_cache_dir(model_name: str) -> Path:
    backend = asr_model_backend(model_name)
    if backend == "mlx":
        return MLX_ASR_MODEL_DIR
    if backend == "funasr":
        return FUNASR_MODEL_DIR
    return ASR_MODEL_DIR


def asr_model_repos(model_name: str) -> list[str]:
    base_name = asr_base_model_name(model_name)
    if asr_model_backend(model_name) == "mlx":
        return [MLX_MODEL_REPOS.get(base_name, f"mlx-community/whisper-{base_name}-mlx")]
    if asr_model_backend(model_name) == "funasr":
        return [str(FUNASR_MODEL_CATALOG.get(model_name, {}).get("repo_id") or model_name)]
    return [ASR_MODEL_REPOS.get(base_name, f"Systran/faster-whisper-{base_name}")]


def asr_repo_cache_path(repo_id: str, cache_dir: Path = ASR_MODEL_DIR) -> Path:
    return cache_dir / f"models--{repo_id.replace('/', '--')}"


def asr_model_cache_paths(model_name: str) -> list[Path]:
    if asr_model_backend(model_name) == "funasr":
        return funasr_model_cache_paths(model_name)
    cache_dir = asr_model_cache_dir(model_name)
    return [asr_repo_cache_path(repo_id, cache_dir) for repo_id in asr_model_repos(model_name)]


def funasr_model_marker_path(model_name: str) -> Path:
    return FUNASR_MODEL_MARKER_DIR / f"{model_name}.json"


def funasr_model_cache_paths(model_name: str) -> list[Path]:
    paths = [funasr_model_marker_path(model_name)]
    for repo_id in asr_model_repos(model_name):
        if "/" in repo_id:
            org, name = repo_id.split("/", 1)
            paths.append(FUNASR_MODEL_DIR / "modelscope" / "hub" / "models" / org / name)
            paths.append(FUNASR_MODEL_DIR / "huggingface" / "hub" / f"models--{org}--{name}")
        else:
            paths.append(FUNASR_MODEL_DIR / "modelscope" / "hub" / "models" / repo_id)
    return paths


def funasr_model_cache_ready(model_name: str) -> bool:
    marker = funasr_model_marker_path(model_name)
    if marker.is_file():
        return True
    return any(
        path.exists() and directory_size_bytes(path) > 1024 * 1024
        for path in funasr_model_cache_paths(model_name)
        if path != marker
    )


def mark_funasr_model_installed(model_name: str) -> None:
    FUNASR_MODEL_MARKER_DIR.mkdir(parents=True, exist_ok=True)
    funasr_model_marker_path(model_name).write_text(
        json.dumps(
            {
                "model": model_name,
                "repo_id": asr_model_repos(model_name)[0],
                "installed_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


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
    if asr_model_backend(model_name) == "funasr":
        for path in paths:
            if path.exists():
                return path
        return FUNASR_MODEL_DIR
    for path in paths:
        if asr_model_cache_ready(path):
            return path
    return paths[0]


def local_pyannote_model_path() -> Optional[Path]:
    return PYANNOTE_MODEL_DIR if (PYANNOTE_MODEL_DIR / "config.yaml").exists() else None
