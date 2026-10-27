"""JS IS A TREE IN THE FIELD TOO (#614 part B).

Pinned here:
  - JS is minted as node entities like the shell (tree-sitter's grammar, #622):
    a program's statements in order, a block's statements inside it; an
    object literal is not a block; the same statement is one entity;
  - every program prints back exactly as written (strings, template literals,
    comments, regexes, no semicolons, non-ASCII) and what is not JS is refused;
  - a js root runs on the seat's presence, in an opted-in browser tab, through
    the same execute -> executing -> result edges; a root of another language
    is refused either way;
  - the old door retires: no presence.exec method (an older seat's is removed
    at boot), no presence.js write branch, and the code reaches the tab as
    written (no shell unquoting).
"""
import os
import sys
import threading
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import adapters as AD  # noqa: E402
from membrane import jsexec as JS  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

PRES = MEMBRANE_NS + "presence"


def _seat(answer=None):
    """A seat with a relations store, presence bound, and one opted-in tab that
    answers each job with answer(job) (None: no tab at all)."""
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = types.SimpleNamespace(efield=f, entity_store=est, membrane=None,
                                capturer=types.SimpleNamespace(mode="on"), jobs=[])

    def publish(ev):
        if ev.get("type") == "membrane.jsexec":
            srv.jobs.append(ev["job"])
            threading.Timer(0.02, lambda: JS.result(srv, {"id": ev["job"]["id"], **answer(ev["job"])})).start()
    srv._loop_runner = types.SimpleNamespace(hub=types.SimpleNamespace(publish=publish))
    est._srv = srv
    JS.bind(srv)
    if answer is not None:
        JS.register(srv, "tab_a", "ses_1", "p")
    return srv


def _kids(srv, eid):
    rows = [r for r in R.history_of(srv.efield.b, srv.efield.origin, eid)
            if r["subj"] == eid and str(r["rel"]).startswith("arg:") and not r.get("superseded")]
    return [r["obj"] for r in sorted(rows, key=lambda r: int(r["rel"].split(":")[1]))]


def _text(srv, eid):
    return LG.text(srv.entity_store, eid)


def _kind(srv, eid):
    return srv.entity_store.docs[eid].properties["kind"]


def _walk(srv, eid):
    yield eid
    for k in srv.entity_store.docs[eid].properties.get("args") or []:
        yield from _walk(srv, k)


class TestTheTree(unittest.TestCase):
    def test_a_program_is_its_statements_in_order(self):
        srv = _seat()
        root = LG.mint(srv, "js", "const a = 1\nconsole.log(a); a += 1;")
        self.assertTrue(root.startswith(MEMBRANE_NS + "node:js:"))
        self.assertEqual([_text(srv, k) for k in _kids(srv, root)],
                         ["const a = 1", "console.log(a);", "a += 1;"])

    def test_a_block_holds_its_statements(self):
        srv = _seat()
        root = LG.mint(srv, "js", "if (x) { a(); b(); } else { c(); }")
        blocks = [e for e in _walk(srv, root) if _kind(srv, e) == "statement_block"]
        self.assertEqual([[_text(srv, s) for s in _kids(srv, b)] for b in blocks], [["a();", "b();"], ["c();"]])

    def test_an_object_literal_is_not_a_block(self):
        srv = _seat()
        root = LG.mint(srv, "js", "const o = { a: 1, b: 2 };")
        kinds = {_kind(srv, e) for e in _walk(srv, root)}
        self.assertIn("object", kinds)
        self.assertNotIn("statement_block", kinds)

    def test_the_same_statement_is_one_entity(self):
        srv = _seat()
        a = LG.mint(srv, "js", "a(); b();")
        b = LG.mint(srv, "js", "function f() { a(); }")
        self.assertIn(_kids(srv, a)[0], set(_walk(srv, b)))

    def test_every_program_prints_back_as_written(self):
        srv = _seat()
        for code in ("1+1", "return document.title", "a(); b();\n\n  c()\n",
                     "const s = 'a;b}' + \"{c\" + `t ${ {x: 1}.x } }`;",
                     "// a comment }\nfoo() /* { */\nbar()",
                     "if (/[}]/.test(s)) { n = a / b / c } else x = /;/g",
                     "class A extends B {\n  m() { return 1 }\n  static n = 2\n}",
                     "const f = async (x) => {\n  await g(x?.y ?? 1)\n  return x\n}\nf(1).then(v => console.log(v))",
                     "for (let i = 0; i < 3; i++) {\n  if (i)\n    continue\n}",
                     "try { x() } catch (e) { y(e) } finally { z() }",
                     "switch (k) { case 1: a(); break; default: b() }",
                     "let x = 1\n;[1, 2].forEach(n => x += n)\nx++\nx", "const café = 'naïve ☕'"):
            root = LG.mint(srv, "js", code)
            self.assertEqual(LG.text(srv.entity_store, root), code, code)

    def test_what_is_not_js_is_refused(self):
        for code in ("f(", "a = 'unclosed", "x = }", "/* never closed", "a\x00b"):
            with self.assertRaises(ValueError, msg=code):
                LG.mint(_seat(), "js", code)


