"""#914: a worker stopped at the chunk limit is told the real limit and that
work resumes in a fresh chunk; the 2048 ceiling message is unchanged."""
import asyncio
import unittest
from unittest import mock

from membrane import claude_agent as CA
from membrane import diag_requests as DR


def _stop(lane):
    with mock.patch.object(DR, "current", lambda: {"lane": lane}):
        a = CA._Answer()
    a.calls = a.limit
    out = asyncio.run(a.hook({"tool_use_id": "t1"}))
    return a, out["stopReason"]


class TestChunkMessage(unittest.TestCase):
    def test_worker_stop_names_real_limit_and_the_split(self):   # #1116: no fresh chunk; the task splits
        a, msg = _stop("worker")
        self.assertEqual(a.limit, CA.WORKER_CEILING)
        self.assertIn(str(CA.WORKER_CEILING), msg)
        self.assertNotIn("2048", msg)
        self.assertNotIn("Continue", msg)
        self.assertIn("split", msg)

    def test_true_ceiling_message_unchanged(self):
        a, msg = _stop("chat")
        self.assertEqual(msg, CA.CEILING_MESSAGE)
        self.assertEqual(msg, "This answer reached its ceiling of 2048 tool "
                              "calls. Stop here; the person can press Continue.")


if __name__ == "__main__":
    unittest.main()
