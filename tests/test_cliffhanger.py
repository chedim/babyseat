"""CLIFFHANGER (2026-10-10, the operator: "compose commands kinda like proteins
and use membrane as an OOP-language" -- named after his toy graph language,
github.com/chedim/cliffhanger). Methods are command entities on classes or
objects; a call is an event on an object; dispatch walks the ISA order;
{{self.x}} fills from the object; the receipt lands on the object. Only
harmless `echo` runs here.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import toolset as T  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, CLASS_COMPONENT  # noqa: E402


def _add(est, name, **props):
    d = est.docs[est.add(cls=CLASS_COMPONENT, name=name, summary="x")["eid"]]
    d.properties.update(props)
    return d


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = type("S", (), {"efield": f, "entity_store": est, "membrane": None, "capturer": None})()
    light, switch = _add(est, "ha light"), _add(est, "ha switch")
    port = _add(est, "ha:light.port_strip", entity_id="light.port_strip", parents=[light.eid])
    aft = _add(est, "ha:light.aft_cabin", entity_id="light.aft_cabin", parents=[light.eid])
    fan = _add(est, "ha:switch.fan", entity_id="switch.fan", parents=[switch.eid])
    return srv, f, est, light, switch, port, aft, fan


class TestCliffhanger(unittest.TestCase):
    def test_define_once_call_on_each_member(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        d = N.method_define(srv, est, f, None, light.eid, "turn_on", "echo light-on {{self.entity_id}} {{brightness}}")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["params"], ["brightness"])                      # self.* is never an arg
        for obj, eid in ((port, "light.port_strip"), (aft, "light.aft_cabin")):
            r = N.method_call(srv, est, f, None, obj.eid, "turn_on", {"brightness": 40})
            self.assertEqual(r["rc"], 0, r)
            self.assertIn(f"light-on {eid} 40", str(r["head"]))
            self.assertEqual(r["via"], light.eid)
            self.assertEqual(obj.properties["calls"][-1]["method"], "turn_on")   # the receipt on the object

    def test_polymorphism_the_same_name_on_two_classes(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "turn_on", "echo LIGHT {{self.entity_id}}")
        N.method_define(srv, est, f, None, switch.eid, "turn_on", "echo SWITCH {{self.entity_id}}")
        self.assertIn("LIGHT light.port_strip", str(N.method_call(srv, est, f, None, port.eid, "turn_on")["head"]))
        self.assertIn("SWITCH switch.fan", str(N.method_call(srv, est, f, None, fan.eid, "turn_on")["head"]))

    def test_the_objects_own_method_wins_and_global_is_the_fallback(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "status", "echo CLASS")
        N.method_define(srv, est, f, None, port.eid, "status", "echo OWN")
        N.method_define(srv, est, f, None, "", "ping", "echo PING {{self.name}}")
        self.assertIn("OWN", str(N.method_call(srv, est, f, None, port.eid, "status")["head"]))
        self.assertIn("CLASS", str(N.method_call(srv, est, f, None, aft.eid, "status")["head"]))
        r = N.method_call(srv, est, f, None, fan.eid, "ping")
        self.assertEqual(r["via"], "global")
        self.assertIn("PING", str(r["head"]))

    def test_a_missing_method_names_what_the_object_answers_to(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "turn_on", "echo x")
        r = N.method_call(srv, est, f, None, port.eid, "explode")
        self.assertFalse(r["ok"])
        self.assertIn("turn_on", r["error"])
        self.assertFalse(N.method_call(srv, est, f, None, "nobody-here", "turn_on")["ok"])

    def test_a_missing_arg_is_refused_and_args_are_data(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "say", "echo said {{word}}")
        self.assertIn("needs args: word", str(N.method_call(srv, est, f, None, port.eid, "say")["error"]))
        r = N.method_call(srv, est, f, None, port.eid, "say", {"word": "x; rm -rf /tmp/never"})
        self.assertIn("x; rm -rf /tmp/never", str(r["head"]))

    def test_the_family_and_the_lanes(self):
        self.assertEqual(T.resolve("exec", {"action": "call", "object": "o", "method": "m", "args": {"a": 1}}),
                         ("method_call", {"object": "o", "method": "m", "args": {"a": 1}}))
        self.assertEqual(T.resolve("exec", {"action": "define", "on": "c", "method": "m", "command": "echo"}),
                         ("method_define", {"on": "c", "method": "m", "command": "echo"}))
        self.assertIn("method_call", S.OPERATOR_TOOLS)
        self.assertNotIn("method_call", S.PLAN_TOOLS)
        self.assertNotIn("method_call", S.PHONE_TOOLS)
        self.assertIn("Cliffhanger", T.help_text("cliffhanger"))


if __name__ == "__main__":
    unittest.main()


class TestReviewFixes(unittest.TestCase):
    """Argus's review of step 1 (2026-10-10)."""

    def test_a_missing_or_empty_self_value_blocks_before_anything_runs(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "dim", "echo dim {{self.room}} {{self.entity_id}}")
        r = N.method_call(srv, est, f, None, port.eid, "dim")
        self.assertEqual(r["status"], "blocked")
        self.assertIn("self.room", r["error"])
        self.assertNotIn("calls", port.properties)                  # nothing ran, nothing recorded
        port.properties["room"] = ""
        self.assertEqual(N.method_call(srv, est, f, None, port.eid, "dim")["status"], "blocked")
        light.properties["room"] = "aft cabin"                      # a class default fills it
        port.properties.pop("room")
        self.assertEqual(N.method_call(srv, est, f, None, port.eid, "dim")["status"], "ok")

    def test_the_receipt_says_who_answered_and_what_ran(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "turn_on", "echo on {{self.entity_id}}")
        r = N.method_call(srv, est, f, None, port.eid, "turn_on")
        self.assertEqual(r["via"], light.eid)
        self.assertEqual(r["fed"], "echo on light.port_strip")
        self.assertEqual(r["mro"][:2], ["ha:light.port_strip", "ha-light"])
        self.assertEqual(port.properties["calls"][-1]["fed"], "echo on light.port_strip")

    def test_read_only_calls_stay_out_of_the_call_ring(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "status", "echo st {{self.entity_id}}", read_only=True)
        r = N.method_call(srv, est, f, None, port.eid, "status")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["read_only"])
        self.assertNotIn("calls", port.properties)

    def test_outcomes(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "fail", "sh -c 'exit 3'")
        self.assertEqual(N.method_call(srv, est, f, None, port.eid, "fail")["status"], "error")
        self.assertEqual(N.method_call(srv, est, f, None, port.eid, "nope")["status"], "blocked")
        self.assertEqual(set(N.OUTCOMES), {"ok", "blocked", "error", "timeout", "partial"})


