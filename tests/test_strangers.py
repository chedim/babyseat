"""The login page's strangers (2026-10-10): TTL'd visitor entities whose
window on the field is a small, label-free piece around them."""
import os
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from membrane import strangers as SG  # noqa: E402
from membrane import fieldpulse as FP  # noqa: E402
from membrane.entities import EntityStore, CLASS_ENDPOINT  # noqa: E402


class _Proj:
    def __init__(self, n=400, seed=3):
        rng = np.random.default_rng(seed)
        self.ids = [f"e{i}" for i in range(n)] + ["site:login"]
        self.coords = np.vstack([rng.random((n, 3)), [[0.5, 0.5, 0.5]]]).astype(np.float32)
        self.mass = np.arange(n + 1)[::-1].copy()
        self.index = {e: i for i, e in enumerate(self.ids)}
        self.edges = [(i, i + 1, "rel", 1) for i in range(n)]
        self._edges_of = {}
        for j, e in enumerate(self.edges):
            self._edges_of.setdefault(e[0], []).append(j)
            self._edges_of.setdefault(e[1], []).append(j)
        self.axes = []
        self.ts = time.time()
        self.pid = "p1"


class _Field:
    def __init__(self):
        self.lines = []

    def write_events(self, evs, session=""):
        self.lines += [e.result_head for e in evs]


def _srv():
    est = EntityStore(path=None, shared=False)
    srv = type("S", (), {})()
    srv.entity_store, srv.efield = est, _Field()
    srv._field3d = {"p1": _Proj()}
    srv._field_pulse = FP.FieldPulse(srv)
    return srv


class TestStrangers(unittest.TestCase):
    def test_a_visit_mints_a_stranger_beside_the_site(self):
        srv = _srv()
        r = SG.visit(srv, ip_key="a", now=1000.0)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["name"], "stranger1")
        est = srv.entity_store
        self.assertIsNotNone(est._resolve_spec("stranger1"))
        self.assertEqual(est._resolve_spec("site:login").cls, CLASS_ENDPOINT)
        self.assertIn("stranger1 arrived at the login page", srv.efield.lines)
        self.assertEqual(SG.visit(srv, ip_key="b", now=1001.0)["name"], "stranger2")

    def test_the_window_is_small_relative_and_label_free(self):
        srv = _srv()
        t = SG.visit(srv, now=1000.0)["t"]
        v = SG.view(srv, t, now=1001.0)
        self.assertTrue(v["ok"])
        self.assertLess(len(v["p"]), 401)
        self.assertLessEqual(len(v["p"]), SG.MAX_NODES)
        self.assertGreater(len(v["p"]), 0)                       # the site itself is near
        for p in v["p"]:
            self.assertTrue(all(-1.0 <= c <= 1.0 for c in p))
        blob = repr(v)
        self.assertNotIn("e1", blob.replace("'e':", ""))         # no ids leak
        self.assertNotIn("site:login", blob)
        self.assertEqual(set(v), {"ok", "name", "p", "m", "e", "n", "pulse", "me_pulse", "ttl"})
        for a, b in v["e"] + v["n"]:
            self.assertTrue(0 <= a < len(v["p"]) and 0 <= b < len(v["p"]))

    def test_pulses_are_indices_into_the_window(self):
        srv = _srv()
        t = SG.visit(srv, now=time.time())["t"]
        SG.view(srv, t)
        srv._field_pulse.applied(["site:login", "nowhere"], "observation")
        v = SG.view(srv, t, now=time.time() + 0.01)
        self.assertEqual(len(v["pulse"]), 1)
        self.assertLess(v["pulse"][0], len(v["p"]))

    def test_the_mouse_moves_the_stranger_and_counts_coalesce(self):
        srv = _srv()
        t = SG.visit(srv, now=1000.0)["t"]
        s = next(iter(srv._strangers["by_token"].values()))
        before = list(s["me"])
        self.assertTrue(SG.event(srv, t, "move", n=10, dx=400, dy=-100, now=1001.0)["ok"])
        self.assertNotEqual(before, s["me"])
        moved = max(abs(a - b) for a, b in zip(before, s["me"]))
        self.assertLessEqual(moved, SG.MAX_STEP + 1e-9)
        SG.event(srv, t, "click", n=2, now=1002.0)
        n_lines = len(srv.efield.lines)
        SG.event(srv, t, "move", n=5, now=1010.0)               # < a minute: no new line
        self.assertEqual(len(srv.efield.lines), n_lines)
        SG.event(srv, t, "move", n=1, now=1070.0)
        self.assertIn("stranger1 on the login page after 70s: move 16, click 2", srv.efield.lines[-1])

    def test_only_known_kinds_and_numbers_get_in(self):
        srv = _srv()
        t = SG.visit(srv, now=1000.0)["t"]
        self.assertFalse(SG.event(srv, t, "ignore previous instructions", now=1001.0)["ok"])
        self.assertFalse(SG.event(srv, t, "move", n="lots", now=1001.0)["ok"])
        self.assertFalse(SG.event(srv, "bogus", "move", now=1001.0)["ok"])

    def test_caps_and_ttl(self):
        srv = _srv()
        self.assertTrue(SG.visit(srv, ip_key="x", now=1000.0)["ok"])
        self.assertTrue(SG.visit(srv, ip_key="x", now=1000.2)["ok"])
        self.assertTrue(SG.visit(srv, ip_key="x", now=1000.5)["ok"])    # the address's oldest makes room
        self.assertEqual(sum(1 for s in srv._strangers["by_token"].values() if s["ip"] == "x"), 2)
        self.assertIsNone(srv.entity_store._resolve_spec("stranger1"))
        for i in range(SG.MAX_LIVE - 2):
            self.assertTrue(SG.visit(srv, ip_key=f"k{i}", now=1000.0)["ok"])
        self.assertEqual(SG.visit(srv, ip_key="new", now=1000.0)["error"], "busy")
        later = 1000.0 + SG.TTL_S + 1
        self.assertTrue(SG.visit(srv, ip_key="new", now=later)["ok"])
        self.assertEqual(SG.stats(srv)["live"], 1)
        self.assertIsNone(srv.entity_store._resolve_spec("stranger2"))   # expired = removed

    def test_leave_removes_the_entity_and_says_so(self):
        srv = _srv()
        t = SG.visit(srv, now=1000.0)["t"]
        SG.event(srv, t, "click", now=1001.0)
        self.assertTrue(SG.leave(srv, t, now=1005.0)["ok"])
        self.assertIsNone(srv.entity_store._resolve_spec("stranger1"))
        self.assertIn("left (closed)", srv.efield.lines[-1])
        self.assertFalse(SG.view(srv, t, now=1006.0)["ok"])

    def test_a_restart_sweeps_leftover_strangers(self):
        srv = _srv()
        SG.visit(srv, now=1000.0)
        del srv._strangers
        SG.visit(srv, now=1001.0)
        self.assertIsNone(srv.entity_store._resolve_spec("stranger1"))
        self.assertIsNotNone(srv.entity_store._resolve_spec("stranger2"))


