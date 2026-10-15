"""THE VIEWER SEES 0.5.0 (2026-10-15). The card drawer rendered evidence, nearest and
relations only: 'changes like this one' (#58) was computed on every card and never
shown; the sentence a co edge was said in (#62), what a command is usually followed by
(the idioms), the forewarning's calibration (#59, #66) and the entity's WORLDLINE were
invisible.

The worldline on the card: its chapters (episodes, each with when it ran and the
sentence that closed it -- descriptive only: a big jump between chapters marks a change
too weakly to say so, AUC 0.545) and its SHAPE, as percentiles among the field's mature
entities -- step (how far it jumps between chapters), spread (how widely it ranges),
returns (how often it comes back to an old chapter). Shape is a trait: first half vs
second half of an entity's life, beyond size, step +0.75 / +0.67, spread +0.73 / +0.69,
returns +0.26 / +0.30 (two seats). Vectors never leave the seat.

The drawer's HTML is a pure function (FieldLogic.cardHtml), rendered here through node."""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import fieldmap as FM  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402
import test_worldlines as W  # noqa: E402

FIELD_JS = Path(__file__).resolve().parent.parent / "membrane" / "ui" / "field.js"


def _mature_world():
    f = W._field()
    W._say(f, W._lines(W.OLD, W.A, 25, "pa", 1) + W._lines(W.OLD, W.B, 25, "pb", 2))
    W._say(f, W._lines("membrane/wander.py", W.A + W.B + W.C, 45, "wa", 7), t0=8000.0)
    W._say(f, W._lines("membrane/steady.py", W.C, 45, "st", 8), t0=12000.0)
    return f


class TestTheEpisodesRememberWhen(unittest.TestCase):
    def test_each_chapter_knows_when_it_ran_and_how_it_ended(self):
        f = _mature_world()
        st = f.get("f:" + W.OLD)
        self.assertEqual(len(st.ep_meta), len(st.episodes))
        t0, t1, sid = st.ep_meta[0]
        self.assertLessEqual(t0, t1)
        self.assertTrue(sid)
        back = EF.EntityState.from_doc(st.to_doc(f.origin))
        self.assertEqual([list(m) for m in back.ep_meta], [list(m) for m in st.ep_meta])

    def test_a_merge_keeps_the_span(self):
        from unittest import mock
        with mock.patch.object(EF, "EP_MAX", 4):
            f = _mature_world()
        st = f.get("f:" + W.OLD)
        self.assertEqual(len(st.ep_meta), len(st.episodes))
        self.assertEqual(st.ep_meta[0][0], min(m[0] for m in st.ep_meta))
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(st.ep_meta, st.ep_meta[1:])))


class TestTheWorldlineOf(unittest.TestCase):
    def test_chapters_say_when_and_what(self):
        f = _mature_world()
        wl = f.worldline_of("f:" + W.OLD)
        self.assertEqual(len(wl["chapters"]), 10)
        c = wl["chapters"][0]
        self.assertLessEqual(c["from"], c["to"])
        self.assertEqual(c["n"], EF.EP_RUN)
        self.assertIn(W.OLD, c["said"])

    def test_the_shape_is_a_percentile_among_mature_entities(self):
        f = _mature_world()
        wander, steady = f.worldline_of("f:membrane/wander.py"), f.worldline_of("f:membrane/steady.py")
        for k in ("step", "spread", "returns"):
            self.assertTrue(0 <= wander["shape"][k] <= 100)
        self.assertGreater(wander["shape"]["spread"], steady["shape"]["spread"])

    def test_a_young_entity_has_no_shape_yet(self):
        f = _mature_world()
        W._say(f, W._lines("membrane/kid.py", W.A, 6, "k", 9), t0=20000.0)
        self.assertNotIn("shape", f.worldline_of("f:membrane/kid.py"))

    def test_the_card_carries_it_and_no_vector(self):
        f = _mature_world()
        st = f.get("f:" + W.OLD)
        card = FM.entity_card(st.to_doc(f.origin), [], [], [], worldline=f.worldline_of(st.eid))
        self.assertIn("chapters", card["worldline"])
        self.assertNotIn("episodes", card)
        self.assertNotIn("ep_acc", card)


class TestTheCalibrationShows(unittest.TestCase):
    def test_both_forecasts_report_what_they_saw(self):
        f = _mature_world()
        f._ostats["forewarn"] = {"n": 10, "bad": 3, "flagged": 2, "flagged_bad": 2}
        f._ostats["forewarn_act"] = {"n": 40, "bad": 8, "flagged": 4, "flagged_bad": 3, "first": 9, "first_bad": 2}
        cal = f.forewarn_calibration()
        self.assertEqual(cal["moments"]["flagged_bad"], 2)
        self.assertEqual(cal["acts"]["n"], 40)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestTheDrawerRendersIt(unittest.TestCase):
    def _node(self, expr):
        js = (f"const L = require({json.dumps(str(FIELD_JS))});\n"
              f"process.stdout.write(JSON.stringify({expr}));")
        return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)

    CARD = {
        "eid": "c:grep", "n": 90, "tier": "live",
        "changes": {"latest": {"text": "grep found nothing in membrane/x.py", "n": 41, "ts": 1790000000},
                    "self": [{"text": "grep found nothing in efield", "sim": 0.81, "eid": "c:grep"}],
                    "others": [{"text": "sed found nothing in efield", "sim": 0.74, "eid": "c:sed"}]},
        "relations": [{"subj": "c:grep", "rel": "co", "obj": "f:membrane/x.py", "n": 3,
                       "said": "grep -n foo membrane/x.py"}],
        "followed_by": [{"to": "read", "n": 24, "lift": 4.0, "share": 0.3}],
        "worldline": {"chapters": [{"from": 1790000000, "to": 1790003600, "n": 5, "said": "grep ran on the proxy"}],
                      "shape": {"step": 82, "spread": 30, "returns": 64}},
    }

    def test_every_new_section_is_drawn(self):
        h = self._node(f"L.cardHtml({json.dumps(self.CARD)}, () => true)")
        for want in ("Changes like this one", "grep found nothing in membrane/x.py", "sed found nothing in efield",
                     "grep -n foo membrane/x.py", "Usually followed by", "read", "4.0",
                     "Its life", "grep ran on the proxy", "82", "64"):
            self.assertIn(want, h)

    def test_what_was_said_is_escaped(self):
        card = dict(self.CARD, relations=[{"subj": "c:grep", "rel": "co", "obj": "x", "n": 1, "said": "<script>x</script>"}])
        h = self._node(f"L.cardHtml({json.dumps(card)}, () => true)")
        self.assertNotIn("<script>", h)

    def test_an_old_card_still_draws(self):
        h = self._node('L.cardHtml({"eid": "f:a.py", "n": 3}, () => false)')
        self.assertIn("seen", h)

    def test_the_hud_chip_reads_the_calibration(self):
        chip = self._node('L.forewarnChip({"moments": {"n": 10, "bad": 3, "flagged": 2, "flagged_bad": 2},'
                          ' "acts": {"n": 40, "bad": 8, "flagged": 4, "flagged_bad": 3}})')
        self.assertIn("5/6", chip)            # flagged that went bad, both forecasts
        self.assertEqual(self._node("L.forewarnChip({})"), "")


if __name__ == "__main__":
    unittest.main()
