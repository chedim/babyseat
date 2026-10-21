"""SESSION ABSORPTION: the seat's boot looks at EXTERNAL agents'
session stores -- Claude Code (~/.claude/projects/<slug>/*.jsonl) and
OpenCode (~/.local/share/opencode/opencode.db) -- for sessions that ran
since the seat's last run, imports them (chat rows into the serve
stores, events into the entity field) and auto-continues the newest.

WHO ANSWERED DOES NOT GATE (issue #396): a session answered by an
Anthropic model is imported and learned like any other -- the operator
reversed the older "don't remix Claude's answers" rule on 2026-10-21.
Provenance stays: the session doc and every assistant row keep the model
id and provider that answered (`anthropic` for claude-*/anthropic model
ids, `ollama` for local ones, `unknown` when a Claude Code line names no
model), and a claude-agent row keeps its `madeBy` mark. OpenCode sessions
from EVERY provider are eligible (issue #404), each assistant row keeping
the provider that answered it (`unknown` when the message names none),
except SEAT_PROVIDER: `membrane` is the provider the seat writes into the
OpenCode config it launches, so such a session is the seat's own traffic
and absorbing it would feed the seat its own turns back.

Claude Code's transcript folder is the cwd with every character that is
not a letter or a digit replaced by '-' (issue #401); the older form
(only '/' replaced) is still read, so nothing that ever matched stops
matching.

Sessions an older rule skipped (the Anthropic filter of #396, the
provider allowlist of #404, the folder name of #401) are rescanned ONCE
past the watermark (marker version 3; see absorb()).

Conventions: opencode session/message ids are kept verbatim; claude ids
are namespaced `xcl_` (their uuids are unique per line). Import marks
the session doc with an `absorbed: {source, sid}` provenance key and
uses a watermark marker (serve-absorb.json beside serve-sessions.json:
{last_run_ts, sessions}) so nothing is imported twice and a session
that GREW while the seat was up is topped up (MessageStore rows upsert
by id, so re-import is safe by construction).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .substrate import _is_lab_field_path
from .streams import Event, clean, discourse_clean, split_turn

ABSORB_MAX_SESSIONS = 5            # first boot after a gap: newest 5 only
ABSORB_MAX_ROWS = 200              # per-session safety cap
TEXT_CAP = 8000                    # per-row text cap (the chat rows)
CLAUDE_NS = "xcl_"                 # claude ids get a namespace
WATCH_FIELD_SESSION_PREFIX = "x:"  # field sessions: x:claude:<sid>, ...


def _h16(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _xprt(mid: str, i: int) -> str:
    # deterministic part ids: a top-up re-import is byte-stable
    return "xprt_" + _h16(f"{mid}:{i}")


# ---- shapes ----------------------------------------------------------------

@dataclass
class ToolCall:
    call_id: str
    name: str
    args: str                 # raw JSON-ish text (state.input as written)
    output: Optional[str] = None   # None = call-only (no result seen)


@dataclass
class TurnRow:
    msg_id: str
    role: str                 # user | assistant
    ts_ms: int
    text: str = ""
    thought: str = ""
    calls: list = field(default_factory=list)   # list[ToolCall]
    model_id: str = ""
    provider_id: str = ""     # who answered this row (provenance)
    made_by: str = ""         # the claude-agent mark, carried verbatim


@dataclass
class AbsorbSession:
    source: str               # claude | opencode
    sid: str
    title: str = ""
    created_ms: int = 0
    updated_ms: int = 0
    model_id: str = ""
    provider_id: str = ""
    rows: list = field(default_factory=list)    # list[TurnRow], in order
    old_rule_skipped: bool = False   # the pre-#396 Anthropic filter
                                     # would have dropped it (the rescan)


# ---- provenance (no longer a filter, issue #396) -------------------------------

SEAT_PROVIDER = "membrane"   # the seat's own opencode provider: never absorbed
MARKER_VERSION = 3       # 2 = the #396 rescan is done; 3 = the #401/#404 one


def _is_anthropic_model(model_id: str | None) -> bool:
    """The pre-#396 fail-closed classifier: no model id, or a claude-/
    anthropic one, counts as Anthropic's. Now it only says which sessions
    the old rule skipped (the one-time rescan); it gates nothing."""
    m = str(model_id or "").strip().lower()
    if not m:
        return True
    from .claude_agent import is_rung_name
    if is_rung_name(m):
        return True                    # the claude-agent rung (#384)
    return m.startswith("claude") or "anthropic" in m


def is_claude_made_row(info: dict | None) -> bool:
    """A row the claude-agent rung made (issue #384) -- the transcript's
    Claude-made mark, the provider id, or a model id in the rung namespace
    membrane mints. Provenance only since #396: such a row is absorbed and
    keeps its mark."""
    from .claude_agent import is_claude_made, is_rung_name
    if not isinstance(info, dict):
        return False
    return is_claude_made(info) or is_rung_name(info.get("modelID"))


# ---- collector: Claude Code transcripts -------------------------------------

def _claude_provider(model: str | None) -> str:
    """Who answered a Claude Code line, read off its model id."""
    if not str(model or "").strip():
        return "unknown"
    return "anthropic" if _is_anthropic_model(model) else "ollama"


def _project_slug(cwd: str) -> str:
    """Claude Code's transcript folder name for `cwd`: every character
    that is not an ASCII letter or digit becomes '-' (issue #401)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def _old_project_slug(cwd: str) -> str:
    """The pre-#401 form (only '/' replaced), still read."""
    return str(cwd).replace("/", "-")


def _project_slugs(cwd: str) -> list[str]:
    """The folders to read: Claude Code's name first, then the old form
    when it differs."""
    new, old = _project_slug(cwd), _old_project_slug(cwd)
    return [new] if new == old else [new, old]


def _iso_s(ts: str) -> float:
    """ISO-8601 (Z) -> epoch seconds; 0.0 on anything unparseable."""
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except Exception:
        return 0.0


def _claude_ts_ms(line: dict) -> int:
    return int(_iso_s(line.get("timestamp") or "") * 1000)


def _blk_text(b: dict) -> str:
    if b.get("type") == "text":
        return str(b.get("text") or "")
    return ""


def _res_content(r) -> str:
    """A tool_result's content: string, or a list of text blocks."""
    if isinstance(r, str):
        return r
    if isinstance(r, list):
        return "\n".join(_blk_text(b) for b in r if isinstance(b, dict))
    return ""


def claude_sessions(cwd: str, since_s: float,
                    home: Path | None = None) -> list[AbsorbSession]:
    """Parse ~/.claude/projects/<slug(cwd)>/*.jsonl for sessions (any
    model, issue #396) whose last activity is newer than
    `since_s`. Sidechains, subagent dirs, compact/meta lines and noise
    line types are skipped."""
    base = home if home is not None else Path.home() / ".claude" / "projects"
    slugs = _project_slugs(cwd)
    old_slug = _old_project_slug(cwd)
    out, seen = [], set()
    for slug in slugs:
        proj = base / slug
        if not proj.is_dir():
            continue
        for fp in sorted(proj.glob("*.jsonl")):
            if fp.stem in seen:
                continue                 # one session, listed once
            try:
                a = _parse_claude_file(fp)
            except Exception:
                continue
            if a is not None and a.rows and a.updated_ms / 1000.0 > since_s:
                seen.add(fp.stem)
                if slug != old_slug:
                    # the pre-#401 code never read this folder
                    a.old_rule_skipped = True
                out.append(a)
    out.sort(key=lambda a: a.updated_ms, reverse=True)
    return out


def _parse_claude_file(fp: Path) -> AbsorbSession | None:
    rows: list[TurnRow] = []
    title = ""
    updated_s = 0.0
    pending: dict = {}                      # tool_use_id -> (name, args)
    old_rule = False                        # pre-#396 filter would drop
    try:
        lines = [json.loads(ln) for ln in
                 fp.read_text(errors="replace").splitlines() if ln.strip()]
    except Exception:
        return None
    for ln in lines:
        typ = ln.get("type")
        if typ == "ai-title":
            title = str(ln.get("title") or ln.get("content") or title)
            continue
        if typ not in ("user", "assistant"):
            continue                          # mode/system/permission-mode/
                                              # attachment/snapshot lanes
        if ln.get("isSidechain") or ln.get("isMeta") \
                or ln.get("isCompactSummary"):
            continue                          # subagent / machinery rows
        if ln.get("isApiErrorMessage"):
            continue
        msg = ln.get("message") or {}
        if typ == "assistant":
            model = msg.get("model")
            old_rule = old_rule or _is_anthropic_model(model)
            ts_ms = _claude_ts_ms(ln)
            updated_s = max(updated_s, _iso_s(ln.get("timestamp") or ""))
            text, thought, calls = [], [], []
            for b in (msg.get("content") or []):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    text.append(_blk_text(b))
                elif b.get("type") == "thinking":
                    thought.append(str(b.get("thinking") or
                                       b.get("text") or ""))
                elif b.get("type") == "tool_use":
                    cid = str(b.get("id") or "")
                    args = b.get("input")
                    calls.append(ToolCall(
                        cid, str(b.get("name") or ""),
                        args if isinstance(args, str)
                        else json.dumps(args or {})))
                    if cid:
                        pending[cid] = calls[-1]
            mid = "xcl_" + str(ln.get("uuid") or msg.get("id") or
                               _h16(f"{fp.name}:{ts_ms}"))
            rows.append(TurnRow(mid, "assistant", ts_ms,
                                "".join(text), "".join(thought), calls,
                                str(model or ""), _claude_provider(model)))
        else:                                 # user
            ts_ms = _claude_ts_ms(ln)
            updated_s = max(updated_s, _iso_s(ln.get("timestamp") or ""))
            content = msg.get("content")
            texts, results = [], []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) \
                            and b.get("type") == "tool_result":
                        cid = str(b.get("tool_use_id") or "")
                        out = _res_content(b.get("content"))
                        if cid and cid in pending:
                            pending[cid].output = out      # merge back
                        else:
                            results.append((cid, out))
            text = " ".join(t for t in (s.strip() for s in texts) if t)
            # a pure tool_result line MERGES into the call it answers --
            # it never becomes a chat row (the assistant's tool part
            # carries the output); a leftover with no pairing is dropped
            if text:
                mid = "xcl_" + str(ln.get("uuid") or _h16(f"{fp.name}:{ts_ms}"))
                rows.append(TurnRow(mid, "user", ts_ms, text))
    if not rows:
        return None
    if not any(r.role == "assistant" and r.text.strip() for r in rows):
        return None                             # nothing to continue/learn
    last = next((r for r in reversed(rows) if r.role == "assistant"), None)
    return AbsorbSession("claude", _sid_of(fp), title,
                         int(rows[0].ts_ms), int(updated_s * 1000),
                         next((r.model_id for r in reversed(rows)
                               if r.model_id), ""),
                         last.provider_id if last else "unknown", rows,
                         old_rule_skipped=old_rule)


def _sid_of(fp: Path) -> str:
    """The raw claude session id (the transcript uuid). Namespacing
    (xcl_) happens ONCE, in import_session -- here we carry the bare
    foreign id so marker keys read source:<foreign-sid>."""
    return fp.stem


# ---- collector: OpenCode (sqlite) ---------------------------------------------

def opencode_sessions(cwd: str, since_s: float,
                      db: Path | None = None) -> list[AbsorbSession]:
    """Read ~/.local/share/opencode/opencode.db READ-ONLY (a live WAL
    db; a missing/locked db degrades to zero sessions, never a raise).
    Eligible: session.directory == cwd, any provider (#404) except
    SEAT_PROVIDER, on the session or on ANY assistant message (the seat's
    own traffic). A missing provider id is recorded as `unknown`.
    Anthropic-answered sessions are absorbed like any other (#396).
    """
    path = Path(db) if db is not None else (
        Path.home() / ".local/share/opencode" / "opencode.db")
    if not path.is_file():
        return []
    target = os.path.realpath(str(cwd))
    out = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            sess_rows = conn.execute(
                "SELECT id, directory, title, model, time_created,"
                " time_updated FROM session").fetchall()
            for sid, directory, title, model_json, created, updated \
                    in sess_rows:
                if os.path.realpath(str(directory or "")) != target:
                    continue
                try:
                    sm = json.loads(model_json or "{}")
                except Exception:
                    sm = {}
                prov = str(sm.get("providerID") or "") or "unknown"
                mid = str(sm.get("modelID") or "")
                if prov == SEAT_PROVIDER:
                    continue                     # the seat's own traffic
                rows = _opencode_rows(conn, sid, prov)
                if not rows:
                    continue
                if not any(r.role == "assistant" and r.text.strip()
                           for r in rows):
                    continue
                a = AbsorbSession(
                    "opencode", str(sid), str(title or ""),
                    int(created or 0), int(updated or 0), mid, prov, rows,
                    # any non-ollama provider (or none) was dropped by
                    # the #396 filter or the #404 allowlist: rescan it
                    old_rule_skipped=(prov != "ollama"
                                      or _is_anthropic_model(mid)
                                      or any(r.role == "assistant" and (
                                          r.provider_id != "ollama"
                                          or _is_anthropic_model(r.model_id)
                                          or r.made_by)
                                          for r in rows)))
                if a.updated_ms / 1000.0 > since_s:
                    out.append(a)
        finally:
            conn.close()
    except Exception as e:
        print(f"[absorb] opencode store unread ({str(e)[:80]})",
              file=sys.stderr)
    out.sort(key=lambda a: a.updated_ms, reverse=True)
    return out


def _opencode_rows(conn, sid: str, prov: str) -> list[TurnRow]:
    msgs = conn.execute(
        "SELECT id, data FROM message WHERE session_id = ?"
        " ORDER BY time_created, id", (sid,)).fetchall()
    rows = []
    for mid, data in msgs:
        try:
            d = json.loads(data or "{}")
        except Exception:
            continue
        role = str(d.get("role") or "")
        ts_ms = int(((d.get("time") or {}).get("created")) or 0)
        if role not in ("user", "assistant"):
            continue
        if role == "assistant":
            mprov = str(d.get("providerID") or "")
            if mprov == SEAT_PROVIDER:
                return []                        # the seat's own, whole sid
        parts = conn.execute(
            "SELECT data FROM part WHERE message_id = ?"
            " ORDER BY time_created, id", (mid,)).fetchall()
        text, thought, calls = [], [], []
        for (pdata,) in parts:
            try:
                p = json.loads(pdata or "{}")
            except Exception:
                continue
            pt = p.get("type")
            if pt == "text":
                text.append(str(p.get("text") or ""))
            elif pt == "reasoning":
                thought.append(str(p.get("text") or ""))
            elif pt == "tool":
                st = p.get("state") or {}
                args = st.get("input")
                calls.append(ToolCall(
                    str(p.get("callID") or ""), str(p.get("tool") or ""),
                    args if isinstance(args, str)
                    else json.dumps(args or {}), st.get("output")))
        asst = role == "assistant"
        rows.append(TurnRow(str(mid), role, ts_ms, "".join(text),
                            "".join(thought), calls,
                            str(d.get("modelID") or "") if asst else "",
                            (str(d.get("providerID") or "") or "unknown")
                            if asst else "",
                            _made_by(d) if asst else ""))
        if len(rows) >= ABSORB_MAX_ROWS:
            break
    return rows


def _made_by(d: dict) -> str:
    from .claude_agent import PROVIDER
    return PROVIDER if is_claude_made_row(d) else ""


# ---- the importer ---------------------------------------------------------------

def import_session(sessions, messages, a: AbsorbSession, model_id: str,
                   efield=None, cwd: str | None = None) -> dict:
    """One absorbed session -> chat rows (+ field events). Fresh import
    inserts the session doc (provenance 'absorbed'); a re-encounter of
    the same id TOPS UP (only rows that are not already stored append;
    time.updated only rises). A native session's id is a hard collision
    (the store refuses). Returns {sid, n_msgs, n_events, topup}."""
    sid = a.sid if a.source == "opencode" else CLAUDE_NS + a.sid
    seat_model = model_id or "membrane"
    existing_rows = messages.list(sid)
    have = {str(r.get("info", {}).get("id") or "") for r in existing_rows}
    topup = bool([m for m in have if m])
    fresh = [r for r in a.rows
             if (r.role == "user" and r.text.strip()) or
                (r.role == "assistant" and (r.text.strip() or r.thought
                                            or r.calls))]
    fresh = [r for r in fresh if r.msg_id not in have][:ABSORB_MAX_ROWS]
    if not topup and not fresh:
        raise ValueError("empty session, nothing to import")
    doc = _session_doc(a, sid, cwd)
    sessions.import_session(doc)
    n_msgs = 0
    prev_user = str(_last_user_id(existing_rows) or "")
    n_events = 0
    for r in fresh:
        if r.role == "user":
            info, parts = _user_info_row(r, sid, seat_model)
            prev_user = r.msg_id
        else:
            info, parts = _assistant_info_row(r, a, sid, cwd, prev_user)
        messages.append(sid, info, parts)
        n_msgs += 1
    if efield is not None and fresh:
        try:
            evs = _field_events(a, fresh, sid)
            if evs:
                efield.write_events(evs, session=f"x:{a.source}:{a.sid}")
                n_events = len(evs)
        except Exception:
            pass                                   # the field never blocks
    return {"sid": sid, "n_msgs": n_msgs, "n_events": n_events,
            "topup": topup}


SESSION_PREFIX = "x:"


def _last_user_id(existing_rows: list) -> str:
    for r in reversed(existing_rows):
        if (r.get("info") or {}).get("role") == "user":
            return r["info"]["id"]
    return ""


def _session_doc(a: AbsorbSession, sid: str, cwd: str | None) -> dict:
    d = os.path.realpath(str(cwd or os.getcwd()))
    rows_ts = max([r.ts_ms for r in a.rows] or [0])
    return {"id": sid, "projectID": "membrane",
            "directory": d,
            "path": os.path.relpath(d, "/"),
            "slug": _h16(f"sl:{sid}")[:8] + "-rest",
            "title": (a.title or f"absorbed {a.source} session")[:120],
            "version": "1.0", "agent": "build",
            "model": {"id": a.model_id, "providerID": a.provider_id,
                      "variant": "default"},
            "cost": 0,
            "tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}},
            "summary": {"additions": 0, "deletions": 0, "files": 0},
            "time": {"created": a.created_ms or rows_ts,
                     "updated": max(a.updated_ms, rows_ts)},
            "absorbed": {"source": a.source, "sid": a.sid}}


def _user_info_row(r: TurnRow, sid: str, seat_model: str) -> tuple:
    return ({"id": r.msg_id, "sessionID": sid, "role": "user",
             "agent": "build",
             "model": {"providerID": "membrane", "modelID": seat_model},
             "time": {"created": r.ts_ms}, "summary": {"diffs": []}},
            [{"id": _xprt(r.msg_id, 0), "sessionID": sid,
              "messageID": r.msg_id, "type": "text",
              "text": r.text[:TEXT_CAP],
              "time": {"start": r.ts_ms, "end": r.ts_ms}}])


def _assistant_info_row(r: TurnRow, a: AbsorbSession, sid: str,
                        cwd: str | None, parent_id: str) -> tuple:
    d = os.path.realpath(str(cwd or os.getcwd()))
    info = {"id": r.msg_id, "sessionID": sid, "role": "assistant",
            "parentID": parent_id or None,
            "providerID": r.provider_id or a.provider_id,
            "modelID": r.model_id or a.model_id,
            "mode": "build", "agent": "build",
            "path": {"cwd": d, "root": d},
            "cost": 0.0,
            "tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}},
            "time": {"created": r.ts_ms, "start": r.ts_ms,
                     "completed": r.ts_ms}}
    if r.made_by:
        from .claude_agent import MARK_KEY
        info[MARK_KEY] = r.made_by               # provenance (#384/#396)
    parts = [{"id": _xprt(r.msg_id, -1), "sessionID": sid,
              "messageID": r.msg_id, "type": "step-start"}]
    i = 0
    for c in r.calls:
        st_out = (c.output or "")[:TEXT_CAP] if c.output is not None else ""
        parts.append({"id": _xprt(r.msg_id, i), "sessionID": sid,
                      "messageID": r.msg_id, "type": "tool",
                      "callID": c.call_id, "tool": c.name,
                      "state": {"status": "completed"
                                if c.output is not None else "pending",
                                "input": _json_or_str(c.args),
                                "output": st_out, "title": c.name,
                                "time": {"start": r.ts_ms,
                                         "end": r.ts_ms}}})
        i += 1
    if r.thought.strip():
        parts.append({"id": _xprt(r.msg_id, i), "sessionID": sid,
                      "messageID": r.msg_id, "type": "reasoning",
                      "text": r.thought[:TEXT_CAP],
                      "time": {"start": r.ts_ms, "end": r.ts_ms}})
        i += 1
    if r.text.strip():
        parts.append({"id": _xprt(r.msg_id, i), "sessionID": sid,
                      "messageID": r.msg_id, "type": "text",
                      "text": r.text[:TEXT_CAP],
                      "time": {"start": r.ts_ms, "end": r.ts_ms}})
        i += 1
    parts.append({"id": _xprt(r.msg_id, i), "sessionID": sid,
                  "messageID": r.msg_id, "type": "step-finish",
                  "reason": "stop", "cost": 0,
                  "tokens": {"input": 0, "output": 0, "reasoning": 0,
                             "cache_read": 0, "cache_write": 0}})
    return info, parts


def _json_or_str(s: str):
    try:
        return json.loads(s)
    except Exception:
        return str(s or "")[:4000]


# ---- the field half -------------------------------------------------------------

def _field_events(a: AbsorbSession, rows: list[TurnRow], sid: str) -> list:
    """Group the fresh rows into exchanges (user ask -> the assistant
    work that followed) and split_turn them into the canonical events.
    Call-only tools go via `calls`, answered ones via `results`."""
    src = f"absorb:{a.source}"
    evs: list[Event] = []
    q, prose, thought, calls, results = "", [], [], {}, []
    ts = None
    n = [0]

    def flush():
        if not q.strip() and not prose and not results and not calls:
            return
        e = split_turn(query=q, prose=" ".join(prose), calls=dict(calls) or
                       None, thought=" ".join(thought), results=results,
                       ts=ts)
        for ev in e:
            ev.src = src
        evs.extend(e)

    for r in rows:
        n[0] += 1
        if r.role == "user":
            flush()
            q, prose, thought, calls, results = r.text, [], [], {}, []
            ts = r.ts_ms / 1000.0 if r.ts_ms else None
        else:
            if r.text.strip():
                prose.append(r.text)
            if r.thought.strip():
                thought.append(r.thought)
            for c in r.calls:
                if c.output is not None:
                    results.append((c.call_id, c.name, c.args, c.output))
                elif c.call_id:
                    calls[len(calls)] = {"id": c.call_id,
                                         "function": {"name": c.name,
                                                      "arguments": c.args}}
    flush()
    return evs


# ---- the watermark -----------------------------------------------------------

def load_marker(path: str) -> dict:
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def stamp_marker(path: str, rep: dict) -> None:
    """Atomic; merges the per-session watermarks; never raises."""
    try:
        prior = load_marker(path)
        sessions = dict(prior.get("sessions") or {})
        for imp in rep.get("imported") or []:
            sessions[f"{imp['source']}:{imp['sid']}"] = {
                "last_ts_s": imp.get("last_ts_s") or rep.get("last_run_ts"),
                "n_msgs": imp.get("n_msgs", 0)}
        doc = {"version": int(rep.get("version") or 1), "last_run_ts": rep.get("last_run_ts") or
               time.time(), "sessions": sessions}
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
    except Exception:
        pass


# ---- the entry point --------------------------------------------------------

def absorb(sessions, messages, efield, cwd: str, marker_path: str,
           model_id: str, max_sessions: int = ABSORB_MAX_SESSIONS,
           home: Path | None = None, db: Path | None = None) -> dict:
    """The boot step. Collect -> filter (already done, collector level)
    -> import newest-first, capped -> stamp the watermark (ALWAYS, even
    when nothing imported, so a quiet boot still advances it)."""
    rep = {"last_run_ts": time.time(), "imported": [], "skipped": []}
    if (home is None and db is None and
            _is_lab_field_path(os.path.dirname(os.path.abspath(marker_path)))):
        # lab/tempdir seats reading the REAL default sources: refuse.
        # (An explicit home/db is synthetic by construction -- tests.)
        print("[absorb] lab path -- absorption refused", file=sys.stderr)
        return rep
    mk = load_marker(marker_path)
    since = float(mk.get("last_run_ts") or 0.0)
    known = mk.get("sessions") or {}
    # THE ONE-TIME RESCAN (#396, again for #401/#404): a marker older
    # than MARKER_VERSION was stamped by a seat that skipped sessions an
    # older rule dropped (Anthropic-answered, a provider off the old
    # allowlist, a folder the old name missed), so the watermark hides
    # them. Until the rescan drains, such a session that
    # the marker does not know is eligible whatever its age; known
    # sessions keep their per-session watermark (no re-import).
    rescan = bool(mk) and int(mk.get("version") or 1) < MARKER_VERSION
    cands = []
    try:
        cands += claude_sessions(cwd, 0.0, home=home)
    except Exception as e:
        print(f"[absorb] claude scan failed ({str(e)[:80]})", file=sys.stderr)
    try:
        cands += opencode_sessions(cwd, 0.0, db=db)
    except Exception as e:
        print(f"[absorb] opencode scan failed ({str(e)[:80]})",
              file=sys.stderr)
    cands = [a for a in cands if a.rows]

    def _fresh(a) -> bool:
        # two ways in: newer than the seat's last run (never seen), or a
        # KNOWN session that grew while the seat was up (its new rows
        # are older than last_run_ts but past the session watermark).
        upd = a.updated_ms / 1000.0
        if upd > since:
            return True
        m = known.get(f"{a.source}:{a.sid}")
        if m is None:
            return rescan and a.old_rule_skipped
        return upd > float(m.get("last_ts_s") or 0.0)
    cands = [a for a in cands if _fresh(a)]
    # newest first, so the cap falls on the OLDEST
    cands.sort(key=lambda a: a.updated_ms, reverse=True)
    rescan_pending = False
    if len(cands) > max_sessions:
        skipped_n = len(cands) - max_sessions
        cut = cands[max(1, max_sessions):]
        cands = cands[:max(1, max_sessions)]
        # a rescan whose backlog fell past the cap stays pending: the
        # next boot takes the next newest (version is stamped only once
        # the backlog is drained)
        rescan_pending = rescan and any(
            a.old_rule_skipped and f"{a.source}:{a.sid}" not in known
            for a in cut)
        rep["skipped"].append({"source": "absorb", "sid": "-",
                               "reason": f"{skipped_n} older session(s) "
                                         "past the cap"})
    for a in cands:
        try:
            r = import_session(sessions, messages, a, model_id,
                               efield=efield, cwd=cwd)
        except ValueError as e:
            rep["skipped"].append({"source": a.source, "sid": a.sid,
                                   "reason": str(e)[:120]})
            continue
        except Exception as e:
            rep["skipped"].append({"source": a.source, "sid": a.sid,
                                   "reason": str(e)[:120]})
            continue
        if r["n_msgs"]:
            r["source"] = a.source
            # report + marker keys carry the FOREIGN sid (claude uuids
            # bare, opencode sids verbatim) -- namespacing is a store
            # detail, not a watermark fact.
            r["sid"] = a.sid
            r["last_ts_s"] = a.updated_ms / 1000.0
            rep["imported"].append(r)
    rep["version"] = 1 if rescan_pending else MARKER_VERSION
    stamp_marker(marker_path, rep)
    return rep


def banner(rep: dict) -> str:
    """The boot stderr line."""
    imported = rep.get("imported") or []
    if not imported:
        return "[absorb] nothing new since the seat's last run"
    bits = [f"{r['source']} {r['sid'][:24]} ({r['n_msgs']} msg(s))"
            for r in imported]
    cont = imported[0]["sid"]
    return (f"[absorb] {len(imported)} session(s): "
            + ", ".join(bits) + f" -- continuing {cont}")


def absorb_gate(field_path: str, new_session: bool, no_absorb: bool,
                native_up: bool) -> bool:
    """Lab/tempdir seats NEVER absorb the operator's real sessions."""
    return bool(native_up and not new_session and not no_absorb
                and not _is_lab_field_path(field_path))