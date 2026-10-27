"""A METHOD'S CODE IS A TREE WITH PARAMETERS (#614 part C).

Pinned here:
  - defining a method mints its command as a code tree (`code` on the method);
    each {{name}} (or a quoted '{{name}}' / "{{name}}") is a `param` node, and
    the tree prints back the command exactly;
  - a call binds the params -- each value shell-quoted once, a quoted
    placeholder's own quotes replaced -- and the bound code runs through the
    code tree's executor edges (a result entity per run);
  - a param with no value blocks the call and names it; an older seat's method
    (a command, no tree) gets its tree on the first call;
  - code_run keeps {{x}} literal (only methods have parameters);
  - the text templates retire: night._fill, night.substitute_args.
"""
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = types.SimpleNamespace(efield=f, entity_store=est, membrane=None,
                                capturer=types.SimpleNamespace(mode="on"))
    est._srv = srv
    box = QuantumEntity(eid=MEMBRANE_NS + "Component:box", cls="Component", properties={"name": "box"})
    est.upsert(box, embedder=None)
    return srv, est, f, box


def _define(srv, est, f, box, command, method="say"):
    out = N.method_define(srv, est, f, None, box.eid, method, command)
    assert out.get("ok"), out
    return est.docs[out["eid"]]


def _call(srv, est, f, box, args, method="say"):
    return N.method_call(srv, est, f, None, box.eid, method, args)


def _walk(est, eid):
    yield eid
    for k in est.docs[eid].properties.get("args") or []:
        yield from _walk(est, k)


def _params(est, root):
    return [est.docs[e].properties["text"] for e in _walk(est, root)
            if est.docs[e].properties.get("kind") == "param"]


class TestTheMethodsTree(unittest.TestCase):
    def test_a_defined_method_holds_its_code_as_a_tree_with_params(self):
        srv, est, f, box = _world()
        cmd = "echo {{text}} --by={{self.name}} '{{tag}}'"
        m = _define(srv, est, f, box, cmd)
        root = m.properties["code"]
        self.assertTrue(root.startswith(MEMBRANE_NS + "node:sh:"))
        self.assertEqual(_params(est, root), ["{{text}}", "{{self.name}}", "'{{tag}}'"])
        self.assertEqual(LG.text(est, root), cmd)

    def test_a_call_binds_each_value_quoted_once_and_runs_through_the_tree(self):
        srv, est, f, box = _world()
        _define(srv, est, f, box, "echo {{text}} '{{tag}}' {{self.name}}")
        out = _call(srv, est, f, box, {"text": "a b; echo pwned", "tag": "it's"})
        self.assertEqual(out.get("rc"), 0, out)
        self.assertEqual(out["head"].strip(), "a b; echo pwned it's box")
        self.assertTrue(out.get("result", "").startswith(MEMBRANE_NS + "result:"), out)
        root = out["root"]
        self.assertEqual([r["obj"] for r in R.history_of(f.b, f.origin, root, "result")
                          if not r.get("superseded")], [out["result"]])

    def test_a_param_with_no_value_blocks_the_call(self):
        srv, est, f, box = _world()
        _define(srv, est, f, box, "echo {{text}}")
        out = _call(srv, est, f, box, {})
        self.assertNotEqual(out.get("rc"), 0)
        self.assertIn("text", str(out.get("error")))

    def test_an_older_seats_method_gets_its_tree_on_the_first_call(self):
        srv, est, f, box = _world()
        m = _define(srv, est, f, box, "echo {{text}}")
        m.properties.pop("code")
        out = _call(srv, est, f, box, {"text": "hi"})
        self.assertEqual(out["head"].strip(), "hi")
        self.assertTrue(est.docs[m.eid].properties.get("code"))

    def test_code_run_keeps_braces_literal(self):
        srv, est, f, box = _world()
        out = LG.run(srv, code="echo {{x}}")
        self.assertEqual(out["head"].strip(), "{{x}}")
        self.assertEqual(_params(est, out["root"]), [])

    def test_the_text_templates_retire(self):
        self.assertFalse(hasattr(N, "_fill"))
        self.assertFalse(hasattr(N, "substitute_args"))


if __name__ == "__main__":
    unittest.main()
