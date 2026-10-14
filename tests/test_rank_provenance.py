"""Issue #9: a tool-observed file must outrank a prose-only phantom.

A doc names membrane/router.py (absent, prose-only); a tool observation
sees membrane/kernel.py. A semantic ask must not rank the phantom above
the observed file -- nearness may not mint rank that evidence never paid.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import ingest as IG  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


class TestRankProvenance(unittest.TestCase):
    def test_observed_code_outranks_prose_phantom(self):
        d = Path(tempfile.mkdtemp())
        emb = FakeEmbedder()
        f = EF.EntityField(MemBackend(), emb, "o")
        doc = d / "NOTES.md"
        doc.write_text(
            "move the ingest loop into membrane/router.py\n"
            "the ingest loop is the heart of the router\n"
            "membrane/kernel.py is far from the router\n"
        )
        IG.ingest_events(f, [str(doc)], root=d)
        f.write_events([ST.Event(stream="observation", tool="shell",
                                 args={"cmd": "cat membrane/kernel.py"},
                                 outcome="ok", code="cat membrane/kernel.py",
                                 text="membrane/kernel.py ok",
                                 result_head="membrane/kernel.py ok", ts=20.0)])
        f.process()   # extraction runs on process(), not on write_events
        qvec, = emb.embed(["the ingest loop of the router"])
        eids = list(EB.question_entities(f, "the ingest loop of the router", qvec, 6))
        self.assertIn("f:membrane/kernel.py", eids, f"observed file absent: {eids}")
        self.assertIn("f:membrane/router.py", eids, f"phantom absent: {eids}")
        self.assertLess(eids.index("f:membrane/kernel.py"),
                        eids.index("f:membrane/router.py"),
                        f"phantom outranks tool-observed code: {eids}")


if __name__ == "__main__":
    unittest.main()
