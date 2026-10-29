"""A decision event goes where the card came from (2026-10-28): a card a watch
loop planted wakes the watcher pool; any other card tells its chat, as one
message that starts a turn. The lane stamps `planted_by` at creation."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from membrane.ledger import UserLedger  # noqa: E402


def _seat():
    d = Path(tempfile.mkdtemp())
    est = EntityStore(path=str(d / "e.json"), shared=False)
    est._events_pool = WA.WatchEvents()
    est.sent = []
    est._decision_chat = lambda sid, text: est.sent.append((sid, text))
    return est, UserLedger(str(d / "f.ledger.jsonl"))


def _card(est, name, **props):
    eid = est.add(cls="Decision", name=name, summary="x")["eid"]
    assert est.edit(eid, properties={"status": "open", "var": {"type": "option",
                                     "options": ["a", "b"]}, **props})["ok"]
    return eid


def test_a_watch_planted_card_wakes_the_pool_not_the_chat():
    est, led = _seat()
    eid = _card(est, "Which one", planted_by="watch", chat="ses_1")
    assert DM.answer(est, led, eid, "a", conv="ses_1")["ok"]
    assert [e["kind"] for e in est._events_pool.take()] == ["decision"]
    assert est.sent == []


def test_any_other_card_tells_its_chat_not_the_pool():
    est, led = _seat()
    eid = _card(est, "Which one", planted_by="chat", chat="ses_1")
    assert DM.answer(est, led, eid, "b", conv="ses_1")["ok"]
    assert est._events_pool.take() == []
    [(sid, text)] = est.sent
    assert sid == "ses_1" and "Which one" in text and "b" in text


def test_a_drop_routes_the_same_way():
    est, _ = _seat()
    chat = _card(est, "Q1", planted_by="chat", chat="ses_1")
    watch = _card(est, "Q2", planted_by="watch")
    assert DM.drop(est, chat)["ok"] and DM.drop(est, watch)["ok"]
    assert [e["entity"] for e in est._events_pool.take()] == [watch]
    assert len(est.sent) == 1 and "dropped" in est.sent[0][1]


def test_the_lane_stamps_the_planter_on_a_new_card():
    args = {"class": "Decision", "name": "Q", "properties": {"status": "open"}}
    out = S.stamp_card_planter("entity_add", args, "watch: goal:abc")
    assert out["properties"]["planted_by"] == "watch"
    out = S.stamp_card_planter("entity_add", args, None)
    assert out["properties"]["planted_by"] == "chat"
    kept = S.stamp_card_planter("entity_add", {"class": "Decision", "name": "Q",
                                               "properties": {"planted_by": "watch"}}, None)
    assert kept["properties"]["planted_by"] == "watch"
    other = {"class": "Goal", "name": "G"}
    assert S.stamp_card_planter("entity_add", other, None) == other


def test_a_chat_card_with_no_chat_falls_back_to_the_pool():
    est, led = _seat()
    eid = _card(est, "Which one", planted_by="chat")
    assert DM.answer(est, led, eid, "a")["ok"]
    assert [e["kind"] for e in est._events_pool.take()] == ["decision"]
    assert est.sent == []


def test_a_chat_planted_card_records_the_chat_that_planted_it():
    args = {"class": "Decision", "name": "Q", "properties": {"status": "open"}}
    out = S.stamp_card_planter("entity_add", args, None, "ses_9")
    assert out["properties"]["planted_by"] == "chat" and out["properties"]["chat"] == "ses_9"
    kept = S.stamp_card_planter("entity_add", {"class": "Decision", "name": "Q",
                                               "properties": {"chat": "ses_1"}}, None, "ses_9")
    assert kept["properties"]["chat"] == "ses_1"
    watch = S.stamp_card_planter("entity_add", args, "watch: goal:abc", "ses_9")
    assert "chat" not in watch["properties"]


def test_answering_a_chat_planted_card_wakes_its_planting_chat():
    est, led = _seat()
    props = S.stamp_card_planter("entity_add", {"class": "Decision", "name": "Q",
            "properties": {"status": "open", "var": {"type": "option", "options": ["a", "b"]}}},
            None, "ses_9")["properties"]
    eid = est.add(cls="Decision", name="Q", summary="x")["eid"]
    assert est.edit(eid, properties=props)["ok"]
    assert DM.answer(est, led, eid, "a")["ok"]      # the board answers with no conv
    assert est._events_pool.take() == []
    assert [s for s, _ in est.sent] == ["ses_9"]
