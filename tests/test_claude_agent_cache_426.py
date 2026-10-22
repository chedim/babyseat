"""THE CLAUDE-AGENT RUNG'S PROMPT CACHE (issue #426).

- the cache split is kept: Claude's usage (uncached input, cache reads,
  cache writes, output) rides the OpenAI-shaped usage
  (prompt_tokens_details.cached_tokens / cache_write_tokens) with the
  same totals as before, lands on the stored message tokens
  (tokens.cache.read / write), and sums since boot (plus per-call
  averages) in GET /membrane/diag/claude-agent;
- cache-friendly order: the stable system messages (the doctrine, the
  client's instructions) are the SDK's system prompt; membrane's per-turn
  blocks (recall, where-we-are, banner, anything rag._inject /
  rag._inject_after placed) move to the END of the prompt -- after the
  conversation, right before the latest message, delimited -- so the
  system prompt is byte-identical across calls whose recall differs.
"""
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import rag as RAG  # noqa: E402
from membrane import server as MS  # noqa: E402

import test_claude_agent_384 as T384  # noqa: E402  (the stubbed SDK)

CACHED = {"input_tokens": 120, "cache_read_input_tokens": 9000,
          "cache_creation_input_tokens": 700, "output_tokens": 40}


def cached_sdk(usage=None):
    """The stubbed SDK whose ResultMessage carries a cache split."""
    sdk = T384.fake_sdk()
    real_query = sdk.query

    async def query(*, prompt, options=None, transport=None):
        async for m in real_query(prompt=prompt, options=options, transport=transport):
            if type(m).__name__ == "ResultMessage":
                m = T384.ResultMessage(usage=dict(usage or CACHED))
            yield m
    sdk.query = query
    return sdk


class _Case(unittest.TestCase):
    def setUp(self):
        self.sdk = cached_sdk()
        patches = [mock.patch.object(CA, "load_sdk", lambda: self.sdk),
                   mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"),
                   T384.key_fixture()]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        CA.reset_stats()
        self.addCleanup(CA.reset_stats)


# ---- 1. the cache split ------------------------------------------------------

class TestUsageSplit(_Case):
    def test_usage_keeps_the_split_and_the_old_totals(self):
        u = CA._usage(CACHED)
        self.assertEqual(u["prompt_tokens"], 120 + 9000 + 700)      # as before
        self.assertEqual(u["completion_tokens"], 40)
        self.assertEqual(u["total_tokens"], 120 + 9000 + 700 + 40)
        self.assertEqual(u["prompt_tokens_details"]["cached_tokens"], 9000)
        self.assertEqual(u["prompt_tokens_details"]["cache_write_tokens"], 700)

    def test_buffered_reply_split_lands_on_the_stored_tokens(self):
        body = json.loads(CA.JSONReply([{"role": "user", "content": "hi"}],
                                       "claude-agent:haiku", self.sdk).read())
        a_info = {}
        MS._tokens_fill(a_info, body)
        t = a_info["tokens"]
        self.assertEqual(t["input"], 9820)
        self.assertEqual(t["output"], 40)
        self.assertEqual(t["cache"], {"read": 9000, "write": 700})

    def test_streamed_reply_split_rides_the_last_chunk(self):
        chunks = [json.loads(ln[5:]) for ln in b"".join(
                      CA.SSEReply([{"role": "user", "content": "hi"}],
                                  "claude-agent:haiku", self.sdk)).split(b"\n")
                  if ln.startswith(b"data:") and ln[5:].strip() != b"[DONE]"]
        usage = [c["usage"] for c in chunks if c.get("usage")][-1]
        a_info = {}
        MS._tokens_fill(a_info, {"usage": usage})
        self.assertEqual(a_info["tokens"]["cache"], {"read": 9000, "write": 700})

    def test_two_rounds_sum_on_the_stored_tokens(self):
        a_info = {}
        for _ in range(2):
            MS._tokens_fill(a_info, {"usage": CA._usage(CACHED)})
        self.assertEqual(a_info["tokens"]["cache"], {"read": 18000, "write": 1400})

    def test_diag_sums_since_boot_and_averages_per_call(self):
        from membrane import diag as D
        for _ in range(2):
            list(CA.iter_reply([{"role": "user", "content": "go"}],
                               "claude-agent:haiku", stream=False, port=9))
        sent = {}
        h = types.SimpleNamespace(path="/membrane/diag/claude-agent", command="GET",
                                  headers={}, client_address=("127.0.0.1", 1))
        with mock.patch.object(D, "_send", lambda hh, code, obj: sent.update(code=code, obj=obj)), \
                mock.patch("membrane.seatauth.is_loopback", lambda hh: True):
            self.assertTrue(D.route(h, "/membrane/diag/claude-agent"))
        u = sent["obj"]["usage"]
        self.assertEqual(u["calls"], 2)
        self.assertEqual(u["input"], 240)            # uncached
        self.assertEqual(u["cache_read"], 18000)
        self.assertEqual(u["cache_write"], 1400)
        self.assertEqual(u["output"], 80)
        avg = u["per_call"]
        self.assertEqual(avg["input"], 120)
        self.assertEqual(avg["cache_read"], 9000)
        self.assertEqual(avg["cache_write"], 700)
        self.assertEqual(avg["output"], 40)
        # the cap counters are still there
        self.assertIn("cap_hits", sent["obj"])

    def test_reset_clears_the_usage_sums(self):
        list(CA.iter_reply([{"role": "user", "content": "go"}],
                           "claude-agent:haiku", stream=False, port=9))
        self.assertEqual(CA.usage_stats()["calls"], 1)
        CA.reset_stats()
        self.assertEqual(CA.usage_stats()["calls"], 0)
        self.assertEqual(CA.usage_stats()["per_call"]["input"], 0)


