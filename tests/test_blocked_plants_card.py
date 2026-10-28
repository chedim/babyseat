"""A GOAL BLOCKED ON THE OPERATOR ASKS (#643, 2026-10-28): a pass that ends
with plain BLOCKED plants a Decision card (the loop's line, a free answer,
planted by the watch, blocking the goal) and parks the goal on it, so the
answer wakes the goal. One open card per goal; a timer or an entity
condition plants none."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import (CLASS_DECISION, CLASS_GOAL, EntityStore,  # noqa: E402
                               MEMBRANE_NS, QuantumEntity)
from membrane.ledger import UserLedger  # noqa: E402

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
    def __init__(self, est, reply):
        self.entity_store = est
        self.events_pool = _Pool()
        self.loops = WA.LoopRunner(max_loops=3)
        self.efield = None
        self._operator_last_ts = 0
        self.create_loop_session = lambda agent, title=None: "ses_x"
        self.run_loop_turn = lambda sid, text, agent: {"text": reply}


def _seat(reply=ASK):
    est = EntityStore(bucket=f"test-blocked-card-{time.time_ns()}", shared=False)
    eid = f"{MEMBRANE_NS}goal:g"
    est.upsert(QuantumEntity(eid=eid, cls=CLASS_GOAL, properties={
        "name": "ship 0.7.4", "status": "open", "intent_planted_ts": 0}), embedder=None)
    return est, _Srv(est, reply), eid


def _pass(srv, eid):
    WA._LoopDrainer(srv).handle({"kind": "intent-planted", "entity": eid, "text": "g"})


def _cards(est):
    return [d for d in est.docs.values() if d.cls == CLASS_DECISION]


def test_plain_blocked_plants_one_card_and_parks_on_it():
    est, srv, eid = _seat()
    _pass(srv, eid)
    [card] = _cards(est)
    p = card.properties
    assert p["status"] == "open" and p["planted_by"] == "watch"
    assert p["var"] == {"type": "string"} and p["blocks"] == [eid]
    assert "541" in p["name"] and not p["name"].startswith("on the operator")
    assert est.docs[eid].properties["blocked_entity"] == card.eid


def test_still_blocked_reuses_the_open_card():
    est, srv, eid = _seat()
    _pass(srv, eid)
    _pass(srv, eid)
    assert len(_cards(est)) == 1


def test_a_timer_or_an_entity_plants_no_card():
    for reply in ("BLOCKED retry after 2h", "BLOCKED on w:agatha arriving"):
        est, srv, eid = _seat(reply)
        _pass(srv, eid)
        assert _cards(est) == []


def test_answering_the_card_wakes_the_goal():
    est, srv, eid = _seat()
    _pass(srv, eid)
    [card] = _cards(est)
    now = time.time() + 1
    assert WA._parked(srv, est.docs[eid].properties, now=now)
    led = UserLedger(str(Path(tempfile.mkdtemp()) / "f.ledger.jsonl"))
    assert DM.answer(est, led, card.eid, "skip to #569")["ok"]
    assert not WA._parked(srv, est.docs[eid].properties, now=now)
