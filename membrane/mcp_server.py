"""The MCP tool surface (P1): membrane's memory as TOOLS, not prose frames.

The canonical frames (`membrane: query the field for ...`, `membrane: learn
that ...`) are prose-fragile: the model must remember the lane discipline,
output exact phrasing, and wait a FULL turn for the answer. An MCP server
makes the same operations structured tool calls -- typed arguments, no frame
parsing, no one-turn delay, no possibility of the turn being LEARNED.

This module is a STATELESS transport adapter. It owns NO field state: every
tool relays to the RUNNING membrane proxy over 127.0.0.1:<port> (the proxy
that `membrane opencode` started). All guards stay in the proxy -- dedup,
variety gate, length caps, the audit trail, the two-authority boundary. The
MCP layer can only do what the operator's proxy allows; kill it with
--no-mcp and nothing but the convenience is lost.

Tools (each maps 1:1 to a canonical frame / endpoint):
  membrane_query    read recall (GET  /membrane/ask?q=...&k=N)     [read-only]
  membrane_state    field health/stats (GET /membrane/state?mcp=1) [read-only]
  membrane_learn    write one earned lesson (POST /membrane/learn) [guarded]
  membrane_forget   delete cells (POST /membrane/forget)           [guarded]

Protocol: MCP stdio transport -- newline-delimited JSON-RPC 2.0 on stdin/
stdout (initialize / tools/list / tools/call + notifications). Only stdlib.
Nothing this server prints to stdout is ever logged; diagnostics go to
stderr. The server never learns anything itself: it holds no field file, no
embedder, and the proxy-side endpoints it calls are the SAME intercepted
paths chat uses (reads are pre-gate; writes run the ordinary learn guards).
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "membrane"
SERVER_VERSION = "1.0.0"

# A query/state read can embed + search; a learn may dedup-embed. Generous
# but bounded: a hung proxy fails the tool call, it never wedges opencode.
HTTP_TIMEOUT_S = 30.0

TOOLS: list[dict] = [
    {
        "name": "membrane_query",
        "description": (
            "Read membrane's long-term memory: the nearest past exchanges for "
            "a topic (Q/A pairs with similarity and answered-model "
            "provenance). Read-only, no model call, never learned. Use "
            "before redoing known work, when past-session context would "
            "help, or to check what the field already learned."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string",
                          "description": "The topic/question to recall."},
                "k": {"type": "integer", "minimum": 1, "maximum": 8,
                      "description": "Max pairs (default 3)."},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "membrane_state",
        "description": (
            "Membrane's own live state: field size, safe ratio, effective "
            "rank, free energy, compactor status. Read-only, computed from "
            "the running proxy. Use for memory-health checks."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "membrane_learn",
        "description": (
            "Write ONE durable earned lesson into membrane's field. A lesson "
            "is a note, not a transcript: no secrets, no user personal data, "
            "no code dumps, no fenced blocks. The proxy applies the same "
            "guards as any learned turn (dedup, variety gate) and may skip "
            "it. Use when you EARNED something durable (a fixed quirk, the "
            "command that worked, why a gate exists)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lesson": {"type": "string",
                           "description": "One sentence-ish durable lesson."},
            },
            "required": ["lesson"],
        },
    },
    {
        "name": "membrane_forget",
        "description": (
            "Delete memories. Index form (e.g. '#3-7, 9' or '12') DELETES "
            "those cells (capped). Wordier asks return candidate indexes as "
            "a PROPOSAL -- they never delete. After deleting, prefer a "
            "membrane_learn that overwrites the lesson with the correction."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string",
                         "description": "'#N' / '#N-M, K' indexes, or a "
                                        "wordy topic (proposal only)."},
            },
            "required": ["spec"],
        },
    },
]


# ---- transport helpers -------------------------------------------------------

class ProxyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _http(port: int, method: str, path: str, body: dict | None = None,
          timeout: float = HTTP_TIMEOUT_S) -> dict:
    url = f"http://127.0.0.1:{int(port)}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("error", "")
        except Exception:
            detail = e.reason
        raise ProxyError(e.code, str(detail)) from None
    except (urllib.error.URLError, OSError) as e:
        raise ProxyError(503, f"membrane proxy unreachable on :{port} "
                              f"({e})") from None


# ---- tool implementations (all guards live proxy-side) ------------------------

def tool_query(args: dict, port: int) -> str:
    topic = str(args.get("topic") or "").strip()
    if not topic:
        raise ProxyError(400, "topic required")
    try:
        k = max(1, min(8, int(args.get("k") or 3)))
    except (TypeError, ValueError):
        k = 3
    qs = urllib.parse.urlencode({"q": topic[:512], "k": k})
    out = _http(port, "GET", f"/membrane/ask?{qs}")
    if not out.get("hits"):
        return (f"Nothing SAFE in the field about {topic!r} yet "
                f"(field holds {out.get('field_size', '?')} exemplars). "
                "Do not invent memories; proceed and let the turn learn.")
    lines = [f"Field recall (membrane; read-only, this call was NOT learned):"]
    for h in out["hits"]:
        via = f" [via {h['model']}]" if h.get("model") else ""
        tag = h.get("outcome") or "pending"
        src = h.get("src") or "user"
        lines.append(f"({h['sim']:.2f} [{tag}]{via} [src: {src}]) "
                     f"Q: {h['situation']}\n"
                     f"    A: {h['action']}")
    return "\n".join(lines)


def tool_state(args: dict, port: int) -> str:
    out = _http(port, "GET", "/membrane/state?mcp=1")
    th = out.get("thermo") or {}
    comp = out.get("compact") or {}
    lines = [
        "Membrane state (live from the running proxy):",
        f"- field: {out.get('field_size', '?')} exemplars "
        f"({out.get('safe', '?')} safe / "
        f"{max(0, (out.get('field_size') or 0) - (out.get('safe') or 0))} unsafe; "
        f"{out.get('notes', 0)} compact notes)",
        f"- effective rank {th.get('eff_rank', 0):.2f} "
        f"(ratio {th.get('rank_ratio', 0):.2f}); free energy "
        f"{th.get('free_energy', 0):.2f} (safe_frac {th.get('safe_frac', 0):.2f})",
        f"- embed model {out.get('embed_model') or 'unresolved'} "
        f"| gate sim_min={((out.get('gate') or {}).get('sim_min'))}",
    ]
    if comp:
        lines.append(f"- compactor: cycles={comp.get('cycles', 0)} "
                     f"clusters={comp.get('clusters', 0)} "
                     f"notes={comp.get('notes', 0)} "
                     f"model={out.get('compact_model') or '?'}")
    if out.get("review_pending"):
        lines.append(f"- review pending: {out['review_pending']}")
    return "\n".join(lines)


def tool_learn(args: dict, port: int) -> str:
    payload = str(args.get("lesson") or "").strip()
    if not payload:
        raise ProxyError(400, "lesson required")
    out = _http(port, "POST", "/membrane/learn", {"lesson": payload})
    if not out.get("written"):
        return ("Agent learn skipped by the field's own guards (dedup or "
                "variety gate): nothing new written. "
                + str(out.get("reason") or ""))
    ex = out.get("exemplar") or {}
    return (f"Learned as exemplar #{out.get('ex_idx')} "
            f"(no model call). Field now {out.get('field_size')}.\n"
            f"    Q: {ex.get('situation', '')[:280]}\n"
            f"    A: {(ex.get('action') or '')[:280]}")


def tool_forget(args: dict, port: int) -> str:
    spec = str(args.get("spec") or "").strip()
    if not spec:
        raise ProxyError(400, "spec required")
    out = _http(port, "POST", "/membrane/forget", {"spec": spec})
    if out.get("removed") is not None:
        lines = [f"Removed {out['removed']} of {out.get('field_size_before')} "
                 f"(index form). Field now {out.get('field_size')}."]
        for r in out.get("removed_cells") or []:
            lines.append(f"- #{r['idx']}: {r['situation']}")
        return "\n".join(lines)
    cands = out.get("candidates") or []
    if not cands:
        return ("Wordy forgets propose, never delete: nothing matched "
                "(sim >= 0.55, safe cells only). Nothing removed.")
    lines = ["Wordy asks never delete from tool calls. Nearest safe cells:",
             ", ".join(f"#{c['idx']} (sim {c['sim']:.2f})"
                       for c in cands[:8]),
             "To remove, re-ask membrane_forget with explicit indexes. "
             "Nothing removed."]
    return "\n".join(lines)


TOOL_IMPLS = {
    "membrane_query": tool_query,
    "membrane_state": tool_state,
    "membrane_learn": tool_learn,
    "membrane_forget": tool_forget,
}


# ---- the JSON-RPC loop ---------------------------------------------------------

def _rpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _handle(req: dict, port: int) -> dict | None:
    """One request -> one response (or None for notifications)."""
    method = req.get("method") or ""
    req_id = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        return _rpc_result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _rpc_result(req_id, {})
    if method == "tools/list":
        return _rpc_result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        impl = TOOL_IMPLS.get(name)
        if impl is None:
            return _rpc_error(req_id, -32602, f"unknown tool: {name}")
        try:
            text = impl(params.get("arguments") or {}, port)
        except ProxyError as e:
            # tool-level failure, not a protocol failure: MCP wants the
            # error IN the result (isError) so the model can read it.
            return _rpc_result(req_id, {
                "content": [{"type": "text",
                             "text": f"membrane tool error: {e.message}"}],
                "isError": True})
        except Exception as e:
            return _rpc_result(req_id, {
                "content": [{"type": "text",
                             "text": f"membrane tool error: {e}"}],
                "isError": True})
        return _rpc_result(req_id, {
            "content": [{"type": "text", "text": text}]})
    if req_id is None:
        return None                     # unknown notification: ignore
    return _rpc_error(req_id, -32601, f"method not found: {method}")


def serve(port: int, stdin=sys.stdin, stdout=sys.stdout) -> None:
    """Newline-delimited JSON-RPC over stdio until EOF."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _emit(stdout, _rpc_error(None, -32700, "parse error"))
            continue
        try:
            resp = _handle(req, port)
        except Exception as e:          # never let the loop die
            resp = _rpc_error(req.get("id") if isinstance(req, dict) else None,
                              -32603, f"internal error: {e}")
        if resp is not None:
            _emit(stdout, resp)


def _emit(stdout, obj: dict) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()


# ---- entry ---------------------------------------------------------------------

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        prog="membrane mcp",
        description="Membrane's memory as MCP tools (stdio; relays to the "
                    "running membrane proxy on 127.0.0.1:<port>).")
    ap.add_argument("--port", type=int, default=11500,
                    help="port of the RUNNING membrane proxy (default 11500)")
    args = ap.parse_args()
    # one boot-line on stderr (stdout is protocol-only)
    print(f"[membrane-mcp] stdio server up; proxy=127.0.0.1:{args.port}",
          file=sys.stderr)
    serve(args.port)


if __name__ == "__main__":
    main()