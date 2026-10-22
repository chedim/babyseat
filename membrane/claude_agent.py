"""THE CLAUDE-AGENT RUNG (issue #384): Claude through the Claude Agent SDK.

The SDK (`claude-agent-sdk`, an OPTIONAL extra: `pip install
'babyseat[claude-agent]'`) drives the `claude` CLI (the SDK bundles one).
membrane never calls an Anthropic API itself: a turn routed to a `claude-agent:<model>` rung is handed to the
SDK's query() -- the turn's system messages as the system prompt, the rest
as the prompt -- and the reply comes back as an ordinary OpenAI chat
response, streamed (chat.completion.chunk SSE lines) or buffered.

MEMBRANE'S TOOLS IN CLAUDE'S LOOP (issue #389): Claude Code's own tools are
switched off (no built-in tools, no user MCP config, no settings files).
The SDK runs its own agent loop and cannot hand a tool call back to a caller
unexecuted, so Claude gets membrane's MCP tool surface (mcp_server.TOOLS,
run by mcp_server.TOOL_IMPLS, write tools included) as an in-process SDK
MCP server, and allowed_tools is exactly that mcp__membrane__* set. The
tools relay to this seat's proxy like the stdio MCP server's do, so every
proxy-side guard applies. Check-ins pace the loop (below). The turn's own
tool definitions (the caller's surface) are not passed on; the reply never
carries tool_calls for the caller to run. Each tool Claude ran rides the
reply as a TOOL_KEY chunk (streamed) or TOOLS_KEY list (buffered), so the
transcript shows it and the proxy captures it.

CHECK-INS INSTEAD OF A TURN CAP (issue #425, the operator's 2026-10-21,
values approved; it replaced #389's fixed 4-turn batches): one answer is ONE
continuous SDK run. A PreToolUse hook (_Answer.hook) counts Claude's tool
calls since its last words to the person: at most CHECKIN_TOOL_CALLS in a
row; any assistant text resets the count; the next call is denied with
CHECKIN_MESSAGE, which Claude gets as that tool's result (the person never
sees it as an error), so Claude says where it is and goes on. A hard
ceiling, MAX_TOOL_CALLS_PER_ANSWER, stops the run: the call past it is
denied and the hook stops the loop. MAX_TURNS is only the SDK's backstop,
sized never to bind before the ceiling.

THE CEILING AND CONTINUE (#389's machinery): an answer that stops at the
ceiling (or the backstop) is neither an error nor a silent cut. The reply
carries CAPPED_KEY {session, calls}; the transcript row keeps it as CAP_MARK
and the chat offers Continue. Continue is a person's action only (never
automatic, never on a background lane): it resumes the SAME SDK session
(the SDK's `resume`, RESUME_KEY on the turn) with the same tools and options
and a fresh ceiling, and each capped answer continues at most once.
STATS counts check-in denials, ceiling hits and continues (RAM,
/membrane/diag/claude-agent).

LEARNED LIKE ANY MODEL (issue #389, the operator's 2026-10-21): Claude's
reply, its reasoning and its tool calls and results are learned like any
other model's. The transcript row keeps the Claude-made mark (MARK_KEY,
from the response's RESP_KEY stamp) for provenance only.

Opt-in: the router never escalates to a claude-agent rung on its own (see
router.OPT_IN_BACKENDS); the operator pins it or adds it to the ladder
(--ladder-include). Background lanes never use it.

THE API KEY (issue #413, a release gate): the rung authenticates with an
Anthropic API key by default -- the one membrane already reads
(ANTHROPIC_API_KEY, else `membrane auth anthropic`'s store) -- handed to the
CLI subprocess as ANTHROPIC_API_KEY in ClaudeAgentOptions.env (the SDK merges
options.env over the inherited environment). In that mode the credentials
the CLI ranks ABOVE the key (ANTHROPIC_AUTH_TOKEN, the Bedrock / Vertex /
Foundry switches) and CLAUDE_CODE_OAUTH_TOKEN are blanked, so the CLI cannot
fall back to a login: with the key in its env the CLI reports
authMethod "api_key" even where a claude.ai login is stored. No key: the rung
refuses (Unavailable, KEY_HINT) and no model is called. The key is never
printed, logged or written anywhere new; an error text is scrubbed of it.

The CLI's own login is a development / personal opt-in only (`membrane auth
claude-agent --login`, mark MODE_LOGIN): never chosen silently. In that mode
ANTHROPIC_API_KEY is blanked so the login answers; membrane still never reads
the login's token. The old `cli-login` mark (stored before #413 with no
choice offered) is NOT the opt-in: it means the default, the API key.
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
MODE_KEY = "api-key"                # `membrane auth claude-agent`: the default
MODE_LOGIN = "login"                # `... --login`: the CLI's login (opt-in)
ENABLED_MARK = MODE_KEY             # the mark the plain command stores (not a secret)
KEY_PROVIDER = "anthropic"          # auth.load_api_key's row for the key
KEY_ENV = "ANTHROPIC_API_KEY"       # how the CLI subprocess gets the key
# credentials the CLI would use instead of ANTHROPIC_API_KEY: blanked in the
# subprocess env so the mode is what it says (#413)
OVERRIDING_ENV = ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                  "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
LOGIN_ENV = ("CLAUDE_CODE_OAUTH_TOKEN",)   # a login token: never in key mode
KEY_HINT = ("run `membrane auth anthropic` (or set ANTHROPIC_API_KEY), then "
            "`membrane auth claude-agent`")
INSTALL_HINT = ("pip install 'babyseat[claude-agent]' (or: pip install "
                "claude-agent-sdk)")
CLI_HINT = ("reinstall the SDK (it bundles the `claude` CLI) or install "
            "Claude Code, then `membrane auth claude-agent`")
LOGIN_HINT = ("run `claude` once to log in, then "
              "`membrane auth claude-agent --login`")
STATUS_TIMEOUT_S = 20
CHECKIN_TOOL_CALLS = 7              # tool calls in a row without words to the
                                    # person (#425; approved 2026-10-21)
MAX_TOOL_CALLS_PER_ANSWER = 50      # the hard ceiling per answer; Continue
                                    # gives a fresh one (#425; approved 2026-10-21)
MAX_TURNS = 2 * MAX_TOOL_CALLS_PER_ANSWER   # the SDK's backstop only: every
                                    # allowed call may take a turn and be
                                    # denied once first, so it never binds
                                    # before the ceiling (#425)
CHECKIN_MESSAGE = ("Tell the person where you are first (one or two lines), "
                   "then continue.")
CEILING_MESSAGE = (f"This answer reached its ceiling of "
                   f"{MAX_TOOL_CALLS_PER_ANSWER} tool calls. Stop here; the "
                   f"person can press Continue.")
SEEN_WAIT_S = 2.0                   # the hook waits this long for the reader to
                                    # see the call's message (its words first)
MCP_NAME = "membrane"               # the SDK MCP server's name: tools are
TOOL_PREFIX = f"mcp__{MCP_NAME}__"  # mcp__membrane__<tool>
TOOL_KEY = "membrane_tool"          # a streamed chunk: one tool Claude ran
TOOLS_KEY = "membrane_tools"        # a buffered response: every tool Claude ran
CAPPED_KEY = "membrane_capped"      # reply: the answer stopped at the ceiling (#425)
CAP_MARK = "claudeCapped"           # transcript row: {session, turns, continued}
RESUME_KEY = "claude_resume"        # turn/body: resume this SDK session
CONTINUE_FIELD = "claudeContinue"   # a person's POST: the capped message's id
CONTINUE_TEXT = "Continue"          # the person's row a Continue adds
CONTINUE_PROMPT = ("Continue the task from where you stopped at the turn "
                   "limit.")
MAX_TURNS_SUBTYPE = "error_max_turns"   # the SDK's result subtype at the cap


class ContinueRefused(ValueError):
    """A Continue that may not run: not capped, already continued, unknown,
    or a background lane's."""


