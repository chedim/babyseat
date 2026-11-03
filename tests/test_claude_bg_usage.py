"""The Claude rung's calls outside a stored chat turn still land on the token tally:
the gap call (#887, language._ask_model) and a buffered answer whose final usage never
came (it is then the sum of its steps, as the streaming reply already reports, #472)."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import bgusage, claude_agent as CA, language as LANG  # noqa: E402

import test_claude_agent_384 as T384  # noqa: E402
import test_claude_agent_steps_450 as T450  # noqa: E402


class GapCallIsCounted(unittest.TestCase):
    def test_the_gap_call_records_its_usage(self):
        got = []
        usage = {"prompt_tokens": 15200, "completion_tokens": 40, "total_tokens": 15240}
        with mock.patch.object(CA, "iter_reply", lambda *a, **k: iter([("text", "x: none"), ("usage", usage)])), \
                mock.patch.object(bgusage, "record", lambda *a, **k: got.append((a, k))):
            text, u = LANG._ask_model("prompt")
        self.assertEqual(text, "x: none")
        self.assertEqual(got, [(("gap", LANG.GAP_MODEL), {"input": 15200, "output": 40, "reasoning": 0})])


class BufferedAnswerWithoutResult(unittest.TestCase):
    def test_its_usage_is_the_sum_of_its_steps(self):
        sdk = T384.fake_sdk()

        async def query(*, prompt, options=None, transport=None):
            yield T450.AssistantMessage([], usage=T450.FIRST, message_id="a")
            yield T450.AssistantMessage([], usage=T450.LATER, message_id="b")
            # no ResultMessage: the run ended before its result
        sdk.query = query
        with mock.patch.object(CA, "load_sdk", lambda: sdk), \
                mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"), T384.key_fixture():
            rep = CA.open_reply({"model": "claude-agent:sonnet", "stream": False,
                                 "messages": [{"role": "user", "content": "go"}]}, port=1)
            resp = json.loads(rep.read())
        f, l = T450.FIRST, T450.LATER
        want = sum(f[k] + l[k] for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        self.assertEqual(resp.get("usage", {}).get("prompt_tokens"), want)
        self.assertEqual(resp["usage"]["completion_tokens"], f["output_tokens"] + l["output_tokens"])


if __name__ == "__main__":
    unittest.main()
