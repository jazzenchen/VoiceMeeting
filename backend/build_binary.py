from __future__ import annotations

import argparse
import os
import platform
from pathlib import Path

import PyInstaller.__main__


def _is_apple_mlx_platform() -> bool:
    return platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}


def _add_options(args: list[str], flag: str, values: list[str]) -> None:
    for value in values:
        args.extend([flag, value])


def build_server() -> None:
    backend_dir = Path(__file__).resolve().parent
    args = [
        "server.py",
        "--onefile",
        "--name",
        "voice-meeting-server",
        "--hidden-import",
        "backend",
        "--hidden-import",
        "backend.main",
        "--hidden-import",
        "backend.asr",
        "--hidden-import",
        "backend.config",
        "--hidden-import",
        "backend.diarization",
        "--hidden-import",
        "backend.llm",
        "--hidden-import",
        "backend.media_tools",
        "--hidden-import",
        "backend.speaker_tracker",
        "--hidden-import",
        "backend.storage",
        "--hidden-import",
        "backend.summarizer",
        "--hidden-import",
        "backend.transcript",
        "--hidden-import",
        "backend.vibearound",
        "--hidden-import",
        "fastapi",
        "--hidden-import",
        "uvicorn",
        "--hidden-import",
        "huggingface_hub",
        "--hidden-import",
        "resemblyzer",
        "--hidden-import",
        "opencc",
        "--hidden-import",
        "imageio_ffmpeg",
        "--hidden-import",
        "numpy",
        "--hidden-import",
        "torch",
        "--hidden-import",
        "litellm",
        "--collect-all",
        "resemblyzer",
        "--collect-all",
        "imageio_ffmpeg",
        "--copy-metadata",
        "huggingface-hub",
        "--copy-metadata",
        "resemblyzer",
        "--copy-metadata",
        "tqdm",
        "--copy-metadata",
        "litellm",
        "--copy-metadata",
        "openai",
        "--distpath",
        str(backend_dir / "dist"),
        "--workpath",
        str(backend_dir / "build"),
        "--noconfirm",
        "--clean",
    ]

    if platform.system() == "Windows":
        args.append("--noconsole")

    if _is_apple_mlx_platform():
        _add_options(args, "--hidden-import", ["mlx", "mlx.core", "mlx_whisper", "tiktoken"])
        _add_options(args, "--collect-all", ["mlx", "mlx_whisper", "tiktoken", "regex"])
        _add_options(args, "--copy-metadata", ["mlx", "mlx-whisper", "tiktoken"])
        _add_options(
            args,
            "--exclude-module",
            [
                "av",
                "ctranslate2",
                "faster_whisper",
                "funasr",
                "grpc",
                "grpc_tools",
                "hydra",
                "lightning",
                "lightning_fabric",
                "matplotlib",
                "modelscope",
                "onnxruntime",
                "pandas",
                "PIL",
                "pyannote",
                "pyannote.audio",
                "pyannote.core",
                "pyannote.database",
                "pyannote.pipeline",
                "sklearn",
                "sqlalchemy",
                "sympy",
                "tokenizers",
                "torchcodec",
                "torchaudio",
                "transformers",
            ],
        )
    else:
        _add_options(
            args,
            "--hidden-import",
            [
                "faster_whisper",
                "ctranslate2",
                "tokenizers",
                "funasr",
                "modelscope",
                "torchaudio",
                "torchcodec",
                "pyannote.audio",
                "pyannote.audio.pipelines",
            ],
        )
        _add_options(
            args,
            "--collect-all",
            [
                "faster_whisper",
                "ctranslate2",
                "tokenizers",
                "av",
                "pyannote.audio",
                "pyannote.core",
                "pyannote.database",
                "pyannote.pipeline",
            ],
        )
        _add_options(
            args,
            "--copy-metadata",
            [
                "torchcodec",
                "faster-whisper",
                "ctranslate2",
                "tokenizers",
                "funasr",
                "modelscope",
                "pyannote.audio",
                "pyannote.core",
                "pyannote.database",
                "pyannote.pipeline",
            ],
        )

    os.chdir(backend_dir)
    PyInstaller.__main__.run(args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build VoiceMeeting sidecar binaries")
    parser.parse_args()
    build_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