_stats_lock = threading.Lock()
STATS = {"checkin_denials": 0, "ceiling_hits": 0, "continues": 0}   # diagnostics (RAM)


def _count(key: str) -> None:
    with _stats_lock:
        STATS[key] = STATS.get(key, 0) + 1


def stats() -> dict:
    with _stats_lock:
        return dict(STATS)


def reset_stats() -> None:
    with _stats_lock:
        for k in STATS:
            STATS[k] = 0
        for k in USAGE:
            USAGE[k] = 0


# ---- the prompt cache's ledger (#426) -------------------------------------------
# Every Claude call's usage, split the way Anthropic bills it: uncached
# input, cache reads, cache writes, output. Sums since boot (RAM).

USAGE = {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0}


def _record_usage(u: dict) -> None:
    """One call's OpenAI-shaped usage (from _usage) onto the ledger."""
    det = u.get("prompt_tokens_details") or {}
    read = int(det.get("cached_tokens") or 0)
    write = int(det.get("cache_write_tokens") or 0)
    with _stats_lock:
        USAGE["calls"] += 1
        USAGE["input"] += int(u.get("prompt_tokens") or 0) - read - write
        USAGE["cache_read"] += read
        USAGE["cache_write"] += write
        USAGE["output"] += int(u.get("completion_tokens") or 0)


