"""THE AUTONODE (the operator's 2026-10-01 design: 'the model should be
the one who writes those watcher scripts').

A seat watches its world. Not source-specifically: collectors differ,
dispatch doesn't. A file appears, an MQTT topic speaks, a signal
arrives -- each lands on the same bounded queue as one ``Trigger``,
and every ENTITY carrying a ``watch_script`` whose ``watch`` prop
selects the trigger fires. The scripts are authored through the same
entity edit surface as the clock's tick_script; watching starts by
``entity_edit(eid, watch_script=..., watch={...})``.

THE BLOCKING TRADE (the honest one): watch scripts may block --
subprocesses, sleeps, network reads. Threads cannot be killed, so the
bounds are: a ThreadPoolExecutor of TRIGGER_WORKERS (4) -- at most 4
scripts may wedge at once --, a fire budget WATCH_TIMEOUT_S whose
overrun STRIKES the script (the usual 3-strikes lane) but cannot stop
the thread (its result still applies when it finally lands), and a
drop-oldest backlog TRIGGER_BACKLOG (64) -- a burst of churn can never
wedge the seat or flood the room. Hard-kill would need subprocess
isolation; the pool is the limit that's real.

The seeded ``tool:filetree`` entity is the template the model copies.
"""

import fnmatch
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .codegraph import _SKIP_DIRS as SKIP_DIRS, _MAX_FILES

MEMBRANE_AUTONODE_SCAN_S = 10.0   # the day cadence (the 2s scan was the
                                  # 100%-idle-CPU exhibit's burner; the
                                  # night's x4 lives in run())
MEMBRANE_AUTONODE_SCAN_S = float(
    os.environ.get("MEMBRANE_AUTONODE_SCAN_S") or MEMBRANE_AUTONODE_SCAN_S)
TRIGGER_WORKERS = 4            # how many (possibly blocking) scripts at once
TRIGGER_BACKLOG = 64           # queued triggers before drop-oldest bites
WATCH_TIMEOUT_S = 10.0         # a fire's budget; overrun strikes, not kills
WATCH_TELL_MIN_S = 10.0        # the field's churn guard (night.py owns it)

# The seat's own runtime files (beside the watched tree when the seat is
# started from its seat dir) -- the scanner never emits them.
_OWN_NAMES = {"entities.json", "field.json"}


# ---- the generic model ------------------------------------------------------

@dataclass
class Trigger:
    kind: str                      # file | signal
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


# ---- THE DELTA-RENDER CONTRACT (the engine runs the diff) ------------------
# A watcher opting in with result['delta']=True gets ITS OWN geometry
# diffed by the engine against the persisted _delta_prev props stash --
# no per-script bookkeeping, and the entity doc's true vocabulary
# (position / ring_velocity / trajectory ring / status), never guessed
# accessors. No delta -> empty render -> a no-op apply (no tell: the
# debounce lives HERE). result['delta'] may also be a dict of extra
# metrics to snapshot alongside the defaults. A big velocity jump
# speaks (planner-gated, opencode.speak; per-entity speak_thresh).

def _geometry_snap(ent) -> dict:
    snap = {}
    try:
        pos = ent.position or []
        snap["pos"] = ",".join(f"{float(x):.2f}" for x in pos[:3])
        snap["dim"] = len(pos)
    except Exception:
        pass
    try:
        snap["vel"] = round(float(ent.ring_velocity(time.time())), 3)
    except Exception:
        pass
    try:
        snap["ring"] = len(getattr(ent, "position_history", None) or [])
    except Exception:
        pass
    try:
        snap["status"] = str((ent.properties or {}).get("status") or "")[:40]
    except Exception:
        pass
    return snap


