from __future__ import annotations

from typing import Any, Dict, Optional


SpeakerSnapshot = Optional[list[Dict[str, Any]]]


def capture_speaker_snapshot(store: Any, meeting_id: str) -> SpeakerSnapshot:
    return store.list_speakers(meeting_id)


def restore_speaker_snapshot(store: Any, meeting_id: str, snapshot: SpeakerSnapshot) -> None:
    if snapshot is None:
        return
    try:
        store.replace_speakers(meeting_id, snapshot)
    except Exception:
        pass
