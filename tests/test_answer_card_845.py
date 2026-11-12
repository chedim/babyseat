"""#845: the agent in a person's chat passes their chat answer to a Decision card with
answer_card. The quote must be their own words, the record says it was relayed, and no
loop holds the tool. entity_edit keeps refusing the answer keys."""
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import decision_map as DM  # noqa: E402
from membrane import mcp_server as MCP  # noqa: E402
from membrane import server as SRV  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402
from membrane.ledger import UserLedger  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.est = EntityStore(path=str(d / "e.json"), shared=False)
        self.ledger = UserLedger(str(d / "f.ledger.jsonl"))
        self.srv = types.SimpleNamespace(entity_store=self.est, user_ledger=self.ledger)
        self.card = self.est.add(cls="Decision", name="Max in flight", summary="cap")["eid"]
        self.est.edit(self.card, properties={"status": "open", "var": {"type": "int"}, "pick": 12})

    def _said(self, mid, text, role="user", ts=None):
        self.est.upsert(QuantumEntity(eid=CG.msg_eid(mid), cls=CG.CLASS_MESSAGE, properties={
            "name": text[:80], "role": role, "sid": "ses_1", "mid": mid, "text": text,
            "ts": ts or time.time()}), embedder=None)

    def _props(self):
        return self.est.docs[self.card].properties


class TestRelay(_Base):
    def test_the_persons_words_answer_the_card(self):
        self._said("msg_1", "how many commands? 12 is fine for now, a slider later")
        res = DM.relay(self.srv, self.card, 12, "12 is fine for now")
        self.assertTrue(res["ok"], res)
        p = self._props()
        self.assertEqual((p["value"], p["status"]), (12, "decided"))
        self.assertEqual(p["relayed_by"], "llm")
        self.assertEqual(p["answer_quote"], "12 is fine for now")
        self.assertEqual(p["message"], "msg_1")
        self.assertEqual(p["answer_chat"], "ses_1")

    def test_the_ledger_line_carries_the_quote(self):
        self._said("msg_1", "12 is fine for now")
        DM.relay(self.srv, self.card, 12, "12 is fine for now")
        line = self.ledger.entries()[-1]["text"]
        self.assertIn("Max in flight = 12", line)
        self.assertIn('relayed from the chat: "12 is fine for now"', line)

    def test_invented_words_answer_nothing(self):
        self._said("msg_1", "let me think about that")
        res = DM.relay(self.srv, self.card, 12, "12 is fine")
        self.assertFalse(res["ok"])
        self.assertEqual(self._props()["status"], "open")
        self.assertIsNone(self._props().get("value"))

    def test_no_quote_answers_nothing(self):
        self._said("msg_1", "12")
        for q in ("", "   ", "!!"):
            self.assertFalse(DM.relay(self.srv, self.card, 12, q)["ok"], q)
        self.assertEqual(self._props()["status"], "open")

    def test_part_of_a_word_is_not_the_quote(self):
        self._said("msg_1", "112 maybe")
        self.assertFalse(DM.relay(self.srv, self.card, 12, "12")["ok"])

    def test_the_agents_own_reply_is_not_the_person(self):
        self._said("msg_a", "12 is fine for now", role="assistant")
        self._said("msg_m", "12 is fine for now", role="membrane")
        self.assertFalse(DM.relay(self.srv, self.card, 12, "12 is fine for now")["ok"])

    def test_words_said_before_the_card_was_made_answer_nothing(self):
        self.assertGreater(self._props()["born"], 1000.0)      # a new card knows when it was made
        self._said("msg_old", "12 is fine for now", ts=999.0)
        self.assertFalse(DM.relay(self.srv, self.card, 12, "12 is fine for now")["ok"])

    def test_the_current_turns_words_count(self):
        self.srv._cg_text = {"ses_1": "12 is fine for now"}
        res = DM.relay(self.srv, self.card, 12, "12 is fine for now")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self._props()["answer_chat"], "ses_1")

    def test_the_value_still_fits_the_var(self):
        self._said("msg_1", "a dozen")
        self.assertFalse(DM.relay(self.srv, self.card, "a dozen", "a dozen")["ok"])
        self.assertEqual(self._props()["status"], "open")

    def test_a_decided_card_stays_decided(self):
        self._said("msg_1", "12")
        self.assertTrue(DM.relay(self.srv, self.card, 12, "12")["ok"])
        self._said("msg_2", "13")
        res = DM.relay(self.srv, self.card, 13, "13")
        self.assertFalse(res["ok"])
        self.assertEqual(self._props()["value"], 12)

    def test_the_answer_wakes_a_parked_goal(self):
        put = []
        self.est._events_pool = types.SimpleNamespace(put=put.append)
        self._said("msg_1", "12")
        DM.relay(self.srv, self.card, 12, "12")
        self.assertEqual([e["entity"] for e in put], [self.card])
        self.assertTrue(self._props().get("value_ts"))

    def test_the_message_answers_the_card_in_the_graph(self):
        from unittest import mock
        self._said("msg_1", "12")
        with mock.patch.object(CG, "_rel", return_value=("be", "o")), \
                mock.patch.object(CG, "_edge") as edge:
            DM.relay(self.srv, self.card, 12, "12")
        edge.assert_called_once_with("be", "o", CG.msg_eid("msg_1"), "answers", self.card)

    def test_a_board_answer_is_not_marked_relayed(self):
        self.assertTrue(DM.answer(self.est, self.ledger, self.card, 12)["ok"])
        self.assertNotIn("relayed_by", self._props())

    def test_entity_edit_still_refuses_the_answer_keys(self):
        res = self.est.edit(self.card, properties={"value": 12, "pick": 13})
        self.assertEqual(set(res["properties_refused"]), {"value"})
        self.assertFalse(self.est.edit(self.card, properties={"status": "decided"})["ok"])


