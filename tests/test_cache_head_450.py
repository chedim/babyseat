"""#450: the claude-agent rung's stable cached head.

Every answer used to send its whole prompt as one text block, so nothing an
answer wrote to the prompt cache could be read by the next one. Now the
conversation goes as one content block per message with a cache breakpoint
on the last earlier message (the head); the per-turn blocks (banner, recall)
and the latest message follow it (the tail). The next answer's prompt starts
with the same blocks, so the cache's look-back finds the head written by the
answer before and reads it. The serve window advances in steps of
HISTORY_ANCHOR_STEP messages, so the head's first block stays put for several
answers. The bundled CLI keeps a block's cache_control and adds its own, 4 in
all (the API's maximum, measured by capture): an answer the API refuses for
its breakpoint is sent again as today's plain prompt, and the head stays off
until the seat restarts."""
import asyncio
import os
import sys
import tempfile
import unittest
from collections.abc import AsyncIterable
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import rag as RAG  # noqa: E402
from membrane import server as SV  # noqa: E402

import test_claude_agent_384 as T384  # noqa: E402  (the stubbed SDK)


def convo(n_pairs, ask, banner):
    msgs = [{"role": "system", "content": "DOCTRINE: be brief."}]
    for i in range(n_pairs):
        msgs += [{"role": "user", "content": f"ask {i}"},
                 {"role": "assistant", "content": f"reply {i}"}]
    msgs.append(RAG.Injected(role="system", content=banner))
    msgs.append({"role": "user", "content": ask})
    return msgs


def texts(blocks):
    return [b["text"] for b in blocks]


