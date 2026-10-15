"""THE MICROPHONE'S LENGTH (2026-10-15): the operator kept getting cut off -- a
recording stopped at 60 s and sent what it had. The limit lived in THREE places (the
page's recorder, the standalone seat's /ui/stt, Home Assistant's /api/membrane/stt)
plus a hidden fourth: HA's own web server refuses a body over 16 MiB. Now 8 minutes
everywhere -- the longest the HA path carries (16 kHz s16 mono = 32 kB/s) -- and the
transcription wait scales with it. This test keeps the places in agreement."""
import re
import unittest
from pathlib import Path

from membrane import ui_serve as U

ROOT = Path(__file__).resolve().parent.parent
REC_S = 8 * 60
WAV_BYTES = 44 + REC_S * 16000 * 2
HA_BODY_CAP = 16 * 1024 * 1024


def _num(text, name):
    m = re.search(rf"\b{name}\s*=\s*([0-9_.]+)", text)
    return float(m.group(1).replace("_", "")) if m else None


class TestTheMicrophoneIsLongEnough(unittest.TestCase):
    def test_the_page_records_eight_minutes(self):
        self.assertEqual(_num((ROOT / "membrane/ui/home.js").read_text(), "REC_MAX_S"), REC_S)

    def test_the_standalone_seat_takes_it(self):
        self.assertGreaterEqual(U.MAX_STT, WAV_BYTES)
        self.assertGreaterEqual(U.STT_TIMEOUT_S, 120)

    def test_home_assistant_takes_it_and_its_server_does_too(self):
        src = (ROOT / "custom_components/membrane/stt.py").read_text()
        self.assertGreaterEqual(_num(src, "MAX_BYTES"), WAV_BYTES)
        self.assertLess(_num(src, "MAX_BYTES"), HA_BODY_CAP)      # HA refuses bigger bodies itself
        self.assertGreaterEqual(_num(src, "STT_TIMEOUT_S"), 120)

    def test_the_refusal_says_the_real_limit(self):
        self.assertNotIn("60 s", (ROOT / "membrane/ui_serve.py").read_text())
        self.assertNotIn("60 s", (ROOT / "custom_components/membrane/stt.py").read_text())


if __name__ == "__main__":
    unittest.main()
