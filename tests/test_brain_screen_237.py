"""#237 (part of #161): the brain on the screen -- membrane home shows self
thinking. What it looks at now (a beam + a label + its answer), a fading trail
of its last looks, a pulse when rung 5 notices a shift, its waits / pushes /
level-ups, and a one-line caption that narrates the newest record line in
plain words (citing the line, never a claim the record doesn't hold).

Plus the camera: ◎ self frames self's own neighbourhood (its distance fitted
to the amoeba + its recent look targets + its aim), and 👁 self's eyes looks
out from the amoeba toward what it reaches for. The person self meets is a
labelled presence ("you").

The pure logic lives in home.js (HomeLogic) and is run here with node; the
wiring is asserted on the source.
"""
import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"


def _js(expr):
    js = ('const H=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "home.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _cap(rec, me="dmitrii"):
    return _js("H.brainCaption(%s, %s)" % (json.dumps(rec), json.dumps(me)))


NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


# ---- the labels: friendly, never invented ----------------------------------------

@NODE
class TestLabels(unittest.TestCase):
    def _l(self, eid, me="dmitrii"):
        return _js("H.brainLabel(%s, %s)" % (json.dumps(eid), json.dumps(me)))

    def test_the_person_self_meets(self):
        self.assertEqual(self._l("chat:in:dmitrii"), "you")
        self.assertEqual(self._l("chat:out:dmitrii"), "you")
        self.assertEqual(self._l("chat:in:roomata-estorsky"), "roomata-estorsky")
        self.assertEqual(self._l("chat:in:operator"), "the operator")

    def test_the_eye_toys_and_words(self):
        self.assertEqual(self._l("i"), "the eye")
        self.assertEqual(self._l("membrane::Component:toy:dice"), "dice")
        self.assertEqual(self._l("cradle:mobile"), "mobile")
        self.assertEqual(self._l("w:burst"), "w:burst")
        self.assertEqual(self._l("membrane::Component:w:prime"), "w:prime")
        self.assertEqual(self._l(None), "?")


# ---- the caption: plain words, true to the record line --------------------------

@NODE
class TestCaption(unittest.TestCase):
    def test_a_look(self):
        self.assertEqual(_cap({"kind": "reach", "eid": "w:burst", "answer": "changed", "p": 0.62}),
                         "Blurp looked at w:burst — it changed (it expected 0.62)")
        self.assertEqual(_cap({"kind": "reach", "eid": "i", "answer": "unchanged", "p": 0.1}),
                         "Blurp looked at the eye — no change (it expected 0.1)")
        self.assertEqual(_cap({"kind": "reach", "eid": "toy:dice", "answer": "none", "why": "throttled", "p": 0.4}),
                         "Blurp reached for dice — no answer (throttled)")

    def test_a_timed_look_says_so(self):
        self.assertEqual(_cap({"kind": "reach", "eid": "w:burst", "answer": "changed", "p": 0.7, "timed": True}),
                         "Blurp looked at w:burst when it judged a change likely — it changed (it expected 0.7)")

    def test_a_reply_check(self):
        self.assertEqual(_cap({"kind": "reach", "eid": "chat:in:dmitrii", "reply": True, "answer": "changed", "p": 0.4}),
                         "Blurp checked whether you answered — you did (it expected 0.4)")
        self.assertEqual(_cap({"kind": "reach", "eid": "chat:in:bob", "reply": True, "answer": "unchanged", "p": 0.4}),
                         "Blurp checked whether bob answered — not yet (it expected 0.4)")

    def test_a_push(self):
        self.assertEqual(_cap({"kind": "push", "to": "chat:in:dmitrii", "asked": True}),
                         "Blurp said hello to you and asked the model if you'll answer")
        self.assertEqual(_cap({"kind": "push", "to": "chat:in:bob"}), "Blurp said hello to bob")
        self.assertEqual(_cap({"kind": "push", "to": "chat:in:bob", "asked": True}),
                         "Blurp said hello to bob and asked the model if they'll answer")

    def test_a_shift_a_wait_a_rung(self):
        self.assertEqual(_cap({"kind": "shift", "eid": "w:prime", "was": 0.8, "now": 0.0}),
                         "Blurp noticed w:prime stopped behaving like itself (it changed 0.8 of the time, now 0)")
        self.assertEqual(_cap({"kind": "shift", "eid": "w:prime"}), "Blurp noticed w:prime stopped behaving like itself")
        self.assertEqual(_cap({"kind": "wait", "n": 3}), "Blurp waited — nothing was due")
        self.assertEqual(_cap({"kind": "rung", "rung": 7}), "level up: rung 7 · take turns")   # #258: the ladder flip
        self.assertEqual(_cap({"kind": "rung", "rung": 8}), "level up: rung 8 · understand an other")

    def test_the_rest(self):
        self.assertEqual(_cap({"kind": "cycle", "act": "move", "gap": 0.0312}),
                         "Blurp moved — it landed 0.031 from where it expected")
        self.assertEqual(_cap({"kind": "error", "act": "reach", "why": "boom"}), "Blurp hit an error in reach: boom")
        self.assertEqual(_cap({"kind": "birth"}), "Blurp was born")
        self.assertEqual(_cap({"kind": "audit", "phase": "start", "eid": "w:x"}), "Blurp is auditing w:x (start)")
        self.assertEqual(_cap({"kind": "zzz"}), "Blurp: zzz")

    def test_it_never_claims_an_expectation_the_line_lacks(self):
        self.assertEqual(_cap({"kind": "reach", "eid": "w:burst", "answer": "changed"}), "Blurp looked at w:burst — it changed")


# ---- the feed: deduped, oldest first; the trail: capped, fading -----------------

@NODE
class TestFeed(unittest.TestCase):
    def test_new_lines_oldest_first_and_deduped(self):
        lines = [{"kind": "reach", "ts": 3, "n": 3, "eid": "a"}, {"kind": "wait", "ts": 2, "n": 2},
                 {"kind": "shift", "ts": 2, "eid": "a"}]
        out = _js("H.brainFresh(%s, [])" % json.dumps(lines))
        self.assertEqual([r["kind"] for r in out["fresh"]], ["shift", "wait", "reach"])
        again = _js("H.brainFresh(%s, %s)" % (json.dumps(lines), json.dumps(out["seen"])))
        self.assertEqual(again["fresh"], [])
        more = [{"kind": "wait", "ts": 4, "n": 4}] + lines
        self.assertEqual(len(_js("H.brainFresh(%s, %s)" % (json.dumps(more), json.dumps(out["seen"])))["fresh"]), 1)

    def test_seen_is_capped(self):
        lines = [{"kind": "wait", "ts": i, "n": i} for i in range(500)]
        self.assertLessEqual(len(_js("H.brainFresh(%s, [])" % json.dumps(lines))["seen"]), 300)

    def test_the_trail_keeps_the_last_8_looks(self):
        t = _js("(() => { let t = []; for (let i = 0; i < 12; i++) t = H.brainTrail(t, {kind: 'reach', eid: 'w:' + i, answer: 'changed'});"
                " return H.brainTrail(t, {kind: 'wait'}); })()")
        self.assertEqual([x["eid"] for x in t], ["w:%d" % i for i in range(4, 12)])

    def test_the_trail_fades_with_age(self):
        a = [_js("H.trailAlpha(%d, 8)" % i) for i in range(8)]
        self.assertEqual(a, sorted(a))
        self.assertGreater(a[-1], 0.8)
        self.assertLess(a[0], 0.3)


# ---- ◎ self: the frame fits self's neighbourhood ---------------------------------

@NODE
class TestFraming(unittest.TestCase):
    def test_the_radius_is_the_farthest_point(self):
        self.assertEqual(_js("H.frameRadius([0,0,0], [[3,4,0], null, [1,0,0]])"), 5)
        self.assertEqual(_js("H.frameRadius([0,0,0], [])"), 0)

    def test_local_fiddling_is_close_far_reaching_is_wide(self):
        near = _js("H.frameDistance(20)")
        far = _js("H.frameDistance(200)")
        self.assertLess(near, 160)                      # much closer than the old fixed 420
        self.assertLess(_js("H.frameDistance(150)"), 420)   # a typical neighbourhood is still closer
        self.assertGreater(far, near)
        # the whole extent fits the field of view (55 deg), with a margin
        # (up to the close cap; the far reach is clamped since the camera-close fix)
        self.assertGreaterEqual(far * math.tan(math.radians(27.5)), 200 * 1.1)
        self.assertEqual(_js("H.frameDistance(1e9)"), _js("H.frameDistance(1e10)"))   # clamped
        self.assertEqual(_js("H.frameDistance(0)"), _js("H.frameDistance(1)"))        # a floor

    def test_damping_is_smooth_and_reduced_motion_steps(self):
        self.assertAlmostEqual(_js("H.dampTo(100, 200, 0.1, 1, false)"), 100 + 100 * (1 - math.exp(-0.1)), places=6)
        self.assertEqual(_js("H.dampTo(100, 200, 0.1, 1, true)"), 200)
        self.assertEqual(_js("H.dampTo(100, 200, 0, 1, false)"), 100)

    def test_the_frame_includes_the_look_steps_and_the_aim(self):
        st = {"pos": [0, 0, 0], "look": {"p": [0, 50, 0], "age": 100}, "aim": [0, 0, -80], "steps": [[5, 0, 0]]}
        self.assertEqual(_js("H.selfFrame(%s).radius" % json.dumps(st)), 80)


# ---- 👁 self's eyes ---------------------------------------------------------------

@NODE
class TestEyes(unittest.TestCase):
    def test_a_mode_beside_self(self):
        self.assertEqual(_js("H.FOLLOW_MODES"), ["activity", "seat", "you", "self", "eyes"])
        self.assertEqual(_js("H.nextMode('self')"), "eyes")
        self.assertEqual(_js("H.nextMode('eyes')"), "activity")
        self.assertEqual(_js("H.followLabel('eyes')"), "👁 Blurp's eyes")       # #277: the name it goes by
        self.assertEqual(_js("H.followLabel('self')"), "◎ Blurp")

    def test_a_manual_orbit_leaves_the_eyes(self):
        self.assertEqual(_js("H.modeAfterTouch('eyes')"), "self")
        self.assertEqual(_js("H.modeAfterTouch('activity')"), "activity")

    def test_what_the_eyes_look_at(self):
        base = {"pos": [0, 0, 0], "aim": [0, 0, 100], "trail": [[-10, 0, 0], [0, 0, 0]]}
        look = dict(base, look={"p": [100, 0, 0], "age": 1000})
        self.assertEqual(_js("H.eyesTarget(%s).kind" % json.dumps(look)), "look")
        stale = dict(base, look={"p": [100, 0, 0], "age": 60000})
        self.assertEqual(_js("H.eyesTarget(%s).kind" % json.dumps(stale)), "aim")
        unknown = dict(base, look={"p": None, "age": 10})
        self.assertEqual(_js("H.eyesTarget(%s).kind" % json.dumps(unknown)), "aim")
        heading = {"pos": [0, 0, 0], "trail": [[-10, 0, 0], [0, 0, 0]]}
        h = _js("H.eyesTarget(%s)" % json.dumps(heading))
        self.assertEqual(h["kind"], "heading")
        self.assertGreater(h["p"][0], 0)
        self.assertEqual(_js("H.eyesTarget(%s).kind" % json.dumps({"pos": [50, 0, 0], "trail": []})), "centre")
        self.assertIsNone(_js("H.eyesTarget({pos: null})"))

    def test_turning_is_smooth(self):
        d = _js("H.turnToward([1,0,0], [0,1,0], 0.5)")
        self.assertAlmostEqual(math.hypot(*d), 1, places=6)
        self.assertAlmostEqual(d[0], d[1], places=6)
        self.assertEqual(_js("H.turnToward([1,0,0], [0,2,0], 1)"), [0, 1, 0])

    def test_the_camera_sits_at_the_amoeba_looking_along_dir(self):
        o = _js("H.eyesOrbit([10,20,30], [0,0,1], 6)")
        cam = [o["target"][0] + o["r"] * math.sin(o["phi"]) * math.sin(o["theta"]),
               o["target"][1] + o["r"] * math.cos(o["phi"]),
               o["target"][2] + o["r"] * math.sin(o["phi"]) * math.cos(o["theta"])]
        for a, b in zip(cam, [10, 20, 24]):
            self.assertAlmostEqual(a, b, places=4)
        self.assertGreater(o["target"][2], 30)          # looking forward


# ---- the labels round self: laid out, clamped, readable (review of #241) ---------

def _f3(expr):
    js = ('const F=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "field3d.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _boxes_overlap(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


@NODE
class TestFieldArea(unittest.TestCase):
    def test_nothing_open_is_the_whole_canvas(self):
        self.assertEqual(_f3("F.fieldArea(1440, 900, [])"), {"x0": 0, "y0": 0, "x1": 1440, "y1": 900})

    def test_a_chat_column_on_the_right_cuts_the_right(self):
        a = _f3("F.fieldArea(1440, 900, [{x: 968, y: 12, w: 460, h: 876}])")
        self.assertEqual((a["x0"], a["x1"], a["y0"], a["y1"]), (0, 968, 0, 900))

    def test_the_baby_panel_cuts_the_left_and_the_phone_chat_the_bottom(self):
        a = _f3("F.fieldArea(1280, 800, [{x: 48, y: 64, w: 360, h: 660}])")
        self.assertEqual((a["x0"], a["x1"]), (408, 1280))
        p = _f3("F.fieldArea(390, 844, [{x: 8, y: 364, w: 374, h: 472}])")
        self.assertEqual((p["x0"], p["x1"], p["y0"], p["y1"]), (0, 390, 0, 364))

    def test_a_top_bar_cuts_the_top(self):
        a = _f3("F.fieldArea(1440, 900, [{x: 12, y: 12, w: 940, h: 66}, {x: 968, y: 12, w: 460, h: 876}])")
        self.assertEqual((a["y0"], a["x1"]), (78, 968))


@NODE
class TestLabelLayout(unittest.TestCase):
    AREA = {"x0": 0, "y0": 0, "x1": 1000, "y1": 800}

    def _lay(self, items, area=None):
        return _f3("F.layoutLabels(%s, %s)" % (json.dumps(items), json.dumps(area or self.AREA)))

    def test_labels_on_one_spot_do_not_overlap(self):
        items = [{"key": k, "ax": 500, "ay": 400, "w": 120, "h": 16, "prio": p}
                 for k, p in (("tag", 5), ("expects", 1), ("pushes", 1), ("wants", 1), ("look", 4))]
        out = self._lay(items)
        boxes = [dict(out[i["key"]], w=i["w"], h=i["h"]) for i in items if not out[i["key"]]["hidden"]]
        self.assertGreaterEqual(len(boxes), 4)
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                self.assertFalse(_boxes_overlap(boxes[i], boxes[j]), (boxes[i], boxes[j]))

    def test_the_most_important_keeps_its_place(self):
        out = self._lay([{"key": "low", "ax": 500, "ay": 400, "w": 100, "h": 16, "prio": 0},
                         {"key": "tag", "ax": 500, "ay": 400, "w": 100, "h": 16, "prio": 9, "dx": 14, "dy": -6}])
        self.assertEqual((out["tag"]["x"], out["tag"]["y"]), (514, 394))
        self.assertNotEqual(out["low"]["y"], 394)

    def test_an_optional_label_with_no_room_is_hidden_not_piled(self):
        items = [{"key": "k%d" % i, "ax": 50, "ay": 10, "w": 40, "h": 16, "prio": 1, "optional": True} for i in range(8)]
        out = self._lay(items, {"x0": 0, "y0": 0, "x1": 120, "y1": 40})
        self.assertTrue(any(v["hidden"] for v in out.values()))

    def test_a_label_stays_inside_the_area(self):
        out = self._lay([{"key": "noticed", "ax": 950, "ay": 300, "w": 260, "h": 16, "prio": 3}],
                        {"x0": 0, "y0": 0, "x1": 968, "y1": 900})
        self.assertLessEqual(out["noticed"]["x"] + 260, 968)
        self.assertFalse(out["noticed"]["out"])                       # the anchor itself is visible

    def test_an_anchor_behind_a_panel_goes_to_the_edge_with_an_arrow(self):
        out = self._lay([{"key": "noticed", "ax": 1200, "ay": 300, "w": 200, "h": 16, "prio": 3}],
                        {"x0": 0, "y0": 0, "x1": 968, "y1": 900})
        n = out["noticed"]
        self.assertTrue(n["out"])
        self.assertEqual(n["arrow"], "→")
        self.assertLessEqual(n["x"] + 200, 968)

    def test_an_optional_label_whose_anchor_is_hidden_is_not_pinned_to_the_edge(self):
        out = self._lay([{"key": "mark", "ax": 1200, "ay": 300, "w": 60, "h": 16, "prio": 1, "optional": True}],
                        {"x0": 0, "y0": 0, "x1": 968, "y1": 900})
        self.assertTrue(out["mark"]["hidden"])

    def test_arrows(self):
        self.assertEqual(_f3("F.arrowFor(0, 0, 10, 0)"), "→")
        self.assertEqual(_f3("F.arrowFor(0, 0, 0, 10)"), "↓")
        self.assertEqual(_f3("F.arrowFor(0, 0, -10, -10)"), "↖")


@NODE
class TestSelfTag(unittest.TestCase):
    L = {"rung": 6, "bar": 0.64, "skill": 0.41, "split": {"chat:in:x": "other"}, "meets": {"other": "chat:in:x"}}

    def test_short_while_the_camera_follows_self(self):
        self.assertEqual(_f3("F.selfTag(%s, true)" % json.dumps(self.L)), "Blurp · Lv 6 · 64%")

    def test_long_otherwise(self):
        t = _f3("F.selfTag(%s, false)" % json.dumps(self.L))
        self.assertTrue(t.startswith("Blurp · rung 6 · 64% · skill 0.41"), t)
        self.assertIn("meets an other", t)


class TestLabelLook(unittest.TestCase):
    def test_labels_have_a_theme_token_backdrop(self):
        f3 = (UI / "field3d.js").read_text()
        self.assertIn("SELF_LABEL_CSS", f3)
        self.assertIn("var(--card-background-color", f3.split("SELF_LABEL_CSS", 1)[1].split(";\n", 1)[0])
        self.assertIn("var(--primary-text-color", f3.split("SELF_LABEL_CSS", 1)[1].split(";\n", 1)[0])
        home = (UI / "home.js").read_text()
        self.assertIn("occluders:", home)                           # the panels the labels keep out of
        self.assertIn("selfFollow", home)


# ---- the wiring --------------------------------------------------------------------

class TestWiring(unittest.TestCase):
    def setUp(self):
        self.home = (UI / "home.js").read_text()
        self.f3 = (UI / "field3d.js").read_text()

    def test_the_caption_is_on_the_page(self):
        self.assertIn('class="brainline', self.home)
        self.assertIn("_paintBrain(", self.home)

    def test_it_polls_only_while_visible(self):
        body = self.home.split("THE BRAIN ON THE SCREEN", 1)[1].split("// ---- end of the brain", 1)[0]
        self.assertIn("document.hidden", body)
        self.assertIn("clearInterval(this._brainT)", self.home)
        self.assertIn("_selfRecent(", body)
        self.assertNotIn("POST", body)                    # read-only
        self.assertNotIn("_setSelfLimit", body)

    def test_the_viewer_draws_looks_trail_pulse_and_the_other(self):
        for hook in ("  selfLook(", "  selfShift(", "  selfWait(", "  selfLevelUp(", "  setSelfOther(",
                     "BRAIN_TRAIL", "reducedMotion"):
            self.assertIn(hook, self.f3, hook)
        self.assertIn("prefers-reduced-motion", self.f3)

    def test_the_eyes_mode_hides_the_amoeba_and_a_touch_leaves_it(self):
        self.assertIn("selfEyes", self.f3)
        self.assertIn("modeAfterTouch(", self.home)
        self.assertIn('this._mode === "eyes"', self.home)
        self.assertIn("followsNow(", self.home)        # a touch holds ◎ self briefly

    def test_the_docs_describe_it(self):
        doc = (ROOT / "docs" / "selfgrowth.md").read_text()
        for want in ("### The brain on the screen", "trail", "pulse", "caption", "👁 self's eyes", "framing"):
            self.assertIn(want, doc, want)


if __name__ == "__main__":
    unittest.main()
