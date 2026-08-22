"""The HTTP proxy: Membrane as a drop-in front of the Ollama model server.

Topology (see README): the chat window used to call the model server directly.

    you (chat window) --[today]--> model server (Ollama /api/chat)

  You slide Membrane in between:

    you (chat window) --> Membrane (proxy, owns the field) --> model server (Ollama)

  The window now talks to Membrane. Membrane embeds each /api/chat query (local
  Ollama/mxbai), runs the gate, and either REPLAYS a recalled action (no model
  call -- the absorb) or FORWARDS to the real model server (the novel) and logs
  the turn to the field + audit. Membrane is local-first (127.0.0.1 only).

Everything that is NOT /api/chat (e.g. /api/embed, /api/tags, /api/generate) is
forwarded transparently, so the proxy is a true drop-in: point any Ollama client
at http://127.0.0.1:11435 instead of :11434 and the field starts self-building.

Both NON-STREAMING and STREAMING /api/chat are gated. On stream:true the gate runs
first; an absorb synthesizes an NDJSON stream from the recalled action (no model
call), an escalation tees the backend stream to the client while accumulating the
response into the field. Set stream:false for the buffered path; stream:true is what
GUI/IDE clients (e.g. Continue, Open WebUI) send by default.

Run:
  PYTHONPATH=. .venv/bin/python -m membrane.proxy --listen 11435
  # then point a client at http://127.0.0.1:11435 (e.g. OLLAMA_HOST=127.0.0.1:11435)
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .runtime import Membrane, OllamaChatResponder, OllamaJudge, StubJudge, hint_system_message

HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding",
              "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "upgrade"}


def _ndjson_chunks(model: str, content: str):
    """Yield Ollama-shaped /api/chat stream chunks for a pre-known answer (the absorb).

    The recalled action is split into ~256-char pieces so it reads like a stream to
    a client that renders progressively; the client concatenates ``message.content``
    across chunks, so the join is exact (whitespace/code indentation preserved).
    Ends with a ``done:true`` chunk carrying empty content + zeroed eval stats.
    """
    pieces = [content[i:i + 256] for i in range(0, len(content), 256)] if content else []
    for p in pieces:
        yield {"model": model, "created_at": _now_iso(),
               "message": {"role": "assistant", "content": p}, "done": False}
    yield {"model": model, "created_at": _now_iso(),
           "message": {"role": "assistant", "content": ""},
           "done": True, "total_duration": 0, "load_duration": 0,
           "prompt_eval_count": 0, "prompt_eval_duration": 0,
           "eval_count": 0, "eval_duration": 0}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ProxyHandler(BaseHTTPRequestHandler):
    # protocol_version lets us reuse connections to the client
    protocol_version = "HTTP/1.1"

    # ---- routing ----------------------------------------------------------
    def do_POST(self):
        if self.path == "/api/chat":
            self._chat()
        else:
            self._forward()

    def do_GET(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    # ---- the gate path ----------------------------------------------------
    def _chat(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._forward_raw(raw)
            return
        # Ollama defaults stream=true; gate the stream path too now (absorb synthesizes
        # NDJSON from the recalled action, escalate tees the backend stream + learns).
        if body.get("stream", True):
            self._chat_stream(raw, body)
            return
        try:
            resp, decision = self.server.membrane.handle(body)
        except Exception as e:                       # never break the chat on a gate error
            print(f"[membrane] gate error, forwarding raw: {e}", file=sys.stderr)
            self._forward_raw(raw)
            return
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        sim = decision.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        print(f"[membrane] {decision['verdict']:18s} sim={sim_s}  field={len(self.server.membrane.store)}",
              file=sys.stderr)

    # ---- the gate path (STREAMING /api/chat) --------------------------------
    def _chat_stream(self, raw: bytes, body: dict) -> None:
        """Gate a STREAMING /api/chat turn.

        Absorb (act) -> synthesize NDJSON from the recalled action (NO model call,
        the field's replay delivered as a stream). Escalate -> tee the backend
        stream to the client while accumulating content, then learn the exemplar
        from the accumulated response. If the gate throws BEFORE any bytes are sent
        we fall back to a transparent forward (never break the chat); once headers
        are out, a mid-stream error just closes the connection (the client already
        has partial bytes).
        """
        mem = self.server.membrane
        try:
            verdict, vec, query, _ = mem.gate_turn(body)
        except Exception as e:                       # gate failed pre-headers -> safe to bail out
            print(f"[membrane] gate error, forwarding stream raw: {e}", file=sys.stderr)
            self._forward_raw(raw)
            return
        # committed to a streamed response now
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")      # streamed + close (no chunked framing needed)
        self.close_connection = True
        self.end_headers()
        try:
            if vec is None:                           # empty query: forward the stream, learn nothing
                self._stream_forward(body)
                return
            if verdict["verdict"] == "act":
                self._stream_replay(verdict, body, query, vec, mem)
                return
            self._stream_escalate(verdict, body, query, vec, mem)
        except Exception as e:                        # mid-stream: client already has partial bytes
            print(f"[membrane] stream delivery error: {e}", file=sys.stderr)

    def _stream_replay(self, verdict, body, query, vec, mem) -> None:
        """Absorb, delivered as a synthesized NDJSON stream of the recalled action."""
        model = body.get("model", "membrane")
        for chunk in _ndjson_chunks(model, verdict.get("action", "")):
            self.wfile.write((json.dumps(chunk) + "\n").encode())
        self.wfile.flush()
        mem.record_act(query, vec, verdict, model)
        sim = verdict.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        print(f"[membrane] act(stream)        sim={sim_s}  field={len(mem.store)}",
              file=sys.stderr)

    def _stream_escalate(self, verdict, body, query, vec, mem) -> None:
        """Escalate: stream the backend response to the client verbatim while teeing
        the content into the field, then learn. The recalled action is injected as
        the same hint system message the non-stream responder uses."""
        up = dict(body)
        up["stream"] = True
        h = hint_system_message(verdict.get("hint") if verdict["verdict"] == "escalate-with-hint" else None)
        if h:
            up["messages"] = [h] + list(up.get("messages", []))
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/api/chat", data=data,
                                     method="POST", headers={"Content-Type": "application/json"})
        parts: list[str] = []
        done_obj: dict = {}
        with urllib.request.urlopen(req, timeout=300) as r:
            while True:                               # NDJSON: one JSON object per line
                line = r.readline()
                if not line:
                    break
                self.wfile.write(line)                # forward verbatim (line keeps its newline)
                self.wfile.flush()
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if obj.get("done"):
                    done_obj = obj
                c = (obj.get("message") or {}).get("content")
                if c:
                    parts.append(c)
        self.wfile.flush()
        content = "".join(parts)
        model = done_obj.get("model") or body.get("model") or "membrane"
        mem.record_escalation(query, vec, verdict, content, model, False)
        sim = verdict.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        tag = f"{verdict['verdict']}(stream)"
        print(f"[membrane] {tag:18s} sim={sim_s}  field={len(mem.store)}", file=sys.stderr)

    def _stream_forward(self, body: dict) -> None:
        """Forward a stream with no gating (only used for the empty-query path once
        the 200/ndjson headers are already sent)."""
        up = dict(body)
        up["stream"] = True
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/api/chat", data=data,
                                     method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
        self.wfile.flush()

    # ---- transparent forward (everything else + gate-error fallback) ----
    def _forward(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else None
        self._send_to_backend(raw)

    def _forward_raw(self, raw: bytes) -> None:
        self._send_to_backend(raw)

    def _send_to_backend(self, raw: bytes | None) -> None:
        backend = self.server.backend
        url = backend + self.path
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        req = urllib.request.Request(url, data=raw, method=self.command, headers=headers)
        try:
            r = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            r = e
        self.send_response(r.status)
        for k, v in r.headers.items():
            if k.lower() not in HOP_BY_HOP:
                self.send_header(k, v)
        self.end_headers()
        while True:
            chunk = r.read(4096)
            if not chunk:
                break
            self.wfile.write(chunk)

    def log_message(self, *a):                          # silence the default access log
        pass


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Membrane proxy: a field-first front door in front of Ollama.")
    ap.add_argument("--listen", type=int, default=11435, help="port to listen on (127.0.0.1 only)")
    ap.add_argument("--backend", default="http://localhost:11434", help="upstream Ollama URL")
    ap.add_argument("--field", default="membrane-field.json", help="path to the exemplar field store")
    ap.add_argument("--audit", default="membrane-audit.jsonl", help="path to the append-only audit log")
    ap.add_argument("--sim-min", type=float, default=0.85,
                    help="cosine cutoff for absorb (conservative for the bootstrap; loosen once audit proves safe)")
    ap.add_argument("--consensus-min", type=float, default=0.60)
    ap.add_argument("--demote-threshold", type=int, default=1,
                    help="re-asks needed to demote an exemplar (raise to require more than one re-ask)")
    # ---- live model-judge (opt-in; default off = StubJudge, zero behavior change) ----
    ap.add_argument("--judge", action="store_true",
                    help="enable the live model-judge: on each demotion a fresh model adjudicates "
                         "(CONTEST restores a correct answer; ACCEPT lets the demotion stand). "
                         "Off by default -- no extra model calls unless you ask.")
    ap.add_argument("--judge-model", default="granite3.3:8b",
                    help="model that adjudicates demotions (independent of the responder; override with JUDGE_MODEL)")
    ap.add_argument("--judge-timeout", type=int, default=120, help="judge call timeout (seconds)")
    ap.add_argument("--judge-backend", default=None,
                    help="Ollama URL for the judge (defaults to --backend)")
    args = ap.parse_args()

    judge = (OllamaJudge(backend=args.judge_backend or args.backend,
                        model=args.judge_model, timeout=args.judge_timeout)
             if args.judge else StubJudge())
    membrane = Membrane(
        responder=OllamaChatResponder(args.backend),
        field_path=args.field,
        audit_path=args.audit,
        sim_min=args.sim_min,
        consensus_min=args.consensus_min,
        demote_threshold=args.demote_threshold,
        judge=judge,
    )
    srv = ThreadingHTTPServer(("127.0.0.1", args.listen), ProxyHandler)
    srv.membrane = membrane
    srv.backend = args.backend.rstrip("/")
    judge_s = f"ON (model={args.judge_model}, timeout={args.judge_timeout}s)" if args.judge else "off (StubJudge)"
    print(f"membrane proxy  http://127.0.0.1:{args.listen}  ->  {args.backend}\n"
          f"  field={args.field}  audit={args.audit}  sim_min={args.sim_min}  "
          f"demote_threshold={args.demote_threshold}  field_size={len(membrane.store)}\n"
          f"  judge: {judge_s}\n"
          f"  (gates /api/chat BOTH stream and non-stream; all other routes pass through)",
          file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[membrane] bye", file=sys.stderr)


if __name__ == "__main__":
    main()