def usage_stats() -> dict:
    """The ledger plus per-call averages (diag/claude-agent)."""
    with _stats_lock:
        out = dict(USAGE)
    n = out["calls"]
    out["per_call"] = {k: (round(out[k] / n) if n else 0)
                       for k in ("input", "cache_read", "cache_write", "output")}
    return out


class _Answer:
    """One answer's check-ins (#425): the PreToolUse hook's state. The
    reader (_consume) notes Claude's words and each tool_use it sees; the
    hook, run by the SDK before each call, waits until the reader has seen
    that call's message -- so words in the same message count first --
    then allows, denies (a check-in) or stops (the ceiling)."""

    def __init__(self):
        self.calls = 0                  # allowed calls in this answer
        self.streak = 0                 # allowed calls since Claude's last words
        self.denied: set = set()        # tool_use ids the hook refused
        self.ceiling = False
        self._seen: dict = {}           # tool_use id -> asyncio.Event

    def _event(self, tid: str):
        ev = self._seen.get(tid)
        if ev is None:
            ev = self._seen[tid] = asyncio.Event()
        return ev

    def note_text(self, text) -> None:
        if str(text or "").strip():
            self.streak = 0

    def note_use(self, tid: str) -> None:
        self._event(str(tid)).set()

    async def hook(self, hook_input, tool_use_id=None, context=None):
        tid = str((hook_input or {}).get("tool_use_id") or tool_use_id or "")
        try:
            await asyncio.wait_for(self._event(tid).wait(), SEEN_WAIT_S)
        except asyncio.TimeoutError:
            pass                        # counted on what the reader has seen
        self._seen.pop(tid, None)
        if self.calls >= MAX_TOOL_CALLS_PER_ANSWER:
            self.ceiling = True
            self.denied.add(tid)
            return {"continue_": False, "stopReason": CEILING_MESSAGE,
                    "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": CEILING_MESSAGE}}
        if self.streak >= CHECKIN_TOOL_CALLS:
            self.denied.add(tid)
            _count("checkin_denials")
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": CHECKIN_MESSAGE}}
        self.calls += 1
        self.streak += 1
        return {}


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
    """The provider's rungs on the router: cloud kind, completion caps (the
    rung never takes the caller's tools: its tools are membrane's, run in
    Claude's own loop), opt-in (router.OPT_IN_BACKENDS)."""
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


def auth_mode(home=None, environ=None) -> str:
    """MODE_LOGIN only on the explicit opt-in (the mark `membrane auth
    claude-agent --login` stores, or MEMBRANE_CLAUDE_AGENT=login); anything
    else -- the old `cli-login` mark included -- is MODE_KEY (#413)."""
    from . import auth as _auth
    mark = _auth.load_api_key(PROVIDER, home, environ).strip().lower()
    return MODE_LOGIN if mark == MODE_LOGIN else MODE_KEY


def credential_env(home=None, environ=None) -> dict:
    """The CLI subprocess's credential env (ClaudeAgentOptions.env). Key
    mode: ANTHROPIC_API_KEY = the key, every credential that would win over
    it or stand in for it blanked; no key -> Unavailable(KEY_HINT). Login
    mode: ANTHROPIC_API_KEY blanked so the CLI's own login answers."""
    from . import auth as _auth
    blank = {k: "" for k in OVERRIDING_ENV}
    if auth_mode(home, environ) == MODE_LOGIN:
        return {**blank, KEY_ENV: ""}
    key = _auth.load_api_key(KEY_PROVIDER, home, environ)
    if not key:
        raise Unavailable(f"claude-agent: no Anthropic API key; {KEY_HINT}")
    return {**blank, **{k: "" for k in LOGIN_ENV}, KEY_ENV: key}


