"""THE TURN CAP AND CONTINUE (issue #389, the operator's 2026-10-21):
MAX_TURNS = 4 per Claude answer, with a Continue. (#425 replaced the 4-turn
cap with check-ins and a tool-call ceiling; the cap here is the SDK's
backstop, marked the same way: tests/test_claude_agent_checkin_425.py.)

- a claude-agent answer that hits the cap is MARKED (the SDK's
  error_max_turns result is neither an error nor a silent cut): the reply
  carries the cap and the SDK session id, the transcript row says so and
  offers Continue;
- Continue is a person's action: it resumes the SAME SDK session (the SDK's
  `resume`) for another MAX_TURNS, with the same tools and options, and can
  be pressed again at the next cap; a capped answer continues once (nothing
  runs twice), never on a background lane, never by itself;
- a RAM counter of cap hits and continues answers on /membrane/diag.

The SDK is a stand-in: no real Claude, no login.
"""
import json
import os
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import server as MS  # noqa: E402
from tests.test_claude_agent_384 import key_fixture  # noqa: E402
from tests.test_claude_agent_tools_389 import (  # noqa: E402
    AssistantMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
    fake_sdk as _base_sdk)


class ResultMessage:
    def __init__(self, subtype="success", session_id="sess-1"):
        self.subtype = subtype
        self.is_error = subtype != "success"
        self.result = None
        self.session_id = session_id
        self.usage = {"input_tokens": 9, "output_tokens": 2}


def capping_sdk(capped=True, session_id="sess-1"):
    """A stand-in SDK whose loop runs one tool and then stops at the cap
    (subtype error_max_turns), or answers normally (capped=False)."""
    mod = _base_sdk()
    mod.tool_runs = []

    async def query(*, prompt, options=None, transport=None):
        mod.calls.append({"prompt": prompt, "options": options})
        if getattr(options, "resume", None) is None:
            mod.tool_runs.append("learn")
            yield AssistantMessage([ToolUseBlock("toolu_1", "mcp__membrane__learn", {"lesson": "x"})])
            yield UserMessage([ToolResultBlock("toolu_1", "Learned as note #1", False)])
        yield AssistantMessage([TextBlock("still working" if capped else "done")])
        yield ResultMessage("error_max_turns" if capped else "success", session_id)

    mod.query = query
    return mod


class _SDKCase(unittest.TestCase):
    capped = True

    def setUp(self):
        self.sdk = capping_sdk(self.capped)
        self._p = [mock.patch.dict(sys.modules, {"claude_agent_sdk": self.sdk}),
                   mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"),
                   key_fixture()]                       # #413: a fake key
        for p in self._p:
            p.start()
        CA.reset_stats()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()


# ---- the cap is marked, not an error -----------------------------------------

