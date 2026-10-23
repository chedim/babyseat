"""The proxy's HTTP base and doctrine helpers.

The one proxy is ``membrane.opencode`` (MembraneOpenAIHandler extends
RAGProxyHandler below). This module keeps what the proxy needs as a shared
surface:

  RAGProxyHandler             the transparent HTTP forward base (GET/POST/
                              PUT/DELETE/PATCH/HEAD piped to the upstream
                              model server; anything the concrete handler
                              does not claim) and the per-turn log line
  _RAG_SYSTEM / _DOCTRINE ... the doctrine: shipped once via the generated
                              opencode config's instructions file (a
                              byte-stable system prefix every provider can
                              cache) -- _inject re-stamps only unstamped
                              transcripts
  _inject                     inserts a context block as a system message
                              just before the last user turn (cache-aware:
                              the transcript prefix stays byte-stable)
  _clip / strip_machinery_text
  _model_chat(srv)            the background lanes' chat resolver: a cloud
                              rung rides its provider's OpenAI-compatible
                              chat path with memory-only auth headers, a
                              local model (or a router-less srv) falls back
                              to plain Ollama /api/chat

The old Membrane/Compactor-backed RAG proxy (field.py + its retrieval
screen, its momentum skew, its compactor) was retired with the exemplar
field; git history is the archive. The entity field's recall now lives in
entity_block.py and efgate.py, served by the opencode proxy.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding",
              "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "upgrade"}

_RAG_SYSTEM = None      # loaded at import-end (see _load_doctrine())

def _load_doctrine() -> str:
    """THE DOCTRINE'S SINGLE SOURCE OF TRUTH: the repo's doctrine.md,
    read at import — no more duplicated doctrine text inside code (the
    stale-exec-line exhibit: the code's copy outlived the repo's
    corrections and the model obeyed demolished tools)."""
    try:
        return (pathlib.Path(_REPO_DOCTRINE).read_text()
                if pathlib.Path(_REPO_DOCTRINE).exists()
                else _LEGACY_DOCTRINE_FALLBACK)
    except Exception:
        return _LEGACY_DOCTRINE_FALLBACK

_REPO_DOCTRINE = str(pathlib.Path(__file__).resolve().parent.parent
                     / "doctrine.md")
_LEGACY_DOCTRINE_FALLBACK = ""
_RAG_SYSTEM = _load_doctrine()

_DOCTRINE_STAMP = "membrane-doctrine: v1"


def _has_doctrine_stamp(messages) -> bool:
    """True when a system message already carries the doctrine stamp (i.e.
    the doctrine was delivered via the config's instructions file)."""
    for m in messages or []:
        if (isinstance(m, dict) and m.get("role") == "system"
                and _DOCTRINE_STAMP in (m.get("content") or "")):
            return True
    return False


def doctrine_instructions_text() -> str:
    """The instructions-file form of the doctrine: same text plus the stamp
    line, so _inject can recognize delivered doctrine and skip re-stamping."""
    return _DOCTRINE_STAMP + "\n" + _RAG_SYSTEM


def _clip(s: str, cap: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= cap else s[: max(1, cap - 1)] + "..."


_SYSTEM_TAG_SPAN = re.compile(
    r"<system-reminder>.*?</system-reminder>|<system-warning>.*?</system-warning>",
    re.DOTALL | re.IGNORECASE)


def strip_machinery_text(text: str) -> str:
    """Surface form of a stored text: machinery spans dropped, whitespace
    collapsed. Returns "" when nothing but machinery remained."""
    if not text:
        return ""
    t = _SYSTEM_TAG_SPAN.sub(" ", text)
    return " ".join(t.split())


class Injected(dict):
    """A system message membrane placed for THIS turn (_inject /
    _inject_after): recall, where-we-are, banners, watch reports. On the
    wire it is a plain dict; the claude-agent rung reads the type to keep
    these per-turn blocks out of its cached system prompt (#426)."""


def _inject(messages, block: str) -> list:
    """Insert a context block as a system message near the END of the
    conversation -- just before the last user turn -- instead of after the
    leading system messages.

    Cache-aware: providers (and local KV caches) match the prompt PREFIX; a
    block after the system header changes every turn, invalidating the cache
    for the whole transcript. Near the end, the system + full history prefix
    stays byte-stable across turns and only the tail re-rolls (the injected
    block itself is still per-turn-variable, but it now costs only its own
    bytes, not the transcript's).

    Doctrine split: the STATIC doctrine ships once, via the generated opencode
    config's `instructions` file (a byte-stable system prefix every provider
    can cache), so the per-turn tail carries ONLY the variable block. Clients
    that already carry the doctrine stamp -- the marker line
    `_DOCTRINE_STAMP` in an existing system message -- are not re-stamped;
    everyone else (bare clients, anthropic mode) gets both parts as before.
    """
    msgs = list(messages) if isinstance(messages, list) else []
    # doctrine-move ON = this process shipped the doctrine as the generated
    # config's instructions file (a byte-stable system prefix). Trust the
    # delivery: never re-stamp the preamble per turn, even when the stamp
    # check misses opencode's merged system shape (the every-turn ~2k waste).
    if getattr(_inject, "doctrine_delivered", False) or _has_doctrine_stamp(msgs):
        content = block
    else:
        content = _RAG_SYSTEM + "\n\n" + block
    i = len(msgs)
    while i > 0 and msgs[i - 1].get("role") == "user":
        i -= 1
    msgs.insert(i, Injected(role="system", content=content))
    return msgs


def _inject_after(messages, block: str) -> list:
    """Append the block as a system message AFTER the last user turn --
    the transcript's bottom edge. The speech-relay footer: membrane's
    voice (marked [membrane-says]) rides BELOW the user's message (the
    model reads it last, the TUI renders it at the bottom of the
    exchange) instead of inside the context block that sits above the
    user's message."""
    msgs = list(messages) if isinstance(messages, list) else []
    msgs.append(Injected(role="system", content=str(block)))
    return msgs


def think_value(name: str | None) -> bool | None:
    """`--think on|off|auto` -> the body value: on=True, off=False,
    auto (the default) = None = forward the payload verbatim."""
    if name == "on":
        return True
    if name == "off":
        return False
    return None            # auto / anything unrecognized = the dial says nothing


def apply_think_dial(payload: dict, think: bool | None) -> dict:
    """THE THINK DIAL'S TURN-LANE SEAM: the seat-level say in why the
    backend reasons (oils on|off into every forwarded body; auto =
    forward verbatim -- a non-thinking card never learns a think:true
    it 400s on). auto forwards verbatim; on is a DEFAULT (the client's
    own think wins); off is a hard kill switch -- it forces think:false
    even over the client's think:true (the operator said stop reasoning).
    Same object back when nothing is set."""
    if think is None:
        return payload
    if think and "think" in payload:      # on defers to the client
        return payload
    u2 = dict(payload)
    u2["think"] = bool(think)             # off overrides it
    return u2


def _ollama_chat(backend: str, model: str, messages: list,
                 temperature: float = 0.2, timeout: int = 120,
                 think: bool | None = None, lane: str = "background") -> str:
    """One non-stream Ollama /api/chat call -> the assistant text (or "").
    Its usage lands on the background tally under `lane` (bgusage)."""
    body = {"model": model, "stream": False, "temperature": float(temperature),
            "messages": messages}
    if think is not None:
        body["think"] = bool(think)
    try:
        req = urllib.request.Request(backend + "/api/chat",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except Exception as e:
        print(f"[bg] llm call failed ({e})", file=sys.stderr)
        return ""
    from . import bgusage
    bgusage.from_ollama(lane, model, resp)
    msg = resp.get("message") if isinstance(resp, dict) else None
    c = msg.get("content") if isinstance(msg, dict) else None
    return c.strip() if isinstance(c, str) else ""


def _openai_chat(url: str, headers: dict, model: str, messages: list,
                 temperature: float = 0.2, timeout: int = 120,
                 think: bool | None = None, lane: str = "background") -> str:
    """One non-stream OpenAI-compatible chat call -> the assistant text
    (or ""). The cloud-lane twin of _ollama_chat: the same background
    prompts, a different wire shape (choices[0].message.content)."""
    body = {"model": model, "stream": False, "temperature": float(temperature),
            "messages": messages}
    if think is not None:
        body["think"] = bool(think)
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except Exception as e:
        print(f"[bg] llm call failed ({e})", file=sys.stderr)
        return ""
    from . import bgusage
    bgusage.from_openai(lane, model, resp)
    ch = resp.get("choices") if isinstance(resp, dict) else None
    msg = (ch[0].get("message") if isinstance(ch, list) and ch
           and isinstance(ch[0], dict) else None)
    c = msg.get("content") if isinstance(msg, dict) else None
    return c.strip() if isinstance(c, str) else ""


def _claude_cortex_chat(srv, model: str, messages: list, lane: str) -> str:
    """One buffered claude-agent answer for the Idle thinker (an explicit
    pick only, minds_models.claude_allowed); '' on any failure."""
    from . import claude_agent as _CA
    from . import minds_models as _MM
    body = {"model": model, "messages": list(messages or []), "stream": False}
    auth = _MM.credential_mode(srv, "cortex")
    if auth:
        body[_CA.AUTH_KEY] = auth
    try:
        port = getattr(srv, "listen_port", None)
        if port is None:
            port = (getattr(srv, "server_address", None) or (None, None))[1]
        rep = _CA.open_reply(body, port=port)
        resp = json.loads(rep.read() or b"{}")
    except Exception as e:
        print(f"[bg] {model}: claude-agent call failed ({_CA.scrub(e)})", file=sys.stderr)
        return ""
    try:
        from . import bgusage
        bgusage.from_openai(lane, model, resp)
    except Exception:
        pass
    ch = resp.get("choices") if isinstance(resp, dict) else None
    msg = (ch[0].get("message") if isinstance(ch, list) and ch
           and isinstance(ch[0], dict) else None)
    c = msg.get("content") if isinstance(msg, dict) else None
    return c.strip() if isinstance(c, str) else ""


def _model_chat(srv):
    """A model-aware chat(backend, model, messages) for the background
    lanes (idle judge, synthesis, the night). The rung resolver: a cloud
    model (srv.router.records[m].backend = a registered provider) rides
    that provider's OpenAI-compatible chat path with its memory-only
    auth headers -- the same wire the chat lane uses; a local model (or a
    router-less srv) falls back to _ollama_chat. `lane` names the caller on
    the background token tally."""
    router = getattr(srv, "router", None)
    backend = getattr(srv, "backend", "")

    def chat(bg: str, model: str, messages: list,
             temperature: float = 0.2, timeout: int = 120,
             lane: str = "background") -> str:
        rec = router.records.get(model) if router is not None else None
        think = getattr(srv, "think", None)
        from . import claude_agent as _CA
        if _CA.is_claude_rung(router, model):
            from . import minds_models as _MM
            if _MM.claude_allowed(srv, "cortex", model):
                # the operator picked this rung for the Idle thinker (⚙,
                # 2026-10-23): it runs with the cortex's own credentials
                return _claude_cortex_chat(srv, model, messages, lane)
            # issue #384: background lanes never use the claude-agent rung
            # unless it is the mind's explicit pick
            print(f"[bg] {model}: background lanes never use the "
                  f"claude-agent rung ({lane}); no call made", file=sys.stderr)
            return ""
        if rec is not None and rec.backend:
            from .auth import PROVIDERS as _PROV
            prov = _PROV.get(rec.backend) or {}
            url = (prov.get("base", "") + prov.get("chat_path",
                   "/v1/chat/completions"))
            from . import auth as _auth
            key = (getattr(srv, "cloud_keys", None) or {}).get(rec.backend, "")
            headers = _auth._headers(rec.backend, key) if key else {}
            return _openai_chat(url, headers, model, messages,
                                temperature=temperature, timeout=timeout,
                                think=think, lane=lane)
        return _ollama_chat(bg or backend, model, messages,
                            temperature=temperature, timeout=timeout,
                            think=think, lane=lane)

    return chat


class RAGProxyHandler(BaseHTTPRequestHandler):
    """The transparent forward base. Routing: everything lands on the
    upstream model server verbatim; the concrete handler (opencode's
    MembraneOpenAIHandler) claims the paths it answers itself."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self._forward()

    def do_GET(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()

    def do_HEAD(self):
        self._forward()

    def _log_turn(self, verdict: dict, ctx: str | None, ex_idx=None,
                  field_size=None, replayed: int | None = None) -> None:
        """The one per-turn stderr line (the concrete handler calls it)."""
        sim = verdict.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        rp = f" replayed=#{replayed}" if replayed is not None else ""
        fl = "" if field_size is None else f" field={field_size}"
        print(f"[proxy] {verdict.get('verdict', '-'):14s} sim={sim_s}"
              f"{fl}{rp}", file=sys.stderr)

    # ---- transparent forward (everything else + error fallback) --------
    def _forward(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else None
        self._send_to_backend(raw)

    def _forward_raw(self, raw: bytes | None) -> None:
        self._send_to_backend(raw)

    def _send_to_backend(self, raw: bytes | None) -> None:
        url = self.server.backend + self.path
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP}
        from . import jwtauth as _JWT
        if _JWT.enabled():
            # JWT MODE: the caller's token is the seat's business, never the
            # model server's (Ollama / a provider) -- nor is its cookie
            headers = {k: v for k, v in headers.items()
                       if k.lower() not in ("authorization", "cookie")}
        req = urllib.request.Request(url, data=raw, method=self.command,
                                     headers=headers)
        try:
            r = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            r = e
        except Exception as e:
            out = json.dumps({"error": f"backend unreachable: {e}"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
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

    def log_message(self, *a):          # silence the default access log
        pass