def scrub(text) -> str:
    """`text` with the Anthropic key masked: for every error or log line
    that may echo what the SDK or the CLI said."""
    text = str(text)
    try:
        from . import auth as _auth
        key = _auth.load_api_key(KEY_PROVIDER)
    except Exception:
        key = ""
    if key and key in text:
        text = text.replace(key, _auth.mask(key))
    return text


def check_available():
    """Raise Unavailable (with what to do) unless the SDK, the CLI and the
    credentials (the API key, or the login opt-in) are all here. Returns
    the SDK module."""
    sdk = load_sdk()
    if not find_cli():
        raise Unavailable(f"claude-agent: the `claude` CLI was not found; "
                          f"{LOGIN_HINT if auth_mode() == MODE_LOGIN else CLI_HINT}")
    credential_env()
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


CONTEXT_OPEN = "<membrane-context>"    # membrane's per-turn blocks (#426)
CONTEXT_CLOSE = "</membrane-context>"


def _volatile(m, seen_convo: bool) -> bool:
    """A system message that changes per call: one membrane placed for this
    turn (rag.Injected), or any system message after the conversation began."""
    from .rag import Injected
    return seen_convo or isinstance(m, Injected)


def _context(blocks) -> str:
    return f"{CONTEXT_OPEN}\n" + "\n\n".join(blocks) + f"\n{CONTEXT_CLOSE}"


def split_messages(messages) -> tuple[str, str]:
    """OpenAI messages -> (system prompt, prompt). The STABLE system
    messages (the doctrine, the client's instructions) are the system
    prompt -- byte-identical across calls, so Anthropic's prompt cache
    (tools, then system) holds (#426). membrane's per-turn blocks (recall,
    where-we-are, banners: _volatile) go to the END of the prompt,
    delimited, right before the latest message (a block placed after it,
    the speech footer, stays after it). A lone user message with no
    per-turn block is the prompt as is; a longer conversation is rendered
    as a transcript ending on the last message."""
    from .rag import _RAG_SYSTEM
    system, convo, ctx = [], [], []      # ctx: (blocks before convo item #i, text)
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        text = _text_of(m.get("content")).strip()
        if role == "system":
            if text and _volatile(m, bool(convo)):
                head = _RAG_SYSTEM.strip()
                if head and text.startswith(head):     # the undelivered doctrine
                    if head not in system:
                        system.append(head)
                    text = text[len(head):].strip()
                if text:
                    ctx.append((len(convo), text))
            elif text:
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
    last = max(len(convo) - 1, 0)
    before = [t for i, t in ctx if i <= last]
    after = [t for i, t in ctx if i > last]
    lone = len(convo) == 1 and convo[0][0] == "user"
    items = [convo[0][2]] if lone else [f"{label}\n{text}"
                                        for _r, label, text in convo]
    if before:
        items.insert(last, _context(before))
    if after:
        items.append(_context(after))
    prompt = "\n\n".join(items)
    if convo and not lone:
        prompt = ("The conversation so far, oldest first; answer its last "
                  "message.\n\n" + prompt)
    return sys_prompt, prompt


# ---- membrane's tools, served to the SDK in-process (#389) ----------------------

def tool_names() -> list[str]:
    """allowed_tools: exactly membrane's MCP tools, as the SDK names them."""
    from . import mcp_server as _mcp
    hide = _coproc_hidden()
    return [TOOL_PREFIX + t["name"] for t in _mcp.TOOLS if t["name"] not in hide]


def _coproc_hidden() -> frozenset:
    """MEMBRANE_COPROC=1 (#454): no tool named like a coproc call
    (decide/remember/intend/says) is exposed -- a python-shaped `intend(...)`
    line must stay text the proxy parses, never a tool Claude calls."""
    from . import coproc as _cp
    return _cp.RESERVED_TOOL_NAMES if _cp.enabled() else frozenset()


