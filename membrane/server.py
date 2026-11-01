"""THE NATIVE SERVER (stage 3): membrane replaces opencode completely.

`membrane serve` used to launch `opencode serve` as a child -- its own REST
surface facing the room, with membrane proxying its model traffic and
capturing it. The full-replacement lane drops the middleman: membrane
HOSTS that surface itself. This module is opencode's replacement --
opencode-compatible where clients care:

- `SessionStore` / `MessageStore`: opencode's Session / Message / Part
  shapes, persisted beside the field (a device's history survives a seat
  restart).
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
  session/message/abort/config subset a chat client speaks.

What is deliberately NOT here: opencode's file/VCS/LSP/share surface (a
client needs to work on the room through run and the field's
readers, which the doctrine already teaches).
"""
import copy
import hashlib
import json
import os
import secrets
import socket
import threading
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue, Empty
from pathlib import Path

from . import mcp_server as _mcp
from . import jwtauth as _JWT
from . import seatauth as _SA
from . import diag as _BUSY                 # #431: the seat-busy error's shape
from . import quota as _QUOTA               # the out-of-tokens pause, per model


class SeatBusy(RuntimeError):
    """The /v1 proxy answered 'too busy to answer with its memory' (#431):
    the rung outlived its budget. The turn fails visibly; nothing retries
    it -- the person's Retry does."""


# the failed row's mark: the chat renders it with a Retry (#431)
BUSY_MARK = "seatBusy"
# OUT OF TOKENS (quota.py): the row a turn left when its model ran out; the
# person's message waits (TurnRunner.held) and the row never replays as history
QUOTA_MARK = "quotaHeld"
# A ROUND THAT ENDED EARLY (#449): the streamed round's marker, and the
# error its row carries (a cut reply is never stored as a finished one)
CUT_KEY = "membrane_cut"
CUT_ERRORS = {
    "abort": {"name": "MessageAbortedError",
              "data": {"message": "cut: the turn was cut before this reply finished (a person spoke, or Stop)"}},
    "drop": {"name": "StreamDropped",
             "data": {"message": "the model's stream dropped before this reply finished"}},
}


def _busy_error(obj) -> str | None:
    """The seat-busy message in a proxy error body, or None."""
    e = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(e, dict) and e.get("type") == _BUSY.SEAT_BUSY_TYPE:
        return str(e.get("message") or _BUSY.SEAT_BUSY_MESSAGE)
    return None

VERSION = "1.0"
PROJECT_ID = "membrane"
CLOUD_TIMEOUT_S = 1800.0              # a model turn is allowed minutes
MAX_ROUNDS = 24                       # tool-loop budget per turn
HISTORY_MAX_MESSAGES = 12             # prior conversation rows replayed
                                      # into a serve turn's history (the
                                      # narrative: 'it' needs an
                                      # antecedent in the transcript)
HISTORY_TEXT_CAP = 4000               # per-prior-message text cap
HISTORY_ANCHOR_STEP = 4               # the window's first row is a multiple of
                                      # this (#450): its start holds while the
                                      # chat grows, so a cached prompt head is
                                      # read again; 9-12 rows instead of 12


