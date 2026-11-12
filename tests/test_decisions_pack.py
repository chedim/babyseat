"""The answer to a Decision card is the decisions pack's (packs/decisions.c1f; the operator, 2026-11-12: everything moves to
cliffhanger, no Python behind it). Two ways in: `Decision.answer` (the person's own answer, through the seat's door) and
`Decision.relay = {value, quote}` (the model passing on what the person said, proved against the person's messages).
The receipt is `answer_result`; the ledger line, the card's fields and the event to whoever waits all come from the pack."""
import json
import os
import sqlite3
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import adapters as AD  # noqa: E402
from membrane import decision_map as DM  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from membrane.ledger import UserLedger  # noqa: E402


def _store(path, messages):
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    for t in ("c_events", "c_entity_deltas", "c_sentences"):
        c.execute(f"CREATE TABLE {t} (k TEXT PRIMARY KEY, body TEXT NOT NULL)")
    for i, (conv, text, ts, author) in enumerate(messages, 1):
        c.execute("INSERT INTO c_events VALUES (?, ?)", (f"e{i}", json.dumps({"o": i, "d": {
            "seq": i, "conv": conv, "role": "user", "stream": "discourse", "text": text, "author": author, "ts": ts,
            "status": "applied"}})))
    c.commit()
    c.close()


