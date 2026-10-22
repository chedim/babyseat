"""OLLAMA'S NATIVE CHAT SURFACE, THROUGH MEMORY (issue #221).

/api/chat and /api/generate on the proxy port run the SAME memory turn as
/v1/chat/completions: the request is translated into the OpenAI body, the /v1
turn runs untouched (gate, injection, capture, routing, the JWT stamp), and what
it writes is translated back into Ollama's shapes on the way out.

The seam is the writer: ``OllamaWire`` stands in for the handler's ``wfile``
while the /v1 turn runs. It reads the HTTP response the turn writes (status,
headers, then a JSON body or an SSE stream) and writes Ollama's instead: one
JSON body, or NDJSON chunks ending in a ``done: true`` record. The /v1 path never
knows; its bytes for /v1 clients stay what they were.

Pure translation lives here; the handler (lanes.MembraneOpenAIHandler) only
routes and swaps the writer."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from http import HTTPStatus

NATIVE_PATHS = ("/api/chat", "/api/generate")


# ---- request: Ollama -> OpenAI ------------------------------------------------

def is_load_request(kind: str, body: dict) -> bool:
    """No prompt / no messages = Ollama's load (or keep_alive:0 unload) request:
    nothing to remember -- the backend answers it verbatim."""
    if kind == "generate":
        return not (body.get("prompt") or body.get("images"))
    return not body.get("messages")


def _mime(b64: str) -> str:
    for head, mime in (("/9j/", "image/jpeg"), ("iVBOR", "image/png"),
                       ("R0lG", "image/gif"), ("UklGR", "image/webp")):
        if b64.startswith(head):
            return mime
    return "image/png"


def _content(text, images) -> object:
    """Ollama's text + base64 images -> an OpenAI content (a string, or parts)."""
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    if not images:
        return text
    parts = [{"type": "text", "text": text}]
    for im in images:
        im = str(im)
        url = im if im.startswith("data:") else f"data:{_mime(im)};base64,{im}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _args_str(args) -> str:
    """Ollama's tool-call arguments are an OBJECT; OpenAI's a JSON STRING."""
    if isinstance(args, str):
        return args
    return json.dumps(args if args is not None else {})


def _args_obj(args) -> dict:
    """OpenAI's JSON-string arguments -> Ollama's object ({} when unparseable)."""
    if isinstance(args, dict):
        return args
    if not args:
        return {}
    try:
        v = json.loads(args)
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _messages(msgs: list) -> list:
    out: list = []
    pending: list = []               # (call id, tool name) awaiting their tool result
    n = 0
    for m in msgs or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "user"
        om: dict = {"role": role, "content": _content(m.get("content"), m.get("images"))}
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            pending = []
            for tc in m["tool_calls"]:
                fn = (tc or {}).get("function") or {}
                cid = tc.get("id") or f"call_{n}"
                n += 1
                calls.append({"id": cid, "type": "function",
                              "function": {"name": fn.get("name") or "",
                                           "arguments": _args_str(fn.get("arguments"))}})
                pending.append((cid, fn.get("name") or ""))
            om["tool_calls"] = calls
        elif role == "tool":
            name = m.get("tool_name") or m.get("name") or ""
            cid = m.get("tool_call_id")
            if not cid and pending:
                hit = next((p for p in pending if p[1] == name), pending[0])
                pending.remove(hit)
                cid, name = hit[0], name or hit[1]
            om["tool_call_id"] = cid or f"call_{n}"
            if name:
                om["name"] = name
        out.append(om)
    return out


_OPT_MAP = {"temperature": "temperature", "top_p": "top_p", "seed": "seed",
            "stop": "stop", "frequency_penalty": "frequency_penalty",
            "presence_penalty": "presence_penalty"}


def to_openai(kind: str, body: dict) -> dict:
    """An Ollama /api/chat or /api/generate body -> the OpenAI chat body the /v1
    turn reads. stream defaults to TRUE (Ollama's default)."""
    if kind == "generate":
        msgs = []
        if body.get("system"):
            msgs.append({"role": "system", "content": str(body["system"])})
        msgs.append({"role": "user", "content": _content(body.get("prompt") or "",
                                                         body.get("images"))})
    else:
        msgs = _messages(body.get("messages") or [])
    stream = body.get("stream", True)
    stream = True if stream is None else bool(stream)
    up: dict = {"model": body.get("model") or "membrane", "messages": msgs,
                "stream": stream}
    if stream:
        up["stream_options"] = {"include_usage": True}
    opts = body.get("options") if isinstance(body.get("options"), dict) else {}
    for k, v in _OPT_MAP.items():
        if opts.get(k) is not None:
            up[v] = opts[k]
    npred = opts.get("num_predict")
    if isinstance(npred, int) and npred > 0:
        up["max_tokens"] = npred
    fmt = body.get("format")
    if fmt == "json":
        up["response_format"] = {"type": "json_object"}
    elif isinstance(fmt, dict):
        up["response_format"] = {"type": "json_schema",
                                 "json_schema": {"name": "response", "schema": fmt}}
    if "think" in body and body["think"] is not None:
        up["think"] = body["think"]           # the think dial's own key (rag.apply_think_dial)
    if body.get("tools"):
        up["tools"] = body["tools"]
    return up


