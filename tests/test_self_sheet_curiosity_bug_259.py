"""#259 on self's timeline: "curiosity chased the heartbeat's restart glitches".

Unlike #191 / #208 / #254 it never held a rung shut; it cost PART of self's
time: a bug window now carries a `share` (default 1.0), working hours lose
share x running time, and where windows overlap the costliest open one's share
applies (never counted twice). It opens at the #212 curiosity merge (on a seat
past rung 2, the rung curiosity starts at) and closes at the record's
`fix` line (issue 259), which self_loop writes once, at its first cycle running
the fix; open until then.
"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_self_sheet_249 as S  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SH  # noqa: E402
from _self_cap import start_rebuilt, stop_rebuilt  # noqa: E402

_REBUILT: list = []


def setUpModule():
    """#333: meet, turns, expect and mirror built as their rebuild (part 3) will
    build them; the passes of rungs 6-9 here are on ladder 3."""
    _REBUILT[:] = start_rebuilt()


def tearDownModule():
    stop_rebuilt(_REBUILT)

H = S.H
MERGE212 = dt.datetime(2026, 10, 18, 20, 17, 24, tzinfo=dt.timezone.utc).timestamp()
B0 = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc).timestamp()
R6 = dt.datetime(2026, 10, 18, 22, 0, tzinfo=dt.timezone.utc).timestamp()
NOW = dt.datetime(2026, 10, 19, 6, 0, tzinfo=dt.timezone.utc).timestamp()


def _bug(sheet, issue=259):
    return {b["issue"]: b for b in sheet["time"]["bugs"]}.get(issue)


def _young(srv, upto=6):
    """Born B0, running all along; rungs 1..upto, rung 6 (honest: with a
    person) at R6, so #254 opens there; #208 never applies (the person came
    before rung 5)."""
    S._put(srv, {"ts": B0, "kind": "birth"})
    S._cycles(srv, B0 + 1, NOW)
    S._put(srv, {"ts": B0 + 0.4 * H, "kind": "push", "to": S.ALICE})
    for n, t in ((1, B0 + 0.1 * H), (2, B0 + 0.2 * H), (3, B0 + 0.3 * H), (4, B0 + 0.5 * H),
                 (5, B0 + 1.0 * H), (6, R6)):
        if n <= upto:
            S._put(srv, {"ts": t, "kind": "rung", "rung": n, "skill": 1.0, "eid": S.ALICE, "ladder": SL.LADDER_V})
    if upto >= 6:                                  # #333 part 3e: meet needs first reach too (no wall before it)
        S._put(srv, {"ts": B0 + 0.15 * H, "kind": "rung", "rung": 13, "skill": 1.0, "ladder": SL.LADDER_V})
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv


def _without(*issues):
    return mock.patch.object(SH, "BUGS", tuple(b for b in SH.BUGS if b["issue"] not in issues))


class TestCuriosityBugWindow(unittest.TestCase):
    def test_the_bug_is_on_the_list(self):
        b = {x["issue"]: x for x in SH.BUGS}[259]
        self.assertEqual(b["share"], 0.06)
        self.assertEqual(b["until"], "fix_line")
        self.assertEqual(b["opens_at"], MERGE212)
        self.assertIn("curiosity chased the heartbeat's restart glitches", b["what"])

    def test_the_older_windows_cost_all_their_time(self):
        for b in SH.BUGS:
            if b["issue"] != 259:
                self.assertEqual(b.get("share", 1.0), 1.0)

    def test_open_before_the_fix_and_closed_at_it(self):
        srv = _young(S._seat(), upto=2)
        b = _bug(S._sheet(srv, NOW))
        self.assertTrue(b["open"])
        self.assertEqual(b["from"], SH.iso(MERGE212))
        self.assertEqual(b["share"], 0.06)
        fix = NOW - 2 * H
        S._put(srv, {"ts": fix, "kind": "fix", "issue": 259})
        b = _bug(S._sheet(srv, NOW))
        self.assertFalse(b["open"])
        self.assertEqual(b["to"], SH.iso(fix))
        self.assertAlmostEqual(b["running_h"], (fix - MERGE212) / H, places=2)

    def test_the_partial_share_subtracts_proportionally(self):
        srv = _young(S._seat(), upto=2)                    # only #259 applies: no rung 6
        with_bug = S._sheet(srv, NOW)
        with _without(259):
            no_bug = S._sheet(srv, NOW)
        drop = no_bug["time"]["working_h"] - with_bug["time"]["working_h"]
        run = (NOW - MERGE212) / H
        self.assertAlmostEqual(drop, 0.06 * run, places=2)
        self.assertAlmostEqual(with_bug["time"]["bugs_h"], 0.06 * run, places=2)
        b = _bug(with_bug)
        self.assertAlmostEqual(b["running_h"], run, places=2)
        self.assertAlmostEqual(b["cost_h"], 0.06 * run, places=2)

    def test_an_overlap_with_the_254_window_is_not_counted_twice(self):
        srv = _young(S._seat())
        s = S._sheet(srv, NOW)
        self.assertTrue(_bug(s, 254)["open"] and _bug(s, 259)["open"])
        want = (0.06 * (R6 - MERGE212) + (NOW - R6)) / H     # 1.0 covers 0.06 from R6 on
        self.assertAlmostEqual(s["time"]["bugs_h"], want, places=2)
        with _without(259):
            only254 = S._sheet(srv, NOW)
        self.assertAlmostEqual(only254["time"]["bugs_h"], (NOW - R6) / H, places=2)
        self.assertAlmostEqual(only254["time"]["working_h"] - s["time"]["working_h"],
                               0.06 * (R6 - MERGE212) / H, places=2)

    def test_existing_windows_are_unchanged(self):
        srv = S._lived(S._seat())                          # 2026-10-16/17: before #259 opened
        now = S.T0 + 12 * H
        s = S._sheet(srv, now)
        self.assertIsNone(_bug(s, 259))
        with _without(259):
            self.assertEqual(S._sheet(srv, now)["time"], s["time"])
        for b in s["time"]["bugs"]:
            self.assertEqual(b["share"], 1.0)
            self.assertAlmostEqual(b["cost_h"], b["running_h"], places=3)

    def test_a_seat_before_rung_2_never_had_it(self):
        srv = _young(S._seat(), upto=1)
        self.assertIsNone(_bug(S._sheet(srv, NOW)))

    def test_a_seat_running_the_fix_from_the_start_never_had_it(self):
        srv = _young(S._seat(), upto=2)
        S._put(srv, {"ts": B0 + 0.05 * H, "kind": "fix", "issue": 259})
        self.assertIsNone(_bug(S._sheet(srv, NOW)))


class TestTheFixMark(unittest.TestCase):
    def test_the_loop_writes_the_fix_once_past_rung_2(self):
        srv = S._seat()
        for r in (1, 2):
            S._put(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9, "ladder": SL.LADDER_V})
        SL._mark_glitch_fix(srv, 50.0)
        SL._mark_glitch_fix(srv, 60.0)
        SL._RT.pop(id(srv), None)                          # a restart: runtime gone
        SL.bind(srv)
        SL._mark_glitch_fix(srv, 70.0)
        marks = [r for r in SL._read(srv) if r.get("kind") == "fix" and r.get("issue") == 259]
        self.assertEqual([m["ts"] for m in marks], [50.0])

    def test_no_fix_line_before_rung_2(self):
        srv = S._seat()
        S._put(srv, {"ts": 1.0, "kind": "rung", "rung": 1, "skill": 0.9})
        SL._mark_glitch_fix(srv, 50.0)
        self.assertFalse([r for r in SL._read(srv) if r.get("kind") == "fix" and r.get("issue") == 259])


@S.NODE
class TestTheSheetShowsTheShare(unittest.TestCase):
    def test_a_partial_window_shows_its_share_and_cost(self):
        d = S._demo()
        d["time"]["bugs"].append({"issue": 259, "what": "curiosity chased the heartbeat's restart glitches",
                                  "from": "2026-10-18T20:17:24+00:00", "to": "2026-10-19T06:00:00+00:00",
                                  "open": True, "running_h": 9.71, "share": 0.06, "cost_h": 0.58})
        html = S._js("L.selfSheetHtml(%s, {}, 1100)" % json.dumps(d))
        for want in ("#259 curiosity chased the heartbeat", "now (open) · 6% of 9.7 h running = 35 min"):
            self.assertIn(want, html, want)


if __name__ == "__main__":
    unittest.main()
