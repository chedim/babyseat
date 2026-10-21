"""THE CLAUDE-AGENT RUNG (issue #384): Claude through the Claude Agent SDK.

The SDK (`claude-agent-sdk`, an OPTIONAL extra: `pip install
'membrane[claude-agent]'`) drives the local `claude` CLI with that CLI's own
login. membrane never reads the login's token and never calls an Anthropic
API itself: a turn routed to a `claude-agent:<model>` rung is handed to the
SDK's query() -- the turn's system messages as the system prompt, the rest
as the prompt -- and the reply comes back as an ordinary OpenAI chat
response, streamed (chat.completion.chunk SSE lines) or buffered.

Claude Code's own tools are switched off (no built-in tools, nothing
auto-allowed, no MCP servers, no settings files, one turn): membrane's tool
loop stays the only tool surface. The SDK runs its own agent loop when it
gets tools, and it cannot hand a tool call back to a caller unexecuted, so
this rung is TEXT-ONLY: the turn's tool definitions are not passed on and
the reply never carries tool calls.

THE RULE (Anthropic's terms, the operator's 2026-10-21): Claude's replies
never become learned material. What membrane keeps from a Claude turn:
- learned: the person's side, and tool RESULTS (world data). Never Claude's
  reply text, its reasoning, or its tool-call arguments (the proxy hands
  the capturer an empty reply; the voice, the predictor, the A/B judge and
  the frame lint never see it);
- shown: the reply stays in the session transcript (the chat shows it, the
  conversation continues). The transcript row is marked Claude-made
  (MARK_KEY) so nothing later feeds it to the field.
The rule keys on the PROVIDER (the rung's backend, the response's
RESP_KEY stamp, the row's MARK_KEY), never on a model's name.

Opt-in: the router never escalates to a claude-agent rung on its own (see
router.OPT_IN_BACKENDS); the operator pins it or adds it to the ladder
(--ladder-include). Background lanes never use it.

Policy note: this relies on Anthropic allowing Agent SDK use with a Claude
plan login; see docs/claude-agent.md.
"""
from __future__ import annotations

import asyncio
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

PROVIDER = "claude-agent"
RUNG_PREFIX = PROVIDER + ":"
DEFAULT_MODELS = ("sonnet", "opus", "haiku")   # the CLI's own model aliases
MARK_KEY = "madeBy"                 # transcript rows: info[MARK_KEY] == PROVIDER
RESP_KEY = "membrane_provider"      # proxy responses/chunks: the answering provider
ENABLED_MARK = "cli-login"          # what `membrane auth claude-agent` stores:
                                    # not a secret, only "use the CLI's login"
INSTALL_HINT = ("pip install 'membrane[claude-agent]' (or: pip install "
                "claude-agent-sdk)")
LOGIN_HINT = ("install Claude Code, run `claude` once to log in, then "
              "`membrane auth claude-agent`")
STATUS_TIMEOUT_S = 20


class Unavailable(RuntimeError):
    """The SDK or the CLI is missing: the turn cannot reach Claude."""


# ---- names -------------------------------------------------------------------

def rung_names(models=DEFAULT_MODELS) -> list[str]:
    return [RUNG_PREFIX + m for m in models]


def is_rung_name(name) -> bool:
    return str(name or "").strip().lower().startswith(RUNG_PREFIX)


def sdk_model(name: str) -> str:
    n = str(name or "").strip()
    return n[len(RUNG_PREFIX):] if is_rung_name(n) else n


def _router(srv_or_router):
    if srv_or_router is None:
        return None
    if hasattr(srv_or_router, "records"):
        return srv_or_router
    return getattr(srv_or_router, "router", None)


