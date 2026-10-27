"""THE COMMAND LIBRARY (2026-10-09, the operator: "why does it re-derive how
to do things every time, why doesn't it create re-usable entities", red
first).

Nothing showed the seat its own commands. Since #576 part F the model runs a
command only as a method it defined, so the library is its methods: a
'your methods:' line, most called first, with their {{params}} and how to
call one. Only harmless `echo` commands run here.
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
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, CLASS_COMPONENT, CLASS_TOOL, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = type("S", (), {"efield": f, "entity_store": est, "membrane": None, "capturer": None})()
    return srv, f, est


class TestParams(unittest.TestCase):
    def test_params_are_the_named_placeholders(self):
        self.assertEqual(N.command_params("x {{a}} {{parent.head}} {{b}} {{a}}"), ["a", "b"])


class TestLibrary(unittest.TestCase):
    def test_the_seat_sees_its_methods_most_called_first(self):
        srv, f, est = _world()
        obj = est.docs[est.add(cls=CLASS_COMPONENT, name="greeter", summary="x")["eid"]]
        N.method_define(None, est, None, None, obj.eid, "say", "echo said {{word}}")
        N.method_define(None, est, None, None, obj.eid, "up", "echo up")
        for _ in range(3):
            N.method_call(srv, est, f, None, obj.eid, "say", {"word": "w"})
        N.method_call(srv, est, f, None, obj.eid, "up")
        # a bare command entity (a script's) is not the model's to call: not listed
        bare = QuantumEntity(eid=MEMBRANE_NS + "c:bare", cls=CLASS_TOOL,
                             properties={"name": "bare", "command": "echo bare", "runs": 9})
        est.docs[bare.eid] = bare
        lib = N.command_library(est)
        self.assertTrue(lib.startswith("your methods"), lib)
        self.assertIn("exec(action='call'", lib)
        self.assertLess(lib.index("say{word} x3"), lib.index("up x1"))
        self.assertNotIn("bare", lib)
        self.assertNotIn("link", lib)
        self.assertIsNone(N.command_library(_world()[2]))


if __name__ == "__main__":
    unittest.main()
