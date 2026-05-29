from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path

import PyInstaller.__main__


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
        "faster_whisper",
        "--hidden-import",
        "ctranslate2",
        "--hidden-import",
        "tokenizers",
        "--hidden-import",
        "huggingface_hub",
        "--hidden-import",
        "funasr",
        "--hidden-import",
        "modelscope",
        "--hidden-import",
        "resemblyzer",
        "--hidden-import",
        "opencc",
        "--hidden-import",
        "imageio_ffmpeg",
        "--collect-all",
        "faster_whisper",
        "--collect-all",
        "ctranslate2",
        "--collect-all",
        "tokenizers",
        "--collect-all",
        "av",
        "--collect-all",
        "imageio_ffmpeg",
        "--copy-metadata",
        "faster-whisper",
        "--copy-metadata",
        "ctranslate2",
        "--copy-metadata",
        "tokenizers",
        "--copy-metadata",
        "huggingface-hub",
        "--copy-metadata",
        "funasr",
        "--copy-metadata",
        "modelscope",
        "--copy-metadata",
        "tqdm",
        "--distpath",
        str(backend_dir / "dist"),
        "--workpath",
        str(backend_dir / "build"),
        "--noconfirm",
        "--clean",
    ]

    if platform.system() == "Windows":
        args.append("--noconsole")

    if platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        args.extend(
            [
                "--hidden-import",
                "mlx",
                "--hidden-import",
                "mlx.core",
                "--hidden-import",
                "mlx_whisper",
                "--hidden-import",
                "tiktoken",
                "--collect-all",
                "mlx",
                "--collect-all",
                "mlx_whisper",
                "--collect-all",
                "tiktoken",
                "--collect-all",
                "regex",
                "--copy-metadata",
                "mlx",
                "--copy-metadata",
                "mlx-whisper",
                "--copy-metadata",
                "tiktoken",
            ]
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
