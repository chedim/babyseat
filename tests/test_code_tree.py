"""CODE IS A TREE IN THE FIELD (#614, the operator, 2026-10-27: "executors work as
blurp's chat works: the model builds a path from the command entity through its
arguments and terminates it on the executor node").

Pinned here (PR A: the nodes, the sh language, the executor's states):
  - code is minted as content-addressed node entities with ordered `arg:N` edges;
    the same subtree is one entity wherever it appears, and a repeated argument
    keeps both places;
  - the tree prints back exactly what the shell parser means;
  - running is an edge: root -execute-> executor (owed), -executing-> (in
    progress), then -result-> a result entity unique to that run; re-running
    re-creates the execute edge and adds a second result;
  - a root in another language than its executor's is refused; old results
    trim to the newest RESULTS_KEEP; the reaper runs owed edges written by
    anyone; a seat without a relations store still runs;
  - the model's one door: exec(action='run') -> code_run, in the lanes that may
    execute, behind the pause and owner gates.
"""
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane import scripts as SC  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import CLASS_TOOL, EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

EX = MEMBRANE_NS + "tool:executor"


def _seat(relations: bool = True):
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = types.SimpleNamespace(efield=f, entity_store=est, membrane=None,
                                capturer=types.SimpleNamespace(mode="on" if relations else "off"))
    est._srv = srv
    return srv


def _rows(srv, eid, rel=None, live=True):
    rows = R.history_of(srv.efield.b, srv.efield.origin, eid, rel)
    return [r for r in rows if not live or not r.get("superseded")]


def _args(srv, eid):
    """A node's children in order, read from its arg:N edges."""
    rows = [r for r in _rows(srv, eid) if r["subj"] == eid and str(r["rel"]).startswith("arg:")]
    return [r["obj"] for r in sorted(rows, key=lambda r: int(r["rel"].split(":")[1]))]


def _stage(srv, root):
    """The first stage of a one-command script: script -> pipe -> stage."""
    return _args(srv, _args(srv, root)[0])[0]


def _text(srv, eid):
    return srv.entity_store.docs[eid].properties["text"]


class TestTheTree(unittest.TestCase):
    def test_a_command_is_a_stage_with_its_arguments_in_order(self):
        srv = _seat()
        root = LG.mint(srv, "sh", "ls -la ..")
        self.assertTrue(root.startswith(MEMBRANE_NS + "node:sh:"))
        self.assertEqual([_text(srv, a) for a in _args(srv, _stage(srv, root))], ["ls", "-la", ".."])

    def test_the_same_subtree_is_one_entity(self):
        srv = _seat()
        a = LG.mint(srv, "sh", "ls ..")
        b = LG.mint(srv, "sh", "cd .. && ls ..")
        self.assertEqual(LG.mint(srv, "sh", "ls .."), a)                  # same code, same root
        dotdot = _args(srv, _stage(srv, a))[1]
        self.assertIn(dotdot, _args(srv, _stage(srv, b)))                 # `..` is one entity
        self.assertEqual(_args(srv, _args(srv, b)[1])[0], _stage(srv, a))   # so is `ls ..`

    def test_a_repeated_argument_keeps_both_places(self):
        srv = _seat()
        root = LG.mint(srv, "sh", "cp a a")
        args = _args(srv, _stage(srv, root))
        self.assertEqual(len(args), 3)
        self.assertEqual(args[1], args[2])

    def test_the_tree_prints_what_the_parser_means(self):
        srv = _seat()
        for code in ("ls -la ..", "A=1 ls 2>&1 | grep -v x && echo ok", "! true || false; echo $(pwd)",
                     "(cd /tmp; cat <<EOF\nhi\nEOF\n) > out.txt", "echo {{x}} 'a b' \"$HOME\"",
                     "for f in *.py; do echo $f; done"):
            try:
                want = SC.compile(SC.parse(code))
            except SC.NotAScript:
                continue
            self.assertEqual(LG.text(srv.entity_store, LG.mint(srv, "sh", code)), want, code)

    def test_what_is_not_code_is_refused(self):
        with self.assertRaises(SC.NotAScript):
            LG.mint(_seat(), "sh", "echo 'unclosed")


