"""THE CITATION CARTEL (2026-10-08, the seat's own review, red first):

  "The token carrier shows said: 1205 for what is one env-source line. If
   my own echo-mentions increment 'said', you get a feedback loop: I
   mention it -> it looks hot -> it stays in my tracks -> I mention it
   again. Fix: split said-by-user/observed/code from said-by-model-echo,
   with decay."

The field counted mentions per STREAM only -- 'said' = user + model +
reasoning + notes -- and ranked specific entities by st.n, so the model's
own echoes raised an entity's rank in its own next context. Now:
  * every mention is counted per ROLE (n_by_role);
  * the model's echoes (model / reasoning) also land in a DECAYING echo
    count (half-life ECHO_HALF_LIFE_S);
  * evidence_n = the mentions that are not the model's echo -- the
    ranking and the banner's 'said' read it; the echo shows apart.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402


def _st(eid="m:c:token"):
    return EF.EntityState(eid=eid, kind="m", surface="token", tier="live")


class TestRoleCounts(unittest.TestCase):
    def test_roles_split_and_echo_decays(self):
        st = _st()
        EF.note_mention(st, "discourse", "user", 1000.0)
        EF.note_mention(st, "discourse", "user", 1000.0)
        for _ in range(3):
            EF.note_mention(st, "discourse", "model", 1000.0)
        EF.note_mention(st, "discourse", "reasoning", 1000.0)
        EF.note_mention(st, "observation", "", 1000.0)
        self.assertEqual(st.n_by_role, {"user": 2, "model": 3, "reasoning": 1, "observation": 1})
        self.assertEqual(EF.evidence_n(st), 3)                 # 2 user + 1 observed
        self.assertAlmostEqual(EF.echo_now(st, 1000.0), 4.0, places=6)
        later = 1000.0 + EF.ECHO_HALF_LIFE_S
        self.assertAlmostEqual(EF.echo_now(st, later), 2.0, places=6)
        EF.note_mention(st, "discourse", "model", later)
        self.assertAlmostEqual(EF.echo_now(st, later), 3.0, places=6)

    def test_notes_are_not_echo(self):
        st = _st()
        EF.note_mention(st, "discourse", "note", 5.0)           # a script's own tell
        self.assertEqual(EF.evidence_n(st), 1)
        self.assertEqual(EF.echo_now(st, 5.0), 0.0)

    def test_round_trip(self):
        st = _st()
        EF.note_mention(st, "discourse", "model", 7.0)
        EF.note_mention(st, "discourse", "user", 7.0)
        d = st.to_doc("o")
        d["state"] = d["seed"] = d["rsum"] = None
        back = EF.EntityState.from_doc({**d, "tier": "nursery"})
        self.assertEqual(back.n_by_role, {"model": 1, "user": 1})
        self.assertAlmostEqual(back.echo, 1.0)
        self.assertEqual(back.echo_ts, 7.0)


class _Field:
    def __init__(self, st):
        self.st = st

    def get(self, eid):
        return self.st if eid == self.st.eid else None

    def resolve(self, s):
        return self.st.eid

    def graph_fn(self):
        return None

    def evidence(self, eid, k=1, **kw):
        return []


class TestBanner(unittest.TestCase):
    def test_track_line_shows_evidence_and_echo_apart(self):
        st = _st()
        st.n, st.last_ts = 6, 1000.0
        # the real order (_update): each mention is counted as it lands
        for r in ("user", "model", "model", "model", "reasoning"):
            EF.note_mention(st, "discourse", r, 1000.0)
        EF.note_mention(st, "observation", "", 1000.0)
        st.n_by_stream = {"discourse": 5, "observation": 1}
        line = EB.track_line(_Field(st), st.eid, now=1000.0)
        self.assertIn("said 1", line)
        self.assertIn("echo ~4", line)
        self.assertIn("observed 1", line)
        self.assertNotIn("said 5", line)

    def test_pre_split_entities_say_so(self):
        st = _st()
        st.n, st.last_ts = 1205, 1000.0
        st.n_by_stream = {"discourse": 1205}
        line = EB.track_line(_Field(st), st.eid, now=1000.0)
        self.assertIn("said 1205 (unsplit)", line)

    def test_rank_reads_evidence_not_echo(self):
        loud, real = _st("m:c:loud"), _st("m:c:real")
        for _ in range(50):
            EF.note_mention(loud, "discourse", "model", 1.0)
        loud.n = 50
        for _ in range(5):
            EF.note_mention(real, "discourse", "user", 1.0)
        real.n = 5
        self.assertGreater(EB.rank_weight(real), EB.rank_weight(loud))


if __name__ == "__main__":
    unittest.main()


class TestPreSplitWithoutRewrites(unittest.TestCase):
    """The seat's follow-up, the cheap way (2026-10-08): a full relabel pass
    rewrote every live state row (~45KB each) into the append-only journal
    -- ~250MB on the phone. Instead: a pre-split state's old discourse never
    counts as evidence (it may be the model's own storm), and the state
    converts itself the next time it is mentioned (it is rewritten then
    anyway)."""

    def test_unsplit_discourse_is_not_evidence(self):
        st = _st()
        st.n = 1232
        st.n_by_stream = {"discourse": 1205, "observation": 20, "code": 7}
        self.assertEqual(EF.evidence_n(st), 27)

    def test_the_next_mention_converts_the_old_count_to_echo(self):
        st = _st()
        st.n, st.last_ts = 1205, 1000.0
        st.n_by_stream = {"discourse": 1205}
        EF.note_mention(st, "discourse", "user", 1000.0)
        self.assertEqual(st.n_by_role.get("user"), 1)
        self.assertIn("unsplit", st.n_by_role)
        self.assertEqual(EF.evidence_n(st), 1)
        self.assertAlmostEqual(EF.echo_now(st, 1000.0), 1205.0, places=3)
