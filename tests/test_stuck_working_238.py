"""#238: the chat stays on "working" while the seat reports the session idle.

The seat side: every send path that publishes busy (or implies a turn) ends
with an idle publish for that session once nothing runs or waits -- a plain
turn on the main chat (the cosmetic busy used to swallow its idle), a turn
whose body raises, the flood guard's refusal, a queued turn, a message merged
into the running turn, an interjection that arrives after the turn is over,
membrane-only sends, job continuations, Stop, and a person's preempt.

The page side (panel.js): while the page believes its chat is busy it asks
the seat's /session/status and adopts the answer -- reconcileStatus is the
pure decision; the wiring polls only while busy, never overlapping.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as S  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


class _Hub:
    def __init__(self):
        self.events = []
        self._lock = threading.Lock()

    def publish(self, ev):
        with self._lock:
            self.events.append(dict(ev))

    def last_state(self, sid):
        """The session's last status as the stream told it: busy / idle."""
        with self._lock:
            evs = [e for e in self.events if e.get("sessionID") == sid
                   and e.get("type") in ("session.status", "session.idle")]
        if not evs:
            return None
        e = evs[-1]
        return "idle" if e["type"] == "session.idle" else (e.get("status") or {}).get("type")

    def busied(self, sid):
        with self._lock:
            return any(e.get("type") == "session.status" and e.get("sessionID") == sid
                       and (e.get("status") or {}).get("type") == "busy" for e in self.events)


def _reply(text="ok"):
    return {"choices": [{"message": {"content": text}}]}


def _runner(ladder=None, executor=None):
    hub = _Hub()
    r = S.TurnRunner(hub, S.SessionStore(), S.MessageStore(),
                     ladder or (lambda body: _reply()),
                     executor or (lambda n, a, c: "ok"), system="", model="m", tools=[])
    r.heartbeat_s = 999.0
    sid = r.sessions.create(title="t")["id"]
    return r, hub, sid


def _user(sid, text="hi", **kw):
    mid = S._gen_id("msg")
    info = {"id": mid, "sessionID": sid, "role": "user", "agent": "build",
            "time": {"created": int(time.time() * 1000)}, **kw}
    parts = [{"id": S._gen_id("prt"), "sessionID": sid, "messageID": mid,
              "type": "text", "text": text}]
    return info, parts


def _settle(r, hub, sid, timeout=5.0):
    """Wait until nothing runs or waits, then a beat for the drain's tail."""
    end = time.time() + timeout
    while time.time() < end:
        if sid not in r._busy and not r._queued_depth(sid) and \
                not any(t.is_alive() for t in r._drain_threads):
            time.sleep(0.15)
            if sid not in r._busy and not r._queued_depth(sid):
                return
        time.sleep(0.02)


