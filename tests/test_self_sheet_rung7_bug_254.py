"""#254 on self's timeline: "rung 7 graded the LLM's guess, not self's".

A known-bug window in self_sheet.BUGS like #191 / #208: it opens at the
honest rung 6 pass (rung 7 started counting then) and ends when the fixed
rung 7 starts counting -- the `rung7_restart` line self_loop writes once, at
its first cycle running the fix on a seat past rung 6. Until that line is on
the record the window is open and runs to the record's end. Working hours
drop by its running time, and the sheet marks rung 7's restart.

#258: that rung 7 is rung 8 (understand an other) since the ladder flip; the
window and the `rung7_restart` line keep their names. The restart note sits
on the expect skill's card (rung 8, #300) and on rung 8's quest, not on the new
rung 7's (take turns).
"""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_self_sheet_249 as S  # noqa: E402  (its fixtures: a seat that lived through the bugs)
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

H, T0 = S.H, S.T0
NOW = T0 + 12 * H


def _bug(sheet, issue=254):
    return {b["issue"]: b for b in sheet["time"]["bugs"]}.get(issue)


class TestRung7BugWindow(unittest.TestCase):
    def test_the_bug_is_on_the_list(self):
        b = {x["issue"]: x for x in SH.BUGS}[254]
        self.assertEqual(b["opens_after"], 6)
        self.assertEqual(b["until"], "rung7_restart")
        self.assertIn("graded the LLM", b["what"])

    def test_open_before_the_fix(self):
        srv = S._lived(S._seat())
        b = _bug(S._sheet(srv, NOW))
        self.assertIsNotNone(b)
        self.assertTrue(b["open"])
        self.assertEqual(b["from"], SH.iso(srv._real6))            # the honest rung 6 pass
        self.assertAlmostEqual(b["running_h"], (NOW - srv._real6) / H, places=2)

    def test_closed_after_the_fix(self):
        srv = S._lived(S._seat())
        fix = T0 + 11 * H
        S._put(srv, {"ts": fix, "kind": "rung7_restart", "issue": 254})
        b = _bug(S._sheet(srv, NOW))
        self.assertFalse(b["open"])
        self.assertEqual(b["to"], SH.iso(fix))
        self.assertAlmostEqual(b["running_h"], (fix - srv._real6) / H, places=2)

    def test_working_hours_drop_by_its_running_time(self):
        srv = S._lived(S._seat())
        # #305: every test built from birth, so no wall stands where the window is (on
        # this seat, born 2026-10-16, rung 8's test reached the seat only on 2026-10-19:
        # a wall would take the window's time once the bug is gone -- counted once)
        with mock.patch.object(SH, "BUILT_AT", S.BUILT_FROM_BIRTH):
            with_bug = S._sheet(srv, NOW)
            without = [b for b in SH.BUGS if b["issue"] != 254]
            with mock.patch.object(SH, "BUGS", tuple(without)):
                no_bug = S._sheet(srv, NOW)
        drop = no_bug["time"]["working_h"] - with_bug["time"]["working_h"]
        self.assertAlmostEqual(drop, _bug(with_bug)["running_h"], places=2)
        self.assertGreater(drop, 1.0)

    def test_a_seat_that_reached_rung_6_after_the_fix_never_had_it(self):
        srv = S._seat()
        t0 = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.timezone.utc).timestamp()
        S._put(srv, {"ts": t0, "kind": "birth"})
        S._cycles(srv, t0 + 1, t0 + 3 * H)
        S._put(srv, {"ts": t0 + 0.1 * H, "kind": "rung7_restart", "issue": 254})
        for n, h in ((1, 0.2), (2, 0.5), (3, 0.6), (4, 0.7), (5, 1.0), (6, 1.5)):
            S._put(srv, {"ts": t0 + h * H, "kind": "rung", "rung": n, "skill": 1.0, "eid": S.ALICE, "ladder": SL.LADDER_V})
        S._put(srv, {"ts": t0 + 1.2 * H, "kind": "push", "to": S.ALICE})
        self.assertIsNone(_bug(S._sheet(srv, t0 + 3 * H)))


class TestRung7RestartMark(unittest.TestCase):
    def test_the_sheet_marks_the_restart(self):
        srv = S._lived(S._seat())
        fix = T0 + 11 * H
        S._put(srv, {"ts": fix, "kind": "rung7_restart", "issue": 254})
        s = S._sheet(srv, NOW)
        day = dt.datetime.fromtimestamp(fix, dt.timezone.utc).strftime("%Y-%m-%d")
        note = f"restarted {day}: was grading the LLM, now grades Blurp"
        self.assertEqual(s["quest"]["rung"], 7)                    # take turns (#258)...
        self.assertIsNone(s["quest"].get("note"))                  # ...which never graded the LLM
        # #300: the note rides on the expect skill's card while it is being learned
        self.assertNotIn("expect", [k["id"] for k in s["skills"]])      # the limit (7) holds it: not on the card
        SL._rt(srv)["isolate"] = SL._limit_ids(8)                    # #299: the limit is gone; rungs 1..8 run
        k = {x["id"]: x for x in S._sheet(srv, NOW)["skills"]}["expect"]
        self.assertEqual(k["note"], note)

    def test_no_mark_before_the_restart(self):
        s = S._sheet(S._lived(S._seat()), NOW)
        self.assertIsNone(s["quest"].get("note"))

    def test_the_loop_writes_the_restart_once_past_rung_6(self):
        srv = S._seat()
        for r in (1, 2, 3, 4, 5, 6):
            S._put(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9, "ladder": SL.LADDER_V})
        SL._mark_rung7_restart(srv, 50.0)
        SL._mark_rung7_restart(srv, 60.0)
        SL._RT.pop(id(srv), None)                                  # a restart: runtime gone
        SL.bind(srv)
        SL._mark_rung7_restart(srv, 70.0)
        marks = [r for r in SL._read(srv) if r.get("kind") == "rung7_restart"]
        self.assertEqual([m["ts"] for m in marks], [50.0])

    def test_no_restart_line_before_rung_6(self):
        srv = S._seat()
        S._put(srv, {"ts": 1.0, "kind": "rung", "rung": 1, "skill": 0.9})
        SL._mark_rung7_restart(srv, 50.0)
        self.assertFalse([r for r in SL._read(srv) if r.get("kind") == "rung7_restart"])


@S.NODE
class TestTheSheetShowsIt(unittest.TestCase):
    def test_the_page_renders_the_restart_the_void_and_the_open_window(self):
        import json
        d = S._demo()
        d["skills"][0]["note"] = "restarted 2026-10-19: was grading the LLM, now grades self"   # #300: on its skill's card
        d["quest"]["voided"] = ["rung 7 passed 2026-10-19 05:38Z under the #254 bug: graded the LLM, not self; "
                                "doesn't count"]
        d["time"]["bugs"].append({"issue": 254, "what": "rung 7 graded the LLM's guess, not self's",
                                  "from": "2026-10-18T21:43:02+00:00", "to": "2026-10-19T05:50:00+00:00",
                                  "open": True, "running_h": 7.83})
        html = S._js("L.selfSheetHtml(%s, {}, 1100)" % json.dumps(d))
        for want in ("restarted 2026-10-19: was grading the LLM, now grades self",
                     "05:38Z under the #254 bug: graded the LLM, not self; doesn't count",
                     "#254 rung 7 graded the LLM", "now (open) · 7.8 h running"):
            self.assertIn(want, html, want)


if __name__ == "__main__":
    unittest.main()
