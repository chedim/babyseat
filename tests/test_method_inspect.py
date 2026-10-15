"""INSPECT A METHOD (issue #80, Memo; 2026-10-15). presence.exec lost its binding and
refused every call, and from the model's seat there was no way to LOOK at it: no read
of its stored command, its expected args, or what a call resolves to after
inheritance -- every breakage became an operator hand-off instead of a self-repair.

exec(action='inspect', object=..., method=..., args=...) runs NOTHING and says:
where the call resolves (via, the resolution order, who defines the method along it),
the definition (command template, params, guard/steps, read_only), and -- with args --
the exact command the call WOULD run or the exact refusal it WOULD give. Inspect and
call share one preparation, so the dry run never disagrees with the call."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import night as N  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import toolset as T  # noqa: E402
import test_cliffhanger as C  # noqa: E402


class TestInspect(unittest.TestCase):
    def test_it_shows_where_the_call_resolves_and_the_definition(self):
        srv, f, est, light, switch, port, aft, fan = C._world()
        N.method_define(srv, est, f, None, light.eid, "turn_on", "echo on {{self.entity_id}} {{brightness}}")
        r = N.method_inspect(est, port.eid, "turn_on")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["via"], light.eid)
        self.assertEqual(r["command"], "echo on {{self.entity_id}} {{brightness}}")
        self.assertEqual(r["params"], ["brightness"])
        self.assertIn("ha-light", r["mro"])
        self.assertEqual([d["owner"] for d in r["defined_on"]], [light.eid])

    def test_an_override_is_visible(self):
        srv, f, est, light, switch, port, aft, fan = C._world()
        N.method_define(srv, est, f, None, light.eid, "status", "echo CLASS")
        N.method_define(srv, est, f, None, port.eid, "status", "echo OWN")
        r = N.method_inspect(est, port.eid, "status")
        self.assertEqual(r["via"], port.eid)
        self.assertEqual([d["owner"] for d in r["defined_on"]], [port.eid, light.eid])

    def test_a_dry_run_says_what_would_run_and_runs_nothing(self):
        srv, f, est, light, switch, port, aft, fan = C._world()
        N.method_define(srv, est, f, None, light.eid, "turn_on", "echo on {{self.entity_id}} {{brightness}}")
        r = N.method_inspect(est, port.eid, "turn_on", {"brightness": 40})
        self.assertEqual(r["would_run"], "echo on light.port_strip 40")
        self.assertNotIn("would_refuse", r)
        self.assertNotIn("calls", port.properties)          # nothing ran, no receipt

    def test_a_dry_run_says_the_refusal_the_call_would_give(self):
        srv, f, est, light, switch, port, aft, fan = C._world()
        N.method_define(srv, est, f, None, light.eid, "probe", 'echo "n={n}"')
        r = N.method_inspect(est, port.eid, "probe", {"n": 60})
        call = N.method_call(srv, est, f, None, port.eid, "probe", {"n": 60})
        self.assertEqual(r["would_refuse"], call["error"])   # never disagrees with the call
        self.assertNotIn("would_run", r)

    def test_todays_corrupted_door_is_diagnosable(self):
        srv, f, est, light, switch, port, aft, fan = C._world()
        N.method_define(srv, est, f, None, port.eid, "exec", "presence.exec{code: ps -eo lstart,pid,args}")
        r = N.method_inspect(est, port.eid, "exec", {"code": "1+1"})
        self.assertEqual(r["command"], "presence.exec{code: ps -eo lstart,pid,args}")
        self.assertIn("bind to nothing", r["would_refuse"])

    def test_a_missing_method_says_what_it_answers_to(self):
        srv, f, est, light, switch, port, aft, fan = C._world()
        N.method_define(srv, est, f, None, light.eid, "turn_on", "echo x")
        r = N.method_inspect(est, port.eid, "explode")
        self.assertFalse(r["found"])
        self.assertIn("turn_on", r["answers_to"])

    def test_a_composite_lists_its_steps(self):
        srv, f, est, light, switch, port, aft, fan = C._world()
        N.method_define(srv, est, f, None, light.eid, "pair", "",
                        steps=[{"name": "a", "command": "echo a"}, {"name": "b", "command": "echo b"}])
        r = N.method_inspect(est, port.eid, "pair")
        self.assertEqual([s.get("command") for s in r["steps"]], ["echo a", "echo b"])


class TestTheWiring(unittest.TestCase):
    def test_exec_inspect_is_the_inspect_tool(self):
        self.assertEqual(T.resolve("exec", {"action": "inspect", "object": "o", "method": "m", "args": {"a": 1}}),
                         ("method_inspect", {"object": "o", "method": "m", "args": {"a": 1}}))

    def test_the_hands_lanes_have_it_and_plans_never_see_exec(self):
        self.assertIn("method_inspect", S.OPERATOR_TOOLS)
        self.assertIn("method_inspect", S.LISTENER_TOOLS)
        self.assertNotIn("method_inspect", S.PLAN_TOOLS)       # the plan lane never sees exec at all
        from membrane import jobs as J
        self.assertNotIn("method_inspect", J.RUN_TOOLS)        # nothing runs

    def test_the_mcp_door_knows_it(self):
        from membrane import mcp_server as M
        self.assertIn("method_inspect", M.TOOL_IMPLS)

    def test_the_help_says_so(self):
        self.assertIn("inspect", T.help_text("cliffhanger"))


if __name__ == "__main__":
    unittest.main()
