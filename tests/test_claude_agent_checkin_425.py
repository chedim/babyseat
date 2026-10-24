"""ONE CONTINUOUS RUN AND A CEILING (issue #425; the check-ins went
2026-10-23, the operator: "let's do it as Claude"): the claude-agent rung
runs ONE continuous SDK run per answer, and its PreToolUse hook only counts.

- no check-ins: no tool call is ever refused for silence, however many come
  in a row without words to the person (the 7-call check-in denial is gone);
- MAX_TOOL_CALLS_PER_ANSWER = 2048: the hard ceiling; at it the run stops the
  way the old cap did (the reply's cap mark, the row's claudeCapped, the
  person's Continue), and Continue resumes the same session with a fresh 50;
- max_turns is only a backstop that never binds before the ceiling;
- nothing runs twice, nothing continues by itself;
- /membrane/diag/claude-agent counts ceiling_hits and continues.

The SDK is a stand-in that calls the hooks it was handed, as the real CLI
does (PreToolUse after the assistant message that carries the tool_use).
"""
import json
import os
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import server as MS  # noqa: E402
from tests.test_claude_agent_384 import key_fixture  # noqa: E402
from tests.test_claude_agent_continue_389 import ResultMessage  # noqa: E402
from tests.test_claude_agent_tools_389 import (  # noqa: E402
    AssistantMessage, StreamEvent, TextBlock, ToolResultBlock, ToolUseBlock,
    UserMessage, fake_sdk as _base_sdk)

STATUS = "Where I am: halfway through the files."


