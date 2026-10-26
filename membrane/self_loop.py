"""THE SELF LOOP (#161): Blurp's body, between learners (#576).

membrane::self is the entity the operator named Blurp (#277). #576 retired
its first learner -- the try / expect / notice cycle, its methods and the
`self::` route, the skill ladder (rungs 0-13), the mirror, change itself,
aims, curiosity, the tempo and the nudges. Blurp starts anew: what stays here
is its body, and the new learner (#576 D2) plugs into ONE hook, learn_step.

THE SWITCH: a per-seat mode (off / on / watch), the operator's
(seat_panel.control), off by default. Off runs nothing and erases nothing;
the mode and the record survive a restart. The first switch-on is the birth
(a `birth` line).

THE RECORD: <data dir>/self/record.jsonl, append-only while Blurp is awake,
starting at birth. #345 part 2, CONSOLIDATION: when Blurp falls asleep every
raw line of the day moves, unchanged, to its dated archive
(self/archive/record-<UTC date>.jsonl), and the working record is emptied
down to one `summary` line that carries the lines every reader reads whole
(KEEP: the birth and the operator's accepts) and the last RECENT_MAX lines
(the activity feed's, recent(), and the card's last activity). No line is
ever edited or lost:
history() is the archive plus the working record, for the sheet.

THE LOOP: each step (step()) charges Blurp's energy for the time awake and
the CPU its learner used (energy.tick); when a reserve runs out it falls
asleep -- no learner runs while energy restores -- and it wakes rested or
when a person speaks. Asleep is not off. A step whose learner returns a line
with `dur_s` is followed by pace_wait of it (#343: the person's CPU share x
Blurp's energy); otherwise the loop waits for its rest tick (PACE_S). A
paused seat runs nothing.

ACCEPT (#299): the operator's accept stays as a door (accept(): an `accept`
line, {"kind": "accept", "id", "ts", "by"}); it grants nothing until the new
learner reads it (#576: Accept grants a branch).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import self_api as _API     # #440 part 2: the seat's state in
from . import self_writer as _W   # #440: the one writer for self/

ENT = "self"
MODES = ("off", "on", "watch")
PACE_S = 10.0               # the rest tick: a step with no line to pace by waits this long
RECENT_MAX = 100            # #230: the most record lines the page's activity feed may ask for
OPERATOR_KINDS = ("accept", "accept_migrated")   # #299: record lines the operator's acts write, not Blurp's running
KEEP = ("birth", "accept", "accept_migrated")    # the lines a consolidation's summary carries (read whole)
_lock = threading.RLock()
_RT: dict[int, dict] = {}   # id(srv) -> runtime (mode, the wake event, the record in memory)

SUMMARY = "summary"         # #345: the consolidated record's first line
ARCHIVE = "archive"         # ...the dated archives' folder under self/
MANIFEST = "manifest.jsonl"     # ...the order the archived lines were written in (one line per sleep)
CONSOLIDATING = "consolidating.json"   # ...the journal of a consolidation under way


# ---- the entity ------------------------------------------------------------------

def _eid():
    from .entities import MEMBRANE_NS
    return MEMBRANE_NS + ENT


def _entity(srv):
    from .entities import CLASS_COMPONENT, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(_eid())
    if ent is None:
        ent = QuantumEntity(eid=_eid(), cls=CLASS_COMPONENT, properties={
            "name": "self",
            "summary": "a loop the operator named Blurp (#161, #277); the operator switches it (off / on / watch)",
            "silent_birth": True})
        est.upsert(ent, embedder=None)
        try:
            est.flush()
        except Exception:
            pass
    return ent


def bind(srv) -> None:
    """Boot: the entity, its world, the saved mode."""
    if getattr(srv, "entity_store", None) is None or _entity(srv) is None:
        return
    from . import self_world as _SW
    _SW.bind(srv)                           # #337: its world (born, or migrated)
    from . import chat_io as _CIO
    _CIO.migrate(srv)                       # #333 part 3a: once -- chat:in + chat:out -> the chat thing
    load_state(srv)


def load_state(srv) -> None:
    """Blurp's runtime from its own files under self/: the mode, and the
    record (a consolidation the seat died in is finished here, _read). The
    seat's bind ends with it, and Blurp's own process (#440, self_proc) boots
    with it alone."""
    _rt(srv)["mode"] = _load_mode(srv)
    _read(srv)


def _rt(srv) -> dict:
    with _lock:
        rt = _RT.get(id(srv))
        if rt is None:
            rt = _RT[id(srv)] = {"mode": "off", "wake": threading.Event(), "srv": srv}
        return rt


# ---- the mode and the record (the data dir) --------------------------------

def _dir(srv) -> Path | None:
    return _W.dir_of(srv)


def record_path(srv) -> Path | None:
    d = _dir(srv)
    return d / "record.jsonl" if d is not None else None


def _load_mode(srv) -> str:
    """The saved mode (off / on / watch)."""
    d = _dir(srv)
    try:
        doc = json.loads((d / "mode.json").read_text()) if d is not None else {}
    except Exception:
        doc = {}
    return doc.get("mode") if doc.get("mode") in MODES else "off"


def _save_mode(srv, mode: str) -> None:
    _W.save_json(srv, "mode.json", {"mode": mode, "ts": time.time()})


def _append(srv, rec: dict) -> None:
    _W.append(srv, rec)                     # #440: the one writer for self/


def _read(srv) -> list[dict]:
    """The record, read from disk once and kept in memory as it grows. #345:
    a consolidated record's first line is its summary: read as the summary
    followed by the lines it carries, then the lines since. A consolidation
    the seat died in is finished first (_recover)."""
    rt = _rt(srv)
    if rt.get("recs") is None:
        with _lock:
            if rt.get("recs") is None:
                redo = _recover(srv)
                rt["recs"], rt["recs_bytes"] = _load_record(record_path(srv))
                if redo is not None:
                    consolidate(srv, redo)          # it was cut off before the swap: do it whole
    return rt["recs"]


def _load_record(p) -> tuple[list, int]:
    out: list = []
    size = 0
    if p is not None and p.exists():
        for i, ln in enumerate(p.read_text().splitlines()):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            size += len(ln) + 1
            if i == 0 and isinstance(r, dict) and r.get("kind") == SUMMARY:
                lines = r.pop("lines", None) or []
                r["n"] = len(lines)
                out.append(r)
                out.extend(lines)
            else:
                out.append(r)
    return out, size


def _head(recs: list) -> int:
    """#345: how many entries at the start of the record are the summary's
    (itself and the lines it carries): 0 for a record never consolidated."""
    if recs and isinstance(recs[0], dict) and recs[0].get("kind") == SUMMARY:
        return 1 + int(recs[0].get("n") or 0)
    return 0


def record_bytes(srv) -> int:
    """#345: the record held in memory, as the bytes of its serialized lines
    (a running sum kept as lines are appended, never a sizeof walk)."""
    with _lock:
        _read(srv)
        return int(_rt(srv).get("recs_bytes") or 0)


def _load_json(srv, name: str):
    d = _dir(srv)
    try:
        return json.loads((d / name).read_text()) if d is not None and (d / name).exists() else None
    except Exception:
        return None


def mode(srv) -> str:
    return _rt(srv)["mode"]


def accept(srv, i, by: str = "operator") -> dict:
    """#299's door, kept for the new learner (#576: Accept grants a branch):
    an `accept` line naming `i`. -> {"ok", "id", "name", "already"} or
    {"ok": False, "error"}; accepting twice writes nothing."""
    i = str(i if i is not None else "").strip().lower()
    if not i:
        return {"ok": False, "error": "accept what? (an id)"}
    with _lock:
        if any(r.get("kind") == "accept" and r.get("id") == i for r in _read(srv)):
            return {"ok": True, "id": i, "name": i, "already": True}
        _append(srv, {"ts": time.time(), "kind": "accept", "id": i, "by": str(by or "operator")})
    rt = _rt(srv)
    rt.pop("sheet", None)                   # the panel's cached sheet shows it at once
    rt["wake"].set()
    return {"ok": True, "id": i, "name": i, "already": False}


def set_mode(srv, m: str, by: str = "operator") -> bool:
    """The operator's switch (seat_panel.control): off / on / watch. The
    first switch-on is the birth. Off runs nothing and erases nothing."""
    if m not in MODES:
        return False
    rt = _rt(srv)
    with _lock:
        if m != "off" and not any(r.get("kind") == "birth" for r in _read(srv)):
            _append(srv, {"ts": time.time(), "kind": "birth", "by": by})
        rt["mode"] = m
        _save_mode(srv, m)
    if m != "off":
        rt["wake"].set()                    # switching on kicks the first step
    return True


# ---- the pace and its CPU share (#343) ---------------------------------------------

def _paused(srv) -> bool:
    """The seat's pause (lanes.nerve_paused; the bare flag when it can't say).
    #440: in Blurp's own process, the seat's context says (step's start check)."""
    r = _API._remote(srv)
    if r is not None:
        return bool(r.nerve_paused())
    from .lanes import nerve_paused
    try:
        return bool(nerve_paused(srv))
    except Exception:
        return bool(getattr(srv, "paused", False))


