"""#646: agent-* audit rows say who called (ts, writer, chain)."""
import json
from types import SimpleNamespace

from membrane import night, writes as W


def _rows(p):
    return [json.loads(x) for x in open(p)]


def test_agent_row_carries_ts_writer_and_chain(tmp_path):
    mem = SimpleNamespace(audit_path=str(tmp_path / "a.jsonl"))
    with W.source("person", "ses_1"), W.as_writer("llm"):
        night._audit(mem, {"event": "agent-method-call", "origin": "mcp"})
    r = _rows(mem.audit_path)[0]
    assert r["ts"] > 0
    assert r["writer"] == "llm"
    assert r["chain"].startswith("person-ses_1-")
    assert r["chain_root"] == "person"


def test_row_without_context_still_has_ts(tmp_path):
    mem = SimpleNamespace(audit_path=str(tmp_path / "a.jsonl"))
    night._audit(mem, {"event": "agent-code-run", "origin": "mcp"})
    r = _rows(mem.audit_path)[0]
    assert r["ts"] > 0 and r["writer"] == "field"


def test_explicit_fields_are_kept(tmp_path):
    mem = SimpleNamespace(audit_path=str(tmp_path / "a.jsonl"))
    night._audit(mem, {"event": "agent-method-call", "ts": 5.0, "writer": "x"})
    r = _rows(mem.audit_path)[0]
    assert r["ts"] == 5.0 and r["writer"] == "x"


def test_mcp_client_is_named_by_initialize_and_pid(tmp_path, monkeypatch):
    """An external MCP client has no chain: initialize's clientInfo + the
    stdio process pid ride a header and land on the audit row as `caller`."""
    import os
    from membrane import mcp_server as M
    monkeypatch.setattr(M, "_CLIENT", "")
    M._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"clientInfo": {"name": "claude-code", "version": "2.1"}}}, 0)
    hdrs = M._W.headers(default_writer="llm")
    hdrs[W.HDR_CALLER] = M._client_caller()
    assert hdrs[W.HDR_CALLER] == f"claude-code 2.1 pid {os.getpid()}"
    mem = SimpleNamespace(audit_path=str(tmp_path / "a.jsonl"))
    with W.from_headers(hdrs):
        night._audit(mem, {"event": "agent-method-call", "origin": "mcp"})
    r = _rows(mem.audit_path)[0]
    assert r["caller"] == f"claude-code 2.1 pid {os.getpid()}"
    assert r["writer"] == "llm"


def test_caller_not_stamped_when_a_chain_names_the_session(tmp_path):
    mem = SimpleNamespace(audit_path=str(tmp_path / "a.jsonl"))
    with W.from_headers({W.HDR_CALLER: "x 1 pid 2"}), W.source("person", "s"):
        night._audit(mem, {"event": "agent-method-call", "origin": "mcp"})
    assert "caller" not in _rows(mem.audit_path)[0]