def is_claude_rung(srv_or_router, model_name) -> bool:
    """Is this model a claude-agent rung? Keyed on the router record's
    PROVIDER (backend); a name the router does not know falls back to the
    rung namespace membrane itself mints (fail-closed)."""
    reg = _router(srv_or_router)
    rec = None
    if reg is not None:
        try:
            rec = reg.records.get(str(model_name or ""))
        except Exception:
            rec = None
    if rec is not None:
        return getattr(rec, "backend", "") == PROVIDER
    return is_rung_name(model_name)


def is_claude_made(info) -> bool:
    """A transcript row Claude made (by provider, never by model name)."""
    if not isinstance(info, dict):
        return False
    return info.get(MARK_KEY) == PROVIDER or info.get("providerID") == PROVIDER


def register_rungs(router, models=DEFAULT_MODELS) -> int:
    """The provider's rungs on the router: cloud kind, TEXT-ONLY caps
    (completion, no tools), opt-in (router.OPT_IN_BACKENDS)."""
    n = 0
    for name in rung_names(models):
        rec = router.records.get(name)
        if rec is None:
            n += router.add_cloud_models([name], provider=PROVIDER)
            rec = router.records.get(name)
        if rec is not None:
            rec.backend = PROVIDER
            rec.kind = "cloud"
            rec.caps = ["completion"]
    return n


# ---- who may use it -----------------------------------------------------------

def person_agent(agent) -> bool:
    """A person's lane (the chat, plan, the phone, a speaker) may take a
    claude-agent rung; the watch loops, the minds, the field's own calls
    never do."""
    a = str(agent or "").strip().lower()
    return a in ("", "build", "plan", "phone", "speaker")


def guard_turn_model(srv, model: str, agent, fallback: str) -> str:
    """The serve turn's model, with the background rule applied: a
    claude-agent rung on a non-person lane falls back."""
    if is_claude_rung(srv, model) and not person_agent(agent):
        print(f"[claude-agent] {model} refused on the {agent!r} lane "
              f"(background lanes never use it); {fallback} answers",
              file=sys.stderr)
        return fallback
    return model


# ---- the SDK and the CLI --------------------------------------------------------

def load_sdk():
    try:
        import claude_agent_sdk as sdk          # optional extra, lazy
    except ImportError as e:
        raise Unavailable(f"claude-agent: the Claude Agent SDK is not "
                          f"installed ({e}); {INSTALL_HINT}") from None
    if sdk is None:
        raise Unavailable(f"claude-agent: the Claude Agent SDK is not "
                          f"installed; {INSTALL_HINT}")
    return sdk


def find_cli() -> str | None:
    """The `claude` CLI the SDK would drive: on PATH, else the SDK's
    bundled copy."""
    p = shutil.which("claude")
    if p:
        return p
    try:
        import importlib.util
        spec = importlib.util.find_spec("claude_agent_sdk")
        if spec is not None and spec.origin:
            b = Path(spec.origin).parent / "_bundled" / "claude"
            if b.is_file():
                return str(b)
    except Exception:
        pass
    return None


def check_available():
    """Raise Unavailable (with what to do) unless the SDK and the CLI are
    both here. Returns the SDK module."""
    sdk = load_sdk()
    if not find_cli():
        raise Unavailable(f"claude-agent: the `claude` CLI was not found; "
                          f"{LOGIN_HINT}")
    return sdk


def cli_status(run=subprocess.run, cli: str | None = None) -> dict:
    """`claude auth status --json` -> {installed, logged_in, method, plan,
    cli, error}. Reads only the status fields; never the credentials."""
    cli = cli or find_cli()
    out = {"installed": bool(cli), "logged_in": False, "method": "",
           "plan": "", "cli": cli or "", "error": ""}
    if not cli:
        out["error"] = "the `claude` CLI was not found"
        return out
    try:
        r = run([cli, "auth", "status", "--json"], capture_output=True,
                text=True, timeout=STATUS_TIMEOUT_S)
        d = json.loads(r.stdout or "{}")
        out["logged_in"] = bool(d.get("loggedIn"))
        out["method"] = str(d.get("authMethod") or "")
        out["plan"] = str(d.get("subscriptionType") or "")
    except Exception as e:
        out["error"] = f"`claude auth status` failed: {str(e)[:160]}"
    return out


