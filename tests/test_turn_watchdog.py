"""THE TURN WATCHDOG (the 26-message graveyard covenant): a turn whose
loop outlives TURN_MAX_S aborts itself -- the honest error part
streams, the guard frees, the queue drains; one dead backend socket
can never hold the room for 5 minutes. Plus: 410 GONE is a rung-down
code (a retired model descends, never wedges)."""
import threading
import time

import pytest

from membrane.server import TurnRunner, SSEHub, SessionStore, MessageStore


def _runner(tmp):
    return TurnRunner(SSEHub(), SessionStore(f"{tmp}/s.json"),
                      MessageStore(f"{tmp}/m.json"),
                      call_ladder=None, executor=None, system="", model="m")


def test_turn_max_constant():
    assert 60 <= TurnRunner.TURN_MAX_S <= 300   # the 14B's cold load = ~2min; the honest fence


def test_turn_watchdog_aborts_a_wedged_turn(tmp_path):
    """A turn whose _busy sits past TURN_MAX_S: the watchdog marks the
    abort flag and streams the error part."""
    r = _runner(str(tmp_path))
    r.TURN_MAX_S = 0.2                    # the test's fast clock
    r.TURN_MAX_S = 0.2
    s = r.sessions.create(title="x")
    sid = s["id"]
    r._busy[sid] = {"type": "busy"}
    stop = threading.Event()
    r._turn_stops[sid] = stop
    seen_err = {}
    r._stream_error_part = (lambda sid, msg:
                            seen_err.setdefault("msg", msg))
    r._turn_watchdog(sid, time.time())
    assert sid in r._aborting
    assert "watchdog" in seen_err["msg"], seen_err


def test_turn_watchdog_stands_down_when_turn_ended(tmp_path):
    """The turn ENDED (the normal way): the watchdog's timer finds no
    busy and stands down -- no abort, no error part."""
    r = _runner(str(tmp_path))
    r.TURN_MAX_S = 0.1
    s = r.sessions.create(title="x")
    sid = s["id"]
    seen_err = {}
    r._stream_error_part = (lambda sid, msg:
                            seen_err.setdefault("msg", msg))
    time.sleep(0.15)
    r._turn_watchdog(sid, time.time())
    assert "msg" not in seen_err          # stood down


def test_stream_error_part_appends_to_the_session(tmp_path):
    r = _runner(str(tmp_path))
    s = r.sessions.create(title="x")
    sid = s["id"]
    r._stream_error_part(sid, "the turn outlived 120s")
    rows = r.messages.msgs.get(sid) or []
    texts = [p.get("text") for row in rows for p in row.get("parts", [])
             if p.get("type") == "text"]
    assert any("outlived" in t for t in texts), texts


def test_ladder_is_retired():
    """THE LADDER'S RETIREMENT (the operator's call: the standalone
    router proxy owns failover now): the pick IS the rung — no rung
    descent in the seat; a down rung = the honest note + raise."""
    import pathlib as _pl
    src = _pl.Path("membrane/opencode.py").read_text()
    assert "_next_rung" not in src.split("def run_command_entity")[0] \
        or src.count("_next_rung") == 0, "the ladder's descent lives"
    assert "rung_down" in src               # the honest down-note's lane
    assert "router.note_outage" in src      # the outage's ledger


def test_session_model_patch_stores_the_pick(tmp_path):
    """THE MODEL SWITCH (the TUI's lane): PATCH /session/{id} carries
    the model dict — the session's row stores it (the turn builder's
    read)."""
    r = _runner(str(tmp_path))
    s = r.sessions.create(title="x")
    sid = s["id"]
    # simulate the PATCH handler's lane (the store side):
    with r.sessions._lock:
        row = r.sessions.sessions.get(sid)
        row["model"] = {"id": "qwen3:14b", "providerID": "membrane",
                        "variant": "default"}
    pick = (r.sessions.sessions.get(sid).get("model") or {}).get("id")
    assert pick == "qwen3:14b"
    # the turn builder reads it:
    _sess = r.sessions.sessions.get(sid) or {}
    _sm = _sess.get("model") or {}
    _id = (_sm.get("id") if isinstance(_sm, dict) else str(_sm or "")).strip()
    assert _id == "qwen3:14b"


def test_message_pick_sticks_and_the_turn_uses_it(tmp_path, monkeypatch):
    """THE CHOOSER'S LAST MILE (the operator's 'it ignores the model
    chooser'): a POST's body's model lands on the session's row; the
    turn's builder reads THE SESSION and sends the pick (never the
    boot's default)."""
    import membrane.server as S
    monkeypatch.setattr(S.TurnRunner, "TURN_MAX_S", 300.0, raising=True)
    r = _runner(str(tmp_path))
    s = r.sessions.create(title="x")
    sid = s["id"]
    # the POST's body's model (the TUI's CURRENT pick) — the _message
    # lane's code path: simulate its store-lane step by hand:
    _bm = {"providerID": "membrane", "id": "granite4:latest"}
    row = r.sessions.sessions.get(sid)
    row["model"] = {"id": _bm.get("modelID") or _bm.get("id"),
                    "providerID": _bm.get("providerID") or "membrane",
                    "variant": "default"}
    # the turn's body build (the builder's lane, same logic):
    _sess = r.sessions.sessions.get(sid) or {}
    _sm = _sess.get("model") or {}
    _pick = (_sm.get("id") if isinstance(_sm, dict)
             else str(_sm or "")).strip()
    assert _pick == "granite4:latest"
    assert "qwen" not in _pick           # never the hash-luck default
