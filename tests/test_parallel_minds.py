"""PARALLEL MINDS, NOT DUPLICATE ONES (2026-10-11, the operator's go on: keep up
to 3 loops; one loop per entity; a claim note when another mind touches an
entity a loop holds; each watch pass sees the last passes on its entity; the
round's leading read-only calls run together)."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from membrane.fieldpulse import FieldPulse  # noqa: E402


class TestOneLoopPerEntity(unittest.TestCase):
    def test_a_second_loop_on_a_held_entity_waits(self):
        pool = WA.LoopRunner(3)
        a = WA.LoopDesc("watch", eid="membrane::Component:bat")
        self.assertTrue(pool.admit(a))
        self.assertFalse(pool.admit(WA.LoopDesc("watch", eid="membrane::Component:bat")))
        self.assertTrue(pool.admit(WA.LoopDesc("watch", eid="membrane::Component:cam")))
        self.assertTrue(pool.holds("membrane::Component:bat"))
        pool.release(a)
        self.assertFalse(pool.holds("membrane::Component:bat"))


class TestTheLastPasses(unittest.TestCase):
    def test_the_ring_keeps_three(self):
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        ent = QuantumEntity(eid=MEMBRANE_NS + "Component:bat", cls="Component", properties={"name": "bat"})
        est.docs[ent.eid] = ent
        for i in range(5):
            est.stamp_watch_decision(ent.eid, {"verdict": "step", "decision": f"d{i}", "ts": i})
        self.assertEqual([d["decision"] for d in ent.properties["watch_decisions"]], ["d2", "d3", "d4"])

    def test_the_prompt_carries_them(self):
        t = WA.loop_task_text({"kind": "getter-applied", "entity": "membrane::Component:bat",
                               "previous": [{"verdict": "done", "decision": "linked the battery getter", "ts": 0}]})
        self.assertIn("Your last passes on it", t)
        self.assertIn("DONE: linked the battery getter", t)
        self.assertIn("do not redo what landed", t)


class TestTheClaim(unittest.TestCase):
    def test_another_mind_hears_who_holds_it(self):
        class _S:
            pass
        srv = _S()
        fp = FieldPulse(srv)
        srv._field_pulse = fp
        fp.focus("watch-bat", "membrane::Component:phone:battery", "start")
        out = MS._claim_note(srv, "operator", "entity_read", {"spec": "Component:phone:battery"}, "40%")
        self.assertTrue(out.startswith("40%\n[note: loop-1 is working on"))
        self.assertEqual(MS._claim_note(srv, "watch-bat", "entity_read", {"spec": "Component:phone:battery"}, "40%"),
                         "40%")                                            # its own loop: no note
        self.assertEqual(MS._claim_note(srv, "operator", "entity_read", {"spec": "Component:cam"}, "x"), "x")
        fp.focus("watch-bat", "membrane::Component:phone:battery", "end")
        self.assertEqual(MS._claim_note(srv, "operator", "entity_read", {"spec": "Component:phone:battery"}, "40%"),
                         "40%")


class TestParallelReads(unittest.TestCase):
    def _runner(self, log):
        def ex(name, args, cid):
            log.append(("start", name, time.time()))
            time.sleep(0.3)
            return name + ":" + json.dumps(args, sort_keys=True)
        return MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), lambda b: {}, ex,
                             system="", model="m", tools=None)

    def _tc(self, i, name, **args):
        return {"id": f"c{i}", "function": {"name": name, "arguments": json.dumps(args)}}

    def test_leading_reads_run_together_a_write_stops_the_prefetch(self):
        log = []
        r = self._runner(log)
        tcs = [self._tc(1, "entity_read", spec="a"), self._tc(2, "entity_read", spec="b"),
               self._tc(3, "state"), self._tc(4, "entity_edit", spec="a", summary="x"),
               self._tc(5, "entity_read", spec="c")]
        t0 = time.time()
        pre = r._prefetch_reads(tcs, "operator")
        self.assertEqual(sorted(pre), ["c1", "c2", "c3"])                  # not the write, nor the read after it
        outs = [f.result() for _, f in pre.values()]
        self.assertLess(time.time() - t0, 0.75)                            # 3 x 0.3s, together
        self.assertTrue(all(o.split(":")[0] in ("entity_read", "state") for o in outs))

    def test_one_read_is_not_worth_a_pool(self):
        r = self._runner([])
        self.assertEqual(r._prefetch_reads([self._tc(1, "entity_read", spec="a"),
                                            self._tc(2, "entity_edit", spec="a")], "operator"), {})



class TestThinkingOrb(unittest.TestCase):
    def test_a_round_is_a_think_start_and_end_with_no_text(self):
        class _S:
            pass
        fp = FieldPulse(_S())
        fp.focus("watch-bat", "membrane::Component:bat", "start")
        fp.thinking("operator", "start")
        fp.thinking("watch-bat", "start")
        fp.thinking("operator", "end")
        th = [f for f in fp.frames() if f["type"] == "mind.think"]
        self.assertEqual(th, [{"type": "mind.think", "agent": "chat", "phase": "start"},
                              {"type": "mind.think", "agent": "loop-1", "phase": "start"},
                              {"type": "mind.think", "agent": "chat", "phase": "end"}])


if __name__ == "__main__":
    unittest.main()
