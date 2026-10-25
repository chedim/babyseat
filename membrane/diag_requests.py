"""What the seat last sent to each model (GET /membrane/diag/requests).

Every outbound model request passes one recorder call at its send:
the proxy's upstream send (lanes._open_backend), the background lanes'
calls (rag._ollama_chat / rag._openai_chat), the delegate lane, and the
claude-agent rung's SDK prompt (claude_agent._consume). The last RING sit
in RAM -- never on disk -- newest first, each: ts, model, lane (chat /
loop / thinker / ...), the upstream path, the body as sent.

Recording is cheap: a shallow copy of the body (its messages list copied,
the messages themselves by reference) and the headers with their secrets
already masked. Sizes (chars and an estimated token count per message and
per section: system, tools, history, injected, latest_user, total) and the
redaction of secret values are computed only when the route is read.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from urllib.parse import urlsplit

RING = 5                  # the requests kept (the operator's N, 2026-10-25)
CHARS_PER_TOKEN = 4       # the estimate's ratio: router.Registry.estimate_tokens' ~4 chars/token
REDACTED = "[redacted]"
CLAUDE_PATH = "claude-agent-sdk"   # the claude-agent rung's "upstream": the SDK, in-process

_lock = threading.Lock()
_ring: deque = deque(maxlen=RING)
_ctx = threading.local()  # this thread's lane + injection, for a send that cannot name them

_SECRET_KEY = re.compile(r"^(authorization|proxy-authorization|cookie|x-api-key|api[-_]?key|"
                         r"x-goog-api-key|.*secret.*|.*password.*|(access|refresh|seat|auth|bearer)[-_]?token|"
                         r"token)$", re.I)
_SECRET_TEXT = [re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{3,}"),
                re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")]


def reset() -> None:
    with _lock:
        _ring.clear()
    _ctx.__dict__.clear()


def context(lane: str | None = None, injected: str | None = None) -> None:
    """Name this thread's lane (and the turn's injection) for the next send."""
    _ctx.lane = lane
    _ctx.injected = injected


def current() -> dict:
    return {"lane": getattr(_ctx, "lane", None), "injected": getattr(_ctx, "injected", None)}


def lane_of(srv, up: dict) -> str:
    """A proxy turn's lane: "loop" when its session is one of the field
    loops' (fieldpulse), else "chat"."""
    sid = str((up or {}).get("_turn_sid") or "")
    if sid:
        if sid.startswith("loop::"):
            return "loop"
        fp = getattr(srv, "_field_pulse", None)
        if sid in set((getattr(fp, "_sids", None) or {}).values()):
            return "loop"
    return "chat"


def _mask_headers(headers) -> dict:
    return {k: (REDACTED if _SECRET_KEY.match(str(k)) else v) for k, v in dict(headers or {}).items()}


def _push(entry: dict) -> None:
    with _lock:
        _ring.append(entry)


def record(model, url: str, body, lane: str | None = None, headers=None,
           injected: str | None = None) -> None:
    """One outbound request, as sent. Never raises (a diagnostic)."""
    try:
        cur = current()
        if lane is None:
            lane = cur["lane"]
            injected = injected if injected is not None else cur["injected"]
        b = dict(body) if isinstance(body, dict) else {"raw": body}
        if isinstance(b.get("messages"), list):
            b["messages"] = list(b["messages"])
        _push({"ts": time.time(), "model": str(model or b.get("model") or ""),
               "lane": lane or "unknown", "path": urlsplit(str(url or "")).path or str(url or ""),
               "kind": "http", "headers": _mask_headers(headers), "body": b,
               "injected": injected})
    except Exception:
        pass


def record_claude(model, system_prompt: str, prompt, blocks, tools, resume=None) -> None:
    """The claude-agent rung's call: the system prompt, the prompt (or its
    cache blocks) and the tool names the SDK is handed."""
    try:
        cur = current()
        body = {"model": str(model or ""), "system_prompt": system_prompt,
                "prompt": prompt if not blocks else None,
                "blocks": list(blocks) if blocks else None,
                "allowed_tools": list(tools or []), "resume": resume or None}
        _push({"ts": time.time(), "model": str(model or ""), "lane": cur["lane"] or "chat",
               "path": CLAUDE_PATH, "kind": "claude", "headers": {}, "body": body,
               "injected": cur["injected"]})
    except Exception:
        pass


