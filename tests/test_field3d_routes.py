"""The 3D field's routes on a real (tiny) entity field: the projection
covers every live entity, the pid serves tiles and locates, the cache
reuses within the fresh window, the seat relay passes the three reads."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ef_fakes as _fakes  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import server as MS  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402


class _Srv:
    pass


def _field():
    f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
    for i in range(6):
        f.write_events(ST.split_turn(
            f"membrane/router.py and membrane/lanes.py talk about the gateway {i}",
            f"membrane/router.py retries; membrane/efield.py flushes round {i}"), session="s")
    f.process()
    f.flush()
    return f


class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv()
        self.srv.efield = _field()

    def _live(self):
        f = self.srv.efield
        return [d["eid"] for _, d in f.b.scan("entity_states", {"origin": f.origin, "tier": "live"})
                if d.get("state")]

    def test_the_projection_covers_every_live_entity(self):
        p = L.field3d_projection(self.srv, "")
        live = self._live()
        self.assertGreater(len(live), 0)
        self.assertEqual(p.meta()["n_total"], len(live))
        for eid in live:
            self.assertIsNotNone(p.locate(eid), eid)

    def test_the_cache_reuses_and_keeps_pids(self):
        p1 = L.field3d_projection(self.srv, "pc1,pc2,pc3")
        p2 = L.field3d_projection(self.srv, "")
        self.assertIs(p1, p2)                                 # same axes, fresh
        live = self._live()
        p3 = L.field3d_projection(self.srv, f"e:{live[0]},pc1,pc2")
        self.assertIsNot(p3, p1)
        self.assertIn(p1.pid, self.srv._field3d)
        self.assertIn(p3.pid, self.srv._field3d)

    def test_an_entity_axis_puts_the_reference_near_zero(self):
        live = self._live()
        p = L.field3d_projection(self.srv, f"e:{live[0]},pc1,pc2")
        xs = sorted(p.locate(e)["p"][0] for e in live)
        self.assertLessEqual(p.locate(live[0])["p"][0], xs[len(xs) // 2])

    def test_the_seat_relays_the_three_reads(self):
        class S:
            server_address = ("127.0.0.1", 11500)
        for path in ("/membrane/map/field3d?axes=pc1", "/membrane/map/tile?pid=x&l=0&x=0&y=0&z=0",
                     "/membrane/map/locate?pid=x&eid=y"):
            self.assertTrue(MS.seat_read_url(S(), path), path)


if __name__ == "__main__":
    unittest.main()
