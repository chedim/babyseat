"""NOVELTY CREATION (#891; the operator, 2026-11-03: tensors propose, code is the meaning, the field
verifies; "d-build": the chat's association edges confirm; 5 candidates, 2 messages, alpha 0.05).

A surprise word that plain statements say of things ("goal two landed") gets an invented meaning
without the model once this chat's association edges carry it: a condition true of every distinct
thing the word was said of, over at least INVENT_MIN_SEEN messages, and too rare among those
things' peers to agree by chance (p ** k <= INVENT_ALPHA). The model's own answers go through the
same rule before they're taught. Memo's review cases are here: coincidence, the same thing twice,
non-statements, any one-word property.
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
    """landed ~ closed ~ shipped; everything else apart. Deterministic."""
    GROUPS = [{"landed", "closed", "shipped"}]
    OFFSET = {"landed": 0.1, "shipped": 0.1, "closed": 0.3}

    def embed(self, texts):
        out = []
        for t in texts:
            t = str(t).lower().split()[-1] if str(t).split() else ""
            v = np.zeros(8)
            for i, g in enumerate(self.GROUPS):
                if t in g:
                    v[i] = 1.0
            v[7] = self.OFFSET.get(t, 0.2)
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


def _two(srv, word="landed", first="goal two landed", second="goal three landed"):
    assert LL.invent(srv, SID, first, word) is None          # one message is never enough
    return LL.invent(srv, SID, second, word)


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Invent(unittest.TestCase):
    def test_a_rare_state_two_things_share_is_invented(self):
        srv, f, est, *_ = _seat()
        got = _two(srv)
        self.assertEqual((got["kind"], got["meaning"], got["k"]), ("adjective", "x.status == 'closed'", 2))
        self.assertLessEqual(got["p"] ** got["k"], LL.INVENT_ALPHA)
        self.assertTrue(est.docs[LL._adj_eid("landed")].properties["invented"])
        self.assertEqual(LL.understand(srv, SID, "goal two landed")["surprise"], [])

    def test_a_common_state_agrees_by_chance_and_is_not_invented(self):
        """Memo: 88 fulfilled of 98 goals, so two picked at random agree ~90% of the time."""
        srv, *_ = _seat(open_goals=1)                         # closed: 2 of 4 goals, p = 0.5
        self.assertIsNone(_two(srv, "failed", "goal two failed", "goal three failed"))

    def test_the_same_thing_twice_is_one_thing(self):
        srv, *_ = _seat()
        self.assertIsNone(_two(srv, "cool", "goal two is cool", "wow, goal two is cool"))

    def test_non_statements_count_nothing(self):
        for a, b in [("if goal two landed, tell me", "if goal three landed, tell me"),
                     ("goal two should be landed", "goal three should be landed"),
                     ("i want goal two landed", "i want goal three landed"),
                     ("is goal two landed?", "is goal three landed?"),
                     ("goal two is not landed", "goal three never landed")]:
            srv, *_ = _seat()
            self.assertIsNone(_two(srv, "landed", a, b), (a, b))

    def test_a_property_most_peers_share_is_not_a_meaning(self):
        """Memo: with an owner on goals, "cool" became owner == 'Dima'."""
        srv, f, est, goal, g1, g2, g3 = _seat()
        for d in est.docs.values():
            if goal.eid in (d.properties.get("parents") or []):
                d.properties["owner"] = "alice"
        got = _two(srv)
        self.assertEqual(got["meaning"], "x.status == 'closed'")

    def test_a_mood_word_is_never_invented(self):
        srv, *_ = _seat()                                     # said of a closed goal, then an open one
        self.assertIsNone(_two(srv, "great", "goal two great", "goal one great"))
        self.assertIsNone(LL.invent(srv, SID, "goal three great", "great"))

    def test_no_verb_is_invented(self):
        srv, f, est, goal, *_ = _seat()
        from membrane import night as N
        from membrane import verbs as V
        assert N.method_define(srv, est, f, None, goal.eid, "check", "echo ok", read_only=True).get("ok")
        V.boot(srv)
        self.assertIsNone(_two(srv, "peek", "please peek goal one", "please peek goal two"))

    def test_counts_are_per_chat(self):
        srv, *_ = _seat()
        self.assertIsNone(LL.invent(srv, "ses_other", "goal two landed", "landed"))
        self.assertIsNone(LL.invent(srv, SID, "goal three landed", "landed"))   # a different chat: one each

    def test_the_association_edges_are_kept(self):
        srv, f, est, goal, g1, g2, g3 = _seat()
        LL.invent(srv, SID, "goal two landed", "landed")
        LL.invent(srv, SID, "goal two landed", "landed")      # the same message: counted once
        a = est.docs[LL._assoc_eid(SID, "landed")].properties
        self.assertEqual((a["seen"], a["things"]), (1, [g2.eid]))
        self.assertEqual(a["held"]["x.status == 'closed'"], [g2.eid])

    def test_only_the_top_candidates_are_checked(self):
        srv, f, est, goal, g1, g2, g3 = _seat()
        for d in est.docs.values():                           # five states ranked first, true of every peer
            if goal.eid in (d.properties.get("parents") or []):
                for i in range(LL.INVENT_CANDIDATES):
                    d.properties[f"tag{i}"] = "shipped"
        self.assertIsNone(_two(srv))
        for d in est.docs.values():
            d.properties.pop("tag0", None)
        self.assertEqual(LL.invent(srv, SID, "goal two landed!", "landed")["meaning"], "x.status == 'closed'")

    def test_the_model_is_asked_only_what_the_chat_couldnt_tell(self):
        srv, *_ = _seat()
        prompts = []
        LL.fill_gaps(srv, SID, "goal three landed", ["landed"], ask=lambda p: prompts.append(p) or ("landed: none", {}))
        prompts.clear()
        got = LL.fill_gaps(srv, SID, "ok, goal two landed", ["ok", "landed"],
                           ask=lambda p: prompts.append(p) or ("ok: none", {}))
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("landed", prompts[0].split("understand these words:")[1].split("\n")[0])
        by = {w["word"]: w for w in got["words"]}
        self.assertTrue(by["landed"]["invented"] and by["landed"]["kept"])

    def test_a_later_definition_marks_the_invention_corrected(self):
        srv, f, est, *_ = _seat()
        _two(srv)
        [step] = LL.compile(srv, SID, "landed means status merged")
        LL.teach(est, step)
        d = est.docs[LL._adj_eid("landed")]
        self.assertTrue(d.properties["corrected"])
        self.assertFalse(d.properties["invented"])

    def test_the_report_counts_inventions(self):
        rows = [{"filled": {"words": [{"word": "landed", "kept": True, "invented": True},
                                      {"word": "fix", "kept": True},
                                      {"word": "yup", "kept": False}]}}]
        self.assertEqual(LL.shadow_report(rows)["novelty"], {"invented": 1, "model_kept": 1, "none": 1})


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class TheModelsAnswers(unittest.TestCase):
    """The operator, 2026-11-03: the model's answers go through the d-build too."""
    def test_a_proposed_condition_waits_for_the_chat(self):
        srv, f, est, *_ = _seat()
        LL.fill_gaps(srv, SID, "goal two shipped", ["shipped"], ask=lambda p: ("shipped means closed", {}))
        self.assertNotIn(LL._adj_eid("shipped"), est.docs)              # proposed, not taught
        self.assertTrue(est.docs[LL._gap_eid("shipped")].properties["proposed"])
        got = LL.fill_gaps(srv, SID, "goal three shipped", ["shipped"], ask=lambda p: ("", {}))
        self.assertTrue(got["words"][0]["confirmed"])
        self.assertIn(LL._adj_eid("shipped"), est.docs)

    def test_a_proposed_condition_the_chat_contradicts_is_never_taught(self):
        srv, f, est, *_ = _seat()
        LL.fill_gaps(srv, SID, "goal two shipped", ["shipped"], ask=lambda p: ("shipped means closed", {}))
        LL.fill_gaps(srv, SID, "goal one shipped", ["shipped"], ask=lambda p: ("", {}))   # goal one is open
        self.assertNotIn(LL._adj_eid("shipped"), est.docs)

    def test_a_write_verb_synonym_is_never_taught(self):
        """Live 2026-11-03: Haiku taught keep = pause."""
        srv, f, est, goal, *_ = _seat()
        LL.fill_gaps(srv, SID, "please keep goal one", ["keep"], ask=lambda p: ("keep means close", {}))
        LL.fill_gaps(srv, SID, "please keep goal two", ["keep"], ask=lambda p: ("", {}))
        from membrane import verbs as V
        v = est.docs.get(V.verb_eid("keep"))
        self.assertFalse(v is not None and (v.properties or {}).get("same_as"))


if __name__ == "__main__":
    unittest.main()
