"""THE RERANKER LOADS AFTER THE BOOT (#1115): its load (torch + the bge weights onto the GPU) started
right after the field's rows and ran beside the rest of the boot, competing with it. It starts once
the boot is otherwise done (after the `[boot]` line), or earlier on the first recall that wants it;
while it loads, recall runs as it does without a reranker (None: the scorer's order), and once it is
loaded recall uses it.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import recall_rerank as RR  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()


class TheBootOrder(unittest.TestCase):
    def test_the_load_starts_after_the_boot_line(self):
        main = SRC[SRC.index("\ndef main() -> None:"):]
        self.assertEqual(main.count("recall_rerank.warm("), 1)
        self.assertGreater(main.index("recall_rerank.warm("), main.index("_boot_laps.done()"))
        self.assertGreater(main.index("recall_rerank.warm("), main.index("runner._httpd, _ = start("))


class TheFirstRecallBeforeIt(unittest.TestCase):
    def setUp(self):
        self._saved = (RR._state, RR._model, RR._peer)
        RR._state, RR._model, RR._peer = "idle", None, None

    def tearDown(self):
        RR._state, RR._model, RR._peer = self._saved

    def test_a_recall_starts_the_load_and_runs_without_it_meanwhile(self):
        with mock.patch.dict("os.environ", {"MEMBRANE_RERANK": "1"}), \
                mock.patch.object(RR, "_installed", return_value=True), mock.patch.object(RR, "_start") as st:
            self.assertIsNone(RR.scores("q", ["a", "b"]))
            self.assertEqual(RR._state, "loading")
        st.assert_called_once()

    def test_once_loaded_recall_uses_it(self):
        class _M:
            def predict(self, pairs, **kw):
                return [float(len(d)) for _, d in pairs]
        with mock.patch.dict("os.environ", {"MEMBRANE_RERANK": "1"}), \
                mock.patch.object(RR, "_installed", return_value=True):
            RR._state, RR._model = "ready", _M()
            self.assertEqual(RR.scores("q", ["ab", "abcd"]), [2.0, 4.0])


if __name__ == "__main__":
    unittest.main()
