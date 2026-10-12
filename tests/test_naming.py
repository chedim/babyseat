"""The name ceremony (naming.py): a standalone seat names itself, a name a
person can call -- one word, 2-3 syllables, in the recognizer's vocabulary."""
import os
import struct
import tempfile
import unittest
from unittest import mock

from membrane import naming as N
from membrane import settings as S


def _fst(words):
    b = b"\x00" * 8 + b"".join(struct.pack("<i", len(w)) + w.encode() + struct.pack("<q", i)
                               for i, w in enumerate(["<eps>"] + words))
    return b + struct.pack("<i", 0)


class TestNaming(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_rules(self):
        v = {"juniper", "nova", "standalone", "argus", "wren"}
        self.assertTrue(N.acceptable("Juniper", v))
        self.assertTrue(N.acceptable("Nova", v))
        self.assertFalse(N.acceptable("Argus", v))           # the boat's seat
        self.assertFalse(N.acceptable("Standalone", v))      # the folder, not a name
        self.assertFalse(N.acceptable("Wren", v))            # one syllable: too easy to miss
        self.assertFalse(N.acceptable("Kestrel", v))         # the recognizer can't hear it
        self.assertFalse(N.acceptable("Mary Jane", v))
        self.assertTrue(N.acceptable("Kestrel", None))       # no recognizer at hand: no vocab rule

    def test_fst_symbols(self):
        self.assertEqual(N._fst_symbols(_fst(["hey", "juniper", "!SIL"])), {"hey", "juniper"})

    def test_ensure_keeps_the_first_good_pick(self):
        with mock.patch.object(N, "propose", return_value=["Argus", "Wren", "Ember", "Nova"]), \
             mock.patch.object(N, "vosk_words", return_value={"ember", "nova", "wren"}):
            self.assertEqual(N.ensure("http://x", "m", []), "Ember")
        self.assertEqual(S.load()["name"], "Ember")
        with mock.patch.object(N, "propose", side_effect=AssertionError("asked twice")):
            self.assertEqual(N.ensure("http://x", "m", []), "Ember")   # named once, kept

    def test_ensure_falls_back_when_the_model_is_silent(self):
        with mock.patch.object(N, "propose", return_value=[]), \
             mock.patch.object(N, "vosk_words", return_value=None):
            self.assertEqual(N.ensure("http://x", "m", []), N.FALLBACK[0])

    def test_personalize(self):
        self.assertEqual(N.personalize("let Argus act as you", "Ember"), "let Ember act as you")
        self.assertEqual(N.personalize("let Argus act", "Argus"), "let Argus act")


if __name__ == "__main__":
    unittest.main()
