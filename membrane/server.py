"""THE NATIVE SERVER (stage 3): membrane replaces opencode completely.

`membrane serve` used to launch `opencode serve` as a child -- its own REST
surface facing the room, with membrane proxying its model traffic and
capturing it. The full-replacement lane drops the middleman: membrane
HOSTS that surface itself. This module is opencode's replacement --
opencode-compatible where clients care:

- `SessionStore` / `MessageStore`: opencode's Session / Message / Part
  shapes, persisted beside the field (a device's history survives a seat
  restart, same covenant as serve-auth.json).
- The TURN LOOP (`TurnRunner`): the client's text goes to THIS seat's own
  /v1 on loopback -- so the gate, the injection, the routing ladder, the
  banner and the capture machinery exercise exactly the same code path a
  desktop TUI's turn does; loopback responses come back and, if they carry
  tool_calls, the tools execute IN-PROCESS against the MCP tool impls (the
  same membrane_* surface, the same proxy-side guards), their results ride
  back as role:'tool' messages, and the loop repeats until the model
  answers. Each part's state transition (pending -> completed) is
  published on the SSE hub, so the model's membrane tool use shows in the
  client like any regular opencode tool.
- `SSEHub` + the HTTP surface (`make_handler`): the /event stream and the
  session/message/abort/config subset a chat client speaks, basic-authed
  from the same serve-auth.json pair.

What is deliberately NOT here: opencode's file/VCS/LSP/share surface (a
client needs to work on the room through membrane_run and the field's
readers, which the doctrine already teaches).
"""
import copy
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue, Empty
from pathlib import Path

from . import mcp_server as _mcp

VERSION = "1.0"
PROJECT_ID = "membrane"
CLOUD_TIMEOUT_S = 1800.0              # a model turn is allowed minutes
MAX_ROUNDS = 24                       # tool-loop budget per turn
HISTORY_MAX_MESSAGES = 12             # prior conversation rows replayed
                                      # into a serve turn's history (the
                                      # narrative: 'it' needs an
                                      # antecedent in the transcript)
HISTORY_TEXT_CAP = 4000               # per-prior-message text cap


def _gen_id(prefix: str) -> str:
    return prefix + "_" + secrets.token_hex(12)


_SLUG_A = ("misty", "tidy", "calm", "quiet", "swift", "soft", "amber",
           "early", "humble", "silver", "violet", "gentle")
_SLUG_B = ("lagoon", "wolf", "meadow", "harbor", "cedar", "ember", "dune",
           "fjord", "grove", "brook", "reef", "tide")


def _slug() -> str:
    return (secrets.choice(_SLUG_A) + "-" + secrets.choice(_SLUG_B))


def _auth_header(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(
        f"{user}:{password}".encode()).decode()


# ---- stores -----------------------------------------------------------------

class SessionStore:
    """opencode's Session dicts, persisted beside the field. A missing
    file, a corrupt one or an unwritable home degrade to RAM-only (never a
    serve failure)."""

    def __init__(self, path: str | None = None):
        self.path = path
        self.sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        if path is not None:
            try:
                raw = Path(path).read_text()
                d = json.loads(raw)
                if isinstance(d, dict):
                    self.sessions = d
            except Exception:
                pass

    def create(self, title: str | None = None, parent_id: str | None = None
               ) -> dict:
        now = int(time.time() * 1000)
        d = os.getcwd()
        s = {"id": _gen_id("ses"), "projectID": PROJECT_ID, "directory": d,
             "path": os.path.relpath(d, "/"),
             "slug": _slug(), "title":
             title or f"New session {time.strftime('%H:%M')}",
             "version": VERSION, "agent": "build",
             "model": {"id": "", "providerID": "membrane",
                       "variant": "default"},
             "cost": 0, "tokens": {"total": 0, "input": 0, "output": 0,
                                   "reasoning": 0,
                                   "cache": {"read": 0, "write": 0}},
             "summary": {"additions": 0, "deletions": 0, "files": 0},
             "time": {"created": now, "updated": now}}
        if parent_id:
            s["parentID"] = parent_id
        with self._lock:
            self.sessions[s["id"]] = s
            self._flush()
        return s

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self.sessions.values(),
                          key=lambda s: s["time"]["created"])

    def import_session(self, d: dict) -> dict:
        """ABSORPTION (the import lane): a caller-supplied session doc
        (foreign id/model/time, provenance in 'absorbed'). An existing
        id WITHOUT the absorbed key is a native session's id -- a hard
        collision, refused (ValueError); an absorbed id returns the
        existing doc (the top-up branch: message rows extend by id and
        time.updated only ever rises -- the TUI's -c lane sorts on it).
        """
        sid = str(d.get("id") or "")
        with self._lock:
            cur = self.sessions.get(sid)
            if cur is not None:
                if isinstance(cur.get("absorbed"), dict):
                    nu = int(d.get("time", {}).get("updated") or 0)
                    if nu:
                        cur["time"]["updated"] = max(
                            int(cur["time"].get("updated") or 0), nu)
                    return cur
                raise ValueError(
                    f"session {sid} already exists and is not an "
                    "absorbed session")
            self.sessions[sid] = d
            self._flush()
        return d

    def get(self, sid: str) -> dict | None:
        with self._lock:
            return self.sessions.get(sid)

    def patch(self, sid: str, title: str | None = None) -> dict | None:
        with self._lock:
            s = self.sessions.get(sid)
            if s is None:
                return None
            if title is not None:
                s["title"] = title
            s["time"]["updated"] = int(time.time() * 1000)
            self._flush()
            return s

    def touch(self, sid: str) -> None:
        with self._lock:
            s = self.sessions.get(sid)
            if s is not None:
                # epoch-ms, like every other SDK time: the attached TUI's
                # -c/continue lane sorts sessions by time.updated desc --
                # a seconds value sorted as ancient broke the lane
                s["time"]["updated"] = int(time.time() * 1000)
                self._flush()

    def delete(self, sid: str) -> bool:
        with self._lock:
            if sid not in self.sessions:
                return False
            del self.sessions[sid]
            self._flush()
            return True

    def _flush(self) -> None:
        """The caller holds the lock."""
        if self.path is None:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.sessions, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def flush(self) -> None:
        with self._lock:
            self._flush()