class TestTheRun(unittest.TestCase):
    def test_a_run_ends_on_a_result_entity(self):
        srv = _seat()
        out = LG.run(srv, code="echo hi")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["rc"], 0)
        self.assertIn("hi", out["head"])
        root, res = out["root"], out["result"]
        self.assertEqual([r["obj"] for r in _rows(srv, root, "result")], [res])
        self.assertEqual(_rows(srv, root, "execute"), [])
        self.assertEqual(_rows(srv, root, "executing"), [])
        p = srv.entity_store.docs[res].properties
        self.assertEqual((p["rc"], p["root"], p["text"]), (0, root, "echo hi"))

    def test_the_edge_says_executing_while_it_runs(self):
        srv = _seat()
        seen = {}
        real = LG.LANGS["sh"].runner

        def spy(srv_, text, timeout_s=None):
            root = LG.mint(srv, "sh", "echo hi")
            seen["executing"] = [r["obj"] for r in _rows(srv, root, "executing")]
            seen["execute"] = _rows(srv, root, "execute")
            return real(srv_, text, timeout_s)
        LG.LANGS["sh"].runner = spy
        try:
            LG.run(srv, code="echo hi")
        finally:
            LG.LANGS["sh"].runner = real
        self.assertEqual(seen["executing"], [EX])
        self.assertEqual(seen["execute"], [])

    def test_a_rerun_adds_its_own_result(self):
        srv = _seat()
        first = LG.run(srv, code="echo hi")
        again = LG.run(srv, node=first["root"])
        self.assertEqual(again["root"], first["root"])
        self.assertNotEqual(again["result"], first["result"])
        self.assertEqual(len(_rows(srv, first["root"], "result")), 2)

    def test_a_root_of_another_language_is_refused(self):
        srv = _seat()
        js = QuantumEntity(eid=MEMBRANE_NS + "tool:jsrun", cls=CLASS_TOOL,
                           properties={"name": "jsrun", "lang": "js"})
        srv.entity_store.upsert(js, embedder=None)
        out = LG.run(srv, code="echo hi", on="tool:jsrun")
        self.assertFalse(out["ok"])
        self.assertIn("js", out["error"])
        self.assertEqual(_rows(srv, out["root"], "result"), [])

    def test_old_results_trim_to_the_newest(self):
        srv = _seat()
        keep = LG.RESULTS_KEEP
        LG.RESULTS_KEEP = 2
        try:
            r1 = LG.run(srv, code="echo hi")
            LG.run(srv, node=r1["root"])
            r3 = LG.run(srv, node=r1["root"])
        finally:
            LG.RESULTS_KEEP = keep
        live = [r["obj"] for r in _rows(srv, r1["root"], "result")]
        self.assertEqual(len(live), 2)
        self.assertIn(r3["result"], live)
        self.assertNotIn(r1["result"], srv.entity_store.docs)

    def test_the_reaper_runs_an_execute_edge_anyone_wrote(self):
        srv = _seat()
        root = LG.mint(srv, "sh", "echo reaped")
        R.upsert(srv.efield.b, srv.efield.origin, root, "execute", EX, "test")
        ran = LG.reap(srv)
        self.assertEqual(len(ran), 1)
        self.assertEqual(len(_rows(srv, root, "result")), 1)
        self.assertEqual(LG.reap(srv), [])                            # the edge is spent

    def test_a_seat_without_relations_still_runs(self):
        srv = _seat(relations=False)
        out = LG.run(srv, code="echo hi")
        self.assertTrue(out["ok"], out)
        self.assertIn(out["result"], srv.entity_store.docs)
        self.assertEqual(LG.state(srv, out["root"])["results"][-1]["eid"], out["result"])

    def test_the_state_reads_the_tree_and_its_runs(self):
        srv = _seat()
        out = LG.run(srv, code="echo hi")
        st = LG.state(srv, out["root"])
        self.assertEqual((st["code"], st["lang"], st["state"]), ("echo hi", "sh", "idle"))
        self.assertEqual([r["eid"] for r in st["results"]], [out["result"]])

    def test_the_old_executor_reaper_runs_the_new_edges(self):
        srv = _seat()
        root = LG.mint(srv, "sh", "echo via-night")
        R.upsert(srv.efield.b, srv.efield.origin, root, "execute", EX, "test")
        N.run_pending_links(srv, srv.entity_store, srv.efield, None, EX)
        self.assertEqual(len(_rows(srv, root, "result")), 1)


class TestTheListener(unittest.TestCase):
    def test_an_execute_edge_is_an_adapter_branch(self):
        from membrane import adapters as A
        root = MEMBRANE_NS + "node:sh:abc"
        self.assertEqual(A._branch(f"rel:{root}|execute|{EX}"), ("exec", root, EX))
        self.assertIsNone(A._branch(f"rel:{root}|result|{EX}"))


class TestTheDoor(unittest.TestCase):
    def test_exec_run_is_code_run_in_the_executing_lanes(self):
        from membrane import mcp_server as M
        from membrane import server as S
        from membrane import toolset as T
        self.assertEqual(T.resolve("exec", {"action": "run", "code": "ls"})[0], "code_run")
        self.assertIn("code_run", {t["name"] for t in M.TOOLS})
        self.assertIn("code_run", S.OPERATOR_TOOLS)
        self.assertIn("code_run", S.LISTENER_TOOLS)
        self.assertNotIn("code_run", S.PLAN_TOOLS)
        self.assertNotIn("code_run", S.PHONE_TOOLS)
        from membrane import jobs as J
        self.assertIn("code_run", J.RUN_TOOLS)


class TestTheRoute(unittest.TestCase):
    def setUp(self):
        from membrane import lanes as L
        from tests.test_jwt_auth import _jwt_env
        self.tmp = tempfile.TemporaryDirectory()
        self.env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=os.path.join(self.tmp.name, "seat-token"),
                            MEMBRANE_DATA_DIR=self.tmp.name)
        self.env.start()
        os.environ.pop("MEMBRANE_NERVE_PAUSE", None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), L.MembraneOpenAIHandler)
        s = self.httpd
        s.backend, s.data_dir, s.paused = "http://127.0.0.1:9", self.tmp.name, False
        s.entity_store = EntityStore(path=None, shared=False)
        s.entity_store._srv = s
        s.membrane = types.SimpleNamespace(audit_path=str(Path(self.tmp.name) / "audit.jsonl"))
        s.efield, s.capturer = None, None
        N.seed_executor_entity(s.entity_store)
        threading.Thread(target=s.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.env.stop()
        self.tmp.cleanup()

    def _post(self, body) -> tuple[int, dict]:
        from tests.test_offbox_auth import _req
        code, b, _ = _req(self.httpd.server_address[1], "/membrane/code/run", "POST", body)
        try:
            return code, json.loads(b or b"{}")
        except ValueError:
            return code, {"raw": b}

    def test_the_route_runs_code(self):
        code, out = self._post({"code": "echo routed"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["rc"], 0)
        self.assertIn("routed", out["head"])

    def test_the_pause_refuses(self):
        self.httpd.paused = True
        code, _ = self._post({"code": "echo no"})
        self.assertEqual(code, 423)


if __name__ == "__main__":
    unittest.main()
