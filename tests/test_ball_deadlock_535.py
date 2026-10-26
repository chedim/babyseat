"""#535: the ball deadlocked on self's side. A person's roll was refused while
self had the ball ("it rolls it to you on its turn"), but #333 removed self's
turn-taking roll: self moves the ball only if its own nudge aims at it, so the
ball sat on self's side and nobody could roll it.

The fix (the operator's rules: force self to do nothing, hard-code no
timeout): a person may roll the ball whenever they like, whichever side it is
on, recorded as before. Self gets no new act, aim, timeout, fetch or clock;
its own nudge on the ball works as it did. The dock offers the roll from
either side.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import toybox as TB  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    yield tmp_path / "data"


def test_a_person_rolls_the_ball_while_self_has_it():
    TB.create("ball", "toy:ball", now=100.0)
    assert TB.state("toy:ball")["side"] == "self"
    out = TB.poke("toy:ball", "roll", by="alice", now=120.0)
    assert out["ok"] and out["by"] == "alice" and out["reading"] == {"side": "self"}
    st = TB.state("toy:ball")
    assert (st["side"], st["by"], st["at"]) == ("self", "alice", 120.0)
    assert (st["other_by"], st["other_at"]) == ("alice", 120.0)          # the other's act, as a roll-back
    assert [(e["by"], e["what"], e["side"]) for e in st["log"]] == [("alice", "roll", "self")]
    assert st["counts"].get("alice") == 1
    assert TB.last_person_poke()["by"] == "alice"


def test_a_person_rolls_it_again_and_again():
    TB.create("ball", "toy:ball", now=100.0)
    for i, t in enumerate((110.0, 111.0, 112.0), 1):
        assert TB.poke("toy:ball", "roll", by="alice", now=t)["ok"]
        assert TB.state("toy:ball")["counts"]["alice"] == i
    assert TB.state("toy:ball")["other_at"] == 112.0


def test_no_new_self_act_and_self_never_moves_on_a_persons_roll():
    assert not hasattr(SL, "METHODS")                  # #576: no acts of its own at all (no roll, fetch or timer)
    TB.create("ball", "toy:ball", now=100.0)
    TB.poke("toy:ball", "roll", by="alice", now=120.0)
    assert TB.tick("toy:ball", now=10_000.0) is False                    # still no clock of its own
    assert all(e["by"] != "self" for e in TB.state("toy:ball")["log"])
    assert TB.state("toy:ball")["counts"].get("self", 0) == 0


def test_selfs_nudge_on_the_ball_is_unchanged():
    TB.create("ball", "toy:ball", now=100.0)
    TB.poke("toy:ball", "roll", by="alice", now=110.0)                  # from self's side: it stays with self
    out = TB.nudge("toy:ball", now=120.0)
    assert out["ok"] and out["changed"] and not out["refused"]
    assert TB.state("toy:ball")["side"] == "other"
    again = TB.nudge("toy:ball", now=121.0)                             # self can't roll a ball it hasn't got
    assert again["refused"] and not again["changed"]
    assert TB.poke("toy:ball", "roll", by="alice", now=130.0)["ok"]       # the person's roll-back, as before
    assert TB.state("toy:ball")["side"] == "self"


def test_the_hints_no_longer_say_wait_for_selfs_turn():
    src = (Path(TB.__file__)).read_text()
    assert "it rolls it to you on its turn" not in src
    assert "roll it back when it is on your side" not in src
    assert "only while it is on your side" not in src
    assert "only while it is on your side" not in (UI / "panel.js").read_text()
    assert "rolls it to you on its turn" not in (UI / "panel.js").read_text()


def _js(expr):
    js = "const L=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / "panel.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _ball(side):
    return {"name": "toy:ball", "kind": "ball", "reading": {"side": side}, "health": "ok", "looks": 3,
            "changes": {"self": 2}, "last": {"by": "self", "what": "roll", "age_s": 2}}


@NODE
class TestDockOffersTheRollFromEitherSide(unittest.TestCase):
    def test_with_blurp_the_popover_still_offers_the_roll(self):
        h = _js("L.toyPopoverHtml(%s, null)" % json.dumps(_ball("self")))
        self.assertIn('data-poke-action="roll"', h)
        self.assertIn('data-poke-toy="toy:ball"', h)
        self.assertIn("Blurp has the ball", h)
        self.assertNotIn("on its turn", h)

    def test_on_your_side_it_is_roll_it_back(self):
        h = _js("L.toyPopoverHtml(%s, null)" % json.dumps(_ball("other")))
        self.assertIn('data-poke-action="roll"', h)
        self.assertIn("roll it back", h)

    def test_the_glyph_with_blurp_does_not_say_wait(self):
        lab = _js("L.toyGlyph(%s).label" % json.dumps(_ball("self")))
        self.assertIn("with Blurp", lab)
        self.assertNotIn("its turn next", lab)