def _tool_handler(name: str, port):
    """The SDK tool's handler: the stdio MCP server's own function for
    `name` (mcp_server.TOOL_IMPLS), run off the event loop, relaying to this
    seat's proxy on `port`. A failure goes back to Claude as an error
    result, the way the stdio server returns it."""
    from . import mcp_server as _mcp
    impl = _mcp.TOOL_IMPLS[name]

    async def handler(args):
        try:
            if port is None:
                raise _mcp.ProxyError(503, "the seat's proxy port is unknown")
            text = await asyncio.to_thread(impl, dict(args or {}), int(port))
            return {"content": [{"type": "text", "text": str(text)}]}
        except _mcp.ProxyError as e:
            msg = f"membrane tool error: {e.message}"
        except Exception as e:
            msg = f"membrane tool error: {e}"
        return {"content": [{"type": "text", "text": msg}], "is_error": True}
    return handler


def build_mcp_server(sdk, port):
    """membrane's MCP surface as an in-process SDK MCP server: one SDK tool
    per mcp_server.TOOLS entry (its name, description and JSON schema),
    each run by the matching mcp_server.TOOL_IMPLS function."""
    from . import mcp_server as _mcp
    tools = [sdk.tool(t["name"], t["description"], t["inputSchema"])(
                 _tool_handler(t["name"], port))
             for t in _mcp.TOOLS if t["name"] in _mcp.TOOL_IMPLS
             and t["name"] not in _coproc_hidden()]
    return sdk.create_sdk_mcp_server(name=MCP_NAME, version=_mcp.SERVER_VERSION,
                                     tools=tools)


def build_options(sdk, system: str, model: str, stream: bool, port=None,
                  resume=None, env=None, answer=None):
    answer = answer if answer is not None else _Answer()
    extra = {"resume": str(resume)} if resume else {}   # Continue: the same session
    return sdk.ClaudeAgentOptions(
        **extra,
        env=credential_env() if env is None else dict(env),   # the key (#413)
        system_prompt=system or None,
        tools=[],                  # Claude Code's built-in tools: off
        allowed_tools=tool_names(),  # only membrane's tools (#389)
        mcp_servers={MCP_NAME: build_mcp_server(sdk, port)},
        strict_mcp_config=True,    # no user MCP config, no claude.ai connectors (#455)
        setting_sources=[],        # no settings files, no CLAUDE.md
        max_turns=MAX_TURNS,       # a backstop: the check-in hook paces (#425)
        hooks={"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[answer.hook])]},
        model=sdk_model(model) or None,
        include_partial_messages=bool(stream),
    )


def _usage(u) -> dict:
    u = u if isinstance(u, dict) else {}
    pin = int(u.get("input_tokens") or 0) \
        + int(u.get("cache_read_input_tokens") or 0) \
        + int(u.get("cache_creation_input_tokens") or 0)
    pout = int(u.get("output_tokens") or 0)
    # the cache split rides the OpenAI shape (#426): cached_tokens is
    # OpenAI's read count; cache_write_tokens the common extension
    return {"prompt_tokens": pin, "completion_tokens": pout,
            "total_tokens": pin + pout,
            "prompt_tokens_details": {
                "cached_tokens": int(u.get("cache_read_input_tokens") or 0),
                "cache_write_tokens": int(u.get("cache_creation_input_tokens") or 0)}}


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text") or "") for c in content
                         if isinstance(c, dict) and c.get("text"))
    return "" if content is None else str(content)


def _bare(name: str) -> str:
    n = str(name or "")
    return n[len(TOOL_PREFIX):] if n.startswith(TOOL_PREFIX) else n


