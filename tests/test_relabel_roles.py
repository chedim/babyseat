"""THE PRE-SPLIT COUNTS (2026-10-08, the seat's own follow-up, red first):

  "do the pre-split `said N (unsplit)` counts ever decay or get
   retro-relabeled? hass-states sitting at 27 unsplit mention-echoes will
   keep it pinned near the top of my context ranking forever if they count
   as evidence."

They did count (evidence_n fell back to n). relabel_roles() relabels every
live state that has no per-role counts yet, ONCE:
  * entities with per-mention DELTAS: each delta names its event, the event
    names its ROLE -> the true split (model/reasoning = echo, decayed from
    the mention's own time);
  * word/phrase entities (no deltas -- the word-delta skip): their old
    discourse count is unknowable, so it becomes ECHO decaying from the
    state's last-seen time; observation/code stay evidence.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ef_fakes as _fakes  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402


def _field():
    f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
    for i in range(4):
        f.write_events(ST.split_turn(
            f"why does membrane/router.py keep retrying the gateway, round {i}?",
            f"membrane/router.py retries the gateway with backoff (round {i}); "
            f"membrane/router.py logs each rung"), session="s")
    f.process()
    f.flush()
    return f


def _wipe(f):
    """Back to the pre-split shape (no per-role counts, no echo)."""
    for st in list(f._states.values()):
        st.n_by_role, st.echo, st.echo_ts = {}, 0.0, None
        f._dirty.add(st.eid)
    f.flush()


class TestRelabel(unittest.TestCase):
    def test_delta_entities_get_the_true_split(self):
        f = _field()
        eid = "f:membrane/router.py"
        st = f.get(eid)
        self.assertIsNotNone(st, sorted(f._states)[:20])
        before = dict(st.n_by_role)
        _wipe(f)
        out = f.relabel_roles()
        self.assertGreaterEqual(out["from_deltas"], 1)
        st = f.get(eid)
        self.assertEqual(st.n_by_role, before)            # the live split, recovered
        self.assertGreater(st.n_by_role.get("model", 0), st.n_by_role.get("user", 0))
        self.assertEqual(EF.evidence_n(st), st.n_by_role.get("user", 0))

    def test_word_entities_become_fading_echo(self):
        f = _field()
        _wipe(f)
        words = [st for st in f._states.values() if st.tier == "live"
                 and st.eid.split(":", 1)[0] in ("w", "p") and st.n_by_stream.get("discourse")]
        if not words:
            self.skipTest("no live word entity in this tiny field")
        f.relabel_roles()
        w = f.get(words[0].eid)
        self.assertTrue(w.n_by_role)                       # relabelled (no legacy fallback)
        self.assertEqual(EF.evidence_n(w), w.n_by_stream.get("observation", 0) + w.n_by_stream.get("code", 0))
        self.assertGreater(w.echo, 0)

    def test_once_only(self):
        f = _field()
        _wipe(f)
        first = f.relabel_roles()
        self.assertGreater(first["relabelled"], 0)
        self.assertEqual(f.relabel_roles()["relabelled"], 0)

    def test_survives_a_reload(self):
        f = _field()
        _wipe(f)
        f.relabel_roles()
        eid = "f:membrane/router.py"
        want = dict(f.get(eid).n_by_role)
        f._states.clear()
        self.assertEqual(f.get(eid).n_by_role, want)


if __name__ == "__main__":
    unittest.main()
