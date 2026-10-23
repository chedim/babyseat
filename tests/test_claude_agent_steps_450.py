"""#450: the claude-agent ledger measures per STEP, not only per answer.

One answer is one SDK run of many API round trips (steps). The diag's old
`per_call` divided by answers, so it mixed the fresh prompt an answer writes
to the cache once (its first step: the transcript window, the banner and the
recall, #450's "changing tail") with the few hundred tokens each later step
appends. The SDK reports usage on every AssistantMessage (one API response
can arrive as several messages with the same message_id: counted once), so
the ledger now keeps steps, the first steps' cache writes apart from the
later steps', and per-step averages. `per_call` stays as it was (per answer)
for old readers, and `per_answer` names it."""
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

import test_claude_agent_384 as T384  # noqa: E402  (the stubbed SDK)


class AssistantMessage:
    def __init__(self, content, usage=None, message_id=None):
        self.content, self.usage, self.message_id = content, usage, message_id
        self.model, self.error = "claude-sonnet", None


def U(inp, cr, cw, out):
    return {"input_tokens": inp, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw, "output_tokens": out}


FIRST, LATER = U(2, 15000, 8000, 50), U(1, 23000, 1000, 40)


def stepped_sdk(steps):
    sdk = T384.fake_sdk()

    async def query(*, prompt, options=None, transport=None):
        for m in steps:
            yield m
        yield T384.ResultMessage(usage=U(3, 38000, 9000, 90))
    sdk.query = query
    return sdk


class _Case(unittest.TestCase):
    def run_answer(self, steps):
        sdk = stepped_sdk(steps)
        with mock.patch.object(CA, "load_sdk", lambda: sdk), \
                mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"), \
                T384.key_fixture():
            return list(CA.iter_reply([{"role": "user", "content": "go"}],
                                      "claude-agent:haiku", stream=False, port=9))

    def setUp(self):
        CA.reset_stats()
        self.addCleanup(CA.reset_stats)


class TestSteps(_Case):
    def test_first_step_write_is_apart_from_the_later_steps(self):
        self.run_answer([
            AssistantMessage([T384.TextBlock("looking")], FIRST, "m1"),
            AssistantMessage([], FIRST, "m1"),            # the same response: once
            AssistantMessage([T384.TextBlock("done")], LATER, "m2"),
        ])
        u = CA.usage_stats()
        self.assertEqual(u["calls"], 1)                   # answers, as before
        s = u["steps"]
        self.assertEqual(s["steps"], 2)
        self.assertEqual(s["first"]["cache_write"], 8000)
        self.assertEqual(s["first"]["cache_read"], 15000)
        self.assertEqual(s["later"]["cache_write"], 1000)
        self.assertEqual(s["later"]["cache_read"], 23000)
        self.assertEqual(s["per_step"]["cache_write"], 4500)
        self.assertEqual(s["per_first_step"]["cache_write"], 8000)
        self.assertEqual(s["per_later_step"]["cache_write"], 1000)

    def test_per_call_stays_per_answer_and_per_answer_names_it(self):
        self.run_answer([AssistantMessage([T384.TextBlock("hi")], FIRST, "m1")])
        u = CA.usage_stats()
        self.assertEqual(u["per_call"], u["per_answer"])
        self.assertEqual(u["per_answer"]["cache_write"], 9000)    # the result's sum

    def test_no_step_usage_leaves_the_step_ledger_empty(self):
        self.run_answer([T384.AssistantMessage([T384.TextBlock("hi")])])
        s = CA.usage_stats()["steps"]
        self.assertEqual(s["steps"], 0)
        self.assertEqual(s["per_step"]["cache_write"], 0)

    def test_steps_never_reach_the_reply(self):
        items = self.run_answer([AssistantMessage([T384.TextBlock("hi")], FIRST, "m1")])
        self.assertNotIn("step", [k for k, _v in items])

    def test_reset_clears_the_steps(self):
        self.run_answer([AssistantMessage([T384.TextBlock("hi")], FIRST, "m1")])
        CA.reset_stats()
        self.assertEqual(CA.usage_stats()["steps"]["steps"], 0)


if __name__ == "__main__":
    unittest.main()
