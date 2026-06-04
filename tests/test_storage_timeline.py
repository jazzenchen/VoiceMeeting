import tempfile
import unittest
from pathlib import Path

from backend.storage import MeetingStore


class StorageTimelineTests(unittest.TestCase):
    def test_segments_are_returned_on_global_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MeetingStore(Path(temp_dir) / "test.sqlite3")
            meeting = store.create_meeting("timeline test")
            meeting_id = meeting["id"]
            try:
                chunk = store.create_chunk(
                    meeting_id=meeting_id,
                    audio_bytes=b"test",
                    filename="chunk.webm",
                    mime_type="audio/webm",
                    duration_ms=2000,
                    started_at_ms=12000,
                    ended_at_ms=14000,
                )
                inserted = store.add_segments(
                    meeting_id,
                    chunk["id"],
                    [
                        {
                            "start_ms": 500,
                            "end_ms": 1500,
                            "speaker": "Speaker 1",
                            "text": "hello",
                            "confidence": 0.9,
                        }
                    ],
                )

                self.assertEqual(inserted[0]["start_ms"], 500)
                self.assertEqual(inserted[0]["absolute_start_ms"], 12500)

                hydrated = store.get_meeting(meeting_id)
                segment = hydrated["segments"][0]
                utterance = hydrated["utterances"][0]

                self.assertEqual(segment["relative_start_ms"], 500)
                self.assertEqual(segment["start_ms"], 12500)
                self.assertEqual(segment["end_ms"], 13500)
                self.assertEqual(utterance["start_ms"], 12500)
                self.assertEqual(utterance["end_ms"], 13500)
            finally:
                store.delete_meeting(meeting_id)


if __name__ == "__main__":
    unittest.main()