def _runs(srv) -> bool:
    return _rt(srv)["mode"] != "off" and not _paused(srv)


def cpu_share(srv) -> float:
    """#343: Blurp's CPU share, a seat setting (`self_cpu_share` in the seat's
    <data dir>/settings.json): options.cpu_share(srv), the one reader.
    #440: in Blurp's own process, the seat's context carries it."""
    r = _API._remote(srv)
    if r is not None:
        return float(r.cpu_share())
    from . import options as O
    return O.cpu_share(srv)


def share_wait(dur_s: float, share: float) -> float:
    """#343: the wait after a step that took dur_s, at CPU share `share`:
    dur_s x (1 / share - 1) -- as long as the step at 50%, none at 100%."""
    return max(0.0, float(dur_s)) * (1.0 / share - 1.0)


def pace_share(srv) -> float:
    """#343 x #345: the share the loop paces by -- the person's throttle
    (cpu_share) x Blurp's energy (energy.share), so a tired Blurp slows."""
    from . import energy as _E
    return max(0.0, _E.share(cpu_share(srv), srv))


def pace_wait(srv, dur_s: float) -> float | None:
    """The wait after a step that took dur_s, at pace_share. None at a share
    of 0 (energy spent): the loop waits for its rest tick (PACE_S)."""
    sh = pace_share(srv)
    return share_wait(dur_s, sh) if sh > 0.0 else None


