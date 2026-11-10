"""MCP SERVERS IN THE FIELD (#963, the operator 2026-11-10): the I/O half of `membrane/packs/mcp.c1f`.

The pack decides everything (when a server connects, which entities exist, their state); this module only does what
a `.c1f` script can't -- talk to the servers:

- `McpServer:<name>.link = 'up' | 'down'` (the pack writes it from `enabled`): start a stdio server as the seat's
  child, or connect to an http one; then write back `listed_tools` / `listed_resources` / `listed_prompts`,
  `link_state` and `link_error`. A server's list-changed notification lists again. 'down' closes it.
- `McpTool:<server>:<tool>.call:run = {id, args}`: run the tool, write the receipt `call:run_result` = {id, status, rc,
  head, result} (#956's shape). Secret-named args are refused (a written call is stored); entity scripts and peers
  never call. A call longer than CALL_TIMEOUT_S is an error receipt.
- `sdk_servers()`: the enabled servers in the Claude Agent SDK's form, so the Claude rung mounts them as its own tools
  (stage 4), with `sdk_tool_names()` for its allowed list.
- `import_configs()`: on request, the MCP servers other clients configured (~/.claude.json, .mcp.json, opencode's
  config) become McpServer entities, disabled.

Secrets are never stored: `env_from` / `headers_from` name environment variables, read when a server starts. One
asyncio loop on its own thread holds every connection; the adapter worker only hands work to it, never waits.
A server that dies is `failed` with its error; writing `enabled` again retries (no automatic restart).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

CALL_TIMEOUT_S = 60.0          # the operator, 2026-11-10: a tool call's limit, then an error receipt
CONNECT_TIMEOUT_S = 60.0       # starting a server and listing what it offers (the same limit)
HEAD_MAX = 4000                # a receipt's head (the #956 receipt's own cap)
WRITER = "adapter:mcp"
SERVER, TOOL = "McpServer", "McpTool"
_STATE: dict = {"loop": None, "thread": None, "srv": None, "conns": {}}
_LOCK = threading.Lock()


# ---- the loop thread ----------------------------------------------------------------------------------------

def _loop() -> asyncio.AbstractEventLoop:
    with _LOCK:
        loop = _STATE["loop"]
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            t = threading.Thread(target=loop.run_forever, daemon=True, name="mcp")
            t.start()
            _STATE.update(loop=loop, thread=t)
        return loop


def start(srv) -> None:
    """Boot: the servers enabled before the restart connect again (the pack's `link` is written on a change of
    `enabled` only, and a restart changes nothing)."""
    _STATE["srv"] = srv
    est = getattr(srv, "entity_store", None)
    for d in _servers(est):
        asyncio.run_coroutine_threadsafe(_open(d.eid, _config(d)), _loop())


def stop() -> None:
    """Seat shutdown: every server closes (stdio servers stop with the seat)."""
    loop = _STATE.get("loop")
    if loop is None:
        return
    for eid in list(_STATE["conns"]):
        fut = asyncio.run_coroutine_threadsafe(_close(eid), loop)
        try:
            fut.result(timeout=10)
        except Exception:                                 # noqa: BLE001
            pass
    loop.call_soon_threadsafe(loop.stop)
    _STATE.update(loop=None, thread=None)


# ---- writing back --------------------------------------------------------------------------------------------

def _est():
    srv = _STATE.get("srv")
    return getattr(srv, "entity_store", None) if srv is not None else None


def _write(eid: str, props: dict) -> None:
    """The adapter's own writes onto an entity (writer adapter:mcp: they fire the pack's setters, never the adapter)."""
    from . import writes as W
    est = _est()
    d = (est.docs or {}).get(eid) if est is not None else None
    if d is None:
        return
    with W.as_writer(WRITER):
        d.properties = {**(d.properties or {}), **props}
        est.upsert(d, embedder=None)


def _secret_env(names: dict | None) -> dict:
    """{key: value} from {key: ENV_VAR}; a missing variable is an error naming it (never its value)."""
    out = {}
    for k, var in (names or {}).items():
        v = os.environ.get(str(var))
        if v is None:
            raise RuntimeError(f"environment variable {var} is not set (for {k})")
        out[str(k)] = v
    return out


def _dump(x) -> object:
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json", exclude_none=True, by_alias=True)
    return x


# ---- connections ---------------------------------------------------------------------------------------------

class _Conn:
    def __init__(self, eid: str):
        self.eid, self.session, self.stop, self.task, self.ready = eid, None, asyncio.Event(), None, asyncio.Event()
        self.closing = False           # set by the adapter thread the moment `down` is written: no call starts after


async def _relist(conn: "_Conn") -> None:
    s = conn.session
    if s is None:
        return
    caps = getattr(getattr(conn, "init", None), "capabilities", None)
    out: dict = {"link_state": "up", "link_error": ""}
    tools = await s.list_tools()
    out["listed_tools"] = [{"name": t.name, "description": t.description or "",
                            "input_schema": _dump(getattr(t, "input_schema", None) or getattr(t, "inputSchema", None))}
                           for t in tools.tools]
    if caps is None or getattr(caps, "resources", None) is not None:
        try:
            rs = await s.list_resources()
            out["listed_resources"] = [{"uri": str(r.uri), "name": r.name or "", "description": r.description or "",
                                        "mime": getattr(r, "mime_type", None) or getattr(r, "mimeType", None)}
                                       for r in rs.resources]
        except Exception:                                  # noqa: BLE001 -- a server without resources
            out["listed_resources"] = []
    if caps is None or getattr(caps, "prompts", None) is not None:
        try:
            ps = await s.list_prompts()
            out["listed_prompts"] = [{"name": p.name, "description": p.description or "",
                                      "arguments": [_dump(a) for a in (p.arguments or [])]} for p in ps.prompts]
        except Exception:                                  # noqa: BLE001
            out["listed_prompts"] = []
    _write(conn.eid, out)


async def _serve(conn: "_Conn", cfg: dict) -> None:
    """Open the transport and the session, list, then hold them open until stopped."""
    from contextlib import AsyncExitStack
    from mcp import ClientSession
    try:
        async with AsyncExitStack() as stack:
            transport = str(cfg.get("transport") or ("http" if cfg.get("url") else "stdio"))
            if transport == "stdio":
                from mcp import StdioServerParameters
                from mcp.client.stdio import stdio_client
                env = {**os.environ, **_secret_env(cfg.get("env_from"))}
                params = StdioServerParameters(command=str(cfg["command"]), args=[str(a) for a in (cfg.get("args") or [])],
                                               env=env, cwd=cfg.get("cwd") or None)
                streams = await stack.enter_async_context(stdio_client(params, errlog=sys.stderr))
            elif transport == "http":
                from mcp.client.streamable_http import streamable_http_client
                headers = _secret_env(cfg.get("headers_from"))
                client = None
                if headers:
                    import httpx2
                    client = await stack.enter_async_context(httpx2.AsyncClient(headers=headers))
                streams = await stack.enter_async_context(streamable_http_client(str(cfg["url"]), http_client=client))
            else:
                raise RuntimeError(f"transport is stdio or http, not {transport!r}")
            read, write = streams[0], streams[1]

            async def on_message(message):
                kind = type(getattr(message, "root", message)).__name__
                if "ListChanged" in kind and conn.session is not None:
                    try:
                        await _relist(conn)
                    except Exception as e:                  # noqa: BLE001
                        _write(conn.eid, {"link_error": f"listing again failed: {e}"[:400]})

            session = await stack.enter_async_context(ClientSession(read, write, message_handler=on_message))
            conn.init = await asyncio.wait_for(session.initialize(), CONNECT_TIMEOUT_S)
            conn.session = session
            await asyncio.wait_for(_relist(conn), CONNECT_TIMEOUT_S)
            conn.ready.set()
            stopped = asyncio.ensure_future(conn.stop.wait())
            await stopped
    except Exception as e:                                 # noqa: BLE001 -- the server's failure is its state
        msg = f"{type(e).__name__}: {e}"
        if isinstance(e, BaseExceptionGroup):
            msg = "; ".join(f"{type(x).__name__}: {x}" for x in e.exceptions)
        _write(conn.eid, {"link_state": "down", "link_error": msg[:400]})
    finally:
        conn.session = None
        conn.ready.set()
        if _STATE["conns"].get(conn.eid) is conn:
            _STATE["conns"].pop(conn.eid, None)
        if conn.stop.is_set():
            _write(conn.eid, {"link_state": "down"})


async def _open(eid: str, cfg: dict) -> None:
    await _close(eid)
    conn = _Conn(eid)
    _STATE["conns"][eid] = conn
    _write(eid, {"link_state": "starting", "link_error": ""})
    conn.task = asyncio.get_running_loop().create_task(_serve(conn, cfg))


async def _close(eid: str) -> None:
    conn = _STATE["conns"].get(eid)
    if conn is None:
        return
    conn.stop.set()
    if conn.task is not None:
        try:
            await asyncio.wait_for(conn.task, 15)
        except Exception:                                  # noqa: BLE001
            conn.task.cancel()


def _config(d) -> dict:
    p = d.properties or {}
    return {k: p.get(k) for k in ("transport", "command", "args", "env_from", "url", "headers_from", "cwd")}


# ---- the adapter's effects -----------------------------------------------------------------------------------

def link(srv, est, ent, prop, new, by) -> None:
    """`McpServer.link` written: 'up' (re)connects, 'down' closes. The adapter worker never waits for it."""
    _STATE["srv"] = srv
    want = str(new or "")
    if want not in ("up", "down"):
        return
    loop = _loop()
    old = _STATE["conns"].get(ent.eid)
    if old is not None:
        old.closing = True
    if want == "up":
        asyncio.run_coroutine_threadsafe(_open(ent.eid, _config(ent)), loop)
    else:
        asyncio.run_coroutine_threadsafe(_close(ent.eid), loop)
        _write(ent.eid, {"link_state": "down", "link_error": ""})
    _write(ent.eid, {"link": ""})                            # consumed


def call(srv, est, ent, prop, new, by) -> None:
    """`McpTool.call:run = {id, args}`: run the tool on its server; the receipt lands when it returns."""
    from . import adapters as AD
    _STATE["srv"] = srv
    req = new if isinstance(new, dict) else None
    rid = (req or {}).get("id") if req else ""
    _write(ent.eid, {"call:run": ""})                        # consumed
    if str(by).startswith(("script:", "peer")):
        AD._call_receipt(est, ent.eid, "call:run", rid, 2, "refused: entity scripts and peers don't call MCP tools")
        return
    if req is None or not str(rid or "").strip() or not isinstance(req.get("args", {}), dict):
        AD._call_receipt(est, ent.eid, "call:run", rid, 2, "a call request is {id, args}: nothing ran")
        return
    args = dict(req.get("args") or {})
    secret = AD._secret_args(args)
    if secret:
        AD._call_receipt(est, ent.eid, "call:run", rid, 2,
                         f"refused: a written call can't carry secret-named args ({', '.join(secret)}); "
                         "put the secret in the server's env_from instead")
        return
    p = ent.properties or {}
    conn = _STATE["conns"].get(str(p.get("server") or ""))
    if conn is None or conn.session is None or conn.closing:
        AD._call_receipt(est, ent.eid, "call:run", rid, 1, f"the server {p.get('server')} is not up: nothing ran")
        return
    asyncio.run_coroutine_threadsafe(_call(conn, ent.eid, str(p.get("name") or ""), args, rid), _loop())


async def _call(conn, tool_eid: str, name: str, args: dict, rid) -> None:
    from . import adapters as AD
    from . import writes as W
    est = _est()
    try:
        r = await asyncio.wait_for(conn.session.call_tool(name, args), CALL_TIMEOUT_S)
        texts = [getattr(c, "text", None) for c in (getattr(r, "content", None) or [])]
        head = "\n".join(t for t in texts if t)[:HEAD_MAX]
        res = getattr(r, "structured_content", None) or getattr(r, "structuredContent", None)
        rc = 1 if getattr(r, "is_error", None) or getattr(r, "isError", None) else 0
    except asyncio.TimeoutError:
        rc, head, res = 1, f"the tool took longer than {CALL_TIMEOUT_S:.0f} s: no result", None
    except Exception as e:                                 # noqa: BLE001
        rc, head, res = 1, f"{type(e).__name__}: {e}"[:HEAD_MAX], None
    if est is not None:
        with W.as_writer(WRITER):
            AD._call_receipt(est, tool_eid, "call:run", rid, rc, head, result=res)


# ---- the Claude rung (stage 4) ---------------------------------------------------------------------------------

def _servers(est):
    for d in list((est.docs or {}).values()) if est is not None else []:
        if d.cls == SERVER and (d.properties or {}).get("enabled") and (d.properties or {}).get("name"):
            yield d


def sdk_servers(est=None) -> dict:
    """The enabled servers in the Agent SDK's form ({name: {type, ...}}); one whose secret is missing is left out."""
    est = est if est is not None else _est()
    out = {}
    for d in _servers(est):
        cfg, name = _config(d), str(d.properties["name"])
        try:
            if str(cfg.get("transport") or ("http" if cfg.get("url") else "stdio")) == "http":
                out[name] = {"type": "http", "url": str(cfg["url"]), "headers": _secret_env(cfg.get("headers_from"))}
            else:
                out[name] = {"type": "stdio", "command": str(cfg["command"]),
                             "args": [str(a) for a in (cfg.get("args") or [])], "env": _secret_env(cfg.get("env_from"))}
        except Exception as e:                             # noqa: BLE001
            print(f"[mcp] {name} not mounted for Claude: {e}", file=sys.stderr)
    return out