# ---- 2. the cache-friendly order --------------------------------------------

DOCTRINE = "DOCTRINE: be brief."


def _turn(recall: str, history=()):
    """A turn as the proxy builds it: the client's system prompt, the
    history, the latest user message, and membrane's recall block placed
    by rag._inject (just before the last user turn)."""
    msgs = [{"role": "system", "content": DOCTRINE}, *history,
            {"role": "user", "content": "what now?"}]
    with mock.patch.object(RAG._inject, "doctrine_delivered", True, create=True):
        return RAG._inject(msgs, recall)


class TestCacheOrder(_Case):
    def test_first_turn_recall_leaves_the_system_prompt(self):
        system, prompt = CA.split_messages(_turn("Recalled memories: the boat is blue"))
        self.assertEqual(system, DOCTRINE)
        self.assertIn("the boat is blue", prompt)
        self.assertTrue(prompt.rstrip().endswith("what now?"), prompt)
        self.assertLess(prompt.index("the boat is blue"), prompt.index("what now?"))

    def test_volatile_blocks_land_after_the_conversation(self):
        history = [{"role": "user", "content": "first question"},
                   {"role": "assistant", "content": "first answer"}]
        system, prompt = CA.split_messages(
            _turn("WHERE WE ARE: mid-refactor", history))
        self.assertEqual(system, DOCTRINE)
        i_hist = prompt.index("first answer")
        i_ctx = prompt.index("WHERE WE ARE: mid-refactor")
        i_last = prompt.index("what now?")
        self.assertLess(i_hist, i_ctx)
        self.assertLess(i_ctx, i_last)
        # delimited: the block sits inside membrane's context marks
        self.assertIn(CA.CONTEXT_OPEN, prompt)
        self.assertIn(CA.CONTEXT_CLOSE, prompt)
        self.assertLess(prompt.index(CA.CONTEXT_OPEN), i_ctx)
        self.assertLess(i_ctx, prompt.index(CA.CONTEXT_CLOSE))
        self.assertLess(prompt.index(CA.CONTEXT_CLOSE), i_last)

    def test_a_mid_conversation_system_message_is_volatile(self):
        msgs = [{"role": "system", "content": DOCTRINE},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "system", "content": "banner: 12:01"},
                {"role": "user", "content": "q2"}]
        system, prompt = CA.split_messages(msgs)
        self.assertEqual(system, DOCTRINE)
        self.assertIn("banner: 12:01", prompt)

    def test_the_speech_footer_stays_after_the_last_message(self):
        msgs = RAG._inject_after(_turn("Recalled memories: x"), "[membrane-says] hello")
        system, prompt = CA.split_messages(msgs)
        self.assertEqual(system, DOCTRINE)
        self.assertLess(prompt.index("what now?"), prompt.index("[membrane-says] hello"))

    def test_an_undelivered_doctrine_prefix_stays_stable(self):
        msgs = [{"role": "user", "content": "hello"}]
        with mock.patch.object(RAG._inject, "doctrine_delivered", False, create=True):
            msgs = RAG._inject(msgs, "Recalled memories: y")
        system, prompt = CA.split_messages(msgs)
        self.assertEqual(system, RAG._RAG_SYSTEM.strip())
        self.assertIn("Recalled memories: y", prompt)
        self.assertNotIn("Recalled memories", system)

    def test_system_prompt_identical_across_calls_with_different_recall(self):
        for recall in ("Recalled memories: the boat is blue",
                       "Recalled memories: the mast is 12m\nWHERE WE ARE: 14:07"):
            list(CA.iter_reply(_turn(recall), "claude-agent:haiku", stream=False, port=9))
        sp = [c["options"].kw["system_prompt"] for c in self.sdk.calls]
        self.assertEqual(len(sp), 2)
        self.assertEqual(sp[0], sp[1])
        self.assertEqual(sp[0], DOCTRINE)
        self.assertIn("the boat is blue", self.sdk.calls[0]["prompt"])
        self.assertIn("the mast is 12m", self.sdk.calls[1]["prompt"])

    def test_no_volatile_block_keeps_the_lone_message_as_is(self):
        system, prompt = CA.split_messages([{"role": "system", "content": DOCTRINE},
                                            {"role": "user", "content": "just this"}])
        self.assertEqual((system, prompt), (DOCTRINE, "just this"))


if __name__ == "__main__":
    unittest.main()
