"""ISSUE #120 (dream-loop idle mining): the lazy-ingest primitive (#116) only
runs when a seat ASKS for it (`{mine: path}`), so a seat re-read two papers and
the idle machinery never noticed the growing pile of re-read, unmined artifacts.
The dream-loop pass (`mine_idle_artifacts`) scans the entity store during idle
time for artifacts matching (artifact, no concept children, re-read) and runs
the lazy-ingest miner once per artifact -- policy, not a new primitive. The
concepts arrive from idle mining alone, with no chat mention of them."""

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


def _doc_for_path(est, path):
    for d in (est.docs or {}).values():
        if (d.properties or {}).get("path") == path:
            return d
    return None


PAPER_A = """\
# Equivalence

The equivalence principle states that gravity and acceleration are locally indistinguishable.
"""

PAPER_B = """\
# Symmetry

A conservation law is a statement that a quantity remains constant.
"""


class TestIdleMining(unittest.TestCase):
    def test_idle_mining_yields_concepts_without_chat_mention(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            pa = str(Path(d) / "paper_a.md")
            pb = str(Path(d) / "paper_b.md")
            Path(pa).write_text(PAPER_A)
            Path(pb).write_text(PAPER_B)
            _read(f, est, pa, n=2)
            _read(f, est, pb, n=2)
            # no discourse mention of either concept anywhere: mining alone
            out = N.mine_idle_artifacts(f, est, cap=10)
            self.assertEqual(out["mined"], 2, out)
            self.assertGreater(out["concepts"], 0, out)
            cset = _concepts(est)
            self.assertTrue(cset)
            for k in cset:
                src = est.docs[k].properties["defined_in"]["file"]
                self.assertIn(src, (pa, pb), k)

    def test_one_pass_per_artifact(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper_a.md")
            Path(p).write_text(PAPER_A)
            _read(f, est, p, n=2)
            first = N.mine_idle_artifacts(f, est, cap=10)
            self.assertEqual(first["mined"], 1)
            doc = _doc_for_path(est, p)
            self.assertIn("idle_mined_ts", doc.properties)
            second = N.mine_idle_artifacts(f, est, cap=10)
            self.assertEqual(second["mined"], 0)

    def test_childless_only(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            pa = str(Path(d) / "paper_a.md")
            pb = str(Path(d) / "paper_b.md")
            Path(pa).write_text(PAPER_A)
            Path(pb).write_text(PAPER_B)
            _read(f, est, pa, n=2)
            _read(f, est, pb, n=2)
            # pa already has a concept child -> skipped, pb still mined
            planted = N.concept_entity(est, pa, "existing term",
                                       "an existing definition", line=1)["eid"]
            out = N.mine_idle_artifacts(f, est, cap=10)
            self.assertEqual(out["mined"], 1, out)
            for k in _concepts(est):
                if k == planted:                     # the child that skipped pa
                    continue
                src = est.docs[k].properties["defined_in"]["file"]
                self.assertNotEqual(src, pa, k)

    def test_a_no_salient_spans_file_is_never_retried(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "empty.md")
            Path(p).write_text("")
            _read(f, est, p, n=2)
            first = N.mine_idle_artifacts(f, est, cap=10)
            self.assertEqual(first["mined"], 0)
            doc = _doc_for_path(est, p)
            self.assertIn("idle_mined_ts", doc.properties)
            second = N.mine_idle_artifacts(f, est, cap=10)
            self.assertEqual(second["scanned"], 0, second)

    def test_cap_respected(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            pa = str(Path(d) / "paper_a.md")
            pb = str(Path(d) / "paper_b.md")
            Path(pa).write_text(PAPER_A)
            Path(pb).write_text(PAPER_B)
            _read(f, est, pa, n=2)
            _read(f, est, pb, n=2)
            out = N.mine_idle_artifacts(f, est)  # the default cap is 1
            self.assertEqual(out["mined"], 1, out)
            mined = {d.properties["path"] for d in (est.docs or {}).values()
                     if (d.properties or {}).get("mined_ts")}
            self.assertEqual(len(mined), 1, mined)


if __name__ == "__main__":
    unittest.main()
