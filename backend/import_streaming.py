from __future__ import annotations

import asyncio
import tempfile
import wave
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Tuple

from .asr import ASRUnavailable
from .diarization import DiarizationUnavailable, assign_speakers, split_segments_by_turns
from .meeting_audio import meeting_audio_waveform
from .speaker_tracker import SpeakerTracker, SpeakerTrackingUnavailable
from .storage import MeetingStore
from .transcript import is_unrecognized_text
from .transcription_helpers import (
    asr_context_prompt,
    audio_duration_ms,
    clear_segment_speakers,
    segments_or_unrecognized,
)


IMPORT_WAVEFORM_BARS = 256
IMPORT_WINDOW_GAP_MS = 1200
IMPORT_WINDOW_PAD_MS = 300
IMPORT_MAX_ASR_WINDOW_MS = 60_000
IMPORT_FALLBACK_WINDOW_MS = 60_000


def _coerce_ms(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return default


def _clean_vad_segments(vad_segments: List[Dict[str, Any]], duration_ms: int) -> List[Dict[str, int]]:
    cleaned: List[Dict[str, int]] = []
    for segment in vad_segments or []:
        start_ms = _coerce_ms(segment.get("start_ms"))
        end_ms = _coerce_ms(segment.get("end_ms"))
        if duration_ms > 0:
            start_ms = min(start_ms, duration_ms)
            end_ms = min(end_ms, duration_ms)
        if end_ms <= start_ms:
            continue
        if cleaned and start_ms <= cleaned[-1]["end_ms"]:
            cleaned[-1]["end_ms"] = max(cleaned[-1]["end_ms"], end_ms)
            continue
        cleaned.append({"start_ms": start_ms, "end_ms": end_ms})
    return cleaned


def _split_window(start_ms: int, end_ms: int, duration_ms: int) -> List[Dict[str, int]]:
    windows: List[Dict[str, int]] = []
    cursor = max(0, start_ms)
    limit = max(cursor, end_ms)
    while cursor < limit:
        next_end = min(limit, cursor + IMPORT_MAX_ASR_WINDOW_MS)
        if duration_ms > 0:
            next_end = min(next_end, duration_ms)
        if next_end > cursor:
            windows.append({"start_ms": cursor, "end_ms": next_end})
        cursor = next_end
        if duration_ms > 0 and cursor >= duration_ms:
            break
    return windows


def build_import_windows(
    asr_engine: Any,
    wav_path: Path,
    duration_ms: int,
) -> Tuple[List[Dict[str, int]], List[Dict[str, int]]]:
    try:
        vad_segments = _clean_vad_segments(asr_engine.detect_speech(wav_path), duration_ms)
    except Exception:
        vad_segments = []

    if not vad_segments:
        if duration_ms <= 0:
            return [], []
        windows = []
        for start_ms in range(0, duration_ms, IMPORT_FALLBACK_WINDOW_MS):
            end_ms = min(duration_ms, start_ms + IMPORT_FALLBACK_WINDOW_MS)
            if end_ms > start_ms:
                windows.append({"start_ms": start_ms, "end_ms": end_ms})
        return [], windows

    windows: List[Dict[str, int]] = []
    current_start = max(0, vad_segments[0]["start_ms"] - IMPORT_WINDOW_PAD_MS)
    current_end = min(
        duration_ms if duration_ms > 0 else vad_segments[0]["end_ms"] + IMPORT_WINDOW_PAD_MS,
        vad_segments[0]["end_ms"] + IMPORT_WINDOW_PAD_MS,
    )
    for segment in vad_segments[1:]:
        next_start = max(0, segment["start_ms"] - IMPORT_WINDOW_PAD_MS)
        next_end = min(
            duration_ms if duration_ms > 0 else segment["end_ms"] + IMPORT_WINDOW_PAD_MS,
            segment["end_ms"] + IMPORT_WINDOW_PAD_MS,
        )
        gap_ms = next_start - current_end
        combined_ms = next_end - current_start
        if gap_ms > IMPORT_WINDOW_GAP_MS or combined_ms > IMPORT_MAX_ASR_WINDOW_MS:
            windows.extend(_split_window(current_start, current_end, duration_ms))
            current_start = next_start
            current_end = next_end
        else:
            current_end = max(current_end, next_end)
    windows.extend(_split_window(current_start, current_end, duration_ms))
    return vad_segments, windows


def slice_wav_segment(source: Path, destination: Path, start_ms: int, end_ms: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(source), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        sample_rate = reader.getframerate()
        compression = reader.getcomptype()
        compression_name = reader.getcompname()
        start_frame = max(0, int(start_ms * sample_rate / 1000))
        end_frame = max(start_frame, int(end_ms * sample_rate / 1000))
        frame_count = max(0, min(end_frame, reader.getnframes()) - start_frame)
        reader.setpos(start_frame)
        frames = reader.readframes(frame_count)

    with wave.open(str(destination), "wb") as writer:
        writer.setparams((channels, sample_width, sample_rate, 0, compression, compression_name))
        writer.writeframes(frames)


def offset_asr_segments(
    segments: List[Dict[str, Any]],
    offset_ms: int,
    duration_ms: int,
) -> List[Dict[str, Any]]:
    shifted: List[Dict[str, Any]] = []
    for segment in segments or []:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start_ms = _coerce_ms(segment.get("start_ms")) + offset_ms
        end_ms = _coerce_ms(segment.get("end_ms"), start_ms - offset_ms) + offset_ms
        if duration_ms > 0:
            start_ms = min(start_ms, duration_ms)
            end_ms = min(end_ms, duration_ms)
        end_ms = max(start_ms + 1, end_ms)
        shifted.append(
            {
                **segment,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "absolute_start_ms": start_ms,
                "absolute_end_ms": end_ms,
                "text": text,
            }
        )
    return shifted


def transcribe_import_window(
    asr_engine: Any,
    wav_path: Path,
    window: Dict[str, int],
    requested_language: str,
    prompt: str,
    duration_ms: int,
) -> Dict[str, Any]:
    start_ms = _coerce_ms(window.get("start_ms"))
    end_ms = max(start_ms + 1, _coerce_ms(window.get("end_ms"), start_ms + 1))
    with tempfile.TemporaryDirectory(prefix="voicemeeting-import-") as temp_dir:
        slice_path = Path(temp_dir) / f"window-{start_ms}-{end_ms}.wav"
        slice_wav_segment(wav_path, slice_path, start_ms, end_ms)
        result = asr_engine.transcribe(slice_path, requested_language, prompt)
    return {
        **result,
        "segments": offset_asr_segments(result.get("segments") or [], start_ms, duration_ms),
    }


async def stream_import_events(
    *,
    meeting_id: str,
    chunk_id: str,
    parsed_duration_ms: int,
    requested_language: str,
    requested_model: str,
    requested_speaker_mode: str,
    store: MeetingStore,
    asr_engine: Any,
    prepare_chunk_wav: Callable[[Dict[str, Any]], Path],
    meeting_runtime: Callable[[str], Dict[str, Any]],
    attach_audio_payload: Callable[..., Dict[str, Any]],
    ensure_single_meeting_audio: Callable[..., Any],
    prompt_getter: Callable[[str, str], str],
    diarizer_for_mode: Callable[[str], Any],
    speaker_tracker: SpeakerTracker,
) -> AsyncIterator[tuple[str, Dict[str, Any]]]:
    duration = int(parsed_duration_ms or 0)
    collected_segments: list[Dict[str, Any]] = []
    latest_asr: Dict[str, Any] = {
        "requested_language": requested_language,
        "model": requested_model,
        "vad_segments": [],
    }
    diarization_result: Dict[str, Any] = {
        "status": "disabled",
        "mode": requested_speaker_mode,
        "turns": [],
        "error": "",
    }
    speaker_result: Dict[str, Any] = {
        "status": "disabled",
        "mode": requested_speaker_mode,
        "assigned": 0,
        "created": 0,
        "error": "",
    }

    def stage_payload(status: str, label: str, progress: float, **extra: Any) -> Dict[str, Any]:
        payload = {
            "status": status,
            "label": label,
            "progress": max(0.0, min(1.0, float(progress))),
            "chunk": store.get_chunk(chunk_id),
            "runtime": meeting_runtime(meeting_id),
        }
        payload.update(extra)
        return payload

    def asr_meta(result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "requested_language": result.get("requested_language"),
            "model": requested_model,
            "language": result.get("language"),
            "language_probability": result.get("language_probability"),
            "top_languages": result.get("top_languages") or [],
            "multilingual": result.get("multilingual"),
            "vad_segments": result.get("vad_segments") or [],
        }

    try:
        yield "stage", stage_payload("saved", "导入音视频排队中", 0.02)

        store.update_chunk(chunk_id, status="converting")
        yield "stage", stage_payload("converting", "准备完整音频", 0.04)
        wav_path = await asyncio.to_thread(prepare_chunk_wav, store.get_chunk(chunk_id))
        probed_duration = await asyncio.to_thread(audio_duration_ms, wav_path)
        if probed_duration and probed_duration > 0:
            duration = probed_duration
            store.update_chunk(chunk_id, duration_ms=duration)

        try:
            waveform = await asyncio.to_thread(meeting_audio_waveform, wav_path, IMPORT_WAVEFORM_BARS)
            yield "waveform", {
                "meeting_id": meeting_id,
                "duration_ms": duration or waveform.get("duration_ms"),
                "progress": 0.06,
                **waveform,
            }
        except Exception:
            pass

        store.update_chunk(chunk_id, status="transcribing")
        yield "stage", stage_payload("transcribing", "正在分析人声片段", 0.08)
        vad_segments, windows = await asyncio.to_thread(build_import_windows, asr_engine, wav_path, duration)
        latest_asr["vad_segments"] = vad_segments
        yield "stage", stage_payload(
            "transcribing",
            "完整音视频识别中",
            0.1,
            total_windows=len(windows),
            vad_segments=vad_segments,
        )

        total_windows = max(1, len(windows))
        for index, window in enumerate(windows, start=1):
            processed_start = int(window.get("start_ms") or 0)
            if duration > 0:
                start_progress = 0.1 + min(0.74, processed_start / duration * 0.74)
            else:
                start_progress = 0.1 + min(0.74, (index - 1) / total_windows * 0.74)
            yield "stage", stage_payload(
                "transcribing",
                f"第 {index} 段识别",
                start_progress,
                window=window,
                current_window=index,
                total_windows=len(windows),
            )

            recent_context = "\n".join(
                segment.get("text", "")
                for segment in collected_segments[-6:]
                if segment.get("text")
                and not is_unrecognized_text(segment.get("text"))
            )
            meeting_for_prompt = store.get_meeting(meeting_id)
            result = await asyncio.to_thread(
                transcribe_import_window,
                asr_engine,
                wav_path,
                window,
                requested_language,
                asr_context_prompt(
                    meeting_for_prompt,
                    recent_context,
                    prompt_getter("asr_context", ""),
                ),
                duration,
            )
            latest_asr = {**latest_asr, **asr_meta(result)}
            segments = result.get("segments") or []
            if not segments:
                continue
            inserted = store.add_segments(meeting_id, chunk_id, segments)
            if inserted:
                store.clear_final_markdown(meeting_id)
            collected_segments.extend(segments)
            if duration > 0:
                progress = 0.1 + min(0.78, int(window.get("end_ms") or 0) / duration * 0.78)
            else:
                progress = 0.1 + min(0.78, index / total_windows * 0.78)
            yield "segments", {
                "meeting": attach_audio_payload(store.get_meeting(meeting_id)),
                "segments": inserted,
                "progress": progress,
                "window": window,
                "current_window": index,
                "total_windows": len(windows),
                "runtime": meeting_runtime(meeting_id),
                "asr": latest_asr,
            }

        if not collected_segments:
            placeholder = segments_or_unrecognized(store.get_chunk(chunk_id), [])
            inserted = store.add_segments(meeting_id, chunk_id, placeholder)
            if inserted:
                store.clear_final_markdown(meeting_id)
                collected_segments.extend(placeholder)
                yield "segments", {
                    "meeting": attach_audio_payload(store.get_meeting(meeting_id)),
                    "segments": inserted,
                    "progress": 0.88,
                    "runtime": meeting_runtime(meeting_id),
                    "asr": latest_asr,
                }

        final_segments = list(collected_segments)
        active_diarizer = diarizer_for_mode(requested_speaker_mode)
        if active_diarizer is not None and final_segments:
            try:
                store.update_chunk(chunk_id, status="diarizing")
                yield "stage", stage_payload("diarizing", "完整音视频说话人分离中", 0.9)
                turns = await asyncio.to_thread(active_diarizer.diarize, wav_path)
                if requested_speaker_mode == "diarization":
                    final_segments = split_segments_by_turns(final_segments, turns)
                elif not speaker_tracker.enabled:
                    final_segments = assign_speakers(final_segments, turns)
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

        use_speaker_tracking = speaker_tracker.enabled and requested_speaker_mode in {
            "voiceprint",
            "diarization",
            "auto",
        }
        if use_speaker_tracking and final_segments:
            try:
                store.update_chunk(chunk_id, status="identifying_speakers")
                yield "stage", stage_payload("identifying_speakers", "完整音视频声纹匹配中", 0.94)
                final_segments, speaker_result = await asyncio.to_thread(
                    speaker_tracker.assign_segments,
                    store,
                    meeting_id,
                    wav_path,
                    final_segments,
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
        elif requested_speaker_mode == "off":
            final_segments = clear_segment_speakers(final_segments)

        version_id = store.get_active_transcript_version(meeting_id).get("id") or "auto"
        store.delete_segments_for_chunk(meeting_id, str(version_id), chunk_id)
        final_inserted = store.add_segments(
            meeting_id,
            chunk_id,
            segments_or_unrecognized(store.get_chunk(chunk_id), final_segments),
            version_id=str(version_id),
        )
        if final_inserted:
            store.clear_final_markdown(meeting_id)
        store.update_chunk(chunk_id, status="done")
        store.update_meeting_status(meeting_id, "stopped")
        audio_payload_data = await ensure_single_meeting_audio(meeting_id, cleanup_chunks=True)
        meeting_payload = attach_audio_payload(store.get_meeting(meeting_id), audio_payload_data)
        yield "segments", {
            "meeting": meeting_payload,
            "segments": final_inserted,
            "replace": True,
            "progress": 0.98,
            "runtime": meeting_runtime(meeting_id),
            "asr": latest_asr,
            "diarization": diarization_result,
            "speaker_tracking": speaker_result,
        }
        yield "done", {
            "meeting": meeting_payload,
            "chunk": store.get_chunk(chunk_id),
            "runtime": meeting_runtime(meeting_id),
            "asr": latest_asr,
            "diarization": diarization_result,
            "speaker_tracking": speaker_result,
            "summary_status": "idle",
        }
    except asyncio.CancelledError:
        store.update_chunk(chunk_id, status="error", error="请求已取消。")
        try:
            store.update_meeting_status(meeting_id, "stopped")
        except KeyError:
            pass
        raise
    except ASRUnavailable as exc:
        store.update_chunk(chunk_id, status="error", error=str(exc))
        store.update_meeting_status(meeting_id, "stopped")
        yield "error", {"error": str(exc), "runtime": meeting_runtime(meeting_id)}
    except Exception as exc:
        store.update_chunk(chunk_id, status="error", error=str(exc))
        store.update_meeting_status(meeting_id, "stopped")
        yield "error", {"error": str(exc), "runtime": meeting_runtime(meeting_id)}
