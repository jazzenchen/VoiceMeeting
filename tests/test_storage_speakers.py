import tempfile
import unittest
from pathlib import Path

from backend.storage import MeetingStore


class StorageSpeakerTests(unittest.TestCase):
    def test_replace_speakers_restores_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MeetingStore(Path(temp_dir) / "test.sqlite3")
            meeting = store.create_meeting("speaker test")
            meeting_id = meeting["id"]
            try:
                first = store.create_speaker(meeting_id, [0.1, 0.2, 0.3])
                snapshot = store.list_speakers(meeting_id)

                store.delete_speakers(meeting_id)
                store.create_speaker(meeting_id, [0.9, 0.8, 0.7])
                store.replace_speakers(meeting_id, snapshot)

                speakers = store.list_speakers(meeting_id)
                self.assertEqual(len(speakers), 1)
                self.assertEqual(speakers[0]["id"], first["id"])
                self.assertEqual(speakers[0]["label"], first["label"])
                self.assertEqual(speakers[0]["embedding"], [0.1, 0.2, 0.3])
            finally:
                store.delete_meeting(meeting_id)


if __name__ == "__main__":
    unittest.main()
