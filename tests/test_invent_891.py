"""NOVELTY (#891) as the operator decided after Memo's reviews (2026-11-03): a new meaning needs
BOTH the model's word for it, asked blind, and this chat's field evidence (k >= 2 distinct things,
true of all, able to fail); a contradiction unlearns it; a learned word never picks what a write
acts on; a person's own definition is never "learned". No chance test.

What's true of the things is not what the word means (Memo: people talk about the open goals, so a
rare state is the likeliest coincidence), so the field alone never teaches, and the model isn't
shown the field (shown candidates, Haiku picked "failed = open" 1 in 5). Recurrence alone never
teaches a verb or another name.
"""
import os
import sys
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_living_language import HAVE, SID, _add, _world  # noqa: E402
from membrane import language as LL  # noqa: E402


class Near:
    """landed ~ closed; everything else apart. Deterministic."""
    def embed(self, texts):
        out = []
        for t in texts:
            t = str(t).lower().split()[-1] if str(t).split() else ""
            v = np.zeros(4)
            v[0] = 1.0 if t in ("landed", "closed", "shipped") else 0.0
            v[3] = {"landed": 0.1, "closed": 0.3}.get(t, 0.2)
            out.append(v)
        return np.array(out)


def _seat(open_goals=20):
    """goal one open, goal two and goal three closed, and many more open goals: closed is rare."""
    srv, f, est, goal, g1, g2 = _world()
    srv.efield.emb = Near()
    g3 = _add(est, "goal three", parents=[goal.eid], status="closed")
    for i in range(open_goals):
        _add(est, f"goal open {i}", parents=[goal.eid], status="open")
    return srv, f, est, goal, g1, g2, g3


class Model:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.reply, {}


def _say(srv, text, word, reply=""):
    return LL.fill_gaps(srv, SID, text, [word], ask=Model(reply))