class TestEveryPathEndsIdle(unittest.TestCase):
    def test_a_turn_on_the_main_chat_ends_idle_even_with_the_cosmetic_busy_armed(self):
        # the live seat arms cosmetic_busy (opencode.py); /session/status said
        # idle while the stream never did -- the page sat on "working" forever
        r, hub, sid = _runner()
        r.cosmetic_busy = True
        r.run(sid, "hi", *_user(sid))
        self.assertEqual(r.status(sid)["type"], "idle")
        self.assertEqual(hub.last_state(sid), "idle")

    def test_a_turn_whose_body_raises_still_ends_idle(self):
        r, hub, sid = _runner()
        real = r.messages.append

        def append(s, info, parts):
            if (info or {}).get("role") == "assistant":
                raise RuntimeError("store exploded")
            return real(s, info, parts)
        r.messages.append = append
        with self.assertRaises(RuntimeError):
            r.run(sid, "hi", *_user(sid))
        self.assertTrue(hub.busied(sid))
        self.assertEqual(hub.last_state(sid), "idle")

    def test_the_flood_refusal_ends_idle_once_the_running_turn_is_done(self):
        hold = threading.Event()
        r, hub, sid = _runner(lambda body: hold.wait(5) and _reply())
        r.queue_depth_max = 0
        t = threading.Thread(target=lambda: r.run(sid, "hi", *_user(sid)))
        t.start()
        time.sleep(0.2)
        out = r.run(sid, "again", *_user(sid, "again"))
        self.assertEqual(out.get("status"), "busy")
        self.assertEqual(hub.last_state(sid), "busy")
        hold.set()
        t.join(5)
        _settle(r, hub, sid)
        self.assertEqual(hub.last_state(sid), "idle")

    def test_a_queued_turn_ends_idle(self):
        hold = threading.Event()
        n = []

        def ladder(body):
            n.append(1)
            if len(n) == 1:
                hold.wait(5)
            return _reply("done %d" % len(n))
        r, hub, sid = _runner(ladder)
        r.busy_wait_s = 0.1
        t = threading.Thread(target=lambda: r.run(sid, "hi", *_user(sid)))
        t.start()
        time.sleep(0.2)
        out = r.run(sid, "again", *_user(sid, "again"))
        self.assertEqual(out.get("status"), "queued")
        hold.set()
        t.join(5)
        _settle(r, hub, sid)
        self.assertEqual(len(n), 2)
        self.assertEqual(hub.last_state(sid), "idle")

    def test_a_message_merged_into_the_running_turn_ends_idle(self):
        n = []
        r_box = {}

        def ladder(body):
            n.append(1)
            if len(n) == 1:
                # a word lands mid-turn: the next round takes it in
                info, parts = _user(sid, "a word in between")
                r_box["r"]._enqueue(sid, "a word in between", info, parts, threading.Event())
                return {"choices": [{"message": {"content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "state", "arguments": "{}"}}]}}]}
            return _reply("done")
        r, hub, sid = _runner(ladder)
        r_box["r"] = r
        r.run(sid, "hi", *_user(sid))
        _settle(r, hub, sid)
        self.assertEqual(len(n), 2)                      # merged, not its own turn
        self.assertEqual(r._queued_depth(sid), 0)
        self.assertEqual(hub.last_state(sid), "idle")

    def test_an_interjection_after_the_turn_is_over_leaves_no_busy(self):
        # a word can arrive after the turn it meant to join:
        # its enqueue published busy·queued and nobody ever drained it
        r, hub, sid = _runner()
        r.run(sid, "hi", *_user(sid))
        _settle(r, hub, sid)
        info, parts = _user(sid, "too late")
        took = r.interject(sid, "too late", info, parts)
        self.assertFalse(took)
        self.assertEqual(r._queued_depth(sid), 0)
        self.assertEqual(hub.last_state(sid), "idle")

    def test_an_interjection_while_a_turn_runs_is_taken_and_ends_idle(self):
        n = []
        r_box = {}

        def ladder(body):
            n.append(1)
            if len(n) == 1:
                info, parts = _user(sid, "a word in between")
                r_box["took"] = r_box["r"].interject(sid, "a word in between", info, parts)
                return {"choices": [{"message": {"content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "state", "arguments": "{}"}}]}}]}
            return _reply("done")
        r, hub, sid = _runner(ladder)
        r_box["r"] = r
        r.run(sid, "hi", *_user(sid))
        _settle(r, hub, sid)
        self.assertTrue(r_box["took"])
        self.assertEqual(len(n), 2)
        self.assertEqual(hub.last_state(sid), "idle")

    def test_a_membrane_only_send_ends_idle(self):
        called = []
        r, hub, sid = _runner(lambda body: called.append(1) or _reply())
        r.run(sid, "hi", *_user(sid, membraneOnly=True))
        self.assertEqual(called, [])
        self.assertEqual(hub.last_state(sid), "idle")

    def test_a_job_continuation_ends_idle(self):
        r, hub, sid = _runner()
        r.cosmetic_busy = True
        info = {"id": "msg_jobturn1_1", "sessionID": sid, "role": "user", "agent": "build",
                "continueTurn": True, "jobTurn": True, "jobDepth": 1, "jobText": "job #1 finished: ok",
                "time": {"created": int(time.time() * 1000)}}
        r.run(sid, "", user_info=info, user_parts=[])
        self.assertTrue(hub.busied(sid))
        self.assertEqual(hub.last_state(sid), "idle")

    def test_stop_with_nothing_running_ends_idle(self):
        r, hub, sid = _runner()
        r.abort(sid)
        self.assertEqual(hub.last_state(sid), "idle")

    def test_stop_mid_turn_ends_idle(self):
        hold = threading.Event()
        r, hub, sid = _runner(lambda body: hold.wait(5) and _reply())
        t = threading.Thread(target=lambda: r.run(sid, "hi", *_user(sid)))
        t.start()
        time.sleep(0.2)
        r.abort(sid)
        hold.set()
        t.join(5)
        _settle(r, hub, sid)
        self.assertEqual(hub.last_state(sid), "idle")

    def test_a_persons_preempt_ends_idle(self):
        hold = threading.Event()
        n = []

        def ladder(body):
            n.append(1)
            if len(n) == 1:
                hold.wait(5)
            return _reply("done %d" % len(n))
        r, hub, sid = _runner(ladder)
        r.busy_wait_s = 0.1
        t = threading.Thread(target=lambda: r.run(sid, "loop pulse", *_user(sid, "loop pulse")))
        t.start()
        time.sleep(0.2)
        said = "[HA user: Dmitrii]\nstop that, do this"
        r.run(sid, said, *_user(sid, said))
        hold.set()
        t.join(5)
        _settle(r, hub, sid)
        self.assertEqual(len(n), 2)
        self.assertEqual(hub.last_state(sid), "idle")


# ---- the page: the reconcile decision (node) ------------------------------------

def _js(expr):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "panel.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _rec(local, seat, now):
    return _js("L.reconcileStatus(%s, %s, %d)" % (json.dumps(local), json.dumps(seat), now))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestReconcile(unittest.TestCase):
    NOW = 1_000_000

    def test_idle_page_is_left_alone(self):
        self.assertEqual(_rec({"type": "idle"}, {"type": "busy"}, self.NOW), {"type": "idle"})

    def test_the_seat_idle_wins_over_a_stale_busy(self):
        self.assertEqual(_rec({"type": "busy", "since": self.NOW - 60_000}, {"type": "idle"}, self.NOW),
                         {"type": "idle"})

    def test_a_session_the_seat_does_not_list_is_idle(self):
        self.assertEqual(_rec({"type": "busy", "since": self.NOW - 60_000}, None, self.NOW),
                         {"type": "idle"})

    def test_a_just_sent_message_keeps_its_busy(self):
        # the seat may not have the turn yet: the optimistic busy holds a moment
        local = {"type": "busy", "since": self.NOW - 1_000}
        self.assertEqual(_rec(local, {"type": "idle"}, self.NOW), local)

    def test_the_seat_busy_keeps_the_elapsed_timer_and_takes_the_queue(self):
        out = _rec({"type": "busy", "since": self.NOW - 30_000},
                   {"type": "busy", "queued": True, "depth": 2}, self.NOW)
        self.assertEqual(out, {"type": "busy", "since": self.NOW - 30_000, "queued": True, "depth": 2})

    def test_the_seat_busy_without_a_local_clock_starts_from_its_elapsed(self):
        out = _rec({"type": "busy"}, {"type": "busy", "elapsed_ms": 4_000}, self.NOW)
        self.assertEqual(out["type"], "busy")
        self.assertEqual(out["since"], self.NOW - 4_000)


class TestReconcileWiring(unittest.TestCase):
    def setUp(self):
        self.panel = (UI / "panel.js").read_text()

    def test_it_asks_the_seat_only_while_busy_and_never_overlaps(self):
        self.assertIn("const RECONCILE_MS = 5000;", self.panel)
        self.assertIn("_syncReconcile()", self.panel)
        body = self.panel.split("async _reconcileOnce()", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("this._reconciling", body)
        self.assertIn('SERVE + "/session/status"', body)
        self.assertIn("reconcileStatus(", body)

    def test_it_stops_with_the_page(self):
        dc = self.panel.split("disconnectedCallback() {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("_stopReconcile()", dc)


if __name__ == "__main__":
    unittest.main()
