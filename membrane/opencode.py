"""opencode-on-membrane: `python -m membrane.opencode`.

Starts the RAG proxy (membrane field + background compactor, from
membrane/rag.py), extends it with the OpenAI-compatible surface opencode
speaks, and launches opencode pointed at it:

    you (opencode) --> membrane (field: RAG + state) --> Ollama

What is INTERNAL vs PROXIED (the split this entrypoint implements):
  * INTERNAL -- everything about the membrane's own math and state:
      /v1/models       built here (Ollama's tag list, embed models filtered)
      /v1/embeddings   served by the field's own embedder
      /membrane/state  the JSON instrument (thermo, gate, compactor, audit)
      chat turns that ask about the system (field size, effective rank,
      free energy, compactor, ...) are ANSWERED HERE from the live field --
      computed, deterministic, zero model calls.
  * PROXIED -- everything model-shaped: /v1/chat/completions (grounded by the
    field's RAG injection, learned back into the field every turn) and any
    other path.

The model selected in opencode is the CORTEX's model too: each chat request
stamps `srv.compact_model`, so the background compactor distills with the same
model the user picked (unless --compact-model pins one). Embeddings are the
one place the selected model may not qualify: the resolver tries
--embed-model > a known Ollama embed model > the selected model, and if
nothing can embed, every chat turn returns 503 (err and block -- the field
cannot run without vectors).

Run:
  PYTHONPATH=. .venv/bin/python -m membrane.opencode
  # -> the proxy listens on 127.0.0.1:11500 and opencode opens pointed at it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np

from .embed import OllamaEmbedder
from .rag import Compactor, RAGProxyHandler, _clip, _inject
from .runtime import Membrane, OllamaChatResponder, OllamaJudge, StubJudge
from .variety import thermo_state

# tag markers meaning "not a chat model" (kept out of /v1/models). Cloud models
# (e.g. glm-4.7:cloud, deepseek-v4-pro:cloud, gpt-oss:20b-cloud) never match these
# markers, so they always pass.
_NON_CHAT_MARKERS = ("embed", "minilm", "bge", "whisper", "nomic-embed")
# tag markers meaning "an embedding model" (the embed-role candidates)
_EMBED_MARKERS = ("embed", "minilm", "bge", "arctic", "gte")


def _is_non_chat(name: str) -> bool:
    low = (name or "").lower()
    return any(m in low for m in _NON_CHAT_MARKERS)


# ---- the state/math intercept (internal, no model) ---------------------------
# Phrases that only a question ABOUT the membrane's own math/state uses. No bare
# single words ("membrane" appears in nearly every coding turn in this repo).
_STATE_STRONG = ("field state", "field stats", "system state",
                 "state of the system", "state of the field",
                 "free energy", "effective rank", "eff rank", "eff_rank",
                 "rank ratio", "safe frac", "spectrum entropy",
                 "what do you remember", "memory stats", "audit stats",
                 "how many exemplars", "field size")
_STATE_QSTARTS = ("what", "how", "show", "tell", "give", "print", "report",
                  "dump", "status")
_STATE_EXACT = ("status", "state", "field state", "field stats", "stats",
                "memory", "membrane state")


def _is_state_query(q: str) -> bool:
    """Conservative: the turn asks ABOUT the membrane itself (its math/state,
    not the codebase) -> answered internally from the live field. Guards:
    question-shaped (a '?' or a question/imperative opener), no code fence,
    short (a state ask is short) -- so ordinary coding prose never trips it."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    if not ("?" in low or low.startswith(_STATE_QSTARTS)):
        return False
    if any(m in low for m in _STATE_STRONG):
        return True
    # bare "membrane" only as a short direct question ("what's the membrane?")
    return ("membrane" in low and len(low) <= 120) or low in _STATE_EXACT