@pytest.fixture
def seat(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    store = tmp_path / "store"
    store.mkdir()
    now = time.time()
    _store(store / "field.sqlite3", [
        ("sid:ses_1", "[user#owner: owner]\nhow many commands? 12 is fine for now, a slider later", now + 5, "person"),
        ("sid:ses_1", "[user#owner: owner]\nlet me think about that", now + 6, "person"),
        ("sid:ses_1", "12 is fine from a model", now + 7, "model"),
        ("sid:ses_0", "[user#owner: owner]\n99 was said before the card existed", now - 1000, "person")])
    monkeypatch.setattr(FL, "_store_file", lambda srv: str(store / "field.sqlite3"))
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    ledger = UserLedger(str(store / "f.ledger.jsonl"))       # in the seat's folder: the drivers refuse /tmp elsewhere
    told = []
    est._events_pool = types.SimpleNamespace(put=lambda ev: told.append(ev))
    est._decision_chat = lambda sid, text: None            # a card with its own chat hears it there
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None, user_ledger=ledger,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    live = FL.start(srv)
    AD.start(srv)
    FL.boot_started(est, ledger=ledger.path)
    FL.stop  # (the fixture's finalizer stops it)
    srv.told = told
    yield srv
    AD.stop()
    FL.stop(srv)


def _card(srv, var=None, **props):
    est = srv.entity_store
    eid = est.add(cls="Decision", name="Max in flight", summary="cap")["eid"]
    est.edit(eid, properties={"status": "open", "var": var or {"type": "int"}, "pick": 12, **props})
    est.docs[eid].properties["born"] = time.time() - 60
    est.upsert(est.docs[eid], embedder=None)
    time.sleep(0.2)
    return eid


def _until(fn, timeout=8.0):
    t = time.time() + timeout
    while time.time() < t:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def _props(srv, card):
    return srv.entity_store.docs[card].properties


def _relay(srv, card, value, quote):
    d = srv.entity_store.docs[card]
    d.properties = {**d.properties, "relay": {"value": value, "quote": quote}}
    srv.entity_store.upsert(d, embedder=None)
    return _until(lambda: _props(srv, card).get("answer_result") or _props(srv, card).get("relay_result"))


# ---- the person's own answer -------------------------------------------------------------------------------------

def test_the_persons_answer_decides_the_card_and_writes_the_ledger(seat):
    card = _card(seat)
    res = FL.answer_card(seat, card, {"value": "12", "conv": "ses_1", "message": "msg_1"})
    assert res["ok"] and res["value"] == 12, res
    p = _props(seat, card)
    assert (p["value"], p["status"], p["set_by"]) == (12, "decided", "operator")
    assert p["chat"] == "ses_1" and p["message"] == "msg_1" and p["value_ts"] and p["set_at"]
    line = _until(lambda: UserLedger(seat.user_ledger.path).entries() and UserLedger(seat.user_ledger.path).entries()[-1])
    assert line["text"] == "decided: Max in flight = 12" and line["conv"] == "ses_1" and line["ts"].endswith("Z")


def test_the_event_reaches_whoever_waits(seat):
    card = _card(seat)
    FL.answer_card(seat, card, {"value": 12})
    ev = _until(lambda: seat.told and seat.told[-1])
    assert ev["kind"] == "decision" and ev["entity"] == card and ev["value"] == "12" and ev["title"] == "Max in flight"


def test_a_chat_answer_from_the_chat_itself_sends_nothing_back(seat):
    card = _card(seat)
    FL.answer_card(seat, card, {"value": 12, "conv": "ses_1", "tell": False})
    time.sleep(0.5)
    assert seat.told == []


@pytest.mark.parametrize("var,value,ok,want", [
    ({"type": "int"}, "12", True, 12), ({"type": "int"}, 12, True, 12), ({"type": "int"}, "abc", False, None),
    ({"type": "int"}, "12.5", False, None), ({"type": "int"}, True, False, None),
    ({"type": "float"}, "2.5", True, 2.5), ({"type": "float"}, "x", False, None),
    ({"type": "bool"}, "Yes", True, True), ({"type": "bool"}, "no", True, False), ({"type": "bool"}, "maybe", False, None),
    ({"type": "string"}, "  hello ", True, "hello"), ({"type": "string"}, "   ", False, None),
    ({"type": "option", "options": ["pytest", "unittest"]}, "pytest", True, "pytest"),
    ({"type": "option", "options": ["pytest", "unittest"]}, "nose", False, None),
])
def test_the_value_must_fit_the_cards_variable(seat, var, value, ok, want):
    card = _card(seat, var=var)
    res = FL.answer_card(seat, card, {"value": value})
    assert res["ok"] is ok, res
    if ok:
        assert _props(seat, card)["value"] == want or (var["type"] == "bool" and _props(seat, card)["value"] is True)
    else:
        assert _props(seat, card)["status"] == "open" and _props(seat, card).get("value") is None
        assert res["error"]


def test_a_bool_no_is_a_valid_answer_not_a_refusal(seat):
    card = _card(seat, var={"type": "bool"})
    res = FL.answer_card(seat, card, {"value": "no"})
    assert res["ok"] and _props(seat, card)["value"] is False
    assert _until(lambda: UserLedger(seat.user_ledger.path).entries())[-1]["text"] == "decided: Max in flight = no"


def test_a_decided_card_is_not_answered_again(seat):
    card = _card(seat)
    assert FL.answer_card(seat, card, {"value": 12})["ok"]
    res = FL.answer_card(seat, card, {"value": 13})
    assert res["ok"] is False and res["conflict"] is True
    assert _props(seat, card)["value"] == 12


def test_the_person_can_re_answer_a_decided_card_and_the_old_value_is_kept(seat):
    card = _card(seat)
    assert FL.answer_card(seat, card, {"value": 12})["ok"]
    res = FL.answer_card(seat, card, {"value": 13, "redo": True})
    assert res["ok"] is True and res["value"] == 13
    assert _props(seat, card)["value"] == 13 and _props(seat, card)["previous_value"] == 12
    assert FL.answer_card(seat, card, {"value": 14})["conflict"] is True          # (a plain answer still cannot change it)


def test_work_false_closes_the_card_and_speculative_drafts_follow_the_pick(seat):
    card = _card(seat, speculative={"draft": "x"}, work_needed=True)
    res = FL.answer_card(seat, card, {"value": 13, "work": False})
    assert res["ok"]
    p = _props(seat, card)
    assert p["status"] == "closed" and p["work_needed"] is False
    assert p.get("speculative") in (None, {}) and p["speculative_discarded"] == {"draft": "x"}


# ---- the model's relay -----------------------------------------------------------------------------------------------

def test_the_persons_words_answer_the_card(seat):
    card = _card(seat)
    res = _relay(seat, card, 12, "12 is fine for now")
    assert res["ok"], res
    p = _until(lambda: _props(seat, card) if _props(seat, card).get("value") == 12 else None)
    assert (p["value"], p["status"], p["relayed_by"]) == (12, "decided", "llm")
    assert p["answer_quote"] == "12 is fine for now" and p["answer_chat"] == "sid:ses_1"
    text = _until(lambda: UserLedger(seat.user_ledger.path).entries())[-1]["text"]
    assert 'relayed from the chat: "12 is fine for now"' in text and "Max in flight = 12" in text


def test_case_and_punctuation_do_not_matter_but_the_words_do(seat):
    card = _card(seat)
    res = _relay(seat, card, 12, "12 IS fine,   for now!!")
    assert res["ok"], res


def test_invented_words_answer_nothing(seat):
    card = _card(seat)
    res = _relay(seat, card, 12, "12 is fine ok")
    assert res["ok"] is False and "word for word" in res["error"]
    assert _props(seat, card)["status"] == "open" and _props(seat, card).get("value") is None


def test_a_models_words_and_words_before_the_card_are_not_proof(seat):
    card = _card(seat)
    assert _relay(seat, card, 12, "12 is fine from a model")["ok"] is False
    card2 = _card(seat)
    assert _relay(seat, card2, 99, "99 was said before the card existed")["ok"] is False


def test_no_quote_answers_nothing(seat):
    card = _card(seat)
    for q in ("", "   ", "!!"):
        assert _relay(seat, card, 12, q)["ok"] is False, q
        time.sleep(0.05)
    assert _props(seat, card)["status"] == "open"


def test_part_of_a_word_is_not_the_quote(seat):
    card = _card(seat)
    assert _relay(seat, card, 12, "slid")["ok"] is False


def test_a_relayed_value_that_does_not_fit_is_refused_after_the_proof(seat):
    card = _card(seat, var={"type": "int"})
    d = seat.entity_store.docs[card]
    d.properties = {**d.properties, "relay": {"value": "twelve", "quote": "12 is fine for now"}}
    seat.entity_store.upsert(d, embedder=None)
    assert _until(lambda: _props(seat, card).get("answer_result"))["ok"] is False   # the value does not fit an int
    assert _props(seat, card)["relay_result"]["ok"] is True                         # but the words were theirs
    assert _props(seat, card)["status"] == "open"


# ---- the door ---------------------------------------------------------------------------------------------------------

def test_a_model_cannot_write_the_answer_or_decide_the_value(seat):
    card = _card(seat)
    for key, val in (("value", 12), ("answer", {"value": 12}), ("set_by", "operator"), ("answer_result", {"ok": True})):
        res = seat.entity_store.edit(card, properties={key: val})
        assert key in (res.get("properties_refused") or []) or not res.get("ok"), (key, res)
    assert _props(seat, card).get("value") is None


def test_the_relay_is_the_chats_hand_not_a_loops(seat, monkeypatch):
    from membrane import diag_requests as DR
    card = _card(seat)
    monkeypatch.setattr(DR, "current", lambda: {"agent": "watch:membrane::goal:x"})
    assert DM.refusal("Decision", "relay", {"value": 1, "quote": "x"}, {}) and "listener lane" in DM.refusal("Decision", "relay", {}, {})
    monkeypatch.setattr(DR, "current", lambda: {"agent": "plan"})
    assert "plan mode" in DM.refusal("Decision", "relay", {}, {})
    monkeypatch.setattr(DR, "current", lambda: {"agent": "build"})
    assert DM.refusal("Decision", "relay", {}, {}) is None


def test_the_ledger_path_is_known_before_the_start_is_announced(monkeypatch, tmp_path):
    """Live 2026-11-12: the setter on Boot.started ran before the engine heard Boot.ledger and removed the path."""
    from membrane import writes as W
    order = []
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    W.on_write(lambda w: order.append(w.get("prop")) if str(w.get("eid")) == "Boot:seat" else None)
    FL.boot_started(est, ledger=str(tmp_path / "l.jsonl"))
    assert "ledger" in order and "started" in order and order.index("ledger") < order.index("started"), order


def test_the_ledger_path_the_pack_gets_is_absolute(tmp_path, monkeypatch):
    """Live 2026-11-12: the seat's path was relative to its start folder and the file driver resolved it against the data
    folder (.membrane/.membrane/...): the decisions went to a file nobody reads."""
    monkeypatch.chdir(tmp_path)
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    FL.boot_started(est, ledger=".membrane/field.json.ledger.jsonl")
    got = est.docs["Boot:seat"].properties["ledger"]
    assert os.path.isabs(got) and got == str((tmp_path / ".membrane" / "field.json.ledger.jsonl").resolve())
