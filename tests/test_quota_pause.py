"""THE OUT-OF-TOKENS PAUSE (operator, 2026-10-23/24).

When a cloud model says its quota is spent -- the claude-agent rung's
`rate_limit` ("You've hit your session limit · resets 10:50pm
(America/New_York)"), a usage-limit error, an Ollama cloud 429 -- ONLY that
model pauses (membrane/quota.py): its calls are not sent at all until its
reset time (parsed, with the timezone) or, with no reset given, until the
next real call after QUOTA_PROBE_S succeeds (the probe is a real call, never a
synthetic one). Other models keep working.

  * a person's chat on a paused model WAITS: the message is held (stays
    queued), the page shows "<model> is out of tokens · it resumes at HH:MM"
    with Resume now; Resume now clears the pause and sends the held message;
  * the background minds (the Goal worker's loops, the Idle thinker) PARK:
    goals stay open, the loops' events wait, nothing is called;
  * the state lives in one place, RAM + <data dir>/quota.json, on the serve
    port: GET /membrane/quota, POST /membrane/quota/resume {model}.
"""
import calendar
import json
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import quota as Q  # noqa: E402
from membrane import server as S  # noqa: E402
from tests.test_claude_agent_384 import (AssistantMessage, ResultMessage,  # noqa: E402
                                         TextBlock, _SDKCase)
from tests.test_nerve_pause_348 import _Env, _seat, _serve  # noqa: E402
from tests.test_offbox_auth import _req  # noqa: E402

# 2026-10-24 20:00:00 in New York (EDT, UTC-4) = 2026-10-25 00:00:00 UTC
NOW = calendar.timegm((2026, 10, 25, 0, 0, 0))
LIMIT = "You've hit your session limit · resets 10:50pm (America/New_York)"


