"""The name ceremony (naming.py): a standalone seat is Membrane until it and
the operator agree on a name; the agreed name is adopted only when a person
can call it -- one word, 2-3 syllables, in the recognizer's vocabulary."""
import os
import struct
import tempfile
import unittest
from unittest import mock

from membrane import naming as N
from membrane import settings as S
from membrane.entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity


def _fst(words):
    b = b"\x00" * 8 + b"".join(struct.pack("<i", len(w)) + w.encode() + struct.pack("<q", i)
                               for i, w in enumerate(["<eps>"] + words))
    return b + struct.pack("<i", 0)


class TestNaming(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": self.tmp.name})
        self.env.start()
        N._VOCAB.clear()
        self.vocab = mock.patch.object(N, "vosk_words", return_value={"juniper", "nova", "wren", "membrane"})
        self.vocab.start()

    def tearDown(self):
        self.vocab.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _ent(self, **pp):
        return QuantumEntity(eid=MEMBRANE_NS + "name", cls=CLASS_CONFIG, properties=pp)

    def test_membrane_until_named(self):
        self.assertEqual(N.current(), "Membrane")
        self.assertIn("none yet", N.card_line())
        self.assertIn("Never pick one alone", N.card_line())

    def test_rules(self):
        v = {"juniper", "nova", "standalone", "argus", "wren"}
        self.assertIsNone(N.check("Juniper", v))
        self.assertIn("taken", N.check("Argus", v))
        self.assertIn("syllables", N.check("Wren", v))
        self.assertIn("recognizer", N.check("Kestrel", v))
        self.assertIn("one plain word", N.check("Mary Jane", v))
        self.assertIsNone(N.check("Kestrel", None))

    def test_fst_symbols(self):
        self.assertEqual(N._fst_symbols(_fst(["hey", "juniper", "!SIL"])), {"hey", "juniper"})

    def test_not_adopted_without_the_operator(self):
        ent = self._ent(name="Juniper")
        out = N.on_edit(None, ent)
        self.assertFalse(out["adopted"])
        self.assertIn("agreed_with", ent.properties["status"])
        self.assertEqual(N.current(), "Membrane")
        self.assertEqual(ent.properties["name"], "")

    def test_adopted_together(self):
        ent = self._ent(name="juniper", agreed_with="Dmitrii")
        self.assertEqual(N.on_edit(None, ent), {"adopted": True, "name": "Juniper"})
        self.assertEqual(N.current(), "Juniper")
        self.assertEqual(S.load()["name"], "Juniper")
        self.assertIn("adopted", ent.properties["status"])
        self.assertIn("you and the operator agreed", N.card_line())

    def test_unhearable_name_refused(self):
        ent = self._ent(name="Kestrel", agreed_with="Dmitrii")
        self.assertFalse(N.on_edit(None, ent)["adopted"])
        self.assertIn("recognizer", ent.properties["status"])
        self.assertEqual(N.current(), "Membrane")

    def test_personalize(self):
        self.assertEqual(N.personalize("let Argus act as you", "Membrane"), "let Membrane act as you")
        self.assertEqual(N.personalize("let Argus act", "Argus"), "let Argus act")


if __name__ == "__main__":
    unittest.main()
