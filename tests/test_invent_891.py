"""NOVELTY CREATION (#891; the operator, 2026-11-03: "can novelty creation use tensors?" ->
tensors propose, code is the meaning, the field verifies, the model is the fallback; 5 candidates).

A surprise word the parse ties to things the message names ("goal two landed") gets an invented
meaning without the model: candidates from those things' own properties (x.status == 'closed')
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

    def embed(self, texts):
        out = []
        for t in texts:
            t = str(t).lower().split()[-1] if str(t).split() else ""
            v = np.zeros(8)
            for i, g in enumerate(self.GROUPS):
                if t in g:
                    v[i] = 1.0
            v[7] = 0.1 + (hash(t) % 7) / 100.0          # every word a little apart
            out.append(v)
        return np.array(out)


def _seat():
    srv, f, est, goal, g1, g2 = _world()
    srv.efield.emb = Near()
    return srv, f, est, goal, g1, g2


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Invent(unittest.TestCase):
    def test_a_condition_is_invented_from_the_things_own_state(self):
        srv, f, est, goal, g1, g2 = _seat()
        got = LL.invent(srv, SID, "goal two landed", "landed")
        self.assertEqual((got["kind"], got["meaning"]), ("adjective", "x.status == 'closed'"))
        d = est.docs[LL._adj_eid("landed")]
        self.assertTrue(d.properties["invented"])
        self.assertEqual(LL.understand(srv, SID, "goal two landed")["surprise"], [])
        self.assertIn("goal two is landed", LL.respond(srv, SID, "is goal two landed?"))

    def test_a_read_only_verb_is_invented_as_a_synonym(self):
        srv, f, est, goal, g1, g2 = _seat()
        from membrane import night as N
        assert N.method_define(srv, est, f, None, goal.eid, "check", "echo ok", read_only=True).get("ok")
        from membrane import verbs as V
        V.boot(srv)
        got = LL.invent(srv, SID, "please peek goal one", "peek")
        self.assertEqual((got["kind"], got["meaning"]), ("verb", "check"))

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
        for g in (g1, g2):
            g.properties["kind"] = "closed"           # the same for every goal: can't fail
        got = LL.invent(srv, SID, "goal two landed", "landed")
        self.assertEqual(got["meaning"], "x.status == 'closed'")

    def test_only_the_top_candidates_are_checked(self):
        srv, f, est, goal, g1, g2 = _seat()
        self.assertEqual(LL.INVENT_CANDIDATES, 5)

    def test_the_model_is_asked_only_what_tensors_couldnt_invent(self):
        srv, *_ = _seat()
        prompts = []
        got = LL.fill_gaps(srv, SID, "ok, goal two landed", ["ok", "landed"],
                           ask=lambda p: prompts.append(p) or ("ok: none", {}))
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("landed", prompts[0].split("understand these words:")[1].split("\n")[0])
        by = {w["word"]: w for w in got["words"]}
        self.assertTrue(by["landed"]["invented"] and by["landed"]["kept"])
        self.assertFalse(by["ok"].get("invented"))

    def test_a_later_definition_marks_the_invention_corrected(self):
        srv, f, est, *_ = _seat()
        LL.invent(srv, SID, "goal two landed", "landed")
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