def scripted_sdk(plans, session_id="sess-1"):
    """A stand-in SDK. Each query() call follows the next plan (the last one
    repeats): a list of steps, ("text", words) or ("tools", n) -- n tool
    calls in ONE assistant message. Every call goes through the PreToolUse
    hooks first; a denied call never runs, its result (the hook's reason,
    is_error) goes back to the model, and this well-behaved Claude then
    says STATUS and retries it. A hook that stops the run (continue_ False)
    ends the query."""
    mod = _base_sdk()
    mod.tool_runs = []                 # every tool call that really ran
    mod.seen_by_model = []             # the denial texts Claude got back

    async def _hook(options, tid, args):
        out = {}
        for m in (getattr(options, "hooks", None) or {}).get("PreToolUse", []):
            for h in m.hooks:
                out = await h({"hook_event_name": "PreToolUse", "session_id": session_id,
                               "tool_name": "mcp__membrane__learn", "tool_input": args,
                               "tool_use_id": tid}, tid, {"signal": None}) or {}
                hso = out.get("hookSpecificOutput") or {}
                if hso.get("permissionDecision") == "deny" or out.get("continue_") is False:
                    return out
        return out

    async def query(*, prompt, options=None, transport=None):
        i = len(mod.calls)
        mod.calls.append({"prompt": prompt, "options": options})
        plan = plans[min(i, len(plans) - 1)]
        partial = getattr(options, "include_partial_messages", False)
        n = 0

        def say(words):
            if partial:
                yield StreamEvent({"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "text_delta", "text": words}})
            yield AssistantMessage([TextBlock(words)])

        for step in plan:
            if step[0] == "text":
                for m in say(step[1]):
                    yield m
                continue
            todo = []
            for _ in range(step[1]):
                n += 1
                todo.append((f"toolu_{i}_{n}", {"lesson": f"l{n}"}))
            while todo:
                yield AssistantMessage([ToolUseBlock(tid, "mcp__membrane__learn", a)
                                        for tid, a in todo])
                retry = []
                for tid, a in todo:
                    out = await _hook(options, tid, a)
                    hso = out.get("hookSpecificOutput") or {}
                    if hso.get("permissionDecision") == "deny":
                        why = hso.get("permissionDecisionReason") or ""
                        mod.seen_by_model.append(why)
                        yield UserMessage([ToolResultBlock(tid, why, True)])
                        if out.get("continue_") is False:
                            yield ResultMessage("error_during_execution", session_id)
                            return
                        retry.append((tid + "r", a))
                        continue
                    mod.tool_runs.append(a["lesson"])
                    yield UserMessage([ToolResultBlock(tid, "Learned", False)])
                if retry:
                    for m in say(STATUS):
                        yield m
                todo = retry
        yield ResultMessage("success", session_id)

    mod.query = query
    return mod


class _Case(unittest.TestCase):
    plans = [[("tools", 1), ("text", "done")]]
    ceiling = None      # a class pins a small ceiling to reach it (the real one is 2048)

    def setUp(self):
        self.sdk = scripted_sdk(self.plans)
        self._p = [mock.patch.dict(sys.modules, {"claude_agent_sdk": self.sdk}),
                   mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"),
                   key_fixture()]
        if self.ceiling:
            self._p.append(mock.patch.object(CA, "MAX_TOOL_CALLS_PER_ANSWER", self.ceiling))
        for p in self._p:
            p.start()
        CA.reset_stats()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()

    def _items(self, resume=None, stream=False):
        return list(CA.iter_reply([{"role": "user", "content": "go"}],
                                  "claude-agent:sonnet", stream=stream, port=9,
                                  resume=resume))


# ---- the constants and the options -------------------------------------------

class TestOptions(_Case):
    def test_the_approved_values(self):
        self.assertEqual(CA.MAX_TOOL_CALLS_PER_ANSWER, 2048)
        self.assertFalse(hasattr(CA, "CHECKIN_TOOL_CALLS"))
        self.assertFalse(hasattr(CA, "CHECKIN_MESSAGE"))

    def test_max_turns_never_binds_before_the_ceiling(self):
        self._items()
        kw = self.sdk.calls[0]["options"].kw
        self.assertEqual(kw["max_turns"], CA.MAX_TURNS)
        # every allowed call may cost a turn, and every one may be denied once
        self.assertGreaterEqual(CA.MAX_TURNS, 2 * CA.MAX_TOOL_CALLS_PER_ANSWER)

    def test_one_pre_tool_use_hook_for_every_tool(self):
        self._items()
        hooks = self.sdk.calls[0]["options"].kw["hooks"]
        self.assertEqual(set(hooks), {"PreToolUse"})
        self.assertEqual(len(hooks["PreToolUse"]), 1)
        self.assertIsNone(hooks["PreToolUse"][0].matcher)     # every tool


# ---- no check-ins -----------------------------------------------------------

class TestNoCheckins(_Case):
    plans = [[("tools", 9), ("text", "done")]]

    def test_silent_calls_in_a_row_all_run(self):
        got = self._items()
        self.assertEqual(self.sdk.seen_by_model, [])           # nothing denied
        self.assertEqual(self.sdk.tool_runs, [f"l{n}" for n in range(1, 10)])
        self.assertNotIn(STATUS, "".join(v for k, v in got if k == "text"))
        self.assertNotIn("checkin_denials", CA.stats())
        self.assertEqual(len(self.sdk.calls), 1)               # one continuous run


class TestManySilentMessages(_Case):
    plans = [[("tools", 1)] * 30 + [("tools", 20), ("text", "done")]]

    def test_fifty_silent_calls_run_unrefused(self):
        self._items(stream=True)
        self.assertEqual(self.sdk.seen_by_model, [])
        self.assertEqual(len(self.sdk.tool_runs), 50)


# ---- the ceiling ---------------------------------------------------------------

_TALKATIVE = [s for _ in range(12) for s in (("tools", 5), ("text", "five more done"))]


class TestCeiling(_Case):
    ceiling = 50
    plans = [_TALKATIVE, [("tools", 10), ("text", "done")]]

    def test_the_ceiling_stops_the_run_and_offers_continue(self):
        got = self._items()
        self.assertEqual(len(self.sdk.tool_runs), CA.MAX_TOOL_CALLS_PER_ANSWER)
        self.assertEqual([v for k, v in got if k == "capped"],
                         [{"session": "sess-1", "calls": CA.MAX_TOOL_CALLS_PER_ANSWER}])
        self.assertEqual(CA.stats()["ceiling_hits"], 1)
        self.assertEqual(len(self.sdk.calls), 1)               # no auto-continue
        tools = [v for k, v in got if k == "tool"]
        self.assertEqual(len(tools), CA.MAX_TOOL_CALLS_PER_ANSWER)
        self.assertFalse([t for t in tools if t["is_error"]])

    def test_a_persons_continue_gets_a_fresh_ceiling(self):
        self._items()
        body = json.loads(CA.open_reply({"model": "claude-agent:sonnet", "stream": False,
                                         "messages": [{"role": "user", "content": "go"}],
                                         CA.RESUME_KEY: "sess-1"}, port=9).read())
        self.assertEqual(self.sdk.calls[1]["options"].kw["resume"], "sess-1")
        self.assertEqual(self.sdk.calls[1]["prompt"], CA.CONTINUE_PROMPT)
        self.assertEqual(len(self.sdk.tool_runs), CA.MAX_TOOL_CALLS_PER_ANSWER + 10)
        self.assertNotIn(CA.CAPPED_KEY, body)
        self.assertEqual(CA.stats()["continues"], 1)


class TestBackstop(_Case):
    ceiling = 50
    def test_the_sdk_turn_backstop_still_marks_the_cap(self):
        mod = self.sdk

        async def query(*, prompt, options=None, transport=None):
            mod.calls.append({"prompt": prompt, "options": options})
            yield AssistantMessage([TextBlock("still going")])
            yield ResultMessage("error_max_turns", "sess-1")
        mod.query = query
        got = self._items()
        self.assertEqual([v for k, v in got if k == "capped"],
                         [{"session": "sess-1", "calls": CA.MAX_TOOL_CALLS_PER_ANSWER}])
        self.assertEqual(CA.stats()["ceiling_hits"], 1)


# ---- the transcript ------------------------------------------------------------

class TestTranscript(_Case):
    ceiling = 50
    plans = [_TALKATIVE]

    def test_the_capped_row_and_no_error_parts(self):
        def ladder(body):
            return json.loads(CA.open_reply(dict(body, stream=False), port=9).read())
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a: "ok", system="", model="claude-agent:sonnet")
        sid = r.sessions.create(title="t")["id"]
        r.sessions.sessions[sid]["agent"] = "build"
        r.run(sid, "do the long thing")
        asst = [x for x in r.messages.list(sid) if x["info"]["role"] == "assistant"][-1]
        self.assertEqual(asst["info"][CA.CAP_MARK],
                         {"session": "sess-1", "calls": CA.MAX_TOOL_CALLS_PER_ANSWER,
                          "continued": False})
        self.assertFalse(asst["info"].get("error"))
        tparts = [p for p in asst["parts"] if p.get("type") == "tool"
                  and p.get(CA.MARK_KEY) == CA.PROVIDER]
        self.assertEqual(len(tparts), CA.MAX_TOOL_CALLS_PER_ANSWER)
        self.assertFalse([p for p in tparts if p["state"]["status"] == "error"])


class TestTranscriptSilentCalls(_Case):
    plans = [[("tools", 8), ("text", "done")]]

    def test_silent_calls_are_plain_tool_parts(self):
        def ladder(body):
            return json.loads(CA.open_reply(dict(body, stream=False), port=9).read())
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a: "ok", system="", model="claude-agent:sonnet")
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, "go")
        asst = [x for x in r.messages.list(sid) if x["info"]["role"] == "assistant"][-1]
        tparts = [p for p in asst["parts"] if p.get("type") == "tool"
                  and p.get(CA.MARK_KEY) == CA.PROVIDER]
        self.assertEqual(len(tparts), 8)
        self.assertFalse([p for p in tparts if p["state"]["status"] == "error"])
        self.assertNotIn(CA.CAP_MARK, asst["info"])
        self.assertNotIn(STATUS, json.dumps(asst["parts"]))


