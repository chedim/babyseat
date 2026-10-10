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