def _delta_string(prev, cur, eps: float = 0.02) -> str:
    parts = []
    for k in sorted(cur):
        a = (prev or {}).get(k)
        b = cur[k]
        if a is None:
            parts.append(f"{k}={b}")
            continue
        try:
            fa, fb = float(a), float(b)
            if abs(fb - fa) >= eps:
                parts.append(f"{k} {fa}->{fb}")
            continue
        except (TypeError, ValueError):
            pass
        if str(a) != str(b):
            sa, sb = str(a), str(b)
            if len(sa) + len(sb) > 48:
                sa, sb = sa[:20], sb[:20]
            parts.append(f"{k} {sa}->{sb}")
    return "; ".join(parts)


def _delta_apply(srv, ent, out, trig) -> None:
    "The engine-side delta: snapshot, diff, render, stash, big-jump speak."
    props = ent.properties or {}
    prev = props.get("_delta_prev")
    prev = prev if isinstance(prev, dict) else {}
    cur = _geometry_snap(ent)
    extra = out.get("delta")
    if isinstance(extra, dict):
        for k, v in list(extra.items())[:8]:
            try:
                cur[str(k)[:24]] = round(float(v), 3)
            except Exception:
                pass
    dstr = _delta_string(prev, cur)
    if not dstr:
        # NOTHING moved: a pure delta watcher stays silent (no
        # tell), but a script with its own render keeps it -- a
        # real event with an unmoved geometry keeps its voice.
        if not str(out.get("render") or ""):
            out["render"] = ""
        return
    pl = getattr(trig, "payload", None) or {}
    sub = str(pl.get("path") or pl.get("topic") or pl.get("name") or "")
    sub = sub.rsplit("/", 1)[-1][:32]
    dline = "delta: " + dstr + " [" + str(getattr(trig, "kind", "watch"))
    if sub:
        dline += ":" + sub
    dline += "]"
    old_r = str(out.get("render") or "")
    out["render"] = (dline + ((" | " + old_r) if old_r else ""))[:600]
    pd = out.get("patch") if isinstance(out.get("patch"), dict) else {}
    pd = dict(pd)
    pd["_delta_prev"] = cur
    out["patch"] = pd
    try:
        vd = abs(float(cur.get("vel", 0.0)) - float(prev.get("vel", 0.0)))
        th = float(props.get("speak_thresh", 0.3))
        if prev and vd >= th:
            from .opencode import speak
            speak(srv, "delta-jump: " + str(ent.eid) + " " + dline[:160],
                  kind="report-state")
    except Exception:
        pass


def watcher_matches(props: dict, trig: Trigger) -> bool:
    """The CHEAP pre-filter per fire (the script does the real reading):
    kind required (absent watch prop defaults to 'file'), plus the
    optional glob/topic/name narrowing. Script presence + disabled/retired
    state is the census's AND the retired guard's (a stale watcher can't
    fire); the watch_script census lives in Autonode._watchers."""
    if props.get("script_disabled") or props.get("retired"):
        return False
    w = props.get("watch")
    w = w if isinstance(w, dict) else {}
    kind = str(w.get("kind") or "file")
    if kind != str(trig.kind):
        return False
    p = trig.payload or {}
    if trig.kind == "file":
        globs = w.get("glob")
        path = str(p.get("path") or "")
        if globs:
            pats = [globs] if isinstance(globs, str) else list(globs)
            if not any(fnmatch.fnmatch(path, str(g)) for g in pats):
                return False
    elif trig.kind == "signal":
        name = str(w.get("name") or "")
        if name and str(p.get("name") or "!none") != name:
            return False   # the watcher wants a SPECIFIC signal; a
                           # nameless trigger never matches it
    return True


# ---- collector 1: the file tree (fingerprint poll, no watchdog dep) --------

