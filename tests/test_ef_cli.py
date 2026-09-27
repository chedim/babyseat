"""Stage 16 on the entity field: ingest as DOC events, reembed, the viewer's
entity cloud."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import ingest as IG  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


class _Emb2(FakeEmbedder):
    model = "other-embedder"


class TestIngest(unittest.TestCase):
    def test_docs_become_events_once(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "o")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notes.md"
            p.write_text("# Router\n\nmembrane/router.py picks the cheapest model that "
                         "passes the tool check, and falls back to the local one.\n")
            r = IG.ingest_events(f, [str(p)], root=d)
            self.assertEqual((r["absorbed"], r["dedup"]), (1, 0))
            r2 = IG.ingest_events(f, [str(p)], root=d)
            self.assertEqual((r2["absorbed"], r2["dedup"]), (0, 1))
        (ev,) = [d for _, d in f.b.scan("events")]
        self.assertEqual((ev["role"], ev["src"], ev["status"]), ("doc", "ingest:notes.md", "applied"))
        self.assertEqual(f.get("f:membrane/router.py").n_by_stream, {"discourse": 1})


class TestReembed(unittest.TestCase):
    def test_reembed_equals_a_fresh_field_on_the_new_model(self):
        texts = ["membrane/router.py picks the model", "the gateway in membrane/router.py is slow"]
        a = EF.EntityField(MemBackend(), FakeEmbedder(), "o")
        a.write_events([ST.Event(stream="discourse", role="user", text=t, ts=10.0 + i)
                        for i, t in enumerate(texts)], session="s")
        a.process()
        n = a.reembed(_Emb2())
        self.assertEqual(n, 2)
        self.assertEqual(a.model, "other-embedder")
        b = EF.EntityField(MemBackend(), _Emb2(), "o")
        b.write_events([ST.Event(stream="discourse", role="user", text=t, ts=10.0 + i)
                        for i, t in enumerate(texts)], session="s")
        b.process()
        ra, rb = a.get("f:membrane/router.py"), b.get("f:membrane/router.py")
        self.assertEqual(ra.n, rb.n)
        self.assertEqual([d["n"] for _, d in sorted(a.b.scan("events"))],
                         [d["n"] for _, d in sorted(b.b.scan("events"))])


class TestViewer(unittest.TestCase):
    def test_entity_points(self):
        from membrane.opencode import _entity_points
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "o")
        f.write_events([ST.Event(stream="discourse", role="user", ts=1.0 + i,
                                 text=f"membrane/m{i}.py does part {i}") for i in range(6)], session="s")
        f.process()

        class Srv:
            pass
        pts = _entity_points(Srv, f)
        self.assertEqual(len(pts), len([1 for _, d in f.b.scan("entity_states") if d["tier"] == "live"]))
        self.assertEqual(len(pts[0]["p"]), 3)
        self.assertIn("mass", pts[0])


if __name__ == "__main__":
    unittest.main()
