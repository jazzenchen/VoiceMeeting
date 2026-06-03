import unittest

from backend.diarization import split_segments_by_turns


class DiarizationSplitTests(unittest.TestCase):
    def test_splits_segment_by_turns_near_punctuation(self) -> None:
        segments = [
            {
                "start_ms": 0,
                "end_ms": 6000,
                "speaker": "",
                "text": "我先说一句，然后你来回答。",
                "confidence": 0.9,
            }
        ]
        turns = [
            {"start_ms": 0, "end_ms": 3000, "speaker": "Speaker 1"},
            {"start_ms": 3000, "end_ms": 6000, "speaker": "Speaker 2"},
        ]

        result = split_segments_by_turns(segments, turns)

        self.assertEqual(len(result), 2)
        self.assertEqual([item["speaker"] for item in result], ["Speaker 1", "Speaker 2"])
        self.assertEqual(result[0]["start_ms"], 0)
        self.assertEqual(result[0]["end_ms"], 3000)
        self.assertEqual(result[1]["start_ms"], 3000)
        self.assertEqual(result[1]["end_ms"], 6000)
        self.assertEqual("".join(item["text"] for item in result), "我先说一句，然后你来回答。")

    def test_ignores_tiny_turn_overlap(self) -> None:
        segments = [
            {
                "start_ms": 0,
                "end_ms": 6000,
                "speaker": "",
                "text": "这是一段连续说话内容",
                "confidence": None,
            }
        ]
        turns = [
            {"start_ms": 0, "end_ms": 5600, "speaker": "Speaker 1"},
            {"start_ms": 5600, "end_ms": 5850, "speaker": "Speaker 2"},
        ]

        result = split_segments_by_turns(segments, turns)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["speaker"], "Speaker 1")
        self.assertEqual(result[0]["text"], "这是一段连续说话内容")

    def test_keeps_repeated_speaker_turns_separate_when_interleaved(self) -> None:
        segments = [
            {
                "start_ms": 0,
                "end_ms": 9000,
                "speaker": "",
                "text": "第一句。第二句。第三句。",
                "confidence": None,
            }
        ]
        turns = [
            {"start_ms": 0, "end_ms": 3000, "speaker": "Speaker 1"},
            {"start_ms": 3000, "end_ms": 6000, "speaker": "Speaker 2"},
            {"start_ms": 6000, "end_ms": 9000, "speaker": "Speaker 1"},
        ]

        result = split_segments_by_turns(segments, turns)

        self.assertEqual(len(result), 3)
        self.assertEqual([item["speaker"] for item in result], ["Speaker 1", "Speaker 2", "Speaker 1"])
        self.assertEqual("".join(item["text"] for item in result), "第一句。第二句。第三句。")


if __name__ == "__main__":
    unittest.main()
