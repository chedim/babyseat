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
