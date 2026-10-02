"""The busy contract (red-first): parallel messages on one session QUEUE
with a visible busy event, heartbeat while working, explicit busy stub
after the wait, and a crash never leaves the session stuck."""
import threading
import time
import types

import pytest

import membrane.server as srv


class _Hub:
    def __init__(self):
        self.events = []

    def publish(self, ev):
        self.events.append(dict(ev))


def _store(messages_ok=True):
    def append(sid, info, parts):
        if not messages_ok:
            raise RuntimeError("store exploded")
    return types.SimpleNamespace(append=append, list=lambda sid: [])


def make_runner(ladder=None, store_ok=True):
    hub = _Hub()
    sessions = types.SimpleNamespace(get=lambda sid: None,
                                     touch=lambda sid: None)
    msgs = _store(store_ok)
    r = srv.TurnRunner(hub, sessions, msgs, ladder, None, "", "m", tools=[])
    r.heartbeat_s = 999.0
    return r, hub


def _turn(content):
    _n = [0]
    def ladder(body):
        _n[0] += 1
        return {"choices": [{"message": {"content": "%s %d" % (content, _n[0])}}]}
    return ladder


def _statuses(hub):
    return [e for e in hub.events if e.get("type") == "session.status"]


def test_second_message_queues_not_races():
    r, hub = make_runner()
    hold = threading.Event()
    calls = []
    def ladder(body):
        calls.append(1)
        if len(calls) == 1:
            hold.wait(timeout=5)
        return {"choices": [{"message": {"content": "done %d" % len(calls)}}]}
    r.call_ladder = ladder
    out1, out2 = {}, {}
    t1 = threading.Thread(target=lambda: out1.update(r.run("s1", "hi")))
    t1.start()
    time.sleep(0.2)
    t2 = threading.Thread(target=lambda: out2.update(r.run("s1", "again")))
    t2.start()
    time.sleep(0.25)
    queued = [e for e in _statuses(hub) if (e["status"] or {}).get("queued")]
    assert queued, "second POST must publish a visible busy/queued event"
    assert r.status("s1").get("type") == "busy"
    hold.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    texts = [p["text"] for p in (out2.get("parts") or [])
             if p.get("type") == "text"]
    assert texts == ["done 2"], texts
    assert [p["text"] for p in (out1.get("parts") or [])
            if p.get("type") == "text"] == ["done 1"]
    assert [e for e in _statuses(hub)
            if (e["status"] or {}).get("type") == "idle"]


def test_queued_post_returns_busy_after_wait():
    r, hub = make_runner(_turn("slow"))
    hold = threading.Event()
    r.call_ladder = lambda body: (hold.wait(timeout=5) or
                                  {"choices": [{"message": {"content": "slow"}}]})
    r.busy_wait_s = 0.1
    t1 = threading.Thread(target=lambda: r.run("s1", "hi"))
    t1.start()
    time.sleep(0.2)
    out2 = r.run("s1", "again")
    assert out2.get("status") == "busy", out2
    assert out2.get("sessionID") == "s1"
    assert [e for e in _statuses(hub) if (e["status"] or {}).get("queued")]
    hold.set()
    t1.join(timeout=5)


def test_busy_heartbeat_publishes_elapsed():
    r, hub = make_runner()
    r.heartbeat_s = 0.05
    def ladder(body):
        time.sleep(0.25)
        return {"choices": [{"message": {"content": "hb"}}]}
    r.call_ladder = ladder
    out = r.run("s1", "hi")
    assert out.get("parts")
    hbs = [e for e in _statuses(hub)
           if isinstance((e["status"] or {}).get("elapsed_ms"), int)]
    assert hbs, "a running turn must publish busy heartbeats with elapsed_ms"
    assert hbs[-1]["status"]["elapsed_ms"] >= 40


def test_crash_leaves_no_stuck_busy():
    r, hub = make_runner(store_ok=False)
    with pytest.raises(RuntimeError):
        r.run("s1", "hi")
    assert r.status("s1").get("type") != "busy"
    r.messages = _store(True)
    r.call_ladder = _turn("recovered")
    out = r.run("s1", "try again")
    assert out.get("status") is None and out.get("parts"), out
