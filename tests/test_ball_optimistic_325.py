"""#325: rolling the ball back repaints the dock at once. toyAfterPoke (panel.js)
gives the toys the dock paints: before the seat answers, the expected result (a
rolled-back ball is on self's side); after, the seat's own reading; the rest of
the toys untouched and the input never mutated."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")

TOYS = [{"name": "toy:ball", "kind": "ball", "reading": {"side": "other"}, "by": "self"},
        {"name": "toy:die", "kind": "die", "reading": {"value": 3}}]


def _p(expr):
    js = "const L=require(%r); const T=%s; process.stdout.write(JSON.stringify(%s))" % (
        str(UI / "panel.js"), json.dumps(TOYS), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
class TestBallRollsBackAtOnce(unittest.TestCase):
    def test_before_the_seat_answers_the_ball_is_on_selfs_side(self):
        out = _p('L.toyAfterPoke(T, "toy:ball", "roll", null)')
        self.assertEqual(out[0]["reading"]["side"], "self")
        self.assertEqual(out[1], TOYS[1])

    def test_the_glyph_says_so(self):
        out = _p('L.toyGlyph(L.toyAfterPoke(T, "toy:ball", "roll", null)[0]).label')
        self.assertNotIn("roll it back", out)

    def test_the_seats_reading_wins_when_it_answers(self):
        out = _p('L.toyAfterPoke(T, "toy:die", "roll", {"value": 6})')
        self.assertEqual(out[1]["reading"]["value"], 6)
        self.assertEqual(out[0], TOYS[0])

    def test_an_unknown_guess_changes_nothing(self):
        self.assertEqual(_p('L.toyAfterPoke(T, "toy:die", "roll", null)'), TOYS)

    def test_the_input_is_not_mutated(self):
        self.assertEqual(_p('(L.toyAfterPoke(T, "toy:ball", "roll", null), T)'), TOYS)


class TestHomePaintsBeforeItWaits(unittest.TestCase):
    def test_the_click_paints_before_the_poke_and_restores_on_failure(self):
        src = (UI / "home.js").read_text()
        body = src[src.index("async _onToyPoke(e)"):]
        body = body[:body.index("\n    }\n")]
        guess = body.index("_toyAfterPoke(")
        self.assertLess(guess, body.index("await c._toyPoke("), "the dock repaints before the poke's round trip")
        self.assertIn("this._paintDock(before)", body, "a failed poke puts the dock back")
