"""NOVELTY CREATION (#891; the operator, 2026-11-03: "can novelty creation use tensors?" ->
tensors propose, code is the meaning, the field verifies, the model is the fallback; 5 candidates).

A surprise word the parse ties to things the message names ("goal two landed") gets an invented
meaning without the model once the conversation's association edges confirm it (d-build: the
same condition held every time, over INVENT_MIN_SEEN messages): candidates from those things' own properties (x.status == 'closed')
and the read-only verbs that apply to them, ranked by the word's vector, the top INVENT_CANDIDATES
checked on the field. The first that holds is kept, marked invented; a later definition of the
word marks it corrected.
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

from test_living_language import HAVE, SID, _world  # noqa: E402
from membrane import language as LL  # noqa: E402


class Near:
    """Words in the same group share a vector: landed ~ closed, peek ~ check."""
    GROUPS = [{"landed", "closed", "shipped"}, {"peek", "check"}]
    OFFSET = {"landed": 0.1, "shipped": 0.1, "closed": 0.3}

    def embed(self, texts):
        out = []
        for t in texts:
            t = str(t).lower().split()[-1] if str(t).split() else ""
            v = np.zeros(8)
            for i, g in enumerate(self.GROUPS):
                if t in g:
                    v[i] = 1.0
            v[7] = self.OFFSET.get(t, 0.2)               # fixed: landed and shipped closest, then closed
            out.append(v)
        return np.array(out)


def _seat():
    from test_living_language import _add
    srv, f, est, goal, g1, g2 = _world()
    srv.efield.emb = Near()
    _add(est, "goal three", parents=[goal.eid], status="closed")
    return srv, f, est, goal, g1, g2


def _twice(srv, word="landed", first="goal two landed", second="goal three landed"):
    """d-build: the first message only counts; the second that agrees confirms it."""
    assert LL.invent(srv, SID, first, word) is None
    return LL.invent(srv, SID, second, word)


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Invent(unittest.TestCase):
    def test_a_condition_is_invented_from_the_things_own_state(self):
        srv, f, est, goal, g1, g2 = _seat()
        got = _twice(srv)
        self.assertEqual((got["kind"], got["meaning"], got["n"]), ("adjective", "x.status == 'closed'", 2))
        d = est.docs[LL._adj_eid("landed")]
        self.assertTrue(d.properties["invented"])
        self.assertEqual(LL.understand(srv, SID, "goal two landed")["surprise"], [])
        self.assertIn("goal two is landed", LL.respond(srv, SID, "is goal two landed?"))

    def test_no_verb_is_invented(self):
        """Memo on #894: "please review goal one" made review = check, so a command would only read."""
        srv, f, est, goal, g1, g2 = _seat()
        from membrane import night as N
        assert N.method_define(srv, est, f, None, goal.eid, "check", "echo ok", read_only=True).get("ok")
        from membrane import verbs as V
        V.boot(srv)
        self.assertIsNone(LL.invent(srv, SID, "please peek goal one", "peek"))

    def test_a_question_asserts_nothing(self):
        srv, *_ = _seat()
        self.assertIsNone(LL.invent(srv, SID, "is goal one landed?", "landed"))
        self.assertIsNone(LL.invent(srv, SID, "did goal two land", "land"))

    def test_a_negation_asserts_the_opposite(self):
        srv, *_ = _seat()
        self.assertIsNone(LL.invent(srv, SID, "goal two is not landed", "landed"))
        self.assertIsNone(LL.invent(srv, SID, "goal two never landed", "landed"))

    def test_a_peer_that_cant_evaluate_is_not_a_no(self):
        """Memo on #894: an evaluation error counted as "false", so a broken peer passed the check."""
        from membrane import ch
        srv, f, est, goal, g1, g2 = _seat()
        real = ch.evaluate

        def broken_for_goal_one(expr, env):
            if getattr(env.get("x"), "_eid", None) != g2.eid:     # every peer of goal two is broken
                raise ch.Refused("broken")
            return real(expr, env)
        ch.evaluate = broken_for_goal_one
        try:
            self.assertIsNone(LL.invent(srv, SID, "goal two landed", "landed"))
            self.assertIsNone(LL.invent(srv, SID, "goal two landed again", "landed"))
        finally:
            ch.evaluate = real

    def test_a_write_verb_is_never_invented(self):
        srv, *_ = _seat()                     # close is a write method
        self.assertIsNone(LL.invent(srv, SID, "shut goal one", "shut"))

    def test_a_word_tied_to_nothing_is_not_invented(self):
        srv, *_ = _seat()
        self.assertIsNone(LL.invent(srv, SID, "landed, yay", "landed"))
        self.assertIsNone(LL.invent(srv, SID, "scan goal one", "scan"))     # a misparsed command: no state

    def test_an_action_is_not_a_state(self):
        srv, *_ = _seat()
        self.assertIsNone(LL.invent(srv, SID, "goal two landed the goals", "landed"))

    def test_a_condition_every_peer_shares_is_not_a_meaning(self):
        srv, f, est, goal, g1, g2 = _seat()
        for d in est.docs.values():
            if d.cls == g2.cls:
                d.properties["kind"] = "closed"       # the same for every such thing: can't fail
        got = _twice(srv)
        self.assertEqual(got["meaning"], "x.status == 'closed'")

    def test_only_the_top_candidates_are_checked(self):
        srv, f, est, goal, g1, g2 = _seat()
        peers = [d for d in est.docs.values() if d.cls == g2.cls]
        for i in range(LL.INVENT_CANDIDATES):         # five states ranked above the one that holds
            for d in peers:
                d.properties[f"tag{i}"] = "shipped"   # shared by every such thing: none of them can fail
        self.assertIsNone(_twice(srv))
        for d in peers:
            del d.properties["tag0"]
        self.assertEqual(LL.invent(srv, SID, "goal two landed!", "landed")["meaning"], "x.status == 'closed'")

    def test_the_model_is_asked_only_what_tensors_couldnt_invent(self):
        srv, *_ = _seat()
        prompts = []
        LL.fill_gaps(srv, SID, "goal three landed", ["landed"], ask=lambda p: prompts.append(p) or ("landed: none", {}))
        prompts.clear()                                # the first: counted, asked, nothing kept
        got = LL.fill_gaps(srv, SID, "ok, goal two landed", ["ok", "landed"],
                           ask=lambda p: prompts.append(p) or ("ok: none", {}))
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("landed", prompts[0].split("understand these words:")[1].split("\n")[0])
        by = {w["word"]: w for w in got["words"]}
        self.assertTrue(by["landed"]["invented"] and by["landed"]["kept"])
        self.assertFalse(by["ok"].get("invented"))

    def test_a_mood_word_is_never_invented(self):
        srv, *_ = _seat()                         # said of a closed goal, then an open one
        self.assertIsNone(_twice(srv, "great", "goal two great", "goal one great"))
        self.assertIsNone(LL.invent(srv, SID, "goal three great", "great"))

    def test_the_association_edges_are_kept(self):
        srv, f, est, goal, g1, g2 = _seat()
        LL.invent(srv, SID, "goal two landed", "landed")
        a = est.docs[LL._assoc_eid("landed")].properties
        self.assertEqual(a["n"], 1)
        self.assertEqual(a["held"]["x.status == 'closed'"], 1)
        LL.invent(srv, SID, "goal two landed", "landed")          # the same message: counted once
        self.assertEqual(est.docs[LL._assoc_eid("landed")].properties["n"], 1)

    def test_a_later_definition_marks_the_invention_corrected(self):
        srv, f, est, *_ = _seat()
        _twice(srv)
        [step] = LL.compile(srv, SID, "landed means status merged")
        LL.teach(est, step)
        d = est.docs[LL._adj_eid("landed")]
        self.assertTrue(d.properties["corrected"])
        self.assertFalse(d.properties["invented"])

    def test_the_report_counts_inventions(self):
        rows = [{"filled": {"words": [{"word": "landed", "kept": True, "invented": True},
                                      {"word": "fix", "kept": True},
                                      {"word": "yup", "kept": False}]}}]
        r = LL.shadow_report(rows)["novelty"]
        self.assertEqual(r, {"invented": 1, "model_kept": 1, "none": 1})


if __name__ == "__main__":
    unittest.main()
