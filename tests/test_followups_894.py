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

from test_living_language import _add  # noqa: E402
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

    def _count(self, text):
        srv, f, est, goal, *_ = _seat()
        _add(est, "blocks panel", parents=[goal.eid], status="closed")
        p = LL.observe(srv, SID, text, "landed")
        return int((p or {}).get("seen") or 0)

    def test_a_multi_word_name_counts_when_it_is_the_subject(self):
        for text in ("blocks panel", "blocks panel landed", "the blocks panel landed",
                     "blocks panel is landed", "goal two landed"):
            if text == "blocks panel":
                continue                          # no word to observe: covered below
            with self.subTest(text=text):
                self.assertEqual(self._count(text), 1)

    def test_a_lead_word_before_a_name_is_not_a_statement(self):
        for text in ("hope goal two landed", "wish goal two landed", "hope blocks panel landed",
                     "hope the blocks panel landed", "hope panel landed", "blocks panel almost landed",
                     "if blocks panel landed"):
            with self.subTest(text=text):
                self.assertEqual(self._count(text), 0)

    def _unlearns(self, text):
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        self.assertTrue(_learned(est, "landed"))
        return LL.observe_learned(srv, SID, text) == ["landed"]

    def test_loose_unlearning_ignores_hedges_and_vague_subjects(self):
        for text in ("hope goal one landed", "goal one almost landed", "goal one barely landed",
                     "goal one probably landed", "goal one or goal two landed", "some goals landed"):
            with self.subTest(text=text):
                self.assertFalse(self._unlearns(text))

    def test_loose_unlearning_still_takes_plain_contradictions(self):
        for text in ("goal one landed today", "yes, goal one landed", "goal one just landed",
                     "goal two and goal one landed", "goal one landed in prod",
                     "goal one landed this afternoon", "goal one landed on monday", "goal one landed overnight"):
            with self.subTest(text=text):
                self.assertTrue(self._unlearns(text))

    def test_loose_unlearning_is_default_deny(self):
        for text in ("no goals landed", "neither goal one nor goal two landed", "most goals landed",
                     "goal one hopefully landed", "goal one presumably landed", "goal one reportedly landed",
                     "goal one allegedly landed", "goal one almost certainly landed", "goal one seems landed"):
            with self.subTest(text=text):
                self.assertFalse(self._unlearns(text))

    def test_loose_unlearning_keeps_plain_adverbs_and_determiners(self):
        for text in ("goal one definitely landed", "the goal one landed", "goal one landed today",
                     "goal one already landed", "goal one is landed"):
            with self.subTest(text=text):
                self.assertTrue(self._unlearns(text))

    def _unlearns_named(self, text):
        srv, f, est, goal, *_ = _seat()
        _add(est, "field ui", parents=[goal.eid], status="open")
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        self.assertTrue(_learned(est, "landed"))
        return LL.observe_learned(srv, SID, text) == ["landed"]

    def test_a_multi_word_name_unlearns_and_a_lead_word_still_blocks(self):
        for text in ("field ui landed", "the field ui landed", "field ui landed today", "field ui definitely landed"):
            with self.subTest(text=text):
                self.assertTrue(self._unlearns_named(text))
        for text in ("hope field ui landed", "field ui almost landed", "no field ui landed"):
            with self.subTest(text=text):
                self.assertFalse(self._unlearns_named(text))

    def test_a_persons_definition_is_not_replaced_by_a_proposal(self):
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        [step] = LL.compile(srv, SID, "landed means status closed")
        LL.teach(est, step)                      # the person's own: learned=False
        g = est.docs[LL._gap_eid("landed")]
        g.properties = {**g.properties, "kept": False}
        est.upsert(g, embedder=None)
        self.assertIsNone(LL.confirm_proposed(srv, SID, "goal three landed", "landed"))
        self.assertFalse(est.docs[LL._adj_eid("landed")].properties["learned"])


if __name__ == "__main__":
    unittest.main()
