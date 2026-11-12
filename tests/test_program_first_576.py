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
        from membrane import help_pack as HP
        texts = [CA.TOOLS_NOTE, json.dumps(M.TOOLS), json.dumps({t: HP.packaged(t) for t in HP.topics()}),
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

    def test_the_method_routes_are_gone(self):
        # #1101: methods are Command entities; an unknown /membrane/ POST answers the read-only reply
        for op in ("define", "call", "each", "inspect"):
            code, out = self._post("/membrane/method/" + op, {"object": "x", "method": "y"})
            self.assertEqual(out, {"error": "read-only; GET /membrane/state"}, op)








if __name__ == "__main__":
    unittest.main()
