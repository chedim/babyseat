"""#470: the project decision map. A card is a Decision entity with a status
(open -> in_flight -> decided) that asks for ONE typed variable, arrives with
the agent's pick, and only the operator answers it: the answer route sets the
value and appends the decision to the operator ledger. entity_edit refuses
the answer-owned keys, so an agent can ask but never decide."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _decide as D  # noqa: E402

from membrane import decision_map as DM  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from membrane.ledger import UserLedger  # noqa: E402


def _store():
    d = Path(tempfile.mkdtemp())
    return (EntityStore(path=str(d / "e.json"), shared=False),
            UserLedger(str(d / "f.ledger.jsonl")))


def _card(est, name, status="open", var=None, pick=None, **props):
    eid = est.add(cls="Decision", name=name, summary=f"about {name}")["eid"]
    p = {"status": status, **props}
    if var is not None:
        p["var"] = var
    if pick is not None:
        p["pick"] = pick
    res = est.edit(eid, properties=p)
    assert res["ok"], res
    return eid




class TestAnswer(unittest.TestCase):
    def test_answer_sets_the_value_and_writes_the_ledger(self):
        est, led = _store()
        a = _card(est, "turn time limit", var={"type": "int", "unit": "s"}, pick=1800)
        res = D.answer(est, led, a, "1800", conv="ses_x")
        self.assertTrue(res["ok"], res)
        p = est.docs[a].properties
        self.assertEqual((p["status"], p["value"], p["set_by"]), ("decided", 1800, "operator"))
        self.assertTrue(p["set_at"])
        last = led.entries()[-1]
        self.assertIn("turn time limit", last["text"])
        self.assertIn("1800 s", last["text"])
        self.assertEqual(last["conv"], "ses_x")

    def test_a_bad_value_changes_nothing(self):
        est, led = _store()
        a = _card(est, "turn time limit", var={"type": "int"})
        res = D.answer(est, led, a, "soon")
        self.assertFalse(res["ok"])
        self.assertEqual(est.docs[a].properties["status"], "open")
        self.assertEqual(led.entries(), [])

    def test_only_cards_that_are_not_decided_take_an_answer(self):
        est, led = _store()
        a = _card(est, "turn time limit", var={"type": "int"})
        self.assertTrue(D.answer(est, led, a, 300)["ok"])
        again = D.answer(est, led, a, 600)
        self.assertFalse(again["ok"])
        self.assertIn("decided", again["error"])
        comp = est.add(cls="Component", name="conv_banner.py")["eid"]
        self.assertFalse(D.answer(est, led, comp, 1)["ok"])
        self.assertFalse(D.answer(est, led, "no such card", 1)["ok"])


class TestAgentsAskButNeverDecide(unittest.TestCase):
    def test_entity_edit_refuses_the_answer_keys_on_a_decision(self):
        est, _ = _store()
        a = _card(est, "turn time limit", var={"type": "int"}, pick=1800)
        res = est.edit(a, properties={"value": 300, "set_by": "operator", "pick": 600})
        self.assertTrue(res["ok"])
        self.assertEqual(res["properties_set"], ["pick"])
        self.assertEqual(set(res["properties_refused"]), {"value", "set_by"})
        res = est.edit(a, properties={"status": "decided"})
        self.assertFalse(res["ok"])
        self.assertEqual(est.docs[a].properties["status"], "open")
        self.assertTrue(est.edit(a, properties={"status": "in_flight"})["ok"])

    def test_other_classes_keep_their_keys(self):
        est, _ = _store()
        eid = est.add(cls="Component", name="dial")["eid"]
        res = est.edit(eid, properties={"value": 3, "status": "decided"})
        self.assertEqual(sorted(res["properties_set"]), ["status", "value"])


if __name__ == "__main__":
    unittest.main()


class TestReviewFixes(unittest.TestCase):
    """Memo's review of #471."""



    def test_a_decided_card_cannot_be_reopened_by_an_edit(self):
        est, led = _store()
        a = _card(est, "turn time limit", var={"type": "int"})
        with D.running(est, led) as srv:
            self.assertTrue(D._REAL_DOOR(srv, a, {"value": 300})["ok"])
            for st in ("open", "in_flight"):
                res = est.edit(a, properties={"status": st})
                self.assertFalse(res["ok"], st)
            p = est.docs[a].properties
            self.assertEqual((p["status"], p["value"]), ("decided", 300))
            self.assertFalse(D._REAL_DOOR(srv, a, {"value": 600})["ok"])
        self.assertEqual(len(led.entries()), 1)



    def test_concurrent_answers_decide_once(self):
        import threading
        est, led = _store()
        a = _card(est, "turn time limit", var={"type": "int"})
        oks = []
        with D.running(est, led) as srv:
            ts = [threading.Thread(target=lambda v=v: oks.append(D._REAL_DOOR(srv, a, {"value": v})["ok"]))
                  for v in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(oks.count(True), 1)
        self.assertEqual(len(led.entries()), 1)

    def test_entity_add_takes_no_properties(self):
        import inspect
        self.assertNotIn("properties", inspect.signature(EntityStore.add).parameters)
