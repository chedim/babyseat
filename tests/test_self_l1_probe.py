"""#161 RUNG 4 IN THE LAB: what should a motion move? -- the design probe.

Rung 3 gave the motor channel a rate. This file pins the design probe's two
findings, both of which are *negative* for the obvious designs:

  * pulsing the entity the act is ABOUT (its top `coords`) is the densest
    possible channel and still reads as no body: each entity answers only its
    share of a kind's acts, and a body part must answer every act of one kind.
  * pulsing the TOOL the act ran reads as a body at p_act 1.00 -- and it is a
    tautology (we asserted it, the ledger confirmed it), which the shuffled
    null does not catch either. Only the recorder can refuse to write it.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "self_l1"))

import probe as P  # noqa: E402
import replay as R  # noqa: E402


def _acts_with_coords(n=40, kind="entity_read", dt=10.0, t0=1000.0, coord=True):
    """Each act is about a DIFFERENT entity (the argument varies), and every act
    ran the same tool (the invariant)."""
    ev, dl = [], []
    for i in range(n):
        t = t0 + dt * i
        d = {"stream": "observation", "role": "tool", "tool": kind, "ts": t,
             "src": "live"}
        if coord:
            d["coords"] = [[f"f:note{i}.md", 0.9], ["tool:read", 0.1]]
        ev.append((f"v:a{i}", d))
        dl.append({"eid": f"tool:{kind}", "event": f"v:a{i}", "ts": t})
    return ev, dl


class TestWhatTheNaiveChannelsPulse(unittest.TestCase):
    def test_the_argument_channel_pulses_the_entity_the_act_is_about(self):
        ev, dl = P.plant_channel(*_acts_with_coords(n=5), channel="coord")
        eids = {x["eid"] for x in dl if x["event"].startswith("v:probe:")}
        self.assertEqual(eids, {f"f:note{i}.md" for i in range(5)})

    def test_the_tool_channel_pulses_the_tool_the_act_ran(self):
        ev, dl = P.plant_channel(*_acts_with_coords(n=5), channel="tool")
        eids = {x["eid"] for x in dl if x["event"].startswith("v:probe:")}
        self.assertEqual(eids, {"tool:entity_read"})

    def test_the_planted_motion_is_machinery_and_lands_in_the_window(self):
        ev, dl = P.plant_channel(*_acts_with_coords(n=5), channel="tool", delay=0.3)
        planted = dict(ev)["v:probe:tool:0"]
        self.assertTrue(R.is_machine_act(planted))
        self.assertFalse(R.is_narration(planted))
        self.assertAlmostEqual(float(planted["ts"]) - 1000.0, 0.3)

    def test_an_act_with_no_coords_plants_nothing(self):
        ev, dl = P.plant_channel(*_acts_with_coords(n=5, coord=False), channel="coord")
        self.assertEqual([x for x in dl if x["event"].startswith("v:probe:")], [])


class TestWhatTheLedgerMakesOfThem(unittest.TestCase):
    def setUp(self):
        self.ev, self.dl = _acts_with_coords(n=40)

    def test_pulsing_the_argument_leaves_no_body(self):
        # 40 acts, 40 different arguments, one pulse each: every entity answers
        # exactly one act of the kind -- nothing is an invariant of the action
        row = P.probe(self.ev, self.dl, channels=("coord",))[0]
        self.assertEqual(row["body"], [])
        self.assertEqual(row["planted"], 40)

    def test_pulsing_the_tool_reads_as_a_body_because_we_asserted_it(self):
        row = P.probe(self.ev, self.dl, channels=("tool",))[0]
        self.assertEqual(row["body"], ["tool:entity_read"])
        self.assertEqual(row["stats"]["tool:entity_read"]["p_act"], 1.0)

    def test_the_null_does_not_catch_the_tautology(self):
        # the shuffled-act control finds nothing -- for the tautology too. The
        # ledger cannot audit the recorder's bookkeeping from the outside.
        row = P.probe(self.ev, self.dl, channels=("tool",))[0]
        self.assertEqual(row["null_body"], [])
        self.assertTrue(row["body"], "the tautology is there to be caught")

    def test_the_report_calls_itself_a_design_probe(self):
        txt = P.report((self.ev, self.dl))
        self.assertIn("DESIGN PROBE", txt)
        self.assertIn("tautology", txt.lower())


if __name__ == "__main__":
    unittest.main()
