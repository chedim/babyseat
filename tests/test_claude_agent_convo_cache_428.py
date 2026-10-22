"""THE CLAUDE-AGENT RUNG CACHES THE CONVERSATION (issue #428).

After #426 the system prompt was stable, but every call rendered the whole
conversation as one new user text, so nothing past the system prompt was
ever read from Anthropic's prompt cache. Now one SDK session carries a
conversation: a turn that continues what that session already holds
RESUMES it and sends only the per-turn tail (membrane's per-turn context +
the latest message). The CLI replays the session's turns as real messages
and puts its cache breakpoint at the end of each request, so the next call
reads every earlier turn from cache.

membrane stays the source of truth: a turn resumes only when the
conversation it sends continues the session's (the serve-mode chat's
window may have slid and capped long texts); a regenerate, an edited or
foreign answer, an unknown conversation or a lost SDK session falls back
to the full rendering in a fresh session.

The SDK is a deterministic fake that keeps sessions the way the CLI does
and records what each call would put on the wire; real cache reads and
writes are measured live (/membrane/diag/claude-agent).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import rag as RAG  # noqa: E402

import test_claude_agent_384 as T384  # noqa: E402  (the stubbed SDK)

DOCTRINE = "DOCTRINE: be brief."
MODEL = "claude-agent:haiku"


class ResultMessage:
    def __init__(self, session_id, subtype="success"):
        self.subtype = subtype
        self.is_error = False
        self.result = None
        self.session_id = session_id
        self.usage = {"input_tokens": 5, "output_tokens": 2}


def session_sdk():
    """A fake SDK that keeps sessions like the CLI: a call without `resume`
    opens a new session; `resume` replays the session's turns and appends
    the new prompt (an unknown id fails, as a lost session does). Each call
    records the wire request: the system prompt + the messages in order."""
    sdk = T384.fake_sdk()
    sdk.sessions, sdk.wire = {}, []

    async def query(*, prompt, options=None, transport=None):
        sdk.calls.append({"prompt": prompt, "options": options})
        n = len(sdk.calls)
        resume = getattr(options, "resume", None)
        if resume:
            if resume not in sdk.sessions:
                raise RuntimeError(f"No conversation found with session ID: {resume}")
            sid = resume
        else:
            sid = f"sess-{n}"
            sdk.sessions[sid] = []
        hist = sdk.sessions[sid]
        hist.append({"role": "user", "content": prompt})
        sdk.wire.append({"system": options.system_prompt,
                         "messages": [dict(m) for m in hist]})
        text = f"answer number {n}."
        hist.append({"role": "assistant", "content": text})
        if getattr(options, "include_partial_messages", False):
            yield T384._delta(text)
        msg = T384.AssistantMessage([T384.TextBlock(text)])
        msg.session_id = sid
        yield msg
        yield ResultMessage(sid)
    sdk.query = query
    return sdk


def turn(history, latest, recall=None):
    """A turn as membrane builds it: the client's system prompt, the
    history, the latest user message, and (optionally) a per-turn recall
    block placed by rag._inject."""
    msgs = [{"role": "system", "content": DOCTRINE}, *history,
            {"role": "user", "content": latest}]
    if recall:
        with mock.patch.object(RAG._inject, "doctrine_delivered", True, create=True):
            msgs = RAG._inject(msgs, recall)
    return msgs


class _Case(unittest.TestCase):
    stream = False

    def setUp(self):
        self.sdk = session_sdk()
        p = T384.key_fixture()
        p.start()
        self.addCleanup(p.stop)
        CA.reset_sessions()
        self.addCleanup(CA.reset_sessions)

    def ask(self, history, latest, recall=None):
        items = list(CA.iter_reply(turn(history, latest, recall), MODEL,
                                   stream=self.stream, sdk=self.sdk, port=9))
        return "".join(v for k, v in items if k == "text")

    def chat(self, n, recall=True):
        """n turns of one conversation, each with a fresh recall block;
        returns the history after the last answer."""
        hist = []
        for i in range(n):
            q = f"question {i}"
            a = self.ask(hist, q, f"Recalled memories: fact {i}" if recall else None)
            hist += [{"role": "user", "content": q},
                     {"role": "assistant", "content": a}]
        return hist

    def resumes(self):
        return [getattr(c["options"], "resume", None) for c in self.sdk.calls]


class TestResumeSendsOnlyTheTail(_Case):
    def test_the_first_turn_opens_a_session(self):
        self.ask([], "question 0", "Recalled memories: fact 0")
        self.assertEqual(self.resumes(), [None])

    def test_the_next_turn_resumes_it_with_only_the_tail(self):
        self.chat(1)
        self.ask(self.chat_hist(), "question 1", "Recalled memories: fact 1")
        self.assertEqual(self.resumes(), [None, "sess-1"])
        tail = self.sdk.calls[1]["prompt"]
        self.assertIn("fact 1", tail)                    # the per-turn context
        self.assertIn(CA.CONTEXT_OPEN, tail)
        self.assertTrue(tail.rstrip().endswith("question 1"), tail)
        self.assertLess(tail.index("fact 1"), tail.index("question 1"))
        # the earlier turns are the session's, never re-sent
        self.assertNotIn("question 0", tail)
        self.assertNotIn("answer number 1", tail)

    def chat_hist(self):
        return [{"role": "user", "content": "question 0"},
                {"role": "assistant", "content": "answer number 1."}]

    def test_a_tail_without_per_turn_context_is_the_message_itself(self):
        self.chat(1, recall=False)
        self.ask(self.chat_hist(), "question 1")
        self.assertEqual(self.sdk.calls[1]["prompt"], "question 1")

    def test_the_footer_stays_after_the_latest_message(self):
        self.chat(1)
        msgs = RAG._inject_after(turn(self.chat_hist(), "question 1",
                                      "Recalled memories: fact 1"),
                                 "[membrane-says] hello")
        list(CA.iter_reply(msgs, MODEL, stream=False, sdk=self.sdk, port=9))
        tail = self.sdk.calls[1]["prompt"]
        self.assertEqual(self.resumes(), [None, "sess-1"])
        self.assertLess(tail.index("question 1"), tail.index("[membrane-says] hello"))

    def test_one_session_carries_a_long_conversation(self):
        self.chat(6)
        self.assertEqual(self.resumes(), [None] + ["sess-1"] * 5)


class TestPrefixStability(_Case):
    """What the cache needs: every call's wire request starts with the
    previous call's request plus its answer, byte for byte, and the system
    prompt never changes -- so only the new tail is uncached."""

    def test_each_request_extends_the_last_one(self):
        self.chat(5)
        wire = self.sdk.wire
        self.assertEqual(len({w["system"] for w in wire}), 1)
        self.assertEqual(wire[0]["system"], DOCTRINE)
        for prev, cur in zip(wire, wire[1:]):
            n = len(prev["messages"])
            self.assertEqual(cur["messages"][:n], prev["messages"])
            self.assertEqual(cur["messages"][n]["role"], "assistant")
            self.assertEqual(len(cur["messages"]), n + 2)  # answer + the new tail

    def test_the_recall_of_one_turn_never_rewrites_an_earlier_one(self):
        self.chat(3)
        first = self.sdk.wire[0]["messages"][0]["content"]
        self.assertIn("fact 0", first)
        self.assertEqual(self.sdk.wire[2]["messages"][0]["content"], first)


class TestPrefixStabilityStreamed(TestPrefixStability):
    stream = True


class TestFallsBackToTheFullRendering(_Case):
    def test_an_edited_answer_starts_a_fresh_session(self):
        hist = self.chat(2)
        hist[-1] = {"role": "assistant", "content": "an answer membrane never got"}
        self.ask(hist, "question 2")
        self.assertIsNone(self.resumes()[-1])
        full = self.sdk.calls[-1]["prompt"]
        self.assertIn("question 0", full)
        self.assertIn("an answer membrane never got", full)

    def test_an_answer_from_another_rung_starts_a_fresh_session(self):
        hist = self.chat(1) + [{"role": "user", "content": "question 1"},
                               {"role": "assistant", "content": "a local model's answer"}]
        self.ask(hist, "question 2")
        self.assertIsNone(self.resumes()[-1])

    def test_a_regenerate_never_resumes_a_session_that_moved_on(self):
        self.chat(1)
        hist = [{"role": "user", "content": "question 0"},
                {"role": "assistant", "content": "answer number 1."}]
        self.ask(hist, "question 1")
        self.ask(hist, "question 1 (again)")          # the same prefix, once more
        self.assertEqual(self.resumes(), [None, "sess-1", None])

    def test_a_lost_session_falls_back_within_the_same_answer(self):
        hist = self.chat(1)
        self.sdk.sessions.clear()                     # the CLI's session is gone
        a = self.ask(hist, "question 1", "Recalled memories: fact 1")
        self.assertTrue(a)
        self.assertEqual(self.resumes(), [None, "sess-1", None])
        full = self.sdk.calls[-1]["prompt"]
        self.assertIn("question 0", full)
        self.assertIn("fact 1", full)
        # and the fresh session carries the conversation on
        self.ask(hist + [{"role": "user", "content": "question 1"},
                         {"role": "assistant", "content": a}], "question 2")
        self.assertEqual(self.resumes()[-1], "sess-3")

    def test_a_conversation_ending_on_a_tool_result_renders_in_full(self):
        hist = self.chat(1) + [{"role": "user", "content": "question 1"}]
        msgs = turn(hist[:-1], "question 1") + [
            {"role": "tool", "name": "read", "content": "file body"}]
        list(CA.iter_reply(msgs, MODEL, stream=False, sdk=self.sdk, port=9))
        self.assertIsNone(self.resumes()[-1])

    def test_two_conversations_keep_their_own_sessions(self):
        a1 = self.ask([], "alpha 0")
        b1 = self.ask([], "beta 0")
        self.ask([{"role": "user", "content": "alpha 0"},
                  {"role": "assistant", "content": a1}], "alpha 1")
        self.ask([{"role": "user", "content": "beta 0"},
                  {"role": "assistant", "content": b1}], "beta 1")
        self.assertEqual(self.resumes(), [None, None, "sess-1", "sess-2"])

    def test_reset_forgets_every_session(self):
        hist = self.chat(1)
        CA.reset_sessions()
        self.ask(hist, "question 1")
        self.assertIsNone(self.resumes()[-1])


class TestTheChatsWindow(_Case):
    """The serve-mode chat replays a sliding window of capped texts
    (server.HISTORY_MAX_MESSAGES, HISTORY_TEXT_CAP): a window of the
    session's own conversation still resumes it."""

    def test_a_slid_window_still_resumes(self):
        hist = self.chat(4)
        self.ask(hist[3:], "question 4")              # the oldest rows dropped
        self.assertEqual(self.resumes()[-1], "sess-1")
        self.assertEqual(self.sdk.calls[-1]["prompt"], "question 4")

    def test_a_capped_long_text_still_resumes(self):
        hist = self.chat(1)
        long_q = "word " * 2000
        a = self.ask(hist, long_q)
        hist += [{"role": "user", "content": long_q[:4000].strip()},
                 {"role": "assistant", "content": a}]
        self.ask(hist, "question 2")
        self.assertEqual(self.resumes(), [None, "sess-1", "sess-1"])

    def test_a_window_that_is_not_a_suffix_does_not_resume(self):
        hist = self.chat(3)
        self.ask(hist[:2] + hist[4:], "question 3")    # a turn cut from the middle
        self.assertIsNone(self.resumes()[-1])


