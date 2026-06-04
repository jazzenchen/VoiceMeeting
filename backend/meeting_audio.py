from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .config import MEETINGS_DIR
from .transcription_helpers import audio_duration_ms, existing_audio_path


SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1


class MeetingAudioError(RuntimeError):
    pass


def meeting_audio_path(meeting_id: str) -> Path:
    return MEETINGS_DIR / meeting_id / "meeting.wav"


def _coerce_ms(value: Any, *, minimum: int = 0) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(round(float(str(value).strip())))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= minimum else None


def _chunk_sort_key(chunk: Dict[str, Any]) -> tuple[bool, int, int]:
    started = _coerce_ms(chunk.get("started_at_ms"))
    seq = int(chunk.get("seq") or 0)
    return started is None, started if started is not None else seq, seq


def _source_wav_chunks(chunks: Iterable[Dict[str, Any]]) -> list[tuple[Dict[str, Any], Path]]:
    sources: list[tuple[Dict[str, Any], Path]] = []
    for chunk in sorted(chunks, key=_chunk_sort_key):
        path = existing_audio_path(chunk.get("wav_path"))
        if path is not None:
            sources.append((chunk, path))
    return sources


def _frame_for_ms(ms: int) -> int:
    return int(round(ms * SAMPLE_RATE / 1000))


def _ms_for_frame(frame: int) -> int:
    return int(round(frame * 1000 / SAMPLE_RATE))


def _validate_wav(path: Path) -> tuple[int, int, int, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
    except wave.Error as exc:
        raise MeetingAudioError(f"Cannot read wav chunk {path.name}: {exc}") from exc

    if channels != CHANNELS or sample_width != SAMPLE_WIDTH or sample_rate != SAMPLE_RATE:
        raise MeetingAudioError(
            f"Unexpected wav format for {path.name}: "
            f"{channels}ch/{sample_width * 8}bit/{sample_rate}Hz"
        )
    return channels, sample_width, sample_rate, frame_count


def _chunk_write_frames(chunk: Dict[str, Any], source_frame_count: int, source_duration_ms: int) -> int:
    duration_ms = _coerce_ms(chunk.get("duration_ms"), minimum=1)
    started_ms = _coerce_ms(chunk.get("started_at_ms"))
    ended_ms = _coerce_ms(chunk.get("ended_at_ms"))
    if started_ms is not None and ended_ms is not None and ended_ms > started_ms:
        duration_ms = ended_ms - started_ms
    if duration_ms is None:
        duration_ms = source_duration_ms
    return max(0, min(source_frame_count, _frame_for_ms(duration_ms)))


def build_meeting_audio(chunks: Iterable[Dict[str, Any]], output_path: Path) -> Optional[Dict[str, Any]]:
    sources = _source_wav_chunks(chunks)
    if not sources:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="meeting-", suffix=".wav", dir=str(output_path.parent))
    os.close(fd)
    temp_path = Path(temp_name)

    written_chunks = 0
    cursor_frame = 0
    try:
        with wave.open(str(temp_path), "wb") as output:
            output.setnchannels(CHANNELS)
            output.setsampwidth(SAMPLE_WIDTH)
            output.setframerate(SAMPLE_RATE)

            for chunk, source_path in sources:
                _channels, _sample_width, _sample_rate, source_frame_count = _validate_wav(source_path)
                source_duration_ms = _ms_for_frame(source_frame_count)
                start_ms = _coerce_ms(chunk.get("started_at_ms"))
                if start_ms is None:
                    start_frame = cursor_frame
                else:
                    start_frame = _frame_for_ms(start_ms)

                write_frame_count = _chunk_write_frames(chunk, source_frame_count, source_duration_ms)
                if write_frame_count <= 0:
                    continue

                if start_frame > cursor_frame:
                    output.writeframes(b"\x00" * (start_frame - cursor_frame) * SAMPLE_WIDTH)
                    cursor_frame = start_frame

                skip_frames = max(0, cursor_frame - start_frame)
                if skip_frames >= write_frame_count:
                    continue

                frames_to_write = write_frame_count - skip_frames
                with wave.open(str(source_path), "rb") as source:
                    source.setpos(skip_frames)
                    output.writeframes(source.readframes(frames_to_write))

                cursor_frame += frames_to_write
                written_chunks += 1

        if written_chunks <= 0 or cursor_frame <= 0:
            temp_path.unlink(missing_ok=True)
            return None

        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return {
        "path": str(output_path),
        "duration_ms": _ms_for_frame(cursor_frame),
        "chunk_count": written_chunks,
    }


def ensure_meeting_audio(chunks: Iterable[Dict[str, Any]], output_path: Path) -> Optional[Dict[str, Any]]:
    chunk_items = list(chunks)
    sources = _source_wav_chunks(chunk_items)
    if output_path.is_file():
        duration_ms = audio_duration_ms(output_path)
        if duration_ms and duration_ms > 0:
            if not sources:
                return {
                    "path": str(output_path),
                    "duration_ms": duration_ms,
                    "chunk_count": len(chunk_items),
                }
            latest_source_mtime = max(source_path.stat().st_mtime for _chunk, source_path in sources)
            if output_path.stat().st_mtime >= latest_source_mtime:
                return {
                    "path": str(output_path),
                    "duration_ms": duration_ms,
                    "chunk_count": len(sources),
                }

    if not sources:
        return None

    return build_meeting_audio((chunk for chunk, _path in sources), output_path)


def meeting_audio_waveform(path: Path, bins: int = 256) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    bin_count = max(32, min(2048, int(bins or 256)))
    _channels, sample_width, sample_rate, frame_count = _validate_wav(path)
    duration_ms = _ms_for_frame(frame_count)
    items = [
        {
            "peak": 0.0,
            "rms": 0.0,
            "samples": 0,
            "hasAudio": False,
        }
        for _index in range(bin_count)
    ]
    if frame_count <= 0:
        return {
            "duration_ms": 0,
            "sample_rate": sample_rate,
            "bins": items,
        }

    with wave.open(str(path), "rb") as handle:
        for index in range(bin_count):
            start_frame = int(frame_count * index / bin_count)
            end_frame = int(frame_count * (index + 1) / bin_count)
            frame_span = max(0, end_frame - start_frame)
            if frame_span <= 0:
                continue
            stride = max(1, frame_span // 480)
            handle.setpos(start_frame)
            payload = handle.readframes(frame_span)
            frame_width = sample_width * CHANNELS
            peak = 0.0
            total = 0.0
            count = 0
            for offset in range(0, len(payload), stride * frame_width):
                sample = int.from_bytes(payload[offset:offset + sample_width], "little", signed=True)
                value = abs(sample) / 32768.0
                peak = max(peak, value)
                total += value * value
                count += 1
            if count <= 0:
                continue
            items[index] = {
                "peak": peak,
                "rms": (total / count) ** 0.5,
                "samples": count,
                "hasAudio": peak > 0.0005,
            }

    return {
        "duration_ms": duration_ms,
        "sample_rate": sample_rate,
        "bins": items,
    }


def cleanup_chunk_audio_files(chunks: Iterable[Dict[str, Any]], preserved_path: Path) -> int:
    preserved = preserved_path.resolve()
    removed = 0
    seen: set[Path] = set()
    for chunk in chunks:
        for key in ("audio_path", "wav_path"):
            value = chunk.get(key)
            if not value:
                continue
            path = Path(str(value))
            if path in seen:
                continue
            seen.add(path)
            try:
                if not path.is_file() or path.resolve() == preserved:
                    continue
                path.unlink()
                removed += 1
            except Exception:
                continue
    return removed
