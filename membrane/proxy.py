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

Skeleton limitation: only NON-STREAMING /api/chat is gated (stream:true is
forwarded transparently). Set stream:false to route through Membrane. Streaming
support is a follow-up.

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

from .runtime import Membrane, OllamaChatResponder, OllamaJudge, StubJudge

HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding",
              "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "upgrade"}


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
        # Ollama defaults stream=true; only the non-stream path is gated here.
        if body.get("stream", True):
            self._forward_raw(raw)
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

    # ---- transparent forward (everything else + stream + gate errors) ----
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
          f"  (gate the /api/chat NON-STREAM path; stream:true and all other routes pass through)",
          file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[membrane] bye", file=sys.stderr)


if __name__ == "__main__":
    main()