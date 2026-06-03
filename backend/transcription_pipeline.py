from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .diarization import DiarizationUnavailable, PyannoteDiarizer, assign_speakers, split_segments_by_turns
from .speaker_tracker import SpeakerTracker, SpeakerTrackingUnavailable
from .storage import MeetingStore
from .transcript import is_unrecognized_text
from .transcription_helpers import asr_context_prompt, segments_or_unrecognized


PromptGetter = Callable[[str, str], str]
DiarizerResolver = Callable[[str], Optional[PyannoteDiarizer]]


@dataclass
class ChunkTranscriptionResult:
    inserted: list[Dict[str, Any]]
    asr: Dict[str, Any]
    diarization: Dict[str, Any]
    speaker_tracking: Dict[str, Any]


class ChunkTranscriptionPipeline:
    def __init__(
        self,
        store: MeetingStore,
        audio_converter: Any,
        prompt_getter: PromptGetter,
        diarizer_for_mode: DiarizerResolver,
        speaker_tracker: SpeakerTracker,
    ) -> None:
        self.store = store
        self.audio_converter = audio_converter
        self.prompt_getter = prompt_getter
        self.diarizer_for_mode = diarizer_for_mode
        self.speaker_tracker = speaker_tracker

    async def process(
        self,
        meeting_id: str,
        chunk: Dict[str, Any],
        asr_engine: Any,
        requested_language: str,
        requested_model: str,
        requested_speaker_mode: str,
    ) -> ChunkTranscriptionResult:
        audio_path = Path(chunk["audio_path"])
        if audio_path.suffix.lower() == ".wav":
            wav_path = audio_path.with_name(f"{audio_path.stem}_16k.wav")
        else:
            wav_path = audio_path.with_suffix(".wav")

        self.store.update_chunk(chunk["id"], status="converting")
        self.audio_converter.convert_to_wav(audio_path, wav_path)
        self.store.update_chunk(chunk["id"], wav_path=str(wav_path), status="transcribing")

        meeting_for_prompt = self.store.get_meeting(meeting_id)
        recent_context = "\n".join(
            segment.get("text", "")
            for segment in (meeting_for_prompt.get("segments") or [])[-6:]
            if segment.get("text")
            and not is_unrecognized_text(segment.get("text"))
        )
        asr_result = await asyncio.to_thread(
            asr_engine.transcribe,
            wav_path,
            requested_language,
            asr_context_prompt(
                meeting_for_prompt,
                recent_context,
                self.prompt_getter("asr_context", ""),
            ),
        )

        diarization_result = await self._apply_diarization(
            chunk["id"],
            wav_path,
            asr_result,
            requested_speaker_mode,
        )
        speaker_result = await self._apply_speaker_tracking(
            meeting_id,
            chunk["id"],
            wav_path,
            asr_result,
            requested_speaker_mode,
        )

        segments_to_store = segments_or_unrecognized(chunk, asr_result.get("segments") or [])
        inserted = self.store.add_segments(meeting_id, chunk["id"], segments_to_store)
        self.store.update_chunk(chunk["id"], status="done")
        return ChunkTranscriptionResult(
            inserted=inserted,
            asr=asr_result,
            diarization=diarization_result,
            speaker_tracking=speaker_result,
        )

    async def _apply_diarization(
        self,
        chunk_id: str,
        wav_path: Path,
        asr_result: Dict[str, Any],
        requested_speaker_mode: str,
    ) -> Dict[str, Any]:
        diarization_result = {
            "status": "disabled",
            "mode": requested_speaker_mode,
            "turns": [],
            "error": "",
        }
        active_diarizer = self.diarizer_for_mode(requested_speaker_mode)
        if active_diarizer is None:
            return diarization_result

        try:
            self.store.update_chunk(chunk_id, status="diarizing")
            turns = await asyncio.to_thread(active_diarizer.diarize, wav_path)
            if requested_speaker_mode == "diarization":
                asr_result["segments"] = split_segments_by_turns(asr_result["segments"], turns)
            elif not self.speaker_tracker.enabled:
                asr_result["segments"] = assign_speakers(asr_result["segments"], turns)
            return {
                "status": "done",
                "mode": requested_speaker_mode,
                "turns": turns,
                "error": "",
            }
        except DiarizationUnavailable as exc:
            return {
                "status": "error",
                "mode": requested_speaker_mode,
                "turns": [],
                "error": str(exc),
            }

    async def _apply_speaker_tracking(
        self,
        meeting_id: str,
        chunk_id: str,
        wav_path: Path,
        asr_result: Dict[str, Any],
        requested_speaker_mode: str,
    ) -> Dict[str, Any]:
        speaker_result = {
            "status": "disabled",
            "mode": requested_speaker_mode,
            "assigned": 0,
            "created": 0,
            "error": "",
        }
        use_speaker_tracking = self.speaker_tracker.enabled and requested_speaker_mode in {
            "voiceprint",
            "auto",
        }
        if not use_speaker_tracking:
            return speaker_result

        try:
            self.store.update_chunk(chunk_id, status="identifying_speakers")
            asr_result["segments"], speaker_result = await asyncio.to_thread(
                self.speaker_tracker.assign_segments,
                self.store,
                meeting_id,
                wav_path,
                asr_result["segments"],
            )
            speaker_result["mode"] = requested_speaker_mode
            return speaker_result
        except SpeakerTrackingUnavailable as exc:
            return {
                "status": "error",
                "mode": requested_speaker_mode,
                "assigned": 0,
                "created": 0,
                "error": str(exc),
            }