class TestTheRun(unittest.TestCase):
    def test_a_js_root_runs_in_the_tab_and_ends_on_a_result(self):
        srv = _seat(lambda job: {"ok": True, "value": "5", "logs": ["hi"]})
        out = LG.run(srv, code="console.log('hi'); return 2+3", lang="js")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["executor"], PRES)
        self.assertIn("=> 5", out["head"])
        self.assertEqual(srv.jobs[0]["code"], "console.log('hi'); return 2+3")   # as written
        p = srv.entity_store.docs[out["result"]].properties
        self.assertEqual((p["rc"], p["root"]), (0, out["root"]))
        self.assertEqual(LG.state(srv, out["root"])["state"], "idle")

    def test_a_quoted_string_reaches_the_tab_quoted(self):
        srv = _seat(lambda job: {"ok": True, "value": job["code"]})
        out = LG.run(srv, code="'hello'", lang="js")
        self.assertEqual(srv.jobs[0]["code"], "'hello'")
        self.assertTrue(out["ok"], out)

    def test_no_tab_is_a_failed_result_that_says_why(self):
        srv = _seat()
        out = LG.run(srv, code="return 1", lang="js")
        self.assertFalse(out["ok"])
        self.assertIn("opt in", out["head"])
        self.assertIn(out["result"], srv.entity_store.docs)

    def test_languages_never_cross(self):
        srv = _seat(lambda job: {"ok": True, "value": "1"})
        js_on_sh = LG.run(srv, code="return 1", lang="js", on="tool:executor")
        sh_on_js = LG.run(srv, code="echo hi", lang="sh", on="presence")
        self.assertFalse(js_on_sh["ok"])
        self.assertFalse(sh_on_js["ok"])
        self.assertEqual(srv.jobs, [])

    def test_an_edge_anyone_wrote_runs_through_the_listener_shape(self):
        srv = _seat(lambda job: {"ok": True, "value": "7"})
        root = LG.mint(srv, "js", "return 7")
        R.upsert(srv.efield.b, srv.efield.origin, root, "execute", PRES, "test")
        ran = LG.reap(srv)
        self.assertEqual([r["executor"] for r in ran], [PRES])
        self.assertIn("=> 7", ran[0]["head"])


class TestTheOldDoorRetires(unittest.TestCase):
    def _exec_methods(self, srv):
        return [d for d in srv.entity_store.docs.values()
                if PRES in ((d.properties or {}).get("method_of") or [])
                and (d.properties or {}).get("method_name") == "exec"]

    def test_presence_has_no_exec_method(self):
        self.assertEqual(self._exec_methods(_seat()), [])

    def test_an_older_seats_exec_method_is_removed_at_boot(self):
        srv = _seat()
        N.method_define(srv, srv.entity_store, None, None, on=PRES, method="exec", command="",
                        steps=[{"name": "exec", "write": ["self.js", "code"]}])
        self.assertEqual(len(self._exec_methods(srv)), 1)
        JS.bind(srv)
        self.assertEqual(self._exec_methods(srv), [])

    def test_presence_js_is_no_write_branch(self):
        self.assertIsNone(AD._branch(PRES + ".js"))


if __name__ == "__main__":
    unittest.main()
