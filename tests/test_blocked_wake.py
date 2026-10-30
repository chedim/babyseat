"""BLOCKED ON A THING, NOT A PERSON + THE DORMANT DIGEST (2026-10-08, Argus's
own requests after the change report, red first).

  "BLOCKED parks 'until the operator speaks' -- but my w:agatha goal is
   blocked on an external arrival (ISA parent / command prop from another
   lane). If parking suppresses pulse rewakes, the prop arriving never
   re-wakes it and it dead-locks behind operator speech. Let BLOCKED carry
   a re-wake condition."
  "When a boot skips open goals (backoff/parked), a one-line digest of
   what stayed asleep would show a captain the dormant set without waking
   anything."

Pinned: `BLOCKED retry after <N m|h|d>` parks on a timer; `BLOCKED on
<entity>` parks until that entity moves (field or store); plain BLOCKED
still waits for the operator (who also unparks the other two). The
dormant line counts backoff and parked goals and never wakes one.
"""

import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import (CLASS_GOAL, EntityStore, MEMBRANE_NS,  # noqa: E402
                               QuantumEntity)


def _store():
    return EntityStore(bucket=f"test-blocked-wake-{time.time_ns()}", shared=False)


def _goal(est, slug, **props):
    ent = QuantumEntity(eid=f"{MEMBRANE_NS}goal:{slug}", cls=CLASS_GOAL,
                        properties={"name": slug, "status": "open", **props})
    est.upsert(ent, embedder=None)
    return ent.eid


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(dict(ev))

    def take(self):
        out, self.items = self.items, []
        return out

    def depth(self):
        return len(self.items)


class _St:
    def __init__(self, ts):
        self.last_ts = ts


class _Field:
    def __init__(self):
        self.states = {}

    def get(self, key):
        return self.states.get(key)


class _Srv:
    def __init__(self, est):
        self.entity_store = est
        self.events_pool = _Pool()
        self.loops = WA.LoopRunner(max_loops=3)
        self.efield = _Field()
        self._operator_last_ts = 0


class TestParseCondition(unittest.TestCase):
    def test_the_forms(self):
        P = WA.blocked_condition
        now = 1000.0
        self.assertEqual(P("retry after 2h", now), {"blocked_until": now + 7200})
        self.assertEqual(P("on the operator; retry after 30 min", now), {"blocked_until": now + 1800})
        self.assertEqual(P("retry in 1d", now), {"blocked_until": now + 86400})
        self.assertEqual(P("on w:agatha (its ISA parent from the HA lane)", now),
                         {"blocked_entity": "w:agatha"})
        self.assertEqual(P("on membrane::Component:agdroid-call arriving", now),
                         {"blocked_entity": "membrane::Component:agdroid-call"})
        self.assertEqual(P("on the operator's signature", now), {})


class TestWake(unittest.TestCase):
    def setUp(self):
        self.est = _store()
        self.srv = _Srv(self.est)
        self.now = time.time()

    def _due(self):
        return WA.rewake_open_goals(self.srv, now=self.now, min_age_s=1800)

    def test_retry_after_wakes_on_time(self):
        _goal(self.est, "t", intent_planted_ts=0, blocked_ts=self.now - 100,
              blocked_until=self.now + 600)
        self.assertEqual(self._due(), 0)
        self.now += 700
        self.assertEqual(self._due(), 1)

    def test_on_entity_wakes_when_it_moves(self):
        _goal(self.est, "e", intent_planted_ts=0, blocked_ts=self.now - 100,
              blocked_entity="w:agatha")
        self.srv.efield.states["w:agatha"] = _St(self.now - 500)     # before the block
        self.assertEqual(self._due(), 0)
        self.srv.efield.states["w:agatha"] = _St(self.now - 10)      # it arrived
        self.assertEqual(self._due(), 1)

    def test_on_a_store_entity_wakes_when_its_doc_moves(self):
        self.est.upsert(QuantumEntity(eid=MEMBRANE_NS + "Component:x", cls="Component",
                                      properties={"name": "x", "run_ts": self.now - 500}),
                        embedder=None)
        _goal(self.est, "s", intent_planted_ts=0, blocked_ts=self.now - 100,
              blocked_entity="component:x")
        self.assertEqual(self._due(), 0)
        self.est.docs[MEMBRANE_NS + "Component:x"].properties["run_ts"] = self.now - 5
        self.assertEqual(self._due(), 1)

    def test_the_operator_unparks_every_kind(self):
        _goal(self.est, "t", intent_planted_ts=0, blocked_ts=self.now - 100,
              blocked_until=self.now + 9999)
        self.srv._operator_last_ts = self.now - 1
        self.assertEqual(self._due(), 1)


class TestDormantDigest(unittest.TestCase):
    def test_counts_without_waking(self):
        est = _store()
        srv = _Srv(est)
        now = time.time()
        _goal(est, "b1", intent_planted_ts=now - 60, landings=[{"op": "ignored"}] * 2)
        _goal(est, "b2", intent_planted_ts=now - 60, landings=[{"op": "aborted"}] * 4)
        _goal(est, "op", intent_planted_ts=0, blocked_ts=now - 10, blocked_on="signature")
        _goal(est, "tm", intent_planted_ts=0, blocked_ts=now - 10, blocked_until=now + 3600)
        _goal(est, "en", intent_planted_ts=0, blocked_ts=now - 10, blocked_entity="w:agatha")
        _goal(est, "fresh", intent_planted_ts=now - 60)                 # just waiting: not dormant
        line = WA.dormant_line(srv, now=now)
        self.assertIn("5 goal", line)
        self.assertIn("2 in backoff", line)
        self.assertIn("x4", line)
        self.assertIn("x16", line)
        self.assertIn("1 blocked on the operator", line)
        self.assertIn("1 waiting on an entity", line)
        self.assertIn("1 on a timer", line)
        self.assertEqual(srv.events_pool.items, [])

    def test_nothing_dormant_says_nothing(self):
        est = _store()
        self.assertEqual(WA.dormant_line(_Srv(est)), "")


if __name__ == "__main__":
    unittest.main()
