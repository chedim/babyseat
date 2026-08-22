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
import signal
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .runtime import (Membrane, OllamaChatResponder, AnthropicMessagesResponder,
                      OllamaJudge, StubJudge, StubResponder, _last_user, _response_text,
                      hint_system_message)
from .buffer import BufferView, SituationBuffer
from .cell import Cell
from .curiosity import CURIOSITY_MIN
from .pool import CellPool

HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding",
              "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "upgrade"}

# auth-shaped headers we forward to Anthropic but NEVER log or persist. The
# cortex-experiment-token discipline: the API key is passthrough only.
AUTH_HEADERS = {"x-api-key", "authorization", "anthropic-version"}


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


def _anthropic_sse_events(model: str, content: str):
    """Yield Anthropic ``/v1/messages`` SSE events for a pre-known answer (a
    replay under ``--absorb``). Mirrors ``_ndjson_chunks`` for the Ollama path:
    the recalled action is delivered as ``text_delta`` events so a streaming
    client renders it progressively. Each yield is one ``event:``/``data:`` pair
    plus the blank-line separator, as bytes ready to write to the wire."""
    def frame(etype: str, obj: dict) -> bytes:
        return f"event: {etype}\ndata: {json.dumps(obj)}\n\n".encode()
    msg = {"id": "msg_membrane_replay", "type": "message", "role": "assistant",
           "content": [], "model": model, "stop_reason": None,
           "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
    yield frame("message_start", {"type": "message_start", "message": msg})
    yield frame("content_block_start",
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "text", "text": ""}})
    pieces = [content[i:i + 256] for i in range(0, len(content), 256)] if content else []
    for p in pieces:
        yield frame("content_block_delta",
                    {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": p}})
    yield frame("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield frame("message_delta",
                {"type": "message_delta",
                 "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                 "usage": {"output_tokens": 0}})
    yield frame("message_stop", {"type": "message_stop"})


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
        elif self.path.startswith("/v1/messages"):
            self._messages()
        else:
            self._forward()

    def do_GET(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def _anthropic_auth(self) -> dict:
        """Passthrough auth headers for the Anthropic backend, read per-request
        from the incoming headers. Never stored on the handler/instance (a
        ThreadingHTTPServer would race it), never written to the field/audit, and
        never printed -- the caller logs only the verdict/sim, not the key."""
        auth = {}
        for h in AUTH_HEADERS:
            v = self.headers.get(h)
            if v:
                auth[h] = v
        return auth

    # ---- the feeding boundary (stage 2: multi-cell) -------------------------
    def _maybe_deposit(self, body: dict) -> int | None:
        """Deposit this turn's user text into the shared buffer for the background cell
        pool. Only a REAL human turn enters: ``_last_user`` returns None for
        tool_result-only / empty turns, so the buffer never sees self-generated or
        non-conversational traffic (the safe=given-not-taken guardrail -- situations
        enter only through the boundary). Called only when ``self.server.buffer is not
        None`` (i.e. ``--cells`` on); when off the caller's guard short-circuits before
        here, so the live single-Membrane path pays only one ``is not None`` check.

        Stage 3: deposits with ``meta={"pending": True}`` and returns the situation's
        index -- a handle the caller stores on ``self._deposit_id`` and later passes to
        ``_maybe_attach`` once the forward returns, so the real response is teed into the
        same situation (``attach_response`` -> ``complete=True``). The cell pulls complete
        pairs and learns the real action; the boundary stays one-way."""
        q = _last_user(body)
        if q:
            return self.server.buffer.deposit(q, meta={"pending": True})
        return None

    def _maybe_attach(self, content: str) -> None:
        """Tee the real assistant response into the deposited situation (stage 3). Called
        after the response is produced -- never before -- so the gate/decide/replay path is
        untouched (this is a post-response side-effect). No-op when ``--cells`` is off or
        when this request had nothing to deposit (empty/tool_result turn -> ``_deposit_id``
        is None) or when there is no response text. The cell learns whatever the user
        received (real forward OR a replayed cached action); the response is model output
        only, never auth/keys (``_response_text`` reads only ``content``)."""
        if self._deposit_id is not None and self.server.buffer is not None and content:
            self.server.buffer.attach_response(self._deposit_id, content)

    # ---- the gate path ----------------------------------------------------
    def _chat(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._forward_raw(raw)
            return
        # the feeding boundary (stage 2): deposit the real user turn into the shared
        # buffer for the background cell pool. No-op when --cells is off (srv.buffer is
        # None); only _last_user-non-empty turns enter. One deposit per request,
        # before the stream/non-stream split, so both paths feed the buffer exactly once.
        # Stage 3: the returned id is the handle for the later attach (the response tee);
        # None means nothing was deposited (empty/tool_result turn -> nothing to attach).
        self._deposit_id = None
        if self.server.buffer is not None:
            self._deposit_id = self._maybe_deposit(body)
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
        self._maybe_attach(_response_text(resp))   # stage 3: tee the real response to the cell buffer
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
            if verdict["verdict"] == "act" and not mem.observer:
                self._stream_replay(verdict, body, query, vec, mem)
                return
            # observer (any verdict) OR escalate: tee the backend stream + learn.
            # In observer mode a gate ``act`` forwards instead of replaying, and
            # ``record_escalation`` audits ``would_absorb`` for it.
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
        self._maybe_attach(verdict.get("action", ""))   # stage 3: tee the replayed action to the cell buffer
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
        self._maybe_attach(content)                # stage 3: tee the real response to the cell buffer
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

    # ---- the Anthropic /v1/messages gate path (Claude Code, observer-only) ----
    def _messages(self):
        """Gate an Anthropic ``/v1/messages`` turn. Same shape as ``_chat``: run
        ``gate_turn`` (which also fires the observer ambient signals -- tool-error,
        re-ask-after-escalate), then deliver. In observer mode (the default for
        ``--anthropic``) every verdict FORWARDS to Anthropic and learns -- never
        replays a coding answer. The API key is passthrough only."""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._forward_raw(raw)
            return
        # the feeding boundary (stage 2): one deposit per request, before the
        # stream/non-stream split. No-op when --cells is off; only real user turns enter.
        # Stage 3: the returned id is the handle for the later attach (the response tee).
        self._deposit_id = None
        if self.server.buffer is not None:
            self._deposit_id = self._maybe_deposit(body)
        auth = self._anthropic_auth()
        if body.get("stream", False):
            self._messages_stream(raw, body, auth)
            return
        try:
            resp, decision = self.server.membrane.handle(body, auth=auth)
        except Exception as e:                       # never break the chat on a gate error
            print(f"[membrane] /v1/messages gate error, forwarding raw: {e}",
                  file=sys.stderr)
            self._forward_raw(raw)
            return
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        self._maybe_attach(_response_text(resp))   # stage 3: tee the real response to the cell buffer
        self._log_messages(decision)

    def _messages_stream(self, raw: bytes, body: dict, auth: dict) -> None:
        """Gate a STREAMING ``/v1/messages`` turn (Claude Code's default).

        Observer (default): ALWAYS forward Anthropic's SSE to the client verbatim
        while teeing ``text_delta`` text into the field; on ``message_stop`` learn
        the exemplar (auditing ``would_absorb`` when the gate had said ``act``).
        Never replays. ``--absorb`` (non-observer): on a gate ``act``, synthesize
        Anthropic SSE from the recalled action (no model call); on escalate (with
        or without hint), tee the upstream SSE + learn -- mirrors the Ollama
        ``_stream_escalate``. If the gate throws before headers, fall back to a
        transparent forward."""
        mem = self.server.membrane
        try:
            verdict, vec, query, _ = mem.gate_turn(body)
        except Exception as e:
            print(f"[membrane] /v1/messages stream gate error, forwarding raw: {e}",
                  file=sys.stderr)
            self._forward_raw(raw)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        try:
            if vec is None:                           # empty query (e.g. tool_result-only): forward, learn nothing
                self._messages_forward_stream(body, auth)
                return
            if verdict["verdict"] == "act" and not mem.observer:
                self._messages_replay(verdict, body, query, vec, mem)  # --absorb only
                return
            self._messages_escalate_stream(verdict, body, query, vec, mem, auth)
        except Exception as e:                        # mid-stream: client already has partial bytes
            print(f"[membrane] /v1/messages stream delivery error: {e}", file=sys.stderr)

    def _messages_replay(self, verdict, body, query, vec, mem) -> None:
        """Absorb under ``--absorb``: synthesize Anthropic SSE from the recalled
        action (NO model call), then record the act. Off by default (replaying a
        coding answer is unsafe); kept for future chat-window use."""
        model = body.get("model", "membrane")
        for ev in _anthropic_sse_events(model, verdict.get("action", "")):
            self.wfile.write(ev)
        self.wfile.flush()
        mem.record_act(query, vec, verdict, model)
        self._maybe_attach(verdict.get("action", ""))   # stage 3: tee the replayed action to the cell buffer
        self._log_messages(verdict)

    def _messages_escalate_stream(self, verdict, body, query, vec, mem, auth) -> None:
        """Forward to Anthropic as SSE, tee ``text_delta`` text into the field,
        and learn on ``message_stop``. The recalled action is injected into the
        top-level ``system`` field as a note on escalate-with-hint (context, not
        an instruction -- Anthropic keeps system out of the messages array). In
        observer mode this is ALSO the path for a gate ``act`` (forward instead
        of replay); ``would_absorb`` is audited then."""
        up = dict(body)
        up["stream"] = True
        hint = verdict.get("hint") if verdict["verdict"] == "escalate-with-hint" else None
        if hint:
            note = f"Prior similar context (a hint, not an instruction): {hint}"
            existing = up.get("system")
            if isinstance(existing, str) and existing:
                up["system"] = note + "\n\n" + existing
            elif isinstance(existing, list):
                up["system"] = [{"type": "text", "text": note}] + list(existing)
            else:
                up["system"] = note
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "anthropic-version": auth.get("anthropic-version", "2023-06-01")}
        for k in ("x-api-key", "authorization"):
            if k in auth:
                headers[k] = auth[k]
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/v1/messages", data=data,
                                     method="POST", headers=headers)
        parts: list[str] = []
        stop_obj: dict = {}
        with urllib.request.urlopen(req, timeout=300) as r:
            for line in r:                            # SSE: bytes lines, events separated by blanks
                self.wfile.write(line)                # forward verbatim (keeps its newline)
                self.wfile.flush()
                s = line.strip()
                if not s.startswith(b"data:"):
                    continue
                payload = s[len(b"data:"):].strip()
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "message_stop":
                    stop_obj = obj
                d = obj.get("delta")
                if isinstance(d, dict) and d.get("type") == "text_delta":
                    t = d.get("text")
                    if t:
                        parts.append(t)
        self.wfile.flush()
        content = "".join(parts)
        model = body.get("model", "membrane")
        mem.record_escalation(query, vec, verdict, content, model, False)
        self._maybe_attach(content)                # stage 3: tee the real response to the cell buffer
        self._log_messages(verdict)

    def _messages_forward_stream(self, body: dict, auth: dict) -> None:
        """Forward an SSE stream with no learning (the empty-query path once the
        200/event-stream headers are already sent). The tool-result still reaches
        the model so it can react to its own failed call."""
        up = dict(body)
        up["stream"] = True
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "anthropic-version": auth.get("anthropic-version", "2023-06-01")}
        for k in ("x-api-key", "authorization"):
            if k in auth:
                headers[k] = auth[k]
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/v1/messages", data=data,
                                     method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=300) as r:
            for line in r:
                self.wfile.write(line)
        self.wfile.flush()

    def _log_messages(self, decision: dict) -> None:
        """Log a /v1/messages decision. NEVER logs the auth/key -- only verdict + sim."""
        sim = decision.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        tag = decision["verdict"]
        if self.server.membrane.observer and tag == "act":
            tag = "act→fwd"          # observer never replays; show the forward
        print(f"[membrane] {tag:18s} sim={sim_s}  field={len(self.server.membrane.store)}",
              file=sys.stderr)

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
    ap = argparse.ArgumentParser(
        description="Membrane proxy: a field-first front door in front of Ollama or Anthropic.")
    ap.add_argument("--listen", type=int, default=11435, help="port to listen on (127.0.0.1 only)")
    ap.add_argument("--backend", default=None,
                    help="upstream URL (default: http://localhost:11434 for Ollama, "
                         "https://api.anthropic.com with --anthropic)")
    ap.add_argument("--field", default="membrane-field.json", help="path to the exemplar field store")
    ap.add_argument("--audit", default="membrane-audit.jsonl", help="path to the append-only audit log")
    ap.add_argument("--sim-min", type=float, default=0.85,
                    help="cosine cutoff for absorb (conservative for the bootstrap; loosen once audit proves safe)")
    ap.add_argument("--consensus-min", type=float, default=0.60)
    ap.add_argument("--dedup-sim", type=float, default=0.95,
                    help="learn-time guard: don't append a near-identical situation above this cosine "
                         "(stops the field filling with same-situation/different-action copies of vague "
                         "prompts; set >1.0 to disable). Default 0.95.")
    ap.add_argument("--no-neg-followup", dest="neg_followup", action="store_false",
                    help="disable negative-follow-up demotion. By default a short complaint right "
                         "after an absorb ('not working', 'wrong', 'no') demotes the replayed exemplar "
                         "so you needn't repeat the query to signal 'bad'. Can false-demote (e.g. 'no' "
                         "answering a yes/no question -- guarded, not eliminated); pass this to revert "
                         "to verbatim-re-ask-only.")
    ap.add_argument("--demote-threshold", type=int, default=1,
                    help="re-asks needed to demote an exemplar (raise to require more than one re-ask)")
    # ---- live model-judge (default ON; opt out with --no-judge) ----
    # Fires ONLY on an actual demotion (a safe flip) -- the rare moment where adjudication
    # matters, never every turn. The internal self-check (the apoptosis half of the
    # two-authority boundary): a neg-followup false positive that wrongly demotes a good
    # answer gets CONTESTed and restored with no user labor. --no-judge reverts to StubJudge.
    ap.add_argument("--judge", dest="judge", action="store_true", default=True,
                    help="(default on; kept for compatibility -- judge is on unless --no-judge)")
    ap.add_argument("--no-judge", dest="judge", action="store_false",
                    help="disable the live model-judge. By default every demotion (a safe flip) "
                         "triggers a fresh model to adjudicate -- CONTEST restores a correct answer "
                         "that was wrongly demoted (e.g. a neg-followup false positive), ACCEPT lets "
                         "the demotion stand. Fires only on a demotion, never per turn.")
    ap.add_argument("--judge-model", default="granite3.3:8b",
                    help="model that adjudicates demotions (independent of the responder; override with JUDGE_MODEL)")
    ap.add_argument("--judge-timeout", type=int, default=120, help="judge call timeout (seconds)")
    ap.add_argument("--judge-backend", default=None,
                    help="Ollama URL for the judge (defaults to the Ollama backend; the judge stays "
                         "Ollama even in --anthropic mode)")
    # ---- Anthropic /v1/messages adapter + observer-only mode ----
    ap.add_argument("--anthropic", action="store_true",
                    help="gate Anthropic /v1/messages (Claude Code) instead of Ollama /api/chat. "
                         "Backend defaults to https://api.anthropic.com; --observer is implied "
                         "(never replays a coding answer). ANTHROPIC_API_KEY is passed through from "
                         "the client's x-api-key header and NEVER stored/logged.")
    ap.add_argument("--observer", action="store_true",
                    help="observer-only: forward every turn + learn + quality-label, NEVER replay "
                         "(implied by --anthropic). The field's value is its variety, not token savings.")
    ap.add_argument("--absorb", action="store_true",
                    help="re-enable replay (absorb) in --anthropic mode for future chat-window use. "
                         "OFF by default -- replaying a coding answer is unsafe (the right action "
                         "depends on the full tool-using transcript, not a cosine match).")
    ap.add_argument("--vague-min-tokens", type=int, default=4,
                    help="learn-time vague filter: skip situations shorter than this many words "
                         "(with no ?/path/digit/identifier/stem) -- stops 'yes'/'ok'/'beep'-class "
                         "junk filling the field. Default 4. Conservative (false-negative = learn).")
    ap.add_argument("--no-vague-filter", dest="vague_filter", action="store_false",
                    help="disable the vague filter (learn every situation, however thin).")
    ap.add_argument("--variety-gate", action="store_true",
                    help="only append an exemplar if it grows the field's effective rank (adds "
                         "variety, not just count). 2x SVD per learn on a big field, so OFF by "
                         "default; the cheap vague+dedup cover most cases. The principled "
                         "entropy-based learn gate.")
    # ---- the field's thermodynamic state: variety recorded per decision, opt-in
    # adaptive temperature (the "only variety can absorb variety" steering) ----
    ap.add_argument("--variety-temp", action="store_true",
                    help="ADAPTIVE TEMPERATURE: ease sim_min toward --sim-min-floor as the "
                         "field's free_energy (safe variety) grows. A low-variety field stays "
                         "conservative; a grown-SAFE field can afford to absorb more. OFF by "
                         "default (static threshold). 'only SAFE variety absorbs variety' -- a "
                         "junk field (variety but all unsafe) does NOT get eager, because the "
                         "disorder tax keeps free_energy low.")
    ap.add_argument("--temp-driver", default="free_energy",
                    choices=["free_energy", "spectrum_entropy"],
                    help="what drives adaptive eagerness: 'free_energy' (default, the safe-"
                         "weighted VALUE number -- safe variety earns eagerness, junk does not) "
                         "or 'spectrum_entropy' (raw variety, ignores safety -- the reckless one).")
    ap.add_argument("--temp-tau", type=float, default=1.0,
                    help="saturating constant in eagerness = val/(val+temp_tau). Smaller = relax "
                         "sooner (need less variety); larger = need more variety before easing.")
    ap.add_argument("--sim-min-floor", type=float, default=0.70,
                    help="the eagerness lower bound -- never get more eager than this. 0.70 is "
                         "the validated safe cosine cutoff (real_knee experiment).")
    # ---- the veil: nudge everything that transits this instance ----
    ap.add_argument("--nudge-sigma", type=float, default=0.0,
                    help="the veil: per-veil Gaussian perturbation of the incoming situation "
                         "embedding (added then renormalized), applied to EVERY message through "
                         "this instance -- both the gate query and the stored exemplar vec. The "
                         "forwarded prompt text is NEVER garbled; only the field's representation "
                         "is veiled. 0.0 = identity veil (off, zero behaviour change). >0 lets a "
                         "low-variety workload build a varied field, and lets independent veils "
                         "(different --nudge-seed) realize divergent variety trajectories from "
                         "the same traffic -- without any central input or orchestrator.")
    ap.add_argument("--nudge-seed", type=int, default=0,
                    help="seed for the veil's nudge RNG. Each veil seeds its own so independent "
                         "instances diverge; the same seed reproduces the same nudge (reproducible).")
    ap.add_argument("--tag", default=None,
                    help="instance label for a multi-veil run. When set, the field/audit paths "
                         "default to membrane-field-<tag>.json / membrane-audit-<tag>.jsonl so "
                         "independent veils own independent fields. Overridden by explicit "
                         "--field/--audit. Also shown in the banner.")
    ap.add_argument("--cells", type=int, default=0,
                    help="run N curiosity-driven cells in the background that pull real user turns "
                         "from a shared buffer and grow per-cell fields (observer mode, never author "
                         "a response). 0 = off (default): the live single-Membrane path is unchanged "
                         "except one cheap guard per request. Cells persist across boots (fresh=False) "
                         "and are read-only on the buffer (the feeding boundary).")
    args = ap.parse_args()

    # --tag defaults the field/audit paths so independent veils own independent fields
    # (the decentralized design: no central store, no clobbering). Explicit --field/
    # --audit still win.
    if args.tag:
        if args.field == "membrane-field.json":
            args.field = f"membrane-field-{args.tag}.json"
        if args.audit == "membrane-audit.jsonl":
            args.audit = f"membrane-audit-{args.tag}.jsonl"

    anthropic = args.anthropic
    backend = args.backend or ("https://api.anthropic.com" if anthropic else "http://localhost:11434")
    # observer is implied by --anthropic unless --absorb explicitly re-enables replay
    observer = args.observer or (anthropic and not args.absorb)
    if anthropic and args.absorb and not args.observer:
        observer = False

    judge = (OllamaJudge(backend=args.judge_backend or ("http://localhost:11434" if anthropic else backend),
                        model=args.judge_model, timeout=args.judge_timeout)
             if args.judge else StubJudge())
    responder = (AnthropicMessagesResponder(backend) if anthropic
                 else OllamaChatResponder(backend))
    membrane = Membrane(
        responder=responder,
        field_path=args.field,
        audit_path=args.audit,
        sim_min=args.sim_min,
        consensus_min=args.consensus_min,
        dedup_sim=args.dedup_sim,
        neg_followup=args.neg_followup,
        demote_threshold=args.demote_threshold,
        judge=judge,
        observer=observer,
        vague_filter=args.vague_filter,
        vague_min_tokens=args.vague_min_tokens,
        variety_gate=args.variety_gate,
        variety_temp=args.variety_temp,
        temp_driver=args.temp_driver,
        temp_tau=args.temp_tau,
        sim_min_floor=args.sim_min_floor,
        nudge_sigma=args.nudge_sigma,
        nudge_seed=args.nudge_seed,
    )
    # ---- the feeding boundary + background cell pool (stage 2) ----------------
    # When --cells is off (default), buf/pool stay None and the live single-Membrane
    # path pays only one ``if srv.buffer is not None`` guard per request (never taken).
    # When on, real user turns are deposited into a shared thread-safe buffer and N
    # curiosity-driven cells pull from a read-only view in daemon threads, growing
    # per-cell fields. Cells never serve live requests (observer mode) and never feed
    # back (one-way feed). The gate/decide/replay path (runtime.py/gate.py) is untouched.
    pool = None
    buf = None
    if args.cells > 0:
        buf = SituationBuffer()
        view = BufferView(buf)
        cells = []
        for i in range(args.cells):
            cells.append(Cell(
                tag=f"cell-{i}",
                responder=StubResponder(tag=f"cell-{i}"),
                field_path=f"membrane-field-cell-{i}.json",
                audit_path=f"membrane-audit-cell-{i}.jsonl",
                sim_min=args.sim_min,
                consensus_min=args.consensus_min,
                dedup_sim=args.dedup_sim,
                observer=True,            # the never-authors guardrail -- do NOT flip
                vague_filter=False,        # isolate curiosity+veil (the experiment config)
                variety_gate=False,
                variety_temp=False,
                nudge_sigma=0.25,          # the veil: cells diverge (experiment-proven)
                nudge_seed=i + 1,          # per-cell seed -> different manifolds -> specialization
                curiosity_on=True,
                curiosity_min=CURIOSITY_MIN,
                fresh=False,              # persist across boots (load existing field, keep growing)
            ))
        pool = CellPool(cells, view, interval=2.0)
        pool.start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.listen), ProxyHandler)
    srv.membrane = membrane
    srv.backend = backend.rstrip("/")
    srv.buffer = buf          # None when OFF (the off sentinel the deposit guard checks)
    srv.cell_pool = pool      # None when OFF (nothing for the shutdown finally to stop)
    judge_s = f"ON (model={args.judge_model}, timeout={args.judge_timeout}s)" if args.judge else "off (StubJudge)"
    neg_s = "on (complaint follow-up demotes; --no-neg-followup to disable)" if args.neg_followup else "off (verbatim re-ask only)"
    mode_s = ("Anthropic /v1/messages" if anthropic else "Ollama /api/chat")
    obs_s = ("OBSERVER (forward+learn, never replay)" if observer
             else ("absorb (replay on act)" if anthropic else "absorb (replay on act)"))
    vague_s = (f"on (min {args.vague_min_tokens} tokens; --no-vague-filter to disable)"
               if args.vague_filter else "off")
    variety_s = "ON (effective-rank grow gate; 2x SVD/learn)" if args.variety_gate else "off"
    vtemp_s = (f"ON (driver={args.temp_driver}, tau={args.temp_tau}, "
               f"floor={args.sim_min_floor}; sim_min eases as safe variety grows)"
               if args.variety_temp else "off (static sim_min)")
    nudge_s = (f"ON (sigma={args.nudge_sigma}, seed={args.nudge_seed}; "
               f"every transit veiled, prompt text untouched)" if args.nudge_sigma > 0
               else "off (identity veil)")
    cells_s = (f"ON ({args.cells} cells, sigma=0.25, curiosity_min={CURIOSITY_MIN}, "
               f"observer/fresh-persist/teed; per-cell field-cell-<i>; pool cadence 2s)"
               if args.cells > 0 else "off (single-Membrane path, unchanged)")
    tag_s = f"  tag={args.tag}" if args.tag else ""
    print(f"membrane proxy  http://127.0.0.1:{args.listen}  ->  {backend}  [{mode_s}]\n"
          f"  field={args.field}  audit={args.audit}  sim_min={args.sim_min}  "
          f"dedup_sim={args.dedup_sim}  demote_threshold={args.demote_threshold}  "
          f"field_size={len(membrane.store)}{tag_s}\n"
          f"  mode: {obs_s}\n"
          f"  neg-followup: {neg_s}\n"
          f"  vague filter: {vague_s}\n"
          f"  variety gate: {variety_s}\n"
          f"  adaptive temp: {vtemp_s}\n"
          f"  veil nudge: {nudge_s}\n"
          f"  cells: {cells_s}\n"
          f"  judge: {judge_s}\n"
          f"  (API key: passthrough only, never stored/logged; gates {mode_s} stream + non-stream)",
          file=sys.stderr)
    # SIGTERM (run.sh's TERM trap, or `kill`) -> KeyboardInterrupt so the except
    # catches it cleanly. Today nothing closes the socket; the finally also joins
    # the cell pool so a background cell mid-pull cannot hang the exit (the daemon
    # threads would die with the process anyway, but joining is bounded and clean).
    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[membrane] bye", file=sys.stderr)
    finally:
        if srv.cell_pool is not None:
            srv.cell_pool.stop()
        srv.server_close()


if __name__ == "__main__":
    main()