async def _consume(sdk, messages, model, stream, put, port=None, resume=None,
                   env=None):
    system, prompt = split_messages(messages)
    if resume:
        prompt = CONTINUE_PROMPT         # the session already holds the task
    answer = _Answer()                   # a fresh ceiling, Continue too (#425)
    opts = build_options(sdk, system, model, stream, port, resume, env, answer)
    partial = False
    capped = False
    pending: dict = {}                   # tool_use id -> (name, input)
    session = str(resume or "")
    async for m in sdk.query(prompt=prompt, options=opts):
        kind = type(m).__name__
        if getattr(m, "session_id", None):
            session = str(m.session_id)
        if kind == "StreamEvent":
            ev = getattr(m, "event", None) or {}
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    partial = True
                    answer.note_text(d["text"])          # words: a check-in
                    put(("text", d["text"]))
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    put(("reasoning", d["thinking"]))
        elif kind == "AssistantMessage":
            err = getattr(m, "error", None)
            if err:
                raise RuntimeError(f"claude-agent: {err}")
            for b in getattr(m, "content", None) or []:
                bk = type(b).__name__
                if bk == "ToolUseBlock":
                    pending[str(getattr(b, "id", ""))] = (
                        _bare(getattr(b, "name", "")), getattr(b, "input", None) or {})
                    answer.note_use(str(getattr(b, "id", "")))
                elif partial:
                    continue                  # the words already came as deltas
                elif bk == "TextBlock" and getattr(b, "text", ""):
                    answer.note_text(b.text)
                    put(("text", b.text))
                elif bk == "ThinkingBlock" and getattr(b, "thinking", ""):
                    put(("reasoning", b.thinking))
        elif kind == "UserMessage":
            # the tool results the SDK's loop fed back to Claude
            for b in getattr(m, "content", None) or []:
                if type(b).__name__ != "ToolResultBlock":
                    continue
                tid = str(getattr(b, "tool_use_id", ""))
                name, args = pending.pop(tid, ("", {}))
                if tid in answer.denied:
                    continue          # the hook's denial: Claude's, never run (#425)
                put(("tool", {"id": tid, "name": name, "input": args,
                              "output": _result_text(getattr(b, "content", None)),
                              "is_error": bool(getattr(b, "is_error", False))}))
        elif kind == "ResultMessage":
            if answer.ceiling or getattr(m, "subtype", "") == MAX_TURNS_SUBTYPE:
                # the ceiling (or the backstop): marked, never an error
                put(("usage", _usage(getattr(m, "usage", None))))
                capped = True
                continue
            if getattr(m, "is_error", False):
                why = getattr(m, "result", None) or getattr(m, "errors", None) \
                    or getattr(m, "subtype", "error")
                raise RuntimeError(f"claude-agent: {why}")
            put(("usage", _usage(getattr(m, "usage", None))))
    if capped or answer.ceiling:
        _count("ceiling_hits")
        put(("capped", {"session": session, "calls": MAX_TOOL_CALLS_PER_ANSWER}))


_END = object()


def iter_reply(messages, model: str, stream: bool = True, sdk=None, port=None,
               resume=None):
    """The SDK's reply as ("text"|"reasoning"|"tool"|"usage", value) items,
    in order. `port` is this seat's proxy, where membrane's tools relay.
    The async SDK runs on its own thread and loop (the proxy's handler
    threads are synchronous); an SDK error re-raises here."""
    sdk = sdk or check_available()
    env = credential_env()                  # no key -> Unavailable, no call
    q: queue.Queue = queue.Queue()

    def run():
        try:
            asyncio.run(_consume(sdk, messages, model, stream, q.put, port, resume,
                                 env))
        except BaseException as e:          # carried to the reader, key-free
            clean = scrub(e)
            q.put(("error", e if clean == str(e) else RuntimeError(clean)))
        finally:
            q.put(_END)

    threading.Thread(target=run, daemon=True, name="claude-agent").start()
    while True:
        item = q.get()
        if item is _END:
            return
        if item[0] == "error":
            raise item[1]
        if item[0] == "usage":
            _record_usage(item[1])
        yield item


# ---- the reply as an OpenAI response ---------------------------------------------

def _chunk(model: str, delta: dict, finish=None, usage=None, tool=None,
           capped=None) -> bytes:
    side = tool is not None or capped is not None
    obj = {"id": "chatcmpl-claude-agent", "object": "chat.completion.chunk",
           "created": int(time.time()), "model": model, RESP_KEY: PROVIDER,
           "choices": ([] if side else
                       [{"index": 0, "delta": delta, "finish_reason": finish}])}
    if tool is not None:
        obj[TOOL_KEY] = tool
    if capped is not None:
        obj[CAPPED_KEY] = capped
    if usage is not None:
        obj["usage"] = usage
    return b"data: " + json.dumps(obj).encode() + b"\n"


