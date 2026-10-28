"""A goal fed an old answer stays due (#650 review): the BLOCKED pass that is
answered from a card it already had does not park the goal, so the next beat
wakes it; BLOCKED again after that plants a fresh card."""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import (CLASS_DECISION, CLASS_GOAL, EntityStore,  # noqa: E402
                               MEMBRANE_NS, QuantumEntity)

ASK = "BLOCKED on the operator: say whether Memo takes #541 or I skip to #569"


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(dict(ev))

    def take(self):
        out, self.items = self.items, []
        return out


class _Srv:
    def __init__(self, est):
        self.entity_store = est
        self.events_pool = est._events_pool = _Pool()
        self.loops = WA.LoopRunner(max_loops=3)
        self.efield = None
        self._operator_last_ts = 0
        self.create_loop_session = lambda agent, title=None: "ses_x"
        self.run_loop_turn = lambda sid, text, agent: {"text": ASK}


def _seat():
    est = EntityStore(bucket=f"test-fed-650-{time.time_ns()}", shared=False)
    eid = f"{MEMBRANE_NS}goal:g"
    est.upsert(QuantumEntity(eid=eid, cls=CLASS_GOAL, properties={
        "name": "ship 0.7.4", "status": "open", "intent_planted_ts": 0}), embedder=None)
    return est, _Srv(est), eid


def _pass(srv, eid):
    WA._LoopDrainer(srv).handle({"kind": "intent-planted", "entity": eid, "text": "g"})


def _cards(est):
    return [d for d in est.docs.values() if d.cls == CLASS_DECISION]


def test_a_fed_goal_is_not_parked_and_blocked_again_gets_a_fresh_card():
    est, srv, eid = _seat()
    _pass(srv, eid)
    [card] = _cards(est)
    card.properties = {**card.properties, "status": "decided", "value": "Memo"}
    est.upsert(card)
    srv.events_pool.take()
    _pass(srv, eid)                                   # the same question again
    evs = [e for e in srv.events_pool.take() if e["kind"] == "decision"]
    assert len(evs) == 1 and evs[0]["value"] == "Memo" and evs[0]["blocks"] == [eid]
    assert len(_cards(est)) == 1                      # no new card yet
    props = est.docs[eid].properties
    assert not WA._parked(srv, props)                 # due on the next beat
    _pass(srv, eid)                                   # still blocked after the feed
    opens = [d for d in _cards(est) if d.properties.get("status") == "open"]
    assert len(opens) == 1 and opens[0].eid != card.eid


def test_a_reused_card_keeps_the_other_goal_it_blocks():
    est, srv, eid = _seat()
    other = f"{MEMBRANE_NS}goal:other"
    est.upsert(QuantumEntity(eid=other, cls=CLASS_GOAL, properties={
        "name": "other", "status": "open"}), embedder=None)
    first = WA.ask_operator(est, other, {}, ASK)
    second = WA.ask_operator(est, eid, {}, ASK)
    assert first == second
    assert set(est.docs[first].properties["blocks"]) == {other, eid}