class TestBlocks(unittest.TestCase):
    def setUp(self):
        CA._HEAD_REFUSED.clear()
        self.addCleanup(CA._HEAD_REFUSED.clear)

    def test_the_blocks_say_what_the_prompt_says(self):
        msgs = convo(3, "and now?", "BANNER one")
        system, prompt = CA.split_messages(msgs)
        system2, blocks = CA.prompt_blocks(msgs)
        self.assertEqual(system2, system)
        self.assertEqual("".join(texts(blocks)), prompt)

    def test_one_block_per_earlier_message_and_the_breakpoint_on_the_last(self):
        _s, blocks = CA.prompt_blocks(convo(3, "and now?", "BANNER one"))
        marked = [i for i, b in enumerate(blocks) if b.get("cache_control")]
        self.assertEqual(marked, [len(blocks) - 2])               # the head's last block
        self.assertEqual(blocks[marked[0]]["cache_control"], {"type": "ephemeral"})
        self.assertIn("BANNER one", blocks[-1]["text"])           # the tail: banner + ask
        self.assertIn("and now?", blocks[-1]["text"])
        self.assertTrue(blocks[-2]["text"].startswith("[assistant]\nreply 2"))

    def test_the_next_answer_starts_with_the_same_blocks(self):
        _s, a = CA.prompt_blocks(convo(3, "and now?", "BANNER one"))
        nxt = convo(3, "and now?", "BANNER two")[:-2] + [
            {"role": "user", "content": "and now?"},
            {"role": "assistant", "content": "this"},
            RAG.Injected(role="system", content="BANNER two"),
            {"role": "user", "content": "next?"}]
        _s, b = CA.prompt_blocks(nxt)
        head_a = [{k: v for k, v in x.items() if k != "cache_control"} for x in a[:-1]]
        head_b = [{k: v for k, v in x.items() if k != "cache_control"} for x in b[:len(head_a)]]
        self.assertEqual(head_a, head_b)

    def test_a_lone_message_has_no_head(self):
        _s, blocks = CA.prompt_blocks([{"role": "user", "content": "hi"}])
        self.assertIsNone(blocks)

    def test_the_switch_turns_it_off(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_CLAUDE_CACHE_HEAD": "0"}):
            self.assertIsNone(CA.prompt_blocks(convo(3, "x", "B"))[1])


class _SDKCase(unittest.TestCase):
    def sdk_with(self, fail_blocks=False):
        sdk = T384.fake_sdk()
        sdk.prompts = []
        real = sdk.query

        async def query(*, prompt, options=None, transport=None):
            if isinstance(prompt, AsyncIterable):
                got = [m async for m in prompt]
                sdk.prompts.append(got)
                if fail_blocks:
                    raise RuntimeError("claude-agent: messages: A maximum of 4 blocks "
                                       "with cache_control may be provided. Found 5.")
            else:
                sdk.prompts.append(prompt)
            async for m in real(prompt="x", options=options, transport=transport):
                yield m
        sdk.query = query
        return sdk

    def answer(self, sdk, msgs):
        with mock.patch.object(CA, "load_sdk", lambda: sdk), \
                mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"), \
                T384.key_fixture():
            return list(CA.iter_reply(msgs, "claude-agent:haiku", stream=False, port=9))

    def setUp(self):
        CA._HEAD_REFUSED.clear()
        self.addCleanup(CA._HEAD_REFUSED.clear)
        CA.reset_stats()
        self.addCleanup(CA.reset_stats)


class TestTheCall(_SDKCase):
    def test_a_conversation_goes_as_blocks(self):
        sdk = self.sdk_with()
        out = self.answer(sdk, convo(3, "and now?", "B"))
        self.assertIn("Hello world", "".join(v for k, v in out if k == "text"))
        msg = sdk.prompts[0][0]
        self.assertEqual(msg["type"], "user")
        self.assertIsInstance(msg["message"]["content"], list)
        self.assertTrue(any(b.get("cache_control") for b in msg["message"]["content"]))

    def test_a_lone_message_goes_as_text(self):
        sdk = self.sdk_with()
        self.answer(sdk, [{"role": "user", "content": "hi"}])
        self.assertEqual(sdk.prompts, ["hi"])

    def test_a_refused_breakpoint_is_sent_again_as_text_and_the_head_stays_off(self):
        sdk = self.sdk_with(fail_blocks=True)
        out = self.answer(sdk, convo(3, "and now?", "B"))
        self.assertIn("Hello world", "".join(v for k, v in out if k == "text"))
        self.assertIsInstance(sdk.prompts[1], str)                  # the retry
        self.answer(sdk, convo(3, "and now?", "B"))
        self.assertIsInstance(sdk.prompts[2], str)                  # stays off
        self.assertEqual(CA.stats()["head_refused"], 1)

    def test_the_prompt_shape_is_measured(self):
        sdk = self.sdk_with()
        self.answer(sdk, convo(3, "and now?", "BANNER"))
        self.answer(sdk, convo(3, "and now?", "BANNER"))
        p = CA.usage_stats()["prompt"]
        self.assertEqual(p["answers"], 2)
        self.assertGreater(p["per_answer"]["head_chars"], 0)
        self.assertGreater(p["per_answer"]["tail_chars"], 0)
        self.assertGreater(p["per_answer"]["system_chars"], 0)
        self.assertEqual(p["system_changes"], 0)                  # the same system twice


class TestWindowSteps(unittest.TestCase):
    def runner(self, n):
        d = tempfile.mkdtemp()
        ss = SV.SessionStore(str(Path(d) / "s.json"))
        ms = SV.MessageStore(str(Path(d) / "m.json"))
        for i in range(n):
            ms.append("ses_t", {"id": f"msg_{i}", "role": "user" if i % 2 == 0 else "assistant",
                                "time": {"created": i}, "sessionID": "ses_t"},
                      [{"id": f"p{i}", "type": "text", "text": f"m{i}"}])
        return SV.TurnRunner(SV.SSEHub(), ss, ms, None, None, system="", model="glm")

    def window(self, n):
        return [m["content"] for m in self.runner(n)._prior_history("ses_t", "")]

    def test_the_window_starts_on_a_step(self):
        step, cap = SV.history_anchor_step(), SV.history_max_messages()
        for n in range(cap, cap + 3 * step):
            w = self.window(n)
            first = int(w[0][1:])
            self.assertEqual(first % step, 0, (n, w[0]))
            self.assertLessEqual(len(w), cap)
            self.assertGreater(len(w), cap - step)
            self.assertEqual(w[-1], f"m{n - 1}")                  # always up to now

    def test_the_start_holds_while_the_chat_grows_within_a_step(self):
        step = SV.history_anchor_step()
        n0 = SV.history_max_messages() + 1                         # just past a step
        firsts = {self.window(n)[0] for n in range(n0, n0 + step)}
        self.assertEqual(len(firsts), 1)

    def test_a_short_chat_is_all_in(self):
        self.assertEqual(self.window(5), [f"m{i}" for i in range(5)])

    def test_the_env_overrides(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_HISTORY_ANCHOR": "1",
                                          "MEMBRANE_HISTORY_MAX": "6"}):
            self.assertEqual(self.window(20), [f"m{i}" for i in range(14, 20)])

    def test_the_defaults(self):
        self.assertEqual(SV.history_anchor_step(), 4)
        self.assertEqual(SV.history_max_messages(), 12)


if __name__ == "__main__":
    unittest.main()