# ---- the views ---------------------------------------------------------------------

def status(srv) -> dict:
    """Blurp's state for the panel and the page. The ladder retired (#576):
    the level is 0 and the tree is empty until the new learner fills them;
    the keys stay for the page's readers."""
    recs = _read(srv)
    return {"mode": _rt(srv)["mode"], "born": any(r.get("kind") == "birth" for r in recs),
            "rung": 0, "level": 0, "rung_name": "", "next": [], "earned": [], "accepted": [], "awaiting": [],
            "bar": 0.0, "waiting_on": None, "slipping": False, "ladder": []}


def _short(eid: str) -> str:
    """membrane::Component:toy:dice -> toy:dice (the name a toy goes by)."""
    from .entities import MEMBRANE_NS
    s = eid[len(MEMBRANE_NS):] if eid.startswith(MEMBRANE_NS) else eid
    head, sep, rest = s.partition(":")
    return rest if sep and head[:1].isupper() and rest else s


def recent(srv, n=30) -> list[dict]:
    """#230: the last n record lines, newest first, capped at RECENT_MAX --
    for the page's activity feed (copies: the record is never edited)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 30
    n = max(1, min(n, RECENT_MAX))
    with _lock:
        tail = [r for r in _read(srv)[-(n + 1):] if r.get("kind") != SUMMARY][-n:]   # #345: never the summary
    return [dict(r) for r in reversed(tail)]


# ---- #345 part 2: consolidation -- nothing raw stays through sleep ----------------------
#
# At the moment Blurp falls asleep (energy._fall_asleep) every raw line of the
# waking period moves, unchanged, to the dated archive
# self/archive/record-<UTC date of the line's own ts>.jsonl, and the working
# record becomes ONE line: the summary,
#
#   {"kind": "summary", "id", "at", "n", "lines": [...]}
#
# `lines` are copies of the KEEP lines (the birth, the accepts) and of the
# last RECENT_MAX lines, in record order. The archive is for the sheet and
# later analysis (history()).
#
# RESTART SAFETY: the journal self/consolidating.json is written first (stage
# "archive", with the archive files' sizes before), the archive appended and
# synced, the manifest line appended, the new record written beside the old
# one (record.jsonl.next) and synced, the journal set to "swap", the record
# swapped in (os.replace), the journal removed. On load (_recover) a journal
# at "archive" is undone (the archive cut back to its sizes) and the
# consolidation redone from the untouched record; one at "swap" finishes the
# swap. No line is lost or doubled.

def _day(ts) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime("%Y-%m-%d")


def _sync_write(path: Path, data: bytes, mode: str = "ab") -> None:
    _W.sync_write(path, data, mode)


def _journal(srv, doc: dict) -> None:
    """The consolidation's journal, synced before the step it announces."""
    d = _dir(srv)
    tmp = d / (CONSOLIDATING + ".tmp")
    _sync_write(tmp, json.dumps(doc).encode(), "wb")
    _W.replace(tmp, d / CONSOLIDATING)


