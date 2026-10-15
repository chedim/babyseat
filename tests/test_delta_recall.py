"""THE CHANGES LIKE THIS ONE (2026-10-15, the delta lane, stage 2b).

The rows keep each mention's move (#55 dbits: against the running state;
#57 pbits: against the previous mention). A move's DIRECTION names the kind of
change on entities never seen (prose: started/stopped 0.86, broke/fixed 0.93,
chance 0.25 -- experiments/delta_lane), and only by RESEMBLANCE: zero-shot
against the words 'started'/'fixed' it reads at chance. So the recall is
nearest neighbours in the bit space: field.deltas_like(row) -> the past
changes most like this one -- on this entity ('self'), on others ('others'),
or both -- each as what was SAID (sentence, #N, time) with its similarity
(1.0 identical, 0.5 unrelated), never the bits. The card's 'changes' shows
the latest change and its look-alikes.

The fake embedder is bag-of-words: 'the tests for X fail' -> 'the tests for
X pass again' is the same move for every file X -- the property the recall
must find across entities."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import fieldmap as FM  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane import vsa  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

FILES = ["membrane/alpha.py", "membrane/beta.py", "membrane/gamma.py"]
STORY = ["{f} looks fine this morning",
         "the tests for {f} fail with an import error",
         "the tests for {f} pass again after the fix",
         "{f} was moved into the utils package"]


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")


def _say(f, texts, t0=1000.0):
    evs = [ST.Event(stream="discourse", role="user", text=t, ts=t0 + i)
           for i, t in enumerate(texts)]
    f.write_events(evs, session="s1")
    f.process()


def _world():
    f = _field()
    _say(f, [s.format(f=p) for p in FILES for s in STORY])
    return f


def _row(f, path, i):
    rows = sorted(f.deltas_of("f:" + path), key=lambda d: (d["seq"], d["sidx"]))
    return rows[i]


class TestChangesLikeThisOne(unittest.TestCase):
    def test_a_change_finds_the_same_kind_on_other_entities(self):
        f = _world()
        q = _row(f, FILES[2], 2)                         # gamma: tests pass again
        got = f.deltas_like(q, k=2, scope="others")
        self.assertEqual(len(got), 2)
        self.assertEqual({g["eid"] for g in got}, {"f:" + FILES[0], "f:" + FILES[1]})
        for g in got:
            self.assertIn("pass again", g["text"])

    def test_self_scope_stays_on_the_entity_and_skips_the_query(self):
        f = _world()
        q = _row(f, FILES[2], 3)
        got = f.deltas_like(q, k=5, scope="self", min_sim=0.0)
        self.assertTrue(got)
        self.assertEqual({g["eid"] for g in got}, {"f:" + FILES[2]})
        self.assertNotIn(q["event"], {g["event"] for g in got})

    def test_results_say_what_was_said_never_the_bits(self):
        f = _world()
        got = f.deltas_like(_row(f, FILES[2], 2), k=1, scope="others")
        g = got[0]
        for key in ("eid", "text", "n", "ts", "sim", "event"):
            self.assertIn(key, g)
        self.assertTrue(0.5 < g["sim"] <= 1.0)
        self.assertFalse({"pbits", "dbits"} & set(g))

    def test_nothing_alike_returns_nothing(self):
        f = _world()
        import numpy as np
        noise = vsa.pack_state(np.random.default_rng(3).integers(0, 2, vsa.DELTA_DIM))
        self.assertEqual(f.deltas_like(noise, k=5), [])

    def test_the_running_state_bits_are_queryable_too(self):
        f = _world()
        got = f.deltas_like(_row(f, FILES[2], 2), k=2, scope="others", bits="dbits", min_sim=0.0)
        self.assertEqual(len(got), 2)

    def test_the_index_follows_new_rows(self):
        f = _world()
        q = _row(f, FILES[2], 2)
        _say(f, [s.format(f="membrane/delta.py") for s in STORY], t0=2000.0)
        got = f.deltas_like(q, k=3, scope="others")
        self.assertIn("f:membrane/delta.py", {g["eid"] for g in got})


class TestTheCardShowsThem(unittest.TestCase):
    def test_latest_changes_has_the_latest_and_its_lookalikes(self):
        f = _world()
        ch = f.latest_changes("f:" + FILES[2], k=2)
        self.assertIn("moved into the utils package", ch["latest"]["text"])
        self.assertTrue(ch["others"])
        self.assertTrue(all("moved" in g["text"] for g in ch["others"]))

    def test_the_card_carries_changes_without_vectors(self):
        f = _world()
        doc = f.get("f:" + FILES[2]).to_doc(f.origin)
        card = FM.entity_card(doc, [], [], [], changes=f.latest_changes("f:" + FILES[2]))
        self.assertIn("changes", card)
        blob = repr(card["changes"])
        self.assertNotIn("pbits", blob)
        self.assertNotIn("dbits", blob)

    def test_an_entity_without_changes_gets_an_empty_section(self):
        f = _world()
        self.assertEqual(f.latest_changes("f:nowhere.py"), {})


if __name__ == "__main__":
    unittest.main()
