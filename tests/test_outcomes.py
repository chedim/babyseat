"""THE SHADOW GATE (2026-10-11, outcome learning phase 2; Argus's review
adopted): the field guesses, blind, whether Argus will ignore each
gateable watch wake, then learns from his verdict. It skips nothing; its
guess never reaches him; exempt kinds are never predicted on; the history
backfill is booked apart from the live record."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import outcomes as OC  # noqa: E402
from membrane import wakes as WK  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

LIGHT = MEMBRANE_NS + "ha:light.aft_head_light"
T0 = 1_800_000_000.0


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)

    def take(self, *a, **k):
        return None

    def depth(self):
        return len(self.items)


class _Srv:
    def __init__(self, reply="ignored: nothing new"):
        d = Path(tempfile.mkdtemp())
        self._models_path = str(d / "models.json")
        self.entity_store = EntityStore(path=str(d / "e.json"), shared=False)
        self.entity_store._srv = self
        e = QuantumEntity(eid=LIGHT, cls="Component", properties={"name": "aft head light",
                                                                     "escalate_when": "it stays off"})   # #729: a triage pass
        self.entity_store.docs[e.eid] = e
        self.loops = WA.LoopRunner(max_loops=3)
        self.events_pool = _Pool()
        self._operator_last_ts = 0.0
        self.reply = reply
        self.texts = []
        self.create_loop_session = lambda agent, title=None: "ses_loop"

        def run(sid, text, agent):
            self.texts.append(text)
            return {"text": self.reply}
        self.run_loop_turn = run


def _wake(i, state=None):
    return {"kind": "getter-applied", "entity": LIGHT, "event_id": f"g{i}", "state": state or {"state": "off"}}


class TestTheScorer(unittest.TestCase):
    def test_learns_to_predict_ignore_and_scores_itself(self):
        g = OC.ShadowGate(None)
        for i in range(12):
            p = g.predict(_wake(i), "Component", now=T0 + i)
            g.learn(p, "ignored", state={"state": "off"}, entity=LIGHT, now=T0 + i)
        p = g.predict(_wake(99), "Component", now=T0 + 100)
        self.assertEqual(p["predict"], "ignore")
        self.assertGreaterEqual(p["p_ignore"], OC.P_IGN)
        g.learn(p, "done", now=T0 + 100)           # he reacted: the expensive error, counted
        r = g.report()["live"]["getter-applied"]
        self.assertEqual(r["fp"], 1)
        self.assertFalse(r["meets_bar"])

    def test_what_changed_splits_the_cases(self):
        g = OC.ShadowGate(None)
        g.last_state[LIGHT] = {"state": "off", "brightness": 10, "updated_ts": 1}
        p = g.predict({"kind": "getter-applied", "entity": LIGHT, "state": {"state": "on", "brightness": 10,
                                                                            "updated_ts": 2}})
        self.assertEqual(p["sig"], "state")             # the clock key doesn't count
        self.assertEqual(OC.changed_sig({"a": 1}, {"a": 1}), "(nothing)")

    def test_counts_decay(self):
        g = OC.ShadowGate(None)
        p = g.predict(_wake(0), now=T0)
        g.learn(p, "ignored", now=T0)
        _, n = g._get(p["keys"][0], T0 + OC.HALF_LIFE_S)
        self.assertAlmostEqual(n, 0.5, places=3)

    def test_wilson(self):
        self.assertLess(OC.wilson_lb(30, 30), 0.95)      # 30/30 is NOT the bar
        self.assertGreaterEqual(OC.wilson_lb(100, 100), 0.95)


class TestTheInvariants(unittest.TestCase):
    def test_exempt_kinds_are_never_predicted_on(self):
        g = OC.ShadowGate(None)
        for ev in ({"kind": "operator-message"}, {"kind": "intent-planted", "entity": "g"},
                   {"kind": "llm-call", "entity": LIGHT}, {"kind": "restart-pending"},
                   {"kind": "getter-applied", "entity": LIGHT, "readonly": True}):
            self.assertTrue(OC.exempt(ev), ev)
            self.assertIsNone(g.predict(ev), ev)
        self.assertFalse(OC.exempt(_wake(1)))

    def test_no_label_without_a_loop(self):
        for o in ("folded", "held", "requeue", "error", "aborted", "skip"):
            self.assertIsNone(OC.label_of(o))


class TestInTheLoop(unittest.TestCase):
    def setUp(self):
        WK._seen.clear()
        WK._last_wake.clear()
        OC._GATES.clear()

    def test_blind_and_skips_nothing(self):
        srv = _Srv()
        g = OC.of(srv)
        g.counts[f"e|getter-applied|{LIGHT}"] = [50.0, 50.0, __import__("time").time()]   # it would say ignore
        WA._LoopDrainer(srv).handle(_wake(1))
        self.assertEqual(len(srv.texts), 1)                       # the turn still ran: nothing skipped
        self.assertNotIn("ignore", srv.texts[0].split("Decide:")[0].lower().replace("ignored", ""))
        self.assertNotIn("shadow", srv.texts[0].lower())
        row = [json.loads(l) for l in open(WK.path_of(srv))][-1]
        self.assertEqual(row["shadow"]["predict"], "ignore")      # the guess lives in the ledger
        self.assertEqual(g.record["live"]["getter-applied"]["tp"], 1)

    def test_backfill_is_booked_apart(self):
        g = OC.ShadowGate(None)
        rows = [{"ts": T0 + i, "kind": "getter-applied", "entity": LIGHT, "verdict": "ignored"} for i in range(20)]
        rows.append({"ts": T0 + 30, "kind": "goal", "entity": "x", "verdict": "done"})
        self.assertEqual(g.replay(rows), 20)
        self.assertTrue(g.seeded)
        self.assertEqual(g.record["live"], {})
        self.assertEqual(g.record["replay"]["getter-applied"]["labelled"], 20)
        _, n = g._get(f"e|getter-applied|{LIGHT}", T0 + 20)
        self.assertAlmostEqual(n, 20 * OC.BACKFILL_WEIGHT, delta=0.1)

    def test_paint_and_persist(self):
        srv = _Srv()
        g = OC.of(srv)
        p = g.predict(_wake(1))
        g.learn(p, "ignored")
        OC.paint(srv)
        ent = srv.entity_store.docs[MEMBRANE_NS + "outcomes"]
        self.assertIn("shadow gate", ent.properties["state"])
        OC._GATES.clear()
        self.assertEqual(OC.of(srv).record["live"]["getter-applied"]["labelled"], 1)   # reloaded


if __name__ == "__main__":
    unittest.main()