def sdk_tool_names(est=None) -> list[str]:
    """The listed tools of the mounted servers, as the SDK names them (mcp__<server>__<tool>)."""
    est = est if est is not None else _est()
    names = set(sdk_servers(est))
    out = []
    for d in list((est.docs or {}).values()) if est is not None else []:
        p = d.properties or {}
        if d.cls == TOOL and p.get("listed") and p.get("name"):
            srv = (est.docs or {}).get(str(p.get("server") or ""))
            sname = str(((srv.properties or {}) if srv is not None else {}).get("name") or "")
            if sname in names:
                out.append(f"mcp__{sname}__{p['name']}")
    return sorted(out)


# ---- importing other clients' configs --------------------------------------------------------------------------

IMPORT_EID = "membrane::mcp"


def import_effect(srv, est, ent, prop, new, by) -> None:
    """`membrane::mcp.import = <anything>`: import other clients' configs now; the receipt `import_result` lists the
    McpServer entities made (disabled: enabling one is the person's write)."""
    try:
        made, rc, head = import_configs(est), 0, ""
    except Exception as e:                                 # noqa: BLE001
        made, rc, head = [], 1, f"{type(e).__name__}: {e}"[:400]
    _write(ent.eid, {"import": "", "import_result": {"made": made, "rc": rc, "head": head or f"{len(made)} imported"}})