# -- the read side (lazy) ------------------------------------------------------------

def _size(chars: int) -> dict:
    return {"chars": int(chars), "tokens": -(-int(chars) // CHARS_PER_TOKEN)}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") if isinstance(p, dict) and "text" in p
                       else json.dumps(p, default=str) for p in content)
    return "" if content is None else json.dumps(content, default=str)


def _http_sizes(body: dict, injected: str | None) -> dict:
    from .rag import Injected
    msgs = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
    sec = {k: 0 for k in ("system", "tools", "history", "injected", "latest_user")}
    per = []
    for i, m in enumerate(msgs):
        txt = _text(m.get("content"))
        n = len(txt) + (len(json.dumps(m["tool_calls"], default=str)) if m.get("tool_calls") else 0)
        inj = isinstance(m, Injected)
        part = n if inj else (len(injected) if injected and injected in txt else 0)
        where = ("system" if m.get("role") == "system" else
                 "latest_user" if i == last_user else "history")
        sec["injected"] += part
        if not inj:
            sec[where] += n - part
        per.append({"i": i, "role": m.get("role"), "injected": bool(inj or part), **_size(n)})
    tools = body.get("tools") or body.get("functions")
    sec["tools"] = len(json.dumps(tools, default=str)) if tools else 0
    return {"sections": {k: _size(v) for k, v in sec.items()}, "messages": per}


def _claude_sizes(body: dict) -> dict:
    blocks = body.get("blocks") or []
    prompt = "".join(str(b.get("text") or "") for b in blocks) if blocks else str(body.get("prompt") or "")
    head = sum(len(str(b.get("text") or "")) for b in blocks[:-1]) if blocks else 0
    sec = {"system": len(body.get("system_prompt") or ""),
           "tools": len(json.dumps(body.get("allowed_tools") or [])),
           "prompt": len(prompt), "prompt_cached_head": head, "prompt_tail": len(prompt) - head}
    return {"sections": {k: _size(v) for k, v in sec.items()},
            "blocks": [_size(len(str(b.get("text") or ""))) for b in blocks]}


def _known_secrets() -> list:
    out = []
    for get in (lambda: __import__("membrane.seatauth", fromlist=["x"]).seat_token(),
                lambda: __import__("membrane.jwtauth", fromlist=["x"]).internal_token()):
        try:
            out.append(get())
        except Exception:
            pass
    try:
        from . import auth as _A
        for p in {"anthropic", *_A.PROVIDERS}:
            try:
                out.append(_A.load_api_key(p))
            except Exception:
                pass
    except Exception:
        pass
    return [s for s in out if isinstance(s, str) and len(s) >= 8]


def _redact(obj, secrets: list):
    if isinstance(obj, dict):
        return {k: (REDACTED if _SECRET_KEY.match(str(k)) and v not in (None, "") else _redact(v, secrets))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(v, secrets) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        obj = bytes(obj).decode("utf-8", "replace")
    if isinstance(obj, str):
        for s in secrets:
            if s in obj:
                obj = obj.replace(s, REDACTED)
        for rx in _SECRET_TEXT:
            obj = rx.sub(lambda m: (m.group(1) if m.groups() else "") + REDACTED, obj)
    return obj


def view(model: str | None = None, n=None) -> dict:
    """The ring, newest first, with sizes and secrets redacted."""
    try:
        n = max(0, min(RING, int(n))) if n not in (None, "") else RING
    except (TypeError, ValueError):
        n = RING
    with _lock:
        rows = list(_ring)[::-1]
    if model:
        rows = [r for r in rows if r["model"] == model]
    secrets = _known_secrets()
    out = []
    for r in rows[:n]:
        body = r["body"]
        sizes = _claude_sizes(body) if r["kind"] == "claude" else _http_sizes(body, r.get("injected"))
        sizes["total"] = _size(len(json.dumps(body, default=str)))
        out.append(_redact({"ts": r["ts"], "model": r["model"], "lane": r["lane"], "path": r["path"],
                            "headers": r["headers"], "sizes": sizes, "body": body}, secrets))
    return {"ring": RING, "chars_per_token": CHARS_PER_TOKEN, "requests": out}