def _state_report(srv) -> str:
    """The internal answer for a state/math query: live numbers straight off
    the field, the compactor, and the audit. No model call."""
    mem = srv.membrane
    exs = list(mem.store.exemplars)
    n = len(exs)
    safe = sum(1 for e in exs if e.safe)
    retired = sum(1 for e in exs
                  if (getattr(e, "outcome", "") or "").startswith("compacted@"))
    notes = sum(1 for e in exs if getattr(e, "outcome", "") == "compact-note")
    th = thermo_state(mem.store)
    comp = (srv.compactor.stats if srv.compactor
            else {"cycles": 0, "clusters": 0, "notes": 0})
    lines = [
        "Membrane state (computed live on this server, no model call):",
        f"- backend {srv.backend} | embed model {getattr(mem.embedder, 'model', None) or 'unresolved'}",
        f"- field: {n} exemplars ({safe} safe / {n - safe} unsafe; "
        f"{retired} compacted, {notes} compact notes)",
        f"- effective rank {th['eff_rank']:.2f} (rank ratio {th['rank_ratio']:.2f}); "
        f"free energy {th['free_energy']:.2f} (safe_frac {th['safe_frac']:.2f}, "
        f"spectrum entropy {th['spectrum_entropy']:.2f})",
        f"- gate: sim_min={mem.sim_min} dedup={mem.dedup_sim} "
        f"reask_window={mem.reask_window_s:.0f}s demote_threshold={mem.demote_threshold}",
        f"- retrieval: k={srv.rag_k}, sim>={srv.rag_sim_min}, "
        f"budget {srv.rag_ctx_chars} chars",
    ]
    if srv.compactor is not None:
        lines.append(f"- cortex/compactor: cycles={comp['cycles']} "
                     f"clusters={comp['clusters']} notes={comp['notes']}  "
                     f"model={srv.compact_model}  every={srv.compactor.idle:.0f}s")
    else:
        lines.append("- cortex/compactor: disabled")
    lines.append(f"- audit: {_audit_count(mem.audit_path)} records")
    return "\n".join(lines)


