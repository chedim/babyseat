"""CONSEQUENCES (2026-10-11, Argus: "I see tool outputs, not outcomes"): a
decision that acted is looked at again 1 h / 24 h / 72 h on -- the entity
then vs now, what OTHER sessions did to it, whether it was decided again
-- and the line comes back on the entity, in the loop's next task and in
the chat diff. Facts, no model."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import consequences as CQ  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

PUMP = MEMBRANE_NS + "Component:ha:switch.bilge_pump"
T0 = 1_800_000_000.0


class _B:
    def __init__(self, events):
        self.events = events

    def get_multi(self, coll, keys):
        return {k: self.events[k] for k in keys if k in self.events}


class _Field:
    def __init__(self):
        self.deltas, self.events, self.written = {}, {}, []
        self.b = _B(self.events)

    def add(self, eid, key, ts, stream, session="", role=""):
        self.events[key] = {"stream": stream, "session": session, "role": role}
        self.deltas.setdefault(eid, []).append({"eid": eid, "event": key, "ts": ts, "stream": stream})

    def deltas_of(self, eid):
        return list(self.deltas.get(eid) or [])

    def write_events(self, evs, session=""):
        self.written += [(e, session) for e in evs]
        return []


class _Srv:
    def __init__(self):
        self.entity_store = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        self.entity_store._srv = self
        self.efield = _Field()
        p = QuantumEntity(eid=PUMP, cls="Component", properties={"name": "bilge pump", "state": "on"})
        self.entity_store.docs[p.eid] = p


def _pending(srv):
    d = srv.entity_store.docs.get(MEMBRANE_NS + "consequences")
    return ((d.properties or {}).get("pending") or []) if d is not None else []


class TestRecord(unittest.TestCase):
    def test_writes_coalesce_per_session_window(self):
        srv = _Srv()
        CQ.record(srv, PUMP, "exec(call)", who="chat", session="s1", kind="exec", now=T0)
        CQ.record(srv, PUMP, "entity(edit)", who="chat", session="s1", kind="entity", now=T0 + 60)
        CQ.record(srv, PUMP, "exec(call)", who="chat", session="s2", kind="exec", now=T0 + 90)
        self.assertEqual(len(_pending(srv)), 2)
        self.assertEqual(_pending(srv)[0]["snap"], {"state": "on"})

    def test_only_writing_tools(self):
        self.assertTrue(CQ.is_write("membrane_exec", {"action": "call"}))
        self.assertTrue(CQ.is_write("entity", {"action": "edit"}))
        self.assertFalse(CQ.is_write("entity", {"action": "read"}))
        self.assertFalse(CQ.is_write("find", {"spec": "x"}))

    def test_a_loop_verdict_records_only_when_it_acted(self):
        for verdict_text, n in (("DONE switched it off", 1), ("ignored: nothing new", 0)):
            srv = _Srv()
            srv.loops = WA.LoopRunner(max_loops=3)
            srv.events_pool = type("P", (), {"put": lambda s, e: None, "take": lambda s, *a, **k: None,
                                             "depth": lambda s: 0})()
            srv._operator_last_ts = 0.0
            srv.create_loop_session = lambda agent, title=None: "ses_loop"
            srv.run_loop_turn = lambda sid, text, agent, _t=verdict_text: {"text": _t}
            WA._LoopDrainer(srv).handle({"kind": "llm-call", "entity": PUMP, "event_id": "e1", "prompt": "check"})
            self.assertEqual(len(_pending(srv)), n, verdict_text)


class TestSettle(unittest.TestCase):
    def test_horizons_and_the_line(self):
        srv = _Srv()
        CQ.record(srv, PUMP, "turned the pump off", who="loop-1", session="s1", kind="done", now=T0)
        srv.entity_store.docs[PUMP].properties["state"] = "off"
        f = srv.efield
        f.add("ha:switch.bilge_pump", "own", T0 + 10, "observation", session="s1")       # its own: not counted
        f.add("ha:switch.bilge_pump", "ha1", T0 + 600, "ha", session="ha")
        f.add("ha:switch.bilge_pump", "op1", T0 + 900, "discourse", session="chat", role="user")
        f.add("ha:switch.bilge_pump", "late", T0 + 7200, "ha", session="ha")             # after 1 h
        self.assertEqual(CQ.settle(srv, now=T0 + 1800), 0)
        self.assertEqual(CQ.settle(srv, now=T0 + 3700), 1)
        line = srv.entity_store.docs[PUMP].properties["consequences"][-1]["line"]
        self.assertIn("state on→off", line)
        self.assertIn("1 ha", line)
        self.assertIn("operator spoke of it 1×", line)
        self.assertIn("not decided again", line)
        self.assertIn("→ 1h", line)
        self.assertEqual(f.written[-1][0].tool, "consequence")
        self.assertEqual(CQ.settle(srv, now=T0 + 3800), 0)          # the 1 h one is done
        self.assertEqual(CQ.settle(srv, now=T0 + 86400 + 1), 1)
        self.assertEqual(CQ.settle(srv, now=T0 + 3 * 86400 + 1), 1)
        self.assertEqual(_pending(srv), [])                          # retired after 72 h

    def test_missed_horizons_settle_once_at_the_latest(self):
        srv = _Srv()
        CQ.record(srv, PUMP, "x", session="s1", kind="done", now=T0)
        self.assertEqual(CQ.settle(srv, now=T0 + 2 * 86400), 1)
        self.assertIn("→ 1d", srv.entity_store.docs[PUMP].properties["consequences"][-1]["line"])
        self.assertEqual(CQ.settle(srv, now=T0 + 2 * 86400 + 60), 0)

    def test_redecided_counts(self):
        srv = _Srv()
        CQ.record(srv, PUMP, "x", session="s1", kind="done", now=T0)
        CQ.record(srv, PUMP, "y", session="s2", kind="done", now=T0 + 1200)
        CQ.settle(srv, now=T0 + 3700)
        self.assertIn("decided again 1×", srv.entity_store.docs[PUMP].properties["consequences"][0]["line"])


class TestSurfaces(unittest.TestCase):
    def test_loop_text_carries_them(self):
        t = WA.loop_task_text({"kind": "watch-fired", "entity": PUMP,
                               "consequences": ["10-11 14:00 loop-1 DONE \"x\" → 1h: its state held"]})
        self.assertIn("What followed your earlier decisions here", t)

    def test_chat_diff_is_watermarked(self):
        srv = _Srv()
        mark = CQ.counter()
        CQ.record(srv, PUMP, "x", session="s1", kind="done", now=T0)
        CQ.settle(srv, now=T0 + 3700)
        lines = CQ.diff_lines(mark)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("+ settled:"))
        self.assertEqual(CQ.diff_lines(CQ.counter()), [])
        self.assertIn("consequences", EB.watermark(type("F", (), {"_ostats": {}})()))


if __name__ == "__main__":
    unittest.main()
