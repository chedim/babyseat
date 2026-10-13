"""The 3D field's routes on a real (tiny) entity field: the projection
covers every live entity, the pid serves tiles and locates, the cache
reuses within the fresh window, the seat relay passes the three reads."""

import os
import sys
import types
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


class TestWholeCatalogSearch(unittest.TestCase):
    """The search route (2026-10-13, the operator: the search box must
    reach EVERY entity, not the rendered budget's): label/eid matches,
    starts-with ranked first, the limit holds, no query and no store
    answer empty."""

    @staticmethod
    def _ent(eid, label, cls="person"):
        return types.SimpleNamespace(eid=eid, cls=cls,
                                     describe=lambda label=label: label)

    def _fire(self, qs, ents):
        ents = [self._ent(*e) for e in ents] if ents is not None else None
        out = []
        r = types.SimpleNamespace(path="/membrane/map/search" + qs,
                                  server=types.SimpleNamespace(
                                      entity_store=types.SimpleNamespace(
                                          docs={e.eid: e for e in ents})
                                      if ents is not None else None))
        r._json_out = out.append
        L.MembraneOpenAIHandler._map_search(r)
        return out[0]

    ENTITIES = [("w:agatha", "Agatha, the operator's seat"),
                ("w:router", "the gateway router"),
                ("person:zoe", "Zoe, a visitor")]

    def test_label_and_eid_match_and_starts_with_ranks_first(self):
        d = self._fire("?q=agat", self.ENTITIES)
        self.assertEqual([h["id"] for h in d["hits"]], ["w:agatha"])
        self.assertEqual(d["hits"][0]["kind"], "person")
        # a substring hit from the label's middle (not its head)
        d = self._fire("?q=router", self.ENTITIES)
        self.assertEqual([h["id"] for h in d["hits"]], ["w:router"])
        # an eid's head matches too
        d = self._fire("?q=w%3Ar", self.ENTITIES)
        self.assertEqual([h["id"] for h in d["hits"]], ["w:router"])

    def test_the_limit_holds(self):
        d = self._fire("?q=w%3A&limit=2", self.ENTITIES)
        self.assertEqual(len(d["hits"]), 2)

    def test_no_query_and_no_store_answer_empty(self):
        self.assertEqual(self._fire("?q=", self.ENTITIES), {"q": "", "hits": []})
        self.assertEqual(self._fire("?q=x", None), {"q": "x", "hits": []})

    def test_the_route_holds_a_seat_read_pass(self):
        class S:
            server_address = ("127.0.0.1", 11500)
        self.assertTrue(MS.seat_read_url(S(), "/membrane/map/search?q=x"))


if __name__ == "__main__":
    unittest.main()