# ---- response: OpenAI -> Ollama -----------------------------------------------

def created_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _reasoning(d: dict) -> str:
    for k in ("reasoning_content", "reasoning", "thinking"):
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _ollama_calls(calls) -> list:
    out = []
    for tc in calls or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        c = {"function": {"name": fn.get("name") or "",
                          "arguments": _args_obj(fn.get("arguments"))}}
        if tc.get("id"):
            c["id"] = tc["id"]
        out.append(c)
    return out


def _done_reason(finish) -> str:
    return "length" if finish == "length" else "stop"


class _Clock:
    """Durations in nanoseconds: total = wall time of the turn; prompt eval =
    until the first answer byte; eval = from then to the end; load unknown (0)."""

    def __init__(self, t0: float):
        self.t0 = t0
        self.first = None

    def mark(self):
        if self.first is None:
            self.first = time.monotonic()

    def counts(self, usage: dict | None) -> dict:
        now = time.monotonic()
        first = self.first if self.first is not None else now
        u = usage or {}
        return {"total_duration": max(1, int((now - self.t0) * 1e9)),
                "load_duration": 0,
                "prompt_eval_count": int(u.get("prompt_tokens") or 0),
                "prompt_eval_duration": int((first - self.t0) * 1e9),
                "eval_count": int(u.get("completion_tokens") or 0),
                "eval_duration": int((now - first) * 1e9)}


def _record(kind: str, model: str, text: str, thinking: str = "", calls=None,
            done: bool = False) -> dict:
    rec: dict = {"model": model, "created_at": created_at()}
    if kind == "generate":
        rec["response"] = text
        if thinking:
            rec["thinking"] = thinking
    else:
        msg: dict = {"role": "assistant", "content": text}
        if thinking:
            msg["thinking"] = thinking
        if calls:
            msg["tool_calls"] = calls
        rec["message"] = msg
    rec["done"] = done
    return rec


def from_openai(kind: str, model: str, obj: dict, clock: _Clock) -> dict:
    """A buffered OpenAI chat completion -> one Ollama response body."""
    ch = (obj.get("choices") or [{}])[0] or {}
    msg = ch.get("message") or {}
    text = msg.get("content")
    text = text if isinstance(text, str) else ""
    rec = _record(kind, model, text, _reasoning(msg),
                  _ollama_calls(msg.get("tool_calls")) if kind == "chat" else None, done=True)
    rec["done_reason"] = _done_reason(ch.get("finish_reason"))
    rec.update(clock.counts(obj.get("usage")))
    if obj.get("membrane"):                  # what membrane injected rides along (the /v1 shape)
        rec["membrane"] = obj["membrane"]
    return rec


def _error_text(raw: bytes) -> str:
    try:
        obj = json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace").strip() or "error"
    e = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(e, dict):
        return str(e.get("message") or e)
    if e:
        return str(e)
    return raw.decode(errors="replace").strip() or "error"


# ---- the writer seam ----------------------------------------------------------