def _env_int(name: str, default: int, low: int = 1) -> int:
    try:
        return max(low, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def history_max_messages() -> int:
    """The replay window's size (MEMBRANE_HISTORY_MAX overrides, #450)."""
    return _env_int("MEMBRANE_HISTORY_MAX", HISTORY_MAX_MESSAGES)


def history_anchor_step() -> int:
    """The window's start step (MEMBRANE_HISTORY_ANCHOR overrides; 1 = the
    plain last-N window, #450)."""
    return _env_int("MEMBRANE_HISTORY_ANCHOR", HISTORY_ANCHOR_STEP)


def _gen_id(prefix: str) -> str:
    return prefix + "_" + secrets.token_hex(12)


_SLUG_A = ("misty", "tidy", "calm", "quiet", "swift", "soft", "amber",
           "early", "humble", "silver", "violet", "gentle")
_SLUG_B = ("lagoon", "wolf", "meadow", "harbor", "cedar", "ember", "dune",
           "fjord", "grove", "brook", "reef", "tide")


def _slug() -> str:
    return (secrets.choice(_SLUG_A) + "-" + secrets.choice(_SLUG_B))


# ---- stores -----------------------------------------------------------------

def _migrated_name(path: str) -> str:
    return f"{path}.migrated-{time.strftime('%Y%m%d-%H%M%S')}"


class SessionStore:
    """opencode's Session dicts, persisted beside the field. A missing
    file, a corrupt one or an unwritable home degrade to RAM-only (never a
    serve failure).

    THE DATABASE HOME (#799): with the seat's Couchbase Lite store (`cbl`),
    one document per session in the `sessions` collection, and a save writes
    only the sessions that changed (the file was rewritten whole on every
    touch). The first boot with the database imports the file and keeps it
    renamed as a backup."""

    CBL_COLL = "sessions"

    def __init__(self, path: str | None = None, cbl=None):
        self.path = path
        self.sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._cbl = cbl
        self._sig: dict[str, str] = {}
        if cbl is not None:
            try:
                cbl.coll(self.CBL_COLL)
                for r in cbl.query(f"SELECT s FROM membrane.{self.CBL_COLL}"):
                    s = r.get("s")
                    if isinstance(s, dict) and s.get("id"):
                        self.sessions[s["id"]] = s
                self._sig = {sid: self._sig_of(s) for sid, s in self.sessions.items()}
            except Exception as e:
                print(f"[serve] sessions: database load failed ({e})", file=sys.stderr)
            if self.sessions or path is None or not Path(path).exists():
                return
        if path is not None:
            try:
                raw = Path(path).read_text()
                d = json.loads(raw)
                if isinstance(d, dict):
                    self.sessions = d
            except Exception:
                pass
            if cbl is not None and self.sessions:           # the first database boot
                with self._lock:
                    ok = self._save_cbl(None)
                if not ok:              # the file stays where it is: the next boot imports it again
                    return
                try:
                    os.replace(path, _migrated_name(path))
                    print(f"[serve] migrated {len(self.sessions)} sessions into couchbase lite; "
                          "the JSON file is kept as a backup", file=sys.stderr)
                except OSError:
                    pass

    @staticmethod
    def _sig_of(s: dict) -> str:
        return json.dumps(s, sort_keys=True, default=str)

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
            self._flush(s["id"])
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
            self._flush(sid)
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
            self._flush(sid)
            return s

    def touch(self, sid: str) -> None:
        with self._lock:
            s = self.sessions.get(sid)
            if s is not None:
                # epoch-ms, like every other SDK time: the attached TUI's
                # -c/continue lane sorts sessions by time.updated desc --
                # a seconds value sorted as ancient broke the lane
                s["time"]["updated"] = int(time.time() * 1000)
                self._flush(sid)

    def delete(self, sid: str) -> bool:
        with self._lock:
            if sid not in self.sessions:
                return False
            del self.sessions[sid]
            self._flush(sid)
            return True

    def _flush(self, sid: str | None = None) -> None:
        """The caller holds the lock. `sid`: the one session that changed
        (None: compare every session, for callers that edited the dict)."""
        if self._cbl is not None:
            self._save_cbl(sid)
            return
        if self.path is None:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.sessions, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def _save_cbl(self, sid: str | None) -> bool:
        """-> True when every changed session was written."""
        from .cblite import doc_key
        ids = [sid] if sid is not None else list(dict.fromkeys(list(self.sessions) + list(self._sig)))
        put, gone = [], []
        for k in ids:
            s = self.sessions.get(k)
            if s is None:
                if k in self._sig:
                    gone.append(k)
                continue
            sg = self._sig_of(s)
            if self._sig.get(k) != sg:
                put.append((k, s, sg))
        if not put and not gone:
            return True
        try:
            many = len(put) + len(gone) > 1
            if many:
                self._cbl.begin()
            try:
                for k, s, sg in put:
                    self._cbl.put_json(self.CBL_COLL, doc_key(k), json.dumps({"s": s}, default=str))
                    self._sig[k] = sg
                for k in gone:
                    self._cbl.purge(self.CBL_COLL, doc_key(k))
                    self._sig.pop(k, None)
            finally:
                if many:
                    self._cbl.end()
        except Exception as e:
            print(f"[serve] sessions: database save failed ({e})", file=sys.stderr)
            return False
        return True

    def flush(self) -> None:
        with self._lock:
            self._flush()


from .stats import read_raw as _read_raw


class MessageStore:
    """A session's messages: rows of {info, parts}. Write-through (a
    model turn is minutes apart -- the file write is nothing), atomic
    replace, RAM-only on any IO failure."""

    CBL_COLL = "messages"

    def __init__(self, path: str | None = None, cbl=None):
        self.path = path
        self.msgs: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self._last_flush_ts = 0.0
        self._dirty_rows: set | None = None
        # THE DATABASE HOME (#799): with the seat's Couchbase Lite store, one
        # document per message in the `messages` collection ({sid, mid, n,
        # row}; n keeps the order), and a flush writes only the rows that
        # changed. The first boot with the database imports the snapshot +
        # journal and keeps both renamed as backups.
        self._cbl = cbl
        self._n = 0
        self._pos: dict[tuple, int] = {}
        if cbl is not None:
            try:
                cbl.coll(self.CBL_COLL)
                got = cbl.query(f"SELECT sid, mid, n, row FROM membrane.{self.CBL_COLL}")
            except Exception as e:
                print(f"[serve] messages: database load failed ({e})", file=sys.stderr)
                got = []
            if got:
                got.sort(key=lambda r: int(r.get("n") or 0))
                d: dict = {}
                for r in got:
                    if isinstance(r.get("row"), dict) and r.get("sid"):
                        d.setdefault(r["sid"], []).append(r["row"])
                        self._pos[(r["sid"], str(r.get("mid") or ""))] = int(r.get("n") or 0)
                self._n = max(self._pos.values(), default=0)
                self.msgs = self._mended(d)
                return
            if path is None or not (Path(path).exists() or Path(path + ".jnl").exists()):
                return
        if path is not None:
            try:
                d = _read_raw(path)       # snapshot + journal (#542)
                if isinstance(d, dict):
                    self.msgs = self._mended(d)
            except Exception:
                pass
            if cbl is not None and self.msgs:               # the first database boot
                with self._lock:
                    self._dirty_rows = {(sid, str(r["info"].get("id") or ""))
                                        for sid, rows in self.msgs.items() for r in rows}
                    self._flush()
                    failed = bool(self._dirty_rows)        # a failed save leaves its rows dirty
                if failed:              # the files stay where they are: the next boot imports them again
                    return
                try:
                    stamp = _migrated_name(path)
                    os.replace(path, stamp)
                    if os.path.exists(path + ".jnl"):
                        os.replace(path + ".jnl", stamp + ".jnl")
                    print(f"[serve] migrated {len(self._pos)} messages into couchbase lite; "
                          "the JSON files are kept as backups", file=sys.stderr)
                except OSError:
                    pass

    @staticmethod
    def _mended(d: dict) -> dict:
        """A stored file may predate the snapshot upsert (one message saved
        once per tool-part state change): dedupe ON LOAD -- last parts win,
        first position stays, or resumed history still duplicates.
        THE TOKEN MEND (the `K.tokens.output undefined` TUI-crash exhibit,
        2026-10-06, THE DATA-HALF): a stored assistant row predating the
        tokens covenant (the old watchdog-error mints carried NO tokens)
        POISONED EVERY SESSION LOAD -- the attached TUI's reducer findLast
        lands on it and reads K.tokens.output -> the crash ON STARTUP, every
        boot, forever. The load MENDS: every assistant row gets a full
        tokens dict (zeros when unknown) -- the persisted poison dies with
        this store's next flush."""
        out: dict[str, list[dict]] = {}
        for sid, rows in d.items():
            merged: dict[str, dict] = {}
            ordered: list[str] = []
            for r in rows:
                info = r.get("info") or {}
                mid = str(info.get("id") or "")
                if info.get("role") == "assistant" and \
                        not isinstance(info.get("tokens"), dict):
                    info["tokens"] = {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                                      "cache": {"read": 0, "write": 0}}
                    r["info"] = info
                if mid not in merged:
                    ordered.append(mid)
                merged[mid] = r
            out[sid] = [merged[m] for m in ordered]
        return out

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
                self._dirty_rows = (self._dirty_rows or set())
                self._dirty_rows.add((sid, mid))
                self._flush()
                self._last_flush_ts = time.time()
            else:
                self._dirty_rows = (self._dirty_rows or set())
                self._dirty_rows.add((sid, mid))
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
            self._dirty_rows = (self._dirty_rows or set())
            self._dirty_rows.add((sid, str(row["info"].get("id") or "")))
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
        """AN APPEND COSTS AN APPEND (#542): the rows this writer changed
        go onto the journal `path.jnl` as one JSON line each (a single
        O_APPEND write under a file lock -- a crash leaves at most a torn
        last line, which the readers skip). The snapshot is rewritten only
        by `_compact`, when the journal has outgrown it, so the cost
        stays proportional to the change, not to the 67 MB history.
        THE MULTI-WRITER MERGE (the vanishing-reply fix) lives in the
        readers now: two seats serving out of one cwd both append, and
        `stats.read_raw` unions rows by message id, the newer
        completed/created ts winning -- a stale empty row never displaces
        a finished one. With no dirty set a flush is a no-op.
        A row is journaled whole on each change, so the journal can reach
        the snapshot's size before it compacts (a boot reads up to ~2x).
        With the database (#799) each changed row is its own document."""
        if self._cbl is not None:
            self._save_cbl()
            return
        if self.path is None:
            return
        try:
            dirty = self._dirty_rows
            self._dirty_rows = None
            if not dirty:
                return                      # nothing changed: nothing to write
            rows = []
            for sid, mid in dirty:
                r = next((r for r in self.msgs.get(sid) or []
                          if str(r["info"].get("id") or "") == mid), None)
                if r is not None:
                    rows.append((sid, r))
            if not rows:
                return
            data = "".join(json.dumps({"sid": sid, "row": r}) + "\n"
                           for sid, r in rows).encode()
            with self._file_lock():
                fd = os.open(self.path + ".jnl",
                             os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    os.write(fd, data)
                    jsize = os.fstat(fd).st_size
                finally:
                    os.close(fd)
                try:
                    bsize = os.stat(self.path).st_size
                except OSError:
                    bsize = 0
                if jsize > bsize:           # the journal outgrew the snapshot
                    self._compact_locked()
        except Exception:
            pass

    _dirty_rows: set | None = None

    @staticmethod
    def _doc_key(sid: str, mid: str) -> str:
        from .cblite import doc_key
        return doc_key(f"{sid}|{mid}")

    def _save_cbl(self) -> None:
        """The caller holds the lock: the dirty rows, one document each, in their
        session order (a new row's n follows the rows before it)."""
        dirty = self._dirty_rows
        self._dirty_rows = None
        if not dirty:
            return
        rows = []
        for sid in {k[0] for k in dirty}:
            for i, r in enumerate(self.msgs.get(sid) or []):
                mid = str(r["info"].get("id") or "")
                if (sid, mid) in dirty:
                    rows.append((sid, mid, r))
        if not rows:
            return
        try:
            many = len(rows) > 1
            if many:
                self._cbl.begin()
            try:
                for sid, mid, r in rows:
                    n = self._pos.get((sid, mid))
                    if n is None:
                        self._n += 1
                        n = self._pos[(sid, mid)] = self._n
                    self._cbl.put_json(self.CBL_COLL, self._doc_key(sid, mid),
                                       json.dumps({"sid": sid, "mid": mid, "n": n, "row": r}, default=str))
            finally:
                if many:
                    self._cbl.end()
        except Exception as e:
            self._dirty_rows = (self._dirty_rows or set()) | set(dirty)    # the next flush retries
            print(f"[serve] messages: database save failed ({e})", file=sys.stderr)

    def _file_lock(self):
        import contextlib
        import fcntl

        @contextlib.contextmanager
        def _lk():
            fd = os.open(self.path + ".lock", os.O_WRONLY | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)             # closing releases the flock
        return _lk()

    def _compact_locked(self, drop: tuple = ()) -> None:
        """Fold snapshot + journal into a new snapshot (atomic replace),
        then empty the journal. The caller holds the file lock. A crash
        between the replace and the truncate only replays rows the
        snapshot already holds -- harmless (same ids, same versions)."""
        raw = _read_raw(self.path)
        for sid in drop:
            raw.pop(sid, None)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(raw, f)
        os.replace(tmp, self.path)
        open(self.path + ".jnl", "w").close()

    def delete(self, *sids: str) -> bool:
        """Forget sessions' messages, in RAM and on disk (the journal is
        folded into the snapshot with the sessions dropped, once for all
        the ids, #570)."""
        with self._lock:
            had = False
            for sid in sids:
                had = (self.msgs.pop(sid, None) is not None) or had
                if self._dirty_rows:
                    self._dirty_rows = {k for k in self._dirty_rows
                                        if k[0] != sid}
            if self._cbl is not None:
                gone = [k for k in self._pos if k[0] in sids]
                try:
                    if gone:
                        self._cbl.begin()
                        try:
                            for sid, mid in gone:
                                self._cbl.purge(self.CBL_COLL, self._doc_key(sid, mid))
                                self._pos.pop((sid, mid), None)
                        finally:
                            self._cbl.end()
                except Exception as e:
                    print(f"[serve] messages: database delete failed ({e})", file=sys.stderr)
                return had or bool(gone)
            if self.path is None:
                return had
            try:
                with self._file_lock():
                    if not any(sid in _read_raw(self.path) for sid in sids):
                        return had
                    self._compact_locked(drop=sids)
            except Exception:
                return had
            return True

    def flush(self) -> None:
        with self._lock:
            self._flush()


def post_line(runner, sid: str, text: str) -> None:
    """A membrane line into a chat, as an assistant text part (never a turn,
    never a model call). THE ROW'S TWO LIVES (the crash covenant, 2026-10-06,
    the 'K.tokens.output undefined' TUI exhibit): with an assistant row last,
    the part rides ITS id (the reducer holds it); otherwise append_part MINTS the
    row and publishes the FULL message.updated first (row before part), and the
    wire publishes the STORED part, which carries the minted row's id."""
    rows = [r for r in runner.messages.list(sid) if r]
    last_asst = next((r["info"] for r in reversed(rows)
                      if (r.get("info") or {}).get("role") == "assistant"), None)
    now = int(time.time() * 1000)
    part = {"id": _gen_id("prt"), "sessionID": sid, "type": "text",
            "text": str(text)[:2000], "time": {"start": now, "end": now}}
    if last_asst is not None and last_asst.get("id"):
        part["messageID"] = last_asst["id"]
    runner.messages.append_part(sid, part)
    rows2 = runner.messages.list(sid)
    stored = ((rows2[-1].get("parts") or [])[-1]) if rows2 else part
    runner.hub.publish({"type": "message.part.updated", "part": dict(stored), "sessionID": sid})


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
    name is `function`, parameters come from the MCP inputSchema).
    Wire-shaped rows (a custom caller's explicit set) pass through -- the
    wrapper only shapes the MCP registry's own rows (2026-10-13: an
    explicit set used to die here on a bare KeyError('name'))."""
    from . import toolset as _ts
    tools = _mcp.TOOLS if mcp_tools is None else mcp_tools
    return [t if isinstance(t, dict) and "function" in t else
            {"type": "function",
             "function": {"name": t["name"],
                          "description": t.get("description", ""),
                          "parameters": t.get("inputSchema") or
                          {"type": "object", "properties": {}}}}
            for t in tools
            # ISSUE #182: a row named by a template placeholder
            # ('{function_name}') is never served -- the model copies it
            if not (isinstance(t, dict) and "function" not in t
                    and not _ts.is_tool_name(t.get("name")))]


# ---- THE LANE GATE (the division of hands, 2026-10-06) ----------------------
# The seat's two minds have different hands: the OPERATOR (build/plan
# sessions -- the main chat) plants INTENTS, commits and runs commands
# through the methods it defines (#576 part F); the LISTENERS
# (the watch loops -- agent 'watch:<eid>') hold the ENTITY HANDS --
# add/edit/remove -- the events they process are their
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


def stamp_card_planter(name: str, args, agent, sid: str = "") -> dict:
    """A new Decision card carries who planted it (`planted_by`): "watch" from a
    watch loop's lane, "chat" otherwise; an explicit value is kept. The answer's
    decision event routes by it (decision_map._tell). A chat-planted card also
    records the chat (`sid`) it was planted from, so its answer comes back there."""
    if not str(name).endswith("entity_add") or not isinstance(args, dict) \
            or str(args.get("class") or "").lower() != "decision":
        return args
    props = args.get("properties")
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except ValueError:
            return args
    props = dict(props) if isinstance(props, dict) else {}
    # "listener" = the watch loops and any other agent (subagents too)
    props.setdefault("planted_by", "watch" if _lane_of(agent) == "listener" else "chat")
    if props["planted_by"] == "chat" and sid:
        props.setdefault("chat", str(sid))
    return {**args, "properties": props}


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
    "peer_post", "peer_seats", "peer_read", "junk_cut",
    # the banner tracks (the operator's header)
    "entity_watch", "entity_unwatch",
    # the seat's own alert dial (2026-10-08): how many watch events may
    # interrupt vs fold into the WHERE WE ARE digest
    "attention",
    # THE EXECUTOR'S DOOR (2026-10-08, the operator's call): the operator
    # runs commands itself, the receipt comes back in the same turn -- since
    # #576 part F only as methods it defines and calls (no one-shot link)
    "method_define", "method_call", "method_each",
    # #614: code runs as a tree linked to its executor
    "code_run",
    # a method's read (issue #80): runs nothing, so plans may look too
    "method_inspect",
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
    "method_define", "method_call", "method_each", "code_run",
    # the board's spatial watches (2026-10-08): attention is the
    # listeners' to allocate; the chat lane announces through intend
    "field_watch",
}) - {"intend"}
# plan reads/communicates: never plants, closes or executes
PLAN_TOOLS = OPERATOR_TOOLS - {"intend", "fulfill", "method_define", "method_call", "method_each", "code_run",
                               "method_inspect",
                               "entity_add", "entity_edit",
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


# THE GOAL GRAPH NEVER CLOSES A GOAL (operator, 2026-10-31: "escalate to chat instead of
# closing -- the chat turn decides"): its planner and workers keep every other hand
_GRAPH_LOOPS = ("planner:", "worker:")
_GRAPH_REFUSALS = {
    "fulfill": ("goal-graph loop: a goal isn't closed from here -- the planner ends with "
                "FULFILLED <receipt> (a worker with DONE); the claim goes to the chat, and the "
                "chat turn decides"),
}


def _graph_refusal(name: str, agent: str | None) -> str | None:
    """The refusal for a goal-graph loop's call (None = allowed)."""
    if _lane_gate_off() or not str(agent or "").strip().lower().startswith(_GRAPH_LOOPS):
        return None
    return _GRAPH_REFUSALS.get(name)


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
    if a.startswith(("field-call", "triage")):
        return "plan"                   # llm.py: the field asked; #729: a watch triage pass -- read-only
    if a == "speaker":
        return "operator"               # speaker.py: a person talking -- the chat's hands
    return "listener"


def _lane_tools(lane: str) -> frozenset:
    if _lane_gate_off():
        return _mcp_names()
    return {"operator": OPERATOR_TOOLS, "plan": PLAN_TOOLS,
            "listener": LISTENER_TOOLS,
            "phone": PHONE_TOOLS}.get(lane, OPERATOR_TOOLS)


def _mcp_names() -> frozenset:
    return frozenset(t["name"] for t in _mcp.TOOLS)


PARALLEL_READS = frozenset(("find", "query", "state", "graph", "entities_related", "entity_query",
                            "entity_read", "entity_tracks", "intents"))
PARALLEL_READS_MAX = 4


def _thinking(runner, sid: str, phase: str) -> None:
    """mind.think {agent, phase} (no text): a model round of this mind
    started / its answer arrived -- membrane home's orb shows the wait."""
    try:
        from .fieldpulse import of as _pulse_of
        fp = _pulse_of(getattr(runner, "srv", None))
        if fp is not None:
            fp.thinking((runner._agents or {}).get(sid), phase)
    except Exception:
        pass


def _claim_note(srv, session_agent, tool: str, args: dict, output):
    """THE CLAIM (2026-10-11): a mind touching an entity ANOTHER loop is
    working on hears so in the tool's result -- no lock, the work stays
    parallel; it just isn't done twice."""
    try:
        from .fieldpulse import of as _pulse_of, touched_eid
        fp = _pulse_of(srv)
        eid = touched_eid(tool, args) if fp is not None else ""
        if not eid:
            return output
        h = fp.holder(eid, mine=str(session_agent or ""))
        if not h:
            return output
        slot, since, held = h
        return (str(output or "") + f"\n[note: {slot} is working on {held} right now (since "
                f"{time.strftime('%H:%M', time.localtime(since))}) -- don't redo its pass; its result "
                "lands on the entity (watch_last_decision)]")
    except Exception:
        return output


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
    # THE HONEST REFUSAL (2026-10-11: "that phrase about lanes just means that you
    # called wrong tool, its an obsolete message" -- the seat read it as "this
    # turn is read-only" and stopped acting): an unknown name says so
    known = set(_mcp_names()) | set(_lane_tools("operator"))
    try:
        from . import toolset as _ts
        known |= set(_ts.FAMILIES)
    except Exception:
        pass
    if name not in known:
        try:
            from . import toolset as _ts
            fams = ", ".join(sorted(_ts.FAMILIES))
        except Exception:
            fams = ""
        return (f"no tool is named {name!r} -- the call never ran (nothing about this turn is "
                f"read-only). The tools: {fams}")
    return (f"{name} isn't available in this session's lane ({lane}) -- that call never ran; "
            "the other tools still work.")


def _peers_possible() -> bool:
    """Other seats are reachable only through the Couchbase substrate (peers.py);
    an install without it (Home Assistant's, a plain pip install) has no peers."""
    import importlib.util
    return importlib.util.find_spec("couchbase") is not None


def _openai_tools_for(agent: str | None) -> list:
    """The ADVERTISED tools for the lane (the same registry, filtered)."""
    names = [n for n in _lane_tools(_lane_of(agent)) if _graph_refusal(n, agent) is None]
    # NO PEERS, NO PEER TOOLS (2026-10-12: a seat inside Home Assistant was handed
    # peer_post, its exploration goal posted to seats that can't exist, failed, and
    # waited "BLOCKED on the operator" for good)
    if not _peers_possible():
        names = [n for n in names if not str(n).startswith("peer")]
    # THE TOOL FAMILIES (the token diet, 2026-10-09): ~10 action-tools that
    # resolve to these same impls; MEMBRANE_TOOL_FAMILIES=0 = the legacy 24
    if os.environ.get("MEMBRANE_TOOL_FAMILIES", "1") != "0":
        from . import toolset as _ts
        return _ts.advertised(names)
    return [t for t in _mcp.TOOLS if t["name"] in names]


def _tool_title(name: str, args: dict) -> str:
    s = json.dumps(args) if args else ""
    return f"{name}({s[:80]})"


MESSAGE_PAGE_MAX = 500     # the most rows one paged read returns (#470: the cockpit's chat)


def message_page(rows: list, limit, before: str = "") -> list:
    """A page of chronological rows (#470): the `limit` rows before the message
    `before` (exclusive; an unknown id reads nothing), or the newest `limit`
    without it; no limit: every row (the unpaged read, unchanged)."""
    if before:
        idx = next((i for i, r in enumerate(rows)
                    if str((r.get("info") or {}).get("id") or "") == before), None)
        if idx is None:
            return []
        rows = rows[:idx]
    if limit is None:
        return list(rows)
    n = max(1, min(MESSAGE_PAGE_MAX, int(limit)))
    return list(rows[-n:])


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
    c["write"] = _i(c.get("write")) + (            # claude-agent's split (#426)
        _i(pdet.get("cache_write_tokens")) if isinstance(pdet, dict) else 0)


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


def _step_tokens(a_info: dict, history: list, parts: list) -> dict:
    """A step's tokens (#693): the ladder's usage when it gave any, else the
    running estimate -- never the zeros the message opened with."""
    tk = a_info.get("tokens") or {}
    return dict(tk) if tk.get("total") else _tokens_estimate(history, parts)


BUSY_WAIT_S = 300.0   # a queued second POST waits this long, then gets a visible busy stub
DELTA_COALESCE_S = 0.25   # a streaming part's text goes out at most this often (was: every token)
HEARTBEAT_S = 15.0    # busy-heartbeat cadence while a turn runs
QUEUE_DEPTH_MAX = 8   # queued turns per session beyond which POSTs refuse fast (the flood guard)

class TurnRunner:
    """THE TURN LOOP: client text -> the seat's own /v1 (all of the
    proxy's machinery, in-process, via loopback) -> tool_calls executed
    against the MCP tool impls -> role:'tool' results -> repeat. Every
    Message+Part state transition is published on the hub."""

    # THE TURN'S OWN SID (#768): the chat and the loops run on this one
    # runner at once, so the sid the loopback body carries is per thread --
    # a shared attribute handed a chat turn the last-started worker's sid
    _turn_local = threading.local()

    @property
    def _current_turn_sid(self):
        return getattr(self._turn_local, "sid", None)

    @_current_turn_sid.setter
    def _current_turn_sid(self, sid):
        self._turn_local.sid = sid

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
        # OUT OF TOKENS (quota.py): a person's messages held while their
        # model is paused, per session, in order; released by the reset,
        # the probe time or Resume now
        self._held: dict[str, list[dict]] = {}
        self._held_lock = threading.Lock()
        _QUOTA.subscribe(self._on_quota)

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
        honest idle (a page still showing busy kept the button on with
        nothing to stop) and a flag that only a turn starting within ABORT_PREARM_S
        honours (Stop right after Send still cancels; a stale press used to
        cut the NEXT turn's tools short whenever it came)."""
        self._aborting = getattr(self, "_aborting", None) or set()
        self._turn_activity = getattr(self, "_turn_activity", {}) or {}
        if self._drop_held(sid):
            # Stop on a message waiting for its model's tokens: it is let go
            self.hub.publish({"type": "membrane.quota", "why": "stopped", "sessionID": sid})
            if sid not in (getattr(self, "_busy", None) or {}):
                return
        self._aborting.add(sid)
        (getattr(self, "_preempted", None) or set()).discard(sid)   # Stop is a stop, even after a preempt
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
        # the time membrane spent preparing this round (the proxy's gate), so
        # the card reads e.g. 57.7s, not the 0s of a start == end stamp (#693)
        _g = ext.get("gate_s")
        _start = _now - int(round(float(_g) * 1000)) if isinstance(_g, (int, float)) and _g > 0 else _now
        _ms = ext.get("massage") or {}
        _title = f"membrane injection · ~{len(_inj) // 4} tokens" + (
            f" · alignment {_ms.get('alignment')}" if _ms.get("alignment") else "")
        _ip = mkpart({"type": "tool", "callID": f"inj-{rounds}", "tool": "membrane",
                      "state": {"status": "completed",
                                "input": {"round": rounds, **({k: _ms[k] for k in ("alignment", "tokens", "picked") if k in _ms})},
                                "output": _inj, "title": _title,
                                "time": {"start": _start, "end": _now}},
                      "time": {"start": _start, "end": _now}})
        self.hub.publish({"type": "message.part.updated", "part": dict(_ip),
                          "time": _now, "sessionID": sid})

    def preempt(self, sid: str) -> bool:
        """A PERSON SPOKE (2026-10-10, the operator: "interrupt the chat loop
        whenever there is a message"): the running turn of this session is cut
        now -- the abort flag (between rounds) + its model socket (a round
        blocked in the model) -- and its finally drains the queue, so the
        person's message runs next. False when nothing runs."""
        running = sid in (getattr(self, "_turn_stops", None) or {}) or sid in (getattr(self, "_busy", None) or {})
        if not running:
            return False
        self._aborting = getattr(self, "_aborting", None) or set()
        self._aborting.add(sid)
        self._preempted = getattr(self, "_preempted", None) or set()
        self._preempted.add(sid)                 # not a Stop: the round's calls may finish
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

    def post_line(self, sid: str, text: str) -> None:
        """A membrane line into this runner's chat `sid` (module post_line)."""
        post_line(self, sid, text)

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

    def _prior_history(self, sid: str, exclude_id: str, anchor: bool = False) -> list[dict]:
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
            if info.get("speakerRouted"):
                continue          # answered in the speaker lane (speaker.py), not here
            if info.get("voiceInterim"):
                continue          # a streamed partial (Phase 2): its final transcript replays
            if info.get(QUOTA_MARK):
                continue          # the out-of-tokens note (quota.py): not a reply
            text = "\n".join(
                str(p.get("text") or "") for p in row.get("parts") or []
                if isinstance(p, dict) and p.get("type") == "text").strip()
            if not text:
                continue
            out.append({"role": role, "content": text[:HISTORY_TEXT_CAP]})
        # THE STEPPED WINDOW (#450): the last N rows, its first row rounded
        # UP to a multiple of the anchor step -- the start holds for a few
        # turns, so the prompt's head is the same bytes and its cache is read
        start = max(0, len(out) - history_max_messages())
        step = history_anchor_step()
        if start and step > 1:
            start = -(-start // step) * step
        win = out[start:]
        # THE ANCHOR (2026-10-11, "why is auto-chat not auto-chatting?"): a
        # continuation (auto-chat's rounds until #348; a job's turn) adds
        # only his own replies -- after 12 the window held no user message
        # at all, and the backend answered an all-assistant conversation
        # with nothing (done_reason 'load', 0 tokens), every round. The last user message always rides first.
        if anchor and win and not any(m["role"] == "user" for m in win):
            last_user = next((m for m in reversed(out) if m["role"] == "user"), None)
            win = [last_user or {"role": "user", "content": "(continue)"}] + win[1:]
        return win

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

    def claude_continue(self, sid: str, mid: str) -> str:
        """A PERSON'S CONTINUE (#389): claim the capped claude-agent answer
        `mid` for one Continue and return its SDK session id. Raises
        claude_agent.ContinueRefused on a background lane's session, a row
        that is not capped, or one already continued (nothing runs twice)."""
        from . import claude_agent as _CA
        if not _CA.person_agent(self._turn_agent(sid, None)):
            raise _CA.ContinueRefused("background lanes never continue a claude-agent answer")
        lock = self.__dict__.setdefault("_claude_continue_lock", threading.Lock())
        with lock:
            row = self.messages.get_msg(sid, str(mid or ""))
            info = (row or {}).get("info") or {}
            cap = info.get(_CA.CAP_MARK)
            if not isinstance(cap, dict) or not cap.get("session"):
                raise _CA.ContinueRefused("that message did not stop at the tool-call ceiling")
            if cap.get("continued"):
                raise _CA.ContinueRefused("that answer was already continued")
            info[_CA.CAP_MARK] = dict(cap, continued=True)
            self.messages.append(sid, info, list(row.get("parts") or []))
        self.hub.publish({"type": "message.updated", "info": dict(info),
                          "sessionID": sid})
        return str(cap["session"])

    def claude_continue_row(self, sid: str, mid: str, session: str) -> dict:
        """The person's Continue row (stored): the turn it starts resumes
        `session` (claude_agent.RESUME_KEY)."""
        from . import claude_agent as _CA
        info, parts = self._mint_user_row(sid, _CA.CONTINUE_TEXT)
        info[_CA.RESUME_KEY] = str(session)
        info[_CA.CONTINUE_FIELD] = str(mid)
        self.messages.append(sid, info, parts)
        return info

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

    def _turn_model(self, sid: str, text: str) -> tuple:
        """THE TURN'S MODEL, ONE RULE for the round and the reply's stamp ->
        (model, claude credentials override or None).

        A person's chat: the session's own model (the operator's pick on the
        session holds over the boot default -- the TUI and the chat picker
        pin it, #457), then the active-model entities. A background lane (the
        watch / goal loops): the loop runner's current model, the Goal
        worker in ⚙ (2026-10-23: a loop session that remembered another
        model ran on it while the picker said otherwise). The claude-agent
        rung on a background lane only on an explicit pick for that mind,
        with that mind's credentials (minds_models); else the #384 guard."""
        agent = (self._agents or {}).get(sid)
        lane = _lane_of(agent)
        srv = getattr(self, "srv", None)
        turn_model = self.model
        if lane != "listener":
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
                _am = _AM.for_turn(srv, text, lane)
                if _am:
                    turn_model = _am
            except Exception:
                pass
        auth = None
        try:
            from . import claude_agent as _CA
            from . import minds_models as _MM
            # a field call (an entity script asking a model) is background
            # work like a loop pass: it follows the loops' pick (2026-10-29)
            field_call = str(agent or "").strip().lower().startswith("field-call")
            # #729: a watch triage pass runs on the local idle model, never on Claude
            triage = str(agent or "").strip().lower().startswith("triage")
            if (lane == "listener" or field_call) and not triage and _CA.is_claude_rung(srv, turn_model) \
                    and _MM.claude_allowed(srv, "loops", turn_model):
                auth = _MM.credential_mode(srv, "loops")
            else:
                # THE CLAUDE-AGENT RUNG IS A PERSON'S (issue #384): a loop
                # or a mind not picked onto it falls back to the seat's model
                fb = str(getattr(srv, "default_seat_model", "") or "")
                if field_call or triage:
                    # the idle thinker's local model: one local LLM stays
                    # loaded (the seat's 14b next to the thinker's 8b made
                    # Ollama evict the embedder -- 60 s on a person's turn)
                    idle = str(getattr(srv, "idle_cortex_model", "") or "")
                    if idle and not _CA.is_claude_rung(srv, idle):
                        fb = idle
                if not fb or _CA.is_claude_rung(srv, fb):
                    fb = self.model if not _CA.is_claude_rung(srv, self.model) else "membrane"
                # #729: triage always takes the local pick (a session's own model never holds)
                turn_model = fb if triage else _CA.guard_turn_model(srv, turn_model, agent, fb)
        except Exception:
            pass
        return turn_model, auth

    def blurp_message(self, sid: str, text: str, user_info: dict) -> dict:
        """BLURP'S PRIVATE CHAT (#337, the PM with Blurp): the person's message
        is stored and shown like any other (the caller stored and announced
        it), but no model turn runs and the voice does not answer -- the
        membraneOnly path without the voice. The field hears it, tagged
        blurp:<person> (Blurp's world grows by what it mentions)."""
        try:
            from . import chat_io as _CIO
            _cap = getattr(getattr(self, "srv", None), "capturer", None)
            if _cap is not None and str(text or "").strip():
                _owner = str((self.sessions.get(sid) or {}).get("owner") or "")
                from . import capture as _CAPMOD
                with _CAPMOD.conversation(f"sid:{sid}"):    # #429: this chat
                    _cap.on_turn(text, session=_CIO.blurp_tag(_owner))
        except Exception:
            pass
        self._busy.pop(sid, None)
        _row = (self.sessions.sessions or {}).get(sid)
        if _row is not None:
            _row["person_ts"] = int(time.time() * 1000)   # the person chatted here (chat_io.recent_person)
        self.sessions.touch(sid)
        self.hub.publish({"type": "session.status",
                          "status": {"type": "idle"}, "sessionID": sid})
        self.hub.publish({"type": "session.idle", "sessionID": sid})
        return {"info": dict(user_info or {}), "parts": []}

    def run(self, sid: str, text: str, user_info: dict | None = None,
            user_parts: list | None = None, _drained: bool = False) -> dict:
        """#576: the turn writes as the model, in the chain that woke it (a
        loop's event) or a new one a person's message starts."""
        from . import writes as _W
        with _W.turn(sid):
            from . import ch_blurp as _CHB             # #576 H: this turn's chain is this conversation's
            _CHB.note_turn((_W.current() or {}).get("id"), "sid:" + str(sid))
            return self._run(sid, text, user_info=user_info, user_parts=user_parts, _drained=_drained)

    def _run(self, sid: str, text: str, user_info: dict | None = None,
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
        if (self.sessions.get(sid) or {}).get("blurp"):
            return self.blurp_message(sid, text, user_info)   # #337: no turn in Blurp's private chat
        # THE REFERENT (convgraph.before_turn): "delete it" with two things "it" could be is
        # asked back in the chat and this turn does not run; the person's reply reads normally
        try:
            _psrv = getattr(self, "srv", None)
            from . import convgraph as _CG
            if _psrv is not None and not _drained and _CG.is_chat((self.sessions.get(sid) or {}).get("agent")):
                _ref = _CG.before_turn(_psrv, self, sid, user_info, text)
                _psrv.__dict__.setdefault("_cg_turn", {})[sid] = _ref or {}   # the capture binding reads it
                if _CG.from_person(text):                                      # living language compiles it
                    _psrv.__dict__.setdefault("_cg_text", {})[sid] = _CG._words(text)[1]
                else:
                    (_psrv.__dict__.get("_cg_text") or {}).pop(sid, None)
                if _ref and _ref.get("reask"):
                    return {"info": user_info, "parts": [], "reask": _ref["reask"]}
        except Exception as e:
            print(f"[convgraph] referent {sid[:16]}: {e}", file=sys.stderr)
        # OUT OF TOKENS (quota.py): the turn's model is paused -- a person's
        # message waits for it; a loop's turn is not run (its event waits)
        _qh = self._quota_hold(sid, text, user_info, user_parts)
        if _qh is not None:
            return _qh
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
        ended = False
        try:
            out = self._run_inner(sid, text, user_info, user_parts)
            ended = True
            # the model ran out during the turn: a person's message waits
            # (held before the drain below runs the next queued one)
            _q = out.get("quota") if isinstance(out, dict) else None
            if _q and _lane_of((self._agents or {}).get(sid)) != "listener":
                self._hold(sid, text, user_info, user_parts, _q)
            return out
        finally:
            self._turn_sockets.pop(sid, None)
            self._turn_activity.pop(sid, None)
            (getattr(self, "_idle_turns", None) or set()).discard(sid)
            self._current_turn_sid = None
            stop.set()
            self._turn_stops.pop(sid, None)
            guard.release()
            self._busy.pop(sid, None)
            (getattr(self, "_aborting", None) or set()).discard(sid)
            # A TURN THAT RAISED ENDS IDLE TOO (#238): its body never reached
            # the idle publish, and the page sat on "working" while
            # /session/status said idle (a queued turn's own busy follows)
            if not ended:
                self.hub.publish({"type": "session.status",
                                  "status": {"type": "idle"}, "sessionID": sid})
                self.hub.publish({"type": "session.idle", "sessionID": sid})
            # THE DRAIN: the next queued turn runs NOW (own thread --
            # this thread is the synchronous lane returning a result).
            self._start_next(sid)

    # -- out of tokens (quota.py) ---------------------------------------------

    def _quota_hold(self, sid: str, text: str, user_info, user_parts):
        """The turn's model is paused: a person's message is HELD (None:
        not paused, run it). A loop's turn answers {quota} unrun -- the
        drainer puts its event back (watch_agents)."""
        try:
            self._agents[sid] = self._turn_agent(sid, user_info)
            model = self._turn_model(sid, text)[0]
        except Exception:
            return None
        ent = _QUOTA.paused(model)
        if ent is None:
            return None
        if _lane_of(self._agents.get(sid)) == "listener":
            return {"info": dict(user_info or {}), "parts": [], "quota": ent}
        return self._hold(sid, text, user_info, user_parts, ent)

    def _hold(self, sid: str, text: str, user_info, user_parts, ent: dict) -> dict:
        model = str(ent.get("model") or "")
        mid = str((user_info or {}).get("id") or "")
        with self._held_lock:
            q = self._held.setdefault(sid, [])
            if not any(h["mid"] == mid and mid for h in q):
                q.append({"sid": sid, "mid": mid, "text": text, "model": model,
                          "user_info": user_info, "user_parts": user_parts,
                          "ts": time.time()})
            else:
                for h in q:
                    if h["mid"] == mid:
                        h["model"] = model
        print(f"[serve] {sid}: {model} is out of tokens -- the message waits "
              f"(resumes {_QUOTA.hhmm(ent.get('resumes_at'))})", file=sys.stderr)
        self.hub.publish({"type": "membrane.quota", "why": "held", "model": model,
                          "sessionID": sid})
        self.hub.publish({"type": "session.status", "status": {"type": "idle"},
                          "sessionID": sid})
        return {"status": "held", "sessionID": sid, "model": model,
                "resumes_at": ent.get("resumes_at"), "info": dict(user_info or {}),
                "parts": []}

    def held(self, model: str | None = None) -> list[dict]:
        """The held messages (oldest first), for one model or all:
        {sessionID, messageID, model, text, title, ts}."""
        with self._held_lock:
            rows = [h for q in self._held.values() for h in q
                    if model is None or h["model"] == model]
        rows.sort(key=lambda h: h["ts"])
        out = []
        for h in rows:
            title = ""
            try:
                title = str((self.sessions.get(h["sid"]) or {}).get("title") or "")
            except Exception:
                pass
            out.append({"sessionID": h["sid"], "messageID": h["mid"], "model": h["model"],
                        "text": str(h["text"] or "")[:200], "title": title, "ts": h["ts"]})
        return out

    def _drop_held(self, sid: str) -> bool:
        with self._held_lock:
            return bool(self._held.pop(sid, None))

    def release_held(self, model: str, one: bool = False) -> int:
        """Send what waited on `model`: every held message (each session's
        in order, on its own thread), or only the oldest (`one`: the probe
        -- its answer, or its failure, decides for the rest)."""
        with self._held_lock:
            mine = sorted((h for q in self._held.values() for h in q if h["model"] == model),
                          key=lambda h: h["ts"])
            if one:
                mine = mine[:1]
            for h in mine:
                self._held[h["sid"]].remove(h)
                if not self._held[h["sid"]]:
                    self._held.pop(h["sid"], None)
        by_sid: dict = {}
        for h in mine:
            by_sid.setdefault(h["sid"], []).append(h)

        def _go(items):
            for h in items:
                try:
                    self.run(h["sid"], h["text"], user_info=h["user_info"],
                             user_parts=h["user_parts"], _drained=True)
                except Exception as e:
                    print(f"[serve] a held message failed to run: {e}", file=sys.stderr)
        for items in by_sid.values():
            threading.Thread(target=_go, args=(items,), daemon=True,
                             name="quota-release").start()
        return len(mine)

    def _on_quota(self, model: str, why: str) -> None:
        """quota.py's news: the page re-reads; a model free again sends what
        waited on it (a probe sends the oldest only)."""
        try:
            self.hub.publish({"type": "membrane.quota", "why": why, "model": model})
        except Exception:
            pass
        if why in ("reset", "resumed", "cleared"):
            self.release_held(model)
        elif why == "probe":
            self.release_held(model, one=True)

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

    def interject(self, sid: str, text: str, user_info, user_parts) -> bool:
        """A word for the RUNNING turn: queued for its
        next round only while a turn runs. Nothing running: dropped, False
        -- its enqueue used to publish busy·queued after the turn it meant
        to join was over, and nothing ever drained it (#238)."""
        if sid not in self._busy:
            return False
        self._enqueue(sid, text, user_info, user_parts, threading.Event())
        if sid not in self._busy:
            # the turn ended between the look and the enqueue: its drain
            # may have run already -- this one runs as its own turn (idle after)
            g = self._guard(sid)
            if g.acquire(blocking=False):
                g.release()
                self._start_next(sid)
        return True

    def _take_interjections(self, sid: str, aid: str) -> list:
        """Pop every queued message of this session for the RUNNING turn:
        their rows are stored + announced already (the door); their
        waiters are released with a 'merged' result."""
        with self._queue_lock:
            q = self._queue.get(sid) or []
            taken, self._queue[sid] = list(q), []
        out = []
        for ent in taken:
            # a job's continuation carries its result as jobText (no stored row)
            out.append((str(ent.get("text") or (ent.get("user_info") or {}).get("jobText") or ""),
                        ent.get("user_info") or {}))
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
    # THE IDLE GUARD (a claude-agent turn works like Claude Code, the
    # operator 2026-10-23: "if the model loops, that's not our problem but
    # the model's"): the whole SDK run is ONE round, so the round's touch
    # never moved the clock and the watchdog cut turns still working (284 s
    # Claude turns, 10-22). A claude-agent turn has no wall clock and no
    # lifetime cap: every event its stream publishes touches the activity
    # clock, and it aborts only after TURN_IDLE_S with no event at all. A
    # silent tool call is silence (no keep-alive, the operator 2026-10-24):
    # the tools note tells Claude to detach long commands.
    TURN_IDLE_S = 300.0      # approved 2026-10-23 (the operator)

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
            idle = sid in (getattr(self, "_idle_turns", None) or set())
            limit = self.TURN_IDLE_S if idle else self.TURN_MAX_S
            if now - last >= limit:
                break                          # A REAL hang: 300s of silence
            if now >= cap and not idle:
                break                          # the forever-turn's absolute cap
            if self._stop_wait(sid, min(1.0, max(limit / 10.0, 0.01))):
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
        print(f"[serve] turn {sid} STILL RUNNING "
              + (f"with no event for {self.TURN_IDLE_S}s " if idle
                 else f"after {self.TURN_MAX_S}s ")
              + "-- the turn watchdog aborts (the graveyard covenant)",
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
        if idle:
            self._stream_error_part(sid, f"no event for {self.TURN_IDLE_S}s "
                                       "-- aborted by the turn watchdog's "
                                       "idle guard (a hang, not your message)")
            return
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

    def _prefetch_reads(self, tcs, lane, sid: str = "") -> dict:
        """{call_id: (resolved name, Future)} for the round's leading
        read-only calls (two or more), started together."""
        from concurrent.futures import ThreadPoolExecutor
        from . import toolset as _ts
        lead = []
        for tc in tcs or []:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            try:
                a = json.loads(fn.get("arguments") or "{}")
                name_l, a = _ts.resolve(name, a if isinstance(a, dict) else {})
            except Exception:
                break
            if name == "help" or name_l not in PARALLEL_READS or _lane_refusal(name_l, lane) is not None:
                break
            lead.append((tc.get("id") or "", name_l, a))
        if len(lead) < 2:
            return {}
        pool = ThreadPoolExecutor(max_workers=min(PARALLEL_READS_MAX, len(lead)), thread_name_prefix="read")
        from . import jobs as _jobs
        from . import writes as _W
        out = {cid: (nl, pool.submit(_W.carry(_jobs.dispatch), self, sid, nl, a, cid,   # #576: the turn's chain
                                     lambda aa, _n=nl, _c=cid: self.executor(_n, aa, _c)))
               for cid, nl, a in lead}
        pool.shutdown(wait=False)
        return out

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

    ABORT_PREARM_S = 10.0

    def _run_inner(self, sid: str, text: str, user_info: dict | None = None,
            user_parts: list | None = None) -> dict:
        self._aborting = getattr(self, "_aborting", None) or set()
        # a Stop pressed with nothing running lapses (see abort)
        _ats = (getattr(self, "_abort_ts", None) or {}).pop(sid, None)
        if _ats is not None and time.time() - _ats > self.ABORT_PREARM_S:
            self._aborting.discard(sid); (getattr(self, "_preempted", None) or set()).discard(sid)
        now = int(time.time() * 1000)
        _t_turn = time.time()
        self._busy[sid] = {"type": "busy"}
        # THE TURN'S JOB DEPTH (jobs.py): a turn a finished job woke carries it;
        # a loop pass on a job-done event set it before its turn
        try:
            from . import jobs as _jobs
            _d = (user_info or {}).get("jobDepth")
            _jobs.DEPTH[sid] = int(_d if _d is not None else _jobs.PENDING_DEPTH.pop(sid, 0))
        except Exception:
            pass
        # THE TURN'S TRIGGER (outcome learning: a goal planted in an operator's
        # turn is the operator's; in a continuation or a loop it is the seat's own)
        try:
            self._turn_origin = getattr(self, "_turn_origin", None) or {}
            self._turn_origin[sid] = turn_origin(text, user_info, user_parts,
                                                 (self._agents or {}).get(sid))
        except Exception:
            pass
        # THE OPERATOR MOVES (2026-10-11 sweep: membrane::operator was never
        # planted or moved, so every watch op relative to the operator -- the
        # default rel -- was dead): a person's message drifts it, off the turn's path
        if (getattr(self, "_turn_origin", None) or {}).get(sid) == "operator":
            _srv = getattr(self, "srv", None)
            _est = getattr(_srv, "entity_store", None)
            if _est is not None:
                def _op_move(_t=text, _est=_est, _srv=_srv):
                    try:
                        from .watch_agents import operator_on_message
                        from .speakers import split_speaker
                        operator_on_message(_est, split_speaker(str(_t or ""))[1] or _t,
                                            embedder=getattr(getattr(_srv, "membrane", None), "embedder", None))
                    except Exception:
                        pass
                threading.Thread(target=_op_move, daemon=True, name="operator-move").start()
        # THE AGENT REGISTRY (the lane gate's input): THE TURN'S OWN AGENT WINS (the attached TUI
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
        history = self._prior_history(sid, str(user_info.get("id") or ""),
                                      anchor=bool(user_info.get("continueTurn"))) \
            if user_info else []
        # A CONTINUATION (a finished job's turn, jobs.py; auto-chat's rounds
        # used it until #348): no message at all; the next round starts
        # from the conversation as it stands, his own last reply last
        if not user_info.get("continueTurn"):
            history.append({"role": "user", "content": text})
        elif user_info.get("jobText"):
            # jobs.py: a finished job's result, the turn's input (its card is a tool box)
            history.append({"role": "user", "content": str(user_info["jobText"])})
        # MEMBRANE'S OWN VOICE (the third participant): it speaks first,
        # in its own thread, outside the session's messages
        _aim_text = text
        try:
            from . import voice as _voice
            if not user_info.get("voiceTurn"):       # the voice just spoke: it IS this turn
                _voice.on_turn_start(self, sid, _aim_text, str(user_info.get("id") or ""),
                                     _lane_of((self._agents or {}).get(sid)))
        except Exception:
            pass

        # MEMBRANE ONLY (membrane_only: the old HA panel's LLM switch
        # off -- the seat's own chat has no such switch since #318): the voice
        # answers, the model is never called; the voice still hears the operator
        if user_info.get("membraneOnly"):
            # the field hears the operator either way (the replay path's
            # precedent: the message alone, no model prose -- and never
            # the voice's own line)
            try:
                _cap = getattr(getattr(self, "srv", None), "capturer", None)
                if _cap is not None:
                    from . import capture as _CAPMOD
                    with _CAPMOD.conversation(f"sid:{sid}"):    # #429: this chat
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
        try:
            _turn_pick = self._turn_model(sid, text)[0]
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
        _quota_hit = None                  # the model ran out mid-turn (quota.py)
        # THE REPEATED REFUSAL (issue #274): qwen3:14b sent the same nameless
        # call seven rounds running, each refused the same way, and the person
        # waited ten minutes for nothing. A call refused before in this turn is
        # told so, and the next round goes out without tools: it can only answer.
        _refused: set = set()
        _text_only = False
        while not err_done and rounds < MAX_ROUNDS:
            rounds += 1
            self._turn_activity[sid] = time.time()   # THE ROUND'S TOUCH
            if rounds > 1:
                # THE OPERATOR SPEAKS INTO THE RUNNING TURN (2026-10-08):
                # messages queued since the last round join this turn's
                # history -- the model's next step sees them (a 9-minute
                # turn kept four of them waiting, the hint it needed among
                # them); each is consumed, never replayed as its own turn
                # A TURN BEING CUT TAKES NO ONE'S WORDS (2026-10-11, "I don't see the
                # answer"): a person's message preempts the running turn AND sits in
                # the queue -- the cut turn's next round used to absorb it as an
                # interjection, then stop: the message was consumed, never answered.
                # Cut, it stays queued: the drain runs it as its own turn next.
                _said = [] if sid in self._aborting else self._take_interjections(sid, aid)
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
            # THE TURN'S MODEL (_turn_model: a person's chat -> its
            # session's model; a loop -> the Goal worker's), read every
            # round, so a switch lands on the next round
            try:
                turn_model, _turn_auth = self._turn_model(sid, text)
            except Exception:
                turn_model, _turn_auth = self.model, None
            # a claude-agent turn runs on the idle guard, not the wall clock
            try:
                from . import claude_agent as _CA
                if not hasattr(self, "_idle_turns"):
                    self._idle_turns = set()
                if _CA.is_claude_rung(getattr(self, "srv", None), turn_model):
                    self._idle_turns.add(sid)
                else:
                    self._idle_turns.discard(sid)
            except Exception:
                pass
            # MEMBRANE PREDICTS THIS MOVE (predict.py): every round the seat takes
            # is guessed before the model runs -- a virtual action, never run
            try:
                from . import predict as _predict
                _predict.step_start(self, sid, _predict.context_of(history, _aim_text),
                                    _lane_of((self._agents or {}).get(sid)), step=rounds, mid=aid)
            except Exception:
                pass
            try:                           # THE SCORE (score.py): a model round, per lane
                from . import score as _score
                _score.note("round", lane=_lane_of((self._agents or {}).get(sid)))
            except Exception:
                pass
            _thinking(self, sid, "start")      # the orb: this mind is thinking (membrane home)
            body = {"model": turn_model,
                    "messages": ([{"role": "system", "content": self.system}]
                                 if self.system else []) + history}
            if _turn_auth:                 # a mind's own Claude credentials
                from . import claude_agent as _CA
                body[_CA.AUTH_KEY] = _turn_auth
            # THE LANE'S TOOLS (the division of hands): the registry
            # filtered to the session's agent lane (operator/plan/
            # listener). An EXPLICIT tools param = the constructor's own
            # surface (a custom caller's set -- the gate belongs to it;
            # tests + the boot's explicit tool lists keep passthrough).
            if self.tools is not None:
                tools = _openai_tools(self.tools)
            else:
                # IN THE WIRE SHAPE (2026-10-25: the lane's rows went out in
                # the MCP shape -- {name, inputSchema} -- Ollama read no name
                # in them, and a qwen3 loop's every call came back nameless)
                tools = _openai_tools(_openai_tools_for((self._agents or {}).get(sid)))
            lane = _lane_of((self._agents or {}).get(sid))
            if tools and not _text_only:
                body["tools"] = tools
            # A PERSON'S CONTINUE (#389): the first round resumes the capped
            # claude-agent session -- only on a person's lane, only to a
            # claude-agent rung, never by itself
            _resume = (user_info or {}).get("claude_resume") if rounds == 1 else None
            if _resume:
                from . import claude_agent as _CA
                if _CA.is_claude_rung(getattr(self, "srv", None), turn_model) \
                        and _CA.person_agent((self._agents or {}).get(sid)):
                    body[_CA.RESUME_KEY] = str(_resume)
            mkpart({"type": "step-start"})
            live = {"text": None, "reasoning": None, "placed": 0}
            # THE DELTAS, BATCHED (2026-10-11, the operator: "the whole chat dynamics
            # is broken ... idle, offline, flaaaaky"): one event per token was ~100
            # a second -- HA's relay on a memory-starved agdroid fell behind and the
            # hub hung up on it (offline / idle / offline). A part's text now goes
            # out at most every DELTA_COALESCE_S, in order; the rest at the part's end.
            _pend: dict = {}
            # THE HELD BATCH, TIME-BOUNDED (2026-10-24, "Output is cl"): a
            # batch went out only when the next event came -- a tool running
            # sent none, and the last words sat unshown until it finished.
            # A held batch now goes out after DELTA_COALESCE_S by a timer
            # too; the lock keeps the timer and the stream's events in order.
            _plock = threading.RLock()
            _ptimer = {"t": None, "closed": False}

            def _flush(pid, _pend=_pend):
                e = _pend.get(pid)
                if e and e[0]:
                    self.hub.publish({"type": "message.part.delta", "sessionID": sid, "messageID": aid,
                                      "partID": pid, "field": "text", "delta": e[0]})
                    e[0] = ""
                if e:
                    e[1] = time.time()

            def _flush_held(_pend=_pend):
                with _plock:
                    _ptimer["t"] = None
                    if _ptimer["closed"]:
                        return
                    for _k in [k for k in _pend if _pend[k][0]]:
                        _flush(_k)

            def _arm():
                if _ptimer["t"] is None and not _ptimer["closed"]:
                    t = threading.Timer(DELTA_COALESCE_S, _flush_held)
                    t.daemon = True
                    _ptimer["t"] = t
                    t.start()

            def _close_text(_live=live, _pend=_pend):
                # THE REAL SEQUENCE (#467): the words up to a call are their
                # own part, closed here; the call's part follows; the next
                # words open a new part after it
                for other in [k for k in _pend if _pend[k][0]]:
                    _flush(other)
                _lp = _live.get("text")
                if _lp is not None:
                    _flush(_lp["id"])
                    _lp["time"]["end"] = int(time.time() * 1000)
                    self.hub.publish({"type": "message.part.updated",
                                      "part": dict(_lp),
                                      "time": int(time.time() * 1000),
                                      "sessionID": sid})
                    _live["placed"] += len(_lp["text"])
                    _live["text"] = None

            def _on_event(kind, val, _live=live, _pend=_pend):
                if sid in (getattr(self, "_idle_turns", None) or ()):
                    self._turn_activity[sid] = time.time()   # the idle guard's touch
                if kind == "tick":
                    return sid in self._aborting
                if kind == "ext":
                    self._inject_card(sid, val, rounds, mkpart)
                    return False
                with _plock:
                    if kind == "tool_start":
                        # a call Claude STARTED (2026-10-24): the words before
                        # it go out and close now; the call shows running
                        _close_text()
                        _agent_tool_start(val)
                        return False
                    if kind == "tool":
                        # a tool Claude ran in its own loop (#389): shown, never re-run
                        _close_text()
                        _agent_tool_part(val)
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
                    else:
                        _arm()                               # held: out by the timer at the latest
                return False
            _agent_shown: set = set()
            _agent_running: dict = {}            # tool_use id -> its running part
            _agent_started: set = set()

            def _agent_tool_start(t, _run=_agent_running, _shown=_agent_shown):
                """A call the claude-agent rung STARTED (2026-10-24): its part,
                state running, as Claude Code shows it; the result completes
                the same part. {"id", "dropped"}: the hook denied it -- the
                part ends as an error, never left running."""
                from . import claude_agent as _CA
                if not isinstance(t, dict):
                    return
                tid = str(t.get("id") or "")
                if not tid or tid in _shown:
                    return
                now = int(time.time() * 1000)
                tp = _run.get(tid)
                if t.get("dropped"):
                    if tp is not None:
                        _agent_tool_end(tid, str(t["dropped"]))
                    return
                name = str(t.get("name") or "") or (tp or {}).get("tool") or ""
                targs = t.get("input") if isinstance(t.get("input"), dict) else {}
                if tp is None:
                    _agent_started.add(tid)
                    _run[tid] = mkpart({"type": "tool", "callID": tid, "tool": name,
                                        _CA.MARK_KEY: _CA.PROVIDER,
                                        "state": {"status": "running", "input": targs,
                                                  "title": _tool_title(name, targs),
                                                  "time": {"start": now}},
                                        "time": {"start": now}})
                    return
                if targs:                        # the call's input, now complete
                    tp["state"]["input"] = targs
                    tp["state"]["title"] = _tool_title(name, targs)
                    self.hub.publish({"type": "message.part.updated",
                                      "part": copy.deepcopy(tp), "time": now,
                                      "sessionID": sid})

            def _agent_tool_end(tid, why, _run=_agent_running, _shown=_agent_shown):
                """A started call that will get no result: an error, ended."""
                tp = _run.pop(tid, None)
                if tp is None:
                    return
                _shown.add(tid)
                now = int(time.time() * 1000)
                tp["state"] = dict(tp["state"], status="error", error=why[:300],
                                   time={"start": tp["state"]["time"]["start"], "end": now})
                tp["time"]["end"] = now
                self.hub.publish({"type": "message.part.updated",
                                  "part": copy.deepcopy(tp), "time": now,
                                  "sessionID": sid})

            def _agent_tool_part(t, _shown=_agent_shown, _run=_agent_running):
                """One tool the claude-agent rung ran inside the SDK's loop
                (#389): a finished tool part, marked claude-agent -- the
                running part its start made, completed. Never handed to
                this runner's executor: it already ran."""
                from . import claude_agent as _CA
                if not isinstance(t, dict):
                    return
                key = str(t.get("id") or "") or json.dumps(t, sort_keys=True, default=str)
                if key in _shown:
                    return
                _shown.add(key)
                now = int(time.time() * 1000)
                targs = t.get("input") if isinstance(t.get("input"), dict) else {}
                tp = _run.pop(key, None)
                t0 = tp["state"]["time"]["start"] if tp is not None else now
                name = str(t.get("name") or "") or (tp or {}).get("tool") or ""
                st = {"status": "error" if t.get("is_error") else "completed",
                      "input": targs, "title": _tool_title(name, targs),
                      "time": {"start": t0, "end": now}}
                if t.get("is_error"):
                    st["error"] = str(t.get("output") or "")[:300]
                else:
                    st["output"] = str(t.get("output") or "")
                if tp is not None:
                    tp["tool"] = name
                    tp["state"] = st
                    tp["time"] = {"start": t0, "end": now}
                else:
                    tp = mkpart({"type": "tool", "callID": str(t.get("id") or ""),
                                 "tool": name, _CA.MARK_KEY: _CA.PROVIDER, "state": st,
                                 "time": {"start": now, "end": now}})
                self.hub.publish({"type": "message.part.updated", "part": dict(tp),
                                  "time": now, "sessionID": sid})

            def _settle(_pend=_pend, _run=_agent_running):
                # the stream is over: the tail of every part, before it closes;
                # no timer after the round; a call still running got no result
                with _plock:
                    _ptimer["closed"] = True
                    if _ptimer["t"] is not None:
                        _ptimer["t"].cancel()
                        _ptimer["t"] = None
                    for _pid in list(_pend):
                        _flush(_pid)
                    for _tid in list(_run):
                        _agent_tool_end(_tid, "no result: the reply ended before this call finished")
            _stream = getattr(self.call_ladder, "stream", None) if STREAM_ROUNDS else None
            try:
                if _stream is not None:
                    try:
                        resp = _stream(body, _on_event)
                        _settle()
                    except (SeatBusy, _QUOTA.QuotaPaused):
                        _settle()
                        raise                        # busy (#431) or out of tokens: no second (buffered) call
                    except Exception as _se:
                        _settle()
                        if live["text"] is not None or live["reasoning"] is not None \
                                or _agent_started or _agent_shown:
                            raise                    # words or calls already shown: the round failed for real
                        # the lost stream's steps were spent (#472)
                        _tokens_fill(a_info, {"usage": getattr(_se, "membrane_usage", None)})
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
            except _QUOTA.QuotaPaused as e:
                # OUT OF TOKENS (quota.py): the model is paused -- the row
                # says so, the person's message waits for it (run() holds it)
                now = int(time.time() * 1000)
                _tokens_fill(a_info, {"usage": getattr(e, "membrane_usage", None)})
                mkpart({"type": "text",
                        "text": f"⏸ {e} -- your message waits for it",
                        "time": {"start": now, "end": now}})
                _quota_hit = {"model": e.model or turn_model, "resumes_at": e.until,
                              "reason": e.reason}
                a_info[QUOTA_MARK] = {"model": e.model or turn_model, "until": e.until}
                a_info["error"] = {"name": "QuotaPaused", "data": {"message": str(e)[:300]}}
                _thinking(self, sid, "end")
                break
            except SeatBusy as e:
                # THE BUSY SEAT (#431): the turn failed visibly, marked so
                # the chat offers the person's Retry; nothing retries it
                now = int(time.time() * 1000)
                mkpart({"type": "text", "text": str(e)[:300],
                        "time": {"start": now, "end": now}})
                a_info["error"] = {"name": "SeatBusy", "data":
                                   {"message": str(e)[:300]}}
                a_info[BUSY_MARK] = True
                _thinking(self, sid, "end")
                break
            except Exception as e:
                # an aborted round keeps the usage of the steps it finished (#472)
                _tokens_fill(a_info, {"usage": getattr(e, "membrane_usage", None)})
                now = int(time.time() * 1000)
                mkpart({"type": "text",
                        "text": f"model ladder failed: "
                                f"{type(e).__name__}: {e}"[:300],
                        "time": {"start": now, "end": now}})
                a_info["error"] = {"name": "Error", "data":
                                   {"message": str(e)[:300]}}
                _thinking(self, sid, "end")
                break
            _thinking(self, sid, "end")
            msg = ((resp.get("choices") or [{}])[0].get("message")
                   or {})
            # THE CLAUDE-MADE MARK (issue #384): the proxy stamps the
            # answering provider; the row keeps it for provenance (#389:
            # the reply is learned like any model's). The tools Claude ran
            # in its own loop become finished tool parts (the buffered
            # round's; a streamed round showed them as they came).
            from . import claude_agent as _CA
            _ca_round = resp.get(_CA.RESP_KEY) == _CA.PROVIDER
            if _ca_round:
                a_info[_CA.MARK_KEY] = _CA.PROVIDER
                _raw = msg.get("content") or ""
                for _t in resp.get(_CA.TOOLS_KEY) or []:
                    # a buffered reply says where in the words each call came
                    # (`at`, #467): the text before it is a part first
                    _at = _t.get("at") if isinstance(_t, dict) else None
                    if isinstance(_at, int) and live["text"] is None \
                            and _at > live["placed"]:
                        _seg = _raw[live["placed"]:_at]
                        live["placed"] = _at
                        if _seg.strip():
                            _n = int(time.time() * 1000)
                            _sp = mkpart({"type": "text", "text": _seg.strip(),
                                          "time": {"start": _n, "end": _n}})
                            self.hub.publish({"type": "message.part.updated",
                                              "part": dict(_sp), "time": _n,
                                              "sessionID": sid})
                    _agent_tool_part(_t)
                # THE CEILING (#389, #425): stopped at the tool-call
                # ceiling, not an error; the chat offers the person a
                # Continue on this row
                _cap = resp.get(_CA.CAPPED_KEY)
                if isinstance(_cap, dict) and _cap.get("session"):
                    a_info[_CA.CAP_MARK] = {"session": str(_cap["session"]),
                                            "calls": int(_cap.get("calls")
                                                         or _CA.MAX_TOOL_CALLS_PER_ANSWER),
                                            "continued": False}
            _tokens_fill(a_info, resp)
            # A CUT IS NOT AN ANSWER (#449): a round the person's message (or
            # Stop) cut mid-stream, or whose stream dropped, is marked on its
            # row -- the words shown stay, never stored as a finished reply
            _cut = resp.get(CUT_KEY)
            if _cut in CUT_ERRORS:
                a_info["error"] = dict(CUT_ERRORS[_cut])
            # THE INJECTION AS A CARD (2026-10-08, the operator: show it
            # like every tool call): what membrane added to this round --
            # the first round's, and any later round's that differs
            if isinstance(resp.get("membrane"), dict):
                self._inject_card(sid, resp["membrane"], rounds, mkpart)
            tcs = msg.get("tool_calls")
            content = (msg.get("content") or "").strip()
            # THE EMISSION SALVAGE (2026-10-14, the five bare one-liners):
            # a per-turn draw sometimes writes its calls as display
            # bullets ('🧰 **Tool call** (exec): `{...}`') INSTEAD of
            # emitting tool_calls -- nothing ran, no receipt rode back,
            # the turn ended on the text. When the model fired nothing
            # but wrote the bullet shape, the bullets become the round's
            # REAL calls: unique call_salv ids (never a duplicate of a
            # real call id), the model's RAW args text, and the ordinary
            # loop below does the rest -- resolve, gate, run, receipts
            # in-turn; a bad object refuses honestly there, never
            # guesses. Real tool_calls beside bullets stay real: the
            # model already fired, more would double-execute.
            # (a claude-agent round already ran its tools in its own loop:
            # salvaging its words into calls would run them twice)
            if not tcs and self.tools is None and content and not _ca_round:
                try:
                    from . import streams as _ST
                    _salv = _ST.emission_calls(content)
                except Exception:
                    _salv = []
                if _salv:
                    tcs = [{"id": f"call_salv_{secrets.token_hex(6)}",
                            "type": "function",
                            "function": {"name": n, "arguments": a}}
                           for n, a in _salv]
            reason = "tool-calls" if tcs else "stop"
            try:
                from . import predict as _predict
                _predict.step_end(self, sid, list(tcs or []), content, step=rounds, mid=aid)
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
                # PARALLEL READS (2026-10-11): the round's LEADING read-only
                # calls run together; the loop below takes each result in
                # order (a read after a write is not prefetched -- it waits)
                _pre = self._prefetch_reads(tcs, lane, sid) if self.tools is None else {}
                for tc in tcs:
                    fn = tc.get("function") or {}
                    # the blank name goes to the resolver UNMASKED: '?' read
                    # like a real tool name and its refusal pointed at the
                    # wrong place (issue #25)
                    name = fn.get("name") or ""
                    call_id = tc.get("id") or ""
                    _raw_args = fn.get("arguments")
                    _args_bad = ""
                    args = {}
                    try:
                        args = json.loads(_raw_args or "{}")
                        if not isinstance(args, dict):
                            _args_bad, args = str(_raw_args or "")[:160], {}
                    except json.JSONDecodeError:
                        # never silently emptied (issue #12): the model got
                        # back refusals for calls it DID see run-less
                        _args_bad = str(_raw_args or "")[:160]
                    disp_args = args
                    tstart = int(time.time() * 1000)
                    part = mkpart({"type": "tool", "callID": call_id,
                                   "tool": name,
                                   "state": {"status": "pending",
                                             "input": args,
                                             "raw": json.dumps(args)},
                                   "time": {"start": tstart}})
                    self.messages.append(sid, a_info, list(parts))
                    # A PERSON'S MESSAGE LETS THE ROUND FINISH (2026-10-12, "not run: the
                    # turn was cut" on every voice message): the round's calls are short
                    # now (jobs.py: reads answer from the store, slow runs become jobs) --
                    # they all run, and the turn hands over before its next model round.
                    # Stop still stops here.
                    if sid in self._aborting and sid not in (getattr(self, "_preempted", None) or set()):
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
                                    if _args_bad:
                                        raise ValueError(
                                            f"the call's ARGUMENTS did not parse "
                                            f"({_args_bad}): that field takes a "
                                            f"json object -- the call never ran")
                                    name_l, args = _ts.resolve(name, args)
                                except ValueError as e:
                                    _bad, name_l = str(e), name
                        else:
                            name_l = name
                        if _help is not None or _bad is not None:
                            _refusal = None
                        else:
                            _refusal = (None if self.tools is not None
                                        else (_lane_refusal(name_l, lane)
                                              or _graph_refusal(name_l, (self._agents or {}).get(sid))))
                        if _help is not None:
                            output = _help
                        elif _bad is not None:
                            output = f"Error: {_bad}"
                            _key = (name, json.dumps(args, sort_keys=True, default=str), _bad)
                            if _key in _refused:
                                output += (" -- this exact call already failed in this turn and was "
                                           "not run again: answer the person now in plain text, from "
                                           "what you already have")
                                _text_only = True
                            _refused.add(_key)
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
                            args = stamp_card_planter(
                                name_l, args, (self._agents or {}).get(sid), sid)
                            # THE FIELD'S PULSE: which mind touched which
                            # entity (ids + the tool name only)
                            try:
                                from .fieldpulse import of as _pulse_of
                                _fp = _pulse_of(getattr(self, "srv", None))
                                if _fp is not None:
                                    _fp.touch((self._agents or {}).get(sid), name_l, args)
                            except Exception:
                                pass
                            # GOAL PROVENANCE (outcome learning): a plant carries its turn's trigger
                            if name_l.endswith("intend"):
                                try:
                                    from .lanes import note_plant_origin
                                    note_plant_origin(args.get("text") or args.get("goal") or "",
                                                      (getattr(self, "_turn_origin", None) or {}).get(sid) or "model")
                                except Exception:
                                    pass
                            # CONSEQUENCES (consequences.py): a write is a decision that acted
                            try:
                                from . import consequences as _CQ
                                if _CQ.is_write(name_l, args):
                                    from .fieldpulse import touched_eid as _teid
                                    _e = _teid(name_l, args)
                                    if _e:
                                        _act = str((args or {}).get("action") or "")
                                        _CQ.record(getattr(self, "srv", None), _e,
                                                   f"{name_l}{'(' + _act + ')' if _act else ''}",
                                                   who=(self._agents or {}).get(sid) or "chat",
                                                   session=sid, kind=name_l)
                            except Exception:
                                pass
                            _p = _pre.get(call_id)
                            if _p is not None and _p[0] == name_l:
                                output = _p[1].result()
                            elif self.tools is None:
                                # jobs.py: reads answer from the store, slow runs and a
                                # define's first fire become jobs whose results are events
                                from . import jobs as _jobs
                                output = _jobs.dispatch(self, sid, name_l, args, call_id,
                                                        lambda a, _n=name_l, _c=call_id: self.executor(_n, a, _c))
                            else:
                                output = self.executor(name_l, args, call_id)
                            output = _claim_note(getattr(self, "srv", None), (self._agents or {}).get(sid),
                                                 name_l, args, output)
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
                        "cost": 0, "tokens": _step_tokens(a_info, history, parts)})
                continue
            # the words after the last closed part (#467; all of them when no
            # call split the reply)
            _tail = (msg.get("content") or "")[live["placed"]:].strip() \
                if live["placed"] else content
            if _tail:
                now = int(time.time() * 1000)
                # real serve streams the text part: it opens EMPTY with
                # time.start, the deltas ride, a final part.updated closes
                # it with time.end
                part = live["text"]
                if part is None:
                    part = mkpart({"type": "text", "text": "",
                                   "time": {"start": now}})
                    part["text"] = _tail
                    self.hub.publish({"type": "message.part.delta",
                                      "sessionID": sid, "messageID": aid,
                                      "partID": part["id"], "field": "text",
                                      "delta": _tail})
                else:
                    part["text"] = _tail
                part["time"]["end"] = int(time.time() * 1000)
                self.hub.publish({"type": "message.part.updated",
                                  "part": dict(part),
                                  "time": int(time.time() * 1000),
                                  "sessionID": sid})
            a_info["finish"] = "stop"
            mkpart({"type": "step-finish", "reason": "stop", "cost": 0,
                    "tokens": _step_tokens(a_info, history, parts)})
            break
        if "error" in a_info:
            a_info.pop("finish", None)
        if not (a_info.get("tokens") or {}).get("total"):
            a_info["tokens"] = _tokens_estimate(history, parts)
        # A TOOL CALL THAT NEVER RAN IS CLOSED (2026-10-11: a cut turn left an
        # `exec` 'pending' forever -- the operator saw it hang for minutes)
        _cut_now = int(time.time() * 1000)
        for _p in parts:
            _st = _p.get("state") if _p.get("type") == "tool" else None
            if isinstance(_st, dict) and _st.get("status") in ("pending", "running"):
                _st.update({"status": "error",
                            "error": "not run: the turn was cut before this call (a person spoke, or Stop)",
                            "time": {"start": (_st.get("time") or {}).get("start") or _cut_now, "end": _cut_now}})
                self.hub.publish({"type": "message.part.updated", "part": copy.deepcopy(_p),
                                  "time": _cut_now, "sessionID": sid})
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
        self._aborting.discard(sid); (getattr(self, "_preempted", None) or set()).discard(sid)
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
        # THE TURN ENDS IDLE (#238): the stream says what /session/status
        # says. The cosmetic busy (2026-10-03: the main chat never reported
        # idle, so the event-thinkers' lines read as one ongoing loop) left
        # the web chat on "working" forever while the seat itself said idle;
        # an inject lands as its own part whether the session shows busy or not.
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
                               _lane_end, parts=parts)
            if _lane_end not in _voice._CHAT_LANES:      # the chat's guess rides its voice line
                from . import predict as _predict
                threading.Thread(target=_predict.end, args=(self, sid, list(parts)),
                                 daemon=True, name="predict-end").start()
        except Exception:
            pass
        # THE CONVERSATION GRAPH (convgraph): a chat turn's two messages and their edges,
        # the answers to the reply before and the decisions this reply asks -- off the turn
        try:
            _psrv = getattr(self, "srv", None)
            from . import convgraph as _CG
            if _psrv is not None and user_info is not None and \
                    _CG.is_chat((self._agents or {}).get(sid) or (self.sessions.get(sid) or {}).get("agent")):
                from . import writes as _W
                _reply = "\n".join(p.get("text") or "" for p in parts
                                   if isinstance(p, dict) and p.get("type") == "text")
                _ui, _ai = dict(user_info), dict(a_info)
                threading.Thread(target=_W.carry(lambda: _CG.on_turn_end(_psrv, self, sid, _ui, text, _ai, _reply)),
                                 daemon=True, name="convgraph").start()
        except Exception as e:
            print(f"[convgraph] turn {sid[:16]}: {e}", file=sys.stderr)
        if _quota_hit:
            return {"info": a_info, "parts": parts, "quota": _quota_hit}
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
    from . import writes as _W

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
            headers={"Content-Type": "application/json", **_JWT.internal_headers(),
                     **_W.headers()})          # the turn's chain + writer: the handler resumes them (Blurp's learner links writes by chain)
        try:
            with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT_S) as resp:
                return _json.loads(resp.read())
        except urllib.error.HTTPError as he:
            try:
                _eb = _json.loads(he.read() or b"{}")
            except ValueError:
                _eb = None
            busy = _busy_error(_eb)
            if busy:
                raise SeatBusy(busy) from None
            _qp = _QUOTA.from_error_obj(_eb)
            if _qp is not None:
                raise _qp from None              # out of tokens (quota.py)
            raise

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
        # THE DROPPED STREAM (2026-10-11, "aaaand it is stuck"): the backend's
        # stream was cut mid-generation -- the reader just saw the end, the
        # round counted as a silent 'stop', the operator's question got an
        # empty reply and auto-chat stalled on it. A stream that ends without
        # its finish is TRUNCATED: with nothing usable yet it is asked again
        # (STREAM_RETRIES); words already given are kept; a half tool call never runs.
        for _attempt in range(STREAM_RETRIES + 1):
            out = _stream_once(body, on_event)
            if out is not _TRUNCATED_EMPTY:
                return out
            on_event("reasoning", "\n[membrane: the model's stream dropped -- asking again]\n")
        raise ConnectionError(f"the model's stream dropped {STREAM_RETRIES + 1} times with nothing usable")

    def _stream_once(body: dict, on_event):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions", data=_json.dumps(body).encode(),
            method="POST", headers={"Content-Type": "application/json", **_JWT.internal_headers(),
                                    **_W.headers()})       # as the buffered call: the turn's chain + writer
        seen: dict = {}                      # this round's last usage (#472)
        try:
            return _read_stream(req, on_event, seen, body.get("_turn_sid"))
        except Exception as e:
            # A BROKEN ROUND KEEPS ITS COST (#472): the usage the stream had
            # carried so far rides the exception to the turn
            if seen.get("usage") and getattr(e, "membrane_usage", None) is None:
                try:
                    e.membrane_usage = seen["usage"]
                except Exception:
                    pass
            raise

    def _open_round(req, sid):
        """The round's request, its socket registered under the turn BEFORE the proxy
        answers (2026-10-31: a round waiting in the proxy's gate had no socket the
        stop button or the watchdog could close, so the session stayed busy); the
        proxy's backend response replaces it once it exists."""
        import http.client
        _r = runner if runner is not None else ((cell or {}).get("runner") if cell else None)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=CLOUD_TIMEOUT_S)
        socks = getattr(_r, "_turn_sockets", None) if (_r is not None and sid) else None
        if isinstance(socks, dict):
            socks.setdefault(sid, conn)
        try:
            conn.request(req.get_method(), req.selector, body=req.data, headers=dict(req.header_items()))
            resp = conn.getresponse()
        except BaseException:
            conn.close()
            raise
        finally:
            if isinstance(socks, dict) and socks.get(sid) is conn:
                socks.pop(sid, None)
        if resp.status >= 400:
            raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason, resp.headers, resp)
        return resp

    def _read_stream(req, on_event, seen, sid=None):
        content, reasoning, tcs = [], [], {}
        usage, ext, finish = None, None, None
        provider = None                      # the answering provider's stamp (#384)
        agent_tools = []                     # tools the claude-agent rung ran (#389)
        capped = None                        # ...and its ceiling, when hit (#425)
        done = False
        cut = None                           # why the round ended early (#449)
        with _open_round(req, sid) as resp:
            for raw in resp:
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    done = True
                    break
                try:
                    d = _json.loads(payload)
                except ValueError:
                    continue
                if _busy_error(d):
                    raise SeatBusy(_busy_error(d))   # never a truncation: no re-ask (#431)
                _qp = _QUOTA.from_error_obj(d)
                if _qp is not None:
                    raise _qp                        # out of tokens (quota.py): no re-ask
                if isinstance(d.get("membrane"), dict) and not d.get("choices"):
                    ext = d["membrane"]
                    on_event("ext", ext)
                    continue
                if d.get("usage"):
                    usage = seen["usage"] = d["usage"]
                if d.get("membrane_provider"):
                    provider = d["membrane_provider"]
                if isinstance(d.get("membrane_tool_start"), dict):
                    on_event("tool_start", d["membrane_tool_start"])   # a call started
                if isinstance(d.get("membrane_tool"), dict):
                    agent_tools.append(d["membrane_tool"])
                    on_event("tool", d["membrane_tool"])
                if isinstance(d.get("membrane_capped"), dict):
                    capped = d["membrane_capped"]
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
                        # index DEFAULTS TO 0 (the /v1 tee's own rule,
                        # lanes.py: 'int(tc.get("index", 0) or 0)'): a
                        # backend that omits index streams ONE tool call's
                        # chunks -- defaulting to len(tcs) instead spawned a
                        # separate nameless call per argument delta, every
                        # one refused with no tool name (issue #25)
                        i = int(tc.get("index", 0) or 0)
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
                    done = True                  # an abort is not a drop
                    if finish is None:
                        # A CUT IS NOT AN ANSWER (#449): a person spoke (or Stop)
                        # mid-reply -- the words so far are kept but marked cut,
                        # and a half-built tool call never runs
                        cut = "abort"
                        tcs = {}
                    break
        if not done and finish is None:
            if not content:
                return _TRUNCATED_EMPTY          # nothing usable (a half tool call never runs): ask again
            tcs = {}                             # words came: keep them; drop any half-built call
            finish = "length"
            cut = "drop"
        msg = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            msg["reasoning"] = "".join(reasoning)
        if tcs:
            msg["tool_calls"] = [tcs[i] for i in sorted(tcs)]
        out = {"choices": [{"message": msg, "finish_reason": finish or ("tool_calls" if tcs else "stop")}],
               "usage": usage or {}}
        if ext is not None:
            out["membrane"] = ext
        if provider:
            out["membrane_provider"] = provider
        if agent_tools:
            out["membrane_tools"] = agent_tools
        if capped is not None:
            out["membrane_capped"] = capped
        if cut:
            out[CUT_KEY] = cut
        return out
    call.stream = stream
    return call


