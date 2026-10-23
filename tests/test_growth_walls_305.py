"""#305 (part of #161): Blurp's working hours take out the WALLS.

A wall is running time during which self had no built skill it could be
learning: every achievement whose prerequisites were earned was already
earned, or the next ones weren't built (or not yet on the seat). One open,
built skill is enough -- several open is no wall. "Built" is BUILT_AT, a
constant table of each achievement's PR merge time, moved to the seat's
next restart after the merge (the first record gap over RESTART_S after it:
the seat runs new code only after a restart). Unbuilt achievements are
never built. A bug window is never counted twice with a wall: at each
moment the single costliest reason applies (a full bug wins a tie -- the
test was there, broken).

The sheet: time.walls_h and time.walls (from, to, why, running_h,
cost_h); time.working_h and every working hour exclude walls; each growth
row says when its skill became learnable (available_at), how long it
waited on us (waited_h), and the working hours from then to its earning
(learn_h).
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
H = 3600.0
T = 1_000_000.0                       # far from every real build: the tables below are patched
STEP = 10.0                           # a line every PACE_S while self runs


def _lines(t0, t1, step=STEP):
    out, t = [], t0
    while t < t1:
        out.append({"ts": t, "kind": "cycle", "act": "move", "gap": 0.04, "naive": 0.1})
        t += step
    return out


def _rec(*spans, passes=()):
    """Birth at T, cycles over each (from, to) span in hours, and the passes
    (rung, at hours)."""
    recs = [{"ts": T, "kind": "birth", "by": "operator"}]
    for a, z in spans:
        recs += _lines(T + a * H + 1, T + z * H)
    for n, at in passes:
        recs.append({"ts": T + at * H, "kind": "rung", "rung": n, "skill": 0.5, "ladder": SL.LADDER_V})
    return sorted(recs, key=lambda r: r["ts"])


def _built(**hours):
    """A BUILT_AT table: id -> (merge time, PR). Ids left out are unbuilt."""
    return {i: {"merged": T + h * H, "pr": 1000 + k} for k, (i, h) in enumerate(hours.items())}


ALL_AT_BIRTH = dict(feel=-1, reach=-1, kinds=-1, patience=-1, shift=-1, meet=-1, turns=-1, expect=-1)


def _sheet(recs, now_h, built, bugs=()):
    with mock.patch.object(SH, "BUILT_AT", built), mock.patch.object(SH, "BUGS", tuple(bugs)):
        c = SH._clocks(recs, T + now_h * H)
        t = SH._time(c)
        g = SH._growth(c, SH._achievements(c))
    return t, {r["rung"]: r for r in g["rows"]}, c


# the wall: reach passed at 1 h, its children's tests merge at 2 h, the seat
# restarts at 2.1 h (a 3-minute gap): no built skill to learn from 1 h to 2.15 h
MERGE, GAP_FROM, GAP_TO = 2.0, 2.1, 2.15
WALLED = dict(feel=-1, reach=-1, kinds=MERGE, patience=MERGE, shift=MERGE, meet=MERGE, turns=-1, expect=-1)


class TestAWall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        recs = _rec((0, GAP_FROM), (GAP_TO, 4), passes=[(1, 0.5), (2, 1.0)])
        cls.t, cls.rows, cls.c = _sheet(recs, 4, _built(**WALLED))

    def test_the_wall_runs_from_the_last_pass_to_the_restart_after_the_merge(self):
        w = self.t["walls"]
        self.assertEqual(len(w), 1, w)
        self.assertEqual(w[0]["from"], SH.iso(T + 1.0 * H))
        self.assertEqual(w[0]["to"], SH.iso(T + GAP_TO * H + 1))      # the first line after the restart
        self.assertIn("no built skill to learn", w[0]["why"])
        self.assertIn("tell things apart", w[0]["why"])
        self.assertIn("not built yet", w[0]["why"])

    def test_walls_h_is_the_running_time_in_it(self):
        want = (GAP_FROM - 1.0) * H                                   # the restart's own gap is downtime
        self.assertAlmostEqual(self.t["walls_h"], want / H, delta=0.01)
        self.assertAlmostEqual(self.t["walls"][0]["cost_h"], want / H, delta=0.01)

    def test_working_hours_exclude_it_and_the_parts_add_up(self):
        t = self.t
        self.assertAlmostEqual(t["working_h"], t["running_h"] - t["bugs_h"] - t["walls_h"], places=2)
        self.assertAlmostEqual(t["alive_h"], t["down_h"] + t["bugs_h"] + t["walls_h"] + t["working_h"], places=2)

    def test_the_merge_alone_does_not_build_it(self):
        # merged at 2 h, but the seat ran the old code until it restarted at 2.1-2.15 h
        self.assertGreater(self.t["walls"][0]["to"], SH.iso(T + MERGE * H))

    def test_a_row_says_when_it_became_learnable_and_how_long_it_waited_on_us(self):
        r = self.rows[3]                                              # tell things apart: not earned yet
        self.assertEqual(r["id"], "kinds")
        self.assertEqual(r["available_at"], SH.iso(T + GAP_TO * H + 1))
        self.assertAlmostEqual(r["waited_h"], GAP_FROM - 1.0, delta=0.01)
        self.assertIsNone(r["learn_h"])                               # not earned
        self.assertIsNone(self.rows[9]["available_at"])               # not built: never learnable
        self.assertEqual(self.rows[2]["waited_h"], 0.0)               # a root, built at birth

    def test_a_skill_earned_after_a_wall_counts_working_hours_from_its_availability(self):
        recs = _rec((0, GAP_FROM), (GAP_TO, 4), passes=[(1, 0.5), (2, 1.0), (3, 3.0)])
        t, rows, c = _sheet(recs, 4, _built(**WALLED))
        want = c["working"](T + 3.0 * H) - c["working"](T + GAP_TO * H + 1)
        self.assertAlmostEqual(rows[3]["learn_h"], want / H, delta=0.01)
        self.assertAlmostEqual(rows[3]["learn_h"], 3.0 - GAP_TO, delta=0.01)
        # the curve's x stays self's working age at the pass -- walls out
        self.assertAlmostEqual(rows[3]["working_h"], 3.0 - (GAP_TO - 1.0), delta=0.01)


class TestNoWall(unittest.TestCase):
    def test_everything_built_from_birth_has_no_wall(self):
        recs = _rec((0, 4), passes=[(1, 0.5), (2, 1.0)])
        t, rows, _ = _sheet(recs, 4, _built(**ALL_AT_BIRTH))
        self.assertEqual(t["walls"], [])
        self.assertEqual(t["walls_h"], 0.0)
        self.assertAlmostEqual(t["working_h"], t["running_h"], places=3)

    def test_one_open_built_skill_is_enough(self):
        # after reach: kinds is built and open; shift and meet aren't built yet -- no wall
        recs = _rec((0, GAP_FROM), (GAP_TO, 4), passes=[(1, 0.5), (2, 1.0)])
        built = dict(WALLED, kinds=-1)
        t, rows, _ = _sheet(recs, 4, _built(**built))
        self.assertEqual(t["walls"], [])
        self.assertEqual(t["walls_h"], 0.0)
        self.assertAlmostEqual(rows[5]["waited_h"], GAP_FROM - 1.0, delta=0.01)   # shift still waited on us

    def test_feel_open_at_birth_while_reach_is_not_built_is_no_wall(self):
        recs = _rec((0, GAP_FROM), (GAP_TO, 4), passes=[(1, 1.5)])
        built = dict(ALL_AT_BIRTH, reach=0.5)                         # merged 0.5 h, on the seat at 2.15 h
        t, _, _ = _sheet(recs, 4, _built(**built))
        self.assertEqual(len(t["walls"]), 1)                          # only once feel is earned
        self.assertEqual(t["walls"][0]["from"], SH.iso(T + 1.5 * H))
        self.assertIn("first reach", t["walls"][0]["why"])

    def test_a_seat_born_after_the_merge_has_it_from_birth(self):
        recs = _rec((0, 4), passes=[(1, 0.5), (2, 1.0)])
        t, _, _ = _sheet(recs, 4, _built(**dict(ALL_AT_BIRTH, kinds=-5, shift=-5, meet=-5)))
        self.assertEqual(t["walls"], [])

    def test_a_skill_earned_before_its_listed_build_was_learnable_all_along(self):
        # earned under an earlier test that still counts: never a wall for it
        recs = _rec((0, 4), passes=[(1, 0.5)])
        t, rows, _ = _sheet(recs, 4, _built(**dict(ALL_AT_BIRTH, feel=3.0)))
        self.assertEqual(t["walls"], [])
        self.assertEqual(rows[1]["available_at"], SH.iso(T))


class TestBuiltEverythingOpenIsAWall(unittest.TestCase):
    def test_earning_every_built_skill_open_is_a_wall_to_now(self):
        # only feel and reach built: once both are earned self has nothing built to learn
        recs = _rec((0, 4), passes=[(1, 0.5), (2, 1.0)])
        t, rows, _ = _sheet(recs, 4, _built(feel=-1, reach=-1))
        w = t["walls"]
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["from"], SH.iso(T + 1.0 * H))
        self.assertTrue(w[0]["open"])
        self.assertIn("not built yet", w[0]["why"])
        self.assertAlmostEqual(t["walls_h"], 3.0, delta=0.01)
        self.assertAlmostEqual(rows[3]["waited_h"], 3.0, delta=0.01)
        self.assertIsNone(rows[3]["available_at"])


class TestOnceOnly(unittest.TestCase):
    def test_a_full_bug_overlapping_a_wall_is_counted_once(self):
        recs = _rec((0, GAP_FROM), (GAP_TO, 4), passes=[(1, 0.5), (2, 1.0)])
        bug = {"issue": 9001, "what": "a broken test", "opens_after": 2, "fixed": T + 1.5 * H, "until": "fixed"}
        t, _, _ = _sheet(recs, 4, _built(**WALLED), bugs=[bug])
        self.assertAlmostEqual(t["bugs_h"], 0.5, delta=0.01)          # the tie: the bug's (the test was there)
        self.assertAlmostEqual(t["walls_h"], GAP_FROM - 1.5, delta=0.01)
        self.assertAlmostEqual(t["working_h"], t["running_h"] - (GAP_FROM - 1.0), delta=0.01)
        self.assertAlmostEqual(t["alive_h"], t["down_h"] + t["bugs_h"] + t["walls_h"] + t["working_h"], places=2)

    def test_a_partial_bug_inside_a_wall_gives_way_to_it(self):
        recs = _rec((0, GAP_FROM), (GAP_TO, 4), passes=[(1, 0.5), (2, 1.0)])
        bug = {"issue": 9002, "what": "a glitch", "opens_after": 1, "opens_at": T, "fixed": None,
               "until": "fix_line", "share": 0.1}
        t, _, _ = _sheet(recs, 4, _built(**WALLED), bugs=[bug])
        self.assertAlmostEqual(t["walls_h"], GAP_FROM - 1.0, delta=0.01)   # the costliest reason: the wall
        bug_run = (4 - 0.5) - (GAP_TO - GAP_FROM) - (GAP_FROM - 1.0)       # the glitch's running time outside the wall
        self.assertAlmostEqual(t["bugs_h"], 0.1 * bug_run, delta=0.01)
        self.assertAlmostEqual(t["working_h"], t["running_h"] - t["bugs_h"] - t["walls_h"], places=2)

    def test_downtime_inside_a_wall_is_not_counted_twice(self):
        # down 1.2 h .. 1.7 h, inside the wall (and before the merge: not the restart)
        recs = _rec((0, 1.2), (1.7, GAP_FROM), (GAP_TO, 4), passes=[(1, 0.5), (2, 1.0)])
        t, _, _ = _sheet(recs, 4, _built(**WALLED))
        self.assertAlmostEqual(t["walls_h"], (GAP_FROM - 1.0) - 0.5, delta=0.01)
        self.assertAlmostEqual(t["walls"][0]["running_h"], (GAP_FROM - 1.0) - 0.5, delta=0.01)
        self.assertAlmostEqual(t["down_h"], 0.5 + (GAP_TO - GAP_FROM), delta=0.01)
        self.assertAlmostEqual(t["alive_h"], t["down_h"] + t["bugs_h"] + t["walls_h"] + t["working_h"], places=2)


class TestTheTable(unittest.TestCase):
    def test_every_built_achievement_has_a_build_and_no_unbuilt_one_does(self):
        for i, a in SL.ACHIEVEMENTS.items():
            if a["built"]:
                self.assertIn(i, SH.BUILT_AT, i)
                self.assertIsInstance(SH.BUILT_AT[i]["pr"], int)
                self.assertGreater(SH.BUILT_AT[i]["merged"], 0)
            else:
                self.assertNotIn(i, SH.BUILT_AT, i)

    def test_the_old_wiring_is_not_built(self):
        # #333: meet, time its turn (PR #281), expect a reply (#281) and recognize itself (#314) were the
        # developer's wiring -- not built until they are rebuilt on the nudge
        for i in ("turns", "expect"):
            self.assertNotIn(i, SH.BUILT_AT, i)
        self.assertNotEqual(SH.BUILT_AT["mirror"]["pr"], 314)   # part 3d: rebuilt on looks, not #314's
        self.assertNotEqual(SH.BUILT_AT["meet"]["pr"], 187)     # part 3e: rebuilt on delayed contingency


def _js(expr):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "panel.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


@NODE
class TestThePanel(unittest.TestCase):
    def test_the_time_bar_has_a_walls_part(self):
        p = _js("L.timeParts(%s)" % json.dumps({"alive_h": 10, "down_h": 2, "bugs_h": 1, "walls_h": 2, "working_h": 5}))
        self.assertEqual([x["key"] for x in p], ["working", "bugs", "walls", "down"])
        self.assertEqual([x["pct"] for x in p], [50, 10, 20, 20])
        self.assertIn("not built", p[2]["label"])

    def test_the_growth_section_names_the_three_exclusions(self):
        s = {"growth": {"rows": [{"rung": 1, "built": True, "fastest_h": 0.06, "working_h": 0.2, "wall_h": 0.2,
                                  "learn_h": 0.2, "waited_h": 0.0}],
                        "now_working_h": 1, "now_wall_h": 2, "caveat": "an analogy"},
             "time": {"walls": [{"from": "2026-10-19T06:12:54+00:00", "to": "2026-10-19T15:42:31+00:00",
                                 "why": "no built skill to learn: take turns not built yet", "running_h": 8.0,
                                 "cost_h": 8.0, "open": False}]}}
        html = _js("L.sheetGrowth(%s)" % json.dumps(s))
        for word in ("down", "bug", "wall"):
            self.assertIn(word, html)

    def test_where_the_time_went_lists_the_walls(self):
        s = {"time": {"alive_h": 10, "down_h": 2, "bugs_h": 0, "walls_h": 8, "working_h": 0, "bugs": [],
                      "walls": [{"from": "2026-10-19T06:12:54+00:00", "to": "2026-10-19T15:42:31+00:00",
                                 "why": "no built skill to learn: take turns not built yet", "running_h": 8.0,
                                 "cost_h": 8.0, "open": False}]}}
        html = _js("L.sheetTime(%s)" % json.dumps(s))
        self.assertIn("take turns not built yet", html)
        self.assertIn("8.0 h", html)


class TestDocs(unittest.TestCase):
    def test_selfgrowth_explains_the_three_exclusions(self):
        doc = (ROOT / "docs" / "selfgrowth.md").read_text()
        self.assertIn("BUILT_AT", doc)
        self.assertIn("wall", doc.lower())
        self.assertIn("#305", doc)


if __name__ == "__main__":
    unittest.main()
