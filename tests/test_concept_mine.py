"""ISSUE #118 (document-mode concept mining): the extractor learns per stream
(chat mentions), so a concept quoted once or twice in chat never fires the
mention bar -- even when the source document is fully in hand. #116's
``mine_artifact`` routes a re-read document's salient spans through the
extractor; this PR makes those spans ALSO mint ``Concept`` entities (the #117
recipient): a defined-term sentence ("X is/means/states ...") and an ATX
section header each become a Concept whose provenance points at the source
(``defined_in`` = artifact + line, ``definition`` = the verbatim quote,
``first_seen`` = the mined event's key) and whose getter flags drift. The
never-read / read-once / truncated gates still plant nothing."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder
from membrane.efield import EntityField
from membrane.efstore import MemBackend
from membrane.entities import EntityStore, MEMBRANE_NS
from membrane import night as N


PAPER = """\
# Field Memory as a Continuity Layer

A memory proxy sits between the client and the model server.

A defined term is a phrase whose meaning the extractor should capture.

The equivalence principle states that gravity and acceleration are locally indistinguishable.

The proxy stores embeddings and replays them through the gate.
"""

DEF_SENT = "A defined term is a phrase whose meaning the extractor should capture."
STATE_SENT = ("The equivalence principle states that gravity and acceleration "
              "are locally indistinguishable.")
HEADER = "Field Memory as a Continuity Layer"


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    est = EntityStore(path=None, shared=False)
    return f, est


def _read(f, est, path, n=1):
    for _ in range(n):
        N.file_read_entity(f, est, None, path)


def _concepts(est):
    return {k for k in (est.docs or {})
            if k.startswith(MEMBRANE_NS + "Concept:")}


class TestDocumentModeConcepts(unittest.TestCase):
    def test_mining_a_reread_paper_mints_concepts(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            _read(f, est, p, n=2)
            out = N.mine_artifact(f, est, p)
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["concepts"], 3)
            cset = _concepts(est)
            self.assertIn(MEMBRANE_NS + "Concept:defined-term", cset)
            self.assertIn(MEMBRANE_NS + "Concept:equivalence-principle", cset)
            self.assertIn(MEMBRANE_NS + "Concept:field-memory-as-a-continuity-layer",
                          cset)
            for slug in ("defined-term", "equivalence-principle",
                         "field-memory-as-a-continuity-layer"):
                props = est.docs[MEMBRANE_NS + "Concept:" + slug].properties
                self.assertEqual(props["defined_in"]["file"], p)
                self.assertIsNotNone(props["defined_in"]["line"])
                self.assertTrue(props["definition"])
                self.assertTrue(props["first_seen"])
                self.assertIn("getter_script", props)

    def test_the_definition_line_rides_the_provenance(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            _read(f, est, p, n=2)
            N.mine_artifact(f, est, p)
            def_ent = est.docs[MEMBRANE_NS + "Concept:defined-term"]
            self.assertEqual(def_ent.properties["defined_in"]["line"],
                             PAPER.splitlines().index(DEF_SENT) + 1)
            eq = est.docs[MEMBRANE_NS + "Concept:equivalence-principle"]
            self.assertEqual(eq.properties["defined_in"]["line"],
                             PAPER.splitlines().index(STATE_SENT) + 1)
            self.assertEqual(eq.properties["definition"], STATE_SENT)

    def test_a_never_read_artifact_plants_nothing(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            out = N.mine_artifact(f, est, p)
            self.assertEqual(out["reason"], "never read")
            self.assertEqual(_concepts(est), set())

    def test_a_read_once_artifact_plants_nothing(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            _read(f, est, p, n=1)
            out = N.mine_artifact(f, est, p)
            self.assertEqual(out["reason"], "not re-read yet")
            self.assertEqual(_concepts(est), set())

    def test_one_pass_still_holds(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            _read(f, est, p, n=2)
            first = N.mine_artifact(f, est, p)
            self.assertTrue(first["ok"])
            before = _concepts(est)
            second = N.mine_artifact(f, est, p)
            self.assertEqual(second["reason"], "already mined")
            self.assertEqual(_concepts(est), before)


if __name__ == "__main__":
    unittest.main()