def _c_append(path: Path, lines: list) -> None:
    """Append raw lines to an archive file, synced."""
    _sync_write(path, b"".join(ln + b"\n" for ln in lines))


def _c_manifest(srv, entry: dict) -> None:
    _sync_write(_dir(srv) / ARCHIVE / MANIFEST, (json.dumps(entry) + "\n").encode())


def _c_swap(tmp: Path, p: Path) -> None:
    _W.replace(tmp, p)


def _c_done(srv) -> None:
    _W.unlink(_dir(srv) / CONSOLIDATING)


def _undo(srv, j: dict) -> None:
    """Cut the archive back to the sizes the journal holds (files it created go)."""
    a = _dir(srv) / ARCHIVE
    for name, size in list((j.get("sizes") or {}).items()) + [(MANIFEST, j.get("manifest"))]:
        f = a / name
        if size is None:
            _W.unlink(f)
        elif f.exists():
            _W.truncate(f, size)
    p = record_path(srv)
    _W.unlink(p.with_name(p.name + ".next"))


def _recover(srv) -> float | None:
    """A consolidation the seat died in: finish the swap (journal at "swap"),
    or undo the half-written archive (at "archive") and say when to redo it.
    -> the redo's time, or None."""
    d = _dir(srv)
    j = _load_json(srv, CONSOLIDATING) if d is not None else None
    if not isinstance(j, dict):
        return None
    p = record_path(srv)
    tmp = p.with_name(p.name + ".next")
    if j.get("stage") == "swap":
        if tmp.exists():
            _W.replace(tmp, p)
        _W.unlink(d / CONSOLIDATING)
        return None
    _undo(srv, j)
    _W.unlink(d / CONSOLIDATING)
    return float(j.get("at") or time.time())


def consolidate(srv, now: float | None = None) -> dict | None:
    """SLEEP'S CONSOLIDATION (#345 part 2): archive the waking period's raw
    lines, unchanged, by their own UTC date, and leave the working record as
    one summary line (the KEEP lines and the last RECENT_MAX). Run when Blurp
    falls asleep (energy).
    -> {archived, kept, bytes, id}, or None when there is nothing new or it
    failed (the record is then left whole, the archive as it was)."""
    import uuid
    now = time.time() if now is None else float(now)
    p = record_path(srv)
    if p is None or not p.exists():
        return None
    with _lock:
        recs = _read(srv)
        raw = [ln for ln in p.read_bytes().split(b"\n") if ln.strip()]
        if raw and _head(recs):
            raw = raw[1:]                           # the old summary: its lines are archived already
        if not raw:
            return None
        cid = uuid.uuid4().hex
        swapped = False
        try:
            lo = 1 if recs and recs[0].get("kind") == SUMMARY else 0
            tail = max(lo, len(recs) - RECENT_MAX)
            lines = [r for i, r in enumerate(recs) if i >= lo and (i >= tail or r.get("kind") in KEEP)]
            head = {"kind": SUMMARY, "id": cid, "at": now, "n": len(lines)}
            text = (json.dumps({**head, "lines": lines}) + "\n").encode()
            runs: list = []
            files: dict = {}
            day = _day(now)
            for ln in raw:
                try:
                    t = json.loads(ln).get("ts")
                    day = _day(t) if t is not None else day
                except (ValueError, AttributeError, TypeError, OverflowError, OSError):
                    pass                            # no time of its own: its neighbour's day
                name = f"record-{day}.jsonl"
                files.setdefault(name, []).append(ln)
                if runs and runs[-1][0] == name:
                    runs[-1][1] += 1
                else:
                    runs.append([name, 1])
            a = _dir(srv) / ARCHIVE
            _W.mkdir(a)
            j = {"id": cid, "at": now, "stage": "archive",
                 "sizes": {n: ((a / n).stat().st_size if (a / n).exists() else None) for n in files},
                 "manifest": (a / MANIFEST).stat().st_size if (a / MANIFEST).exists() else None}
            _journal(srv, j)
            for name, ls in files.items():
                _c_append(a / name, ls)
            _c_manifest(srv, {"id": cid, "at": now, "runs": runs})
            tmp = p.with_name(p.name + ".next")
            _sync_write(tmp, text, "wb")
            _journal(srv, {**j, "stage": "swap"})
            _c_swap(tmp, p)
            swapped = True
            _c_done(srv)
        except Exception as e:                      # noqa: BLE001
            import sys
            print(f"[self] consolidation failed ({e!r}); the record stays whole", file=sys.stderr)
            if not swapped:
                j = _load_json(srv, CONSOLIDATING)
                if isinstance(j, dict) and j.get("id") == cid:
                    _undo(srv, j)
                _W.unlink(_dir(srv) / CONSOLIDATING)
                return None
            _W.unlink(_dir(srv) / CONSOLIDATING)
        rt = _rt(srv)
        rt["recs"], rt["recs_bytes"] = [head] + lines, len(text)
        rt.pop("sheet", None)
        return {"archived": len(raw), "kept": len(lines), "bytes": len(text), "id": cid}


