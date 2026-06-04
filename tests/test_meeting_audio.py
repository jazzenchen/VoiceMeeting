import tempfile
import unittest
import wave
from pathlib import Path

from backend.meeting_audio import SAMPLE_RATE, build_meeting_audio


def write_silence(path: Path, duration_ms: int) -> None:
    frame_count = int(round(duration_ms * SAMPLE_RATE / 1000))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"\x00\x00" * frame_count)


def duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return int(round(handle.getnframes() * 1000 / handle.getframerate()))


class MeetingAudioTests(unittest.TestCase):
    def test_trims_overlapping_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.wav"
            second = root / "second.wav"
            output = root / "meeting.wav"
            write_silence(first, 1000)
            write_silence(second, 1000)

            result = build_meeting_audio(
                [
                    {
                        "seq": 1,
                        "started_at_ms": 0,
                        "ended_at_ms": 1000,
                        "duration_ms": 1000,
                        "wav_path": str(first),
                    },
                    {
                        "seq": 2,
                        "started_at_ms": 900,
                        "ended_at_ms": 1900,
                        "duration_ms": 1000,
                        "wav_path": str(second),
                    },
                ],
                output,
            )

            self.assertEqual(result["duration_ms"], 1900)
            self.assertEqual(duration_ms(output), 1900)

    def test_inserts_silence_for_timeline_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.wav"
            second = root / "second.wav"
            output = root / "meeting.wav"
            write_silence(first, 500)
            write_silence(second, 500)

            result = build_meeting_audio(
                [
                    {
                        "seq": 1,
                        "started_at_ms": 0,
                        "ended_at_ms": 500,
                        "duration_ms": 500,
                        "wav_path": str(first),
                    },
                    {
                        "seq": 2,
                        "started_at_ms": 1000,
                        "ended_at_ms": 1500,
                        "duration_ms": 500,
                        "wav_path": str(second),
                    },
                ],
                output,
            )

            self.assertEqual(result["duration_ms"], 1500)
            self.assertEqual(duration_ms(output), 1500)


if __name__ == "__main__":
    unittest.main()
