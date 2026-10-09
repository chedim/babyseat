"""THE TOOL DIET (2026-10-09, the operator: "we were supposed to save tokens
... do we need 24 tools? do we need such large definitions?", red first).

24 tool definitions cost ~4,900 tokens a turn -- entity_edit alone ~2,100.
Pinned: the seat advertises ~10 FAMILIES (an `action` each, one-line
descriptions, short params); every family action resolves to the existing
tool, so the implementations and the lane gates stay exact; a lane sees a
family only with the actions it may use (a phone call's `entity` reads,
nothing else); the old names still work when called; the long-form docs
moved behind `help`.
"""
import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import toolset as T  # noqa: E402


def _tokens(defs):
    return len(json.dumps(defs)) // 4


class TestDiet(unittest.TestCase):
    def test_the_operator_surface_is_small(self):
        defs = T.advertised(S.OPERATOR_TOOLS)
        self.assertLessEqual(len(defs), 11)
        self.assertLess(_tokens(defs), 1600, _tokens(defs))
        for d in defs:
            self.assertLessEqual(len(d["description"]), 260, d["name"])

    def test_every_lane_tool_has_a_family(self):
        covered = {leg for f in T.FAMILIES.values() for leg in f["actions"].values()}
        for lane in (S.OPERATOR_TOOLS, S.LISTENER_TOOLS, S.PLAN_TOOLS, S.PHONE_TOOLS):
            for name in lane:
                if name in T.RETIRED:
                    continue
                self.assertIn(name, covered, name)

    def test_lanes_see_only_their_actions(self):
        phone = {d["name"]: d for d in T.advertised(S.PHONE_TOOLS)}
        self.assertEqual(phone["entity"]["inputSchema"]["properties"]["action"]["enum"], ["read"])
        self.assertNotIn("exec", phone)
        plan = {d["name"]: d for d in T.advertised(S.PLAN_TOOLS)}
        self.assertNotIn("plant", plan["goal"]["inputSchema"]["properties"]["action"]["enum"])
        self.assertNotIn("exec", plan)

    def test_retired_from_the_model(self):
        names = {a for d in T.advertised(S.OPERATOR_TOOLS)
                 for a in d["inputSchema"]["properties"].get("action", {}).get("enum", [])}
        self.assertNotIn("junk_cut", names)


class TestResolve(unittest.TestCase):
    def test_family_actions_map_to_the_existing_tools(self):
        R = T.resolve
        self.assertEqual(R("find", {"action": "memory", "q": "battery"}), ("query", {"topic": "battery"}))
        self.assertEqual(R("find", {"action": "related", "q": "bms", "k": 4}), ("entities_related", {"query": "bms", "k": 4}))
        self.assertEqual(R("find", {"action": "query", "q": "kind:c"}), ("entity_query", {"q": "kind:c"}))
        self.assertEqual(R("entity", {"action": "read", "spec": "c:x", "run": True}), ("entity_read", {"spec": "c:x", "run": True}))
        self.assertEqual(R("goal", {"action": "plant", "text": "wire it"}), ("intend", {"text": "wire it"}))
        self.assertEqual(R("goal", {"action": "close", "spec": "g", "evidence": "rc=0"}),
                         ("fulfill", {"spec": "g", "evidence": "rc=0"}))
        self.assertEqual(R("memory", {"action": "learn", "text": "x"}), ("learn", {"lesson": "x"}))
        self.assertEqual(R("exec", {"action": "link", "commands": "uptime"}), ("entity_link", {"commands": "uptime"}))
        self.assertEqual(R("track", {"action": "list"}), ("entity_tracks", {}))
        self.assertEqual(R("field_watch", {"action": "dial", "budget": 4}), ("attention", {"action": "set", "budget": 4}))
        self.assertEqual(R("state", {}), ("state", {}))

    def test_scripts_object_expands(self):
        leg, args = T.resolve("entity", {"action": "edit", "spec": "c:x",
                                         "scripts": {"getter": "result = {'state': {}}", "tick": "pass"}})
        self.assertEqual(leg, "entity_edit")
        self.assertEqual(args["getter_script"], "result = {'state': {}}")
        self.assertEqual(args["tick_script"], "pass")
        self.assertNotIn("scripts", args)

    def test_old_names_pass_through(self):
        self.assertEqual(T.resolve("entity_read", {"spec": "c:x"}), ("entity_read", {"spec": "c:x"}))

    def test_a_bad_action_says_what_exists(self):
        with self.assertRaises(ValueError) as cm:
            T.resolve("entity", {"action": "destroy"})
        self.assertIn("read", str(cm.exception))

    def test_every_family_action_has_an_impl(self):
        for f in T.FAMILIES.values():
            for leg in f["actions"].values():
                self.assertTrue(leg in M.TOOL_IMPLS, leg)


class TestHelp(unittest.TestCase):
    def test_help_carries_the_long_form(self):
        self.assertIn("result = {'state'", T.help_text("scripts"))
        self.assertIn("parents", T.help_text("exec"))
        self.assertIn("topics", T.help_text(""))
        self.assertIn("help", {d["name"] for d in T.advertised(S.OPERATOR_TOOLS)})



class _Ladder:
    def __init__(self, calls):
        self.calls, self.n, self.bodies = calls, 0, []

    def __call__(self, body):
        self.bodies.append(body)
        self.n += 1
        if self.n <= len(self.calls):
            name, args = self.calls[self.n - 1]
            return {"choices": [{"message": {"content": "", "tool_calls": [
                {"id": f"c{self.n}", "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args)}}]}}]}
        return {"choices": [{"message": {"content": "done"}}]}


def _turn(agent, calls):
    seen = []
    lad = _Ladder(calls)
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), lad,
                     lambda name, args, call_id: seen.append((name, args)) or f"ran {name}",
                     system="", model="m")
    sid = r.sessions.create(title="t")["id"]
    out = r.run(sid, "go", user_info={"id": "msg_u1", "role": "user", "agent": agent})
    tools = [p for p in (out or {}).get("parts") or [] if p.get("type") == "tool"]
    return seen, tools, lad


class TestTheTurn(unittest.TestCase):
    def test_a_family_call_runs_the_existing_tool(self):
        seen, tools, lad = _turn("build", [("entity", {"action": "read", "spec": "c:x"})])
        self.assertEqual(seen, [("entity_read", {"spec": "c:x"})])
        self.assertEqual(tools[0]["tool"], "entity")                 # what the model sent
        self.assertEqual(tools[0]["state"]["input"]["action"], "read")
        sent = {(t.get("function") or t)["name"] for t in lad.bodies[0]["tools"]}
        self.assertIn("entity", sent)
        self.assertNotIn("entity_edit", sent)

    def test_the_lane_gate_still_holds(self):
        seen, tools, _ = _turn("phone", [("exec", {"action": "link", "commands": "reboot"})])
        self.assertEqual(seen, [])
        self.assertIn("Refused", tools[0]["state"]["output"])

    def test_help_and_bad_actions_answer_without_the_executor(self):
        seen, tools, _ = _turn("build", [("help", {"topic": "scripts"}),
                                         ("goal", {"action": "explode"})])
        self.assertEqual(seen, [])
        self.assertIn("getter", tools[0]["state"]["output"])
        self.assertIn("plant", tools[1]["state"]["output"])

    def test_old_names_still_run(self):
        seen, _, _ = _turn("build", [("entity_read", {"spec": "c:x"})])
        self.assertEqual(seen, [("entity_read", {"spec": "c:x"})])


if __name__ == "__main__":
    unittest.main()
