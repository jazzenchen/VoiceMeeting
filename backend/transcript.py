from __future__ import annotations

from typing import Any, Dict, List, Optional


TERMINAL_PUNCTUATION = tuple(".!?。！？…")
SOFT_PUNCTUATION = tuple(",，、;；:：")
MAX_TIGHT_GAP_MS = 900
MAX_FRAGMENT_GAP_MS = 2600
MAX_UTTERANCE_DURATION_MS = 28000
MAX_UTTERANCE_TEXT_CHARS = 420
MIN_LONG_UTTERANCE_CHARS = 180


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _speaker(value: Any) -> str:
    text = str(value or "").strip()
    return text or "Speaker"


def _is_word_char(value: str) -> bool:
    return value.isascii() and (value.isalnum() or value in "_+#@/-")


def _looks_complete(text: str) -> bool:
    text = text.rstrip()
    return bool(text) and text.endswith(TERMINAL_PUNCTUATION)


def _looks_fragment(text: str) -> bool:
    text = text.strip()
    if not text:
        return True
    if text.endswith(SOFT_PUNCTUATION):
        return True
    return not _looks_complete(text)


def _dedupe_overlap(left: str, right: str) -> str:
    max_length = min(28, len(left), len(right))
    for length in range(max_length, 3, -1):
        if left[-length:] == right[:length]:
            return right[length:]
    return right


def _join_text_with_piece(left: str, right: str) -> tuple[str, str]:
    left = left.rstrip()
    right = _dedupe_overlap(left, right.strip())
    if not left:
        return right, right
    if not right:
        return left, ""
    if right[0] in TERMINAL_PUNCTUATION or right[0] in SOFT_PUNCTUATION:
        return f"{left}{right}", right
    last = left[-1]
    first = right[0]
    if _is_word_char(last) and _is_word_char(first):
        piece = f" {right}"
        return f"{left}{piece}", piece
    if (last.isascii() and first.isalnum()) or (_is_word_char(last) and not first.isascii()):
        piece = f" {right}"
        return f"{left}{piece}", piece
    if (not last.isascii()) and _is_word_char(first):
        piece = f" {right}"
        return f"{left}{piece}", piece
    return f"{left}{right}", right


def _join_text(left: str, right: str) -> str:
    joined, _piece = _join_text_with_piece(left, right)
    return joined


def _part_from_segment(segment: Dict[str, Any], text: str) -> Dict[str, Any]:
    return {
        "id": segment.get("id"),
        "chunk_id": segment.get("chunk_id"),
        "chunk_seq": _to_int(segment.get("chunk_seq"), 0),
        "speaker": _speaker(segment.get("speaker")),
        "text": text,
        "raw_text": str(segment.get("text") or "").strip(),
        "start_ms": _to_int(segment.get("absolute_start_ms")),
        "end_ms": _to_int(segment.get("absolute_end_ms")),
        "created_at": segment.get("created_at"),
    }


def _absolute_segment(segment: Dict[str, Any], chunk_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    chunk = chunk_map.get(segment.get("chunk_id") or "") or {}
    base_ms = _to_int(chunk.get("started_at_ms"), 0)
    start_ms = base_ms + _to_int(segment.get("start_ms"), 0)
    end_ms = base_ms + _to_int(segment.get("end_ms"), 0)
    if end_ms < start_ms:
        end_ms = start_ms
    return {
        **segment,
        "speaker": _speaker(segment.get("speaker")),
        "absolute_start_ms": start_ms,
        "absolute_end_ms": end_ms,
        "chunk_seq": _to_int(chunk.get("seq"), 0),
    }


def _should_merge(current: Dict[str, Any], segment: Dict[str, Any]) -> bool:
    if _speaker(current.get("speaker")) != _speaker(segment.get("speaker")):
        return False

    current_text = str(current.get("text") or "")
    current_duration_ms = _to_int(current.get("end_ms")) - _to_int(current.get("start_ms"))
    if len(current_text) >= MAX_UTTERANCE_TEXT_CHARS:
        return False
    if current_duration_ms >= MAX_UTTERANCE_DURATION_MS and len(current_text) >= MIN_LONG_UTTERANCE_CHARS:
        return False

    gap_ms: Optional[int] = None
    if current.get("end_ms") is not None and segment.get("absolute_start_ms") is not None:
        gap_ms = _to_int(segment.get("absolute_start_ms")) - _to_int(current.get("end_ms"))

    if gap_ms is None or gap_ms <= MAX_TIGHT_GAP_MS:
        return True
    if gap_ms <= MAX_FRAGMENT_GAP_MS and _looks_fragment(str(current.get("text") or "")):
        return True
    if gap_ms <= MAX_FRAGMENT_GAP_MS and len(str(segment.get("text") or "").strip()) <= 10:
        return True
    return False


def build_utterances(
    segments: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    chunk_map = {chunk.get("id"): chunk for chunk in chunks if chunk.get("id")}
    ordered_segments = sorted(
        (_absolute_segment(segment, chunk_map) for segment in segments if segment.get("text")),
        key=lambda item: (
            _to_int(item.get("absolute_start_ms")),
            _to_int(item.get("chunk_seq")),
            str(item.get("created_at") or ""),
        ),
    )

    utterances: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for segment in ordered_segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue

        if current is not None and _should_merge(current, segment):
            joined_text, part_text = _join_text_with_piece(str(current.get("text") or ""), text)
            current["text"] = joined_text
            current["end_ms"] = max(_to_int(current.get("end_ms")), _to_int(segment.get("absolute_end_ms")))
            current["segment_count"] = _to_int(current.get("segment_count"), 1) + 1
            current["segment_ids"].append(segment.get("id"))
            current["parts"].append(_part_from_segment(segment, part_text))
            chunk_id = segment.get("chunk_id")
            if chunk_id and chunk_id not in current["chunk_ids"]:
                current["chunk_ids"].append(chunk_id)
            continue

        if current is not None:
            utterances.append(current)

        current = {
            "id": f"utt-{segment.get('id')}",
            "speaker": _speaker(segment.get("speaker")),
            "text": text,
            "start_ms": _to_int(segment.get("absolute_start_ms")),
            "end_ms": _to_int(segment.get("absolute_end_ms")),
            "created_at": segment.get("created_at"),
            "segment_count": 1,
            "segment_ids": [segment.get("id")],
            "chunk_ids": [segment.get("chunk_id")] if segment.get("chunk_id") else [],
            "parts": [_part_from_segment(segment, text)],
        }

    if current is not None:
        utterances.append(current)
    return utterances