def mcp_executor(port: int):
    """The default executor: the MCP tool impls themselves -- the same
    relay surface the TUI uses (all guards stay in the proxy). Unknown
    tools are a clean error, never a crash. The local models'
    method_call keeps their short head (mcp_server.LOCAL_TOOL_IMPLS)."""
    def exec_tool(name: str, args: dict, call_id: str = "") -> str:
        impl = _mcp.LOCAL_TOOL_IMPLS.get(name)
        if impl is None:
            raise ValueError(f"unknown membrane tool: {name}")
        return impl(args or {}, port)
    return exec_tool


# ---- the HTTP surface ----------------------------------------------------------

# THE FIELD'S WINDOW (2026-10-08): the seat's READ surfaces the serve relays
# onto the /v1 proxy -- that proxy binds 127.0.0.1 and has write routes; the
# serve passes exactly these, GET only (the HA field dashboard + its sensors:
# state, the universe map, the drawer). In JWT mode (jwtauth.py) the relay
# presents the seat's internal token, and the caller needed its own first.
STREAM_RETRIES = 2
_TRUNCATED_EMPTY = object()          # a dropped stream that gave nothing usable

# the serve's rounds stream from the /v1 proxy (words show as they come);
# MEMBRANE_STREAM_ROUNDS=0 returns to the buffered call
STREAM_ROUNDS = os.environ.get("MEMBRANE_STREAM_ROUNDS", "1") != "0"