class TestCappedAndContinue(_Case):
    def test_a_capped_answer_is_not_resumed_by_the_next_message(self):
        hist = self.chat(1)
        real = self.sdk.query

        async def capped(*, prompt, options=None, transport=None):
            async for m in real(prompt=prompt, options=options, transport=transport):
                if isinstance(m, ResultMessage):
                    m.subtype = CA.MAX_TURNS_SUBTYPE
                yield m
        self.sdk.query = capped
        a = self.ask(hist, "question 1")
        self.sdk.query = real
        self.ask(hist + [{"role": "user", "content": "question 1"},
                         {"role": "assistant", "content": a}], "question 2")
        self.assertEqual(self.resumes(), [None, "sess-1", None])

    def test_a_continue_still_resumes_with_the_continue_prompt(self):
        self.chat(1)
        msgs = turn([{"role": "user", "content": "question 0"},
                     {"role": "assistant", "content": "answer number 1."}], "Continue")
        list(CA.iter_reply(msgs, MODEL, stream=False, sdk=self.sdk, port=9,
                           resume="sess-1"))
        self.assertEqual(self.resumes()[-1], "sess-1")
        self.assertEqual(self.sdk.calls[-1]["prompt"], CA.CONTINUE_PROMPT)


class TestDiag(_Case):
    def test_diag_reports_resumed_full_and_lost(self):
        import types
        from membrane import diag as D
        hist = self.chat(2)                            # one full, one resumed
        self.sdk.sessions.clear()
        self.ask(hist, "question 2")                   # lost -> full
        sent = {}
        h = types.SimpleNamespace(path="/membrane/diag/claude-agent", command="GET",
                                  headers={}, client_address=("127.0.0.1", 1))
        with mock.patch.object(D, "_send", lambda hh, code, obj: sent.update(obj=obj)), \
                mock.patch("membrane.seatauth.is_loopback", lambda hh: True):
            self.assertTrue(D.route(h, "/membrane/diag/claude-agent"))
        c = sent["obj"]["conversation"]
        self.assertEqual((c["resumed"], c["full"], c["lost"]), (1, 2, 1))
        self.assertEqual(c["sessions"], 1)
        self.assertEqual(c["sessions_max"], CA.CONVO_SESSIONS_MAX)


if __name__ == "__main__":
    unittest.main()
