from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


REPROCESS_ACTIVE_STATUSES = {"queued", "running", "cancelling"}


class ReprocessCancelled(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReprocessJobStore:
    def __init__(self, store: Any) -> None:
        self._store = store
        self._states: Dict[str, Dict[str, Any]] = {}

    def set(self, job_id: str, **fields: Any) -> Dict[str, Any]:
        current = dict(self._states.get(job_id) or {})
        if current.get("cancel_requested"):
            requested_status = fields.get("status")
            if requested_status in {"queued", "running"}:
                fields["status"] = "cancelling"
                fields.setdefault("stage", current.get("stage") or "取消中")
            elif requested_status == "done":
                fields["status"] = "cancelled"
                fields["stage"] = "cancelled"
                fields["error"] = "处理已停止。"
        current.update(fields)
        current["updated_at"] = now_iso()
        self._states[job_id] = current
        return current

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        state = self._states.get(job_id)
        return dict(state) if state else None

    def list_for_meeting(self, meeting_id: str) -> list[Dict[str, Any]]:
        return [
            dict(state)
            for state in self._states.values()
            if state.get("meeting_id") == meeting_id
        ]

    def latest_for_meeting(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        jobs = self.list_for_meeting(meeting_id)
        if not jobs:
            return None
        return sorted(jobs, key=lambda item: str(item.get("updated_at") or ""))[-1]

    def request_cancel(self, job_id: str) -> Dict[str, Any]:
        state = self._states.get(job_id)
        if not state:
            raise KeyError(job_id)
        if state.get("status") not in REPROCESS_ACTIVE_STATUSES:
            return dict(state)
        return self.set(
            job_id,
            status="cancelling",
            stage="取消中",
            cancel_requested=True,
        )

    def check_cancelled(self, job_id: str) -> None:
        state = self._states.get(job_id) or {}
        if state.get("cancel_requested") or state.get("status") == "cancelling":
            raise ReprocessCancelled()

    def version_id(self, job_id: str) -> str:
        return str((self._states.get(job_id) or {}).get("version_id") or "")

    def mark_cancelled(
        self,
        job_id: str,
        meeting_id: str,
        version_id: str = "",
        inserted_total: int = 0,
        changed_total: int = 0,
    ) -> None:
        state = self._states.get(job_id) or {}
        if version_id and version_id != "auto":
            try:
                version = self._store.get_transcript_version(meeting_id, version_id)
                meeting = self._store.get_meeting(meeting_id)
                if meeting.get("active_version_id") == version_id:
                    self._store.set_active_transcript_version(
                        meeting_id,
                        str(version.get("parent_version_id") or "auto"),
                    )
                self._store.delete_segments_for_version(meeting_id, version_id)
                self._store.update_transcript_version_status(
                    meeting_id,
                    version_id,
                    "cancelled",
                    {
                        "error": "处理已停止。",
                        "inserted_segments": inserted_total,
                        "changed_segments": changed_total,
                    },
                )
            except Exception:
                pass
        self.set(
            job_id,
            status="cancelled",
            stage="cancelled",
            cancel_requested=True,
            progress=state.get("progress", 0),
            error="处理已停止。",
        )
