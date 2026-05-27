from __future__ import annotations

import os
import shutil
from functools import lru_cache
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
def ffprobe_path() -> Optional[str]:
    env_path = os.environ.get("VOICE_MEETING_FFPROBE")
    if env_path:
        return env_path
    return shutil.which("ffprobe")
