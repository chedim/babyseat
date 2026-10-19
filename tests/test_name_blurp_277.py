"""#277: the operator named self Blurp (2026-10-19).

A display-only rename: the seat owns the name (self_sheet.NAME, carried in the
sheet header and the dock state) and every place the page shows self to a
person says it (panel.js / home.js take it from the seat, Blurp before the
first load). The ids (membrane::self, seat:self), the record's `by: "self"`,
the follow mode value "self" and the code's vocabulary stay.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
UI = ROOT / "membrane" / "ui"

from membrane import self_sheet as SS  # noqa: E402

NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


def _run(path, var, expr):
    js = ('const %s=require(%r); process.stdout.write(JSON.stringify(%s))' % (var, str(path), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _p(expr):
    return _run(UI / "panel.js", "L", expr)


def _h(expr):
    return _run(UI / "home.js", "H", expr)


class TestSeatOwnsTheName(unittest.TestCase):
    def test_the_name(self):
        self.assertEqual(SS.NAME, "Blurp")

    def test_the_dock_carries_it(self):
        self.assertEqual(SS.dock(None)["name"], "Blurp")

    def test_the_ids_stay(self):
        from membrane import toybox as TB
        self.assertIn("self", TB.RESERVED_BY)              # `by: "self"` is the record's, not the name

    def test_the_model_is_told(self):
        doctrine = (ROOT / "doctrine.md").read_text()
        self.assertIn("Blurp", doctrine)
        self.assertNotIn("unnamed loop", doctrine)


@NODE
class TestPanelSaysBlurp(unittest.TestCase):
    def test_the_sheet_header_without_a_name(self):
        html = _p("L.selfSheetHtml(%s, {}, 0)" % json.dumps({"header": {"level": 0}}))
        self.assertIn('<div class="nm">Blurp</div>', html)

    def test_the_empty_sheet(self):
        html = _p("L.selfSheetHtml(%s, {}, 0)" % json.dumps({"empty": True, "header": {"level": 0}}))
        self.assertIn("Blurp has not been switched on yet", html)

    def test_a_locked_toy(self):
        t = {"name": "toy:mirror", "kind": "mirror", "locked": True, "unlock_rung": 9}
        self.assertIn("unlocks when Blurp passes rung 9", _p("L.toyPopoverHtml(%s, null)" % json.dumps(t)))
        self.assertIn("unlocks when Blurp passes rung 9", _p("L.toyDockItem(%s).label" % json.dumps(t)))

    def test_the_ball_with_blurp(self):
        t = {"name": "toy:ball", "kind": "ball", "health": "ok", "reading": {"side": "self"},
             "last": {"by": "self", "what": "roll", "age_s": 3}, "changes": {"self": 2, "world": 0}}
        h = _p("L.toyPopoverHtml(%s, null)" % json.dumps(t))
        self.assertIn("Blurp has the ball", h)
        self.assertIn("last change by Blurp", h)
        self.assertIn("Blurp 2", h)
        self.assertNotIn("self", h.replace('side-self', ''))
        self.assertIn("with Blurp", _p("L.toyDockItem(%s).label" % json.dumps(t)))
        self.assertEqual(_p("L.toyBadge(%s).text" % json.dumps(t)), "Blurp")

    def test_the_roll_back_title(self):
        a = _p("L.toyActions({toy: 'toy:ball', kind: 'ball'})")
        self.assertIn("roll the ball back to Blurp", a[0]["title"])

    def test_the_chooser_title(self):
        st = {"self": {"mode": "off"}}
        self.assertIn("Blurp (#161) is asleep", _p("L.selfView(%s).title" % json.dumps(st)))

    def test_the_setter_renames(self):
        html = _p("(L.setSelfName('Zed'), [L.selfSheetHtml({header: {level: 0}}, {}, 0), L.selfName(), "
                  "L.setSelfName(''), L.selfName()])")
        self.assertIn('<div class="nm">Zed</div>', html[0])
        self.assertEqual(html[1], "Zed")
        self.assertEqual(html[3], "Zed")                   # an empty name changes nothing

    def test_the_header_name_wins(self):
        html = _p("L.selfSheetHtml({header: {name: 'Quux', level: 0}}, {}, 0)")
        self.assertIn('<div class="nm">Quux</div>', html)


@NODE
class TestHomeSaysBlurp(unittest.TestCase):
    def test_the_activity_lines(self):
        self.assertEqual(_h("H.brainCaption({kind: 'birth'}, 'me')"), "Blurp was born")
        self.assertEqual(_h("H.brainCaption({kind: 'wait'}, 'me')"), "Blurp waited — nothing was due")
        self.assertTrue(_h("H.brainCaption({kind: 'reach', eid: 'membrane::toy:die', answer: 'changed'}, 'me')")
                        .startswith("Blurp looked at die"))

    def test_the_follow_labels(self):
        self.assertEqual(_h("H.followLabel('self')"), "◎ Blurp")      # the mode value stays "self"
        self.assertEqual(_h("H.followLabel('eyes')"), "👁 Blurp's eyes")
        self.assertEqual(_h("H.followLabel('seat')"), "◎ seat")

    def test_the_setter_renames(self):
        r = _h("(H.setSelfName('Zed'), [H.brainCaption({kind: 'birth'}), H.followLabel('self')])")
        self.assertEqual(r, ["Zed was born", "◎ Zed"])

    def test_the_static_labels(self):
        home = (UI / "home.js").read_text()
        for old in ('>👶 self<', 'aria-label="self\'s toys', 'title="self\'s toys"', 'aria-label="self\'s character sheet"',
                    'title="self (#161)', '"self was born"'):
            self.assertNotIn(old, home)


if __name__ == "__main__":
    unittest.main()