class MessageStore:
    """A session's messages: rows of {info, parts}. Write-through (a
    model turn is minutes apart -- the file write is nothing), atomic
    replace, RAM-only on any IO failure."""

    def __init__(self, path: str | None = None):
        self.path = path
        self.msgs: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        if path is not None:
            try:
                d = json.loads(Path(path).read_text())
                if isinstance(d, dict):
                    # a stored file may predate the snapshot upsert (one
                    # message saved once per tool-part state change):
                    # dedupe ON LOAD -- last parts win, first position
                    # stays, or resumed history still duplicates
                    for sid, rows in d.items():
                        merged: dict[str, dict] = {}
                        ordered: list[str] = []
                        for r in rows:
                            mid = str(r.get("info", {}).get("id") or "")
                            if mid not in merged:
                                ordered.append(mid)
                            merged[mid] = r
                        self.msgs[sid] = [merged[m] for m in ordered]
            except Exception:
                pass

    def append(self, sid: str, info: dict, parts: list) -> dict:
        # the TurnRunner re-appends the SAME assistant message per
        # tool-part state change -- those are snapshots, not new messages:
        # an existing id UPSERTS in place or resumed history duplicates
        # the turn once per tool call
        mid = str(info.get("id") or "")
        existing = next((r for r in self.msgs.get(sid) or []
                         if r["info"].get("id") == mid), None) if mid \
            else None
        if existing is not None:
            existing["info"], existing["parts"] = info, parts
            row = existing
        else:
            row = {"info": info, "parts": parts}
        with self._lock:
            if existing is None:
                self.msgs.setdefault(sid, []).append(row)
            self._flush()
        return row

    def list(self, sid: str) -> list[dict]:
        with self._lock:
            return list(self.msgs.get(sid) or [])

    def get_msg(self, sid: str, mid: str) -> dict | None:
        with self._lock:
            return next((r for r in self.msgs.get(sid) or []
                         if r["info"].get("id") == mid), None)

    def _flush(self) -> None:
        if self.path is None:
            return
        try:
            # THE MULTI-WRITER MERGE (the vanishing-reply fix): two seats can
            # serve out of one cwd (an old seat that survived its restart, a
            # sibling test seat) and this is a WHOLE-FILE snapshot flush --
            # a stale boot copy would clobber the other writer's final-text
            # rows with this boot's older view: the assistant row reverts to
            # its empty opening state, the client's completion re-render
            # shows a vanished reply, and the exchange reads as 'my message
            # sitting under the model's previous response'. So the flush
            # re-reads disk first, unions sessions, unions rows by message
            # id, and lets the NEWER row win -- an unfinished row (created
            # only) never displaces a finished one (completed), and our RAM
            # row only displaces an OLDER disk row. Disk-first insertion
            # keeps first positions stable, per the load contract.
            try:
                disk = json.loads(Path(self.path).read_text())
            except Exception:
                disk = {}
            merged: dict = {}
            for src in (disk, self.msgs):
                for sid, rows in (src or {}).items():
                    bucket = merged.setdefault(sid, {})
                    for r in rows:
                        info = r.get("info") or {}
                        mid = str(info.get("id") or "")
                        if not mid:
                            continue
                        tm = info.get("time") or {}
                        ver = tm.get("completed") or tm.get("created") or 0
                        cur = bucket.get(mid)
                        if cur is None or cur[0] <= ver:
                            bucket[mid] = (ver, r)
            out = {sid: [v[1] for v in b.values()]
                   for sid, b in merged.items() if b}
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(out, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def flush(self) -> None:
        with self._lock:
            self._flush()


class SSEHub:
    """The /event fan-out: one queue per connected client; publish is a
    snapshot fan-out under a lock (a slow client can drop events, never a
    publisher's turn)."""

    def __init__(self):
        self._subs: list[Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> Queue:
        q: Queue = Queue(maxsize=256)
        q.put({"type": "server.connected", "properties": {}})
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(dict(event))
            except Exception:
                self.unsubscribe(q)


# ---- the turn loop ------------------------------------------------------------

def _provider_default(config: dict) -> dict:
    """The providerID -> default-modelID map every provider with a
    configured key advertises (real serve 1.18: `{"cortecs":
    "claude-sonnet-4", ...}`). The ATTACHED TUI resolves the model it
    submits with from THIS map — `{"default": {}}` leaves it with no
    model and Enter silently does nothing."""
    return {pid: next(iter(p.get("models") or {}), "")
            for pid, p in (config.get("provider") or {}).items()
            if next(iter(p.get("models") or {}), "")}


def _provider_shapes(config: dict) -> list:
    """The opencode SDK's /config/providers shape: an ARRAY of providers,
    each carrying full model metas (capabilities/cost/limit/...). The bare
    config dict crashes the attached TUI; opencode's own serve 1.18 ships
    this shape and the TUI's UI reads it directly."""
    prov = []
    for pid, p in (config.get("provider") or {}).items():
        opts = p.get("options") or {}
        models = {}
        for mid, m in (p.get("models") or {}).items():
            models[mid] = {
                "id": mid, "providerID": pid,
                "api": {"id": mid, "url": opts.get("baseURL", ""),
                        "npm": p.get("npm",
                                     "@ai-sdk/openai-compatible")},
                "name": (m or {}).get("name", mid), "family": "",
                "capabilities": {"temperature": True, "reasoning": True,
                                 "attachment": True, "toolcall": True,
                                 "input": {"text": True, "audio": False,
                                           "image": False, "video": False,
                                           "pdf": False},
                                 "output": {"text": True, "audio": False,
                                            "image": False, "video": False,
                                            "pdf": False},
                                 "interleaved": False},
                "cost": {"input": 0, "output": 0,
                         "cache": {"read": 0, "write": 0}},
                "limit": {"context": 128000, "output": 8192},
                "status": "active", "options": {}, "headers": {},
                "release_date": "", "variants": {},
            }
        prov.append({"id": pid, "name": p.get("name", pid),
                     "source": "api", "env": [], "key": "any",
                     "options": opts, "models": models})
    return prov


def _agent_shapes() -> list:
    """The /agent list the attached TUI needs to arm Submit: with NO
    primary agent the model chip stays empty and Enter is silently
    refused client-side (probed against real opencode serve 1.18.34 --
    rewriting its /agent response to [] reproduces the dead Enter).
    Membrane keeps its own turn prompts -- these are the client's UI
    rows only -- and allows everything, since the seat's exec runs
    through membrane entities, not opencode's permissions."""
    allow = [{"permission": "*", "pattern": "*", "action": "allow"}]
    return [
        {"name": "build", "native": True, "mode": "primary",
         "description": "The default agent. Executes tools based on "
                        "configured permissions.",
         "permission": allow, "options": {}},
        {"name": "plan", "native": True, "mode": "primary",
         "description": "Plan mode.",
         "permission": allow, "options": {}},
        {"name": "general", "native": True, "mode": "subagent",
         "description": "General-purpose subagent.",
         "permission": allow, "options": {}},
        {"name": "explore", "native": True, "mode": "subagent",
         "description": "Exploration subagent.",
         "permission": allow, "options": {}},
    ]


def _openai_tools(mcp_tools: list | None = None) -> list:
    """The MCP tool list in the OpenAI chat-completions format (the wire
    name is `function`, parameters come from the MCP inputSchema)."""
    tools = _mcp.TOOLS if mcp_tools is None else mcp_tools
    return [{"type": "function",
             "function": {"name": t["name"],
                          "description": t.get("description", ""),
                          "parameters": t.get("inputSchema") or
                          {"type": "object", "properties": {}}}}
            for t in tools]


def _tool_title(name: str, args: dict) -> str:
    s = json.dumps(args) if args else ""
    return f"{name}({s[:80]})"


def _chronological(rows: list) -> list:
    """Replay rows in created-ts order: the user's message rides above the
    reply it prompted, even if a multi-writer race left the store scrambled
    (stable sort -- store insertion order survives equal timestamps)."""
    return sorted(rows, key=lambda r: ((r.get("info") or {})
                                       .get("time") or {}).get("created") or 0)


def _tokens_fill(a_info: dict, resp: dict) -> None:
    """Real token accounting: upstream usage when the ladder provides it,
    SUMMED across the tool-loop's rounds. The hardcoded {total: 0} era is
    over -- the TUI and the companion feed were reading that placeholder."""
    t = a_info.setdefault("tokens", {"total": 0, "input": 0, "output": 0,
                                     "reasoning": 0,
                                     "cache": {"read": 0, "write": 0}})
    u = (resp or {}).get("usage")
    if not isinstance(u, dict) or not u:
        return

    def _i(v):
        try:
            return int(v or 0)
        except Exception:
            return 0

    det = u.get("completion_tokens_details")
    pdet = u.get("prompt_tokens_details")
    t["total"] = _i(t.get("total")) + _i(u.get("total_tokens"))
    t["input"] = _i(t.get("input")) + _i(u.get("prompt_tokens"))
    t["output"] = _i(t.get("output")) + _i(u.get("completion_tokens"))
    t["reasoning"] = _i(t.get("reasoning")) + (
        _i(det.get("reasoning_tokens")) if isinstance(det, dict) else 0)
    c = t.setdefault("cache", {"read": 0, "write": 0})
    c["read"] = _i(c.get("read")) + (
        _i(pdet.get("cached_tokens")) if isinstance(pdet, dict) else 0)


def _tokens_estimate(history: list, parts: list) -> dict:
    """The estimate fallback (the ladder answered without usage): ~4 chars
    per token over the request history and the produced text/reasoning."""
    def _n(s):
        return max(1, len(str(s or "")) // 4)
    inn = sum(_n(m.get("content")) for m in (history or []))
    out = sum(_n(p.get("text")) for p in (parts or [])
              if isinstance(p, dict) and p.get("type") == "text")
    rsn = sum(_n(p.get("text")) for p in (parts or [])
              if isinstance(p, dict) and p.get("type") == "reasoning")
    return {"total": inn + out, "input": inn, "output": out,
            "reasoning": rsn, "cache": {"read": 0, "write": 0}}


class TurnRunner:
    """THE TURN LOOP: client text -> the seat's own /v1 (all of the
    proxy's machinery, in-process, via loopback) -> tool_calls executed
    against the MCP tool impls -> role:'tool' results -> repeat. Every
    Message+Part state transition is published on the hub."""

    def __init__(self, hub, sessions, messages, call_ladder, executor,
                 system: str, model: str, tools: list | None = None):
        self.hub = hub
        self.sessions = sessions
        self.messages = messages
        self.call_ladder = call_ladder
        self.executor = executor
        self.system = system
        self.model = model
        self.tools = tools if tools is not None else None
        self._busy: dict[str, dict] = {}

    # -- lifecycle ------------------------------------------------------------

    def status(self, sid: str) -> dict:
        with self._lock_guard():
            return self._busy.get(sid) or {"type": "idle"}

    def _lock_guard(self):
        return _NL()

    def abort(self, sid: str) -> None:
        self._aborting = getattr(self, "_aborting", None) or set()
        self._aborting.add(sid)

    def _touch_session(self, sid: str) -> None:
        info = self.sessions.get(sid)
        self.hub.publish({"type": "session.updated", "sessionID": sid,
                          "info": dict(info) if info else {"id": sid}})
        self.hub.publish({"type": "session.diff", "sessionID": sid,
                          "diff": []})

    def _announce_user(self, sid: str, info: dict, parts: list) -> None:
        # the user message rides the stream BEFORE busy: message.updated,
        # one part event per part, then the session refresh (real serve
        # 1.18 order)
        self.hub.publish({"type": "message.updated", "sessionID": sid,
                          "info": dict(info)})
        for p in parts or []:
            self.hub.publish({"type": "message.part.updated",
                              "part": dict(p), "sessionID": sid,
                              "time": int(time.time() * 1000)})
        self._touch_session(sid)

        # -- the loop ---------------------------------------------------------------

    def _prior_history(self, sid: str, exclude_id: str) -> list[dict]:
        """THE SERVE-MODE NARRATIVE: the seat's turns used to carry the
        current ask ONLY -- the model never saw the conversation, so
        'who created it?' had no antecedent anywhere (the 'it'-word
        complaint). The stored conversation is replayed as real history:
        prior user/assistant TEXT parts in order (the machinery -- tool
        rounds, reasoning -- never replays), bounded to the last
        HISTORY_MAX_MESSAGES rows, each text capped."""
        out: list[dict] = []
        for row in self.messages.list(sid):
            info = row.get("info") or {}
            if exclude_id and str(info.get("id") or "") == exclude_id:
                continue
            role = info.get("role")
            if role not in ("user", "assistant"):
                continue
            text = "\n".join(
                str(p.get("text") or "") for p in row.get("parts") or []
                if isinstance(p, dict) and p.get("type") == "text").strip()
            if not text:
                continue
            out.append({"role": role, "content": text[:HISTORY_TEXT_CAP]})
        return out[-HISTORY_MAX_MESSAGES:]

    def run(self, sid: str, text: str, user_info: dict | None = None,
            user_parts: list | None = None) -> dict:
        self._aborting = getattr(self, "_aborting", None) or set()
        now = int(time.time() * 1000)
        self._busy[sid] = {"type": "busy"}
        if user_info is None:
            user_info = {"id": _gen_id("msg"), "sessionID": sid,
                         "role": "user", "agent": "build",
                         "model": {"providerID": "membrane",
                                   "modelID": self.model},
                         "time": {"created": now}}
            user_parts = [{"id": _gen_id("prt"), "sessionID": sid,
                           "messageID": user_info["id"], "type": "text",
                           "text": text, "time": {"start": now}}]
            self.messages.append(sid, user_info, user_parts)
        self._announce_user(sid, user_info, user_parts)
        self.hub.publish({"type": "session.status",
                          "status": {"type": "busy"}, "sessionID": sid})
        # the raw OpenAI history this turn sends up: the doctrine system
        # message + the session's prior narrative + the user turn, then
        # assistant/tool pairs as they form
        history = self._prior_history(sid, str(user_info.get("id") or "")) \
            if user_info else []
        history.append({"role": "user", "content": text})
        aid = _gen_id("msg")
        a_info = {"id": aid, "sessionID": sid, "role": "assistant",
                  "parentID": user_info["id"], "modelID": self.model,
                  "providerID": "membrane", "mode": "build",
                  "agent": "build",
                  "path": {"cwd": os.getcwd(), "root": os.getcwd()},
                  "cost": 0.0, "tokens": {"total": 0, "input": 0,
                                          "output": 0, "reasoning": 0,
                                          "cache": {"read": 0, "write": 0}},
                  "time": {"created": now}}
        # real serve announces the ASSISTANT message before any of its
        # parts, with a session refresh beside it. the ATTACHED TUI's
        # render reducer drops message.updated frames without a TOP-LEVEL
        # properties.sessionID (the assistant role never registers in its
        # message store and the reply vanishes at the completion
        # re-render), so every message.updated carries it
        self.hub.publish({"type": "message.updated", "sessionID": sid,
                          "info": dict(a_info)})
        self._touch_session(sid)
        parts: list = []

        def mkpart(p: dict) -> dict:
            # EVERY part carries the SDK base {id, sessionID, messageID}
            p = {"id": _gen_id("prt"), "sessionID": sid, "messageID": aid,
                 **p}
            parts.append(p)
            # a DEEP snapshot: the wire event must not share the live
            # part's nested dicts (the runner mutates time/state after
            # the open publish -- real serve streams the open with time
            # {start} only)
            self.hub.publish({"type": "message.part.updated",
                              "part": copy.deepcopy(p),
                              "time": int(time.time() * 1000),
                              "sessionID": sid})
            return p

        err_done = False
        rounds = 0
        while not err_done and rounds < MAX_ROUNDS:
            rounds += 1
            sid_flag = sid in self._aborting
            if sid in self._aborting or sid_flag:
                break
            body = {"model": self.model,
                    "messages": ([{"role": "system", "content": self.system}]
                                 if self.system else []) + history}
            tools = _openai_tools(self.tools) if self.tools is not None \
                else _openai_tools()
            if tools:
                body["tools"] = tools
            mkpart({"type": "step-start"})
            try:
                resp = self.call_ladder(body)
            except Exception as e:
                now = int(time.time() * 1000)
                mkpart({"type": "text",
                        "text": f"model ladder failed: "
                                f"{type(e).__name__}: {e}"[:300],
                        "time": {"start": now, "end": now}})
                a_info["error"] = {"name": "Error", "data":
                                   {"message": str(e)[:300]}}
                break
            msg = ((resp.get("choices") or [{}])[0].get("message")
                   or {})
            _tokens_fill(a_info, resp)
            tcs = msg.get("tool_calls")
            content = (msg.get("content") or "").strip()
            reason = "tool-calls" if tcs else "stop"
            if msg.get("reasoning") or msg.get("thinking"):
                now = int(time.time() * 1000)
                # the REAL serve streams the reasoning part exactly like
                # the text one: it opens EMPTY with time.start, the
                # deltas ride on the text field, a final part.updated
                # closes it with time.end -- a part left unclosed pins
                # the attached TUI's Thought block on a Thinking spinner
                # (the sync POST /message lane renders the STORED part)
                rtext = str(msg.get("reasoning") or msg.get("thinking")
                            or "")
                rpart = mkpart({"type": "reasoning", "text": "",
                                "time": {"start": now}})
                rpart["text"] = rtext
                self.hub.publish({"type": "message.part.delta",
                                  "sessionID": sid, "messageID": aid,
                                  "partID": rpart["id"], "field": "text",
                                  "delta": rtext})
                rpart["time"]["end"] = int(time.time() * 1000)
                self.hub.publish({"type": "message.part.updated",
                                  "part": dict(rpart),
                                  "time": int(time.time() * 1000),
                                  "sessionID": sid})
            if tcs:
                a_info["time"].setdefault("start", int(time.time() * 1000))
                asst_msg = {"role": "assistant", "content": content or "",
                            "tool_calls": tcs}
                if msg.get("reasoning"):
                    asst_msg["reasoning"] = msg["reasoning"]
                history.append(asst_msg)
                for tc in tcs:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or "?"
                    call_id = tc.get("id") or ""
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                        if not isinstance(args, dict):
                            args = {}
                    except json.JSONDecodeError:
                        args = {}
                    tstart = int(time.time() * 1000)
                    part = mkpart({"type": "tool", "callID": call_id,
                                   "tool": name,
                                   "state": {"status": "pending",
                                             "input": args,
                                             "raw": json.dumps(args)},
                                   "time": {"start": tstart}})
                    self.messages.append(sid, a_info, list(parts))
                    if sid in self._aborting:
                        break
                    try:
                        output = self.executor(name, args, call_id)
                        part["state"] = {"status": "completed",
                                         "input": args,
                                         "output": str(output or ""),
                                         "title": _tool_title(name, args),
                                         "time": {"start": tstart,
                                                  "end": int(
                                                      time.time() * 1000)}}
                    except Exception as e:
                        part["state"] = {"status": "error", "input": args,
                                         "error": (f"{type(e).__name__}:"
                                                   f" {e}")[:300],
                                         "title": _tool_title(name, args),
                                         "time": {"start": tstart,
                                                  "end": int(
                                                      time.time() * 1000)}}
                    self.hub.publish({"type": "message.part.updated",
                                      "part": dict(part),
                                      "time": int(time.time() * 1000),
                                      "sessionID": sid})
                    self.messages.append(sid, a_info, list(parts))
                    history.append({"role": "tool", "tool_call_id": call_id,
                                    "content":
                                    (part["state"].get("output") or
                                     part["state"].get("error") or "")[:8000],
                                    "name": name})
                mkpart({"type": "step-finish", "reason": "tool-calls",
                        "cost": 0, "tokens": dict(a_info["tokens"])})
                continue
            if content:
                now = int(time.time() * 1000)
                # real serve streams the text part: it opens EMPTY with
                # time.start, the deltas ride, a final part.updated closes
                # it with time.end
                part = mkpart({"type": "text", "text": "",
                               "time": {"start": now}})
                part["text"] = content
                self.hub.publish({"type": "message.part.delta",
                                  "sessionID": sid, "messageID": aid,
                                  "partID": part["id"], "field": "text",
                                  "delta": content})
                part["time"]["end"] = int(time.time() * 1000)
                self.hub.publish({"type": "message.part.updated",
                                  "part": dict(part),
                                  "time": int(time.time() * 1000),
                                  "sessionID": sid})
            a_info["finish"] = "stop"
            mkpart({"type": "step-finish", "reason": "stop", "cost": 0,
                    "tokens": dict(a_info["tokens"])})
            break
        if "error" in a_info:
            a_info.pop("finish", None)
        if not (a_info.get("tokens") or {}).get("total"):
            a_info["tokens"] = _tokens_estimate(history, parts)
        a_info["time"]["completed"] = int(time.time() * 1000)
        self.messages.append(sid, a_info, parts)
        self.hub.publish({"type": "message.updated", "sessionID": sid,
                          "info": dict(a_info)})
        self._busy.pop(sid, None)
        self._aborting.discard(sid)
        self.sessions.touch(sid)
        self.hub.publish({"type": "session.status",
                          "status": {"type": "idle"}, "sessionID": sid})
        self.hub.publish({"type": "session.idle", "sessionID": sid})
        self._touch_session(sid)
        return {"info": a_info, "parts": parts}


class _NL:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


# ---- the loopback ladder + the in-process executor -----------------------------

def loopback_ladder(port: int):
    """The turn loop's model call: a buffered POST to THIS seat's /v1 --
    the gate, injection, routing and capture machinery runs once, in
    process, exactly where every other client's turn runs."""
    import json as _json

    def call(body: dict) -> dict:
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        data = _json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT_S) as resp:
            return _json.loads(resp.read())
    return call


def mcp_executor(port: int):
    """The default executor: the MCP tool impls themselves -- the same
    relay surface the TUI uses (all guards stay in the proxy). Unknown
    tools are a clean error, never a crash."""
    def exec_tool(name: str, args: dict, call_id: str = "") -> str:
        impl = _mcp.TOOL_IMPLS.get(name)
        if impl is None:
            raise ValueError(f"unknown membrane tool: {name}")
        return impl(args or {}, port)
    return exec_tool


# ---- the HTTP surface ----------------------------------------------------------

def make_handler(hub=None, sessions=None, messages=None, user="opencode",
                 password="", config=None, runner=None, **_dep) -> type:
    """The opencode-compatible HTTP handler, parametrized by the running
    seat's stores/runner (a factory because ThreadingHTTPServer wants a
    class)."""
    hub = hub if hub is not None else SSEHub()
    config = config if config is not None else {}

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # -- plumbing ---------------------------------------------------------

        def _authed(self) -> bool:
            if not password:
                return True
            hdr = self.headers.get("Authorization", "")
            if not hdr.startswith("Basic "):
                return False
            try:
                given = base64.b64decode(hdr[6:]).decode()
                ok = hmac.compare_digest(given, f"{user}:{password}")
            except Exception:
                ok = False
            if ok:
                return True
            # an Authorization on the SAME pair only; timing-safe compare
            return ok

        def _json_out(self, obj, code: int = 200, close: bool = False):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            if close:
                self.close_connection = True
            self.end_headers()
            self.wfile.write(b)

        def _json_err(self, code: int, msg: str, close: bool = False):
            self._json_out({"type": "error", "error": {"message": msg}},
                           code, close=close)

        def _notfound(self) -> None:
            self._json_err(404, "not found", close=True)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return {}

        # -- SSE --------------------------------------------------------------

        def _sse(self, wrap: bool = False) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.close_connection = True
            self.end_headers()
            q = hub.subscribe()
            try:
                self._sse_flush(q, wrap=wrap)
            finally:
                hub.unsubscribe(q)

        def _sse_flush(self, q, keepalive_s: float = 15.0,
                       wrap: bool = False):
            while True:
                try:
                    ev = q.get(timeout=keepalive_s)
                except Empty:
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    continue
                # the real serve's two SSE schemas: /event is the bare
                # Event ({id, type, properties}); /global/event wraps
                # every frame as GlobalEvent
                # {directory, project, payload:{id, type, properties}} --
                # the attached TUI's event client UNWRAPS payload and
                # DROPS bare frames, so without the wrapper its LIVE SSE
                # render path is dead (the sync POST response was the
                # only render that ever worked)
                props = {k: v for k, v in ev.items() if k != "type"}
                if list(props) == ["properties"]:
                    props = props["properties"]
                env = {"id": _gen_id("evt"), "type": ev.get("type"),
                       "properties": props}
                if wrap:
                    env = {"directory": os.getcwd(), "project": "global",
                           "payload": env}
                data = json.dumps(env, default=str)
                self.wfile.write(f"event: {ev.get('type')}\n".encode())
                self.wfile.write(f"data: {data}\n\n".encode())
                try:
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        # -- routing ----------------------------------------------------------

        def do_GET(self):
            if not self._authed():
                self.send_response(401)
                self.send_header("WWW-Authenticate",
                                 'Basic realm="membrane"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path = self.path.split("?", 1)[0]
            if path == "/global/health":
                self._json_out({"healthy": True, "version": VERSION})
            elif path == "/event":
                self._sse()
            elif path == "/global/event":
                self._sse(wrap=True)
            elif path == "/session":
                self._json_out(sessions.list())
            elif path.startswith("/session/") and path.endswith("/message"):
                sid = path[len("/session/"):-len("/message")]
                if sessions.get(sid) is None:
                    return self._notfound()
                self._json_out(messages.list(sid))
            elif path == "/session/status":
                self._json_out({s: {"type": "idle"} for s in
                                sessions.sessions})
            elif path.startswith("/session/"):
                rest = path[len("/session/"):]
                if rest.endswith("/message/"):
                    return self._notfound()
                segs = rest.split("/")
                sid = segs[0]
                if segments_len(segs) == 3 and segs[1] == "message":
                    row = messages.get_msg(sid, segs[2])
                    if row is None:
                        return self._notfound()
                    return self._json_out(row)
                if segments_len(segs) == 2:
                    if segs[1] in ("todo", "children"):
                        return self._json_out([])
                    if segs[1] == "diff":
                        # the attached TUI seeds its Modified Files
                        # sidebar state from this fetch; the old
                        # fall-through returned the SESSION OBJECT and
                        # the client's diff getter then read undefined
                        # (`J().length`) -- a wide-terminal crasher
                        return self._json_out([])
                    if segs[1] == "message":
                        # display order heals whatever the store survived:
                        # rows ride created-ts order (chronological), so the
                        # user's turn sits above the reply it prompted even
                        # if a multi-writer race scrambled the rows
                        return self._json_out(
                            _chronological(messages.list(sid)))
                s = sessions.get(sid)
                if s is None:
                    return self._notfound()
                self._json_out(s)
            elif path == "/config":
                self._json_out(config)
            elif path.startswith("/config/providers"):
                self._json_out({"providers": _provider_shapes(config),
                                "default": _provider_default(config)})
            elif path == "/provider":
                # the SDK's Provider route: the TUI's model picker reads
                # `default` and `connected` here (a DIFFERENT endpoint
                # from /config/providers; the array shape there crashed
                # it). `connected` is a LIST of provider ids with auth;
                # an empty one leaves the TUI with no model chip and
                # Enter silently does nothing.
                self._json_out({"all": _provider_shapes(config),
                                "default": _provider_default(config),
                                "connected":
                                [p["id"] for p in
                                 _provider_shapes(config)]})
            elif path == "/experimental/capabilities":
                self._json_out({"backgroundSubagents": False})
            elif path == "/experimental/console":
                self._json_out({"consoleManagedProviders": [],
                                "switchableOrgCount": 0})
            elif path == "/experimental/resource":
                self._json_out({})
            elif path == "/experimental/workspace" or \
                    path == "/experimental/workspace/status":
                self._json_out([])
            elif path == "/provider/auth":
                self._json_out({})
            elif path == "/mcp":
                self._json_out({})
            elif path == "/formatter":
                self._json_out([])
            elif path == "/lsp":
                self._json_out([])
            elif path == "/vcs":
                self._json_out({"branch": None, "default_branch": None})
            elif path.startswith("/api/"):
                # the /api/* family the TUI probes at boot (real serve
                # answers them all 200): {location, data} envelope,
                # `data` empty for everything but the model list
                loc = {"directory": os.getcwd(),
                       "project": {"id": PROJECT_ID,
                                   "directory": os.getcwd()}}
                if path == "/api/model":
                    out = [{"id": mid, "providerID": pid,
                            "name": (m or {}).get("name", mid),
                            "api": {"id": mid, "type": "aisdk",
                                    "package": p.get("npm",
                                                     "@ai-sdk/openai"
                                                     "-compatible"),
                                    "url": (p.get("options") or {})
                                    .get("baseURL", "")},
                            "capabilities": {"tools": True,
                                             "input": ["text"],
                                             "output": ["text"]}}
                           for pid, p in (config.get("provider") or {})
                           .items()
                           for mid, m in (p.get("models") or {}).items()]
                elif path == "/api/provider":
                    out = [{"id": pid, "name": p.get("name", pid),
                            "api": {"type": "aisdk",
                                    "package": p.get("npm",
                                                     "@ai-sdk/openai"
                                                     "-compatible"),
                                    "url": (p.get("options") or {})
                                    .get("baseURL", "")},
                            "request": {"headers": {}, "body": {}}}
                           for pid, p in (config.get("provider") or {})
                           .items()]
                else:
                    out = []
                self._json_out({"location": loc, "data": out})
                return
            elif path == "/experimental/workspace/status":
                self._json_out([])
            elif path == "/agent":
                self._json_out(_agent_shapes())
            elif path == "/command":
                self._json_out([])
            elif path == "/path":
                # the SDK's Path.Info: worktree/directory are opencode's
                # runtime cwd (the TUI's file panel lives there)
                self._json_out({"home": os.path.expanduser("~"),
                                "state": os.path.expanduser(
                                    "~/.local/state/opencode"),
                                "config": os.path.expanduser(
                                    "~/.config/opencode"),
                                "worktree": os.getcwd(),
                                "directory": os.getcwd()})
            elif path.startswith("/project/") and \
                    path.endswith("/directories"):
                self._json_out([])
            elif path == "/project/current" or path == "/project":
                cur = {"id": PROJECT_ID,
                       "worktree": os.getcwd(),
                       "vcs": "git",
                       "time": {"created": int(time.time() * 1000),
                                "updated": int(time.time() * 1000)},
                       "sandboxes": []}
                self._json_out(
                    cur if not path.endswith("/current") else cur)
            else:
                self._notfound()

        def do_POST(self):
            if not self._authed():
                self.send_response(401)
                self.send_header("WWW-Authenticate",
                                 'Basic realm="membrane"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path = self.path.split("?", 1)[0]
            if path == "/session":
                b = self._read_body()
                s = sessions.create(title=b.get("title"),
                                    parent_id=b.get("parentID"))
                # the seat's turn model rides the session shape (the TUI
                # shows it; the runner keeps its own routing anyway)
                dm = _provider_default(config)
                if dm:
                    pid = next(iter(dm))
                    s["model"] = {"id": dm[pid], "providerID": pid,
                                  "variant": "default"}
                self._json_out(s)
                return
            if path.startswith("/session/"):
                rest = path[len("/session/"):]
                segs = rest.split("/")
                sid = segs[0]
                if segments_len(segs) == 2:
                    action = segs[1]
                    if action in ("message", "prompt_async"):
                        return self._message(sid, bool(action ==
                                                       "prompt_async"))
                    if action == "abort":
                        if runner is not None:
                            runner.abort(sid)
                        return self._json_out(True)
                    if action == "init":
                        return self._json_out(True)
                    if action == "share":
                        return self._json_out(True)
                    if action == "unshare":
                        return self._json_out(True)
                    if action == "summarize":
                        return self._json_out(True)
                    if action == "shell":
                        return self._notfound()
                    return self._notfound()
                if segments_len(segs) == 3 and segs[2] == "permissions":
                    return self._json_out(True)
            if path == "/log" or path == "/instance/dispose":
                return self._json_out(True)
            if path.startswith("/tui/"):
                return self._json_out(True)
            self._notfound()

        # -- the message turn ---------------------------------------------------

        def _message(self, sid: str, async_: bool) -> None:
            if sessions.get(sid) is None:
                return self._notfound()
            b = self._read_body()
            if runner is None:
                return self._json_err(503, "the turn runner is not wired")
            text = "\n".join(
                p.get("text", "") for p in (b.get("parts") or [])
                if isinstance(p, dict) and p.get("type") == "text")
            if not text.strip():
                return self._json_err(400, "message needs a text part")
            user_info, row = self._record_user(sid, text, b)
            if async_:
                threading.Thread(target=runner.run,
                                 args=(sid, text, user_info),
                                 daemon=True).start()
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            result = runner.run(sid, text, user_info=user_info,
                                user_parts=row["parts"])
            if isinstance(result, dict) and "info" in result:
                out = {"info": result["info"], "parts": result["parts"]}
            else:
                out = {"info": result, "parts": []}
            self._json_out(out)

        def _record_user(self, sid: str, text: str,
                         body: dict | None = None) -> tuple[dict, dict]:
            # the TUI POSTs {agent, model{providerID, modelID}, parts};
            # the server enriches (ids, times) -- a user message with an
            # EMPTY modelID crashes the attached TUI's model lookup
            body = body or {}
            dm = _provider_default(config)
            model = body.get("model")
            if not isinstance(model, dict) or not model.get("modelID"):
                model = ({"providerID": next(iter(dm)),
                          "modelID": dm[next(iter(dm))]} if dm
                         else {"providerID": "membrane",
                               "modelID": getattr(runner, "model", "")})
            now = int(time.time() * 1000)
            info = {"id": _gen_id("msg"), "sessionID": sid, "role": "user",
                    "agent": body.get("agent") or "build",
                    "model": model,
                    "time": {"created": now}, "summary": {"diffs": []}}
            row = messages.append(sid, info, [{"id": _gen_id("prt"),
                                               "sessionID": sid,
                                               "messageID": info["id"],
                                               "type": "text",
                                               "text": text}])
            # the announcement (message.updated + part + session refresh)
            # is the TurnRunner's _announce_user, so the event order is
            # identical whichever lane created the user message
            return info, row

        def do_PATCH(self):
            if not self._authed():
                return self._unauth()
            path = self.path.split("?", 1)[0]
            if path.startswith("/session/"):
                segs = path[len("/session/"):].split("/")
                if segments_len(segs) == 1:
                    s = sessions.patch(segs[0],
                                       self._read_body().get("title"))
                    if s is None:
                        return self._notfound()
                    return self._json_out(s)
            if path == "/config":
                return self._json_out(config)
            self._notfound()

        def do_DELETE(self):
            if not self._authed():
                return self._unauth()
            path = self.path.split("?", 1)[0]
            if path.startswith("/session/"):
                segs = path[len("/session/"):].split("/")
                if segments_len(segs) == 1:
                    if not sessions.delete(segs[0]):
                        return self._notfound()
                    self.hub_deleted(segs[0])
                    return self._json_out(True)
            self._notfound()

        def hub_deleted(self, sid):
            hub.publish({"type": "session.deleted",
                         "info": {"sessionID": sid}})

        def _unauth(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="membrane"')
            self.send_header("Content-Length", "0")
            self.end_headers()

    def segments_len(segs: list) -> int:
        return len([s for s in segs if s != ""])

    return _Handler


def _free_port(preferred: int) -> int:
    for p in range(preferred, min(preferred + 64, 65536)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if s.connect_ex(("127.0.0.1", p)) != 0:
                    pass
                s.bind(("0.0.0.0", p))
                return p
        except OSError:
            continue
    raise OSError(f"no free port at or above {preferred}")


def start(port: int, host: str, user: str, password: str, config: dict,
          runner, sessions: SessionStore, messages: MessageStore):
    """Bind the native surface (returns (httpd, thread); the thread is a
    daemon -- the seat's main loop owns the seat's life)."""
    handler = make_handler(hub=runner.hub, sessions=sessions,
                           messages=messages, user=user, password=password,
                           config=config, runner=runner)
    httpd = ThreadingHTTPServer((host, port), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True,
                          name="membrane-serve")
    th.start()
    return httpd, th