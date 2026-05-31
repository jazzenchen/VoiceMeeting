from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from .transcript import is_unrecognized_text


def transcript_source(meeting: Dict[str, Any], segments: Optional[list[Dict[str, Any]]] = None) -> Dict[str, Any]:
    segment_rows = list(segments if segments is not None else meeting.get("segments") or [])
    payload = [
        {
            "speaker": str(segment.get("speaker") or ""),
            "text": str(segment.get("text") or "").strip(),
            "start_ms": int(segment.get("start_ms") or 0),
            "end_ms": int(segment.get("end_ms") or 0),
            "chunk_id": str(segment.get("chunk_id") or ""),
        }
        for segment in segment_rows
        if str(segment.get("text") or "").strip()
        and not is_unrecognized_text(segment.get("text"))
    ]
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "version_id": str(meeting.get("active_version_id") or "auto"),
        "hash": digest,
        "segment_count": len(payload),
    }


def transcript_source_without_segments(
    meeting: Dict[str, Any],
    removed_segment_ids: set[str],
) -> Dict[str, Any]:
    segments = [
        segment
        for segment in meeting.get("segments") or []
        if str(segment.get("id") or "") not in removed_segment_ids
    ]
    return transcript_source(meeting, segments)


def summary_is_current(meeting: Dict[str, Any], source: Optional[Dict[str, Any]] = None) -> bool:
    current_source = source or transcript_source(meeting)
    if int(current_source.get("segment_count") or 0) == 0:
        summary = meeting.get("summary") or {}
        has_summary = bool(summary.get("summary")) or any(
            bool(summary.get(key))
            for key in ("topics", "decisions", "action_items", "open_questions", "risks")
        )
        return not has_summary
    return bool(current_source.get("hash")) and meeting.get("summary_source_hash") == current_source.get("hash")


def final_notes_current(meeting: Dict[str, Any], source: Optional[Dict[str, Any]] = None) -> bool:
    current_source = source or transcript_source(meeting)
    return bool(meeting.get("final_markdown")) and meeting.get("final_source_hash") == current_source.get("hash")