class _Fresh(unittest.TestCase):
    """quota's RAM and file under a temp data dir."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": self.tmp.name})
        self._env.start()
        Q._forget()

    def tearDown(self):
        Q._forget()
        self._env.stop()
        self.tmp.cleanup()


# ---- reading the errors ----------------------------------------------------------

class TestTheErrors(unittest.TestCase):
    def test_the_session_limit_line_resets_at_its_time_in_its_zone(self):
        # 22:50 New York the same evening = 02:50 UTC the next day
        self.assertEqual(Q.parse_reset(LIMIT, now=NOW), calendar.timegm((2026, 10, 25, 2, 50, 0)))

    def test_a_time_already_past_today_is_tomorrow(self):
        t = Q.parse_reset("limit reached · resets 9am (America/New_York)", now=NOW)
        self.assertEqual(t, calendar.timegm((2026, 10, 25, 13, 0, 0)))     # 09:00 EDT tomorrow

    def test_a_dated_reset(self):
        t = Q.parse_reset("You've hit your weekly limit · resets Oct 27, 10am (Europe/London)", now=NOW)
        self.assertEqual(t, calendar.timegm((2026, 10, 27, 10, 0, 0)))     # GMT after the 25th

    def test_the_old_epoch_form(self):
        self.assertEqual(Q.parse_reset("Claude AI usage limit reached|1793000000", now=NOW), 1793000000)

    def test_no_reset_in_the_error(self):
        self.assertIsNone(Q.parse_reset("claude-agent: rate_limit", now=NOW))
        self.assertIsNone(Q.parse_reset("HTTP Error 429: Too Many Requests", now=NOW))

    def test_retry_after_seconds(self):
        self.assertEqual(Q.retry_after("120", now=NOW), NOW + 120)
        self.assertIsNone(Q.retry_after(None, now=NOW))

    def test_which_errors_are_out_of_tokens(self):
        for s, code in [("claude-agent: rate_limit", None), (LIMIT, None),
                        ("HTTP Error 429: Too Many Requests", 429),
                        ("you (chedim) have reached your weekly usage limit", None),
                        ("Payment required", 402),
                        ("claude-agent: billing_error", None)]:
            self.assertTrue(Q.is_quota_error(s, code), s)
        for s, code in [("HTTP Error 500: boom", 500), ("connection refused", None),
                        ("claude-agent: server_error", None), ("", None)]:
            self.assertFalse(Q.is_quota_error(s, code), s)


# ---- one model at a time ----------------------------------------------------------

class TestPerModel(_Fresh):
    def test_only_that_model_pauses(self):
        Q.note("claude-agent:sonnet", LIMIT, until=NOW + 600, now=NOW)
        self.assertIsNotNone(Q.paused("claude-agent:sonnet", now=NOW))
        self.assertIsNone(Q.paused("glm-5.3-flash:cloud", now=NOW))
        self.assertIsNone(Q.paused("claude-agent:opus", now=NOW))

    def test_the_pause_ends_at_its_reset(self):
        Q.note("m", LIMIT, until=NOW + 600, now=NOW)
        self.assertEqual(Q.paused("m", now=NOW + 599)["resumes_at"], NOW + 600)
        self.assertIsNone(Q.paused("m", now=NOW + 600))
        self.assertEqual(Q.snapshot(now=NOW + 601), [])

    def test_no_reset_waits_for_the_probe(self):
        Q.note("m", "HTTP Error 429", now=NOW)
        e = Q.paused("m", now=NOW + 1)
        self.assertEqual((e["until"], e["resumes_at"]), (None, NOW + Q.PROBE_S))
        # the probe is due: the next real call goes out (nothing synthetic)
        self.assertIsNone(Q.paused("m", now=NOW + Q.PROBE_S))
        # ...it failed again: paused for another interval
        Q.note("m", "HTTP Error 429", now=NOW + Q.PROBE_S + 1)
        self.assertEqual(Q.paused("m", now=NOW + Q.PROBE_S + 2)["resumes_at"], NOW + 2 * Q.PROBE_S + 1)
        self.assertEqual(Q.paused("m", now=NOW + Q.PROBE_S + 2)["since"], NOW)
        # ...it answered: free
        Q.ok("m")
        self.assertIsNone(Q.paused("m", now=NOW + Q.PROBE_S + 3))

    def test_note_error_pauses_only_on_quota_errors(self):
        self.assertIsNone(Q.note_error("m", "HTTP Error 500: boom", code=500, now=NOW))
        self.assertIsNone(Q.paused("m", now=NOW))
        e = Q.note_error("m", "HTTP Error 429: Too Many Requests", code=429, retry_after="60", now=NOW)
        self.assertEqual(e["until"], NOW + 60)

    def test_resume_now_clears_and_tells(self):
        seen = []
        Q.subscribe(lambda m, why: seen.append((m, why)))
        Q.note("m", LIMIT, until=NOW + 600, now=NOW)
        self.assertTrue(Q.resume("m"))
        self.assertIsNone(Q.paused("m", now=NOW))
        self.assertIn(("m", "paused"), seen)
        self.assertIn(("m", "resumed"), seen)
        self.assertFalse(Q.resume("m"))

    def test_a_restart_keeps_the_pause(self):
        Q.note("m", LIMIT, until=NOW + 600, now=NOW)
        self.assertTrue((Path(self.tmp.name) / Q.FILE).exists())
        Q._forget()                                  # the restart: RAM gone, the file stays
        self.assertEqual(Q.paused("m", now=NOW)["until"], NOW + 600)

    def test_the_paused_error_says_when(self):
        e = Q.QuotaPaused.of(Q.note("m", LIMIT, until=NOW + 600, now=NOW))
        self.assertEqual((e.model, e.until), ("m", NOW + 600))
        self.assertIn("m is out of tokens", str(e))
        back = Q.from_error_obj(Q.error_obj(e))
        self.assertEqual((back.model, back.until), ("m", NOW + 600))
        self.assertIsNone(Q.from_error_obj({"error": {"type": "seat_busy"}}))


# ---- one timer per pause, no polling ------------------------------------------------------

class TestTheTimers(_Fresh):
    """Each pause arms ONE timer at its exact deadline (the reset, or the
    probe time); clearing the pause cancels it, a new pause replaces it, the
    boot re-arms the persisted ones. Nothing polls the clock."""

    def _heard(self):
        seen = []
        Q.subscribe(lambda m, why: seen.append((m, why)))
        return seen

    def test_there_is_no_clock_polling(self):
        self.assertFalse(hasattr(Q, "TICK_S"))
        self.assertFalse(hasattr(Q, "start_waker"))
        Q.note("m", LIMIT, until=time.time() + 60)
        self.assertNotIn("membrane-quota", [t.name for t in threading.enumerate()])

    def test_a_reset_soon_releases_the_held_message(self):
        r, lad, _ = _runner()
        sid = r.sessions.create("chat")["id"]
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 0.2)
        self.assertEqual(r.run(sid, "wait for me")["status"], "held")
        self.assertTrue(_wait(lambda: _texts(r, sid) == ["answered"], 3))
        self.assertIsNone(Q.paused("cloudy:cloud"))
        self.assertEqual(Q._timers, {})

    def test_resume_now_cancels_the_timer(self):
        seen = self._heard()
        Q.note("m", LIMIT, until=time.time() + 0.3)
        self.assertIn("m", Q._timers)
        Q.resume("m")
        self.assertEqual(Q._timers, {})
        time.sleep(0.5)
        self.assertNotIn(("m", "reset"), seen)

    def test_an_answer_cancels_the_timer(self):
        Q.note("m", "HTTP 429", now=time.time() - 2 * Q.PROBE_S)
        Q.ok("m")
        self.assertEqual(Q._timers, {})

    def test_a_new_reset_replaces_the_timer(self):
        seen = self._heard()
        Q.note("m", LIMIT, until=time.time() + 0.2)
        first = Q._timers["m"]
        Q.note("m", LIMIT, until=time.time() + 30)
        self.assertIsNot(Q._timers["m"], first)
        time.sleep(0.5)
        self.assertNotIn(("m", "reset"), seen)
        self.assertIsNotNone(Q.paused("m"))

    def test_the_probe_time_fires_once(self):
        seen = self._heard()
        with mock.patch.object(Q, "PROBE_S", 0.2):
            Q.note("m", "HTTP 429")
        self.assertTrue(_wait(lambda: ("m", "probe") in seen, 3))
        time.sleep(0.3)
        self.assertEqual(seen.count(("m", "probe")), 1)

    def test_the_boot_rearms_the_kept_pauses(self):
        Q.note("m", LIMIT, until=time.time() + 0.3)
        Q._forget()                               # the restart: RAM and timers gone
        self.assertEqual(Q._timers, {})
        seen = self._heard()
        self.assertEqual(Q.arm_all(), 1)
        self.assertTrue(_wait(lambda: ("m", "reset") in seen, 3))
        self.assertIsNone(Q.paused("m"))


# ---- the claude-agent rung ----------------------------------------------------------

def _limited_sdk(sdk, events):
    async def query(*, prompt, options=None, transport=None):
        sdk.calls.append({"prompt": prompt, "options": options})
        for ev in events:
            yield ev
    sdk.query = query


class RateLimitInfo:
    def __init__(self, status, resets_at=None):
        self.status, self.resets_at, self.rate_limit_type = status, resets_at, "five_hour"


class RateLimitEvent:
    def __init__(self, info):
        self.rate_limit_info, self.uuid, self.session_id = info, "u", "s"


class TestTheClaudeRung(_SDKCase):
    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": self.tmp.name})
        self._env.start()
        Q._forget()

    def tearDown(self):
        Q._forget()
        self._env.stop()
        self.tmp.cleanup()
        super().tearDown()

    def _ask(self, stream=True):
        return list(CA.iter_reply([{"role": "user", "content": "hi"}], "claude-agent:sonnet", stream))

    def test_the_rejected_event_gives_the_reset(self):
        reset = int(time.time()) + 3600
        _limited_sdk(self.sdk, [RateLimitEvent(RateLimitInfo("rejected", reset)),
                                AssistantMessage([TextBlock(LIMIT)], error="rate_limit")])
        with self.assertRaises(Q.QuotaPaused) as cm:
            self._ask()
        self.assertEqual(cm.exception.until, reset)
        self.assertEqual(Q.paused("claude-agent:sonnet")["until"], reset)
        self.assertIsNone(Q.paused("claude-agent:opus"))

    def test_the_line_alone_gives_the_reset(self):
        _limited_sdk(self.sdk, [AssistantMessage([TextBlock(LIMIT)], error="rate_limit")])
        with self.assertRaises(Q.QuotaPaused):
            self._ask(stream=False)
        e = Q.paused("claude-agent:sonnet")
        self.assertEqual(e["until"], Q.parse_reset(LIMIT, now=e["since"]))
        self.assertIn("session limit", e["reason"])

    def test_a_bare_rate_limit_waits_for_the_probe(self):
        _limited_sdk(self.sdk, [AssistantMessage([], error="rate_limit")])
        with self.assertRaises(Q.QuotaPaused):
            self._ask()
        self.assertIsNone(Q.paused("claude-agent:sonnet")["until"])

    def test_a_paused_rung_is_not_called(self):
        Q.note("claude-agent:sonnet", LIMIT, until=time.time() + 600)
        for stream in (True, False):
            with self.assertRaises(Q.QuotaPaused):
                CA.open_reply({"model": "claude-agent:sonnet", "stream": stream,
                               "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(self.sdk.calls, [])

    def test_the_replies_raise_the_pause_for_the_proxy(self):
        _limited_sdk(self.sdk, [AssistantMessage([TextBlock(LIMIT)], error="rate_limit")])
        with self.assertRaises(Q.QuotaPaused):
            list(CA.SSEReply([{"role": "user", "content": "hi"}], "claude-agent:sonnet", self.sdk))
        Q._forget()
        with self.assertRaises(Q.QuotaPaused):
            CA.JSONReply([{"role": "user", "content": "hi"}], "claude-agent:sonnet", self.sdk).read()

    def test_an_answer_clears_a_probing_pause(self):
        Q.note("claude-agent:sonnet", "rate_limit", now=time.time() - 2 * Q.PROBE_S)
        self.assertIsNone(Q.paused("claude-agent:sonnet"))        # the probe is due
        self.assertEqual(Q.snapshot()[0]["model"], "claude-agent:sonnet")
        _limited_sdk(self.sdk, [AssistantMessage([TextBlock("hello")]), ResultMessage()])
        self._ask()
        self.assertEqual(Q.snapshot(), [])


# ---- a person's chat waits ------------------------------------------------------------

class _Ladder:
    """The loopback ladder's stand-in: answers 'ok', or raises the proxy's
    pause while `paused` names the model."""

    def __init__(self):
        self.bodies = []
        self.fail_with = None
        self.echo = False

    def __call__(self, body):
        self.bodies.append(body)
        if self.fail_with is not None:
            e = self.fail_with           # the proxy notes the pause, then answers it
            Q.note(e.model, e.reason, until=e.until)
            raise e
        if self.echo:
            said = [m for m in body["messages"] if m.get("role") == "user"][-1]["content"]
            return {"choices": [{"message": {"content": "echo: " + said}}]}
        return {"choices": [{"message": {"content": "answered"}}]}


def _runner(model="cloudy:cloud"):
    lad = _Ladder()
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), lad,
                     lambda n, a, c: "ok", system="", model=model)
    events = []
    q = r.hub.subscribe()

    def drain():
        while True:
            ev = q.get()
            events.append(ev)
    threading.Thread(target=drain, daemon=True).start()
    return r, lad, events


def _wait(cond, s=5.0):
    t = time.time() + s
    while time.time() < t:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _texts(r, sid, role="assistant"):
    return ["".join(p.get("text") or "" for p in row["parts"] if p.get("type") == "text")
            for row in r.messages.list(sid) if row["info"]["role"] == role]


class TestTheChatWaits(_Fresh):
    def test_a_paused_model_holds_the_message_and_resume_sends_it(self):
        r, lad, events = _runner()
        sid = r.sessions.create("chat")["id"]
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 3600)
        out = r.run(sid, "hello there")
        self.assertEqual(out["status"], "held")
        self.assertEqual(lad.bodies, [])                         # nothing was sent
        self.assertEqual(_texts(r, sid, "user"), ["hello there"])   # the message is kept
        held = r.held("cloudy:cloud")
        self.assertEqual([(h["sessionID"], h["text"]) for h in held], [(sid, "hello there")])
        self.assertTrue(_wait(lambda: any(e.get("type") == "membrane.quota" for e in events)))
        # Resume now: the pause is cleared and the held message goes
        Q.resume("cloudy:cloud")
        self.assertTrue(_wait(lambda: _texts(r, sid) == ["answered"]))
        self.assertEqual(r.held("cloudy:cloud"), [])
        self.assertEqual(lad.bodies[0]["messages"][-1]["content"], "hello there")

    def test_the_released_message_is_answered_on_its_own_row(self):
        # a backend that echoes the person's text: the release must send it and
        # the reply must land on the assistant row parented on the held message
        r, lad, _ = _runner()
        lad.echo = True
        sid = r.sessions.create("chat")["id"]
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 3600)
        r.run(sid, "echo me back")
        user_id = [row["info"]["id"] for row in r.messages.list(sid) if row["info"]["role"] == "user"][0]
        Q.resume("cloudy:cloud")
        self.assertTrue(_wait(lambda: _texts(r, sid) == ["echo: echo me back"]))
        (a,) = [row for row in r.messages.list(sid) if row["info"]["role"] == "assistant"]
        self.assertEqual(a["info"]["parentID"], user_id)
        self.assertEqual([row["info"]["role"] for row in r.messages.list(sid)], ["user", "assistant"])

    def test_a_streamed_round_that_hits_the_limit_holds_without_a_second_call(self):
        # the streamed round (#505's part publishing): the pause ends the round,
        # no buffered retry goes out, the message waits, and Resume now answers it
        r, lad, _ = _runner()
        streamed = []

        def stream(body, on_event):
            streamed.append(body)
            if lad.fail_with is not None:
                e = lad.fail_with
                Q.note(e.model, e.reason, until=e.until)
                raise e
            on_event("text", "streamed answer")
            return {"choices": [{"message": {"content": "streamed answer"}, "finish_reason": "stop"}]}
        lad.stream = stream
        sid = r.sessions.create("chat")["id"]
        lad.fail_with = Q.QuotaPaused("cloudy:cloud", time.time() + 3600, LIMIT)
        r.run(sid, "stream me")
        self.assertEqual((len(streamed), lad.bodies), (1, []))      # no buffered second call
        self.assertEqual([h["text"] for h in r.held("cloudy:cloud")], ["stream me"])
        lad.fail_with = None
        Q.resume("cloudy:cloud")
        self.assertTrue(_wait(lambda: _texts(r, sid)[-1:] == ["streamed answer"]))
        self.assertEqual(streamed[-1]["messages"][-1]["content"], "stream me")

    def test_another_model_still_answers(self):
        r, lad, _ = _runner(model="local:8b")
        sid = r.sessions.create("chat")["id"]
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 3600)
        out = r.run(sid, "hi")
        self.assertNotEqual(out.get("status"), "held")
        self.assertEqual(_texts(r, sid), ["answered"])

    def test_hitting_the_limit_mid_chat_holds_it_and_resume_retries(self):
        r, lad, _ = _runner()
        sid = r.sessions.create("chat")["id"]
        until = time.time() + 3600
        lad.fail_with = Q.QuotaPaused("cloudy:cloud", until, LIMIT)
        r.run(sid, "first try")
        self.assertEqual([h["text"] for h in r.held("cloudy:cloud")], ["first try"])
        rows = [row for row in r.messages.list(sid) if row["info"]["role"] == "assistant"]
        self.assertEqual(rows[-1]["info"][S.QUOTA_MARK]["model"], "cloudy:cloud")
        # the note row never goes back to the model as history
        self.assertNotIn("out of tokens", json.dumps(r._prior_history(sid, "")))
        # Resume now, and the model still fails: held again, with the new time
        lad.fail_with = Q.QuotaPaused("cloudy:cloud", until + 60, LIMIT)
        Q.resume("cloudy:cloud")
        self.assertTrue(_wait(lambda: len(lad.bodies) == 2))
        self.assertTrue(_wait(lambda: [h["text"] for h in r.held("cloudy:cloud")] == ["first try"]))
        # ...and once it answers, the held message is answered
        lad.fail_with = None
        Q.resume("cloudy:cloud")
        self.assertTrue(_wait(lambda: _texts(r, sid)[-1:] == ["answered"]))
        self.assertEqual(r.held("cloudy:cloud"), [])

    def test_stop_drops_the_held_message(self):
        r, lad, _ = _runner()
        sid = r.sessions.create("chat")["id"]
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 3600)
        r.run(sid, "never mind")
        r.abort(sid)
        self.assertEqual(r.held("cloudy:cloud"), [])

    def test_the_status_names_the_held_chat(self):
        srv, r = _seat(self.tmp.name)
        r.model = "cloudy:cloud"
        sid = r.sessions.create("chat")["id"]
        t0 = time.time()
        Q.note("cloudy:cloud", LIMIT, until=t0 + 600, now=t0)
        r.run(sid, "hello")
        st = Q.status(srv, r)
        (p,) = st["paused"]
        self.assertEqual((p["model"], p["resumes_at"]), ("cloudy:cloud", t0 + 600))
        self.assertEqual([c["sessionID"] for c in p["chats"]], [sid])
        self.assertIn("Goal worker", p["minds"])     # the loops run on the runner's model


# ---- the background minds park ----------------------------------------------------------

class TestTheMindsPark(_Fresh):
    def _srv(self):
        from membrane.watch_agents import LoopRunner, WatchEvents
        srv, r = _seat(self.tmp.name)
        r.model = "cloudy:cloud"
        srv.events_pool = WatchEvents()
        srv.loops = LoopRunner()
        srv.loops._srv = srv
        srv.sessions_made = []
        srv.create_loop_session = lambda *a: srv.sessions_made.append(a) or "ses_x"
        srv.run_loop_turn = lambda *a: {"text": "STEP did a thing"}
        return srv

    def test_a_goal_on_a_paused_model_waits_open(self):
        from membrane.watch_agents import _LoopDrainer
        srv = self._srv()
        est = srv.entity_store
        g = est.add(cls="Goal", name="tidy the docs", summary="")["eid"]
        est.edit(g, properties={"status": "open"})
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 600)
        ev = {"kind": "intent-planted", "entity": g, "event_id": "e1"}
        self.assertEqual(_LoopDrainer(srv).handle(ev), "held")
        self.assertEqual(srv.sessions_made, [])                       # no turn, no session
        self.assertEqual([e["entity"] for e in srv.events_pool.take()], [g])   # the event waits
        self.assertEqual(est.docs[g].properties["status"], "open")
        self.assertNotIn("watch_decisions", est.docs[g].properties)
        (p,) = Q.status(srv, srv._loop_runner)["paused"]
        self.assertEqual([x["name"] for x in p["goals"]], ["tidy the docs"])

    def test_the_pulse_does_not_wake_goals_on_a_paused_model(self):
        from membrane.watch_agents import rewake_open_goals
        srv = self._srv()
        est = srv.entity_store
        g = est.add(cls="Goal", name="g", summary="")["eid"]
        est.edit(g, properties={"status": "open", "intent_planted_ts": 1})
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 600)
        self.assertEqual(rewake_open_goals(srv, min_age_s=1, batch=3), 0)
        Q.resume("cloudy:cloud")
        self.assertEqual(rewake_open_goals(srv, min_age_s=1, batch=3), 1)

    def test_a_loop_turn_that_hits_the_limit_puts_its_event_back(self):
        from membrane.watch_agents import _LoopDrainer
        srv = self._srv()
        est = srv.entity_store
        g = est.add(cls="Goal", name="g", summary="")["eid"]
        est.edit(g, properties={"status": "open"})
        srv.run_loop_turn = lambda *a: {"text": "", "quota": {"model": "cloudy:cloud"}}
        ev = {"kind": "llm-call", "lane": "planner", "entity": g, "event_id": "e1",   # #738: the goal planner
              "prompt": "plan the goal: planted", "depth": 1}
        self.assertEqual(_LoopDrainer(srv).handle(ev), "held")
        self.assertEqual([e["entity"] for e in srv.events_pool.take()], [g])
        self.assertNotIn("watch_decisions", est.docs[g].properties)

    def test_the_idle_thinker_calls_nothing_on_a_paused_model(self):
        from membrane import rag
        srv = types.SimpleNamespace(router=None, backend="http://127.0.0.1:9", think=None)
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 600)
        with mock.patch("urllib.request.urlopen") as up:
            self.assertEqual(rag._model_chat(srv)("", "cloudy:cloud", [{"role": "user", "content": "x"}],
                                                  lane="thinker"), "")
        self.assertFalse(up.called)

    def test_the_idle_thinker_pauses_its_model_on_a_429(self):
        import urllib.error
        from membrane import rag
        srv = types.SimpleNamespace(router=None, backend="http://127.0.0.1:9", think=None)
        err = urllib.error.HTTPError("http://x", 429, "Too Many Requests", {"Retry-After": "90"}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            rag._model_chat(srv)("", "cloudy:cloud", [{"role": "user", "content": "x"}], lane="thinker")
        self.assertIsNotNone(Q.paused("cloudy:cloud"))


# ---- the loopback ladder hears the proxy's pause ----------------------------------------------

class TestTheLadder(_Fresh):
    def _proxy(self, code, body, ctype="application/json"):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def test_buffered_and_streamed_rounds_raise_the_pause(self):
        err = Q.error_obj(Q.QuotaPaused("cloudy:cloud", NOW + 600, LIMIT))
        lad = S.loopback_ladder(self._proxy(429, json.dumps(err).encode()))
        with self.assertRaises(Q.QuotaPaused) as cm:
            lad({"model": "cloudy:cloud", "messages": []})
        self.assertEqual(cm.exception.until, NOW + 600)
        sse = b"data: " + json.dumps(err).encode() + b"\n\ndata: [DONE]\n\n"
        lad = S.loopback_ladder(self._proxy(200, sse, "text/event-stream"))
        with self.assertRaises(Q.QuotaPaused):
            lad.stream({"model": "cloudy:cloud", "messages": []}, lambda k, v: False)


# ---- the proxy's door ------------------------------------------------------------------

class TestTheProxy(_Fresh):
    """A cloud model's 429 pauses it and the proxy answers quota_paused; the
    next call on it is not sent at all."""

    def _backend(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class B(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.server.seen.append(json.loads(self.rfile.read(n) or b"{}"))
                out = b'{"error":"you (someone) have reached your session usage limit, please wait"}'
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "120")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass
        b = ThreadingHTTPServer(("127.0.0.1", 0), B)
        b.seen = []
        threading.Thread(target=b.serve_forever, daemon=True).start()
        self.addCleanup(b.shutdown)
        return b

    def _deliver(self, url, stream):
        from tests.test_busy_timeout_431 import ASK, INJECTION, _handler, _up
        h = _handler(url)
        h._forward_raw = mock.Mock(side_effect=AssertionError("a bare resend"))
        verdict = {"verdict": "escalate", "route": {"route": "cloudy:cloud", "kind": "cloud"}}
        h._v1_deliver(_up("cloudy:cloud", stream), b"{}", ASK, None, verdict, INJECTION)
        return h

    def test_a_429_pauses_the_model_and_the_next_call_is_not_sent(self):
        from tests.test_busy_timeout_431 import _split
        b = self._backend()
        url = f"http://127.0.0.1:{b.server_address[1]}"
        t0 = time.time()
        h = self._deliver(url, stream=False)
        status, body = _split(h.wfile.getvalue())
        self.assertIn("429", status)
        err = json.loads(body)["error"]
        self.assertEqual((err["type"], err["model"]), (Q.ERROR_TYPE, "cloudy:cloud"))
        self.assertAlmostEqual(Q.paused("cloudy:cloud")["until"], t0 + 120, delta=5)
        h = self._deliver(url, stream=True)                  # paused: nothing goes out
        self.assertEqual(len(b.seen), 1)
        status, body = _split(h.wfile.getvalue())
        events = [ln[5:].strip() for ln in body.split(b"\n") if ln.startswith(b"data:")]
        self.assertEqual(events[-1], b"[DONE]")
        self.assertIn(Q.ERROR_TYPE.encode(), b"".join(events))
        h._forward_raw.assert_not_called()


# ---- the serve port --------------------------------------------------------------------

class TestTheRoutes(_Env):
    def setUp(self):
        super().setUp()
        Q._forget()
        self.srv, self.r = _seat(self.tmp.name)
        self.httpd = None

    def tearDown(self):
        if self.httpd is not None:
            self.httpd.shutdown()
        Q._forget()
        super().tearDown()

    def _go(self, method, path, body=None):
        if self.httpd is None:
            self.httpd = _serve(self.srv, self.r, False)
        code, b, _ = _req(self.httpd.server_address[1], path, method, body, None)
        try:
            return code, json.loads(b or b"{}")
        except ValueError:
            return code, b

    def test_status_and_resume(self):
        code, out = self._go("GET", "/membrane/quota")
        self.assertEqual((code, out["paused"]), (200, []))
        Q.note("claude-agent:sonnet", LIMIT, until=time.time() + 600)
        code, out = self._go("GET", "/membrane/quota")
        self.assertEqual([p["model"] for p in out["paused"]], ["claude-agent:sonnet"])
        code, out = self._go("POST", "/membrane/quota/resume", {"model": "claude-agent:sonnet"})
        self.assertEqual(code, 200, out)
        self.assertTrue(out["resumed"])
        self.assertIsNone(Q.paused("claude-agent:sonnet"))
        code, out = self._go("POST", "/membrane/quota/resume", {})
        self.assertEqual(code, 400)

    def test_the_stats_window_shows_the_paused_models(self):
        Q.note("claude-agent:sonnet", LIMIT, until=time.time() + 600)
        code, out = self._go("GET", "/membrane/stats")
        self.assertEqual([p["model"] for p in out["paused"]], ["claude-agent:sonnet"])


if __name__ == "__main__":
    unittest.main()
