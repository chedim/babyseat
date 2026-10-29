"""#683: one reranker per machine. The seat whose model is ready serves it
(POST /membrane/rerank -> serve); a seat that needs it asks the same-host
seats first and calls the one that serves instead of loading a copy."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import recall_rerank as RR  # noqa: E402


class _M:
    def predict(self, pairs, **kw):
        return [float(len(d)) for _q, d in pairs]


class _Base(unittest.TestCase):
    def setUp(self):
        self._st = (RR._state, RR._model, RR._peer, RR._peers_fn)
        RR._state, RR._model, RR._peer, RR._peers_fn = "idle", None, None, None

    def tearDown(self):
        RR._state, RR._model, RR._peer, RR._peers_fn = self._st


class TestServe(_Base):
    def test_ready_model_answers_with_scores(self):
        RR._state, RR._model = "ready", _M()
        self.assertEqual(RR.serve("q", ["ab", "abc"]), [2.0, 3.0])
        self.assertEqual(RR.serve("", []), [])          # the discovery probe

    def test_no_model_answers_null_and_never_loads(self):
        with mock.patch.object(RR, "_start") as st:
            self.assertIsNone(RR.serve("q", ["d"]))
            self.assertIsNone(RR.serve("", []))
        st.assert_not_called()

    def test_a_seat_served_by_a_peer_does_not_serve(self):
        RR._state, RR._peer = "remote", 9
        self.assertIsNone(RR.serve("q", ["d"]))

    def test_off_by_env_neither_serves_nor_calls(self):
        RR._state, RR._model = "ready", _M()
        with mock.patch.dict("os.environ", {"MEMBRANE_RERANK": "0"}), \
                mock.patch.object(RR, "_post") as post:
            self.assertIsNone(RR.serve("q", ["d"]))
            self.assertIsNone(RR.scores("q", ["d"]))
        post.assert_not_called()


class TestCall(_Base):
    def test_a_serving_peer_is_used_and_no_local_load_starts(self):
        RR._peers_fn = lambda: [{"port": 7001}]
        posts = []

        def post(port, payload, timeout):
            posts.append((port, payload))
            return {"scores": [] if not payload["docs"] else [0.5] * len(payload["docs"])}
        with mock.patch.object(RR, "_post", side_effect=post), \
                mock.patch.object(RR, "_installed", return_value=True), \
                mock.patch.dict("sys.modules", {"sentence_transformers": None}):
            RR._load()                                   # the load thread's discovery
            self.assertEqual((RR._state, RR._peer), ("remote", 7001))
            self.assertEqual(RR.scores("q", ["a", "b"]), [0.5, 0.5])
        self.assertEqual(posts[-1][0], 7001)

    def test_first_peer_that_is_ready_wins(self):
        RR._peers_fn = lambda: [{"port": 1}, {"port": 2}, {"port": 3}]
        ans = {1: {"scores": None}, 2: {"scores": []}, 3: {"scores": []}}
        with mock.patch.object(RR, "_post", side_effect=lambda p, pl, t: ans[p]):
            self.assertEqual(RR._find_peer(), 2)

    def test_peer_down_returns_none_without_loading_then_retries_discovery(self):
        RR._state, RR._peer = "remote", 7001
        with mock.patch.object(RR, "_post", side_effect=OSError("down")), \
                mock.patch.object(RR, "_installed", return_value=True), \
                mock.patch.object(RR, "_start") as st:
            self.assertIsNone(RR.scores("q", ["d"]))
            st.assert_not_called()                       # this call: no load, no wait
            self.assertEqual(RR._state, "idle")
            self.assertIsNone(RR.scores("q", ["d"]))     # next call: discovery (the load thread) restarts
            st.assert_called_once()

    def test_no_serving_peer_loads_locally_once(self):
        RR._peers_fn = lambda: [{"port": 7001}]
        with mock.patch.object(RR, "_post", side_effect=OSError("down")):
            self.assertIsNone(RR._find_peer())
        with mock.patch.object(RR, "_installed", return_value=True), \
                mock.patch.object(RR, "_start") as st:
            RR.scores("q", ["d"])
            RR.scores("q", ["d"])
        st.assert_called_once()


if __name__ == "__main__":
    unittest.main()