class TestCapMarked(_SDKCase):
    def test_iter_reply_marks_the_cap(self):
        got = list(CA.iter_reply([{"role": "user", "content": "go"}],
                                 "claude-agent:sonnet", stream=False, port=9))
        capped = [v for k, v in got if k == "capped"]
        self.assertEqual(capped, [{"session": "sess-1", "calls": CA.MAX_TOOL_CALLS_PER_ANSWER}])   # #425: calls
        self.assertIn("still working", "".join(v for k, v in got if k == "text"))
        self.assertEqual(CA.stats()["ceiling_hits"], 1)   # #425

    def test_buffered_reply_carries_the_cap_and_no_error(self):
        body = json.loads(CA.open_reply({"model": "claude-agent:sonnet", "stream": False,
                                         "messages": [{"role": "user", "content": "go"}]},
                                        port=9).read())
        msg = body["choices"][0]["message"]["content"]
        self.assertNotIn("the turn failed", msg)
        self.assertEqual(body[CA.CAPPED_KEY], {"session": "sess-1", "calls": CA.MAX_TOOL_CALLS_PER_ANSWER})   # #425

    def test_streamed_reply_carries_the_cap(self):
        lines = b"".join(CA.open_reply({"model": "claude-agent:sonnet", "stream": True,
                                        "messages": [{"role": "user", "content": "go"}]},
                                       port=9))
        chunks = [json.loads(ln[5:]) for ln in lines.split(b"\n")
                  if ln.startswith(b"data:") and ln[5:].strip() != b"[DONE]"]
        caps = [c[CA.CAPPED_KEY] for c in chunks if c.get(CA.CAPPED_KEY)]
        self.assertEqual(caps, [{"session": "sess-1", "calls": CA.MAX_TOOL_CALLS_PER_ANSWER}])   # #425
        text = "".join((c.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                       for c in chunks if c.get("choices"))
        self.assertNotIn("the turn failed", text)


class TestNotCapped(_SDKCase):
    capped = False

    def test_a_normal_answer_has_no_cap(self):
        got = list(CA.iter_reply([{"role": "user", "content": "go"}],
                                 "claude-agent:sonnet", stream=False, port=9))
        self.assertFalse([v for k, v in got if k == "capped"])
        self.assertEqual(CA.stats()["ceiling_hits"], 0)   # #425


# ---- continue resumes the same SDK session -----------------------------------

class TestResume(_SDKCase):
    def test_resume_uses_the_session_and_the_same_options(self):
        CA.open_reply({"model": "claude-agent:sonnet", "stream": False,
                       "messages": [{"role": "user", "content": "go"}]}, port=9).read()
        CA.open_reply({"model": "claude-agent:sonnet", "stream": False,
                       "messages": [{"role": "user", "content": "go"},
                                    {"role": "assistant", "content": "still working"},
                                    {"role": "user", "content": "Continue"}],
                       CA.RESUME_KEY: "sess-1"}, port=9).read()
        first, second = (c["options"].kw for c in self.sdk.calls)
        self.assertNotIn("resume", first)
        self.assertEqual(second["resume"], "sess-1")
        self.assertEqual(second["max_turns"], CA.MAX_TURNS)
        self.assertEqual(second["max_turns"], 2 * CA.MAX_TOOL_CALLS_PER_ANSWER)   # #425 backstop
        self.assertEqual(second["allowed_tools"], first["allowed_tools"])
        self.assertEqual(second["tools"], [])
        self.assertEqual(set(second["mcp_servers"]), {"membrane"})
        self.assertEqual(self.sdk.calls[1]["prompt"], CA.CONTINUE_PROMPT)
        self.assertEqual(self.sdk.tool_runs, ["learn"])        # nothing ran twice
        self.assertEqual(CA.stats(), {"checkin_denials": 0, "ceiling_hits": 2,
                                      "continues": 1})   # #425 counters


# ---- the transcript and the person's Continue -------------------------------

def _ladder(bodies, sdk_caps):
    """A loopback stand-in: each call answers through CA.open_reply (the
    proxy's own path) and records the body it got."""
    def ladder(body):
        bodies.append(body)
        return json.loads(CA.open_reply(dict(body, stream=False), port=9).read())
    return ladder


class _Runner(_SDKCase):
    agent = "build"

    def setUp(self):
        super().setUp()
        self.bodies = []
        self.r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                               _ladder(self.bodies, True), lambda *a: "ok",
                               system="", model="claude-agent:sonnet")
        self.sid = self.r.sessions.create(title="t")["id"]
        self.r.sessions.sessions[self.sid]["agent"] = self.agent

    def _asst(self):
        return [x for x in self.r.messages.list(self.sid) if x["info"]["role"] == "assistant"][-1]


class TestTranscriptAndContinue(_Runner):
    def test_the_capped_row_is_marked_and_offers_continue(self):
        self.r.run(self.sid, "do the long thing")
        info = self._asst()["info"]
        self.assertTrue(CA.is_claude_made(info))
        self.assertEqual(info[CA.CAP_MARK], {"session": "sess-1",   # #425: calls
                                             "calls": CA.MAX_TOOL_CALLS_PER_ANSWER,
                                             "continued": False})
        self.assertFalse(info.get("error"))                       # not an error

    def test_continue_resumes_once_and_again_at_the_next_cap(self):
        self.r.run(self.sid, "do the long thing")
        mid = self._asst()["info"]["id"]
        sess = self.r.claude_continue(self.sid, mid)
        self.assertEqual(sess, "sess-1")
        self.assertTrue(self.r.messages.get_msg(self.sid, mid)["info"][CA.CAP_MARK]["continued"])
        self.r.run(self.sid, "Continue", user_info=self.r.claude_continue_row(self.sid, mid, sess))
        self.assertEqual(self.bodies[-1].get(CA.RESUME_KEY), "sess-1")
        self.assertEqual(self.sdk.calls[-1]["options"].kw["resume"], "sess-1")
        self.assertEqual(self.sdk.calls[-1]["options"].kw["max_turns"], CA.MAX_TURNS)
        # the same capped answer never continues twice
        with self.assertRaises(CA.ContinueRefused):
            self.r.claude_continue(self.sid, mid)
        # the continued answer capped again: it offers its own Continue
        mid2 = self._asst()["info"]["id"]
        self.assertNotEqual(mid2, mid)
        self.assertEqual(self.r.claude_continue(self.sid, mid2), "sess-1")
        self.assertEqual(self.sdk.tool_runs, ["learn"])          # nothing ran twice

    def test_a_plain_message_never_resumes(self):
        self.r.run(self.sid, "do the long thing")
        self.r.run(self.sid, "Continue")                          # typed, not the action
        self.assertNotIn(CA.RESUME_KEY, self.bodies[-1])
        self.assertNotIn("resume", self.sdk.calls[-1]["options"].kw)

    def test_an_uncapped_row_refuses(self):
        self.r.run(self.sid, "x")
        user_mid = [x for x in self.r.messages.list(self.sid)
                    if x["info"]["role"] == "user"][-1]["info"]["id"]
        with self.assertRaises(CA.ContinueRefused):
            self.r.claude_continue(self.sid, user_mid)
        with self.assertRaises(CA.ContinueRefused):
            self.r.claude_continue(self.sid, "msg_nope")


