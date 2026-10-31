"""The entity mirror's geometry is packed, and a save never re-reads the file
(2026-10-31, sampled: entities.json was 308 MB -- 255 MB of trajectories and 45 MB of
positions as decimal text -- and every 10 s save re-read it whole and re-wrote it whole;
that one thread held the interpreter in 1489 of 1528 samples)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from membrane.entities import EntityStore, QuantumEntity

DIM = 64


def _store(path):
    return EntityStore(path=str(path), shared=False)


class TestPackedMirror(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "entities.json"
        r = np.random.default_rng(3)
        self.pos = [float(x) for x in r.standard_normal(DIM).astype(np.float32)]
        self.hist = [[1000.0 + i, [float(x) for x in r.standard_normal(DIM).astype(np.float32)]]
                     for i in range(5)]
        s = _store(self.path)
        e = QuantumEntity(eid="membrane::thing:a", cls="Component", properties={"name": "a"})
        e.position, e.position_history = list(self.pos), [list(h) for h in self.hist]
        s.docs[e.eid] = e
        s.save()
        self.s = s

    def test_the_geometry_survives_a_reload(self):
        e = _store(self.path).docs["membrane::thing:a"]
        np.testing.assert_allclose(e.position, self.pos, rtol=0, atol=0)
        self.assertEqual([t for t, _ in e.position_history], [t for t, _ in self.hist])
        for (_, got), (_, want) in zip(e.position_history, self.hist):
            np.testing.assert_allclose(got, want, rtol=0, atol=0)

    def test_the_file_holds_no_decimal_vectors(self):
        doc = json.loads(self.path.read_text())["docs"]["membrane::thing:a"]
        ss = doc["state_space"]
        self.assertIsInstance(ss["position"]["tracked"], str)
        self.assertTrue(all(isinstance(p, str) for _, p in ss["trajectory"]))

    def test_a_save_never_re_reads_the_file(self):
        real, reads = Path.read_text, []

        def read_text(p, *a, **k):
            reads.append(str(p))
            return real(p, *a, **k)
        with mock.patch.object(Path, "read_text", read_text):
            self.s.save()
        self.assertEqual(reads, [], "a save re-read the file")
        self.assertTrue(self.s._side_saved)
        self.assertIn("membrane::thing:a", json.loads(self.path.read_text())["docs"])

    def test_an_old_decimal_file_still_loads(self):
        doc = QuantumEntity(eid="membrane::thing:b", cls="Component", properties={"name": "b"})
        doc.position, doc.position_history = list(self.pos), [list(h) for h in self.hist]
        old = Path(tempfile.mkdtemp()) / "entities.json"
        old.write_text(json.dumps({"docs": {doc.eid: doc.to_doc()}}))
        e = _store(old).docs["membrane::thing:b"]
        np.testing.assert_allclose(e.position, self.pos, rtol=0, atol=0)
        self.assertEqual(len(e.position_history), 5)


if __name__ == "__main__":
    unittest.main()