def _error_text(e: BaseException) -> str:
    return f"[claude-agent] the turn failed: {scrub(e)[:300]}"


class SSEReply:
    """A backend-response stand-in for the proxy's streaming tee: iterates
    chat.completion.chunk SSE lines, then [DONE]. close() (the watchdog's
    abort) stops it. After the iteration: .tools (every tool Claude ran)
    and .failed (the turn failed: the reply is the error line)."""

    def __init__(self, messages, model: str, sdk=None, port=None, resume=None):
        self.messages, self.model, self.sdk, self.port = messages, model, sdk, port
        self.resume = resume
        self.closed = False
        self.tools: list = []
        self.failed = False
        self.capped = None

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
            for kind, val in iter_reply(self.messages, self.model, True, self.sdk,
                                        self.port, self.resume):
                if self.closed:
                    break
                if kind == "usage":
                    usage = val
                    continue
                if kind == "capped":
                    self.capped = val
                    yield _chunk(self.model, {}, capped=val)
                    yield b"\n"
                    continue
                if kind == "tool":
                    self.tools.append(val)
                    yield _chunk(self.model, {}, tool=val)
                    yield b"\n"
                    continue
                d = {"content": val} if kind == "text" else {"reasoning": val}
                if first:
                    d["role"] = "assistant"
                    first = False
                yield _chunk(self.model, d)
                yield b"\n"
        except Exception as e:
            print(f"[claude-agent] {scrub(e)}", file=sys.stderr)
            self.failed = True
            yield _chunk(self.model, {"role": "assistant", "content": _error_text(e)})
            yield b"\n"
        yield _chunk(self.model, {}, finish="stop", usage=usage)
        yield b"\n"
        yield b"data: [DONE]\n"
        yield b"\n"


class JSONReply:
    """A backend-response stand-in for the buffered path: read() is the
    whole chat.completion body, with TOOLS_KEY when Claude ran tools.
    After read(): .tools and .failed, as SSEReply's."""

    def __init__(self, messages, model: str, sdk=None, port=None, resume=None):
        self.messages, self.model, self.sdk, self.port = messages, model, sdk, port
        self.resume = resume
        self.tools: list = []
        self.failed = False
        self.capped = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def read(self) -> bytes:
        text, reasoning, usage = [], [], None
        try:
            for kind, val in iter_reply(self.messages, self.model, False, self.sdk,
                                        self.port, self.resume):
                if kind == "capped":
                    self.capped = val
                elif kind == "text":
                    text.append(val)
                elif kind == "reasoning":
                    reasoning.append(val)
                elif kind == "tool":
                    self.tools.append(val)
                elif kind == "usage":
                    usage = val
        except Exception as e:
            print(f"[claude-agent] {scrub(e)}", file=sys.stderr)
            self.failed = True
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
        if self.tools:
            obj[TOOLS_KEY] = list(self.tools)
        if self.capped is not None:
            obj[CAPPED_KEY] = self.capped
        return json.dumps(obj).encode()


def open_reply(body: dict, port=None):
    """The proxy's _open_backend for a claude-agent rung: the turn's
    messages (the caller's tool definitions dropped: Claude's tools are
    membrane's, relaying to `port`) -> a streaming or buffered reply.
    RESUME_KEY (a person's Continue, set by the turn runner) resumes that
    SDK session with a fresh ceiling."""
    sdk = check_available()
    model = str(body.get("model") or "")
    msgs = body.get("messages") or []
    resume = str(body.get(RESUME_KEY) or "") or None
    if resume:
        _count("continues")
    if body.get("stream"):
        return SSEReply(msgs, model, sdk, port, resume)
    return JSONReply(msgs, model, sdk, port, resume)


def captured_calls(tools) -> dict:
    """Claude's tool calls in the shape the capturer's on_calls takes
    (index -> {id, type, function: {name, arguments}})."""
    return {i: {"id": t.get("id") or f"claude-agent-{i}", "type": "function",
                "function": {"name": t.get("name") or "",
                             "arguments": json.dumps(t.get("input") or {})}}
            for i, t in enumerate(tools or [])}