class FileTreeScanner:
    """A recursive (mtime_ns, size) fingerprint poll -- the ledger and
    codegraph's scheme. The FIRST scan primes the baseline (no flood of
    'created' at boot); afterwards every diff is one Trigger per file."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._fps: dict[str, tuple[int, int]] = {}
        self._primed = False

    def scan(self) -> list[Trigger]:
        fps: dict[str, tuple[int, int]] = {}
        root = self.root
        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                dirnames[:] = [dn for dn in dirnames
                               if dn not in SKIP_DIRS and
                               not dn.startswith(".")]
                if len(fps) >= _MAX_FILES:
                    break
                for fn in filenames:
                    # THE FEEDBACK GUARD (found in the boot smoke): the
                    # seat's own state files churn every apply -- watching
                    # them is the watcher reporting its own reporting. The
                    # .membrane dot-dir is skipped above.
                    if fn in _OWN_NAMES or fn.startswith("serve-") or \
                            fn.endswith(".seed.json"):
                        continue
                    fp = Path(dirpath) / fn
                    rel = str(fp.relative_to(root))
                    try:
                        st = fp.stat()
                    except OSError:
                        continue
                    fps[rel] = (st.st_mtime_ns, st.st_size)
        except Exception as e:
            print(f"[autonode] scan failed ({str(e)[:60]})", file=sys.stderr)
            return []
        if not self._primed:
            self._fps, self._primed = fps, True
            return []
        ts = []
        old = self._fps
        for rel, fp in fps.items():
            if rel not in old:
                ts.append(Trigger("file", {"event": "created",
                                           "path": rel}))
            elif old[rel] != fp:
                ts.append(Trigger("file", {"event": "changed",
                                           "path": rel,
                                           "mtime_ns": fp[0],
                                           "size": fp[1]}))
        for rel in old:
            if rel not in fps:
                ts.append(Trigger("file", {"event": "deleted",
                                           "path": rel}))
        self._fps = fps
        return ts


# ---- collector 2 + 3 live in the Autonode (bus subscriptions, signals) ------

class Autonode:
    """The runner: one daemon thread ('membrane-autonode') driving the
    scanner tick, the queue, the bounded pool and the subscription sync.
    All decisions stay test-steppable through pump()."""

    def __init__(self, srv, est, root: Path | None = None,
                 bus=None, scan_s: float = 10.0,
                 backlog: int = TRIGGER_BACKLOG,
                 workers: int = TRIGGER_WORKERS,
                 fire_timeout: float = WATCH_TIMEOUT_S):
        self.srv = srv
        self.est = est
        self.root = Path(root) if root is not None else None
        self.scan_s = max(0.5, float(scan_s))
        self.fire_timeout = float(fire_timeout)
        self.workers = max(1, int(workers))
        self.scanner = FileTreeScanner(self.root) if self.root else None
        self.bus = bus
        self._subs: set[str] = set()
        self._active = 0                      # scripts running right now
        self._lock = threading.Lock()
        self._pending: dict = {}              # future -> {eid, start, struck}
        self._last_scan = 0.0
        self.q = self._DropQ(backlog)
        self.pool = ThreadPoolExecutor(max_workers=self.workers,
                                       thread_name_prefix="membrane-autonode")
        self.alive = True
        self._thread: threading.Thread | None = None

    # ---- the queue (drop-oldest, a burst cannot wedge or flood) ----------
    class _DropQ:
        def __init__(self, maxsize: int):
            self._max = max(1, int(maxsize))
            self._items: list = []
            self._lk = threading.Lock()

        def put(self, t) -> None:
            with self._lk:
                if len(self._items) >= self._max:
                    self._items.pop(0)        # drop-OLDEST
                self._items.append(t)

        def qsize(self) -> int:
            with self._lk:
                return len(self._items)

        def drain(self) -> list:
            with self._lk:
                out, self._items = self._items, []
                return out

        def get_nowait(self):
            with self._lk:
                return self._items.pop(0) if self._items else None

    def backlog_size(self):
        return self.q.qsize()

    @property
    def active(self) -> int:
        """The scripts running RIGHT NOW -- never above TRIGGER_WORKERS."""
        return self._active

    # ---- collectors ------------------------------------------------------
    def dispatch(self, trig: Trigger) -> None:
        self.q.put(trig)

    def sync_subscriptions(self) -> None:
        """RETIRED with mqtt (the operator's 2026-10-03 call: the bus
        is gone): an mqtt-kind watcher now degrades with an HONEST
        refusal stamped on its doc (script_disabled + the reason --
        the model sees WHY the lane died), and this sync is a no-op."""
        try:
            for e in self._watchers():
                w = (e.properties or {}).get("watch")
                w = w if isinstance(w, dict) else {}
                if str(w.get("kind") or "file") == "mqtt" and \
                        not (e.properties or {}).get("script_disabled"):
                    (e.properties or {}).update({
                        "script_disabled": True})
                    (e.properties or {}).update({
                        "script_last_error": "watch lane 'mqtt' retired "
                        "(2026-10-03) -- the event bus is gone; re-author "
                        "as a file/signal watcher"})
                    self.est.upsert(e, embedder=None)
        except Exception:
            pass
        return

    def install_signals(self) -> None:
        """The signal lane: SIGUSR1 nudges the watchers (SIGINT/SIGTERM
        stay teardown-only)."""
        import signal as sg

        def _on(_s, _f):
            try:
                self.q.put(Trigger("signal", {"name": "SIGUSR1"}))
            except Exception:
                pass
        try:
            sg.signal(sg.SIGUSR1, _on)
        except Exception:
            pass

    # ---- the watcher census ----------------------------------------------
    def _watchers(self) -> list:
        out = []
        try:
            for e in (self.est.docs or {}).values():
                p = e.properties or {}
                if p.get("watch_script"):
                    out.append(e)
        except Exception:
            return []
        return out

    def _file_watch(self) -> bool:
        try:
            for e in self._watchers():
                p = e.properties or {}
                if p.get("script_disabled") or p.get("retired"):
                    continue
                w = p.get("watch")
                w = w if isinstance(w, dict) else {}
                if str(w.get("kind") or "file") == "file":
                    return True
        except Exception:
            return False
        return False

    # ---- the bounded fire ------------------------------------------------
    def _fire(self, eid: str, trig: Trigger) -> dict:
        from . import writes as _W
        with _W.source("sensor", f"{trig.kind}:{eid}"):      # #576: a sensor's change starts a chain
            return self._fire_body(eid, trig)

    def _fire_body(self, eid: str, trig: Trigger) -> dict:
        try:
            ent = (self.est.docs or {}).get(eid)
            if ent is None:
                return {"applied": 0}
            from .night import (apply_entity_update,
                                run_entity_update_script)
            extra = dict(trig.payload)
            extra.setdefault("payload", trig.payload)
            extra["kind"] = trig.kind
            out = run_entity_update_script(self.srv, ent, "watch",
                                           extra_state=extra)
            if not out.get("error"):
                # THE DELTA-RENDER CONTRACT: opt in with
                # result['delta']=True -> the engine diffs, renders and
                # stashes; result['silent']=True rides the apply's
                # silence lever.
                # delta opt-in: the script's result['delta']=True
                # flag OR the entity's delta_render prop.
                if out.get("delta") or (ent.properties or {}).get("delta_render"):
                    _delta_apply(self.srv, ent, out, trig)
                apply_entity_update(self.srv, ent, out, "watch",
                                    silent=bool(out.get("silent", False)))
            return out
        except Exception as e:
            return {"error": f"fire failed: {type(e).__name__}: {e}"[:200]}

    def pump(self, marks: dict | None = None) -> None:
        """One duty pass: scan (gated), reap the strays, dispatch what
        the slots allow. The runner thread calls this; tests call it
        directly. `marks`: an optional cost ledger (the run loop's
        slow-pump report fills it per phase).

        THE PAUSE (the stable point): the scanner GATES (the tree's
        diff accumulates silently) and no dispatch goes out -- on
        resume the accumulated mtime diff fires as a bounded burst
        (the 64 backlog is the covenant); the strays' reap keeps (the
        pending fires' strikes are bookkeeping, not acting)."""
        try:
            from .opencode import nerve_paused
            if nerve_paused(self.srv):
                _m0 = marks if marks is not None else {}
                _t1 = time.perf_counter()
                self._reap(time.time())
                _m0["reap"] = time.perf_counter() - _t1
                return
        except Exception:
            pass
        _m = marks if marks is not None else {}
        _t0 = time.perf_counter()
        now = time.time()
        if self.scanner is not None and self._file_watch() and \
                now - self._last_scan >= self.scan_s:
            self._last_scan = now
            try:
                for t in self.scanner.scan():
                    self.q.put(t)
            except Exception as e:
                print(f"[autonode] scanner tick failed ({e})",
                      file=sys.stderr)
        _m["scan"] = time.perf_counter() - _t0
        _t1 = time.perf_counter()
        self._reap(now)
        _m["reap"] = time.perf_counter() - _t1
        _t2 = time.perf_counter()
        self.sync_subscriptions()
        _m["sync"] = time.perf_counter() - _t2
        # the submit lane: while a slot is free, take one trigger and
        # fire every watcher it matches (up to the free slots)
        _t3 = time.perf_counter()
        watchers = self._watchers()
        _m["census"] = time.perf_counter() - _t3
        _t4 = time.perf_counter()
        while self._active < self.workers:
            trig = self.q.get_nowait()
            if trig is None:
                break
            for e in watchers:
                if self._active >= self.workers:
                    break
                if not watcher_matches(e.properties or {}, trig):
                    continue
                with self._lock:
                    self._active += 1
                fut = self.pool.submit(self._fire, e.eid, trig)
                self._pending[fut] = {"eid": e.eid,
                                      "start": now, "struck": False}
        _m["dispatch"] = time.perf_counter() - _t4

    def _reap(self, now: float) -> None:
        """Strays: a fire past its budget strikes ONCE (the thread can't
        be killed -- its result still applies when it lands)."""
        for fut, info in list(self._pending.items()):
            if fut.done():
                try:
                    res = fut.result()
                    if isinstance(res, dict) and res.get("error") and \
                            not str(res["error"]).startswith("cooldown"):
                        # cooldown is a NORMAL gate refusal -- never a
                        # failure print (the smoke showed it spamming)
                        print(f"[autonode] fire {info['eid']}: "
                              f"{str(res['error'])[:120]}", file=sys.stderr)
                except Exception as e:
                    print(f"[autonode] fire {info['eid']} failed "
                          f"({str(e)[:80]})", file=sys.stderr)
                with self._lock:
                    self._active -= 1
                self._pending.pop(fut, None)
            elif not info["struck"] and \
                    now - info["start"] > self.fire_timeout:
                info["struck"] = True
                try:
                    from .night import _script_record_fail
                    ent = (self.est.docs or {}).get(info["eid"])
                    if ent is not None:
                        _script_record_fail(
                            getattr(self.srv, "efield", None), self.est,
                            ent, ent.eid, "watch",
                            f"watch-fire overrun (budget "
                            f"{self.fire_timeout}s) -- the thread cannot "
                            f"be killed, the result still applies",
                            mem=getattr(self.srv, "membrane", None))
                except Exception as e:
                    print(f"[autonode] overrun strike failed ({e})",
                          file=sys.stderr)

    def join_fires(self, timeout: float = 5.0) -> bool:
        """Test/teardown hook: wait for every pending fire's future."""
        import concurrent.futures as cf
        futs = list(self._pending)
        if not futs:
            return True
        done, _ = cf.wait(futs, timeout=timeout)
        for fut in done:
            self._pending.pop(fut, None)
            with self._lock:
                self._active -= 1
            try:
                fut.result()
            except Exception:
                pass
        return not self._pending

    def run(self) -> None:
        """The daemon loop (the clock's heartbeat pattern, own thread).
        THE COST LEDGER (the 100%-idle-CPU exhibit): pump's phases are
        timed -- a pass over its per-phase budget prints the phase and
        the cost (the slowest seat's lane names itself; the print is
        rate-limited to once per 60s)."""
        import time as _t
        _last_report = {"t": 0.0}
        while self.alive:
            _a = _t.perf_counter()
            _marks = {}
            try:
                self.pump(_marks)
            except Exception as e:
                print(f"[autonode] loop skipped ({str(e)[:80]})",
                      file=sys.stderr)
            _pass = _t.perf_counter() - _a
            slow = {k: round(v, 4) for k, v in _marks.items()
                    if v > 0.05}
            if slow and _t.time() - _last_report["t"] > 60.0:
                _last_report["t"] = _t.time()
                print(f"[autonode] slow pump {_pass*1000:.0f}ms: "
                      + ", ".join(f"{k} {v*1000:.0f}ms"
                                  for k, v in slow.items()),
                      file=sys.stderr)
            # THE PUMP'S DUTY CYCLE (the 7:42-utime exhibit): the
            # pass's scan+reap+census is ~700ms of python; a 1s sleep
            # meant ~70% duty forever. The pass sleeps its SCAN
            # CADENCE when the queue is empty (the strays' reap is a
            # per-pass need, not a per-second one); the 1s cadence
            # returns only while work is queued (the watcher's
            # triggers stay immediate).
            _idle = self.q.empty() if hasattr(self.q, "empty") else True
            _scan_s = self.scan_s
            try:
                if getattr(self.srv, "ambient_mode", "day") == "night":
                    from .thinker import NIGHT_SWEEP_X   # was an undefined name: the scan never slowed at night
                    _scan_s = self.scan_s * float(NIGHT_SWEEP_X)
            except Exception:
                pass
            time.sleep(_scan_s if _idle else 1.0)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="membrane-autonode",
                             daemon=True)
        t.start()
        self._thread = t
        return t

    def close(self) -> None:
        self.alive = False
        try:
            self.pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


