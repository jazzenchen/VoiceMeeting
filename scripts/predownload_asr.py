from pathlib import Path
import os
import platform


MLX_MODEL_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


def is_apple_mlx_platform() -> bool:
    return platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}


project_dir = Path(__file__).resolve().parents[1]

if is_apple_mlx_platform():
    from huggingface_hub import snapshot_download

    model_name = os.environ.get("VOICE_MEETING_ASR_MODEL", "mlx-small").removeprefix("mlx-")
    repo_id = MLX_MODEL_REPOS.get(model_name, f"mlx-community/whisper-{model_name}-mlx")
    model_dir = Path(os.environ.get("VOICE_MEETING_MLX_ASR_MODEL_DIR", project_dir / "models" / "mlx-whisper"))
    model_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=repo_id, cache_dir=str(model_dir))
    print(f"Downloaded MLX ASR model '{repo_id}' into {model_dir}")
else:
    from faster_whisper import WhisperModel

    model_name = os.environ.get("VOICE_MEETING_ASR_MODEL", "small")
    model_dir = Path(os.environ.get("VOICE_MEETING_ASR_MODEL_DIR", project_dir / "models" / "faster-whisper"))
    device = os.environ.get("VOICE_MEETING_ASR_DEVICE", "cpu")
    compute_type = os.environ.get("VOICE_MEETING_ASR_COMPUTE_TYPE", "int8")

    model_dir.mkdir(parents=True, exist_ok=True)
    WhisperModel(model_name, device=device, compute_type=compute_type, download_root=str(model_dir))
    print(f"Downloaded faster-whisper model '{model_name}' into {model_dir}")
