"""THE COMMAND LIBRARY (2026-10-09, the operator: "why does it re-derive how
to do things every time, why doesn't it create re-usable entities", red
first).

Four reasons the seat re-wrote its curls: every '. env; curl ...' was named
'.-<hash>' (unfindable by purpose); a re-link of the same line REBUILT its
entity (history lost); a pipeline's {{parent.head}} template was
overwritten by its first fed line (it worked once); and nothing showed the
seat its own commands. Pinned: names by action, the same line re-runs its
entity (runs counted), {{name}} parameters filled shell-quoted per call
(missing ones refused with the call shape), templates kept, and a
'your commands:' line. Only harmless `echo` commands run here.
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
from membrane import toolset as T  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = type("S", (), {"efield": f, "entity_store": est, "membrane": None, "capturer": None})()
    return srv, f, est


def _link(srv, f, est, commands, args=None):
    return N.entity_link(srv, est, f, None, "", commands, args=args)


class TestNames(unittest.TestCase):
    def test_setup_prefixes_say_nothing(self):
        n = N.command_name('. ~/.config/membrane/hass.env 2>/dev/null; curl -s "$HASS_URL/api/states" | python3 -c x')
        self.assertTrue(n.startswith("curl-"), n)
        self.assertIn("states", n)
        self.assertFalse(n.startswith("."))
        self.assertTrue(N.command_name("source x.env && echo hi there").startswith("echo-hi"))


class TestReuse(unittest.TestCase):
    def test_the_same_line_reruns_its_entity(self):
        srv, f, est = _world()
        line = "echo reuse-me && echo twice"
        r1 = _link(srv, f, est, line)
        r2 = _link(srv, f, est, line)
        self.assertEqual(r1["linked"][0]["eid"], r2["linked"][0]["eid"])
        self.assertTrue(r1["runs"][0]["created"])
        self.assertTrue(r2["runs"][0]["reused"])
        self.assertEqual(r2["runs"][0]["run_n"], 2)

    def test_a_line_equal_to_a_labelled_command_reruns_it(self):
        srv, f, est = _world()
        _link(srv, f, est, "greet :: echo   hello   world")
        r = _link(srv, f, est, "echo hello world")
        self.assertIn("greet", r["linked"][0]["eid"])


class TestParams(unittest.TestCase):
    def test_parameters_fill_shell_quoted_and_the_template_stays(self):
        srv, f, est = _world()
        _link(srv, f, est, "say :: echo said {{word}}", args={"word": "one"})
        r = _link(srv, f, est, "say", args={"word": "two; rm -rf /tmp/never"})
        run = r["runs"][0]
        self.assertIn("two; rm -rf /tmp/never", str(run.get("head")))     # data, not shell
        doc = est._resolve_spec(r["linked"][0]["eid"])
        self.assertEqual(doc.properties["command"], "echo said {{word}}")  # the template stays
        self.assertEqual(run["params"], ["word"])

    def test_a_missing_parameter_is_refused_with_the_call_shape(self):
        srv, f, est = _world()
        r = _link(srv, f, est, "say :: echo said {{word}}")
        self.assertFalse(r["runs"][0]["ok"])
        self.assertIn("needs args: word", r["runs"][0]["error"])

    def test_the_family_passes_args(self):
        self.assertEqual(T.resolve("exec", {"action": "link", "commands": "say", "args": {"word": "x"}}),
                         ("entity_link", {"commands": "say", "args": {"word": "x"}}))
        self.assertEqual(N.command_params("x {{a}} {{parent.head}} {{b}} {{a}}"), ["a", "b"])


class TestLibrary(unittest.TestCase):
    def test_the_seat_sees_its_commands_most_used_first(self):
        srv, f, est = _world()
        for _ in range(3):
            _link(srv, f, est, "say :: echo said {{word}}", args={"word": "w"})
        _link(srv, f, est, "uptime-ish :: echo up")
        lib = N.command_library(est)
        self.assertTrue(lib.startswith("your commands"))
        self.assertLess(lib.index("say{word} x3"), lib.index("uptime-ish x1"))
        self.assertIsNone(N.command_library(_world()[2]))


if __name__ == "__main__":
    unittest.main()
