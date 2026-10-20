"""#300: the card stops repeating skill test scores as "attributes" and stops
talking in rungs.

1. ATTRIBUTES are a few traits of self as a whole, each MEASURED from its own
   record (never claimed): Curiosity, Insight, Adaptability, Focus,
   Sociability -- each with a value, a one-line "what it measures" and a short
   trend series. They gate nothing and are no pass criteria (no pass line).
2. SKILLS on the card: only the skills it is learning now (open and allowed),
   each with its staged criteria, bar, what it waits on and its skill's trend;
   several side by side. Earned skills leave the card, except one whose test
   no longer holds: a short slipping line.
3. The header says "Lv N · <latest earned skill>", never "rung".
4. `expect` displays as "expect a reply", `turns` as "time its turn" (ids stay).
5. An allowed open skill in the sheet's tree never waits on "the limit" (#299:
   the limit is gone; a skill only the operator's accept holds says so).
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SH  # noqa: E402
from tests.test_self_sheet_249 import T0, H, _lived, _seat, _sheet  # noqa: E402
from tests.test_skill_tree_295 import _seat as _tree_seat  # noqa: E402

UI = ROOT / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")
A, B, C, D = ("membrane::Component:a", "membrane::Component:b", "membrane::Component:c", "membrane::Component:d")
TRAITS = ["curiosity", "insight", "adaptability", "focus", "sociability"]


def _look(t, eid, p, changed, **kw):
    return {"ts": t, "kind": "reach", "eid": eid, "p": p, "answer": "changed" if changed else "unchanged", **kw}


def _vals(recs):
    return {a["key"]: a for a in SH.traits(recs)}


# ---- 1. the traits ------------------------------------------------------------------

class TestTraitsShape(unittest.TestCase):
    def test_five_traits_each_with_a_value_what_and_trend(self):
        recs = [_look(T0 + i, A, 0.5, i % 2 == 0) for i in range(100)]
        at = SH.traits(recs)
        self.assertEqual([a["key"] for a in at], TRAITS)
        self.assertEqual([a["label"] for a in at], ["Curiosity", "Insight", "Adaptability", "Focus", "Sociability"])
        for a in at:
            self.assertTrue(a["what"], a["key"])
            self.assertIn(a["better"], ("higher", "lower"))
            self.assertEqual(len(a["series"]), SH.SKILL_POINTS)
            # never a gate: no pass line, no rung, no state
            for k in ("pass", "met", "rung", "state"):
                self.assertNotIn(k, a)

    def test_nothing_measured_is_none(self):
        at = _vals([])
        self.assertTrue(all(a["value"] is None for a in at.values()))
        json.dumps(at)


class TestCuriosity(unittest.TestCase):
    def test_the_share_of_its_looks_at_things_it_is_still_learning(self):
        recs, t = [], T0
        W = SL.CURIO_WINDOW
        for i in range(2 * W):                       # A: wrong early, right late -- learning
            p = 0.9 if i < W else 0.1
            recs.append(_look(t, A, p, False)); t += 1
            recs.append(_look(t, B, 0.0, False)); t += 1   # B: always right -- nothing left to learn
        cu = _vals(recs)["curiosity"]
        self.assertAlmostEqual(cu["value"], 0.5, places=4)
        self.assertEqual(cu["better"], "higher")

    def test_too_young_to_say_is_none(self):
        self.assertIsNone(_vals([_look(T0 + i, A, 0.5, True) for i in range(10)])["curiosity"]["value"])


class TestInsight(unittest.TestCase):
    def test_brier_skill_against_the_windows_own_rate(self):
        recs = [_look(T0 + i, A, 0.75 if i % 2 == 0 else 0.25, i % 2 == 0) for i in range(40)]
        ins = _vals(recs)["insight"]
        # Brier 0.0625 against the base rate's 0.25: 1 - 0.0625/0.25
        self.assertAlmostEqual(ins["value"], 0.75, places=4)

    def test_audit_looks_dont_count(self):
        recs = [_look(T0 + i, A, 0.75 if i % 2 == 0 else 0.25, i % 2 == 0) for i in range(40)]
        recs += [_look(T0 + 100 + i, A, 0.0, True, audit=True) for i in range(40)]
        self.assertAlmostEqual(_vals(recs)["insight"]["value"], 0.75, places=4)


class TestAdaptability(unittest.TestCase):
    def test_looks_from_the_first_miss_to_the_noticed_shift(self):
        recs, t = [], T0
        for k in range(3):
            for i in range(10):
                recs.append(_look(t, A, 0.1, False)); t += 1      # hits
            misses = (2, 3, 4)[k]
            for i in range(5 - misses):
                recs.append(_look(t, A, 0.1, False)); t += 1
            for i in range(misses):
                recs.append(_look(t, A, 0.1, True)); t += 1       # misses: it said 0.1, it changed
            recs.append({"ts": t, "kind": "shift", "eid": A, "was": 0.1, "now": 0.6, "looks": 5}); t += 1
        ad = _vals(recs)["adaptability"]
        self.assertEqual(ad["value"], 3.0)                         # the median of 2, 3, 4
        self.assertEqual(ad["better"], "lower")

    def test_no_shift_noticed_yet_is_none(self):
        self.assertIsNone(_vals([_look(T0 + i, A, 0.1, True) for i in range(20)])["adaptability"]["value"])


class TestFocus(unittest.TestCase):
    def test_spread_evenly_is_zero_one_thing_is_one(self):
        even = [_look(T0 + i, (A, B, C, D)[i % 4], 0.5, True) for i in range(400)]
        self.assertAlmostEqual(_vals(even)["focus"]["value"], 0.0, places=4)
        one = [_look(T0 + i, (B, C, D)[i], 0.5, True) for i in range(3)]
        one += [_look(T0 + 10 + i, A, 0.5, True) for i in range(SH.TRAIT_LOOKS)]
        f = _vals(one)["focus"]
        self.assertAlmostEqual(f["value"], 1.0, places=4)
        self.assertEqual(f["detail"]["things"], 1)


class TestSociability(unittest.TestCase):
    def test_its_acts_aimed_at_others_and_how_often_they_answer(self):
        recs, t = [], T0
        for i in range(10):
            recs.append({"ts": t, "kind": "push", "eid": "chat:out", "to": A}); t += 1
            recs.append(_look(t, A, 0.5, i < 4, reply=True)); t += 1
            for _ in range(8):
                recs.append(_look(t, B, 0.5, False)); t += 1
        so = _vals(recs)["sociability"]
        self.assertAlmostEqual(so["value"], 0.1, places=4)            # 10 pushes of 100 acts
        self.assertAlmostEqual(so["detail"]["replied"], 0.4, places=4)  # 4 of 10 reply checks answered


class TestTraitTrend(unittest.TestCase):
    def test_the_series_ends_at_the_value(self):
        recs = [_look(T0 + i * 20, A, 0.75 if i % 2 == 0 else 0.25, i % 2 == 0) for i in range(1000)]
        ins = _vals(recs)["insight"]
        self.assertEqual([p["ago_h"] for p in ins["series"]], [4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5, 0.0])
        self.assertAlmostEqual(ins["series"][-1]["value"], ins["value"], places=4)


# ---- 2. the skills on the card ----------------------------------------------------------

class TestSkillsOnTheCard(unittest.TestCase):
    def test_only_what_it_is_learning_now(self):
        s = _sheet(_lived(_seat()), T0 + 12 * H)       # rungs 1-6 earned, limit 7: turns open and allowed
        ids = [k["id"] for k in s["skills"]]
        self.assertEqual(ids, ["turns"])                # expect is open but the limit holds it; earned ones are gone
        k = s["skills"][0]
        self.assertEqual(k["name"], "time its turn")
        self.assertEqual([c["key"] for c in k["criteria"]], ["pushes", "contingency", "payoff"])
        self.assertEqual(k["waiting_on"], "pushes")
        self.assertIn("bar", k)
        self.assertEqual(len(k["trend"]["series"]), SH.SKILL_POINTS)

    def test_several_side_by_side(self):
        srv = _tree_seat(rungs=(2,), limit="all")
        s = SH.sheet(srv, fresh=True)
        self.assertEqual([k["id"] for k in s["skills"]], ["feel", "kinds", "shift", "meet"])

    def test_an_earned_skill_whose_test_fails_now_is_a_slipping_line(self):
        s = _sheet(_lived(_seat()), T0 + 12 * H)
        sl = {x["id"]: x for x in s["slipping"]}
        self.assertIn("reach", sl)                      # no looks at w:fast on this record
        self.assertNotIn("feel", sl)
        self.assertIn("first reach", sl["reach"]["text"])


# ---- 3. the header and the zoo's card: skills, not rungs --------------------------------

class TestNoRungs(unittest.TestCase):
    def test_the_header_says_lv_and_the_latest_skill(self):
        h = _sheet(_lived(_seat()), T0 + 12 * H)["header"]
        self.assertEqual(h["title"], "Lv 6 · meet an other")
        self.assertEqual(h["learning"], ["time its turn"])
        words = " ".join(str(v) for v in h.values() if isinstance(v, str))
        self.assertNotIn("rung", words.lower())

    def test_the_zoo_card_keeps_its_keys(self):
        c = SH.card(_lived(_seat()))
        self.assertEqual(c["level"], 6)
        self.assertEqual(c["rung_name"], "meet an other")              # the latest earned skill
        self.assertEqual(c["next"], ["turns", "expect", "mirror"])   # #312: recognize itself opens with meet


# ---- 4. the renames ----------------------------------------------------------------------

class TestRenames(unittest.TestCase):
    def test_names_say_what_the_tests_measure(self):
        self.assertEqual(SL.ACHIEVEMENTS["expect"]["name"], "expect a reply")
        self.assertEqual(SL.ACHIEVEMENTS["turns"]["name"], "time its turn")
        self.assertEqual(SL.RUNG_NAMES[7], "time its turn")
        self.assertEqual(SL.RUNG_NAMES[8], "expect a reply")
        self.assertIn("still-face", SL.ACHIEVEMENTS["expect"]["child_ability"])
        self.assertIn("times its turn", SL.ACHIEVEMENTS["turns"]["child_ability"])
        self.assertNotIn("nine-month", SH.CHILD[8][1])
        self.assertLess(SH.CHILD[8][0], 4.0)

    def test_old_record_lines_keep_counting(self):
        srv = _tree_seat(rungs=(1, 2, 3, 4, 5, 6, 7), limit="all")
        t = {n["id"]: n for n in SH.sheet(srv, fresh=True)["tree"]}
        self.assertEqual(t["turns"]["state"], "earned")
        self.assertEqual(t["turns"]["name"], "time its turn")


# ---- 5. the tree's label -----------------------------------------------------------------

class TestTreeLabel(unittest.TestCase):
    def test_an_allowed_open_skill_waits_on_its_test_not_the_limit(self):
        srv = _tree_seat(rungs=(1, 2, 3, 4, 5, 6), limit="all")
        recs = SL._read(srv)
        progs = {n: fn(recs, {}) for n, fn in SL.PROGRESS.items()}
        for st in ({"allow": "all", "mode": "on"}, {"allow": ["turns"], "mode": "on"},
                   {"allow": "all", "allowed": [], "mode": "on"}):
            t = {n["id"]: n for n in SH.tree(recs, st, progs)}
            self.assertEqual(t["turns"]["state"], "open")
            self.assertEqual(t["turns"]["waiting_on"], "pushes", st)

    def test_one_the_accept_holds_says_so(self):            # #299: the limit's job is the operator's accept
        srv = _tree_seat(rungs=(1, 2, 3, 4, 5), lines=[{"ts": 9.5, "kind": "accept_migrated",
                                                         "ids": ["feel", "reach", "kinds", "patience", "shift"]},
                                                        {"ts": 9.6, "kind": "achieve", "id": "meet", "rung": 6, "skill": 1.0}])
        recs = SL._read(srv)
        t = {n["id"]: n for n in SH.tree(recs, SL.status(srv))}
        self.assertEqual(t["turns"]["state"], "locked")
        self.assertEqual(t["turns"]["waiting_on"], "your accept of meet an other")
        k = {x["id"]: x for x in SH.sheet(srv, fresh=True)["skills"]}
        self.assertTrue(k["turns"]["awaiting_accept"] and k["expect"]["awaiting_accept"])   # on the card, saying so
        self.assertEqual(k["turns"]["waiting_on"], "your accept of meet an other")


# ---- the page --------------------------------------------------------------------------

def _js(expr):
    js = 'const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "panel.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _demo():
    series = [{"ago_h": 4.0 - 0.5 * i, "value": 0.1 + 0.05 * i} for i in range(9)]
    return {"empty": False, "level": 7,
            "header": {"name": "Pip", "level": 7, "rung_name": "expect a reply", "title": "Lv 7 · expect a reply",
                       "learning": ["time its turn"], "next": ["turns"], "mode": "on", "age_h": 61.4,
                       "working_h": 12.5, "cycles": 100, "looks": 50, "lines_sent": 3},
            "attributes": [{"key": "curiosity", "label": "Curiosity", "value": 0.42, "unit": "share", "better": "higher",
                            "what": "<i>share</i> of its looks at things it is still learning", "series": series,
                            "trend_h": 0.05, "detail": {}},
                           {"key": "adaptability", "label": "Adaptability", "value": 4.0, "unit": "looks",
                            "better": "lower", "what": "looks from the first miss to a noticed change",
                            "series": series, "trend_h": None, "detail": {}},
                           {"key": "insight", "label": "Insight", "value": None, "unit": "skill", "better": "higher",
                            "what": "Brier skill", "series": [], "trend_h": None, "detail": {}}],
            "skills": [{"id": "turns", "name": "time its <b>turn</b>", "bar": 0.33, "waiting_on": "contingency",
                        "criteria": [{"key": "pushes", "text": "100/100 turns", "met": True, "frac": 1},
                                     {"key": "contingency", "text": "turns in its window 0.38 vs chance 0.45",
                                      "met": False, "frac": 0}],
                        "trend": {"label": "replies gained by its timing", "value": -0.02, "pass": 0.05,
                                  "trend_h": 0.03, "series": series}},
                       {"id": "kinds", "name": "tell things apart", "bar": 0.5, "waiting_on": "focus",
                        "criteria": [], "trend": None}],
            "slipping": [{"id": "reach", "name": "first reach", "text": "first reach: its test no longer holds (skill)"}],
            "quest": {"rung": 7, "name": "time its turn", "criteria": []},
            "achievements": [{"rung": 1, "name": "feel itself", "at": "2026-10-16T14:07:30+00:00", "working_h": 0.19,
                              "wall_h": 0.19, "skill": 0.5667}],
            "growth": {"rows": [], "now_working_h": 12.5, "now_wall_h": 61.4, "caveat": "an analogy"},
            "time": {"alive_h": 61.4, "down_h": 22.8, "bugs_h": 26.1, "working_h": 12.5, "bugs": []},
            "inventory": [], "toys": [], "relationships": {"others": [], "body": [], "other": []}}


@NODE
class TestCardHtml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _js("L.selfSheetHtml(%s, {}, 1100)" % json.dumps(_demo()))

    def test_the_header_is_lv_and_the_latest_skill(self):
        hdr = self.html.split('<details', 1)[0]
        self.assertIn("Lv 7 · expect a reply", hdr)
        self.assertNotIn("rung", hdr.lower())

    def test_the_traits_with_what_they_measure_and_a_trend(self):
        self.assertIn("Curiosity", self.html)
        self.assertIn("42%", self.html)
        self.assertIn("&lt;i&gt;share&lt;/i&gt;", self.html)              # escaped
        self.assertNotIn("<i>share</i>", self.html)
        self.assertIn("4 looks", self.html)
        self.assertIn("not measured yet", self.html)
        self.assertIn('class="trspark"', self.html)

    def test_learning_now_side_by_side_and_the_slipping_line(self):
        self.assertIn(">Learning now<", self.html)
        self.assertEqual(self.html.count('class="lskill'), 2)
        self.assertIn("time its &lt;b&gt;turn&lt;/b&gt;", self.html)
        self.assertIn("100/100 turns", self.html)
        self.assertIn("waiting on: contingency", self.html)
        self.assertIn("first reach: its test no longer holds", self.html)

    def test_earned_skills_leave_the_card(self):
        self.assertNotIn(">Achievements<", self.html)
        self.assertNotIn("Rung ", self.html)
