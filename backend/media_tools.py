from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional


@lru_cache(maxsize=1)
def ffmpeg_path() -> Optional[str]:
    env_path = os.environ.get("VOICE_MEETING_FFMPEG")
    if env_path:
        return env_path

    system_path = shutil.which("ffmpeg")
    if system_path:
        return system_path

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


@lru_cache(maxsize=1)
def prepare_ffmpeg_path() -> Optional[str]:
    path = ffmpeg_path()
    if not path:
        return None

    resolved = Path(path)
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", str(resolved))
    return str(resolved)


@lru_cache(maxsize=1)
def ffprobe_path() -> Optional[str]:
    env_path = os.environ.get("VOICE_MEETING_FFPROBE")
    if env_path:
        return env_path
    return shutil.which("ffprobe")