def _manifest(srv) -> list:
    d = _dir(srv)
    f = d / ARCHIVE / MANIFEST if d is not None else None
    out = []
    if f is not None and f.exists():
        for ln in f.read_text().splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out


def archive_sig(srv) -> tuple:
    """Which consolidations the archive holds (changes only when one is added)."""
    m = _manifest(srv)
    return (len(m), m[-1].get("id") if m else None)


def _archive_lines(srv, manifest: list) -> list:
    """The archived lines in the order they were written (the manifest's runs)."""
    a = _dir(srv) / ARCHIVE
    files: dict = {}
    out = []
    for entry in manifest:
        for name, count in entry.get("runs") or ():
            it = files.get(name)
            if it is None:
                it = files[name] = iter((a / name).read_bytes().split(b"\n"))
            for _ in range(int(count)):
                ln = next(it, None)
                while ln is not None and not ln.strip():
                    ln = next(it, None)
                if ln is None:
                    break
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass                            # as _read: a line that isn't JSON is no line
    return out


def archived(srv) -> list:
    """Every archived line, oldest first (read now, from disk)."""
    return _archive_lines(srv, _manifest(srv))


def history(srv) -> list:
    """THE WHOLE RECORD (#345): the archive, then the lines since the last
    sleep -- what the record was before consolidation, for the sheet. Read
    from disk each time."""
    with _lock:
        recs = _read(srv)
        new = list(recs[_head(recs):])
        m = _manifest(srv)
    return _archive_lines(srv, m) + new


# ---- the thread -----------------------------------------------------------

def learn_step(srv, now: float) -> dict | None:
    """THE LEARNER'S HOOK (#576 D2 plugs the new learner in here): one turn of
    learning on an awake Blurp. -> a record line with `dur_s` (the loop paces
    by it), or None (the rest tick). Today Blurp learns nothing."""
    return None


def step(srv, now: float | None = None) -> dict | None:
    """One turn of the loop's thread (#345): off or paused, nothing runs and
    no time counts; asleep, no learner runs and energy restores (energy.tick);
    awake, learn_step, charged the time awake and the CPU it used on this
    thread (time.thread_time around it). -> learn_step's line, or None."""
    from . import energy as _E
    if not _runs(srv):
        _E.pause(srv)
        return None
    now = time.time() if now is None else now
    if _E.asleep(srv):
        _E.tick(srv, now)
        return None
    t0 = time.thread_time()
    rec = learn_step(srv, now)
    cpu = time.thread_time() - t0
    _E.tick(srv, now, cpu_s=cpu)
    return rec


def start(srv) -> threading.Thread:
    """The loop: wakes on the switch (or its rest tick, PACE_S), runs one
    step, then after a step with a line waits pace_wait of it (#343: the
    person's throttle x energy, so a tired Blurp slows)."""
    rt = _rt(srv)

    def run():
        while True:
            rt["wake"].wait(timeout=PACE_S)
            rt["wake"].clear()
            try:
                rec = step(srv)
                if rec is not None:
                    rt["wake"].clear()
                    wait = pace_wait(srv, rec.get("dur_s") or 0.0)
                    if wait is not None:                    # share 0 (energy spent): no wake, the rest tick
                        rt["wake"].wait(timeout=wait)
                        rt["wake"].set()
            except Exception as e:                          # noqa: BLE001
                import sys
                print(f"[self] step failed ({e!r})", file=sys.stderr)
    t = threading.Thread(target=run, name="self-loop", daemon=True)
    t.start()
    return t