class TestBackgroundNeverContinues(_Runner):
    agent = "watch:battery"

    def test_a_background_session_refuses_continue(self):
        # a capped row on a loop session (it cannot even reach Claude; the
        # mark is planted to prove the Continue itself refuses)
        info = {"id": "msg_cap", "role": "assistant", "sessionID": self.sid,
                CA.MARK_KEY: CA.PROVIDER,
                CA.CAP_MARK: {"session": "sess-1", "turns": 4, "continued": False}}
        self.r.messages.append(self.sid, info, [])
        with self.assertRaises(CA.ContinueRefused):
            self.r.claude_continue(self.sid, "msg_cap")
        self.assertEqual(CA.stats()["continues"], 0)

    def test_a_resume_on_a_background_turn_is_dropped(self):
        ui, parts = self.r._mint_user_row(self.sid, "Continue")
        ui[CA.RESUME_KEY] = "sess-1"                # planted: the lane must drop it
        self.r.messages.append(self.sid, ui, parts)
        self.r.run(self.sid, "Continue", user_info=ui)
        self.assertFalse([b for b in self.bodies if b.get(CA.RESUME_KEY)])
        self.assertFalse([c for c in self.sdk.calls if c["options"].kw.get("resume")])


# ---- the route: a person's POST ----------------------------------------------

class TestContinueRoute(_Runner):
    def setUp(self):
        super().setUp()
        handler = MS.make_handler(hub=self.r.hub, sessions=self.r.sessions,
                                  messages=self.r.messages, config={}, runner=self.r)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.shutdown)
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"

    def _post(self, mid):
        body = {"parts": [{"type": "text", "text": "Continue"}], CA.CONTINUE_FIELD: mid}
        rq = urllib.request.Request(f"{self.base}/session/{self.sid}/message",
                                    data=json.dumps(body).encode(), method="POST",
                                    headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(rq, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_the_button_resumes_and_a_second_press_is_refused(self):
        self.r.run(self.sid, "do the long thing")
        mid = self._asst()["info"]["id"]
        self.assertEqual(self._post(mid), 200)
        self.assertEqual(self.bodies[-1].get(CA.RESUME_KEY), "sess-1")
        n = len(self.sdk.calls)
        self.assertEqual(self._post(mid), 409)
        self.assertEqual(len(self.sdk.calls), n)                  # no second run
        self.assertEqual(CA.stats()["continues"], 1)


# ---- the diagnostics counter ------------------------------------------------

class TestDiagCounter(_SDKCase):
    def test_diag_route_reports_the_counters(self):
        from membrane import diag as D
        self.assertIn("/membrane/diag/claude-agent", D.DIAG_PATHS)
        list(CA.iter_reply([{"role": "user", "content": "go"}], "claude-agent:sonnet",
                           stream=False, port=9))
        sent = {}
        h = types.SimpleNamespace(path="/membrane/diag/claude-agent", command="GET",
                                  headers={}, client_address=("127.0.0.1", 1))
        with mock.patch.object(D, "_send", lambda hh, code, obj: sent.update(code=code, obj=obj)), \
                mock.patch("membrane.seatauth.is_loopback", lambda hh: True):
            self.assertTrue(D.route(h, "/membrane/diag/claude-agent"))
        self.assertEqual(sent["code"], 200)
        self.assertEqual(sent["obj"]["ceiling_hits"], 1)   # #425
        self.assertEqual(sent["obj"]["continues"], 0)
        self.assertEqual(sent["obj"]["max_turns"], CA.MAX_TURNS)


if __name__ == "__main__":
    unittest.main()


# ---- the chat's Continue (panel.js) ------------------------------------------

import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402

_PANEL = Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js"


def _cap_html(info):
    js = ("const M=require(%r); process.stdout.write(JSON.stringify(M.capHtml(%s)))"
          % (str(_PANEL), json.dumps(info)))
    return json.loads(_subprocess.check_output(["node", "-e", js]))


@unittest.skipIf(not _shutil.which("node"), "node not installed")
class TestChatContinueButton(unittest.TestCase):
    def test_a_capped_answer_says_so_and_offers_continue(self):
        html = _cap_html({"id": "msg_1", CA.CAP_MARK: {"session": "s", "turns": 4, "continued": False}})
        self.assertIn("stopped at the turn cap", html)
        self.assertIn('data-ccont="msg_1"', html)

    def test_a_continued_answer_offers_no_second_press(self):
        html = _cap_html({"id": "msg_1", CA.CAP_MARK: {"session": "s", "turns": 4, "continued": True}})
        self.assertIn("stopped at the turn cap", html)
        self.assertNotIn("data-ccont", html)

    def test_an_uncapped_answer_shows_nothing(self):
        self.assertEqual(_cap_html({"id": "msg_1"}), "")
