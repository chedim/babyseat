"""#894 follow-ups: a legacy meaning with no `learned` flag that the model's gap answer kept is
a guess (learned/unlearnable in code); unlearning in a message is not undone by the same message."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_invent_891 import HAVE, SID, Model, _learned, _say, _seat  # noqa: E402
from membrane import language as LL  # noqa: E402


def _legacy(srv, est):
    """landed as #894's predecessor kept it: the model's guess, kept, no `learned` flag."""
    _say(srv, "goal two landed", "landed", "landed means status closed")
    _say(srv, "goal three landed", "landed")
    d = est.docs[LL._adj_eid("landed")]
    d.properties = {k: v for k, v in d.properties.items() if k not in ("learned", "learned_evidence", "learned_ts")}
    est.upsert(d, embedder=None)
    assert "learned" not in est.docs[LL._adj_eid("landed")].properties


@unittest.skipUnless(HAVE, "spaCy not installed")
class Followups(unittest.TestCase):
    def test_a_legacy_guess_counts_as_learned(self):
        srv, f, est, *_ = _seat()
        _legacy(srv, est)
        self.assertTrue(LL.is_learned(est, est.docs[LL._adj_eid("landed")]))
        [step] = LL.compile(srv, SID, "close the landed goals")
        self.assertFalse(step["mapped"])

    def test_a_legacy_guess_is_unlearned_by_a_contradiction(self):
        srv, f, est, *_ = _seat()
        _legacy(srv, est)
        self.assertEqual(LL.observe_learned(srv, SID, "goal one landed today"), ["landed"])
        self.assertNotIn(LL._adj_eid("landed"), est.docs)

    def test_a_persons_definition_without_a_gap_is_not_a_guess(self):
        srv, f, est, *_ = _seat()
        [step] = LL.compile(srv, SID, "landed means status closed")
        LL.teach(est, step)
        self.assertFalse(LL.is_learned(est, est.docs[LL._adj_eid("landed")]))
        self.assertEqual(LL.observe_learned(srv, SID, "goal one landed"), [])

    def test_a_persons_definition_over_a_legacy_guess_survives(self):
        srv, f, est, *_ = _seat()
        _legacy(srv, est)
        [step] = LL.compile(srv, SID, "landed means status closed")
        LL.teach(est, step)
        self.assertFalse(LL.is_learned(est, est.docs[LL._adj_eid("landed")]))
        self.assertEqual(LL.observe_learned(srv, SID, "goal one landed"), [])

    def test_unlearning_is_not_undone_in_the_same_message(self):
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        self.assertTrue(_learned(est, "landed"))
        msg = "goal one landed today"
        self.assertEqual(LL.observe_learned(srv, SID, msg), ["landed"])
        LL.fill_gaps(srv, SID, msg, ["landed"], ask=Model(""))
        self.assertNotIn(LL._adj_eid("landed"), est.docs)
        self.assertFalse(est.docs[LL._gap_eid("landed")].properties["kept"])


if __name__ == "__main__":
    unittest.main()
