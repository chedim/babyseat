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
client needs to work on the room through run and the field's
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
import sys
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

    def patch(self, sid: str, title: str | None = None,
              agent: str | None = None) -> dict | None:
        with self._lock:
            s = self.sessions.get(sid)
            if s is None:
                return None
            if title is not None:
                s["title"] = title
            if agent is not None and str(agent).strip() in \
                    ("build", "plan", "general", "explore", "watch", "phone"):
                s["agent"] = str(agent).strip()
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
        self._last_flush_ts = 0.0
        self._dirty_sessions: set | None = None
        if path is not None:
            try:
                d = json.loads(Path(path).read_text())
                if isinstance(d, dict):
                    # a stored file may predate the snapshot upsert (one
                    # message saved once per tool-part state change):
                    # dedupe ON LOAD -- last parts win, first position
                    # stays, or resumed history still duplicates
                    # THE TOKEN MEND (the `K.tokens.output undefined`
                    # TUI-crash exhibit, 2026-10-06, THE DATA-HALF): a
                    # stored assistant row predating the tokens covenant
                    # (the old watchdog-error mints carried NO tokens)
                    # POISONED EVERY SESSION LOAD -- the attached TUI's
                    # reducer findLast lands on it and reads
                    # K.tokens.output -> the crash ON STARTUP, every
                    # boot, forever. The load MENDS: every assistant
                    # row gets a full tokens dict (zeros when unknown)
                    # -- the persisted poison dies with this store's
                    # next flush.
                    for sid, rows in d.items():
                        merged: dict[str, dict] = {}
                        ordered: list[str] = []
                        for r in rows:
                            info = r.get("info") or {}
                            mid = str(info.get("id") or "")
                            if info.get("role") == "assistant" and \
                                    not isinstance(info.get("tokens"),
                                                   dict):
                                info["tokens"] = {"total": 0, "input": 0,
                                                  "output": 0, "reasoning": 0,
                                                  "cache": {"read": 0,
                                                            "write": 0}}
                                r["info"] = info
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
                # a REAL new message: flush now (the completion lanes
                # read the disk); a tool-state snapshot waits for the
                # cadence (the 24-round turn rewrote the 10MB file per
                # state change before)
                self._dirty_sessions = (self._dirty_sessions or set())
                self._dirty_sessions.add(sid)
                self._flush()
                self._last_flush_ts = time.time()
            else:
                self._dirty_sessions = (self._dirty_sessions or set())
                self._dirty_sessions.add(sid)
                self._flush_maybe()
        return row

    def append_part(self, sid: str, part: dict) -> dict:
        """THE INJECT'S STORE LANE (the event-thinkers' lines): ONE
        part onto the session's LAST message (an assistant message
        when present, else minted) — the row persists + the flush
        follows the same cadence rules as the append's.

        THE WIRE'S ROW-BEFORE-PART COVENANT (the `K.tokens.output
        undefined` TUI crash, 2026-10-06, at its ROOT): when this lane
        MINTS an assistant row, the part's messageID names a row the
        CLIENT has never seen -- its findLast lands undefined and the
        reducer crashes reading tokens. So the mint publishes the FULL
        row on the hub FIRST (message.updated with id/tokens/time --
        the real reducer's demanded shape) and the caller's part
        publishes second. The minted row's announce carries a
        `_synthetic: True` flag (the reducer treats it like any
        assistant row; the render shows the part's text)."""
        hub = getattr(self, "hub", None)
        minted_info: dict | None = None
        with self._lock:
            rows = self.msgs.get(sid) or []
            row = rows[-1] if rows else None
            if row is None or row["info"].get("role") != "assistant":
                aid = _gen_id("msg")
                info = {"id": aid, "sessionID": sid, "role": "assistant",
                        "parentID": part.get("messageID") or "",
                        "modelID": "membrane", "providerID": "membrane",
                        "agent": "memory",
                        "cost": 0.0,
                        "tokens": {"total": 0, "input": 0, "output": 0,
                                   "reasoning": 0,
                                   "cache": {"read": 0, "write": 0}},
                        "time": {"created": int(time.time() * 1000)}}
                part = dict(part)
                part["messageID"] = aid
                row = {"info": info, "parts": [part]}
                self.msgs.setdefault(sid, []).append(row)
                minted_info = info      # the wire's row-before-part
            else:
                row["parts"] = list(row.get("parts") or []) + [part]
            self._dirty_sessions = (self._dirty_sessions or set())
            self._dirty_sessions.add(sid)
            self._flush_maybe()
        if hub is not None and minted_info is not None:
            # THE ROW BEFORE ITS PART: the reducer must HOLD the row
            # (id + tokens) before any part names it -- findLast never
            # lands undefined
            hub.publish({"type": "message.updated", "info":
                         dict(minted_info), "sessionID": sid})
        return row

    FLUSH_MIN_S = 5.0        # the disk is not the RAM: tool-state
                             # snapshots land per turn-end + cadence;
                             # a 24-round turn rewrote the 10MB file per
                             # tool state change (the busy-slow tax)

    def _flush_maybe(self) -> None:
        """The cadence flush: a tool-state change waits FLUSH_MIN_S; a
        real message append (_flush direct) never waits."""
        now = time.time()
        if now - self._last_flush_ts >= self.FLUSH_MIN_S:
            self._flush()
            self._last_flush_ts = now
        return

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
            # THE DIRTY SET (the throttle fix): only the sessions THIS
            # writer touched merge + rewrite; the untouched sides ride
            # disk-bytes-forward (a sibling writer's file stays theirs)
            disk_out: dict = {}
            merged_targets = self._dirty_sessions or set(self.msgs) or \
                set(disk)
            disk_out = disk
            self._dirty_sessions = None
            merged: dict = {}
            for src in (disk, self.msgs):
                for sid, rows in (src or {}).items():
                    if sid not in merged_targets:
                        continue          # untouched: another writer's lane
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
            out = dict(disk)
            for sid, b in merged.items():
                if b:
                    out[sid] = [v[1] for v in b.values()]
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(out, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    _dirty_sessions: set | None = None

    def flush(self) -> None:
        with self._lock:
            self._flush()


class SSEHub:
    """The /event fan-out: one queue per connected client; publish is a
    snapshot fan-out under a lock (a slow client can drop events, never a
    publisher's turn).

    THE SILENT ZOMBIE (2026-10-10, the operator: "the audio is not voicing
    any of the LLM's messages"): a streamed reply publishes one delta per
    token; a client a moment behind (the HA relay to a phone) overflowed its
    256-slot queue mid-reply, was UNSUBSCRIBED -- and its connection stayed
    open on keepalives: no part ever closed for it again, so nothing was
    voiced and nothing reconnected. Now: deltas are droppable (the part's
    closing update carries the whole text); losing anything else CLOSES the
    connection (q.lost), and the client reconnects and resyncs."""

    QUEUE_MAX = 4096
    DROPPABLE = ("message.part.delta",)

    def __init__(self):
        self._subs: list[Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> Queue:
        q: Queue = Queue(maxsize=self.QUEUE_MAX)
        q.lost = False
        q.dropped = 0
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
        droppable = event.get("type") in self.DROPPABLE
        for q in subs:
            if droppable and q.qsize() >= self.QUEUE_MAX // 2:
                q.dropped = getattr(q, "dropped", 0) + 1     # behind: the closing update carries the text
                continue
            try:
                q.put_nowait(dict(event))
            except Exception:
                if droppable:
                    q.dropped = getattr(q, "dropped", 0) + 1
                    continue
                q.lost = True                                 # its stream closes; the client resyncs
                self.unsubscribe(q)
                cb = getattr(self, "on_lost", None)           # the seat panel's event
                if cb is not None:
                    try:
                        cb()
                    except Exception:
                        pass


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


def _agent_shapes(srv=None) -> list:
    """The /agent list the attached TUI needs to arm Submit: with NO
    primary agent the model chip stays empty and Enter is silently
    refused client-side (probed against real opencode serve 1.18.34 --
    rewriting its /agent response to [] reproduces the dead Enter).
    Membrane keeps its own turn prompts -- these are the client's UI
    rows only -- and allows everything, since the seat's exec runs
    through membrane entities, not opencode's permissions.
    THE LIVE MIND: with a server in hand the list also reports the
    thinking pool AS AGENTS -- the 3 loop workers (each one's claim:
    the entity it is processing, or idle) and the thinker/dreamer with
    their live stats -- so `/agents` in the TUI shows membrane's minds,
    not just spawnable types."""
    allow = [{"permission": "*", "pattern": "*", "action": "allow"}]
    rows = [
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
        # THE WATCH-LOOP AGENTS (the operator's 2026-10-02 design):
        # non-operator thinking loops run as background subagents --
        # one per watch fire; the SESSION TITLE (not the static list)
        # carries the processed entity's id, so the frontend shows
        # 'watch: f:membrane/server.py' style rows per loop.
        {"name": "watch", "native": True, "mode": "subagent",
         "description": "A watch-trigger thinking loop: reacts or "
                        "ignores one fired watch event through the "
                        "membrane tools (the event's entity id rides "
                        "the session title).",
         "permission": allow, "options": {}},
    ]
    if srv is None:
        return rows
    rows.extend(_mind_rows(srv, allow))
    return rows


def _mind_rows(srv, allow) -> list:
    """The loop pool's 3 workers + the thinker + the dreamer as agent
    rows, each description carrying the mind's LIVE state (claims,
    stats) -- the pool of thinking agents, visible in `/agents`."""
    rows: list[dict] = []
    # the loop pool: 3 named workers; the running loops' entity claims
    # live in LoopRunner._held, the full agent titles in srv.loops.titles
    loops = getattr(srv, "loops", None)
    max_loops = getattr(loops, "max", 3) if loops is not None else 3
    running = loops.running() if loops is not None else 0
    try:
        titles = [t for t in (getattr(loops, "titles", None) or [])]
    except Exception:
        titles = []
    pool = getattr(srv, "events_pool", None)
    try:
        queued = pool.depth() if pool is not None else 0
    except Exception:
        queued = 0
    for i in range(1, int(max_loops) + 1):
        if i <= len(titles):
            claim = f"processing {titles[i - 1]}"
        elif i <= running:
            claim = "processing"
        else:
            claim = "idle"
        rows.append({
            "name": f"loop-{i}", "native": True, "mode": "subagent",
            "hidden": True,
            "description": f"A thinking-loop worker ({claim}); "
                           f"{queued} event(s) queued.",
            "permission": allow, "options": {}})
    # the ambient minds (their stats dicts are the honest readout)
    _mode = getattr(srv, "ambient_mode", "day")
    _wakes = ""
    try:
        ws = getattr(srv, "_wakeups", None)
        if ws:
            _wakes = f"; last wakeup: {ws[-1].get('kind')}"
    except Exception:
        _wakes = ""
    th = getattr(srv, "thinker", None)
    if th is not None:
        st = getattr(th, "stats", {}) or {}
        rows.append({
            "name": "thinker", "native": True, "mode": "subagent",
            "hidden": True,
            "description": f"The seat's inner speech ({_mode}: the "
                           f"{_mode == 'night' and '30s night pulse' or '1s pulse'})"
                           f"{_wakes}: {st.get('processed', 0)} beats "
                           f"processed, {st.get('spoken', 0)} spoken, "
                           f"{st.get('ticks', 0)} ticks.",
            "permission": allow, "options": {}})
    dr = getattr(srv, "dreamer", None)
    if dr is not None:
        st = getattr(dr, "stats", {}) or {}
        rows.append({
            "name": "dreamer", "native": True, "mode": "subagent",
            "hidden": True,
            "description": f"The field's idle cognition: {st.get('waves', 0)} "
                           f"dream waves, {st.get('committed', 0)} branches "
                           f"committed, {st.get('gc', 0)} gc sweeps.",
            "permission": allow, "options": {}})
    return rows


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


# ---- THE LANE GATE (the division of hands, 2026-10-06) ----------------------
# The seat's two minds have different hands: the OPERATOR (build/plan
# sessions -- the main chat) plants INTENTS, commits and runs commands
# through the executor (entity_link/unlink); the LISTENERS
# (the watch loops -- agent 'watch:<eid>') hold the ENTITY HANDS --
# add/edit/remove/link/unlink -- the events they process are their
# material. Reads and memory are both lanes'. Plan mode (the agent
# 'plan') is the operator minus `intend` (planning sessions don't plant
# commitments -- the direction needs the user's build-mode voice).
# Kill switch: MEMBRANE_LANE_GATE=0 restores the flat surface (the
# ablation/debug). The LAUNCH-MODE stdio MCP child (an external
# opencode's config) keeps the full surface -- membrane cannot identify
# the per-call agent there; the native serve is the enforcement point.

# THE WATCH HANDS' OWNER (2026-10-08): every watch a turn plants carries
# the planting agent, so the attention inventory can show whose it is and
# a later loop can retire a fulfilled goal's leftovers
WATCH_HANDS = ("entity_edit", "field_watch")


def stamp_watch_owner(name: str, args, agent) -> dict:
    """The turn's agent stamped onto the watches a tool call plants (an
    explicit owner is kept); other tools pass through untouched."""
    if name not in WATCH_HANDS or not isinstance(args, dict) or not agent:
        return args
    out = dict(args)
    if name == "field_watch":
        out.setdefault("owner", str(agent))
        return out
    for k in ("watch_ops", "watch_fields"):
        v = out.get(k)
        if isinstance(v, list):
            out[k] = [dict(o, owner=o.get("owner") or str(agent))
                      if isinstance(o, dict) else o for o in v]
    return out


OPERATOR_TOOLS = frozenset((
    # reads
    "query", "state", "graph", "entities_related", "entity_query",
    "entity_read", "entity_tracks",
    # memory
    "learn", "forget",
    # steering (intentions -- THE OPERATOR'S PEN; fulfill = the close
    # with a receipt, the listeners' too: their landed work moves the board)
    "intend", "intents", "fulfill",
    # room
    "peer_post", "peer_seats", "junk_cut",
    # the banner tracks (the operator's header)
    "entity_watch", "entity_unwatch",
    # the seat's own alert dial (2026-10-08): how many watch events may
    # interrupt vs fold into the WHERE WE ARE digest
    "attention",
    # THE EXECUTOR'S DOOR (2026-10-08, the operator's call): the operator
    # runs commands itself -- link to tool:executor runs inline, the
    # receipt comes back in the same turn. The HA seat's main chat kept
    # planting 'run X' goals and waiting a whole loop pass for a listener
    # to do a one-line command. The other entity hands stay the listeners'.
    "entity_link", "entity_unlink", "method_define", "method_call", "method_each",
    # THE FLOOD GATES (2026-10-08, the operator's call: "open the flood gates
    # between the lanes"): the chat lane holds the entity hands and the
    # watches too -- refused -> intend -> a loop cost more than it protected
    "entity_add", "entity_edit", "entity_remove", "field_watch",
    # COMPOSITION IS INHERITANCE (the doctrine's own hand; it sat in no
    # lane -- 52 refusals, 2026-10-08)
    "entity_inherit",
))
LISTENER_TOOLS = (OPERATOR_TOOLS | {
    # the entity hands (the listeners' material)
    "entity_add", "entity_edit", "entity_remove",
    "entity_link", "entity_unlink", "method_define", "method_call", "method_each",
    # the board's spatial watches (2026-10-08): attention is the
    # listeners' to allocate; the chat lane announces through intend
    "field_watch",
}) - {"intend"}
# plan reads/communicates: never plants, closes or executes
PLAN_TOOLS = OPERATOR_TOOLS - {"intend", "fulfill", "entity_link", "method_define", "method_call", "method_each",
                               "entity_unlink", "entity_add", "entity_edit",
                               "entity_remove", "field_watch",
                               "entity_inherit"}

# THE PHONE LANE (2026-10-08): a call's turn reads and answers. A misheard
# sentence must never reach the shell or rewrite entities; a request
# becomes a visible goal (intend) the loops carry out.
PHONE_TOOLS = frozenset(("query", "state", "graph", "entities_related",
                         "entity_query", "entity_read", "entity_tracks",
                         "learn", "intend", "intents"))

_LANE_REFUSALS = {
    "field_watch": ("plan mode: watches aren't planted from a planning "
                    "session -- the build-mode turn plants them"),
    "entity_add": ("plan mode: entities aren't created from a planning "
                   "session -- the build-mode turn does it"),
    "entity_edit": ("plan mode: entities aren't edited from a planning "
                    "session -- the build-mode turn does it"),
    "entity_remove": ("plan mode: entities aren't removed from a planning "
                      "session -- the build-mode turn does it"),
    "entity_link": ("plan mode: commands don't run from a planning "
                    "session -- the build-mode turn links them to the "
                    "executor"),
    "entity_unlink": ("plan mode: the executor's links change from a "
                      "build-mode turn"),
    "fulfill": ("plan mode: goals aren't closed from a planning "
                "session -- the build-mode turn (or a listener with the "
                "receipt) closes them"),
    "intend": ("plan mode: commitments aren't planted from a planning "
               "session -- the direction needs the user's build-mode "
               "word; the board reads via goal(action='list')"),
}
_LISTENER_REFUSALS = {
    "intend": ("listener lane: a watch loop does not plant new goals -- "
               "take this goal's next step yourself, close it (goal action='close') with the "
               "receipt, or end with BLOCKED; the operator's chat plants "
               "commitments"),
}


def _lane_gate_off() -> bool:
    # runtime-read: a kill switch set mid-session acts immediately
    return os.environ.get("MEMBRANE_LANE_GATE") == "0"


def _lane_of(agent: str | None) -> str:
    """The session agent -> the hand-lane: 'operator' (build/plan/None),
    'plan' (the read-only planning agent on a plan-BODY turn), or
    'listener' (the watch loops + anything else)."""
    a = str(agent or "").strip().lower()
    if not a or a in ("build", "plan"):
        return "plan" if a == "plan" else "operator"
    if a == "phone":
        return "phone"
    return "listener"


def _lane_tools(lane: str) -> frozenset:
    if _lane_gate_off():
        return _mcp_names()
    return {"operator": OPERATOR_TOOLS, "plan": PLAN_TOOLS,
            "listener": LISTENER_TOOLS,
            "phone": PHONE_TOOLS}.get(lane, OPERATOR_TOOLS)


def _mcp_names() -> frozenset:
    return frozenset(t["name"] for t in _mcp.TOOLS)


def _lane_refusal(name: str, lane: str) -> str | None:
    """The honest refusal for a cross-lane tool call (None = allowed or
    nothing to say)."""
    if _lane_gate_off():
        return None
    if name in _lane_tools(lane):
        return None
    # the plan-mode texts are for the PLAN lane only: a listener told
    # "plan mode: ..." concluded its seat was in plan mode (2026-10-08)
    if lane == "phone":
        return (f"phone lane: {name} doesn't run from a call (a misheard "
                "sentence must not change the boat) -- answer from what you "
                "can read, or plant the request with goal(action='plant') so a loop "
                "carries it out")
    r = _LANE_REFUSALS.get(name) if lane == "plan" else _LISTENER_REFUSALS.get(name)
    if r is not None:
        return r
    return (f"the {name} tool is not your lane's hand (lane: {lane}); "
            "reads, memory and the steering surface are.")


def _openai_tools_for(agent: str | None) -> list:
    """The ADVERTISED tools for the lane (the same registry, filtered)."""
    names = _lane_tools(_lane_of(agent))
    # THE TOOL FAMILIES (the token diet, 2026-10-09): ~10 action-tools that
    # resolve to these same impls; MEMBRANE_TOOL_FAMILIES=0 = the legacy 24
    if os.environ.get("MEMBRANE_TOOL_FAMILIES", "1") != "0":
        from . import toolset as _ts
        return _ts.advertised(names)
    return [t for t in _mcp.TOOLS if t["name"] in names]


def _tool_title(name: str, args: dict) -> str:
    s = json.dumps(args) if args else ""
    return f"{name}({s[:80]})"


def _chronological(rows: list) -> list:
    """Replay rows in arrival order (completed-when-set, else created-ts): the user's message rides above the
    reply it prompted, even if a multi-writer race left the store scrambled
    (stable sort -- store insertion order survives equal timestamps)."""
    return sorted(rows, key=lambda r: ((r.get("info") or {})
                                       .get("time") or {}).get("completed")
                  or ((r.get("info") or {})
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


BUSY_WAIT_S = 300.0   # a queued second POST waits this long, then gets a visible busy stub
DELTA_COALESCE_S = 0.25   # a streaming part's text goes out at most this often (was: every token)
HEARTBEAT_S = 15.0    # busy-heartbeat cadence while a turn runs
QUEUE_DEPTH_MAX = 8   # queued turns per session beyond which POSTs refuse fast (the flood guard)

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
        self._guards: dict[str, threading.Lock] = {}
        self._busy_lock = threading.Lock()
        self.busy_wait_s: float = BUSY_WAIT_S
        self.heartbeat_s: float = HEARTBEAT_S
        self.queue_depth_max: int = QUEUE_DEPTH_MAX
        # THE BOUNDED LEDGER (the unbounded-growth family): finished
        # drain threads prune at each turn; the results ledger keeps
        # the newest DRAIN_RESULT_MAX (read-after-256-turns is dead).
        self.DRAIN_MAX = 128
        self.DRAIN_RESULT_MAX = 256
        self._agents: dict[str, str] = {}
        self.cosmetic_busy = False
        self._turn_stops: dict[str, threading.Event] = {}
        self._aborting: set[str] = set()
        self._agents: dict[str, str] = {}
        self._drain_order: dict[str, float] = {}
    # the per-session FIFO: queued turns that WILL run (the drain),
    # their results keyed by queue id, and the drain threads
        self._queue: dict[str, list[dict]] = {}
        self._queue_lock = threading.Lock()
        self._drain_results: dict[str, dict] = {}
        self._drain_threads: list[threading.Thread] = []

    # -- lifecycle ------------------------------------------------------------

    def status(self, sid: str) -> dict:
        with self._lock_guard():
            return self._busy.get(sid) or {"type": "idle"}

    def _lock_guard(self):
        return self._busy_lock

    def abort(self, sid: str) -> None:
        """THE STOP BUTTON (2026-10-09, the operator: "the stop button is not
        stopping"). A running turn: the flag (checked between rounds) AND
        the turn's model socket closed, so a round blocked in the model
        call fails now, not after its rung budget. Nothing running: an
        honest idle (the cosmetic busy kept the button on with nothing to
        stop) and a flag that only a turn starting within ABORT_PREARM_S
        honours (Stop right after Send still cancels; a stale press used to
        cut the NEXT turn's tools short whenever it came)."""
        self._aborting = getattr(self, "_aborting", None) or set()
        self._turn_activity = getattr(self, "_turn_activity", {}) or {}
        self._aborting.add(sid)
        try:                                     # Stop ends auto-chat too
            from . import voice as _voice
            if _voice.auto_on(self, sid):
                _voice.auto_set(self, sid, False)
        except Exception:
            pass
        if sid in (getattr(self, "_busy", None) or {}):
            self._kill_turn_socket(sid)
            return
        self._abort_ts = getattr(self, "_abort_ts", None) or {}
        self._abort_ts[sid] = time.time()
        self.hub.publish({"type": "session.status",
                          "status": {"type": "idle"}, "sessionID": sid})
        self.hub.publish({"type": "session.idle", "sessionID": sid})

    def _inject_card(self, sid: str, ext: dict, rounds: int, mkpart) -> None:
        """THE INJECTION AS A CARD (2026-10-08, the operator: show it like
        every tool call): what membrane added to this round -- the first
        round's, and any later round's that differs."""
        _inj = ext.get("injection") if isinstance(ext, dict) else None
        if not (isinstance(_inj, str) and _inj.strip()) or _inj == getattr(self, "_last_inj", {}).get(sid):
            return
        if not hasattr(self, "_last_inj"):
            self._last_inj = {}
        self._last_inj[sid] = _inj
        _now = int(time.time() * 1000)
        _ms = ext.get("massage") or {}
        _title = f"membrane injection · ~{len(_inj) // 4} tokens" + (
            f" · alignment {_ms.get('alignment')}" if _ms.get("alignment") else "")
        _ip = mkpart({"type": "tool", "callID": f"inj-{rounds}", "tool": "membrane",
                      "state": {"status": "completed",
                                "input": {"round": rounds, **({k: _ms[k] for k in ("alignment", "tokens", "picked") if k in _ms})},
                                "output": _inj, "title": _title,
                                "time": {"start": _now, "end": _now}},
                      "time": {"start": _now, "end": _now}})
        self.hub.publish({"type": "message.part.updated", "part": dict(_ip),
                          "time": _now, "sessionID": sid})

    def preempt(self, sid: str) -> bool:
        """A PERSON SPOKE (2026-10-10, the operator: "interrupt the chat loop
        whenever there is a message"): the running turn of this session is cut
        now -- the abort flag (between rounds) + its model socket (a round
        blocked in the model) -- and its finally drains the queue, so the
        person's message runs next. Unlike Stop, auto-chat stays on: the loop
        goes on, with the person's message in it. False when nothing runs."""
        running = sid in (getattr(self, "_turn_stops", None) or {}) or sid in (getattr(self, "_busy", None) or {})
        if not running:
            return False
        self._aborting = getattr(self, "_aborting", None) or set()
        self._aborting.add(sid)
        self._kill_turn_socket(sid)
        return True

    def _kill_turn_socket(self, sid: str) -> None:
        """THE ABORT'S TEETH (the wedged round's fast kill, 2026-10-07):
        the abort flag is only checked BETWEEN rounds -- a turn wedged
        INSIDE a delivery's socket read sat its full 90s rung budget
        (or 300s of watchdog silence) before anything freed. The
        watchdog closes the turn's open backend socket: the blocked
        `read()` RAISES immediately, the round fails fast, the turn's
        own finally frees the guard. Tracked: `_turn_sockets[sid]` =
        the response object the delivery is reading (wired by
        `_v1_chat`'s ladder)."""
        resp = (getattr(self, "_turn_sockets", {}) or {}).get(sid)
        if resp is None:
            return
        try:
            resp.close()
        except Exception:
            pass

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
            if info.get("membraneOnly"):
                continue          # said to the voice alone, not to the model
            text = "\n".join(
                str(p.get("text") or "") for p in row.get("parts") or []
                if isinstance(p, dict) and p.get("type") == "text").strip()
            if not text:
                continue
            out.append({"role": role, "content": text[:HISTORY_TEXT_CAP]})
        return out[-HISTORY_MAX_MESSAGES:]

    # -- THE BUSY CONTRACT ----------------------------------------------------
    def _guard(self, sid: str):
        g = self._guards.get(sid)
        if g is None:
            g = self._guards.setdefault(sid, threading.Lock())
        return g

    def _heartbeat(self, sid: str, stop: threading.Event) -> None:
        start = time.time()
        while not stop.wait(max(0.05, float(self.heartbeat_s))):
            self.hub.publish({"type": "session.status",
                              "status": {"type": "busy",
                                         "elapsed_ms": int((time.time() - start) * 1000)},
                              "sessionID": sid})

    def _mint_user_row(self, sid: str, text: str) -> tuple:
        """The door's minted user row (a caller with no row: the watch
        loops, internal turns). Its agent is the SESSION's (via
        _turn_agent) -- it was hardcoded 'build', and the turn's own agent
        wins the lane registry, so every loop turn laned OPERATOR and its
        exec link was refused (2026-10-07)."""
        now = int(time.time() * 1000)
        info = {"id": _gen_id("msg"), "sessionID": sid,
                "role": "user", "agent": self._turn_agent(sid, None),
                "model": {"providerID": "membrane",
                          "modelID": self.model},
                "time": {"created": now},
                "summary": {"diffs": []}}
        parts = [{"id": _gen_id("prt"), "sessionID": sid,
                  "messageID": info["id"],
                  "type": "text", "text": text,
                  "time": {"start": now}}]
        return info, parts

    def _turn_agent(self, sid: str, user_info: dict | None) -> str:
        """The turn's agent (the lane gate's input): the turn's own agent
        wins, then the session doc's, then 'build'. A bare loop key
        ('loop::<agent>' -- the proxy-only loop session, no doc in this
        store) resolves to ITS agent: a watch loop must never lane as the
        operator (2026-10-07: every loop turn did, the exec link was
        refused, the HA seat's commands never ran)."""
        a = str((user_info or {}).get("agent") or "")
        if a:
            return a
        try:
            _docs = getattr(getattr(self, "sessions", None), "sessions",
                            None) or {}
            a = str((_docs.get(sid) or {}).get("agent") or "")
        except Exception:
            a = ""                  # a bare runner (tests, no store)
        if a:
            return a
        if isinstance(sid, str) and sid.startswith("loop::"):
            return sid[len("loop::"):] or "build"
        return "build"

    def run(self, sid: str, text: str, user_info: dict | None = None,
            user_parts: list | None = None, _drained: bool = False) -> dict:
        """THE GUARDED ENTRY, now a REAL QUEUE: a second message on a busy
        session first publishes a visible {session.status busy
        queued:true} event, then WAITS FOR ITS TURN -- when the running
        turn completes, the next queued turn RUNS (the drain, in its own
        thread with its own heartbeat + idle). The old give-up
        (busy_wait_s -> {'status':'busy'} stub) is the queued-forever
        bug: a turn past busy_wait_s (a 20-min agentic loop over the
        300s wait) died the queued POST with a stub and the TUI's
        message sat Queued forever, never executing, never reporting
        done. The stub lane only covers the pathological case now:
        queue_depth_max turns already waiting -> a fast 'busy' refusal
        (the flood guard). A synchronous lane whose wait gives up gets
        a QUEUED ack (its turn still runs -- results ride result_of).
        A heartbeat thread publishes elapsed_ms while a turn runs. The
        finally-block guaranties the guard frees and _busy cleans even
        if the turn body raises (no stuck-busy after a crash).
        THE MESSAGE AT THE DOOR (the disappear-then-appear-sent
        exhibit): the user message is ANNOUNCED here, at acceptance —
        both lanes (direct and queued) — so the TUI's message row
        exists the moment the seat has it, not when the turn's inner
        machinery reaches its own announce N seconds (or turns) later.
        _run_inner's announce is idempotent against this one."""
        # THE DOOR (both lanes): the stored row announces BEFORE any
        # guard/gate work. When user_info is None the row is minted
        # here (the old path minted inside _run_inner); otherwise the
        # caller's row is used as-is.
        # THE OPERATOR'S CLOCK: a BLOCKED goal parks until a person speaks
        # (watch_agents._parked reads it); loop sessions never stamp it
        try:
            _s = self.sessions.get(sid) or {}
            _psrv = getattr(self, "srv", None)
            if _psrv is not None and _lane_of(_s.get("agent")) != "listener":
                _psrv._operator_last_ts = time.time()
        except Exception:
            pass
        if user_info is None:
            user_info, user_parts = self._mint_user_row(sid, text)
            self.messages.append(sid, user_info, user_parts)
        elif user_parts is None:
            # THE EMPTY BUBBLE (2026-10-08): an info-only caller (the
            # prompt_async thread did this) would announce a part-less row
            # and mark it announced -- the text never streamed. The row is
            # already stored: announce ITS parts.
            _stored = self.messages.get_msg(sid, user_info.get("id"))
            user_parts = list((_stored or {}).get("parts") or [])
        if not user_info.get("continueTurn"):
            self._announce_user(sid, user_info, user_parts)
        user_info["_announced"] = True      # the inner's announce skips
        guard = self._guard(sid)
        got = guard.acquire(blocking=False)
        if not got and _drained:
            # THE DRAIN OWNS A QUEUED TURN (the endless-reply fix,
            # 2026-10-08): wait for the session, NEVER re-enqueue -- the
            # re-enqueue re-armed the same turn after every finished one
            # (two queued messages answered 11x and 7x, alternating)
            guard.acquire()
        elif not got:
            # a PERSON's message does not wait behind a running turn: that
            # turn is cut and this one goes next (loops and goals still wait)
            if _from_person(text, user_parts):
                if self.preempt(sid):
                    print(f"[serve] {sid}: a person spoke -- the running turn is cut, theirs is next",
                          file=sys.stderr)
            depth = self._queued_depth(sid)
            if depth >= self.queue_depth_max:
                # THE FLOOD GUARD: too many turns already waiting in the
                # drain queue -- refuse fast with the visible stub (the
                # old behaviour, now only the pathological case)
                self.hub.publish({"type": "session.status",
                                  "status": {"type": "busy", "queued": True},
                                  "sessionID": sid})
                return {"status": "busy", "sessionID": sid}
            self.hub.publish({"type": "session.status",
                              "status": {"type": "busy", "queued": True},
                              "sessionID": sid})
            # THE QUEUE: enqueue and WAIT FOR THE RESULT -- the running
            # turn's finally drains the next queued turn into a worker
            # (run(_drained=True)); THIS thread never runs it itself (it
            # used to race the drain for the guard: the loser re-enqueued
            # the same turn, forever). The sync POST /message lane still
            # gets its result (the worker files it under the qid).
            done = threading.Event()
            mine = self._enqueue(sid, text, user_info, user_parts, done)
            # THE KICK: the running turn may have finished between the
            # failed acquire above and the enqueue -- its drain then saw
            # an empty queue and nobody would ever run this one
            if guard.acquire(blocking=False):
                guard.release()
                self._start_next(sid)
            if not done.wait(timeout=float(self.busy_wait_s)):
                # the drain still owns it (unbounded patience): a queued
                # ack now, the turn runs when its turn comes
                return {"status": "queued", "queued_id": mine,
                        "sessionID": sid}
            return self._drain_results.get(mine) or {
                "status": "queued", "queued_id": mine, "sessionID": sid}
        stop = threading.Event()
        if not hasattr(self, "_turn_stops"):
            self._turn_stops = {}
        self._turn_stops[sid] = stop
        hb = threading.Thread(target=self._heartbeat, args=(sid, stop),
                              daemon=True)
        hb.start()
        # THE TURN WATCHDOG (the graveyard covenant): a timer at 120s
        # aborts the turn + streams the honest error — one dead
        # backend socket can't hold the guard for 5 minutes.
        wd = threading.Thread(target=self._turn_watchdog,
                              args=(sid, time.time()), daemon=True)
        wd.start()
        # THE ACTIVITY CLOCK'S SEED (the activity-aware watchdog): the
        # turn's last-movement stamp = now (the rounds + tool completions
        # touch it; the watchdog aborts only on the SILENT stretch)
        self._turn_activity = getattr(self, "_turn_activity", {}) or {}
        self._turn_activity[sid] = time.time()
        self._current_turn_sid = sid    # THE ABORT'S ADDRESS (the loopback's
                                        # registration key: _turn_sockets)
        self._turn_sockets = getattr(self, "_turn_sockets", {}) or {}
        try:
            return self._run_inner(sid, text, user_info, user_parts)
        finally:
            self._turn_sockets.pop(sid, None)
            self._turn_activity.pop(sid, None)
            self._current_turn_sid = None
            stop.set()
            self._turn_stops.pop(sid, None)
            guard.release()
            self._busy.pop(sid, None)
            (getattr(self, "_aborting", None) or set()).discard(sid)
            # THE DRAIN: the next queued turn runs NOW (own thread --
            # this thread is the synchronous lane returning a result).
            self._start_next(sid)

    # -- the per-session queue -----------------------------------------------

    def _enqueue(self, sid: str, text: str, user_info, user_parts,
                 done: threading.Event) -> str:
        """One queued turn joined this session's FIFO. Returns its
        queue id (the waker sets the event when the turn has run)."""
        with self._queue_lock:
            q = self._queue.setdefault(sid, [])
            qid = _gen_id("q")
            q.append({"qid": qid, "text": text, "user_info": user_info,
                      "user_parts": user_parts, "done": done})
        self.hub.publish({"type": "session.status",
                          "status": {"type": "busy", "queued": True,
                                     "depth": len(q)},
                          "sessionID": sid})
        return qid

    def _take_interjections(self, sid: str, aid: str) -> list:
        """Pop every queued message of this session for the RUNNING turn:
        their rows are stored + announced already (the door); their
        waiters are released with a 'merged' result."""
        with self._queue_lock:
            q = self._queue.get(sid) or []
            taken, self._queue[sid] = list(q), []
        out = []
        for ent in taken:
            out.append((str(ent.get("text") or ""), ent.get("user_info") or {}))
            self._drain_results[ent["qid"]] = {"status": "merged", "into": aid,
                                               "sessionID": sid}
            try:
                ent["done"].set()
            except Exception:
                pass
        if taken:
            self.hub.publish({"type": "session.status",
                              "status": {"type": "busy", "depth": 0},
                              "sessionID": sid})
        return [(t, ui) for t, ui in out if t]

    def _queued_depth(self, sid: str) -> int:
        with self._queue_lock:
            return len(self._queue.get(sid) or [])

    def _start_next(self, sid: str) -> None:
        """THE DRAIN: pop the next queued turn (FIFO) and run it in its
        own thread; the guard is FREE here (the finishing turn released
        it). Each drained turn re-enters run() -- its own guard
        acquisition wins the just-freed lock (no race: another direct
        POST could take it too, and the queue's acquire would then
        chain normally), its own heartbeat, its own idle, and ITS
        finally drains the next one. Queue exhaustion -> the hub's
        state settles honest: the last drained turn publishes idle and
        the depth-0 heartbeat follows."""
        while True:
            nxt = None
            with self._queue_lock:
                q = self._queue.get(sid) or []
                if q:
                    nxt = q.pop(0)
            if nxt is None:
                return
            ent = nxt

            def _worker(ent=ent, sid=sid):
                done_ev = ent["done"]
                try:
                    res = self.run(sid, ent["text"],
                                   user_info=ent["user_info"],
                                   user_parts=ent["user_parts"],
                                   _drained=True)
                    self._drain_results[ent["qid"]] = res
                except Exception as e:
                    self._drain_results[ent["qid"]] = {
                        "error": f"{type(e).__name__}: {e}"[:200]}
                finally:
                    done_ev.set()
                    # THE BOUNDED LEDGER (the unbounded-growth family):
                    # threads and results are one-turn artifacts -- the
                    # finished thread drops out of the list; old results
                    # past the cap drop oldest-first (a qid read after
                    # 256 later turns is long abandoned).
                    try:
                        with self._queue_lock:
                            self._drain_threads = [
                                t for t in self._drain_threads
                                if t is not threading.current_thread()
                                and t.is_alive()]
                            if len(self._drain_threads) > self.DRAIN_MAX:
                                self._drain_threads = self._drain_threads[-self.DRAIN_MAX:]
                            if len(self._drain_results) > self.DRAIN_RESULT_MAX:
                                for old in sorted(
                                        self._drain_results,
                                        key=lambda k: self._drain_order.get(
                                            k, 0))[:-self.DRAIN_RESULT_MAX]:
                                    self._drain_results.pop(old, None)
                                    self._drain_order.pop(old, None)
                            self._drain_order[ent["qid"]] = time.time()
                    except Exception:
                        pass
            self._drain_threads.append(threading.Thread(
                target=_worker, daemon=True))
            self._drain_threads[-1].start()
            return

    def result_of(self, qid: str) -> dict | None:
        """A queued turn's result (the async lane's read); None while it
        still waits/runs."""
        with self._queue_lock:
            return self._drain_results.get(qid)

    # -- the tool watchdog (the swirl post-mortem, now LIVE) -----------------
    # The kill-artifact pattern (two sessions: a tool part 'pending'
    # forever, the operator kills, no evidence): executor calls are now
    # timed + name-stamped; a call over TOOL_SLOW_S warns ONCE (the
    # stderr names the tool + args head); a call over TOOL_DUMP_S
    # dumps EVERY thread's stack (faulthandler) so a real thread-stall
    # carries its own evidence into the log -- no more post-mortems on
    # a corpse.
    TOOL_SLOW_S = 5.0
    TOOL_DUMP_S = 45.0
    # THE TURN'S OWN WATCHDOG (the 26-message graveyard exhibit): a
    # turn whose loop outlives TURN_MAX_S aborts ITSELF (publishes an
    # honest error part + marks the turn dead) -- one dead backend
    # socket can't hold the guard + queue the room for 5 minutes.
    TURN_MAX_S = 300.0       # a cold 14B load + the transcript prefill's honest scale (the watchdog's 120s fired ON a normal qwen cold turn — the honest budget is the ladder's own 300)

    def _turn_watchdog(self, sid: str, t0: float) -> None:
        """The turn's timer thread: past TURN_MAX_S marks the abort
        flag (the loop's own checks break) + streams the honest error
        part -- the queue's graveyard can never form again.

        THE ACTIVITY-AWARE CLOCK (the 'turn outlived 300s' on a HEALTHY
        multi-round tool turn, 2026-10-07: the brain's turn had six
        FAST tool rounds; three cloud deliveries hung 90s apiece and
        the TOTAL crossed the watchdog while the turn was still
        PROGRESSING -- each round's delivery DID advance it). The
        clock now reads ACTIVITY: `_turn_activity[sid]` (touched per
        round by the runner + per tool completion) -- the watchdog
        aborts only when NO ACTIVITY for TURN_MAX_S (a dead hang), not
        on a long-but-alive turn. The legacy t0 remains the FINAL cap
        (the turn's total lifetime never exceeds 3x -- a forever-turn
        is still impossible)."""
        last = getattr(self, "_turn_activity", {}).get(sid, t0) or t0
        cap = t0 + self.TURN_MAX_S * 3         # the absolute lifetime
        while True:
            now = time.time()
            last = max(last,
                       getattr(self, "_turn_activity", {}).get(sid, 0) or 0)
            if now - last >= self.TURN_MAX_S:
                break                          # A REAL hang: 300s of silence
            if now >= cap:
                break                          # the forever-turn's absolute cap
            if self._stop_wait(sid, 1.0):
                return
        if sid not in self._busy:
            return                      # the turn ended (the normal way)
        self._aborting.add(sid)
        # THE ABORT'S TEETH (the wedged round's fast kill): the flag is
        # only read BETWEEN rounds -- close the turn's in-flight backend
        # socket; the delivery's read raises NOW (not after the rung's
        # full budget)
        try:
            self._kill_turn_socket(sid)
        except Exception:
            pass
        # THE EVIDENCE: the error part + the stack dump (the corpse's
        # own name)
        import faulthandler
        print(f"[serve] turn {sid} STILL RUNNING after {self.TURN_MAX_S}s "
              "-- the turn watchdog aborts (the graveyard covenant)",
              file=sys.stderr)
        # THE EVIDENCE'S OWN FILE (the GIL-free lane): the dump writes
        # to the log file -- the stderr's file handle competes with
        # the wedged threads' prints; a dedicated dump file never
        # interleaves (the corpse's own readable evidence).
        try:
            dp = Path(getattr(self.messages, "path", "") or "/tmp")
            dp = dp.parent / "turn-dump.txt" if hasattr(dp, "parent") \
                else Path("/tmp/turn-dump.txt")
            with open(str(dp), "w") as df:
                faulthandler.dump_traceback(file=df, all_threads=True)
            print(f"[serve] the dump: {dp}", file=sys.stderr)
        except Exception as e:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        self._stream_error_part(sid, "the turn outlived "
                                   f"{self.TURN_MAX_S}s -- aborted by "
                                   "the turn watchdog (a backend "
                                   "socket's hang, not your message)")

    def _stop_wait(self, sid: str, t: float) -> bool:
        """The abort-stop event's WAITER (the watchdog's clock): a
        REAL Event.wait(timeout) — the watchdog PARKS between checks
        (the drainer's busy-wait sin atoned: the watchdog was born
        spinning, burning a core per armed turn)."""
        ev = (getattr(self, "_turn_stops", {}) or {}).get(sid)
        if ev is None:
            time.sleep(t)                      # the event's lost: park raw
            return False
        return ev.wait(t)                      # the honest park

    def _stream_error_part(self, sid: str, msg: str) -> None:
        """One assistant-facing error part: a text part appended to the
        session's row + the hub streams it (the client sees WHY)."""
        try:
            now_ms = int(time.time() * 1000)
            part = {"id": _gen_id("prt"), "sessionID": sid, "type": "text",
                    "text": msg, "time": {"start": now_ms, "end": now_ms}}
            self.messages.append_part(sid, part)
            self.hub.publish({"type": "message.part.updated",
                              "part": part, "sessionID": sid})
        except Exception:
            pass

    def _tool_watchdog_start(self, sid: str, name: str, call_id: str,
                             args: dict) -> None:
        if not hasattr(self, "_tool_t0"):
            self._tool_t0 = {}
        self._tool_t0[call_id] = (time.time(), name,
                                  str(args)[:120])

    def _tool_watchdog_end(self, call_id: str, name: str) -> None:
        t0 = (getattr(self, "_tool_t0", {}) or {}).pop(call_id, None)
        if t0 is None:
            return
        el = time.time() - t0[0]
        if el > self.TOOL_SLOW_S:
            print(f"[serve] tool {name!r} SLOW: {el:.1f}s "
                  f"(args {t0[2][:80]})", file=sys.stderr)

    def _tool_watchdog_dump(self, call_id: str) -> None:
        """The thread-stall dump: a timer thread armed per slow call;
        cancelled on completion (the normal path never fires)."""
        t0 = (getattr(self, "_tool_t0", {}) or {}).get(call_id)
        if t0 is None:
            return
        name, args_head = t0[1], t0[2]
        time.sleep(self.TOOL_DUMP_S)
        live = (getattr(self, "_tool_t0", {}) or {}).get(call_id)
        if live is None:
            return                    # completed meanwhile
        # THE EVIDENCE (the real file: faulthandler needs a fileno --
        # the dump goes to the stderr handle directly, both dead
        # boots' lesson)
        import faulthandler
        print(f"[serve] tool {name!r} STILL PENDING after "
              f"{self.TOOL_DUMP_S}s (args {args_head}); ALL THREADS:",
              file=sys.stderr)
        try:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception as dump_e:
            print(f"[serve] tool {name!r} STILL PENDING after "
                  f"{self.TOOL_DUMP_S}s; stack dump failed ({dump_e})",
                  file=sys.stderr)

    def _cosmetic_busy(self, sid: str) -> bool:
        """THE COSMETIC-BUSY DECIDER (the operator's design): the
        operator's PRIMARY session (the main chat: agent build/plan)
        never reports idle to the client — the thinking loop's end is
        internal, the stream stays busy so a new message queues or
        reopens a loop and the EVENT-THINKERS (the 3-loop pool) may
        inject into this session at any moment, rendering as part of
        the ongoing 'thinking loop'. The loop sessions' (watch:/_)
        idle stays real (they're visible background work).
        ``self.cosmetic_busy`` (the boot's flag) arms it — off by
        default (the wire stays real for plain turns; the launch boot
        arms it when the event-thinkers pool is live)."""
        if not getattr(self, "cosmetic_busy", False):
            return False
        agent = (self._agents or {}).get(sid)
        return agent in (None, "build", "plan")

    ABORT_PREARM_S = 10.0

    def _run_inner(self, sid: str, text: str, user_info: dict | None = None,
            user_parts: list | None = None) -> dict:
        self._aborting = getattr(self, "_aborting", None) or set()
        # a Stop pressed with nothing running lapses (see abort)
        _ats = (getattr(self, "_abort_ts", None) or {}).pop(sid, None)
        if _ats is not None and time.time() - _ats > self.ABORT_PREARM_S:
            self._aborting.discard(sid)
        now = int(time.time() * 1000)
        _t_turn = time.time()
        self._busy[sid] = {"type": "busy"}
        # THE AGENT REGISTRY (the cosmetic-busy decider's + the lane
        # gate's input): THE TURN'S OWN AGENT WINS (the attached TUI
        # sends the mode per message -- 'plan' rows stored build-era
        # sessions and every turn laned build: the 'everything is build
        # despite plan selected' exhibit); the session doc is the
        # fallback (the loops' child sessions carry agent=watch:/loop-N;
        # a plain message POST carries build).
        try:
            self._agents[sid] = self._turn_agent(sid, user_info)
        except Exception:
            pass
        if user_info is None:
            # THE FALLBACK ROW echoes the session's agent (a loop turn's
            # message row carries the loop's agent, never a fake build)
            _sa = str((self.sessions.sessions.get(sid) or {}).get("agent")
                      or "build")
            user_info = {"id": _gen_id("msg"), "sessionID": sid,
                         "role": "user",
                         "agent": _sa,
                         "model": {"providerID": "membrane",
                                   "modelID": self.model},
                         "time": {"created": now}}
            user_parts = [{"id": _gen_id("prt"), "sessionID": sid,
                           "messageID": user_info["id"], "type": "text",
                           "text": text, "time": {"start": now}}]
            self.messages.append(sid, user_info, user_parts)
        # THE DOOR'S DEDUPE (both lanes announce once): the accepting
        # lane's run() already announced this row — a re-announce
        # would render the row twice in the TUI.
        if not user_info.get("_announced"):
            self._announce_user(sid, user_info, user_parts)
        self.hub.publish({"type": "session.status",
                          "status": {"type": "busy"}, "sessionID": sid})
        # THE MINDS' BRANCH: a turn on a session whose agent is one of
        # membrane's own minds (thinker, dreamer, loop-N) is a STATUS
        # REPORT from the live process -- a real (tiny) turn: the user
        # message stored/announced above, the assistant reply rendered
        # from the mind's live state, hub parts streamed like any turn.
        # Not implemented this edit (the turn tail is intricate) --
        # deferred until after the defect passes.
        # the raw OpenAI history this turn sends up: the doctrine system
        # message + the session's prior narrative + the user turn, then
        # assistant/tool pairs as they form
        history = self._prior_history(sid, str(user_info.get("id") or "")) \
            if user_info else []
        # A CONTINUATION (auto-chat, predict role -- 2026-10-11, the operator's
        # option 2: "the turn just keeps going"): no message at all; the next
        # round starts from the conversation as it stands, his own last reply last
        if not user_info.get("continueTurn"):
            history.append({"role": "user", "content": text})
        # MEMBRANE'S OWN VOICE (the third participant): it speaks first,
        # in its own thread, outside the session's messages
        # an auto self-turn (predict role) aims from Argus's own last reply,
        # not from its boilerplate prompt
        _aim_text = text
        if user_info.get("autoTurn"):
            _aim_text = (getattr(self, "_auto_aim", None) or {}).pop(sid, "") or text
        try:
            from . import voice as _voice
            if not user_info.get("voiceTurn"):       # the voice just spoke: it IS this turn
                _voice.on_turn_start(self, sid, _aim_text, str(user_info.get("id") or ""),
                                     _lane_of((self._agents or {}).get(sid)))
        except Exception:
            pass

        # MEMBRANE ONLY (the chat's LLM switch off): the voice answers, the
        # model is never called; the voice still hears the operator
        if user_info.get("membraneOnly"):
            # the field hears the operator either way (the replay path's
            # precedent: the message alone, no model prose -- and never
            # the voice's own line)
            try:
                _cap = getattr(getattr(self, "srv", None), "capturer", None)
                if _cap is not None:
                    _cap.on_turn(text)
            except Exception:
                pass
            try:
                from . import voice as _voice
                _voice.on_turn_end(self, sid, text, "",
                                   _lane_of((self._agents or {}).get(sid)))
            except Exception:
                pass
            self._busy.pop(sid, None)
            self.sessions.touch(sid)
            self.hub.publish({"type": "session.status",
                              "status": {"type": "idle"}, "sessionID": sid})
            self.hub.publish({"type": "session.idle", "sessionID": sid})
            return {"info": dict(user_info), "parts": []}
        aid = _gen_id("msg")
        # THE STAMP'S TRUTH (the TUI's chip reads the session's
        # model): the assistant row's modelID = the TURN'S pick (the
        # session's model -- the operator's switch holds in the
        # provenance), not the boot's
        _turn_pick = "glm-5.3-flash:cloud"
        try:
            _sess = self.sessions.sessions.get(sid) or {}
            _sm = _sess.get("model") or {}
            _pick = (_sm.get("id") if isinstance(_sm, dict)
                     else str(_sm or "")) or ""
            _pick = _pick.strip()
            if _pick and _pick not in ("", "membrane"):
                _turn_pick = _pick
        except Exception:
            _turn_pick = self.model
        a_info = {"id": aid, "sessionID": sid, "role": "assistant",
                  "parentID": user_info["id"], "modelID": _turn_pick,
                  "providerID": "membrane",
                  # THE LANE'S ECHO (the TUI's chip reads the reply's
                  # agent): the turn's lane, not a hardcoded build.
                  "mode": ("plan" if str((user_info or {}).get("agent") or "")
                           == "plan" else "build"),
                  "agent": str((user_info or {}).get("agent")
                               or (self._agents or {}).get(sid) or "build"),
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
            self._turn_activity[sid] = time.time()   # THE ROUND'S TOUCH
            if rounds > 1:
                # THE OPERATOR SPEAKS INTO THE RUNNING TURN (2026-10-08):
                # messages queued since the last round join this turn's
                # history -- the model's next step sees them (a 9-minute
                # turn kept four of them waiting, the hint it needed among
                # them); each is consumed, never replayed as its own turn
                # BABBLE, EVERY MOVE: membrane's line on his last move lands first
                _bth = (getattr(self, "_babble_threads", None) or {}).pop(sid, None)
                if _bth is not None:
                    _bth.join(5)
                _said = self._take_interjections(sid, aid)
                if _said:
                    # THE REPLY SPLITS AROUND THE OPERATOR'S WORDS: the
                    # clients order rows by creation -- a merged message
                    # rendered UNDER a reply still growing above it (it
                    # looked undelivered). The running reply closes here;
                    # a new one continues after the operator's rows.
                    _now = int(time.time() * 1000)
                    a_info["time"]["completed"] = _now
                    self.messages.append(sid, a_info, list(parts))
                    self.hub.publish({"type": "message.updated",
                                      "sessionID": sid, "info": dict(a_info)})
                    for _t, _ui in _said:
                        history.append({"role": "user", "content": _t})
                    _last = _said[-1][1] or {}
                    _after = int(((_last.get("time") or {}).get("created"))
                                 or 0) + 1
                    aid = _gen_id("msg")
                    a_info = dict(a_info, id=aid,
                                  parentID=_last.get("id") or a_info.get("parentID"),
                                  tokens={"total": 0, "input": 0, "output": 0,
                                          "reasoning": 0,
                                          "cache": {"read": 0, "write": 0}},
                                  time={"created": max(_now, _after)})
                    a_info.pop("error", None)
                    a_info.pop("finish", None)
                    self.hub.publish({"type": "message.updated",
                                      "sessionID": sid, "info": dict(a_info)})
                    parts = []
            sid_flag = sid in self._aborting
            if sid in self._aborting or sid_flag:
                break
            # THE SESSION'S MODEL IS THE TURN'S (the operator's pick
            # on the session holds over the boot default -- the TUI
            # pins its session's model; the dict shape's id carries)
            turn_model = self.model
            try:
                _sess = self.sessions.sessions.get(sid) or {}
                _sm = _sess.get("model") or {}
                _pick = (_sm.get("id") if isinstance(_sm, dict)
                         else str(_sm or "")) or ""
                _pick = _pick.strip()
                if _pick and _pick not in ("", "membrane"):
                    turn_model = _pick
            except Exception:
                pass
            # THE ACTIVE MODELS (membrane/active_models.py): the role's entity
            # decides -- the seat can switch its own models, per person too
            try:
                from . import active_models as _AM
                _am = _AM.for_turn(getattr(self, "srv", None), text,
                                   _lane_of((self._agents or {}).get(sid)))
                if _am:
                    turn_model = _am
            except Exception:
                pass
            # MEMBRANE PREDICTS THIS MOVE (predict.py): every round Argus takes
            # is guessed before the model runs -- a virtual action, never run
            try:
                from . import predict as _predict
                _predict.step_start(self, sid, _predict.context_of(history, _aim_text),
                                    _lane_of((self._agents or {}).get(sid)), step=rounds, mid=aid)
            except Exception:
                pass
            body = {"model": turn_model,
                    "messages": ([{"role": "system", "content": self.system}]
                                 if self.system else []) + history}
            # THE LANE'S TOOLS (the division of hands): the registry
            # filtered to the session's agent lane (operator/plan/
            # listener). An EXPLICIT tools param = the constructor's own
            # surface (a custom caller's set -- the gate belongs to it;
            # tests + the boot's explicit tool lists keep passthrough).
            if self.tools is not None:
                tools = _openai_tools(self.tools)
            else:
                tools = _openai_tools_for((self._agents or {}).get(sid))
            lane = _lane_of((self._agents or {}).get(sid))
            if tools:
                body["tools"] = tools
            mkpart({"type": "step-start"})
            live = {"text": None, "reasoning": None}
            # THE DELTAS, BATCHED (2026-10-11, the operator: "the whole chat dynamics
            # is broken ... idle, offline, flaaaaky"): one event per token was ~100
            # a second -- HA's relay on a memory-starved agdroid fell behind and the
            # hub hung up on it (offline / idle / offline). A part's text now goes
            # out at most every DELTA_COALESCE_S, in order; the rest at the part's end.
            _pend: dict = {}

            def _flush(pid, _pend=_pend):
                e = _pend.get(pid)
                if e and e[0]:
                    self.hub.publish({"type": "message.part.delta", "sessionID": sid, "messageID": aid,
                                      "partID": pid, "field": "text", "delta": e[0]})
                    e[0] = ""
                if e:
                    e[1] = time.time()

            def _on_event(kind, val, _live=live, _pend=_pend):
                if kind == "tick":
                    return sid in self._aborting
                if kind == "ext":
                    self._inject_card(sid, val, rounds, mkpart)
                    return False
                p = _live.get(kind)
                if p is None:
                    p = _live[kind] = mkpart({"type": kind, "text": "",
                                              "time": {"start": int(time.time() * 1000)}})
                p["text"] += val
                for other in [k for k in _pend if k != p["id"] and _pend[k][0]]:
                    _flush(other)                        # a new part: the last one's words first
                e = _pend.setdefault(p["id"], ["", 0.0])
                e[0] += val
                if time.time() - e[1] >= DELTA_COALESCE_S:
                    _flush(p["id"])
                return False
            _stream = getattr(self.call_ladder, "stream", None) if STREAM_ROUNDS else None
            try:
                if _stream is not None:
                    try:
                        resp = _stream(body, _on_event)
                        for _pid in list(_pend):
                            _flush(_pid)                 # the tail of every part, before it closes
                    except Exception:
                        for _pid in list(_pend):
                            _flush(_pid)
                        if live["text"] is not None or live["reasoning"] is not None:
                            raise                    # words already shown: the round failed for real
                        resp = self.call_ladder(body)   # nothing shown yet: the buffered call, as before
                else:
                    resp = self.call_ladder(body)
                if not isinstance(resp, dict):
                    raise RuntimeError(
                        f"call_ladder returned {type(resp).__name__}, "
                        f"not a response object")
                if not isinstance(resp, dict):
                    raise RuntimeError(
                        f"call_ladder returned {type(resp).__name__}, "
                        f"not a response object")
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
            # THE INJECTION AS A CARD (2026-10-08, the operator: show it
            # like every tool call): what membrane added to this round --
            # the first round's, and any later round's that differs
            if isinstance(resp.get("membrane"), dict):
                self._inject_card(sid, resp["membrane"], rounds, mkpart)
            tcs = msg.get("tool_calls")
            content = (msg.get("content") or "").strip()
            reason = "tool-calls" if tcs else "stop"
            try:
                from . import predict as _predict
                _predict.step_end(self, sid, list(tcs or []), content, step=rounds, mid=aid)
            except Exception:
                pass
            try:
                from . import voice as _voice
                _voice.babble_move(self, sid, list(tcs or []), content)
            except Exception:
                pass
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
                rpart = live["reasoning"]
                if rpart is None:                    # buffered: the whole text at once
                    rpart = mkpart({"type": "reasoning", "text": "",
                                    "time": {"start": now}})
                    rpart["text"] = rtext
                    self.hub.publish({"type": "message.part.delta",
                                      "sessionID": sid, "messageID": aid,
                                      "partID": rpart["id"], "field": "text",
                                      "delta": rtext})
                else:
                    rpart["text"] = rtext
                rpart["time"]["end"] = int(time.time() * 1000)
                self.hub.publish({"type": "message.part.updated",
                                  "part": dict(rpart),
                                  "time": int(time.time() * 1000),
                                  "sessionID": sid})
            if tcs and content:
                # THE LEAD-IN (2026-10-08): text the model writes beside its
                # tool calls ("checking the BMS...") is a REAL text part,
                # streamed before the tools run -- it lived only in the
                # model's history, so a phone caller heard silence through
                # the whole tool chain
                now = int(time.time() * 1000)
                lpart = live["text"]
                if lpart is None:
                    lpart = mkpart({"type": "text", "text": "",
                                    "time": {"start": now}})
                    lpart["text"] = content
                    self.hub.publish({"type": "message.part.delta",
                                      "sessionID": sid, "messageID": aid,
                                      "partID": lpart["id"], "field": "text",
                                      "delta": content})
                else:
                    lpart["text"] = content
                lpart["time"]["end"] = int(time.time() * 1000)
                self.hub.publish({"type": "message.part.updated",
                                  "part": dict(lpart),
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
                    disp_args = args
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
                    self._tool_watchdog_start(sid, name, call_id, args)
                    try:
                        _wd = threading.Thread(
                            target=self._tool_watchdog_dump,
                            args=(call_id,), daemon=True)
                        _wd.start()
                        # THE LANE GATE ON THE CALL (the division of
                        # hands): a cross-lane call never reaches the
                        # impls -- the honest refusal text IS the tool
                        # result, audited. An explicit tools param =
                        # the constructor's own surface (passthrough).
                        # THE TOOL FAMILIES: a family call resolves to the
                        # existing tool first, so the gate and the impls see
                        # the names they always did (the part keeps the
                        # family's name and input -- what the model sent)
                        _help = None
                        _bad = None
                        if self.tools is None:
                            from . import toolset as _ts
                            if name == "help":
                                _help = _ts.help_text(str(args.get("topic") or ""))
                            else:
                                try:
                                    name_l, args = _ts.resolve(name, args)
                                except ValueError as e:
                                    _bad, name_l = str(e), name
                        else:
                            name_l = name
                        if _help is not None or _bad is not None:
                            _refusal = None
                        else:
                            _refusal = (None if self.tools is not None
                                        else _lane_refusal(name_l, lane))
                        if _help is not None:
                            output = _help
                        elif _bad is not None:
                            output = f"Error: {_bad}"
                        elif _refusal is not None:
                            output = f"Refused: {_refusal}"
                            try:
                                from . import seat_panel as _SP
                                _SP.note_from(self, "refused", tool=name_l, lane=lane)
                            except Exception:
                                pass
                            try:
                                mem = getattr(self, "_audit_mem", None)
                                if mem is not None:
                                    from .opencode import _write_audit
                                    _write_audit(mem, {
                                        "event": "lane-refused",
                                        "origin": "serve",
                                        "lane": lane, "tool": name_l,
                                        "sid": sid})
                            except Exception:
                                pass
                        else:
                            # the watch hands carry their planter: the
                            # owner the attention inventory shows
                            args = stamp_watch_owner(
                                name_l, args, (self._agents or {}).get(sid))
                            # THE FIELD'S PULSE: which mind touched which
                            # entity (ids + the tool name only)
                            try:
                                from .fieldpulse import of as _pulse_of
                                _fp = _pulse_of(getattr(self, "srv", None))
                                if _fp is not None:
                                    _fp.touch((self._agents or {}).get(sid), name_l, args)
                            except Exception:
                                pass
                            output = self.executor(name_l, args, call_id)
                        # THE TOOL'S TOUCH (the activity clock): a
                        # completed tool = the turn moved
                        self._turn_activity[sid] = time.time()
                        part["state"] = {"status": "completed",
                                         "input": disp_args,
                                         "output": str(output or ""),
                                         "title": _tool_title(name, disp_args),
                                         "time": {"start": tstart,
                                                  "end": int(
                                                      time.time() * 1000)}}
                    except Exception as e:
                        part["state"] = {"status": "error", "input": disp_args,
                                         "error": (f"{type(e).__name__}:"
                                                   f" {e}")[:300],
                                         "title": _tool_title(name, disp_args),
                                         "time": {"start": tstart,
                                                  "end": int(
                                                      time.time() * 1000)}}
                    finally:
                        # disarmed on EVERY exit: an erroring call left its
                        # timer armed and dumped ALL THREADS 45s later (the
                        # ThinkPhone's ~370 false 'STILL PENDING' dumps,
                        # 2026-10-08 -- every one a refused arg, not a stall)
                        self._tool_watchdog_end(call_id, name)
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
                part = live["text"]
                if part is None:
                    part = mkpart({"type": "text", "text": "",
                                   "time": {"start": now}})
                    part["text"] = content
                    self.hub.publish({"type": "message.part.delta",
                                      "sessionID": sid, "messageID": aid,
                                      "partID": part["id"], "field": "text",
                                      "delta": content})
                else:
                    part["text"] = content
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
        # THE MID-TURN INJECTS SURVIVE THE FINAL SNAPSHOT (the 'the error
        # disappears' exhibit, 2026-10-07: the watchdog's abort part rode
        # append_part MID-TURN; the turn's final append (an UPSERT by id)
        # replaced the row's parts with THIS thread's list -- the error
        # VANISHED on the next render). The merge: the stored row's parts
        # that this thread never made (ids missing) ride the final
        # snapshot after ours -- the watchdog's message STAYS.
        try:
            _stored = self.messages.get_msg(sid, aid)
            if _stored is not None:
                _have = {p.get("id") for p in parts if isinstance(p, dict)}
                _extra = [p for p in (_stored.get("parts") or [])
                          if isinstance(p, dict)
                          and p.get("id") not in _have]
                parts.extend(_extra)
        except Exception:
            pass
        self.messages.append(sid, a_info, parts)
        self.hub.publish({"type": "message.updated", "sessionID": sid,
                          "info": dict(a_info)})
        self._busy.pop(sid, None)
        self._aborting.discard(sid)
        self.sessions.touch(sid)
        # THE TURN'S HONEST CLOCK (the latency's own ledger): the
        # phases' cost prints at the turn's end -- the 25s-turn
        # mystery's own evidence (the model's time vs the lanes')
        try:
            _el = time.time() - _t_turn
            print(f"[serve] turn {sid[:16]} done: {_el:.1f}s "
                  f"(model {turn_model[:24]})", file=sys.stderr)
        except Exception:
            pass
        # THE COSMETIC-BUSY COVENANT (the operator's design, 2026-10-03):
        # the operator's MAIN session never shows 'idle' to the client —
        # the thinking loop's END is membrane's internal fact, not a
        # reported one. The machinery IS free (the guard released, the
        # turn closed); the STREAM stays cosmetically busy so:
        # (1) a new operator message either queues (loop running) or
        # opens a fresh thinking loop (free) — the client sees one
        # continuous session either way;
        # (2) the EVENT-THINKERS (the 3-loop pool) may inject their
        # lines INTO THIS SESSION at any moment — riding the same
        # stream, rendering to the client as part of the 'thinking
        # loop' regardless of whether the operator's own turn is live.
        # The client's 'busy' is cosmetic; the seat's mind is real.
        if not self._cosmetic_busy(sid):
            self.hub.publish({"type": "session.status",
                              "status": {"type": "idle"},
                              "sessionID": sid})
            self.hub.publish({"type": "session.idle", "sessionID": sid})
        self._touch_session(sid)
        # the voice's teachers: the operator's words and the LLM's reply
        try:
            from . import voice as _voice
            _lane_end = _lane_of((self._agents or {}).get(sid))
            _voice.on_turn_end(self, sid, text,
                               "\n".join(p.get("text") or "" for p in parts
                                         if isinstance(p, dict) and p.get("type") == "text"),
                               _lane_end,
                               voice_turn=bool((user_info or {}).get("voiceTurn")),
                               parts=parts, auto_turn=bool((user_info or {}).get("autoTurn")))
            if _lane_end not in _voice._CHAT_LANES:      # the chat's guess rides its voice line
                from . import predict as _predict
                threading.Thread(target=_predict.end, args=(self, sid, list(parts)),
                                 daemon=True, name="predict-end").start()
        except Exception:
            pass
        return {"info": a_info, "parts": parts}


class _NL:
    def __enter__(self):
        return None


def __exit__(self, *a):
        return False


# ---- the loopback ladder + the in-process executor -----------------------------

def loopback_ladder(port: int, runner=None, cell=None):
    """The turn loop's model call: a buffered POST to THIS seat's /v1 --
    the gate, injection, routing and capture machinery runs once, in
    process, exactly where every other client's turn runs.

    THE ABORT'S TEETH (the wedged round's fast kill): the loopback
    turn's runner hands its sid through the body (`_turn_sid`) -- the
    proxy's `_v1_chat` registers its in-flight backend response under
    it (runner._turn_sockets[sid]); the watchdog's abort CLOSES that
    socket => the wedged read raises NOW, not after the rung's budget.
    `cell` = the late-bound runner (the boot wires it after TurnRunner
    exists -- the ladder's construction precedes the runner)."""
    import json as _json

    def call(body: dict) -> dict:
        sid = None
        _r = runner if runner is not None else (
            (cell or {}).get("runner") if cell else None)
        if _r is not None:
            sid = getattr(_r, "_current_turn_sid", None)
            if sid:
                body = dict(body, _turn_sid=sid)
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        data = _json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT_S) as resp:
            return _json.loads(resp.read())

    def stream(body: dict, on_event) -> dict:
        """THE STREAMED ROUND (2026-10-10: the buffered call showed nothing
        until the model had FINISHED -- 20-30 s of silence a round): the same
        /v1 with stream=true; on_event('text'|'reasoning', delta) as the words
        come, on_event('ext', {...}) for membrane's injection card (the proxy
        sends it first when asked: membrane_ext), on_event('tick') between
        chunks (True = stop: an abort). Returns the buffered call's shape."""
        sid = None
        _r = runner if runner is not None else (
            (cell or {}).get("runner") if cell else None)
        if _r is not None:
            sid = getattr(_r, "_current_turn_sid", None)
        body = dict(body, stream=True, membrane_ext=True, **({"_turn_sid": sid} if sid else {}))
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions", data=_json.dumps(body).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        content, reasoning, tcs = [], [], {}
        usage, ext, finish = None, None, None
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT_S) as resp:
            for raw in resp:
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                try:
                    d = _json.loads(payload)
                except ValueError:
                    continue
                if isinstance(d.get("membrane"), dict) and not d.get("choices"):
                    ext = d["membrane"]
                    on_event("ext", ext)
                    continue
                if d.get("usage"):
                    usage = d["usage"]
                for ch in d.get("choices") or []:
                    delta = ch.get("delta") or ch.get("message") or {}
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                    if delta.get("content"):
                        content.append(delta["content"])
                        on_event("text", delta["content"])
                    r = delta.get("reasoning") or delta.get("reasoning_content") or delta.get("thinking")
                    if r:
                        reasoning.append(r)
                        on_event("reasoning", r)
                    for tc in delta.get("tool_calls") or []:
                        i = tc.get("index", len(tcs))
                        cur = tcs.setdefault(i, {"id": "", "type": "function",
                                                 "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            cur["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name") and not cur["function"]["name"].endswith(fn["name"]):
                            cur["function"]["name"] += fn["name"]
                        a = fn.get("arguments")
                        if a:
                            cur["function"]["arguments"] += a if isinstance(a, str) else _json.dumps(a)
                if on_event("tick", None):
                    break
        msg = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            msg["reasoning"] = "".join(reasoning)
        if tcs:
            msg["tool_calls"] = [tcs[i] for i in sorted(tcs)]
        out = {"choices": [{"message": msg, "finish_reason": finish or ("tool_calls" if tcs else "stop")}],
               "usage": usage or {}}
        if ext is not None:
            out["membrane"] = ext
        return out
    call.stream = stream
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

# THE FIELD'S WINDOW (2026-10-08): the seat's READ surfaces the serve relays
# onto the /v1 proxy -- that proxy binds 127.0.0.1 with no auth and has
# write routes; the basic-authed serve passes exactly these, GET only (the
# HA field dashboard + its sensors: state, the universe map, the drawer).
# the serve's rounds stream from the /v1 proxy (words show as they come);
# MEMBRANE_STREAM_ROUNDS=0 returns to the buffered call
STREAM_ROUNDS = os.environ.get("MEMBRANE_STREAM_ROUNDS", "1") != "0"


def _from_person(text, parts=None) -> bool:
    """Does this user message come from a PERSON -- the HA proxy's / calld's
    '[HA user: <name>]' speaker line, in the text or a part? (A loop's goal
    pulse, the auto-chat's own lines and membrane's voice carry none.)"""
    from .speakers import split_speaker
    if split_speaker(str(text or ""))[0]:
        return True
    for p in parts or []:
        if isinstance(p, dict) and p.get("type") == "text" and split_speaker(str(p.get("text") or ""))[0]:
            return True
    return False


SEAT_READ_PATHS = ("/membrane/state", "/membrane/map/cloud",
                   "/membrane/map/entities", "/membrane/map/axes",
                   "/membrane/map/axis", "/membrane/map/field",
                   "/membrane/field/entity", "/membrane/map/distance",
                   "/membrane/map/field3d", "/membrane/map/tile",
                   "/membrane/map/locate", "/v1/models",
                   "/membrane/entity/read",
                   "/membrane/entity-related",
                   # the login page's strangers (membrane/strangers.py):
                   # GET, numbers in, shape out
                   "/membrane/stranger/visit", "/membrane/stranger/view",
                   "/membrane/stranger/event", "/membrane/stranger/leave",
                   "/membrane/gate-stages", "/membrane/predict", "/membrane/seat")


def seat_read_url(srv, path_qs: str) -> str | None:
    """The local proxy URL for one allow-listed read (query kept), or None
    (not allowed / no seat port known)."""
    path = str(path_qs or "").split("?", 1)[0]
    if path not in SEAT_READ_PATHS:
        return None
    addr = getattr(srv, "server_address", None)
    try:
        port = int(addr[1])
    except Exception:
        return None
    return f"http://127.0.0.1:{port}{path_qs}"


def make_handler(hub=None, sessions=None, messages=None, user="opencode",
                 password="", config=None, runner=None, srv=None, **_dep) -> type:
    """The opencode-compatible HTTP handler, parametrized by the running
    seat's stores/runner (a factory because ThreadingHTTPServer wants a
    class). `srv` is the seat itself -- /agent reads the live minds
    (loop pool, thinker, dreamer) off it."""
    hub = hub if hub is not None else SSEHub()
    config = config if config is not None else {}

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        seat = srv          # the seat object for the live-minds reads

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

        def _seat_read(self) -> None:
            """GET relay of one SEAT_READ_PATHS read onto the local /v1
            proxy (the field dashboard's window; see seat_read_url)."""
            url = seat_read_url(srv, self.path)
            if url is None:
                return self._notfound()
            try:
                with urllib.request.urlopen(url, timeout=45) as up:
                    body, status = up.read(), up.status
                    ctype = up.headers.get("Content-Type", "application/json")
            except urllib.error.HTTPError as e:
                body, status = e.read() or b"", e.code
                ctype = e.headers.get("Content-Type", "application/json")
            except Exception as e:                  # the seat is down/booting
                return self._json_err(502, f"seat unreachable: {e!r}"[:200])
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
                if getattr(q, "lost", False):
                    return                       # it fell behind: hang up, the client resyncs
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
            if path in SEAT_READ_PATHS:
                return self._seat_read()
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
                # THE LIVE SHIELD (the TUI's actual replay route -- the
                # crash's data-half at the reader): an assistant row
                # without tokens is mended ON THE WIRE (the reducer's
                # findLast never lands on undefined)
                _rows = _chronological(messages.list(sid))
                for _r in _rows:
                    _i = _r.get("info") or {}
                    if _i.get("role") == "assistant" and \
                            not isinstance(_i.get("tokens"), dict):
                        _i["tokens"] = {"total": 0, "input": 0,
                                        "output": 0, "reasoning": 0,
                                        "cache": {"read": 0, "write": 0}}
                self._json_out(_rows)
            elif path == "/session/status":
                # the truth per session: the runner's live busy state +
                # the QUEUE's depth (a queued message is not invisible:
                # the TUI/clients see {busy, queued, depth})
                out = {}
                for s in sessions.sessions:
                    st = runner.status(s) if runner is not None \
                        else {"type": "idle"}
                    if isinstance(st, dict) and st.get("type") == "busy":
                        d = runner._queued_depth(s)
                        if d:
                            st = dict(st, queued=True, depth=d)
                    out[s] = st
                self._json_out(out)
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
                    if segs[1] == "auto":
                        from . import voice as _voice
                        return self._json_out(_voice.auto_state(runner, sid) if runner else {"on": False})
                    if segs[1] == "predict":
                        # membrane's phantoms: its guesses of Argus's moves (predict.py)
                        from . import predict as _predict
                        return self._json_out(_predict.phantoms(runner, sid) if runner else [])
                    if segs[1] == "voice":
                        # membrane's own voice: this session's lines
                        from . import voice as _voice
                        _v = _voice.of(srv or getattr(runner, "srv", None))
                        return self._json_out(_v.of_session(sid) if _v else [])
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
                        _rows = _chronological(messages.list(sid))
                        # THE LIVE SHIELD (the crash's data-half, also at
                        # the reader): any assistant row without tokens
                        # gets the mend ON THE WIRE (a persisting flaw
                        # never re-poisons the reducer)
                        for _r in _rows:
                            _i = _r.get("info") or {}
                            if _i.get("role") == "assistant" and \
                                    not isinstance(_i.get("tokens"), dict):
                                _i["tokens"] = {"total": 0, "input": 0,
                                                "output": 0, "reasoning": 0,
                                                "cache": {"read": 0,
                                                          "write": 0}}
                        return self._json_out(_rows)
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
                # THE WATCH LOOPS (the reactive layer): the loops are
                # real child sessions (agent 'watch', title = the
                # event's entity) the TUI renders as background
                # subagents (their tool rounds stream via the parts
                # events like any session).
                self._json_out({"backgroundSubagents": True})
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
            elif path == "/membrane/models":
                from . import minds_models as _MM
                psrv = getattr(runner, "srv", None) if runner is not None else None
                self._json_out(_MM.read(psrv) if psrv is not None else {})
            elif path == "/membrane/seat":
                # the seat as a panel (seat_panel.py): state, events, the controls
                from . import seat_panel as _SP
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    self._json_out({})
                else:
                    _ent = _SP._entity(psrv)
                    self._json_out({"state": _SP.state(psrv), "events": list(((_ent.properties or {}) if _ent else {}).get("events") or []),
                                    "controls": list(_SP.CONTROLS)})
            elif path == "/agent":
                self._json_out(_agent_shapes(getattr(self, "seat", None)))
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
            if path == "/membrane/seat/control":
                # the operator's switches (HA admins only, through the relay)
                from . import seat_panel as _SP
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
                return self._json_out(_SP.control(psrv, self._read_body() or {}, by="operator"))
            if path == "/membrane/models":
                # THE MINDS' MODELS: the watch loops' and the background
                # cortex's models, live (the chat picker sets one session)
                from . import minds_models as _MM
                b = self._read_body()
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
                try:
                    from .lanes import _chat_capable_models
                    known = _chat_capable_models(getattr(psrv, "backend", ""))
                except Exception:
                    known = None
                try:
                    out = _MM.apply(psrv, loops=b.get("loops"), cortex=b.get("cortex"),
                                    known=known or None)
                except ValueError as e:
                    body = json.dumps({"error": str(e)}).encode()
                    self.send_response(400); self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                    return
                return self._json_out(out)
            if path == "/session":
                b = self._read_body()
                s = sessions.create(title=b.get("title"),
                                    parent_id=b.get("parentID"))
                # THE REQUESTER'S PICK WINS (the 'qwen3:8b by hash
                # order' exhibit): the POST body's model (the TUI's
                # {providerID, modelID}) IS the session's model — the
                # config's default lands only when the body omits it
                # (and the default = the BOOT's model, never the
                # providers' dict's arbitrary first key).
                _bm = b.get("model") if isinstance(b.get("model"),
                                                   dict) else None
                if _bm and (_bm.get("modelID") or _bm.get("id")):
                    s["model"] = {"id": _bm.get("modelID") or _bm.get("id"),
                                  "providerID": _bm.get("providerID")
                                                or "membrane",
                                  "variant": "default"}
                else:
                    # THE BOOT'S MODEL IS THE DEFAULT (never the
                    # providers' dict's arbitrary first key — a dict
                    # without insertion-order intent made 'qwen3:8b'
                    # the default by hash luck)
                    boot_model = str(getattr(runner, "model", "") or "")
                    s["model"] = {"id": boot_model or "membrane",
                                  "providerID": "membrane",
                                  "variant": "default"}
                self._json_out(s)
                return
            if path.startswith("/session/"):
                rest = path[len("/session/"):]
                segs = rest.split("/")
                sid = segs[0]
                if segments_len(segs) == 2:
                    action = segs[1]
                    if action == "auto":
                        # auto-chat: {on: bool} -- the voice and the model talk
                        from . import voice as _voice
                        if runner is None or sessions.get(sid) is None:
                            return self._notfound()
                        b = self._read_body()
                        # membrane's role (babble | predict) is its own switch: a
                        # body with only {role} leaves auto-chat as it is
                        if b.get("role"):
                            _voice.role_set(runner, sid, str(b.get("role")))
                        if "on" not in b:
                            return self._json_out(_voice.auto_state(runner, sid))
                        return self._json_out(_voice.auto_set(runner, sid, bool(b.get("on")),
                                                              str(b.get("mode") or "explore")))
                    if action == "voice":
                        # the teaching: {id, vote: up|down, rewrite}
                        from . import voice as _voice
                        _v = _voice.of(srv or getattr(runner, "srv", None))
                        b = self._read_body()
                        rec = _v.lines.get(str(b.get("id") or "")) if _v else None
                        if rec is None or rec.get("sid") != sid:
                            return self._notfound()
                        return self._json_out(_v.feedback(rec["id"], b.get("vote"), b.get("rewrite")))
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
            # THE MESSAGE'S PICK STICKS (the chooser's last mile, the
            # operator's 'the chooser is ignored' exhibit): the POST's
            # body carries the TUI's CURRENT model; the message lane
            # drops it today. It lands on the SESSION ROW (the turn
            # builder's read) — the same store the PATCH lane writes;
            # the pick lives past this turn (a per-message pick = the
            # session's new state).
            _bm = b.get("model")
            if isinstance(_bm, dict) and (_bm.get("modelID")
                                          or _bm.get("id")):
                _pick = {"id": _bm.get("modelID") or _bm.get("id"),
                         "providerID": _bm.get("providerID") or "membrane",
                         "variant": "default"}
                _row = sessions.sessions.get(sid)
                if _row is not None:
                    _row["model"] = _pick
                    sessions._flush()
                # THE PICKER IS A CONTROL OF THE ACTIVE MODELS: a person's
                # pick becomes their own active-model:chat:<person>
                try:
                    from . import active_models as _AM
                    from .speakers import split_speaker as _sps
                    _who = next((_sps(str(p.get("text") or ""))[0] for p in (b.get("parts") or [])
                                 if isinstance(p, dict) and _sps(str(p.get("text") or ""))[0]), None)
                    _psrv = getattr(runner, "srv", None) if runner is not None else None
                    if _who and _psrv is not None and _pick["id"] not in ("", "membrane"):
                        _AM.mirror(_psrv, "chat", _pick["id"], person=_who)
                except Exception:
                    pass
            # THE AGENT'S PICK STICKS (the mode toggle's last mile): the
            # message body's agent (the TUI's build/plan selection) lands
            # on the SESSION ROW -- the same store the PATCH lane writes;
            # the lane reads it even on turns that carry no agent row.
            _ma = str(b.get("agent") or "").strip()
            if _ma in ("build", "plan"):
                _row = (sessions.sessions or {}).get(sid)
                if _row is not None and _row.get("agent") != _ma:
                    _row["agent"] = _ma
                    sessions.flush()   # the public form (the lock's owned)
            if async_:
                # the row's parts ride along (the sync lane below does the
                # same): the door's announcement carries the text
                threading.Thread(target=runner.run,
                                 args=(sid, text, user_info, row["parts"]),
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
            # the chat's "membrane only" switch: the voice answers alone
            if body.get("membrane_only") is True:
                info["membraneOnly"] = True
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
                    b = self._read_body()
                    s = sessions.patch(segs[0], b.get("title"),
                                       agent=b.get("agent"))
                    if s is None:
                        return self._notfound()
                    # THE MODEL'S PATCH (the model switch through this
                    # surface): the session's model = the body's dict
                    # (the TUI's pick: {modelID, providerID}) — the
                    # turn's lane reads it (the session's model
                    # governs its turns). The store's _lock is ALREADY
                    # held here: call _flush (the inner form) — a
                    # flush() = a double-lock deadlock (the PATCH
                    # never returned!).
                    m = b.get("model")
                    if isinstance(m, dict) and (m.get("modelID")
                                                or m.get("id")):
                        row = sessions.sessions.get(segs[0])
                        if row is not None:
                            row["model"] = {
                                "id": m.get("modelID") or m.get("id"),
                                "providerID": m.get("providerID")
                                              or "membrane",
                                "variant": "default"}
                            sessions._flush()
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
    daemon -- the seat's main loop owns the seat's life).

    THE PORT RACE'S TOCTOU (the two seats' simultaneous boots, 2026-10-06:
    the pick found the port free, the sibling's bind landed first, the
    serve FAILED Errno 48 => proxy-only => NO TUI -- 'membrane seat not
    starting'). The boot's prebound socket (runner._serve_socket -- the
    pick reserved it) is BOUND here instead of rebinding: a pick-era race
    resolves at PICK (the walk retries), never post-pick."""
    handler = make_handler(hub=runner.hub, sessions=sessions,
                           messages=messages, user=user, password=password,
                           config=config, runner=runner,
                           srv=getattr(runner, "srv", None))
    held = getattr(runner, "_serve_socket", None)
    httpd = ThreadingHTTPServer((host, port), handler,
                                bind_and_activate=False)
    if held is not None and held.getsockname()[1] == port:
        httpd.socket = held             # the pick's reservation becomes
        httpd.server_activate()         # the server's listening socket
    else:                                # no reservation: bind as before
        httpd.server_bind()
        httpd.server_activate()
    if held is not None and getattr(httpd, "socket") is not held:
        try:
            held.close()                # an unused reservation: release
        except Exception:
            pass
    runner._serve_socket = None
    th = threading.Thread(target=httpd.serve_forever, daemon=True,
                          name="membrane-serve")
    th.start()
    return httpd, th
    th = threading.Thread(target=httpd.serve_forever, daemon=True,
                          name="membrane-serve")
    th.start()
    return httpd, th