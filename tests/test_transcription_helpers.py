import unittest

from backend.transcript import UNRECOGNIZED_TEXT
from backend.transcription_helpers import segments_or_unrecognized


class TranscriptionHelperTests(unittest.TestCase):
    def test_probe_chunks_do_not_emit_unrecognized_placeholders(self) -> None:
        result = segments_or_unrecognized({"cut_reason": "VAD 探测", "duration_ms": 3000}, [])

        self.assertEqual(result, [])

    def test_regular_empty_chunks_still_emit_unrecognized_placeholders(self) -> None:
        result = segments_or_unrecognized({"cut_reason": "VAD 语音结束", "duration_ms": 3000}, [])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], UNRECOGNIZED_TEXT)


if __name__ == "__main__":
    unittest.main()
