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
        self.assertEqual(seen, [("entity_read", {"spec": "c:x", "nofire": True})])   # jobs.py: the read answers from the store
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
        self.assertEqual(seen, [("entity_read", {"spec": "c:x", "nofire": True})])


if __name__ == "__main__":
    unittest.main()


class TestLooseArgs(unittest.TestCase):
    """The seat's three dead calls adopting the light class (2026-10-09)."""

    def test_scripts_as_a_json_string(self):
        leg, a = T.resolve("entity", {"action": "edit", "spec": "c:x", "scripts": '{"getter": "result = {}"}'})
        self.assertEqual(a["getter_script"], "result = {}")

    def test_the_old_edit_name_takes_scripts_too(self):
        leg, a = T.resolve("entity_edit", {"spec": "c:x", "scripts": {"getter": "result = {}"}})
        self.assertEqual(a.get("getter_script"), "result = {}")
        self.assertNotIn("scripts", a)

    def test_learn_with_the_learn_key(self):
        self.assertEqual(T.resolve("memory", {"action": "learn", "learn": "x"}), ("learn", {"lesson": "x"}))

    def test_parents_as_a_json_string(self):
        self.assertEqual(M._listish('["ha light"]'), ["ha light"])
        self.assertEqual(M._listish("ha light"), ["ha light"])
        self.assertEqual(M._listish(["a", "b"]), ["a", "b"])


class TestReadByName(unittest.TestCase):
    def test_read_takes_name_too(self):
        self.assertEqual(T.resolve("entity", {"action": "read", "name": "ha:light.x", "run": True}),
                         ("entity_read", {"spec": "ha:light.x", "run": True}))


class TestReadByObject(unittest.TestCase):
    def test_read_takes_object_too(self):
        self.assertEqual(T.resolve("entity", {"action": "read", "object": "ha:sensor.x"}),
                         ("entity_read", {"spec": "ha:sensor.x"}))


class TestOldEditTakesParents(unittest.TestCase):
    def test_the_old_entity_edit_name_carries_parents(self):
        leg, a = T.resolve("entity_edit", {"name": "probe:dispatch", "parents": ["b", "a"]})
        self.assertEqual(leg, "entity_edit")
        self.assertEqual(a["properties"]["parents"], ["b", "a"])
        self.assertEqual(a["spec"], "probe:dispatch")

    def test_the_names_the_seat_invents_resolve_before_the_lane_gate(self):
        # 2026-10-10: 'entity/action-read' and 'membrane-entity_read' were
        # refused even on the lanes that list entity_read
        for name, args in (("entity/action-read", {"key": "m:x"}), ("membrane-entity_read", {"key": "m:x"}),
                           ("entity.read", {"spec": "m:x"}), ("mcp_entity_read", {"name": "m:x"})):
            leg, a = T.resolve(name, args)
            self.assertEqual((leg, a.get("spec")), ("entity_read", "m:x"), name)
            self.assertIsNone(S._lane_refusal(leg, "operator"), name)
        leg, a = T.resolve("exec/action-link", {"command": "c1", "params": '{"needle": "x"}'})
        self.assertEqual((leg, a["commands"], a["args"]), ("entity_link", "c1", {"needle": "x"}))
        self.assertEqual(T.resolve("membrane-query", {"topic": "t"})[0], "query")
        self.assertEqual(T.resolve("nonsense/action-read", {})[0], "nonsense/action-read")   # still refused, honestly



class TestArgumentsInTheName(unittest.TestCase):
    """2026-10-11: '{"action":' as a tool name got a lane refusal six times.
    2026-10-15 (serve-msgs, 70 live calls): this class is a REAL call whose
    json was split badly by the streamed assembly -- a decodable call in the
    name slot routes (the one owner of the action names the family, args
    untouched); the undecodable junk still refuses with the why."""
    def test_it_salvages_when_the_action_names_the_tool(self):
        from membrane import toolset as T
        leg, a = T.resolve('{"action": "read", "name": "presence.exec"}', {})
        self.assertEqual((leg, a.get("spec")), ("entity_read", "presence.exec"))
        # an unusable name-junk still says what happened
        with self.assertRaises(ValueError) as cm:
            T.resolve('{"action":', {})
        self.assertIn("NAME held its arguments", str(cm.exception))


    def test_an_empty_name_says_so(self):
        from membrane import toolset as T
        for nm in ("", '""', "  ", "''", "?"):
            with self.assertRaises(ValueError) as cm:
                T.resolve(nm, {"action": "read"})
            self.assertIn("no tool name", str(cm.exception))


def test_a_nameless_call_is_salvaged_by_its_action():
    # an HA seat, 2026-10-12: the backend lost the tool's name ('no_name_1') and
    # wrapped the arguments as {"input": "<json>"}; the model retried it 14 times
    from membrane import toolset as T
    name, args = T.resolve("no_name_1", {"input": '{"action": "edit", "spec": "membrane::name", '
                                                   '"properties": {"name": "Juniper", "agreed_with": "Tester"}}'})
    assert name == "entity_edit"
    assert args["spec"] == "membrane::name" and args["properties"]["name"] == "Juniper"
    import pytest
    with pytest.raises(ValueError, match="WITHOUT its tool name"):
        T.resolve("no_name_2", {"action": "list"})          # goal or track: say so, don't guess