def _learned(est, word):
    d = est.docs.get(LL._adj_eid(word))
    return bool(d is not None and d.properties.get("learned"))


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class BothSignals(unittest.TestCase):
    def test_the_model_is_asked_blind(self):
        srv, *_ = _seat()
        ask = Model("landed: none")
        LL.fill_gaps(srv, SID, "goal two landed", ["landed"], ask=ask)
        LL.fill_gaps(srv, SID, "goal three landed", ["shipped"], ask=ask)
        self.assertTrue(all("status closed" not in p and "field shows" not in p for p in ask.prompts))

    def test_a_common_state_can_be_learned(self):
        """No chance test: on a seat where most goals are closed, landed = closed is learnable."""
        srv, f, est, *_ = _seat(open_goals=0)              # goal one open, two and three closed
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        self.assertTrue(_learned(est, "landed"))

    def test_the_models_word_and_the_chats_evidence_teach_it(self):
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        self.assertFalse(_learned(est, "landed"))                 # one thing: a proposal
        got = _say(srv, "goal three landed", "landed")
        self.assertTrue(got["words"][0]["confirmed"])
        self.assertTrue(_learned(est, "landed"))
        self.assertEqual(LL.understand(srv, SID, "goal two landed")["surprise"], [])

    def test_the_field_alone_never_teaches(self):
        """Memo: "failed" said of two open goals became open -- what's true isn't what it means."""
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed: none")
        _say(srv, "goal three landed", "landed", "")
        self.assertNotIn(LL._adj_eid("landed"), est.docs)

    def test_the_model_alone_never_teaches(self):
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal one landed", "landed")                    # goal one is open: the chat disagrees
        self.assertFalse(_learned(est, "landed"))

    def test_one_thing_said_twice_is_one_thing(self):
        srv, f, est, *_ = _seat()
        _say(srv, "goal two is cool", "cool", "cool means status closed")
        _say(srv, "wow, goal two is cool", "cool")
        _say(srv, "goal two is cool", "cool")
        self.assertFalse(_learned(est, "cool"))

    def test_a_question_doesnt_count_as_a_message_about_it(self):
        srv, f, est, *_ = _seat()
        _say(srv, "is goal two cool?", "cool", "cool means status closed")
        _say(srv, "goal three is cool", "cool")
        self.assertFalse(_learned(est, "cool"))                   # one plain statement so far

    def test_only_plain_statements_count(self):
        """Memo's nine forms and the ones after: an allowlist, not exceptions."""
        for a, b in [("if goal two landed, tell me", "if goal three landed, tell me"),
                     ("goal two should be landed", "goal three should be landed"),
                     ("i want goal two landed", "i want goal three landed"),
                     ("goal two is not landed", "goal three never landed"),
                     ("when goal two landed", "when goal three landed"),
                     ("goal two almost landed", "goal three almost landed"),
                     ("no, goal two landed", "no, goal three landed"),
                     ("goal two probably landed", "goal three probably landed"),
                     ("goal two has barely landed", "goal three has hardly landed"),
                     ("hope goal two landed", "hope goal three landed"),
                     ("goal two landed, or did it", "goal three landed, or did it"),
                     ("goal two and not goal three landed", "goal three and not goal two landed")]:
            srv, f, est, *_ = _seat()
            _say(srv, a, "landed", "landed means status closed")
            _say(srv, b, "landed")
            self.assertFalse(_learned(est, "landed"), (a, b))

    def test_a_multi_word_gap_is_confirmed(self):
        """Memo on #894: observe returned None for "big thing", so its proposal was never confirmed."""
        srv, f, est, *_ = _seat()
        _say(srv, "goal two is a big thing", "big thing", "big thing means status closed")
        _say(srv, "goal three is a big thing", "big thing")
        self.assertTrue(_learned(est, "big thing"))

    def test_a_correction_is_counted(self):
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        LL.teach(est, LL._define_step(srv, est, "landed", "status open", "landed means status open"))
        self.assertTrue(est.docs[LL._adj_eid("landed")].properties.get("corrected"))
        self.assertFalse(_learned(est, "landed"))

    def test_a_learned_word_never_picks_a_writes_targets(self):
        """Memo: "close the failed goals" would close every open goal."""
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        self.assertTrue(_learned(est, "landed"))
        [step] = LL.compile(srv, SID, "close the landed goals")
        self.assertFalse(step["mapped"])
        self.assertEqual(step["calls"], [])

    def test_a_contradiction_unlearns_it_on_the_messages_own_path(self):
        """Memo: a learned word is no surprise, so the old path never saw it again."""
        from membrane import lanes as L
        srv, f, est, goal, g1, g2, g3 = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        self.assertTrue(_learned(est, "landed"))
        srv._loop_runner = type("R", (), {"sessions": None, "post_line": lambda self, sid, t: None})()
        srv._cg_turn, srv._cg_text = {SID: {}}, {SID: "goal one landed"}     # goal one is open
        old = LL._ask_model
        LL._ask_model = lambda prompt: ("", {})
        try:
            L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)
        finally:
            LL._ask_model = old
        self.assertNotIn(LL._adj_eid("landed"), est.docs)
        self.assertFalse(est.docs[LL._gap_eid("landed")].properties["kept"])

    def test_a_persons_own_definition_is_not_learned_and_survives(self):
        """Memo: after the person defines it, writes still refused; and a contradiction would delete it."""
        srv, f, est, *_ = _seat()
        _say(srv, "goal two landed", "landed", "landed means status closed")
        _say(srv, "goal three landed", "landed")
        [step] = LL.compile(srv, SID, "landed means status closed")
        LL.teach(est, step)
        self.assertFalse(_learned(est, "landed"))
        LL.observe_learned(srv, SID, "goal one landed")           # would contradict a learned one
        self.assertIn(LL._adj_eid("landed"), est.docs)
        [step] = LL.compile(srv, SID, "close the landed goals")
        self.assertTrue(step["calls"])                            # the person's word may pick targets

    def test_a_verb_synonym_is_never_taught_by_recurrence(self):
        """Memo: "review means show" posted "Reviewed goal two." with nothing reviewed."""
        srv, f, est, goal, *_ = _seat()
        from membrane import night as N
        from membrane import verbs as V
        assert N.method_define(srv, est, f, None, goal.eid, "show", "echo shown", read_only=True).get("ok")
        V.boot(srv)
        _say(srv, "please review goal one", "review", "review means show")
        _say(srv, "please review goal two", "review")
        v = est.docs.get(V.verb_eid("review"))
        self.assertFalse(v is not None and (v.properties or {}).get("same_as"))

    def test_counts_are_per_chat(self):
        srv, f, est, *_ = _seat()
        LL.fill_gaps(srv, "ses_other", "goal two landed", ["landed"], ask=Model("landed means status closed"))
        _say(srv, "goal three landed", "landed")
        self.assertFalse(_learned(est, "landed"))

    def test_the_association_edges_are_kept_once_per_message(self):
        srv, f, est, goal, g1, g2, g3 = _seat()
        LL.observe(srv, SID, "goal two landed", "landed")
        LL.observe(srv, SID, "goal two landed", "landed")
        a = est.docs[LL._assoc_eid(SID, "landed")].properties
        self.assertEqual((a["seen"], a["things"]), (1, [g2.eid]))
        self.assertEqual(a["held"]["x.status == 'closed'"], [g2.eid])

    def test_the_report(self):
        rows = [{"filled": {"words": [{"word": "landed", "kept": True},
                                      {"word": "cool", "proposed": True, "kept": False},
                                      {"word": "yup", "kept": False}]}}]
        self.assertEqual(LL.shadow_report(rows)["novelty"], {"learned": 1, "proposed": 1, "none": 1})


if __name__ == "__main__":
    unittest.main()