# ---- OpenAI-shape helpers ----------------------------------------------------
def _last_user_text(body: dict) -> str:
    for m in reversed(body.get("messages") or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            return c
        if isinstance(c, list):
            parts = [b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") == "text"]
            t = " ".join(p for p in parts if p)
            if t.strip():
                return t
    return ""


def _sse_line(obj) -> bytes:
    return ("data: " + json.dumps(obj) + "\n\n").encode()


def _openai_sse_chunks(model: str, content: str, piece: int = 128):
    """One assistant text as OpenAI-style SSE chunks (the internal state answer)."""
    id_ = "chatcmpl-membrane-state"
    head = {"id": id_, "object": "chat.completion.chunk", "created": 0,
            "model": model}
    yield _sse_line({**head, "choices": [{"index": 0,
                                          "delta": {"role": "assistant"},
                                          "finish_reason": None}]})
    for i in range(0, len(content), piece):
        yield _sse_line({**head, "choices": [{"index": 0,
                                              "delta": {"content": content[i:i + piece]},
                                              "finish_reason": None}]})
    yield _sse_line({**head, "choices": [{"index": 0, "delta": {},
                                          "finish_reason": "stop"}]})
    yield b"data: [DONE]\n\n"


def _known_embed_names(backend: str) -> list:
    """Ollama model names whose tag looks like an embedding model."""
    try:
        with urllib.request.urlopen(backend + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read())
    except Exception:
        return ["mxbai-embed-large", "nomic-embed-text"]
    return [m.get("name", "") for m in tags.get("models", [])
            if any(k in m.get("name", "").lower() for k in _EMBED_MARKERS)]


def _probe_embedder(backend: str, model: str):
    """An OllamaEmbedder for ``model`` iff a real embed call succeeds."""
    try:
        emb = OllamaEmbedder(base_url=backend, model=model, timeout=60)
        emb.embed(["probe"])
        return emb
    except Exception as e:
        print(f"[opencode] {model} cannot embed ({_one_line(e)})", file=sys.stderr)
        return None


def _one_line(e: Exception) -> str:
    import re
    return re.sub(r"\s+", " ", str(e))[:140]


def _audit_count(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


def _chat_capable_models(backend: str) -> list:
    try:
        with urllib.request.urlopen(backend + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read())
    except Exception:
        return []
    return [m.get("name", "") for m in tags.get("models", [])
            if m.get("name") and not _is_non_chat(m.get("name"))]


def _pick_default_model(models: list, preferred: str | None) -> str | None:
    if preferred:
        for m in models:
            if m == preferred or m.split(":")[0] == preferred:
                return m
    for want in ("qwen3", "qwen2.5-coder", "llama3.1", "mistral", "gemma"):
        for m in models:
            if m.startswith(want):
                return m
    return models[0] if models else None


class MembraneOpenAIHandler(RAGProxyHandler):
    """The RAG handler + the OpenAI-compatible surface: /v1/models,
    /v1/chat/completions (the RAG gate + the internal state/math intercept),
    /v1/embeddings (the field's own embedder), /membrane/state. Everything
    else forwards to Ollama."""

    # ---- routing ----
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/v1/models", "/v1/models/"):
            self._v1_models(); return
        if path in ("/membrane/state", "/state"):
            self._membrane_state(); return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/chat/completions":
            self._v1_chat(); return
        if path == "/v1/embeddings":
            self._v1_embeddings(); return
        if path.startswith("/membrane/"):
            self._json_out({"error": "read-only; GET /membrane/state"}); return
        super().do_POST()

    def _json_err_raw(self, code: int, msg: str) -> None:
        out = json.dumps({"error": {"message": msg, "type": "membrane_error"}}
                         ).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _json_out(self, obj) -> None:
        out = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    # ---- /v1/models: internal (Ollama's list, embed models filtered out) ----
    def _v1_models(self) -> None:
        ids = _chat_capable_models(self.server.backend)
        self._json_out({"object": "list",
                        "data": [{"id": m, "object": "model",
                                  "owned_by": "membrane->ollama"} for m in ids]})

    # ---- /v1/embeddings: the field's own embedder ----
    def _v1_embeddings(self) -> None:
        body = self._json_body()
        inp = body.get("input", "")
        texts = inp if isinstance(inp, list) else [inp]
        texts = [str(t) for t in texts if str(t).strip()]
        if not texts:
            self._json_err_raw(400, "input required"); return
        srv = self.server
        if srv.membrane.embedder is None and self._resolve_embedder(None) is None:
            self._json_err_raw(503, srv.pending_embed_error); return
        try:
            vecs = srv.membrane.embedder.embed(texts)
        except Exception as e:
            self._json_err_raw(503, f"embed failed: {_one_line(e)}"); return
        self._json_out({"object": "list",
                        "data": [{"object": "embedding", "index": i,
                                  "embedding": [float(x) for x in v]}
                                 for i, v in enumerate(vecs)],
                        "model": srv.membrane.embedder.model})

    # ---- the embedder resolution ladder (embed role) ----
    def _resolve_embedder(self, selected_model: str | None):
        """Order: explicit --embed-model; a known Ollama embed model from
        /api/tags; the opencode-selected chat model (only if it actually
        embeds). On total failure the error is parked on the server and every
        chat/embeddings turn returns it -- err and block the chat."""
        srv = self.server
        backend = srv.backend
        cands = []
        if srv.embed_model_arg:
            cands.append(srv.embed_model_arg)
        cands += [c for c in _known_embed_names(backend) if c not in cands]
        if selected_model and selected_model not in cands:
            cands.append(selected_model)
        for cand in cands:
            probe = _probe_embedder(backend, cand)
            if probe is not None:
                srv.membrane.embedder = probe
                srv.resolved_embed_name = cand
                srv.pending_embed_error = None
                print(f"[opencode] embedder resolved: {cand}", file=sys.stderr)
                return probe
        srv.pending_embed_error = (
            "no Ollama model can serve embeddings; the membrane field cannot "
            "run without one. Pull an embedding model, e.g. "
            "`ollama pull mxbai-embed-large`, then retry.")
        print(f"[opencode] {srv.pending_embed_error}", file=sys.stderr)
        return None

    # ---- /v1/chat/completions: gate + RAG + learn; state turns internal ----
    def _v1_chat(self) -> None:
        srv = self.server
        mem = srv.membrane
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._forward_raw(raw); return
        model = str(body.get("model", "membrane"))
        # the opencode-selected model is the cortex's model too (unless pinned)
        if srv.compact_follows_chat:
            srv.compact_model = model
        # the embed role: resolve lazily (maybe ollama gained a model); block
        # the chat with a clear 503 when nothing can embed.
        if mem.embedder is None and self._resolve_embedder(model) is None:
            self._json_err_raw(503, srv.pending_embed_error); return
        try:
            verdict, vec, query, _reasked = mem.gate_turn(body)
            if vec is None:
                self._forward_raw(raw); return
        except Exception as e:
            print(f"[opencode] gate error, forwarding raw: {e}", file=sys.stderr)
            self._forward_raw(raw); return
        # ---- INTERNAL: membrane math/state about itself, zero model calls ----
        q = _last_user_text(body)
        if srv.state_intercept and _is_state_query(q):
            self._serve_state_answer(model, q, bool(body.get("stream", False)))
            return
        ctx = self._memory_block(vec)
        up = dict(body)
        if ctx:
            up["messages"] = _inject(body.get("messages") or [], ctx)
        try:
            self._v1_deliver(up, raw, query, vec, verdict, ctx)
        except Exception as e:
            print(f"[opencode] backend error, no field write: {e}", file=sys.stderr)
            self._forward_raw(raw)

    def _v1_deliver(self, up: dict, raw: bytes, query: str, vec, verdict: dict,
                    ctx: str | None) -> None:
        """Forward to Ollama's /v1/chat/completions (stream or buffered), tee
        to the client, then learn the exchange into the field."""
        srv = self.server
        mem = srv.membrane
        data = json.dumps(up).encode()
        req = urllib.request.Request(srv.backend + "/v1/chat/completions",
                                     data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        streaming = bool(up.get("stream", False))
        parts: list[str] = []
        tool_calls = False
        if streaming:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            with urllib.request.urlopen(req, timeout=300) as r:
                for line in r:                      # SSE, tee verbatim
                    if not line:
                        continue
                    self.wfile.write(line)
                    self.wfile.flush()
                    s = line.strip()
                    if not s.startswith(b"data:"):
                        continue
                    payload = s[len(b"data:"):].strip()
                    if not payload or payload == b"[DONE]":
                        continue
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for ch in obj.get("choices", []) or []:
                        d = ch.get("delta") or {}
                        t = d.get("content")
                        if isinstance(t, str):
                            parts.append(t)
                        if d.get("tool_calls"):
                            tool_calls = True
            self.wfile.flush()
            resp_obj = None
        else:
            resp_bytes = urllib.request.urlopen(req, timeout=300).read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            self.wfile.flush()
            try:
                resp_obj = json.loads(resp_bytes)
            except json.JSONDecodeError:
                resp_obj = None
            if resp_obj:
                ch = (resp_obj.get("choices") or [{}])[0]
                msg = ch.get("message") or {}
                c = msg.get("content")
                if isinstance(c, str):
                    parts.append(c)
                if msg.get("tool_calls"):
                    tool_calls = True
        content = "".join(parts)
        ex_idx = None
        # learn text turns; a pure tool-call turn has no human prose to ground
        # future turns on and the gate may have already learned from its user
        # text -- so only text-bearing answers are learned here.
        if content.strip() and not tool_calls:
            try:
                ex_idx = mem.record_escalation(query, vec, verdict, content,
                                               up.get("model", "membrane"),
                                               False)
            except Exception as e:
                print(f"[opencode] learn error (response already served): {e}",
                      file=sys.stderr)
        self._log_turn(verdict, ctx, ex_idx, len(mem.store))

    # ---- the internal state answer (no model call) ----
    def _serve_state_answer(self, model: str, q: str, streaming: bool) -> None:
        text = _state_report(self.server)
        print(f"[opencode] state-intercept: {q[:60]!r} (internal, no model)",
              file=sys.stderr)
        if not streaming:
            self._json_out({"object": "chat.completion", "created": 0,
                            "model": model,
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant",
                                                     "content": text}}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        try:
            for chunk in _openai_sse_chunks(model, text):
                self.wfile.write(chunk)
            self.wfile.flush()
        except Exception as e:
            print(f"[opencode] state answer stream error: {e}", file=sys.stderr)

    # ---- /membrane/state: the JSON instrument ----
    def _membrane_state(self) -> None:
        srv = self.server
        mem = srv.membrane
        exs = mem.store.exemplars
        out = {
            "backend": srv.backend,
            "embed_model": getattr(mem.embedder, "model", None),
            "field_size": len(exs),
            "safe": sum(1 for e in exs if e.safe),
            "retired": sum(1 for e in exs
                           if (getattr(e, "outcome", "") or "").startswith("compacted@")),
            "notes": sum(1 for e in exs if getattr(e, "outcome", "") == "compact-note"),
            "thermo": thermo_state(mem.store),
            "gate": {"sim_min": mem.sim_min, "dedup_sim": mem.dedup_sim,
                     "demote_threshold": mem.demote_threshold,
                     "reask_window_s": mem.reask_window_s,
                     "vague_filter": mem.vague_filter},
            "retrieval": {"k": srv.rag_k, "rag_sim_min": srv.rag_sim_min,
                          "ctx_chars": srv.rag_ctx_chars},
            "compact": dict(srv.compactor.stats) if srv.compactor else None,
            "compact_model": srv.compact_model,
            "compact_follows_chat": srv.compact_follows_chat,
            "state_intercept": srv.state_intercept,
            "audit_records": _audit_count(mem.audit_path),
        }
        self._json_out(out)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="Membrane field + opencode: a local field-backed coding "
                    "agent (math/state answered internally; model work proxied "
                    "to Ollama).")
    ap.add_argument("--listen", type=int, default=11500,
                    help="port for the membrane OpenAI-compatible proxy")
    ap.add_argument("--backend", default="http://localhost:11434",
                    help="upstream Ollama URL")
    ap.add_argument("--model", default=None, metavar="MODEL",
                    help="the model opencode opens with (default: best "
                         "available from Ollama's tags)")
    ap.add_argument("--field", default="rag-field.json")
    ap.add_argument("--audit", default="rag-audit.jsonl")
    ap.add_argument("--embed-model", default=None, metavar="MODEL",
                    help="the field's embedding model (default: auto -- known "
                         "Ollama embed models, then the selected model; chat "
                         "BLOCKS when nothing can embed)")
    ap.add_argument("-k", dest="k", type=int, default=4,
                    help="memories retrieved per turn")
    ap.add_argument("--rag-sim-min", type=float, default=0.45)
    ap.add_argument("--mem-chars", type=int, default=400)
    ap.add_argument("--ctx-chars", type=int, default=2400)
    ap.add_argument("--sim-min", type=float, default=0.85,
                    help="gate absorb threshold (audit-only in RAG mode)")
    ap.add_argument("--demote-threshold", type=int, default=1)
    ap.add_argument("--dedup-sim", type=float, default=0.95)
    ap.add_argument("--no-vague-filter", dest="vague_filter", action="store_false")
    ap.add_argument("--reask-window", type=float, default=300.0)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--judge-model", default="granite3.3:8b")
    ap.add_argument("--compact-every", type=float, default=90.0, metavar="S",
                    help="seconds between cortex compaction cycles (0 disables)")
    ap.add_argument("--compact-min-field", type=int, default=12)
    ap.add_argument("--compact-cluster-min", type=int, default=4)
    ap.add_argument("--compact-cluster-max", type=int, default=8)
    ap.add_argument("--compact-cluster-sim", type=float, default=0.60)
    ap.add_argument("--compact-mem-chars", type=int, default=400)
    ap.add_argument("--compact-model", default=None, metavar="MODEL",
                    help="pin the cortex/distillation model (default: follows "
                         "the model selected in opencode)")
    ap.add_argument("--no-state-intercept", dest="state_intercept",
                    action="store_false",
                    help="proxy every turn to the model, even membrane math/"
                         "state questions")
    ap.add_argument("--no-launch", dest="launch", action="store_false",
                    help="run only the proxy (don't start opencode)")
    ap.add_argument("passthrough", nargs="*",
                    help="extra args passed to opencode (use -- to separate)")
    args = ap.parse_args()
    passthrough = args.passthrough
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    # In launch mode opencode owns the terminal, so membrane's own stderr
    # (the [opencode]/[rag]/[compact] debug lines) would corrupt the TUI.
    # Redirect it to a log file; the terminal stays clean for opencode.
    _log_fh = None
    if args.launch:
        _log_fh = open("membrane-opencode.log", "a", buffering=1)
        sys.stderr = _log_fh

    backend = args.backend.rstrip("/")
    chat_models = _chat_capable_models(backend)
    if not chat_models:
        print(f"[opencode] WARNING: no chat-capable Ollama models at {backend} "
              f"(is Ollama running?)", file=sys.stderr)
    model = _pick_default_model(chat_models, args.model)
    if model is None and args.model:
        model = args.model          # respect an explicit choice even if unlisted

    judge = (OllamaJudge(backend=backend, model=args.judge_model, timeout=120)
             if args.judge else StubJudge())
    mem = Membrane(
        responder=OllamaChatResponder(backend),
        field_path=args.field,
        audit_path=args.audit,
        embedder=None,          # resolved by the ladder (below)
        sim_min=args.sim_min,
        demote_threshold=args.demote_threshold,
        reask_window_s=args.reask_window,
        judge=judge,
        observer=True,          # never replay: always forward, always learn
        vague_filter=bool(args.vague_filter),
    )
    # Membrane defaults embedder to OllamaEmbedder() when None; we want the
    # lazy resolution ladder instead, so clear it after construction.
    mem.embedder = None

    srv = ThreadingHTTPServer(("127.0.0.1", args.listen), MembraneOpenAIHandler)
    srv.membrane = mem
    srv.backend = backend
    srv.rag_k = args.k
    srv.rag_sim_min = max(0.0, min(1.0, float(args.rag_sim_min)))
    srv.rag_ctx_chars = max(200, int(args.ctx_chars))
    srv.rag_mem_chars = max(64, int(args.mem_chars))
    srv.compact_min_field = max(6, int(args.compact_min_field))
    srv.compact_min_cluster = max(2, int(args.compact_cluster_min))
    srv.compact_max_cluster = max(srv.compact_min_cluster,
                                  int(args.compact_cluster_max))
    srv.compact_cluster_sim = max(0.0, min(1.0, float(args.compact_cluster_sim)))
    srv.compact_mem_chars = max(64, int(args.compact_mem_chars))
    srv.compact_follows_chat = args.compact_model is None
    srv.compact_model = args.compact_model or model or "granite3.3:8b"
    srv.pending_embed_error = None
    srv.state_intercept = bool(args.state_intercept)
    srv.embed_model_arg = args.embed_model
    srv.resolved_embed_name = args.embed_model
    if args.compact_every > 0:
        srv.compactor = Compactor(srv, idle=args.compact_every)
        srv.compactor.start()
    else:
        srv.compactor = None

    # resolve the embedder AT BOOT: the field must be able to embed before a
    # single turn (a fresh field also needs nothing, but the resolver proves
    # the embed role early rather than on the first chat).
    if srv.embed_model_arg:
        emb = _probe_embedder(backend, srv.embed_model_arg)
        if emb is not None:
            mem.embedder = emb
            srv.pending_embed_error = None
    if mem.embedder is None:
        # ladder: known embed models, then the selected chat model
        for cand in _known_embed_names(backend) + ([model] if model else []):
            if cand in [srv.embed_model_arg]:
                continue
            probe = _probe_embedder(backend, cand)
            if probe is not None:
                mem.embedder = probe
                srv.resolved_embed_name = cand
                print(f"[opencode] embedder resolved: {cand}", file=sys.stderr)
                break

    # ---- the opencode config + launch ----
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "provider": {
            "membrane": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Membrane (field)",
                "options": {"baseURL": f"http://127.0.0.1:{args.listen}/v1"},
                "models": {m: {"name": m} for m in chat_models},
            },
        },
    }
    if model:
        cfg["model"] = f"membrane/{model}"
        cfg["small_model"] = f"membrane/{model}"
    fd, cfg_path = tempfile.mkstemp(prefix="membrane-opencode-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    env = dict(os.environ)
    env["OPENCODE_CONFIG"] = cfg_path

    print(f"membrane opencode  http://127.0.0.1:{args.listen}/v1  ->  {backend}\n"
          f"  field={args.field}  audit={args.audit}  field_size={len(mem.store)}\n"
          f"  chain: opencode -> membrane (RAG + learning) -> Ollama\n"
          f"  default model: {model or '?'}  |  cortex model: {srv.compact_model}"
          f"{' (follows opencode selection)' if srv.compact_follows_chat else ' (pinned)'}\n"
          f"  embedder: {getattr(mem.embedder, 'model', None) or 'UNRESOLVED -- chat blocked until an embed model is available'}\n"
          f"  state/math: {'answered internally from the live field' if srv.state_intercept else 'off'}\n"
          f"  opencode config: {cfg_path}",
          file=sys.stderr)

    # ---- signal + child lifecycle -------------------------------------------
    # In launch mode opencode owns the terminal: ctrl+c must interrupt opencode
    # (clear the current message), NOT kill membrane. So membrane IGNORES SIGINT
    # and instead shuts itself down when the opencode child exits (polled below)
    # or on SIGTERM. In proxy-only mode there is no child, so SIGINT is the
    # normal "stop the server" signal and is handled as before.
    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    if args.launch:
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    child = None
    try:
        if args.launch:
            opencode = shutil.which("opencode")
            if opencode is None:
                print("[opencode] `opencode` not on PATH; proxy-only "
                      "(point opencode at the /v1 URL above)", file=sys.stderr)
            else:
                extra = []
                if model and not any(a.startswith("--model") or a.startswith("-m")
                                     for a in passthrough):
                    extra = ["--model", f"membrane/{model}"]
                cmd = [opencode] + extra + list(passthrough)
                print(f"[opencode] launching: {' '.join(cmd)}", file=sys.stderr)
                child = subprocess.Popen(cmd, env=env)
        if child is None:
            # proxy-only: block forever, SIGINT/SIGTERM raise KeyboardInterrupt
            srv.serve_forever()
        else:
            # launch mode: serve until the opencode child exits (or a signal).
            # serve_forever() blocks, so poll the child on a short timeout loop.
            srv.timeout = 0.25
            while child.poll() is None:
                srv.handle_request()
    except KeyboardInterrupt:
        print("\n[membrane-opencode] bye", file=sys.stderr)
    finally:
        if child is not None:
            try:
                child.terminate(); child.wait(timeout=10)
            except Exception:
                pass
        if srv.compactor is not None:
            srv.compactor.stop()
        for p in (cfg_path,):
            try:
                os.unlink(p)
            except OSError:
                pass
        srv.server_close()
        if _log_fh is not None:
            try:
                _log_fh.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()