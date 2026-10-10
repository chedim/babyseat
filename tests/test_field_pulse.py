"""THE FIELD'S PULSE (2026-10-08, the viewer v3's live layer).

Pinned: frames coalesce per beat; no message text rides any frame; moved
entities re-place into every CACHED projection only; tool calls name the
entity they touch (the alias set); each running loop holds a stable
loop-N avatar and the /agent titles fill; the field's flush and birth
reach the pulse; the publisher speaks on the serve's hub.
"""

import json
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
from membrane import field3d as F3  # noqa: E402
from membrane import fieldpulse as FP  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402


class _Hub:
    def __init__(self):
        self.frames = []

    def publish(self, f):
        self.frames.append(f)


class _Srv:
    pass


def _proj(n=60, seed=0):
    rng = np.random.default_rng(seed)
    rows = [{"eid": f"f:e{i}", "kind": "f", "surface": f"e{i}", "accr": i, "state": rng.normal(size=8).tolist()}
            for i in range(n)]
    return F3.build(lambda: iter(rows), lambda s: s, ""), rows


class TestFrames(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv()
        self.hub = _Hub()
        self.p = FP.FieldPulse(self.srv, hub=self.hub)

    def test_a_beat_coalesces(self):
        self.p.applied(["f:a", "f:b"], "discourse")
        self.p.applied(["f:a"], "observation")
        self.p.born("f:c")
        self.p.watch("c:x", "near")
        self.assertEqual(self.p.beat(), 3)
        ap = next(f for f in self.hub.frames if f["type"] == "field.applied")
        self.assertEqual(ap["eids"], ["f:a", "f:b"])
        self.assertEqual(ap["streams"], ["discourse", "observation"])
        self.assertEqual(self.p.beat(), 0)                      # drained

    def test_each_event_keeps_its_group_for_edge_pulses(self):
        self.p.applied(["f:a", "f:b", "f:c"], "discourse")
        self.p.applied(["f:a"], "observation")                  # one entity: no edge to pulse
        self.p.beat()
        ap = next(f for f in self.hub.frames if f["type"] == "field.applied")
        self.assertEqual(ap["groups"], [{"s": "discourse", "e": ["f:a", "f:b", "f:c"]}])
        for i in range(FP.MAX_GROUPS + 20):
            self.p.applied([f"f:x{i}", f"f:y{i}"], "observation")
        self.p.beat()
        self.assertEqual(len(self.hub.frames[-1]["groups"]), FP.MAX_GROUPS)

    def test_moves_land_in_cached_projections_only(self):
        proj, rows = _proj()
        self.srv._field3d = {proj.pid: proj}
        self.p.moved("f:e3", rows[3]["state"])
        self.p.moved("f:unknown", rows[4]["state"])
        self.p.beat()
        mv = next(f for f in self.hub.frames if f["type"] == "field.moved")
        self.assertEqual(mv["pid"], proj.pid)
        self.assertEqual([m[0] for m in mv["moves"]], ["f:e3"])
        self.assertEqual(len(mv["moves"][0]), 4)

    def test_tool_touches_name_their_entity(self):
        self.assertEqual(FP.touched_eid("entity_read", {"spec": "c:argus"}), "c:argus")
        self.assertEqual(FP.touched_eid("entity_edit", {"eid": "c:x", "summary": "s"}), "c:x")
        self.assertEqual(FP.touched_eid("query", {"topic": "battery"}), "")
        self.assertEqual(FP.touched_eid("entity_link", {"name": "a\\nb"}), "a\\nb")
        self.p.touch("build", "entity_read", {"spec": "c:argus"})
        self.p.touch("phone", "entity_read", {"spec": "c:bms"})
        self.p.beat()
        touches = [f for f in self.hub.frames if f["type"] == "mind.touch"]
        self.assertEqual([(t["agent"], t["eid"]) for t in touches], [("chat", "c:argus"), ("phone", "c:bms")])

    def test_loops_hold_stable_slots(self):
        self.p.focus("watch: membrane::goal:a", "membrane::goal:a", "start")
        self.p.focus("watch: membrane::goal:b", "membrane::goal:b", "start")
        self.p.touch("watch: membrane::goal:b", "entity_read", {"spec": "c:x"})
        self.p.focus("watch: membrane::goal:a", "membrane::goal:a", "end")
        self.p.focus("watch: membrane::goal:c", "membrane::goal:c", "start")
        self.p.beat()
        ag = [(f["type"], f["agent"]) for f in self.hub.frames if f["type"].startswith("mind.")]
        self.assertEqual(ag, [("mind.focus", "loop-1"), ("mind.focus", "loop-2"),
                              ("mind.touch", "loop-2"), ("mind.focus", "loop-1"),
                              ("mind.focus", "loop-1")])

    def test_no_message_text_rides_any_frame(self):
        self.p.mind("thinker", kind="chat", eid="")
        self.p.touch("build", "learn", {"text": "the captain's private note"})
        self.p.beat()
        blob = json.dumps(self.hub.frames)
        self.assertNotIn("private note", blob)

    def test_the_hub_is_the_runners(self):
        class R:
            hub = _Hub()
        srv = _Srv()
        srv._loop_runner = R()
        p = FP.FieldPulse(srv)
        p.born("f:a")
        self.assertEqual(p.beat(), 1)
        self.assertEqual(R.hub.frames[0]["type"], "field.born")


class TestFieldHooks(unittest.TestCase):
    def test_flush_and_birth_reach_the_pulse(self):
        f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
        moved, born = [], []
        f.pulse_moved = lambda e, v: moved.append(e)
        f.pulse_born = born.append
        for i in range(4):
            f.write_events(ST.split_turn(f"membrane/router.py turn {i}",
                                         f"membrane/router.py answers {i}"), session="s")
        f.process()
        f.flush()
        self.assertTrue(born)
        self.assertTrue(moved)

    def test_the_listener_drops_words(self):
        srv = _Srv()
        srv._field_pulse = p = FP.FieldPulse(srv, hub=_Hub())
        FP.efield_listener(srv)("k", {"mentions": [("w:the", 0, "the"), ("f:a.py", 1, "a.py")],
                                      "stream": "code"})
        self.assertEqual(list(p._applied), ["f:a.py"])


class TestLoopTitles(unittest.TestCase):
    def test_a_running_loop_names_itself(self):
        srv = _Srv()
        srv.loops = WA.LoopRunner(max_loops=3)
        srv.loops.titles = []
        seen = []
        srv.create_loop_session = lambda agent, title=None: "ses"
        srv.run_loop_turn = lambda sid, text, agent: (seen.append(list(srv.loops.titles)) or {"text": "ignored: x"})
        srv._field_pulse = FP.FieldPulse(srv, hub=_Hub())
        WA._LoopDrainer(srv).handle({"kind": "around", "entity": "c:x"})
        self.assertEqual(seen, [["watch: c:x"]])
        self.assertEqual(srv.loops.titles, [])


if __name__ == "__main__":
    unittest.main()