class TestGuardsAndComposites(unittest.TestCase):
    """Cliffhanger nodes as entities (2026-10-10): guards, call steps, named
    data flow, blocked/partial."""

    def test_a_guard_blocks_and_nothing_runs(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        d = N.method_define(srv, est, f, None, light.eid, "turn_on", "echo ON {{self.entity_id}}",
                            guard="result = {'ok': state['self'].get('reachable') == 'yes', 'why': 'unreachable'}")
        self.assertTrue(d["ok"], d)
        r = N.method_call(srv, est, f, None, port.eid, "turn_on")
        self.assertEqual(r["status"], "blocked")
        self.assertIn("unreachable", str(r["error"]))
        self.assertNotIn("ON", str(r.get("head") or ""))
        port.properties["reachable"] = "yes"
        r = N.method_call(srv, est, f, None, port.eid, "turn_on")
        self.assertEqual(r["status"], "ok", r)
        self.assertIn("ON light.port_strip", str(r["head"]))
        guard = est.docs[d["eid"] + ":guard"]                      # the guard is an entity
        self.assertIn("guard", guard.properties)

    def test_a_composite_passes_data_by_name(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        battery = _add(est, "argus:battery")
        N.method_define(srv, est, f, None, battery.eid, "read", 'echo \'{"soc": 92}\'', read_only=True)
        N.method_define(srv, est, f, None, light.eid, "set_level", "echo LEVEL {{self.entity_id}} {{level}}")
        d = N.method_define(srv, est, f, None, light.eid, "evening", "", steps=[
            {"name": "soc", "call": "read", "object": "argus:battery"},
            {"name": "dim", "call": "set_level", "object": "self", "args": {"level": "{{steps.soc.state.soc}}"}}])
        self.assertTrue(d["ok"], d)
        r = N.method_call(srv, est, f, None, port.eid, "evening")
        self.assertEqual(r["status"], "ok", r)
        self.assertIn("LEVEL light.port_strip 92", str(r["steps"]["dim"]["head"]))

    def test_partial_when_a_later_step_fails(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "a", "echo A")
        N.method_define(srv, est, f, None, light.eid, "b", "sh -c 'exit 2'")
        N.method_define(srv, est, f, None, light.eid, "both", "", steps=[
            {"name": "a", "call": "a", "object": "self"}, {"name": "b", "call": "b", "object": "self"}])
        r = N.method_call(srv, est, f, None, port.eid, "both")
        self.assertEqual(r["status"], "partial", r)

    def test_an_unresolved_ref_blocks(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "x", "", steps=[
            {"name": "s", "call": "set_level", "object": "self", "args": {"level": "{{steps.nope.state.v}}"}}])
        r = N.method_call(srv, est, f, None, port.eid, "x")
        self.assertEqual(r["status"], "blocked", r)

    def test_entities_refs_read_now(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        battery = _add(est, "argus:battery")
        battery.properties["script_data"] = {"soc": 77}
        N.method_define(srv, est, f, None, light.eid, "show", "echo SOC {{entities.argus:battery.soc}}")
        self.assertIn("SOC 77", str(N.method_call(srv, est, f, None, port.eid, "show")["head"]))

    def test_composites_nest_at_most_three_deep(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "m0", "echo deep")
        for i in range(1, 5):
            N.method_define(srv, est, f, None, light.eid, f"m{i}", "", steps=[{"name": "s", "call": f"m{i - 1}", "object": "self"}])
        self.assertEqual(N.method_call(srv, est, f, None, port.eid, "m2")["status"], "ok")
        self.assertEqual(N.method_call(srv, est, f, None, port.eid, "m4")["status"], "blocked")


class TestProbeSuiteFindings(unittest.TestCase):
    """Argus's probe suite (2026-10-10): four findings on the engine's side."""

    def test_single_entity_inherit_works_through_the_route_code(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        probe = _add(est, "probe:dispatch")
        from membrane.entities import MultiParentOps
        r = MultiParentOps.set_parents(est, probe.eid, [light.eid, switch.eid])
        self.assertTrue(r["ok"], r)
        import inspect
        from membrane import lanes as L
        self.assertNotIn("est.set_parents(", inspect.getsource(L))       # the unmounted bound form is gone

    def test_edit_with_a_parents_patch_sets_them(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        probe = _add(est, "probe:two")
        r = est.edit(probe.eid, properties={"parents": '["ha light", "ha switch"]'})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(probe.properties["parents"]), 2)
        N.method_define(srv, est, f, None, light.eid, "who", "echo LIGHT")
        N.method_define(srv, est, f, None, switch.eid, "who", "echo SWITCH")
        self.assertIn("LIGHT", str(N.method_call(srv, est, f, None, probe.eid, "who")["head"]))   # declared order

    def test_self_id_and_ha_entity_id_from_the_name(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        bloom = _add(est, "ha:light.port_hue_bloom", parents=[light.eid])
        N.method_define(srv, est, f, None, light.eid, "ids", "echo {{self.id}} {{self.entity_id}}")
        self.assertIn("light.port_hue_bloom light.port_hue_bloom",
                      str(N.method_call(srv, est, f, None, bloom.eid, "ids")["head"]))

    def test_define_on_a_missing_owner_mints_the_class(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        d = N.method_define(srv, est, f, None, "ha sensor", "who", "echo SENSOR")
        self.assertTrue(d["ok"], d)
        self.assertIsNotNone(est._resolve_spec("ha sensor"))


class TestDispatchOrder(unittest.TestCase):
    def test_an_edit_sets_the_exact_order_and_the_family_passes_parents(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        a, b = _add(est, "probe:parentA"), _add(est, "probe:parentB")
        probe = _add(est, "probe:dispatch2")
        N.method_define(srv, est, f, None, a.eid, "who", "echo A")
        N.method_define(srv, est, f, None, b.eid, "who", "echo B")
        self.assertTrue(est.edit(probe.eid, properties={"parents": ["probe:parentA", "probe:parentB"]})["ok"])
        self.assertIn("A", str(N.method_call(srv, est, f, None, probe.eid, "who")["head"]))
        self.assertTrue(est.edit(probe.eid, properties={"parents": ["probe:parentB", "probe:parentA"]})["ok"])
        self.assertEqual(probe.properties["parents"], [b.eid, a.eid])
        self.assertIn("B", str(N.method_call(srv, est, f, None, probe.eid, "who")["head"]))   # swapped
        self.assertTrue(est.edit(probe.eid, properties={"parents": ["probe:parentB"]})["ok"])
        self.assertEqual(probe.properties["parents"], [b.eid])                              # replaced, not added
        leg, args = T.resolve("entity", {"action": "edit", "spec": "x", "parents": '["p1"]'})
        self.assertEqual(args["properties"]["parents"], '["p1"]')
        self.assertFalse(est.edit(probe.eid, properties={"parents": [probe.eid]})["ok"])     # never itself