# ---- diagnostics ---------------------------------------------------------------

class TestDiag(_Case):
    plans = [[("tools", 8), ("text", "done")]]

    def test_diag_reports_the_counters(self):
        from membrane import diag as D
        self._items()
        sent = {}
        h = types.SimpleNamespace(path="/membrane/diag/claude-agent", command="GET",
                                  headers={}, client_address=("127.0.0.1", 1))
        with mock.patch.object(D, "_send", lambda hh, code, obj: sent.update(code=code, obj=obj)), \
                mock.patch("membrane.seatauth.is_loopback", lambda hh: True):
            self.assertTrue(D.route(h, "/membrane/diag/claude-agent"))
        o = sent["obj"]
        self.assertNotIn("checkin_denials", o)
        self.assertNotIn("checkin_tool_calls", o)
        self.assertEqual(o["ceiling_hits"], 0)
        self.assertEqual(o["continues"], 0)
        self.assertEqual(o["max_tool_calls"], CA.MAX_TOOL_CALLS_PER_ANSWER)


# ---- the chat's line (panel.js) ------------------------------------------------

_PANEL = Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js"


@unittest.skipIf(not shutil.which("node"), "node not installed")
class TestChatLine(unittest.TestCase):
    def test_the_ceiling_says_calls(self):
        info = {"id": "msg_1", CA.CAP_MARK: {"session": "s", "calls": 50, "continued": False}}
        js = ("const M=require(%r); process.stdout.write(JSON.stringify(M.capHtml(%s)))"
              % (str(_PANEL), json.dumps(info)))
        html = json.loads(subprocess.check_output(["node", "-e", js]))
        self.assertIn("tool-call ceiling (50 calls)", html)
        self.assertIn("Continue (50 more calls)", html)
        self.assertIn('data-ccont="msg_1"', html)


if __name__ == "__main__":
    unittest.main()