class OllamaWire:
    """The handler's ``wfile`` while the /v1 turn runs for an Ollama request:
    parses the HTTP response the turn writes and writes Ollama's to ``real``."""

    def __init__(self, real, kind: str, model: str, t0: float | None = None):
        self.real, self.kind, self.model = real, kind, model
        self.clock = _Clock(t0 if t0 is not None else time.monotonic())
        self._head = b""
        self.status: int | None = None
        self.ctype = ""
        self.streaming = False
        self._body = bytearray()             # buffered mode: the whole body
        self._line = b""                     # streaming mode: the partial SSE line
        self._calls: dict = {}               # index -> accumulated tool call
        self._finish = None
        self._usage: dict | None = None
        self.finished = False

    # file-like
    def write(self, b) -> int:
        b = bytes(b)
        if self.status is None:
            self._head += b
            if b"\r\n\r\n" not in self._head:
                return len(b)
            head, _, rest = self._head.partition(b"\r\n\r\n")
            self._parse_head(head)
            b = rest
        if self.streaming:
            self._feed(b)
        else:
            self._body += b
        return len(b)

    def flush(self) -> None:
        try:
            self.real.flush()
        except Exception:
            pass

    def _parse_head(self, head: bytes) -> None:
        lines = head.decode("latin-1").split("\r\n")
        try:
            self.status = int(lines[0].split()[1])
        except (IndexError, ValueError):
            self.status = 502
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            if k.strip().lower() == "content-type":
                self.ctype = v.strip().lower()
        if self.status == 200 and self.ctype.startswith("text/event-stream"):
            self.streaming = True
            self._send_head(200, "application/x-ndjson", None)

    def _send_head(self, code: int, ctype: str, length: int | None) -> None:
        try:
            phrase = HTTPStatus(code).phrase
        except ValueError:
            phrase = ""
        h = [f"HTTP/1.1 {code} {phrase}", f"Content-Type: {ctype}",
             f"Date: {datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')}"]
        if length is None:
            h += ["Cache-Control: no-cache", "Connection: close"]
        else:
            h.append(f"Content-Length: {length}")
        self.real.write(("\r\n".join(h) + "\r\n\r\n").encode("latin-1"))

    def _emit(self, rec: dict) -> None:
        self.real.write(json.dumps(rec).encode() + b"\n")
        self.real.flush()

    # streaming: SSE in, NDJSON out
    def _feed(self, b: bytes) -> None:
        buf = self._line + b
        *lines, self._line = buf.split(b"\n")
        for ln in lines:
            self._event(ln.strip())

    def _event(self, s: bytes) -> None:
        if not s.startswith(b"data:"):
            return
        payload = s[5:].strip()
        if not payload or payload == b"[DONE]":
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        if obj.get("error") and not obj.get("choices"):
            # a turn that failed mid-stream (#431: the seat-busy error):
            # Ollama's own NDJSON error line, never a silent empty answer
            e = obj["error"]
            self._emit({"error": str(e.get("message") if isinstance(e, dict) else e)})
            return
        if isinstance(obj.get("usage"), dict) and obj["usage"]:
            self._usage = obj["usage"]
        for ch in obj.get("choices") or []:
            d = ch.get("delta") or {}
            if ch.get("finish_reason"):
                self._finish = ch["finish_reason"]
            text = d.get("content") if isinstance(d.get("content"), str) else ""
            think = _reasoning(d)
            for tc in d.get("tool_calls") or []:
                i = int(tc.get("index", 0) or 0)
                c = self._calls.setdefault(i, {"function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    c["id"] = tc["id"]
                fn = tc.get("function") or {}
                c["function"]["name"] += fn.get("name") or ""
                a = fn.get("arguments")
                c["function"]["arguments"] += a if isinstance(a, str) else (json.dumps(a) if a else "")
            if text or think:
                self.clock.mark()
                self._emit(_record(self.kind, self.model, text, think))

    def finish(self) -> None:
        """The turn returned: close the Ollama response."""
        if self.finished:
            return
        self.finished = True
        if self.streaming:
            if self._line:
                self._event(self._line.strip())
                self._line = b""
            if self._calls and self.kind == "chat":
                self.clock.mark()
                calls = _ollama_calls([self._calls[i] for i in sorted(self._calls)])
                self._emit(_record(self.kind, self.model, "", calls=calls))
            rec = _record(self.kind, self.model, "", done=True)
            rec["done_reason"] = _done_reason(self._finish)
            rec.update(self.clock.counts(self._usage))
            self._emit(rec)
            return
        if self.status is None:
            self._write_json(500, {"error": "the memory turn wrote no answer"})
            return
        raw = bytes(self._body)
        if self.status != 200:
            self._write_json(self.status, {"error": _error_text(raw)})
            return
        try:
            obj = json.loads(raw) if raw else {}
        except ValueError:
            obj = None
        if not isinstance(obj, dict) or "choices" not in obj:
            # not an OpenAI completion (a stray pass-through): the bytes go out as they are
            self._send_head(200, self.ctype or "application/json", len(raw))
            self.real.write(raw)
            return
        self.clock.first = self.clock.t0      # buffered: the whole wall time is eval time
        self._write_json(200, from_openai(self.kind, self.model, obj, self.clock))

    def _write_json(self, code: int, obj: dict) -> None:
        out = json.dumps(obj).encode()
        self._send_head(code, "application/json; charset=utf-8", len(out))
        self.real.write(out)
        self.real.flush()


def log(msg: str) -> None:
    print(f"[ollama-api] {msg}", file=sys.stderr)
