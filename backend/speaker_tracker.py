from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import (
    SPEAKER_CONTINUITY_THRESHOLD,
    SPEAKER_MATCH_THRESHOLD,
    SPEAKER_MAX_AUDIO_MS,
    SPEAKER_MIN_AUDIO_MS,
    SPEAKER_TRACKING_BACKEND,
    SPEAKER_TRACKING_DEVICE,
    SPEAKER_TRACKING_ENABLED,
)


class SpeakerTrackingUnavailable(RuntimeError):
    pass


class SpeakerTracker:
    def __init__(self) -> None:
        self.backend = SPEAKER_TRACKING_BACKEND
        self.device = SPEAKER_TRACKING_DEVICE
        self.enabled = SPEAKER_TRACKING_ENABLED
        self.threshold = SPEAKER_MATCH_THRESHOLD
        self.continuity_threshold = SPEAKER_CONTINUITY_THRESHOLD
        self.min_audio_ms = SPEAKER_MIN_AUDIO_MS
        self.max_audio_ms = SPEAKER_MAX_AUDIO_MS
        self._encoder = None
        self.loading = False
        self.loaded = False
        self.last_error = ""

    def status(self) -> Dict[str, Any]:
        reason = ""
        if not self.enabled:
            reason = "set VOICE_MEETING_SPEAKER_TRACKING=1 to enable"
        elif self.backend != "resemblyzer":
            reason = f"unsupported speaker backend: {self.backend}"
        return {
            "backend": self.backend,
            "device": self.device,
            "enabled": self.enabled,
            "loaded": self.loaded,
            "loading": self.loading,
            "available": self.enabled and not reason,
            "threshold": self.threshold,
            "continuity_threshold": self.continuity_threshold,
            "reason": reason,
            "last_error": self.last_error,
        }

    def _load_encoder(self) -> Any:
        if not self.enabled:
            raise SpeakerTrackingUnavailable("Speaker tracking is disabled.")
        if self.backend != "resemblyzer":
            raise SpeakerTrackingUnavailable(f"Unsupported speaker backend: {self.backend}")
        if self._encoder is not None:
            return self._encoder

        self.loading = True
        self.last_error = ""
        try:
            from resemblyzer import VoiceEncoder

            self._encoder = VoiceEncoder(device=self.device)
            self.loaded = True
            return self._encoder
        except Exception as exc:
            self.last_error = str(exc)
            raise SpeakerTrackingUnavailable(str(exc)) from exc
        finally:
            self.loading = False

    def assign_segments(
        self,
        store: Any,
        meeting_id: str,
        wav_path: Path,
        segments: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not self.enabled or not segments:
            return segments, {"status": "disabled", "assigned": 0, "created": 0, "error": ""}

        audio, sample_rate = self._read_wav(wav_path)
        speakers = store.list_speakers(meeting_id)
        assigned: List[Dict[str, Any]] = []
        created = 0
        embedded = 0
        unassigned = 0
        previous_label: Optional[str] = None
        previous_embedding: Optional[np.ndarray] = None

        for segment in segments:
            embedding = self._embed_segment(audio, sample_rate, segment)
            if embedding is None:
                label = previous_label or self._speaker_label_from_existing(speakers)
                assigned.append({**segment, "speaker": label or segment.get("speaker") or ""})
                unassigned += 1
                continue

            embedded += 1
            match, score = self._best_match(speakers, embedding)
            if match is None and previous_label and previous_embedding is not None:
                continuity_score = self._cosine(previous_embedding, embedding)
                if continuity_score >= self.continuity_threshold:
                    match = self._find_speaker(speakers, previous_label)
                    score = max(continuity_score, self.threshold)

            if match is None or score < self.threshold:
                match = store.create_speaker(meeting_id, self._to_list(embedding))
                speakers.append(match)
                created += 1
            else:
                merged = self._merge_embedding(match, embedding)
                match["embedding"] = merged
                match["sample_count"] = int(match.get("sample_count") or 0) + 1
                store.update_speaker_embedding(
                    match["id"],
                    merged,
                    int(match.get("sample_count") or 1),
                )

            label = str(match.get("label") or "")
            previous_label = label
            previous_embedding = embedding
            assigned.append({**segment, "speaker": label})

        return assigned, {
            "status": "done",
            "backend": self.backend,
            "assigned": embedded,
            "created": created,
            "unassigned": unassigned,
            "speakers": len(speakers),
            "threshold": self.threshold,
            "error": "",
        }

    def _read_wav(self, wav_path: Path) -> Tuple[np.ndarray, int]:
        try:
            with wave.open(str(wav_path), "rb") as audio:
                channels = audio.getnchannels()
                sample_width = audio.getsampwidth()
                sample_rate = audio.getframerate()
                frames = audio.readframes(audio.getnframes())
        except Exception as exc:
            raise SpeakerTrackingUnavailable(f"Cannot read speaker audio: {exc}") from exc

        if sample_width == 2:
            data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        elif sample_width == 4:
            data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise SpeakerTrackingUnavailable(f"Unsupported wav sample width: {sample_width}")
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        return data, sample_rate

    def _embed_segment(
        self,
        audio: np.ndarray,
        sample_rate: int,
        segment: Dict[str, Any],
    ) -> Optional[np.ndarray]:
        start_ms = max(0, int(segment.get("start_ms") or 0) - 300)
        end_ms = max(start_ms, int(segment.get("end_ms") or start_ms) + 300)
        if end_ms - start_ms < self.min_audio_ms:
            return None
        if end_ms - start_ms > self.max_audio_ms:
            center = (start_ms + end_ms) // 2
            half = self.max_audio_ms // 2
            start_ms = max(0, center - half)
            end_ms = center + half

        start_sample = int(start_ms * sample_rate / 1000)
        end_sample = min(len(audio), int(end_ms * sample_rate / 1000))
        if end_sample <= start_sample:
            return None
        window = audio[start_sample:end_sample]
        if len(window) < int(sample_rate * self.min_audio_ms / 1000):
            return None
        rms = float(np.sqrt(np.mean(np.square(window)))) if len(window) else 0.0
        if rms < 0.003:
            return None

        try:
            from resemblyzer import preprocess_wav

            encoder = self._load_encoder()
            prepared = preprocess_wav(window.astype(np.float32), source_sr=sample_rate)
            if len(prepared) < 8000:
                return None
            embedding = np.asarray(encoder.embed_utterance(prepared), dtype=np.float32)
            return self._normalize(embedding)
        except Exception as exc:
            self.last_error = str(exc)
            raise SpeakerTrackingUnavailable(str(exc)) from exc

    def _best_match(
        self,
        speakers: List[Dict[str, Any]],
        embedding: np.ndarray,
    ) -> Tuple[Optional[Dict[str, Any]], float]:
        best: Optional[Dict[str, Any]] = None
        best_score = -1.0
        for speaker in speakers:
            existing = self._as_embedding(speaker.get("embedding"))
            if existing is None:
                continue
            score = self._cosine(existing, embedding)
            if score > best_score:
                best = speaker
                best_score = score
        return best, best_score

    def _merge_embedding(self, speaker: Dict[str, Any], embedding: np.ndarray) -> List[float]:
        existing = self._as_embedding(speaker.get("embedding"))
        if existing is None:
            return self._to_list(embedding)
        weight = min(max(int(speaker.get("sample_count") or 1), 1), 16)
        merged = self._normalize((existing * weight + embedding) / float(weight + 1))
        return self._to_list(merged)

    def _as_embedding(self, value: Any) -> Optional[np.ndarray]:
        if not isinstance(value, list) or not value:
            return None
        try:
            return self._normalize(np.asarray(value, dtype=np.float32))
        except Exception:
            return None

    def _normalize(self, value: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(value))
        if not math.isfinite(norm) or norm <= 0:
            return value
        return value / norm

    def _cosine(self, left: np.ndarray, right: np.ndarray) -> float:
        return float(np.dot(self._normalize(left), self._normalize(right)))

    def _to_list(self, value: np.ndarray) -> List[float]:
        return [float(item) for item in self._normalize(value).tolist()]

    def _find_speaker(self, speakers: List[Dict[str, Any]], label: str) -> Optional[Dict[str, Any]]:
        for speaker in speakers:
            if speaker.get("label") == label:
                return speaker
        return None

    def _speaker_label_from_existing(self, speakers: List[Dict[str, Any]]) -> str:
        if len(speakers) == 1:
            return str(speakers[0].get("label") or "")
        return ""
