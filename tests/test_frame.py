"""ENTITIES AS AXES (2026-10-08, the operator: "does membrane implement
entities as axes of the field? ... lets try it", red first).

The cortex experiment made each detected sensor an axis (one direction per
sensor, a momentum along it). Membrane kept entities as POINTS. Two cheap,
meaningful versions:

  * THE FIT'S COORDINATES: every event is already rebuilt as a
    non-negative mix of its mentioned entities' states (efield.fit, E18);
    the weights were thrown away -- now they ride the event as `coords`,
    its sparse coordinates in entity terms.
  * THE WATCH FRAME: whatever the seat WATCHES (the attention inventory:
    watched entities' states, the board watches' directions) is a small
    live coordinate frame; every message is measured along each axis, a
    fast and a slow average per axis say "the field leans toward X more
    than usual". The seat chooses its axes by choosing what it watches.
"""

import os
import sys
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ef_fakes as _fakes  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import frame as FR  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402


class TestFitCoords(unittest.TestCase):
    def test_fit_weights_are_returned(self):
        a, b = np.eye(4)[0], np.eye(4)[1]
        x = EF.unit(0.8 * a + 0.2 * b)
        xh, w = EF.fit_weights(x, [a, b])
        self.assertAlmostEqual(w[0] / (w[0] + w[1]), 0.8, places=2)
        self.assertTrue(np.allclose(xh, EF.fit(x, [a, b])))

    def test_events_carry_their_entity_coordinates(self):
        f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
        seen = []
        f.listeners.append(lambda k, ev, x: seen.append(ev))
        for i in range(6):
            f.write_events(ST.split_turn(f"membrane/router.py and membrane/lanes.py round {i}",
                                         f"membrane/router.py retries round {i}"), session="s")
        f.process()
        coords = [ev.get("coords") for ev in seen if ev.get("coords")]
        self.assertTrue(coords)
        for c in coords:
            for eid, w in c:
                self.assertIsInstance(eid, str)
                self.assertGreater(w, 0)


class _Field:
    def __init__(self, states):
        self.states = states

    def get(self, key):
        v = self.states.get(key)
        if v is None:
            return None

        class St:
            pass
        s = St()
        s.state, s.tier = v, "live"
        return s


class _Watch:
    def __init__(self, wid, label, vec):
        self.wid, self.label, self.probe = wid, label, list(vec)


class _Board:
    def __init__(self, watches):
        self.watches = {w.wid: w for w in watches}


class _Srv:
    pass


def _srv(entity_axes, board_axes=()):
    srv = _Srv()
    srv.efield = _Field({k: v for k, v in entity_axes.items()})
    srv._watch_board = _Board([_Watch(i + 1, lbl, v) for i, (lbl, v) in enumerate(board_axes)])

    class Est:
        docs = {}
    srv.entity_store = Est()
    return srv


class TestFrame(unittest.TestCase):
    def setUp(self):
        self.bms, self.anchor, self.other = np.eye(8)[0], np.eye(8)[1], np.eye(8)[5]
        self.srv = _srv({"c:bms": self.bms}, [("anchor drag", self.anchor)])
        self.fr = FR.Frame(self.srv, axes_fn=lambda: [("c:bms", "entity"), ("#1 anchor drag", "board")])

    def test_axes_are_what_is_watched(self):
        self.fr.refresh(now=0)
        self.assertEqual(sorted(a for a in self.fr.axes), ["#1 anchor drag", "c:bms"])

    def test_a_message_along_an_axis_raises_it(self):
        self.fr.refresh(now=0)
        for t in range(40):                                # the usual: unrelated talk
            self.fr.observe(EF.unit(self.other + 0.05 * np.random.default_rng(t).normal(size=8)), now=t * 30)
        for t in range(6):                                 # now the battery comes up
            self.fr.observe(EF.unit(self.bms + 0.2 * self.other), now=1200 + t * 30)
        r = {x["axis"]: x for x in self.fr.readings(now=1400)}
        self.assertGreater(r["c:bms"]["lean"], 0.2)
        self.assertLess(abs(r["#1 anchor drag"]["lean"]), 0.1)

    def test_the_line_names_the_leaning_axes(self):
        self.fr.refresh(now=0)
        for t in range(30):
            self.fr.observe(EF.unit(self.other), now=t * 30)
        for t in range(5):
            self.fr.observe(EF.unit(self.bms), now=1000 + t * 30)
        line = self.fr.line(now=1200)
        self.assertIn("c:bms", line)
        self.assertIn("frame", line)

    def test_an_empty_frame_says_nothing(self):
        fr = FR.Frame(_srv({}), axes_fn=lambda: [])
        fr.refresh(now=0)
        fr.observe(EF.unit(self.other), now=1)
        self.assertEqual(fr.line(now=2), "")

    def test_the_inventory_feeds_the_axes(self):
        class Ent:
            properties = {"watch_ops": [{"op": "near", "rel": "self", "z": 0.3}]}

        class Est:
            docs = {"membrane::c:bms": Ent()}
            tracks = {"component:ha:light.salon": {"key": "component:ha:light.salon"}}
        srv = _srv({"m:c:bms": self.bms, "m:component:ha:light.salon": self.other})
        srv.entity_store = Est()
        srv._watch_board = _Board([_Watch(3, "anchor drag", self.anchor)])
        axes = FR.watched_axes(srv)
        self.assertIn(("membrane::c:bms", "entity"), axes)
        self.assertIn(("#3 anchor drag", "board"), axes)
        self.assertIn(("component:ha:light.salon", "track"), axes)       # tracks are watching too
        fr = FR.Frame(srv)
        fr.refresh(now=0)
        self.assertEqual(sorted(fr.axes), ["#3 anchor drag", "component:ha:light.salon", "membrane::c:bms"])


if __name__ == "__main__":
    unittest.main()