def turn_origin(text, user_info=None, parts=None, agent=None) -> str:
    """Who set this turn going: 'operator' (a person's message), 'auto'
    (a continuation / the voice), 'loop' (a watch loop, a goal pulse, a called
    turn) or 'model' (anything else)."""
    ui = user_info or {}
    if _from_person(text, parts):
        return "operator"
    if ui.get("voiceTurn") or ui.get("continueTurn"):
        return "auto"
    if _lane_of(agent) in ("listener", "plan") and str(agent or "") not in ("", "build", "plan"):
        return "loop"
    return "model"


def _from_person(text, parts=None) -> bool:
    """Does this user message come from a PERSON -- the HA proxy's / calld's
    '[HA user: <name>]' speaker line, in the text or a part? (A loop's goal
    pulse, a continuation and membrane's voice carry none.)"""
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
                   "/membrane/map/locate", "/membrane/map/search",
                   "/v1/models",
                   "/membrane/entity/read",
                   "/membrane/entity-related",
                   # the login page's strangers (membrane/strangers.py):
                   # GET, numbers in, shape out
                   "/membrane/stranger/visit", "/membrane/stranger/view",
                   "/membrane/stranger/event", "/membrane/stranger/leave",
                   "/membrane/gate-stages", "/membrane/predict", "/membrane/minds")


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


