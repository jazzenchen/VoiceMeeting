import unittest

from backend.reprocess_jobs import ReprocessCancelled, ReprocessJobStore


class FakeVersionStore:
    def __init__(self) -> None:
        self.meeting = {"id": "m1", "active_version_id": "v2"}
        self.version = {"id": "v2", "parent_version_id": "auto"}
        self.active_version_ids = []
        self.deleted_versions = []
        self.version_statuses = []

    def get_transcript_version(self, meeting_id: str, version_id: str):
        return dict(self.version)

    def get_meeting(self, meeting_id: str):
        return dict(self.meeting)

    def set_active_transcript_version(self, meeting_id: str, version_id: str) -> None:
        self.active_version_ids.append(version_id)
        self.meeting["active_version_id"] = version_id

    def delete_segments_for_version(self, meeting_id: str, version_id: str) -> None:
        self.deleted_versions.append(version_id)

    def update_transcript_version_status(self, meeting_id: str, version_id: str, status: str, meta=None) -> None:
        self.version_statuses.append((version_id, status, meta or {}))


class ReprocessJobStoreTests(unittest.TestCase):
    def test_cancel_request_holds_later_running_updates_in_cancelling(self) -> None:
        jobs = ReprocessJobStore(FakeVersionStore())
        jobs.set("j1", id="j1", meeting_id="m1", status="running", stage="整理文字")

        jobs.request_cancel("j1")
        state = jobs.set("j1", status="running", stage="写入结果")

        self.assertEqual(state["status"], "cancelling")
        self.assertEqual(state["stage"], "写入结果")
        with self.assertRaises(ReprocessCancelled):
            jobs.check_cancelled("j1")

    def test_mark_cancelled_restores_parent_version(self) -> None:
        store = FakeVersionStore()
        jobs = ReprocessJobStore(store)
        jobs.set("j1", id="j1", meeting_id="m1", status="running", version_id="v2", progress=3)

        jobs.mark_cancelled("j1", "m1", version_id="v2", inserted_total=4)

        self.assertEqual(store.active_version_ids, ["auto"])
        self.assertEqual(store.deleted_versions, ["v2"])
        self.assertEqual(jobs.get("j1")["status"], "cancelled")
        self.assertEqual(store.version_statuses[0][1], "cancelled")

    def test_latest_for_meeting_returns_newest_state(self) -> None:
        jobs = ReprocessJobStore(FakeVersionStore())
        jobs.set("j1", id="j1", meeting_id="m1", status="done")
        jobs.set("j2", id="j2", meeting_id="m1", status="running")

        self.assertEqual(jobs.latest_for_meeting("m1")["id"], "j2")


if __name__ == "__main__":
    unittest.main()