# ---- the seeded template ----------------------------------------------------

DEFAULT_FILETREE_WATCH_SCRIPT = (
    "import time as _t\n"
    "x = state.get('extra') or {}\n"
    "py = x.get('payload') or {}\n"
    "ev = py.get('event') or x.get('kind') or '?'\n"
    "pth = py.get('path') or py.get('topic') or py.get('name') or ''\n"
    "result = {'render': 'filetree: ' + str(ev) + ' ' + str(pth),\n"
    "          'patch': {'script_data': {'last_change': {'event': str(ev),\n"
    "                                                  'path': str(pth),\n"
    "                                                  'ts': _t.time()}}}}"
)


# The default shipped before #597: it patched `last_change` at the top level,
# which the engine refuses. A seat still holding exactly this text never
# rewrote it, so boot swaps it for the current default (a rewrite is kept).
OLD_FILETREE_WATCH_SCRIPT = DEFAULT_FILETREE_WATCH_SCRIPT.replace(
    "'patch': {'script_data': {'last_change': {'event': str(ev),\n"
    "                                                  'path': str(pth),\n"
    "                                                  'ts': _t.time()}}}}",
    "'patch': {'last_change': {'event': str(ev),\n"
    "                                    'path': str(pth),\n"
    "                                    'ts': _t.time()}}}")


def seed_filetree_entity(est) -> bool:
    """The boot seeding: plant `tool:filetree` ONCE (idempotent -- a
    model-rewritten watcher survives every restart). The template the
    model copies when it wants anything else watched."""
    if est is None:
        return False
    from .entities import (CLASS_TOOL, MEMBRANE_NS, QuantumEntity)
    eid = MEMBRANE_NS + "tool:filetree"
    doc = (est.docs or {}).get(eid)
    if doc is not None:
        if (doc.properties or {}).get("watch_script") != OLD_FILETREE_WATCH_SCRIPT:
            return False
        est.edit("tool:filetree", watch_script=DEFAULT_FILETREE_WATCH_SCRIPT)
        try:
            est.flush()
        except Exception:
            pass
        return True
    est.upsert(QuantumEntity(eid=eid, cls=CLASS_TOOL, properties={
        "name": "filetree",
        "watch_script": DEFAULT_FILETREE_WATCH_SCRIPT,
        "watch": {"kind": "file"},
        "silent_birth": True}))
    try:
        est.flush()
    except Exception:
        pass
    return True