# ---- the turn -> the SDK ----------------------------------------------------------

def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content
                         if isinstance(p, dict) and p.get("type") in ("text", None)
                         and p.get("text"))
    return "" if content is None else str(content)


def split_messages(messages) -> tuple[str, str]:
    """OpenAI messages -> (system prompt, prompt). Every system message
    joins the system prompt (the doctrine and membrane's context blocks);
    the rest is the conversation. A lone user message is the prompt as is;
    a longer conversation is rendered as a transcript ending on the last
    message."""
    system, convo = [], []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        text = _text_of(m.get("content")).strip()
        if role == "system":
            if text:
                system.append(text)
            continue
        if role == "assistant" and m.get("tool_calls"):
            names = ", ".join(str((tc.get("function") or {}).get("name") or "?")
                              for tc in m["tool_calls"] if isinstance(tc, dict))
            text = (text + "\n" if text else "") + f"(called tools: {names})"
        if role == "tool":
            label = f"[tool result{': ' + str(m.get('name')) if m.get('name') else ''}]"
        else:
            label = f"[{role}]"
        if text:
            convo.append((role, label, text))
    sys_prompt = "\n\n".join(system)
    if len(convo) == 1 and convo[0][0] == "user":
        return sys_prompt, convo[0][2]
    prompt = "\n\n".join(f"{label}\n{text}" for _r, label, text in convo)
    if convo:
        prompt = ("The conversation so far, oldest first; answer its last "
                  "message.\n\n" + prompt)
    return sys_prompt, prompt


def build_options(sdk, system: str, model: str, stream: bool):
    return sdk.ClaudeAgentOptions(
        system_prompt=system or None,
        tools=[],                  # Claude Code's built-in tools: off
        allowed_tools=[],          # nothing auto-allowed
        mcp_servers={},            # no MCP surface of its own
        strict_mcp_config=True,    # ...nor the user's configured ones
        setting_sources=[],        # no settings files, no CLAUDE.md
        max_turns=1,
        model=sdk_model(model) or None,
        include_partial_messages=bool(stream),
    )


def _usage(u) -> dict:
    u = u if isinstance(u, dict) else {}
    pin = int(u.get("input_tokens") or 0) \
        + int(u.get("cache_read_input_tokens") or 0) \
        + int(u.get("cache_creation_input_tokens") or 0)
    pout = int(u.get("output_tokens") or 0)
    return {"prompt_tokens": pin, "completion_tokens": pout,
            "total_tokens": pin + pout}


async def _consume(sdk, messages, model, stream, put):
    system, prompt = split_messages(messages)
    opts = build_options(sdk, system, model, stream)
    partial = False
    async for m in sdk.query(prompt=prompt, options=opts):
        kind = type(m).__name__
        if kind == "StreamEvent":
            ev = getattr(m, "event", None) or {}
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    partial = True
                    put(("text", d["text"]))
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    put(("reasoning", d["thinking"]))
        elif kind == "AssistantMessage":
            err = getattr(m, "error", None)
            if err:
                raise RuntimeError(f"claude-agent: {err}")
            if not partial:
                for b in getattr(m, "content", None) or []:
                    if type(b).__name__ == "TextBlock" and getattr(b, "text", ""):
                        put(("text", b.text))
                    elif type(b).__name__ == "ThinkingBlock" and getattr(b, "thinking", ""):
                        put(("reasoning", b.thinking))
        elif kind == "ResultMessage":
            if getattr(m, "is_error", False):
                why = getattr(m, "result", None) or getattr(m, "errors", None) \
                    or getattr(m, "subtype", "error")
                raise RuntimeError(f"claude-agent: {why}")
            put(("usage", _usage(getattr(m, "usage", None))))


_END = object()


