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


class TestFanOut(unittest.TestCase):
    """Fan-out (2026-10-10, Argus's guard list): members, cap, dry_run, on_fail,
    one summary, as a composite step. Echo only."""

    def test_dry_run_lists_and_runs_nothing(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "turn_off", "echo OFF {{self.id}}")
        r = N.method_each(srv, est, f, None, "ha light", "turn_off", dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertEqual(sorted(r["members"]), ["ha:light.aft_cabin", "ha:light.port_strip"])
        self.assertNotIn("calls", port.properties)                           # nothing ran

    def test_runs_on_every_member_with_one_summary(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "turn_off", "echo OFF {{self.id}}")
        r = N.method_each(srv, est, f, None, "ha light", "turn_off")
        self.assertEqual((r["status"], r["n"], r["n_ok"]), ("ok", 2, 2))
        self.assertEqual(port.properties["calls"][-1]["method"], "turn_off")  # each member's own receipt

    def test_the_cap_blocks_unless_raised(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        for i in range(4):
            _add(est, f"ha:light.extra{i}", parents=[light.eid])
        N.method_define(srv, est, f, None, light.eid, "turn_off", "echo OFF {{self.id}}")
        r = N.method_each(srv, est, f, None, "ha light", "turn_off", max_n=3)
        self.assertEqual(r["status"], "blocked")
        self.assertIn("exceed max=3", r["error"])
        self.assertEqual(N.method_each(srv, est, f, None, "ha light", "turn_off", max_n=10)["n_ok"], 6)
        self.assertEqual(N.method_each(srv, est, f, None, "ha light", "turn_off", max_n=10 ** 6)["max"], N.EACH_MAX_CEILING)

    def test_on_fail_abort_vs_continue_and_partial(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "flaky", "sh -c 'exit 1'")
        N.method_define(srv, est, f, None, port.eid, "flaky", "echo fine")       # one member's own wins
        r = N.method_each(srv, est, f, None, "ha light", "flaky", on_fail="continue")
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["ran"], 2)
        r2 = N.method_each(srv, est, f, None, "ha light", "flaky")
        self.assertIn(r2["status"], ("partial", "error"))
        self.assertTrue(r2["stopped"] or r2["ran"] == 2)

    def test_each_as_a_composite_step(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        room = _add(est, "room:aft")
        N.method_define(srv, est, f, None, light.eid, "turn_off", "echo OFF {{self.id}}")
        N.method_define(srv, est, f, None, room.eid, "lights_out", "", steps=[
            {"name": "all", "each": "ha light", "call": "turn_off", "max": 5}])
        r = N.method_call(srv, est, f, None, room.eid, "lights_out")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["steps"]["all"]["n_ok"], 2)

    def test_the_family(self):
        self.assertEqual(T.resolve("exec", {"action": "each", "over": "ha light", "method": "m", "dry_run": True}),
                         ("method_each", {"over": "ha light", "method": "m", "dry_run": True}))
        self.assertIn("method_each", S.OPERATOR_TOOLS)
        self.assertNotIn("method_each", S.PHONE_TOOLS)


class TestOldNameHabits(unittest.TestCase):
    """The fan-out probe (2026-10-10): the seat called entity_link with
    action='each' -- an old name with a family action."""

    def test_an_old_member_name_with_a_family_action_routes_through_the_family(self):
        self.assertEqual(T.resolve("entity_link", {"action": "each", "over": "ha light", "method": "status", "dry_run": "true"}),
                         ("method_each", {"over": "ha light", "method": "status", "dry_run": "true"}))
        self.assertEqual(T.resolve("entity_link", {"action": "call", "object": "o", "method": "m"})[0], "method_call")
        self.assertEqual(T.resolve("entity_link", {"commands": "uptime"}), ("entity_link", {"commands": "uptime"}))

    def test_link_refuses_a_bare_method_name(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        N.method_define(srv, est, f, None, light.eid, "status", "echo st", read_only=True)
        r = N.entity_link(srv, est, f, None, "", ["status"])
        self.assertFalse(r["ok"])
        self.assertIn("is a method", r["error"])
        self.assertTrue(N.entity_link(srv, est, f, None, "", ["echo still-a-command"])["ok"])


class TestFanOutMembersAndRemoval(unittest.TestCase):
    """The seat's first fan-out probe (2026-10-10): a light ran twice (two
    eids, one name), night-minted text tokens rode the sweep, and a cleanup
    removed the class's real 'status' method as a 'stub'."""

    def test_one_member_per_name_the_lived_one_wins(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        twin = est.docs[est.add(cls="Sensor", name="ha:light.port_strip", summary="x")["eid"]]
        twin.properties.update({"parents": [light.eid]})
        port.properties["readings"] = [{"ts": 1, "v": {"state": "on"}}]
        ms = N.members_of(est, light.eid)
        names = [N.short_name_of(m) for m in ms]
        self.assertEqual(names.count("ha:light.port_strip"), 1)
        self.assertIn(port.eid, [m.eid for m in ms])

    def test_a_class_getter_member_without_a_reading_is_unconfirmed(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        light.properties["getter_script"] = "result = {}"
        port.properties["readings"] = [{"ts": 1, "v": {"state": "on"}}]
        ghost = _add(est, "ha:light.turn_on", parents=[light.eid])
        ms, unc = N.member_split(est, light.eid)
        self.assertIn(port.eid, [m.eid for m in ms])
        self.assertIn(ghost.eid, [m.eid for m in unc])
        self.assertIn(aft.eid, [m.eid for m in unc])              # never read either
        N.method_define(srv, est, f, None, light.eid, "status", "echo st", read_only=True)
        r = N.method_each(srv, est, f, None, light.eid, "status", on_fail="continue")
        self.assertEqual(r["n"], 1)
        self.assertEqual(r["n_unconfirmed"], 2)
        self.assertEqual(r["status"], "ok")

    def test_without_a_class_getter_every_member_counts(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        ms, unc = N.member_split(est, light.eid)
        self.assertEqual(len(ms), 2)
        self.assertEqual(unc, [])

    def test_removing_a_live_method_needs_force(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        d = N.method_define(srv, est, f, None, light.eid, "status", "echo st", read_only=True)
        why = N.removal_refusal(est, d["eid"])
        self.assertIn("method 'status'", why)
        self.assertIn("force=true", why)
        self.assertIsNone(N.removal_refusal(est, port.eid))
        stub = N.entity_link(srv, est, f, None, "", ["echo a-stub"])
        self.assertTrue(stub["ok"])


class TestInterfaces(unittest.TestCase):
    """Interfaces (2026-10-10, the operator: "we should have interfaces"):
    abstract methods make a contract; remote_executor is the first."""

    def _iface(self):
        srv, f, est, light, switch, port, aft, fan = _world()
        self.assertTrue(N.seed_interfaces(est))
        self.assertFalse(N.seed_interfaces(est))                      # idempotent
        return srv, f, est

    def test_the_seed_declares_remote_executor_exec(self):
        srv, f, est = self._iface()
        ie = est._resolve_spec("remote_executor")
        self.assertTrue(ie.properties["interface"])
        self.assertEqual(N.abstract_methods(est, ie), [{"name": "exec", "interface": ie.eid, "params": ["code", "timeout"]}])
        self.assertEqual(N.methods_of(est, ie), [])                    # abstract is no answer

    def test_an_implementer_is_blocked_until_it_implements(self):
        srv, f, est = self._iface()
        box = _add(est, "env:seat-shell", parents=[est._resolve_spec("remote_executor").eid])
        r = N.method_call(srv, est, f, None, box.eid, "exec", {"code": "echo hi"})
        self.assertEqual(r["status"], "blocked")
        self.assertIn("implements remote_executor, which requires exec(code, timeout)", r["error"])
        self.assertIn("implements remote_executor; missing: exec(code, timeout)", N.contract_note(est, box))
        self.assertFalse(N.implements(est, box, "remote_executor"))
        N.method_define(srv, est, f, None, box.eid, "exec", "echo ran: {{code}}")
        self.assertTrue(N.implements(est, box, "remote_executor"))
        self.assertEqual(N.contract_note(est, box), "implements remote_executor")
        r = N.method_call(srv, est, f, None, box.eid, "exec", {"code": "x"})
        self.assertEqual(r["status"], "ok", r)
        self.assertIn("ran: x", str(r["head"]))

    def test_a_class_between_fulfils_it_for_all_its_members(self):
        srv, f, est = self._iface()
        ie = est._resolve_spec("remote_executor")
        shells = _add(est, "sh environments", parents=[ie.eid])
        a = _add(est, "env:a", parents=[shells.eid])
        N.method_define(srv, est, f, None, shells.eid, "exec", "echo sh {{code}}")
        self.assertTrue(N.implements(est, a, "remote_executor"))
        self.assertIn(a.eid, [m.eid for m in N.members_of(est, ie.eid)])

    def test_a_global_method_of_that_name_does_not_fulfil_a_contract(self):
        srv, f, est = self._iface()
        N.method_define(srv, est, f, None, "", "exec", "echo global")
        box = _add(est, "env:b", parents=[est._resolve_spec("remote_executor").eid])
        self.assertFalse(N.implements(est, box, "remote_executor"))

    def test_abstract_is_a_signature_only(self):
        srv, f, est = self._iface()
        r = N.method_define(srv, est, f, None, "remote_executor", "run", "echo no", abstract=True)
        self.assertFalse(r["ok"])
        self.assertIn("signature only", r["error"])
        self.assertFalse(N.method_define(srv, est, f, None, "", "run", "", abstract=True)["ok"])

    def test_the_banner_names_interfaces_and_their_implementers(self):
        srv, f, est = self._iface()
        box = _add(est, "env:c", parents=[est._resolve_spec("remote_executor").eid])
        self.assertIn("remote_executor exec(code, timeout) -- implemented by: nobody yet", N.interfaces_line(srv))
        N.method_define(srv, est, f, None, box.eid, "exec", "echo {{code}}")
        srv._iface_line_cache = None
        self.assertIn("implemented by: env:c", N.interfaces_line(srv))

    def test_the_tool_parses_an_inline_signature(self):
        from membrane.mcp_server import method_signature
        self.assertEqual(method_signature("exec(code, timeout)"), ("exec", ["code", "timeout"]))
        self.assertEqual(method_signature("status"), ("status", []))
