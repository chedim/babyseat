"""PROGRAM THE FIELD FIRST (#576, part F; the operator, 2026-10-26: "I want the
LLM to HAVE TO program entities on the field so, no one-shot commands ANYWHERE,
everything MUST be done by programming the field first and then using it").

Pinned here:
  - the LLM's direct execution surface is gone: no entity_link / entity_unlink
    tool, no exec(action='link'/'unlink'), no /membrane/entity/link|unlink route;
  - a method call (or fan-out) straight at the executor -- tool:executor, the
    remote_executor interface, or the exec of an entity implementing it -- is
    refused with "program the field first: ...";
  - a method the LLM defined on another entity still runs through method_call;
  - a call is recorded for the learner: the method entity's `last_call` holds
    the object and the call's args in full, and its write event carries it.
"""
import json
import os
import tempfile
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import lanes as L  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import toolset as T  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, CLASS_COMPONENT  # noqa: E402
from tests.test_jwt_auth import _jwt_env  # noqa: E402
from tests.test_offbox_auth import _req  # noqa: E402

REFUSAL = "program the field first: define a method on an entity, then call it"


class TestTheSurfaceIsGone(unittest.TestCase):
    def test_no_link_tools(self):
        names = {t["name"] for t in M.TOOLS}
        for gone in ("entity_link", "entity_unlink"):
            self.assertNotIn(gone, names)
            self.assertNotIn(gone, M.TOOL_IMPLS)
            self.assertNotIn(gone, M.LOCAL_TOOL_IMPLS)
            self.assertNotIn(gone, S.OPERATOR_TOOLS)
            self.assertNotIn(gone, S.LISTENER_TOOLS)

    def test_no_exec_link_action(self):
        acts = T.FAMILIES["exec"]["actions"]
        self.assertNotIn("link", acts)
        self.assertNotIn("unlink", acts)
        with self.assertRaises(ValueError):
            T.resolve("exec", {"action": "link", "commands": "ls -a"})

    def test_free_text_never_becomes_a_link(self):
        # a placeholder-named call carrying 'exec <command>' used to run the command
        with self.assertRaises(ValueError):
            T.resolve("{function_name}", {"function_name": "exec ls -a"})

    def test_no_text_the_model_reads_offers_link(self):
        from membrane import claude_agent as CA
        texts = [CA.TOOLS_NOTE, json.dumps(M.TOOLS), json.dumps(T.HELP),
                 json.dumps({f: d["description"] for f, d in T.FAMILIES.items()}),
                 Path(__file__).resolve().parent.parent.joinpath("doctrine.md").read_text()]
        for t in texts:
            for word in ("entity_link", "action='link'", 'action="link"', "exec link"):
                self.assertNotIn(word, t)


def _seat(srv, d):
    srv.backend = "http://127.0.0.1:9"
    srv.data_dir = d
    srv.entity_store = EntityStore(path=None, shared=False)
    srv.entity_store._srv = srv
    srv.paused = False
    srv.membrane = types.SimpleNamespace(audit_path=str(Path(d) / "audit.jsonl"))
    srv.efield = None
    srv.capturer = None
    return srv


class TestTheRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=os.path.join(self.tmp.name, "seat-token"),
                            MEMBRANE_DATA_DIR=self.tmp.name)
        self.env.start()
        os.environ.pop("MEMBRANE_NERVE_PAUSE", None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), L.MembraneOpenAIHandler)
        self.srv = _seat(self.httpd, self.tmp.name)
        self.est = self.srv.entity_store
        N.seed_executor_entity(self.est)
        N.seed_interfaces(self.est)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.env.stop()
        self.tmp.cleanup()

    def _post(self, path, body):
        code, b, _ = _req(self.httpd.server_address[1], path, "POST", body)
        try:
            return code, json.loads(b or b"{}")
        except ValueError:
            return code, b

    def _add(self, name, **props):
        d = self.est.docs[self.est.add(cls=CLASS_COMPONENT, name=name, summary="x")["eid"]]
        d.properties.update(props)
        return d

    def test_the_link_routes_are_gone(self):
        # an unknown /membrane/ POST answers the read-only reply: nothing runs
        for p in ("/membrane/entity/link", "/membrane/entity/unlink"):
            code, out = self._post(p, {"commands": ["echo hi"]})
            self.assertEqual(out, {"error": "read-only; GET /membrane/state"}, p)
        self.assertFalse([d for d in self.est.docs.values()
                          if "echo hi" in str((d.properties or {}).get("command") or "")])

    def test_a_call_on_the_executor_is_refused(self):
        N.method_define(None, self.est, None, None, N.EXECUTOR_EID, "run", "echo {{line}}")
        code, out = self._post("/membrane/method/call", {"object": N.EXECUTOR_EID, "method": "run",
                                                         "args": {"line": "hi"}})
        self.assertIn(REFUSAL, json.dumps(out))
        self.assertFalse(out.get("ok"))
        m = self.est._resolve_spec(N.EXECUTOR_EID + ".run")
        self.assertFalse((getattr(m, "properties", None) or {}).get("runs"))   # nothing ran

    def test_a_call_on_remote_executor_or_its_exec_is_refused(self):
        js = self._add("tab runner", parents=[self.est._resolve_spec(N.REMOTE_EXECUTOR).eid])
        N.method_define(None, self.est, None, None, js.eid, "exec", "echo {{code}}")
        for obj in (N.REMOTE_EXECUTOR, js.eid):
            code, out = self._post("/membrane/method/call", {"object": obj, "method": "exec",
                                                             "args": {"code": "1"}})
            self.assertIn(REFUSAL, json.dumps(out), obj)
        code, out = self._post("/membrane/method/each", {"over": N.REMOTE_EXECUTOR, "method": "exec",
                                                         "args": {"code": "1"}})
        self.assertIn(REFUSAL, json.dumps(out))
        self.assertFalse(out.get("ok"))

    def test_a_defined_method_runs(self):
        obj = self._add("greeter")
        N.method_define(None, self.est, None, None, obj.eid, "hello", "echo hello {{who}}")
        code, out = self._post("/membrane/method/call", {"object": obj.eid, "method": "hello",
                                                         "args": {"who": "world"}})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("hello world", str(out.get("head")))

    def test_a_defined_method_that_uses_the_executor_inside_runs(self):
        N.method_define(None, self.est, None, None, N.EXECUTOR_EID, "run", "echo ran {{line}}")
        obj = self._add("greeter")
        N.method_define(None, self.est, None, None, obj.eid, "wave", "",
                        steps=[{"name": "r", "call": "run", "object": N.EXECUTOR_EID,
                                "args": {"line": "inside"}}])
        code, out = self._post("/membrane/method/call", {"object": obj.eid, "method": "wave"})
        self.assertEqual(out.get("status"), "ok", out)
        self.assertNotIn(REFUSAL, json.dumps(out))

    def test_the_call_is_recorded_with_its_args_in_full(self):
        obj = self._add("greeter")
        N.method_define(None, self.est, None, None, obj.eid, "hello", "echo {{who}}")
        long_arg = "w" * 5000
        seen = []
        W.on_write(seen.append)
        try:
            code, out = self._post("/membrane/method/call", {"object": obj.eid, "method": "hello",
                                                             "args": {"who": long_arg}})
        finally:
            W.off_write(seen.append)
        self.assertEqual(out["status"], "ok", out)
        m = next(d for d in self.est.docs.values()
                 if (d.properties or {}).get("method_name") == "hello")
        want = {"object": obj.eid, "args": {"who": long_arg}}
        self.assertEqual(m.properties.get("last_call"), want)
        ev = [w for w in seen if w["path"] == f"{m.eid}.last_call"]
        self.assertTrue(ev, [w["path"] for w in seen])
        self.assertEqual(ev[-1]["new"], want)

    def test_secrets_are_redacted_and_huge_args_capped(self):
        # operator, on Memo's review: up to 64 KB in full, then truncated with a marker;
        # a value whose key looks secret is stored as <redacted>
        obj = self._add("greeter")
        N.method_define(None, self.est, None, None, obj.eid, "hello", "echo {{who}} {{api_token}} {{Password}}")
        huge = "w" * (W.ARG_MAX + 100)
        self._post("/membrane/method/call", {"object": obj.eid, "method": "hello",
                                             "args": {"who": "x", "api_token": "s3cr3t", "Password": "p"}})
        m = next(d for d in self.est.docs.values() if (d.properties or {}).get("method_name") == "hello")
        self.assertEqual(m.properties["last_call"]["args"],
                         {"who": "x", "api_token": "<redacted>", "Password": "<redacted>"})
        self._post("/membrane/method/call", {"object": obj.eid, "method": "hello",
                                             "args": {"who": huge, "api_token": "t", "Password": "p"}})
        rec = m.properties["last_call"]["args"]["who"]
        self.assertTrue(rec.endswith(W.TRUNCATED) and len(rec) <= W.ARG_MAX + len(W.TRUNCATED))


if __name__ == "__main__":
    unittest.main()
