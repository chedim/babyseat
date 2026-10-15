"""ISSUE #116 (lazy artifact ingestion): a seat fetched two long papers and
quoted passages while re-reading them -- the field learned the LABOR
(tool:shell mentions, file and command entities) but ZERO concept entities,
because the quoted ideas never crossed the chat extractor path: files live
outside the stream. The ``mine_artifact`` primitive routes the SALIENT spans
of a demonstrably re-read file through the normal extractor (the same
discourse/doc lane ``ingest_events`` uses), ONE bounded pass per artifact.
Never on arrival (a truncated download is never mined): the file's f: entity
must exist AND show a re-read (read_count >= 2); the ``mined_ts`` property
makes a second pass a no-op. ``force`` bypasses both gates."""

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


PAPER = """\
# Field Memory as a Continuity Layer

A memory proxy sits between the client and the model server, and it
remembers per-entity state across turns.

A defined term is a phrase whose meaning the extractor should capture.

A salience selector is a policy that picks which spans are worth keeping.

The proxy stores embeddings and replays them through the gate.
"""


def _read(f, est, path, n=1):
    for _ in range(n):
        N.file_read_entity(f, est, None, path)


def _doc(est, eid):
    return (est.docs or {}).get(MEMBRANE_NS + N.overlay_key(eid))


class TestMineArtifactGates(unittest.TestCase):
    def test_never_read_refuses(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            out = N.mine_artifact(f, est, p)
            self.assertFalse(out["ok"])
            self.assertEqual(out["reason"], "never read")
            self.assertEqual(out["mined"], 0)
            self.assertEqual(out["spans"], 0)

    def test_read_once_refuses(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            _read(f, est, p, n=1)
            out = N.mine_artifact(f, est, p)
            self.assertFalse(out["ok"])
            self.assertEqual(out["reason"], "not re-read yet")
            self.assertEqual(out["mined"], 0)

    def test_reread_mines(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            _read(f, est, p, n=2)
            before = int(f._ostats.get("ev_n") or 0)
            out = N.mine_artifact(f, est, p)
            self.assertTrue(out["ok"])
            self.assertTrue(out["mined"] > 0)
            self.assertTrue(out["spans"] > 0)
            # the doc gains the idempotence markers
            doc = _doc(est, out["eid"])
            self.assertIsNotNone(doc)
            self.assertIn("mined_ts", doc.properties)
            self.assertEqual(doc.properties["mine_spans"], out["spans"])
            # spans were routed: the field grew
            self.assertGreater(int(f._ostats.get("ev_n") or 0), before)

    def test_one_pass_per_artifact(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            _read(f, est, p, n=2)
            first = N.mine_artifact(f, est, p)
            self.assertTrue(first["ok"])
            before = int(f._ostats.get("ev_n") or 0)
            second = N.mine_artifact(f, est, p)
            self.assertFalse(second["ok"])
            self.assertEqual(second["reason"], "already mined")
            self.assertEqual(second["mined"], 0)
            self.assertEqual(int(f._ostats.get("ev_n") or 0), before)

    def test_force_re_mines(self):
        f, est = _world()
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "paper.md")
            Path(p).write_text(PAPER)
            _read(f, est, p, n=2)
            first = N.mine_artifact(f, est, p)
            self.assertTrue(first["ok"])
            out = N.mine_artifact(f, est, p, force=True)
            self.assertTrue(out["ok"])           # bypasses "already mined"
            self.assertEqual(out["spans"], first["spans"])


if __name__ == "__main__":
    unittest.main()
