"""#160: an entity_edit of a summary answered 'updated' with the OLD summary,
reads kept returning the old text, and entity_read run=true still said
'getter not fired (deferred: the refresh runs as a job)'.

The live calls (serve-msgs) carried their arguments WRAPPED as a JSON string
under `args` -- entity_edit {"args": "{\"spec\": ..., \"summary\": ...}"},
entity_read {"args": "{\"spec\": ..., \"run\": true}"}. The spec was dug out
of the wrapper (mcp_server._arg), the summary and run were not: the edit set
nothing and echoed the stored text; jobs.dispatch never saw run, so the read
went the stored-values path with nofire.

These tests drive the same chain the serve's turn runner does: toolset.resolve
-> jobs.dispatch -> the MCP tool impl -> the seat's HTTP handler, in process,
over a real EntityStore."""
import os
import sys
import types
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import jobs as J  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import toolset as T  # noqa: E402
from membrane.entities import MEMBRANE_NS, EntityStore, QuantumEntity  # noqa: E402

EID = MEMBRANE_NS + "Component:memo:self"


def _seat(getter: str | None = None):
    est = EntityStore(path=None, shared=False)
    props = {"name": "memo:self", "summary": "old text"}
    if getter:
        props["getter_script"] = getter
    est.upsert(QuantumEntity(eid=EID, cls="Component", properties=props))
    srv = types.SimpleNamespace(entity_store=est, efield=None, efield_locked=False,
                                capturer=None, membrane=types.SimpleNamespace())
    est._srv = srv
    return srv, est


def _http_into(srv):
    """mcp_server._http, answered by the seat's own handlers in process."""
    routes = {"/membrane/entity/edit": L.MembraneOpenAIHandler._entity_edit,
              "/membrane/entity/read": L.MembraneOpenAIHandler._entity_read}

    def http(port, method, path, body=None, timeout=None):
        err, out = [], []
        h = types.SimpleNamespace(
            path=path, server=srv,
            _json_err_raw=lambda code, msg: err.append((code, msg)),
            _json_out=out.append, _json_body=lambda: dict(body or {}),
            _paused_write_refused=lambda: False,
            _entity_audit=lambda *a: None)
        routes[path.split("?")[0]](h)
        if err:
            raise M.ProxyError(err[0][0], err[0][1])
        res = dict(out[0])
        res.setdefault("ok", True)
        return res
    return http


def _turn_call(srv, name, args):
    """One tool call the way the serve's TurnRunner runs it."""
    runner = types.SimpleNamespace(srv=srv, _agents={})
    name_l, a = T.resolve(name, dict(args))
    return J.dispatch(runner, "s", name_l, a, "c",
                      lambda aa: M.TOOL_IMPLS[name_l](aa, 1))


def test_wrapped_edit_applies_the_summary_and_echoes_it():
    srv, est = _seat()
    with mock.patch.object(M, "_http", _http_into(srv)):
        out = _turn_call(srv, "entity_edit", {
            "args": '{"spec": "m:component:memo:self", "summary": "new text"}'})
        assert "new text" in out, out
        assert "old text" not in out, out
        assert est.docs[EID].properties["summary"] == "new text"
        read = _turn_call(srv, "entity_read", {"spec": "m:component:memo:self"})
        assert "new text" in read and "old text" not in read, read


def test_wrapped_family_edit_applies_the_summary():
    srv, est = _seat()
    with mock.patch.object(M, "_http", _http_into(srv)):
        out = _turn_call(srv, "entity", {
            "action": "edit",
            "args": '{"spec": "m:component:memo:self", "summary": "new text"}'})
    assert "new text" in out, out
    assert est.docs[EID].properties["summary"] == "new text"


def test_wrapped_read_run_true_fires_the_getter_now():
    srv, est = _seat(getter="result = {'state': {'pulse': 7}}")
    with mock.patch.object(M, "_http", _http_into(srv)):
        out = _turn_call(srv, "entity_read", {
            "args": '{"spec": "m:component:memo:self", "run": true}'})
    assert "deferred" not in out, out
    assert "getter fired" in out, out
    assert (est.docs[EID].properties.get("script_data") or {}).get("pulse") == 7


def test_the_outer_arguments_win_over_the_wrapped_ones():
    name, a = T.resolve("entity_edit", {"spec": "outer",
                                        "args": '{"spec": "inner", "summary": "s"}'})
    assert name == "entity_edit"
    assert a["spec"] == "outer" and a["summary"] == "s" and "args" not in a


def test_exec_keeps_its_own_args_object():
    # exec's `args` IS a parameter (the {{param}} values) -- never unwrapped
    name, a = T.resolve("exec", {"action": "call", "object": "ha:light.x",
                                 "method": "turn_on", "args": '{"level": 3}'})
    assert name == "method_call"
    assert a["args"] == {"level": 3} and "level" not in a
    name, a = T.resolve("method_call", {"object": "ha:light.x", "method": "turn_on",
                                        "args": '{"level": 3}'})
    assert a["args"] == {"level": 3} and "level" not in a
