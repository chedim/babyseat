"""#470: the project decision map. A card is a Decision entity with a status
(open -> in_flight -> decided) that asks for ONE typed variable, arrives with
the agent's pick, and only the operator answers it: the answer route sets the
value and appends the decision to the operator ledger. entity_edit refuses
the answer-owned keys, so an agent can ask but never decide."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


class TestValidate(unittest.TestCase):
    def test_int_float_bool_string(self):
        self.assertEqual(DM.validate({"type": "int"}, "1800"), (1800, None))
        self.assertEqual(DM.validate({"type": "float"}, "0.5"), (0.5, None))
        self.assertEqual(DM.validate({"type": "bool"}, "yes"), (True, None))
        self.assertEqual(DM.validate({"type": "bool"}, False), (False, None))
        self.assertEqual(DM.validate({"type": "string"}, "  bow-first "), ("bow-first", None))

    def test_option_must_be_listed(self):
        var = {"type": "option", "options": ["one PR", "three PRs"]}
        self.assertEqual(DM.validate(var, "three PRs"), ("three PRs", None))
        v, err = DM.validate(var, "five PRs")
        self.assertIsNone(v)
        self.assertIn("one of", err)

    def test_bad_values_are_refused_out_loud(self):
        for var, val in (({"type": "int"}, "soon"), ({"type": "int"}, True),
                         ({"type": "bool"}, "maybe"), ({"type": "string"}, "  "),
                         ({"type": "matrix"}, 1), ({}, 1)):
            v, err = DM.validate(var, val)
            self.assertIsNone(v, (var, val))
            self.assertTrue(err, (var, val))


class TestCards(unittest.TestCase):
    def test_cards_and_edges(self):
        est, _ = _store()
        a = _card(est, "turn time limit", var={"type": "int", "unit": "s"}, pick=1800)
        b = _card(est, "release when", var={"type": "string"})
        c = _card(est, "banner window", status="in_flight", var={"type": "int"}, pick=3)
        est.edit(b, properties={"blocks": [a]})
        est.edit(c, properties={"carried_by": ["PR #468"]})
        legacy = est.add(cls="Decision", name="old choice", summary="kept 300 s")["eid"]
        est.add(cls="Component", name="conv_banner.py")
        m = DM.board(est)
        by = {x["eid"]: x for x in m["cards"]}
        self.assertEqual(set(by), {a, b, c, legacy})
        self.assertEqual(by[a]["status"], "open")
        self.assertEqual(by[a]["title"], "turn time limit")
        self.assertEqual(by[a]["description"], "about turn time limit")
        self.assertEqual(by[a]["pick"], 1800)
        self.assertEqual(by[a]["var"]["type"], "int")
        self.assertEqual(by[c]["status"], "in_flight")
        self.assertEqual(by[legacy]["status"], "decided")      # a Decision with no status
        self.assertIn({"from": b, "to": a, "rel": "blocks"}, m["edges"])
        self.assertIn({"from": c, "to": "PR #468", "rel": "carried_by"}, m["edges"])


class TestAnswer(unittest.TestCase):
    def test_answer_sets_the_value_and_writes_the_ledger(self):
        est, led = _store()
        a = _card(est, "turn time limit", var={"type": "int", "unit": "s"}, pick=1800)
        res = DM.answer(est, led, a, "1800", conv="ses_x")
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
        res = DM.answer(est, led, a, "soon")
        self.assertFalse(res["ok"])
        self.assertEqual(est.docs[a].properties["status"], "open")
        self.assertEqual(led.entries(), [])

    def test_only_cards_that_are_not_decided_take_an_answer(self):
        est, led = _store()
        a = _card(est, "turn time limit", var={"type": "int"})
        self.assertTrue(DM.answer(est, led, a, 300)["ok"])
        again = DM.answer(est, led, a, 600)
        self.assertFalse(again["ok"])
        self.assertIn("decided", again["error"])
        comp = est.add(cls="Component", name="conv_banner.py")["eid"]
        self.assertFalse(DM.answer(est, led, comp, 1)["ok"])
        self.assertFalse(DM.answer(est, led, "no such card", 1)["ok"])


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


class TestRoutes(unittest.TestCase):
    def _h(self, path, method, body=None, owner=True):
        from membrane import lanes as L
        if not hasattr(self, "srv"):
            est, led = _store()
            self.srv = types.SimpleNamespace(entity_store=est, user_ledger=led,
                                             _last_request_ts=0, paused=False)
            self.card = _card(est, "turn time limit", var={"type": "int"}, pick=1800)
        h = L.MembraneOpenAIHandler.__new__(L.MembraneOpenAIHandler)
        h.server, h.path, h.command, h.headers = self.srv, path, method, {}
        out = []
        h._json_out = lambda obj, *a, **k: out.append(obj)
        h._json_err_raw = lambda code, msg: out.append({"error": msg, "code": code})
        h._json_body = lambda: dict(body or {})
        with mock.patch("membrane.seatauth.owner", return_value=owner), \
                mock.patch("membrane.jwtauth.send_unauthorized",
                           side_effect=lambda hh: out.append({"code": 401})):
            (h.do_GET if method == "GET" else h.do_POST)()
        return out[-1]

    def test_board_route(self):
        out = self._h("/membrane/decisions", "GET")
        self.assertEqual(out["cards"][0]["title"], "turn time limit")

    def test_answer_route(self):
        self._h("/membrane/decisions", "GET")
        out = self._h("/membrane/decision/answer", "POST",
                      {"card": self.card, "value": 1800, "conv": "ses_y"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.srv.entity_store.docs[self.card].properties["value"], 1800)

    def test_only_the_owner_answers(self):
        self._h("/membrane/decisions", "GET")
        out = self._h("/membrane/decision/answer", "POST",
                      {"card": self.card, "value": 1800}, owner=False)
        self.assertEqual(out["code"], 401)
        self.assertEqual(self.srv.entity_store.docs[self.card].properties["status"], "open")


if __name__ == "__main__":
    unittest.main()
