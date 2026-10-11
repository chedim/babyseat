"""THE SILENT ZOMBIE (2026-10-10): a client that fell behind a streamed
reply was unsubscribed while its connection idled on keepalives forever --
no reply ever closed for it, so none was voiced. Deltas are droppable now
(the part's closing update carries the text); anything else lost closes
the stream so the client resyncs."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.server import SSEHub  # noqa: E402


class TestHub(unittest.TestCase):
    def _drain(self, q):
        out = []
        while not q.empty():
            out.append(q.get_nowait()["type"])
        return out

    def test_a_slow_client_loses_deltas_never_the_parts_that_close(self):
        h = SSEHub()
        q = h.subscribe()
        for i in range(SSEHub.QUEUE_MAX * 2):
            h.publish({"type": "message.part.delta", "properties": {"delta": "x"}})
        h.publish({"type": "message.part.updated", "properties": {"part": {"time": {"end": 1}}}})
        got = self._drain(q)
        self.assertEqual(got[-1], "message.part.updated")
        self.assertFalse(q.lost)
        self.assertGreater(q.dropped, 0)
        h.publish({"type": "session.idle", "properties": {}})
        self.assertEqual(self._drain(q), ["session.idle"])           # still subscribed

    def test_losing_a_structural_event_hangs_up_instead_of_idling(self):
        h = SSEHub()
        q = h.subscribe()
        for i in range(SSEHub.QUEUE_MAX + 5):
            h.publish({"type": "message.updated", "properties": {}})
        self.assertTrue(q.lost)                                      # the handler returns; the client resyncs
        self.assertNotIn(q, h._subs)


if __name__ == "__main__":
    unittest.main()