def iter_reply(messages, model: str, stream: bool = True, sdk=None):
    """The SDK's reply as ("text"|"reasoning"|"usage", value) items, in
    order. The async SDK runs on its own thread and loop (the proxy's
    handler threads are synchronous); an SDK error re-raises here."""
    sdk = sdk or check_available()
    q: queue.Queue = queue.Queue()

    def run():
        try:
            asyncio.run(_consume(sdk, messages, model, stream, q.put))
        except BaseException as e:          # carried to the reader
            q.put(("error", e))
        finally:
            q.put(_END)

    threading.Thread(target=run, daemon=True, name="claude-agent").start()
    while True:
        item = q.get()
        if item is _END:
            return
        if item[0] == "error":
            raise item[1]
        yield item


# ---- the reply as an OpenAI response ---------------------------------------------

def _chunk(model: str, delta: dict, finish=None, usage=None) -> bytes:
    obj = {"id": "chatcmpl-claude-agent", "object": "chat.completion.chunk",
           "created": int(time.time()), "model": model, RESP_KEY: PROVIDER,
           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        obj["usage"] = usage
    return b"data: " + json.dumps(obj).encode() + b"\n"


def _error_text(e: BaseException) -> str:
    return f"[claude-agent] the turn failed: {str(e)[:300]}"


class SSEReply:
    """A backend-response stand-in for the proxy's streaming tee: iterates
    chat.completion.chunk SSE lines, then [DONE]. close() (the watchdog's
    abort) stops it."""

    def __init__(self, messages, model: str, sdk=None):
        self.messages, self.model, self.sdk = messages, model, sdk
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def close(self):
        self.closed = True

    def __iter__(self):
        usage = None
        first = True
        try:
            for kind, val in iter_reply(self.messages, self.model, True, self.sdk):
                if self.closed:
                    break
                if kind == "usage":
                    usage = val
                    continue
                d = {"content": val} if kind == "text" else {"reasoning": val}
                if first:
                    d["role"] = "assistant"
                    first = False
                yield _chunk(self.model, d)
                yield b"\n"
        except Exception as e:
            print(f"[claude-agent] {e}", file=sys.stderr)
            yield _chunk(self.model, {"role": "assistant", "content": _error_text(e)})
            yield b"\n"
        yield _chunk(self.model, {}, finish="stop", usage=usage)
        yield b"\n"
        yield b"data: [DONE]\n"
        yield b"\n"


class JSONReply:
    """A backend-response stand-in for the buffered path: read() is the
    whole chat.completion body."""

    def __init__(self, messages, model: str, sdk=None):
        self.messages, self.model, self.sdk = messages, model, sdk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def read(self) -> bytes:
        text, reasoning, usage = [], [], None
        try:
            for kind, val in iter_reply(self.messages, self.model, False, self.sdk):
                if kind == "text":
                    text.append(val)
                elif kind == "reasoning":
                    reasoning.append(val)
                elif kind == "usage":
                    usage = val
        except Exception as e:
            print(f"[claude-agent] {e}", file=sys.stderr)
            text = [_error_text(e)]
        msg = {"role": "assistant", "content": "".join(text)}
        if reasoning:
            msg["reasoning"] = "".join(reasoning)
        obj = {"id": "chatcmpl-claude-agent", "object": "chat.completion",
               "created": int(time.time()), "model": self.model,
               RESP_KEY: PROVIDER,
               "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]}
        if usage is not None:
            obj["usage"] = usage
        return json.dumps(obj).encode()


def open_reply(body: dict):
    """The proxy's _open_backend for a claude-agent rung: the turn's
    messages (tools dropped: text-only) -> a streaming or buffered reply."""
    sdk = check_available()
    model = str(body.get("model") or "")
    msgs = body.get("messages") or []
    if body.get("stream"):
        return SSEReply(msgs, model, sdk)
    return JSONReply(msgs, model, sdk)