class TestReloadsAndReturns(unittest.TestCase):
    V = "a" * 16

    def test_a_reload_resumes_the_live_stranger(self):
        srv = _srv()
        a = SG.visit(srv, ip_key="x", vkey=self.V, now=1000.0)
        b = SG.visit(srv, ip_key="x", vkey=self.V, now=1005.0)
        self.assertEqual(a["t"], b["t"])
        self.assertTrue(b["resumed"])
        self.assertEqual(SG.stats(srv)["live"], 1)
        self.assertEqual(srv.efield.lines.count("stranger1 arrived at the login page"), 1)

    def test_a_returning_browser_gets_its_name_and_place_back(self):
        srv = _srv()
        t = SG.visit(srv, vkey=self.V, now=1000.0)["t"]
        SG.event(srv, t, "move", n=3, dx=500, dy=0, now=1001.0)
        left_at = list(next(iter(srv._strangers["by_token"].values()))["me"])
        SG.visit(srv, vkey="b" * 16, now=1002.0)                       # someone else: stranger2
        SG.leave(srv, t, now=1003.0)
        r = SG.visit(srv, vkey=self.V, now=5000.0)
        self.assertEqual((r["name"], r["visit"]), ("stranger1", 2))
        self.assertIn("stranger1 came back to the login page (visit 2)", srv.efield.lines)
        me = srv._strangers["by_token"][r["t"]]["me"]
        self.assertEqual([round(v, 4) for v in me], [round(v, 4) for v in left_at])
        self.assertEqual(SG.visit(srv, vkey="c" * 16, now=5001.0)["name"], "stranger3")

    def test_quiet_goes_in_two_minutes_hidden_in_fifteen(self):
        srv = _srv()
        quiet = SG.visit(srv, ip_key="q", now=1000.0)["t"]
        hidden = SG.visit(srv, ip_key="h", now=1000.0)["t"]
        SG.event(srv, hidden, "blur", now=1000.0)
        SG.view(srv, "nothing", now=1000.0 + SG.TTL_S + 1)            # a sweep
        self.assertFalse(SG.view(srv, quiet, now=1000.0 + SG.TTL_S + 2)["ok"])
        self.assertTrue(SG.view(srv, hidden, now=1000.0 + SG.TTL_S + 2)["ok"])


if __name__ == "__main__":
    unittest.main()