def config_paths(cwd: str | None = None) -> list[Path]:
    home, cwd = Path.home(), Path(cwd or os.getcwd())
    return [home / ".claude.json", cwd / ".mcp.json", home / ".config" / "opencode" / "opencode.json",
            cwd / "opencode.json"]


def import_configs(est, paths=None) -> list[str]:
    """MCP servers configured for Claude Code (`mcpServers`) or opencode (`mcp`) become McpServer entities, disabled.
    Literal env values and headers are NOT copied (they may be secrets): their names go to env_from / headers_from,
    to be set in the seat's environment. A server already in the field is left as it is. -> the eids made."""
    from .entities import QuantumEntity
    from . import writes as W
    made = []
    for path in (paths or config_paths()):
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
        servers = dict(data.get("mcpServers") or {})
        for name, c in (data.get("mcp") or {}).items():           # opencode: {type: local|remote, command: [...]}
            if isinstance(c, dict):
                cmd = c.get("command") or []
                servers[name] = ({"type": "http", "url": c.get("url")} if c.get("type") == "remote"
                                 else {"command": cmd[0] if cmd else "", "args": cmd[1:], "env": c.get("environment")})
        for name, c in servers.items():
            if not isinstance(c, dict):
                continue
            eid = f"{SERVER}:{name}"
            if eid in (est.docs or {}):
                continue
            http = c.get("type") in ("http", "sse") or (c.get("url") and not c.get("command"))
            props = {"name": name, "enabled": False, "transport": "http" if http else "stdio",
                     "imported_from": str(path)}
            if http:
                props.update(url=c.get("url"), headers_from={h: h.upper().replace("-", "_") for h in (c.get("headers") or {})})
            else:
                props.update(command=c.get("command"), args=list(c.get("args") or []),
                             env_from={k: k for k in (c.get("env") or c.get("environment") or {})})
            with W.as_writer(WRITER):
                est.upsert(QuantumEntity(eid=eid, cls=SERVER, properties=props), embedder=None)
            made.append(eid)
    return made
