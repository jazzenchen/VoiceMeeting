from __future__ import annotations

import re
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, Optional

from .media_tools import ffprobe_path
from .transcript import UNRECOGNIZED_TEXT


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


def clear_segment_speakers(segments: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [{**segment, "speaker": ""} for segment in segments]


def coerce_ms(value: Any, *, minimum: int = 0) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(round(float(str(value).strip())))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= minimum else None


def chunk_duration_for_placeholder(chunk: Dict[str, Any]) -> int:
    duration = coerce_ms(chunk.get("duration_ms"), minimum=1)
    if duration is not None:
        return duration
    started = coerce_ms(chunk.get("started_at_ms"))
    ended = coerce_ms(chunk.get("ended_at_ms"))
    if started is not None and ended is not None and ended > started:
        return ended - started
    path = existing_audio_path(chunk.get("wav_path"), chunk.get("audio_path"))
    if path is not None:
        probed = audio_duration_ms(path)
        if probed and probed > 0:
            return probed
    return 1000


def unrecognized_segment_for_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "start_ms": 0,
        "end_ms": chunk_duration_for_placeholder(chunk),
        "speaker": "",
        "text": UNRECOGNIZED_TEXT,
        "confidence": None,
    }


def segments_or_unrecognized(chunk: Dict[str, Any], segments: Optional[list[Dict[str, Any]]]) -> list[Dict[str, Any]]:
    recognized = [segment for segment in (segments or []) if str(segment.get("text") or "").strip()]
    return recognized if recognized else [unrecognized_segment_for_chunk(chunk)]


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
