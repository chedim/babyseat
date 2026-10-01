"""_recall_report on the EntityField: the announcer-seat break (2026-10-01).

The rewrite left one `fld.embedder` read behind (the old field object's
attribute); the entity field's embedder rides `fld.emb` (the LazyEmbedder)
with the Seat's real embedder underneath -- unresolved = `?` from the
LazyEmbedder's model property. A thought-intercept recall turn 500'd the
seat's proxy on every field ask. This pins the contract: no crash, the
unresolved lane degrades to a message, the resolved lane answers, the dump
lane lists events.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF            # noqa: E402
from membrane import opencode as OC          # noqa: E402
from membrane import streams as ST           # noqa: E402
from membrane.capture import LazyEmbedder    # noqa: E402
from membrane.efstore import MemBackend      # noqa: E402
from _ef_fakes import FakeEmbedder           # noqa: E402

T0 = 1_800_000_000.0


def seat(membed):
    return SimpleNamespace(
        membrane=SimpleNamespace(embedder=membed, audit_path=""),
        efield=None)


def field(emb=FakeEmbedder()):
    f = EF.EntityField(MemBackend(), emb, "lab@host")
    f.write_events([ST.Event(stream="discourse", role="user", ts=T0,
                             text="what do you remember about port walk-up?")],
                   session="s:1:0")
    f.process()
    return f


class TestRecallReportOnEntityField(unittest.TestCase):
    def test_unresolved_embedder_degrades_to_a_message(self):
        # write with a resolved embedder, then the seat UNRESOLVED -- the
        # field keeps its sentences, the topic search must degrade, not raise
        f = field()
        f.emb = LazyEmbedder(SimpleNamespace(embedder=None))
        s = seat(None)
        s.efield = f
        out = OC._recall_report(s, "port")
        self.assertIn("Field recall", out)
        self.assertIn("embedder unresolved", out)

    def test_resolved_embedder_never_crashes(self):
        f = field()
        s = seat(FakeEmbedder())
        s.efield = f
        out = OC._recall_report(s, "port walk-up")
        self.assertIn("Field recall", out)

    def test_dump_lane_lists_events(self):
        f = field()
        s = seat(None)
        s.efield = f
        out = OC._recall_report(s, "")
        self.assertIn("numbered events", out)

    def test_recalled_field_object_reported_when_off(self):
        s = seat(None)
        s.efield = None
        out = OC._recall_report(s, "x")
        self.assertIn("the entity field is off", out)


if __name__ == "__main__":
    unittest.main()