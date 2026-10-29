"""#680: the reranker's score is the scorer's `bge` feature inside the top
RERANK_TOP rows; one ranking path for the banner and /membrane/ask. Optional
(the `rerank` extra), loaded in the background and kept; until it is ready, or
without it, the order is today's."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import recall_banner as RB  # noqa: E402
from membrane import recall_rerank as RR  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def rows_of(n):
    return [{"sim": 0.9 - i * 0.01, "q": "", "q_n": None, "a_n": [100 + i],
             "answer": [f"row {i} says the deploy runs on port {8000 + i}"]} for i in range(n)]


class TestRank(unittest.TestCase):
    def test_without_the_reranker_the_order_is_todays(self):
        rows = rows_of(30)
        with mock.patch.object(RR, "scores", return_value=None):
            got = [r["a_n"] for r in RB.rank(rows, "which port does the deploy run on?")]
        want = [r["a_n"] for r in RB.rank(rows, "which port does the deploy run on?", rerank=False)]
        self.assertEqual(got, want)

    def test_reorders_the_top_rows_only(self):
        rows = rows_of(30)
        base = RB.rank(rows, "which port does the deploy run on?", rerank=False)
        # the reranker loves the 20th row of the scorer's order, and the rest not at all
        fake = lambda text, docs: [10.0 if i == RR.RERANK_TOP - 1 else -10.0 for i in range(len(docs))]
        with mock.patch.object(RR, "scores", side_effect=fake) as sc:
            got = RB.rank(rows, "which port does the deploy run on?")
        self.assertEqual(len(sc.call_args[0][1]), RR.RERANK_TOP)
        self.assertEqual(got[0]["a_n"], base[RR.RERANK_TOP - 1]["a_n"])
        self.assertEqual([r["a_n"] for r in got[RR.RERANK_TOP:]], [r["a_n"] for r in base[RR.RERANK_TOP:]])
        self.assertIn("bge", got[0]["feat"])
        self.assertNotIn("bge", got[-1]["feat"])

    def test_constants(self):
        self.assertEqual(RR.RERANK_TOP, 20)
        self.assertIn("bge", RB.RERANK_WEIGHTS)


class TestLoader(unittest.TestCase):
    def setUp(self):
        self._state = (RR._state, RR._model)
        RR._state, RR._model = "idle", None

    def tearDown(self):
        RR._state, RR._model = self._state

    def test_off_by_env(self):
        with mock.patch.dict("os.environ", {"MEMBRANE_RERANK": "0"}), mock.patch.object(RR, "_start") as st:
            self.assertIsNone(RR.scores("q", ["d"]))
        st.assert_not_called()

    def test_missing_package_is_off_without_a_crash(self):
        with mock.patch.object(RR, "_installed", return_value=False), mock.patch.object(RR, "_start") as st:
            self.assertIsNone(RR.scores("q", ["d"]))
        st.assert_not_called()

    def test_first_call_starts_the_load_and_never_waits(self):
        with mock.patch.object(RR, "_installed", return_value=True), mock.patch.object(RR, "_start") as st:
            self.assertIsNone(RR.scores("q", ["d"]))
            self.assertIsNone(RR.scores("q", ["d"]))
        st.assert_called_once()

    def test_ready_scores_every_doc(self):
        class _M:
            def predict(self, pairs, **kw):
                return [float(len(d)) for _q, d in pairs]
        RR._state, RR._model = "ready", _M()
        with mock.patch.object(RR, "_installed", return_value=True):
            self.assertEqual(RR.scores("q", ["ab", "abcd"]), [2.0, 4.0])


class TestAskUsesTheRanking(unittest.TestCase):
    def test_membrane_ask_reads_the_banner_ranking(self):
        from membrane import opencode as oc
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.learn("the project license is the PolyForm Noncommercial license now")
        f.process()

        class _Srv:
            efield = f
            fid = "x"
        h = object.__new__(oc.MembraneOpenAIHandler)
        h.server = _Srv()
        h.path = "/membrane/ask?q=which+license+does+the+project+use%3F&k=8&sim_min=0"
        got = {}
        h._json_out = lambda d: got.update(d)
        real = RB.rank
        with mock.patch.object(RB, "rank", side_effect=lambda *a, **kw: real(*a, **kw)) as spy:
            h._membrane_ask()
        spy.assert_called_once()
        self.assertTrue(got["hit"])


if __name__ == "__main__":
    unittest.main()
