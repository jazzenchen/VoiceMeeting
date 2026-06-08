from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from .media_tools import prepare_ffmpeg_path


SAMPLE_RATE = 16000


def patch_mlx_whisper_audio_loader(mlx_whisper: Any, unavailable_error: Type[Exception] = RuntimeError) -> None:
    ffmpeg = prepare_ffmpeg_path()
    if not ffmpeg:
        raise unavailable_error("ffmpeg is required for mlx-whisper audio decoding.")

    mlx_audio = mlx_whisper.audio
    if getattr(mlx_audio.load_audio, "_voice_meeting_ffmpeg", "") == ffmpeg:
        return

    def load_audio(file: str = "", sr: int = mlx_audio.SAMPLE_RATE, from_stdin: bool = False):
        if from_stdin:
            command = [ffmpeg, "-i", "pipe:0"]
        else:
            command = [ffmpeg, "-nostdin", "-i", file]
        command.extend([
            "-threads",
            "0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sr),
            "-",
        ])
        try:
            output = subprocess.run(command, capture_output=True, check=True).stdout
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to load audio: {exc.stderr.decode()}") from exc
        return mlx_audio.mx.array(mlx_audio.np.frombuffer(output, mlx_audio.np.int16)).flatten().astype(
            mlx_audio.mx.float32
        ) / 32768.0

    load_audio._voice_meeting_ffmpeg = ffmpeg  # type: ignore[attr-defined]
    mlx_audio.load_audio = load_audio


def detect_speech_by_energy(wav_path: Path) -> List[Dict[str, int]]:
    try:
        import numpy as np

        with wave.open(str(wav_path), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.readframes(audio.getnframes())
    except Exception:
        return []

    if sample_width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        return []
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if samples.size == 0 or sample_rate <= 0:
        return []

    frame_samples = max(1, int(sample_rate * 0.03))
    rms_values: List[float] = []
    for start in range(0, len(samples), frame_samples):
        frame = samples[start : start + frame_samples]
        if frame.size == 0:
            continue
        rms_values.append(float(np.sqrt(np.mean(np.square(frame)))))
    if not rms_values:
        return []

    noise = float(np.percentile(rms_values, 20))
    threshold = max(0.006, noise * 3.0)
    min_speech_ms = 250
    min_silence_ms = 650
    pad_ms = 250
    segments: List[Dict[str, int]] = []
    active_start: Optional[int] = None
    last_speech_end = 0

    for index, rms in enumerate(rms_values):
        start_ms = int(index * frame_samples * 1000 / sample_rate)
        end_ms = int((index + 1) * frame_samples * 1000 / sample_rate)
        if rms >= threshold:
            if active_start is None:
                active_start = start_ms
            last_speech_end = end_ms
        elif active_start is not None and start_ms - last_speech_end >= min_silence_ms:
            if last_speech_end - active_start >= min_speech_ms:
                segments.append({
                    "start_ms": max(0, active_start - pad_ms),
                    "end_ms": min(int(len(samples) * 1000 / sample_rate), last_speech_end + pad_ms),
                })
            active_start = None

    if active_start is not None and last_speech_end - active_start >= min_speech_ms:
        segments.append({
            "start_ms": max(0, active_start - pad_ms),
            "end_ms": min(int(len(samples) * 1000 / sample_rate), last_speech_end + pad_ms),
        })

    merged: List[Dict[str, int]] = []
    for segment in segments:
        if merged and segment["start_ms"] <= merged[-1]["end_ms"]:
            merged[-1]["end_ms"] = max(merged[-1]["end_ms"], segment["end_ms"])
        else:
            merged.append(segment)
    return merged
