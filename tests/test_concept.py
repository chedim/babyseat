"""ISSUE #117 (quote-and-verify): a seat quoted the equivalence principle and
the 1.7 arcsec light-bending figure while re-reading two papers -- verifying
each claim meant an ad-hoc grep against the source on EVERY re-check, because
nothing in the field bound a claim to its evidence. The ``Concept`` entity is
the recipient of those quoted spans: its properties carry the provenance
(``defined_in`` = the artifact + span, ``definition`` = the verbatim quote,
``first_seen`` = the mining event's id) and its getter RE-OPENS the source
span on read, reporting whether the quote still matches -- a changed source
surfaces as drift instead of silent belief."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import (CLASSES, EntityStore,  # noqa: E402
                               MEMBRANE_NS)
from membrane.eids import lint_name  # noqa: E402


SRC = """\
# A concept paper

The equivalence principle states that gravity and acceleration are locally indistinguishable.

It was Einstein's happiest thought.
"""

DEF = "The equivalence principle states that gravity and acceleration are locally indistinguishable."


class _Srv:
    def __init__(self, est):
        self.entity_store = est
        self.events_pool = None
        self.membrane = None
        self.efield = None


def _mint(d, name="equivalence principle", definition=DEF, line=None,
          first_seen=None):
    """Mint a Concept against ``d/paper.md`` and wire a getter host."""
    est = EntityStore(path=str(Path(d) / "e.json"), shared=False)
    p = str(Path(d) / "paper.md")
    Path(p).write_text(SRC)
    if line is None:
        line = SRC.splitlines().index(DEF) + 1
    out = N.concept_entity(est, p, name, definition, line=line,
                           first_seen=first_seen)
    ent = (est.docs or {}).get(out.get("eid"))
    est._srv = _Srv(est)
    return est, out, ent, p


class TestConceptMint(unittest.TestCase):
    def test_mint_stores_the_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            est, out, ent, p = _mint(d)
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["eid"],
                             MEMBRANE_NS + "Concept:equivalence-principle")
            self.assertTrue(out["created"])
            props = ent.properties
            self.assertEqual(props["defined_in"]["file"], p)
            self.assertEqual(props["defined_in"]["line"],
                             SRC.splitlines().index(DEF) + 1)
            self.assertEqual(props["definition"], DEF)
            self.assertIn("getter_script", props)

    def test_mint_is_reuse_not_rebirth(self):
        with tempfile.TemporaryDirectory() as d:
            est, out, ent, p = _mint(d)
            again = N.concept_entity(est, p, "equivalence principle", DEF)
            self.assertTrue(again["ok"])
            self.assertFalse(again["created"])
            self.assertEqual(again["eid"], out["eid"])

    def test_first_seen_rides_through(self):
        with tempfile.TemporaryDirectory() as d:
            est, out, ent, p = _mint(d, first_seen="ev:42")
            self.assertTrue(out["ok"])
            self.assertEqual(ent.properties["first_seen"], "ev:42")

    def test_class_is_registered_and_the_name_is_linted(self):
        self.assertIn("Concept", CLASSES)
        self.assertIsNotNone(lint_name("concept:x"))


class TestConceptDrift(unittest.TestCase):
    def _read(self, est, ent):
        return WA.entity_read_back(est._srv, ent, force=True)

    def test_an_intact_source_does_not_drift(self):
        with tempfile.TemporaryDirectory() as d:
            est, out, ent, p = _mint(d)
            res = self._read(est, ent)
            self.assertTrue(res.get("fired"), res)
            self.assertNotIn("error", res)
            state = res.get("state") or {}
            self.assertIs(state["drift"], False)

    def test_an_edited_definition_drifts(self):
        with tempfile.TemporaryDirectory() as d:
            est, out, ent, p = _mint(d)
            self.assertTrue(self._read(est, ent)["fired"])
            Path(p).write_text(SRC.replace(
                DEF, "The equivalence principle states that gravity and "
                     "acceleration are entirely different."))
            res = self._read(est, ent)
            self.assertTrue(res.get("fired"), res)
            state = res.get("state") or {}
            self.assertIs(state["drift"], True)


if __name__ == "__main__":
    unittest.main()