class TestLanes(unittest.TestCase):
    def test_only_the_chat_holds_the_tool(self):
        self.assertIn("answer_card", MCP.METHOD_TOOLS)          # a method, reached through `call`
        self.assertIn("answer_card", MCP.TOOL_IMPLS)
        self.assertIn("answer_card", SRV.OPERATOR_TOOLS)
        for s in (SRV.LISTENER_TOOLS, SRV.PLAN_TOOLS, SRV.PHONE_TOOLS):
            self.assertNotIn("answer_card", s)

    def test_a_loop_is_refused_out_loud(self):
        for agent in ("planner:g", "worker:t", "triage", "phone", "plan"):
            self.assertIsNotNone(SRV._lane_refusal("answer_card", SRV._lane_of(agent)), agent)
        self.assertIsNone(SRV._lane_refusal("answer_card", SRV._lane_of("build")))

    def test_a_loop_on_the_claude_rung_is_not_given_the_tool(self):
        self.assertIn("answer_card", CA._LOOP_HIDDEN)


class TestRoute(_Base):
    def _post(self, body, owner=True):
        from unittest import mock
        from membrane import lanes as L
        self.srv._paused = False
        h = L.MembraneOpenAIHandler.__new__(L.MembraneOpenAIHandler)
        h.server, h.path, h.command, h.headers = self.srv, "/membrane/decision/relay", "POST", {}
        out = []
        h._json_out = lambda obj, *a, **k: out.append(obj)
        h._json_err_raw = lambda code, msg: out.append({"error": msg, "code": code})
        h._json_body = lambda: dict(body)
        h._paused_write_refused = lambda: False
        with mock.patch("membrane.seatauth.owner", return_value=owner), \
                mock.patch("membrane.jwtauth.send_unauthorized",
                           side_effect=lambda hh: out.append({"code": 401})):
            h.do_POST()
        return out[-1]

    def test_the_route_answers_and_refuses(self):
        self._said("msg_1", "12 is fine for now")
        self.assertEqual(self._post({"card": self.card, "value": 12, "quote": "made up"})["code"], 400)
        self.assertEqual(self._post({"card": self.card, "value": 12, "quote": "12"}, owner=False)["code"], 401)
        self.assertEqual(self._props()["status"], "open")
        self.assertTrue(self._post({"card": self.card, "value": 12, "quote": "12"})["ok"])
        self.assertEqual(self._post({"card": self.card, "value": 13, "quote": "12"})["code"], 409)
        self.assertEqual(self._props()["value"], 12)

    def test_the_tool_posts_its_three_args(self):
        seen = []
        orig = MCP._http
        MCP._http = lambda port, m, path, body=None, timeout=None: (
            seen.append((path, body)) or {"ok": True, "card": {"status": "decided", "value": 12}})
        try:
            out = MCP.TOOL_IMPLS["answer_card"]({"card": "c", "value": 12, "quote": "12"}, 1)
        finally:
            MCP._http = orig
        self.assertEqual(seen, [("/membrane/decision/relay", {"card": "c", "value": 12, "quote": "12"})])
        self.assertIn("decided", out)


if __name__ == "__main__":
    unittest.main()