from . import ui_serve as _UI
from . import diag as _DIAG


def make_handler(hub=None, sessions=None, messages=None, config=None,
                 runner=None, srv=None, **_dep) -> type:
    """The opencode-compatible HTTP handler, parametrized by the running
    seat's stores/runner (a factory because ThreadingHTTPServer wants a
    class). `srv` is the seat itself -- /agent reads the live minds
    (loop pool, thinker, dreamer) off it."""
    hub = hub if hub is not None else SSEHub()
    config = config if config is not None else {}

    class _Handler(_DIAG.TimedHandler, BaseHTTPRequestHandler):   # #346: the timing ring
        protocol_version = "HTTP/1.1"
        seat = srv          # the seat object for the live-minds reads

        def log_message(self, *a):
            pass

        # -- plumbing ---------------------------------------------------------

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

        # -- JWT mode (jwtauth.py; off = every helper here is a no-op) ----------

        def _jwt_door(self) -> bool:
            """The token gate, first in every do_*: Bearer, Basic with the JWT
            as the password (opencode attach, HA), or the UI's cookie. Only the
            standalone page's shell (+ /ui/login) is public."""
            if not _JWT.gate(self, cookie=True, basic=True, public=_UI.public_path(self)):
                return False
            # THE OWNER'S DOOR (#253, seatauth.py): JWT mode off, an OFF-BOX
            # caller's writes need the seat token (Bearer, Basic's password
            # or the UI's cookie); reads and localhost stay open
            return _SA.gate(self, _SA.serve_is_write(self.command, self.path.split("?", 1)[0]),
                            cookie=True, basic=True)

        def _jwt_person(self):
            """The token's person (None: open mode, or the seat's own token)."""
            return _UI.token_person(self)

        def _visible(self, sid: str) -> bool:
            """May this caller see session `sid`? Open mode and the seat's own
            token: every session. A person: theirs, and their sessions' children."""
            who = self._jwt_person()
            if who is None:
                return True
            cur, seen = sid, set()
            while cur and cur not in seen and len(seen) < 16:
                seen.add(cur)
                row = (sessions.sessions or {}).get(cur)
                if row is None:
                    return False
                if row.get("owner"):
                    return row.get("owner") == who.sub
                cur = row.get("parentID")
            return False

        def _session_hidden(self, path: str) -> bool:
            """A /session/<id>[/...] path naming someone else's session (it
            does not exist for this caller: 404, never a 403 that confirms it)."""
            if self._jwt_person() is None or not path.startswith("/session/"):
                return False
            sid = path[len("/session/"):].split("/", 1)[0]
            if not sid or sid == "status":
                return False
            return not self._visible(sid)

        def _event_visible(self, ev: dict) -> bool:
            """The /event filter: a session's events reach its owner only; the
            seat's own news (no session) reaches everyone with a token."""
            sid = ev.get("sessionID")
            for k in ("info", "part", "properties"):
                if sid:
                    break
                v = ev.get(k)
                if isinstance(v, dict):
                    sid = v.get("sessionID")
            return not sid or self._visible(str(sid))

        def _seat_read(self) -> None:
            """GET relay of one SEAT_READ_PATHS read onto the local /v1
            proxy (the field dashboard's window; see seat_read_url)."""
            url = seat_read_url(srv, self.path)
            if url is None:
                return self._notfound()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=_JWT.internal_headers()), timeout=45) as up:
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
            keep = self._event_visible if self._jwt_person() is not None else None
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.close_connection = True
            self.end_headers()
            q = hub.subscribe()
            _UI.note_page()                # an open page is here: no new tab (#243)
            try:
                self._sse_flush(q, wrap=wrap, keep=keep)
            finally:
                hub.unsubscribe(q)

        def _sse_flush(self, q, keepalive_s: float = 15.0,
                       wrap: bool = False, keep=None):
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
                if keep is not None and not keep(ev):
                    continue                     # JWT mode: another person's session
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
            if not self._jwt_door():
                return                       # JWT mode: no valid token, no answer
            if _DIAG.route(self, self.path.split("?", 1)[0]):
                return                       # #346: /membrane/diag/*, localhost only
            if _UI.intercept(self):
                return                       # standalone mode: the UI, its LAN door
            path = self.path.split("?", 1)[0]
            if self._session_hidden(path):
                return self._notfound()      # JWT mode: someone else's session
            if path in SEAT_READ_PATHS:
                return self._seat_read()
            if path == "/global/health":
                self._json_out({"healthy": True, "version": VERSION})
            elif path == "/event":
                self._sse()
            elif path == "/global/event":
                self._sse(wrap=True)
            elif path == "/membrane/ha/subscriptions":
                # HOME ASSISTANT'S PUSH (ha_bridge.py): the entity_ids the seat mirrors
                from . import ha_bridge as _HB
                psrv = getattr(runner, "srv", None) if runner is not None else None
                self._json_out({"entity_ids": _HB.subscriptions(psrv) if psrv is not None else [],
                                "mqtt_topics": _HB.mqtt_topics(psrv) if psrv is not None else []})
            elif path == "/session":
                _all = sessions.list()
                if self._jwt_person() is not None:
                    # JWT MODE: a person's own sessions (and theirs' children)
                    _all = [x for x in _all if self._visible(str(x.get("id") or ""))]
                elif getattr(self, "_ui_relay", False):
                    # STANDALONE'S PAGE (ui_serve.py): a person's own chats, never the
                    # loops' sessions (HA's proxy kept the same per-person list)
                    _me = str(getattr(self, "_ui_speaker", "") or "").lower()
                    _all = [x for x in _all if str(x.get("owner") or "").lower() == _me]
                self._json_out(_all)
            elif path.startswith("/session/") and path.endswith("/message"):
                sid = path[len("/session/"):-len("/message")]
                if sessions.get(sid) is None:
                    return self._notfound()
                # THE LIVE SHIELD (the TUI's actual replay route -- the
                # crash's data-half at the reader): an assistant row
                # without tokens is mended ON THE WIRE (the reducer's
                # findLast never lands on undefined)
                _rows = _chronological(messages.list(sid))
                # #470: a page when asked (?limit=N&before=<message id>), else every row
                _pq = urllib.parse.parse_qs(self.path.partition("?")[2])
                _lim = (_pq.get("limit") or [""])[0]
                if _lim.isdigit() or (_pq.get("before") or [""])[0]:
                    _rows = message_page(_rows, int(_lim) if _lim.isdigit() else None,
                                         (_pq.get("before") or [""])[0])
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
                    if not self._visible(s):
                        continue
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
                    if segs[1] == "predict":
                        # membrane's phantoms: its guesses of the seat's moves (predict.py)
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
            elif path == "/membrane/seat/pause":
                # #348: the nerve pause, as membrane home's ▶ / ⏸ reads it
                from . import seat_panel as _SP
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    return self._json_err(503, "no seat")
                self._json_out(_SP.pause_state(psrv))
            elif path == "/membrane/decisions":
                # #470: the decision board's read (the page calls the serve port)
                from . import decision_map as _DM
                psrv = getattr(runner, "srv", None) if runner is not None else None
                self._json_out(_DM.board(getattr(psrv, "entity_store", None)))
            elif path == "/membrane/goals":
                # #470: the cockpit's goals -- the open Goal entities, the
                # seat's active set (the intent board's selection) first
                from . import decision_map as _DM
                psrv = getattr(runner, "srv", None) if runner is not None else None
                _act = []
                try:
                    from .lanes import _intents_board
                    _ib = _intents_board(psrv) if psrv is not None else None
                    if _ib is not None and _ib.selection is not None:
                        _act = [i.eid for i in _ib.open_intents()]
                except Exception:
                    _act = []                              # no board: the goals, unranked
                self._json_out(_DM.goals(getattr(psrv, "entity_store", None), _act))
            elif path == "/membrane/options":
                # #344: the seat-wide options (options.py) -- the ⚙ window's read
                from . import options as _OPT
                psrv = getattr(runner, "srv", None) if runner is not None else None
                self._json_out(_OPT.read(psrv))            # #363: with Blurp's energy
            elif path == "/membrane/stats":
                # the 📊 window: the seat's tokenomics, read-only (tokenomics.py)
                from . import tokenomics as _TK
                self._json_out(_TK.read(messages))
            elif path == "/membrane/quota":
                # OUT OF TOKENS (quota.py): the paused models and what waits on
                # them -- the chat's popup and membrane home's ⚠ read it
                psrv = getattr(runner, "srv", None) if runner is not None else None
                self._json_out(_QUOTA.status(psrv, runner))
            elif path == "/membrane/self/recent":
                # #230: self's record, newest first and capped -- the 👶
                # panel's activity feed (read-only; the gate above ran first)
                from . import self_loop as _SELF
                from . import self_super as _SUP
                psrv = getattr(runner, "srv", None) if runner is not None else None
                _q = urllib.parse.parse_qs(self.path.partition("?")[2])
                if _SUP.of(psrv) is not None:          # #440 part 4: Blurp's own process
                    return self._json_out(_SUP.of(psrv).view("/recent", n=(_q.get("n") or [30])[0]))
                self._json_out({"lines": _SELF.recent(psrv, (_q.get("n") or [30])[0]) if psrv is not None else []})
            elif path == "/membrane/self/sheet":
                # #249: self's character sheet -- the 👶 panel's numbers,
                # computed from the record + status() + the toybox, cached
                # ~30 s (read-only; the gate above ran first)
                from . import self_sheet as _SHEET
                from . import self_super as _SUP
                _sup = _SUP.of(getattr(runner, "srv", None) if runner is not None else None)
                from . import ch_blurp as _CHB         # #576 D2: the branches, level and code are the seat's VM's
                _psrv = getattr(runner, "srv", None) if runner is not None else None
                if _sup is not None:                   # #440 part 4: Blurp's own process
                    return self._json_out(_CHB.augment(_psrv, _sup.view("/sheet")))
                self._json_out(_CHB.augment(_psrv, _SHEET.sheet(_psrv)))
            elif path == "/membrane/self/card":
                # #283: the zoo's card -- self's name (per seat), level, rung
                # name, mode, last activity; cheap to poll across seats
                # (read-only; the gate above ran first)
                from . import self_sheet as _SHEET
                from . import self_super as _SUP
                _sup = _SUP.of(getattr(runner, "srv", None) if runner is not None else None)
                from . import ch_blurp as _CHB         # #576 D2: its level is the branches granted
                _psrv = getattr(runner, "srv", None) if runner is not None else None
                _card = _sup.view("/card") if _sup is not None else _SHEET.card(_psrv)
                _card = _CHB.augment(_psrv, _card)     # the level and the learner's counts (no-op without a learner)
                self._json_out(_card)
            elif path == "/membrane/self/context":
                # #440 part 2: the snapshot Blurp's own process reads
                # (self_api.context; the in-process loop calls the same code).
                # The seat's own token (or open mode) only: never a person's
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if self._jwt_person() is not None:
                    return self._json_err(403, "for the seat's own token")
                if psrv is None:
                    return self._json_err(503, "no seat")
                from . import self_api as _SAPI
                _q = urllib.parse.parse_qs(self.path.partition("?")[2])
                # a pure read (#586): the learn exchange spends the purse, so it is the POST's
                self._json_out(_SAPI.context(psrv, only=(_q.get("only") or [None])[0],
                                             own=(_q.get("own") or [""])[0] in ("1", "true")))
            elif path == "/membrane/toybox/state":
                # #249: the toy dock's live source -- the toys alone, cheap
                # enough to poll every few seconds (read-only, gated)
                from . import self_sheet as _SHEET
                self._json_out(_SHEET.dock(getattr(runner, "srv", None) if runner is not None else None))
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
            from . import writes as _W
            with _W.from_headers(getattr(self, "headers", None)):     # #576: the caller's chain and writer
                return self._do_POST_body()

        def _do_POST_body(self):
            if not self._jwt_door():
                return                       # JWT mode: no valid token, no answer
            if _DIAG.route(self, self.path.split("?", 1)[0]):
                return                       # #346: /membrane/diag/*, localhost only
            if _UI.intercept(self):
                return                       # standalone mode: the UI, its LAN door
            path = self.path.split("?", 1)[0]
            if self._session_hidden(path):
                return self._notfound()      # JWT mode: someone else's session
            if not getattr(self, "_ui_relay", False) and not _UI.stamp(self):
                return self._json_err(400, "body must be a JSON object")
            if path == "/membrane/rerank":
                # #683: one reranker per machine -- this seat's model scores for
                # the other seats ({scores: null} when it is not ready)
                from . import recall_rerank as _RR
                b = self._read_body()
                b = b if isinstance(b, dict) else {}
                docs = b.get("docs")
                docs = [str(d) for d in docs] if isinstance(docs, list) else []
                return self._json_out({"scores": _RR.serve(str(b.get("text") or ""), docs)})
            if path == "/membrane/toybox/poke":
                # PLAY WITH SELF (#244): the 👶 panel's one write, toybox
                # actions only (toybox.poke refuses anything else, and body).
                # WHO: the token's person (JWT mode), else the page's speaker
                # line (standalone's /ui/serve relay), else the HA relay's
                # stamped `person`, else the operator -- never a name the
                # page put in the body when a stronger line exists
                from . import toybox as _TB
                b = self._read_body()
                if not isinstance(b, dict):
                    return self._json_out({"ok": False, "error": "body must be a JSON object"}, code=400)
                _tok = self._jwt_person()
                if _tok is not None:
                    _by = _tok.sub
                elif getattr(self, "_ui_relay", False):
                    _by = str(getattr(self, "_ui_speaker", "") or "guest")
                else:
                    _by = str(b.get("person") or "")
                out = _TB.poke(str(b.get("toy") or ""), str(b.get("action") or ""), b.get("arg"), by=_by)
                if out.get("ok"):
                    from . import self_sheet as _SHEET           # #249: the dock shows the poke at once
                    _SHEET.forget_dock(getattr(runner, "srv", None) if runner is not None else None)
                return self._json_out(out, code=200 if out.get("ok") else 400)
            if path == "/membrane/self/context":
                # #586: the context WITH the learn exchange (#576 D2): the report
                # settles the purse, so it is a POST (a retried GET would double it)
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if self._jwt_person() is not None:
                    return self._json_err(403, "for the seat's own token")
                if psrv is None:
                    return self._json_err(503, "no seat")
                from . import self_api as _SAPI
                b = self._read_body()
                b = b if isinstance(b, dict) else {}
                _rep = b.get("report")
                return self._json_out(_SAPI.context(
                    psrv, only=b.get("only") or None, own=bool(b.get("own")),
                    report=_rep if isinstance(_rep, dict) and _rep else None))
            if path == "/membrane/self/effects":
                # #440 part 2: Blurp's acts, run in the seat in order
                # (self_api.effects; the in-process loop calls the same code):
                # {"effects": [...]} -> {"results": [...]}. The seat's own
                # token (or open mode) only: never a person's
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if self._jwt_person() is not None:
                    return self._json_err(403, "for the seat's own token")
                if psrv is None:
                    return self._json_err(503, "no seat")
                from . import self_api as _SAPI
                b = self._read_body()
                try:
                    res = _SAPI.effects(psrv, b.get("effects") if isinstance(b, dict) else None)
                except ValueError as e:
                    return self._json_out({"error": str(e)}, code=400)
                return self._json_out({"results": res})
            if path == "/membrane/self/chat":
                # #337: the caller's private chat with Blurp (the PM with
                # Blurp), made on first use -- the session row (marked blurp).
                # WHO, as the poke's: the token's person, the page's speaker
                # line, the HA relay's stamped `person`, else the operator
                from . import chat_io as _CIO
                b = self._read_body()
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    return self._json_err(503, "no seat")
                _tok = self._jwt_person()
                if _tok is not None:
                    _who = _tok.sub
                elif getattr(self, "_ui_relay", False):
                    _who = str(getattr(self, "_ui_speaker", "") or "")
                else:
                    _who = str((b or {}).get("person") or "") if isinstance(b, dict) else ""
                sid = _CIO.blurp_chat(psrv, _who)
                row = sessions.get(sid) if sid else None
                if row is None:
                    return self._json_err(503, "no session store")
                return self._json_out(row)
            if path in ("/membrane/jsexec/register", "/membrane/jsexec/result"):
                # THE SEAT'S JS (jsexec.py): an opted-in tab registers, answers its jobs
                from . import jsexec as _JS
                psrv = getattr(runner, "srv", None) if runner is not None else None
                b = self._read_body() or {}
                if psrv is None:
                    return self._json_out({"ok": False, "error": "no seat"})
                if path.endswith("/register"):
                    return self._json_out(_JS.register(psrv, str(b.get("tab") or ""), str(b.get("sid") or ""),
                                                       str(b.get("person") or ""), str(b.get("device") or ""),
                                                       on=b.get("on") is not False))
                return self._json_out(_JS.result(psrv, b))
            if path in ("/membrane/voice", "/membrane/voice/state"):
                # VOICE IN / OUT (voice_io.py): the page's buttons, the voice log;
                # `person` is the HA user's name, stamped by the relay
                from . import voice_io as _VIO
                psrv = getattr(runner, "srv", None) if runner is not None else None
                b = self._read_body() or {}
                person = str(b.get("person") or "")
                if psrv is None or not person:
                    return self._json_out({"ok": False, "error": "no person"})
                if path.endswith("/state"):
                    return self._json_out({"ok": True, "state": _VIO.set_state(
                        psrv, person, in_on=b.get("in_on"), in_mode=b.get("in_mode"),
                        out_on=b.get("out_on"), device=str(b.get("device") or ""))})
                return self._json_out({"ok": True, **_VIO.read(psrv, person)})
            if path == "/membrane/ha/token":
                # Home Assistant's DELIVERY OF THE SEAT'S HA ACCESS (detached):
                # the pair into ~/.config/membrane/hass.env (0600), read per turn
                from . import ha_bridge as _HB
                b = self._read_body() or {}
                return self._json_out(_HB.save_token(str(b.get("url") or ""), str(b.get("token") or "")))
            if path == "/membrane/ha/states":
                from . import ha_bridge as _HB
                psrv = getattr(runner, "srv", None) if runner is not None else None
                b = self._read_body() or {}
                return self._json_out(_HB.apply(psrv, b.get("states") or []) if psrv is not None else {"updated": 0})
            if path == "/membrane/ha/catalog":
                # HOME ASSISTANT'S CATALOG (ha_bridge.import_catalog): its domains,
                # entities and services become classes, live entities and methods
                from . import ha_bridge as _HB
                psrv = getattr(runner, "srv", None) if runner is not None else None
                b = self._read_body() or {}
                return self._json_out(_HB.import_catalog(psrv, b) if psrv is not None else {"ok": False})
            if path == "/membrane/ha/mqtt":
                # MQTT THROUGH HOME ASSISTANT: the messages on the topics the seat marked
                from . import ha_bridge as _HB
                psrv = getattr(runner, "srv", None) if runner is not None else None
                b = self._read_body() or {}
                return self._json_out(_HB.apply_mqtt(psrv, b.get("messages") or []) if psrv is not None else {"updated": 0})
            if path == "/membrane/field/purge":
                # THE NURSERY GHOSTS OUT (efield.purge_ghosts): the engineer's /
                # operator's door (the serve's credentials), dry_run unless told
                psrv = getattr(runner, "srv", None) if runner is not None else None
                fld = getattr(psrv, "efield", None) if psrv is not None else None
                if fld is None:
                    self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
                b = self._read_body() or {}
                return self._json_out(fld.purge_ghosts(dry_run=b.get("dry_run", True) is not False))
            if path in ("/membrane/seat/event", "/membrane/seat/restart-ask"):
                # restarts.py: HA says it started; the engineer asks before an HA restart
                from . import restarts as _RS
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
                b = self._read_body() or {}
                if path.endswith("/event"):
                    return self._json_out(_RS.on_event(psrv, b))
                return self._json_out(_RS.ask(psrv, str(b.get("what") or "Home Assistant"),
                                              str(b.get("reason") or ""), int(b.get("in_s") or 60)))
            if path == "/membrane/seat/control":
                # the operator's switches (HA admins only, through the relay)
                from . import seat_panel as _SP
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
                b = self._read_body() or {}
                b = b if isinstance(b, dict) else {}
                # #299: who, for the record (an accept line says it): the token's
                # person, the standalone page's speaker, the HA relay's admin
                _tok = self._jwt_person()
                _by = _SP.caller(token_sub=_tok.sub if _tok is not None else None,
                                 relay_speaker=getattr(self, "_ui_speaker", "") if getattr(self, "_ui_relay", False) else None,
                                 body=b)
                return self._json_out(_SP.control(psrv, b, by=_by))
            if path == "/membrane/seat/pause":
                # #348: membrane home's ▶ / ⏸ -- {"paused": true|false} pauses or
                # resumes the seat's acting lanes (lanes.nerve_paused), the typed
                # pause's audit line with who; never on disk (a restart carries it, #395). The owner's door above
                # gates it like every write (off-box: the seat token)
                from . import seat_panel as _SP
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    return self._json_err(503, "no seat")
                b = self._read_body()
                if not isinstance(b, dict) or not isinstance(b.get("paused"), bool):
                    return self._json_out({"error": "the body is {\"paused\": true|false}"}, code=400)
                _tok = self._jwt_person()
                _by = _SP.caller(token_sub=_tok.sub if _tok is not None else None,
                                 relay_speaker=getattr(self, "_ui_speaker", "") if getattr(self, "_ui_relay", False) else None,
                                 body=b)
                return self._json_out(_SP.set_pause(psrv, b["paused"], by=_by))
            if path in ("/membrane/decision/answer", "/membrane/decision/chat", "/membrane/decision/drop"):
                # #470: the board's writes, behind the owner's door above like
                # every write: an answer (decision_map.answer) or a card's chat
                from . import decision_map as _DM
                b = self._read_body()
                if not isinstance(b, dict):
                    return self._json_out({"error": "a JSON object body is required"}, code=400)
                psrv = getattr(runner, "srv", None) if runner is not None else None
                est = getattr(psrv, "entity_store", None)
                if path.endswith("/answer"):
                    res = _DM.answer(est, getattr(psrv, "user_ledger", None), str(b.get("card") or ""),
                                     b.get("value"), conv=str(b.get("conv") or ""),
                                     message=str(b.get("message") or ""),
                                     work=b.get("work") if isinstance(b.get("work"), bool) else None)
                elif path.endswith("/drop"):
                    res = _DM.drop(est, str(b.get("card") or ""))
                else:
                    res = _DM.set_chat(est, str(b.get("card") or ""), str(b.get("chat") or ""))
                if not res.get("ok"):
                    return self._json_out({"error": res.get("error")}, code=409 if res.get("conflict") else 400)
                return self._json_out(res)
            if path == "/membrane/quota/resume":
                # OUT OF TOKENS: a person's Resume now -- the model's pause is
                # cleared (more tokens, another account) and what waited goes;
                # if it fails again the pause comes back with the new reset
                b = self._read_body()
                model = str((b or {}).get("model") or "").strip() if isinstance(b, dict) else ""
                if not model:
                    return self._json_out({"error": "model required"}, code=400)
                resumed = _QUOTA.resume(model)
                if not resumed and runner is not None and hasattr(runner, "release_held"):
                    resumed = runner.release_held(model) > 0   # nothing paused, something waited
                psrv = getattr(runner, "srv", None) if runner is not None else None
                return self._json_out(dict(_QUOTA.status(psrv, runner), resumed=bool(resumed)))
            if path == "/membrane/options":
                # #344: the ⚙ window's write -- the writable options only,
                # validated (a 400 with the reason; nothing written), then
                # settings.json; the owner's door above gates it like every write
                from . import options as _OPT
                try:
                    psrv = getattr(runner, "srv", None) if runner is not None else None
                    return self._json_out(_OPT.apply(self._read_body(), srv=psrv))
                except ValueError as e:
                    return self._json_out({"error": str(e)}, code=400)
            if path == "/membrane/models":
                # THE MINDS' MODELS: the watch loops' and the background
                # cortex's models, live (the chat picker sets one session)
                from . import minds_models as _MM
                b = self._read_body()
                psrv = getattr(runner, "srv", None) if runner is not None else None
                if psrv is None:
                    self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
                known = _MM.pickable(psrv)     # the served models + the claude-agent rungs
                try:
                    out = _MM.apply(psrv, loops=b.get("loops"), cortex=b.get("cortex"),
                                    think=b.get("think"), known=known or None,
                                    loops_auth=b.get("loops_auth"),
                                    cortex_auth=b.get("cortex_auth"))
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
                _who = self._jwt_person()
                if _who is not None or getattr(self, "_ui_relay", False):   # the session is this person's
                    _row = sessions.sessions.get(s["id"])
                    if _row is not None:
                        _row["owner"] = s["owner"] = (_who.sub if _who is not None
                                                      else str(getattr(self, "_ui_speaker", "") or ""))
                        sessions.flush()
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
            # A PERSON'S CONTINUE (#389): the chat's button on a capped
            # claude-agent answer; claimed once, refused on a loop's session
            _cont = None
            from . import claude_agent as _CA
            if b.get(_CA.CONTINUE_FIELD):
                try:
                    _cont = (str(b[_CA.CONTINUE_FIELD]),
                             runner.claude_continue(sid, str(b[_CA.CONTINUE_FIELD])))
                except _CA.ContinueRefused as e:
                    return self._json_err(409, str(e))
            user_info, row = self._record_user(sid, text, b)
            if _cont is not None:
                user_info[_CA.CONTINUE_FIELD], user_info[_CA.RESUME_KEY] = _cont
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
            # #337: Blurp's private chat -- no speaker lane, no model turn
            if (sessions.get(sid) or {}).get("blurp"):
                runner._announce_user(sid, user_info, row["parts"])
                result = runner.blurp_message(sid, text, user_info)
                if async_:
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self._json_out({"info": result["info"], "parts": []})
            # THE SPEAKER LANE (speaker.py): a spoken message runs in the seat's
            # speaker session, beside the chat -- it never cuts the chat's work
            _routed = None
            _interim = None
            try:
                from . import speaker as _SPK
                _routed = _SPK.route(runner, sid, text, user_info, row["parts"])
                _interim = _SPK.INTERIM
            except Exception:
                _routed = None
            if _routed is not None and _routed == _interim:
                # a streamed partial (Phase 2): recorded + shown, no turn yet
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if _routed is not None:
                if async_:
                    threading.Thread(target=_SPK.run, args=(runner, _routed), daemon=True).start()
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                result = _SPK.run(runner, _routed)
                return self._json_out({"info": (result or {}).get("info") if isinstance(result, dict) else result,
                                       "parts": (result or {}).get("parts", []) if isinstance(result, dict) else []})
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
            # "membrane only" (the old HA panel's LLM switch): the voice answers alone
            if body.get("membrane_only") is True:
                info["membraneOnly"] = True
            # VOICE IN (voice_io.py): a spoken message -- it starts the turn like
            # any message, but lives in the voice log, not the chat
            if body.get("voice") is True:
                info["voiceIn"] = True
                # CHUNKED STT (Phase 2, "more is coming"): the page streams the
                # person's speech -- interim transcripts (final false) coalesce in
                # the speaker lane into the one turn their final transcript starts
                if body.get("stream"):
                    info["stream"] = body["stream"]
                    info["final"] = bool(body.get("final", True))
                try:
                    from . import voice_io as _VIO
                    from .speakers import split_speaker as _split
                    _who, _said = _split(text)
                    _psrv = getattr(runner, "srv", None) if runner is not None else None
                    if _who and _psrv is not None:
                        _VIO.heard(_psrv, _who, _said, sid)
                        # the 🎙 carries the state the seat answers by (a fact, not an instruction):
                        # which voice:out is theirs and whether it is on
                        text = _VIO.stamp(_psrv, _who, text)
                except Exception:
                    pass
            else:
                # CHAT IN (chat_io.py): an ordinary message stamps the owner's
                # chat thing (chat:<owner>; chat:in until #333 part 3a) --
                # Blurp's next look at it reads news.
                try:
                    from . import chat_io as _CIO
                    _psrv = getattr(runner, "srv", None) if runner is not None else None
                    if _psrv is not None:
                        _owner = str((runner.sessions.get(sid) or {}).get("owner") or "")
                        _CIO.heard(_psrv, _owner or _CIO.OPERATOR, text, sid)
                except Exception:
                    pass
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
            if not self._jwt_door():
                return                       # JWT mode: no valid token, no answer
            if _UI.intercept(self):
                return                       # standalone mode: the UI, its LAN door
            path = self.path.split("?", 1)[0]
            if self._session_hidden(path):
                return self._notfound()      # JWT mode: someone else's session
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
            if not self._jwt_door():
                return                       # JWT mode: no valid token, no answer
            if _UI.intercept(self):
                return                       # standalone mode: the UI, its LAN door
            path = self.path.split("?", 1)[0]
            if self._session_hidden(path):
                return self._notfound()      # JWT mode: someone else's session
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


def start(port: int, host: str, config: dict,
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
                           messages=messages, config=config, runner=runner,
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