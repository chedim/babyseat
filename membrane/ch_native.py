"""THE NATIVE VM (#971 step 3): ch.VM's API over the Rust core (native/, `membrane_vm`).

The Rust core keeps a mirror of the field from its writes, the triggers' specs, the changed-trigger
index, WHERE bindings, WHEN watchers with their per-binding state, and the loop rule. Its drain is
resumable: `step` returns the next firing, this wrapper applies it to the store (with the trigger's
writer and chain, exactly as ch.VM._fire does), and the write comes back through the listener.

Kept in Python, so their results are exactly ch.VM's:
- defining (`_validate`, the slug, self's signature) and validating a trigger entity;
- writing a firing (`_writable` / `_call_envelope`, the position check, the upsert);
- lookups that follow the store's spelling rules: resolving a spec, `dispatches` (resolve_method),
  `vel`, `align` (callbacks from the core).

Blurp's learner works as on ch.VM: `observe` on every drained write, `on_fire` / `landed` for self's
triggers (live, shadow, skip). The listener only buffers (a listener must not write, and the core is busy while it
steps); the buffer is fed to the core before every step, under the VM's one evaluation lock.
"""
from __future__ import annotations

import collections
import json
import math
import sys
import threading
import time

from . import ch as CH
from . import writes as W
from .entities import MEMBRANE_NS, QuantumEntity

_PyVM = CH.PyVM               # ch.py's own VM: its helpers are reused (a test run may swap CH.VM)


DEPTH_MAX = 64          # nesting kept for the core (serde refuses past 128); deeper becomes its text


def _text(v) -> str:
    """str(v); an integer past Python's own digit limit has no str, so it gets a stand-in."""
    try:
        return str(v)
    except ValueError:
        return f"<{type(v).__name__}>"


def _clean(v, depth: int = 0):
    """A value the core's strict JSON can carry (Memo's review: one NaN in the store stopped the VM):
    NaN / Infinity -> None (as `cblstore.dumps` stores them), a lone surrogate -> U+FFFD (in keys too),
    an integer past a float's range -> the largest float of its sign (still a number), nesting past
    DEPTH_MAX -> its text, anything else not JSON -> its text."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v if abs(v) <= sys.float_info.max else (sys.float_info.max if v > 0 else -sys.float_info.max)
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        try:
            v.encode("utf-8")
            return v
        except UnicodeEncodeError:
            return v.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if depth >= DEPTH_MAX:
        return str(v)[:2000]
    if isinstance(v, dict):
        return {_clean(_text(k)): _clean(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x, depth + 1) for x in v]
    try:
        import numpy as _np
        if isinstance(v, _np.ndarray):
            return _clean(v.tolist(), depth)
        if isinstance(v, _np.generic):
            return _clean(v.item(), depth)
    except Exception:
        pass
    return str(v)


def _plain(v):
    return _clean(v)


def _dumps(v) -> str:
    return json.dumps(v, allow_nan=False, ensure_ascii=False)


_KEEP = object()        # a refresh row that leaves the mirror's position as it is


def _posa(ent):
    """A position as a float64 array; one that isn't all finite numbers is no position (vectorised: a
    seat holds tens of thousands of 1024-d positions)."""
    p = getattr(ent, "position", None)
    if p is None:
        return None
    try:
        import numpy as _np
        a = _np.asarray(p, dtype="<f8")
        if a.ndim != 1 or not _np.isfinite(a).all():
            return None
        return a
    except Exception:
        return None


def _pos(ent):
    a = _posa(ent)
    return None if a is None else a.tolist()


class _Host:
    """The core's callbacks into the store."""

    def __init__(self, est):
        self.est = est

    def resolve(self, spec):
        try:
            e = self.est._resolve_spec(spec)
        except Exception:
            return None
        return e.eid if e is not None else None

    def resolve_exact(self, ref):
        if not hasattr(self.est, "resolve"):
            return None
        try:
            e = self.est.resolve(ref, exact=True)
        except Exception:
            return None
        return e.eid if e is not None else None

    def dispatch(self, eid, name):
        ent = (self.est.docs or {}).get(eid)
        if ent is None:
            return None
        from .night import resolve_method
        try:
            m, _ = resolve_method(self.est, ent, str(name))
        except Exception:                              # noqa: BLE001 -- a lookup failing binds nothing
            return None
        return m.eid if m is not None else None

    def live(self, eid, position):
        ent = (self.est.docs or {}).get(eid)
        if ent is None:
            return None
        try:
            return _dumps([ent.cls or "", _clean(dict(ent.properties or {})), _pos(ent) if position else None])
        except Exception:                              # noqa: BLE001 -- the mirror's copy stands
            return None

    def vel(self, eid, now, thr):
        ent = (self.est.docs or {}).get(eid)
        return ent is not None and float(ent.ring_velocity(now) or 0.0) > float(thr)

    def align(self, eid, thr):
        ent = (self.est.docs or {}).get(eid)
        srv = getattr(self.est, "_srv", None)
        fld = getattr(srv, "efield", None) if srv is not None else None
        if ent is None or fld is None:
            return False
        from .eids import field_id
        s = fld.get(field_id(ent.eid))
        return s is not None and float(s.coherence or 0.0) < float(thr)


class NativeVM:
    def __init__(self, est, clock=time.time, paused=lambda: False):
        from . import membrane_vm as MV
        self.est = est
        self.clock = clock
        self.paused = paused
        self.core = MV.Core(_Host(est))
        self._buf: collections.deque = collections.deque()
        self._run = threading.RLock()
        self._wake = threading.Event()
        self._sweep_wanted = False
        self._stop = False
        self._thread = None
        self._trig_v = 0
        self._trig_sent = None
        self._spec: dict[str, tuple] = {}
        self._cls: dict[str, str] = {}
        self._errors: dict[str, str] = {}
        self._learner = None
        self._heard: dict[int, dict] = {}            # seq -> the write, for the learner's observe
        self._seq = 0
        self._trace: list | None = None
        self.recent: collections.deque = collections.deque(maxlen=CH.RECENT_FIRINGS)
        now = float(self.clock())
        self.on_sweep = None                      # journal.Journal.watch: a sweep is journalled with its time
        self._send(self.core.load_many, self._rows(list((est.docs or {}).keys())), now)
        for eid, ent in list((est.docs or {}).items()):
            self._cls[eid] = ent.cls or ""

    @property
    def trace(self) -> list | None:
        """Tests: the names of the triggers each write visited, in order (ch.VM: its `_on_write` calls)."""
        return self._trace

    @trace.setter
    def trace(self, lst) -> None:
        self._trace = lst
        with self._run:
            self.core.set_trace(lst is not None)

    def _traced(self) -> None:
        if self._trace is not None:
            for eid in self.core.take_trace():
                d = (self.est.docs or {}).get(eid)
                self._trace.append((d.properties or {}).get("name") if d is not None else eid)

    @property
    def learner(self):
        return self._learner

    @learner.setter
    def learner(self, lr) -> None:
        self._learner = lr
        with self._run:
            self.core.set_learner(lr is not None)

    def _rows(self, eids, props: bool = True, positions: bool = True) -> list:
        """[[eid, cls, props | None, position array | None]] from the live store, each row cleaned on its
        own (a row that still can't be carried is left out and said in `errors`)."""
        docs = self.est.docs or {}
        out = []
        for eid in eids:
            ent = docs.get(eid)
            if ent is None:
                continue
            try:
                out.append([eid, ent.cls or "", _clean(dict(ent.properties or {})) if props else None,
                            _posa(ent) if positions else _KEEP])
            except Exception as e:                       # noqa: BLE001
                self._errors[f"row:{eid}"] = f"{type(e).__name__}: {e}"[:200]
        return out

    @staticmethod
    def _pack(rows):
        """(the rows' JSON with each position's length, the positions as one little-endian f64 buffer)."""
        meta, bufs = [], []
        for eid, cls, props, pos in rows:
            if pos is _KEEP:
                meta.append([eid, cls, props, -2])
                continue
            meta.append([eid, cls, props, -1 if pos is None else int(pos.size)])
            if pos is not None:
                bufs.append(pos.tobytes())
        return _dumps(meta), b"".join(bufs)

    def _send(self, method, rows, *args) -> None:
        """Rows to the core in one call; if the batch can't be carried, row by row (a bad row said)."""
        try:
            method(*self._pack(rows), *args)
            return
        except Exception:                                # noqa: BLE001
            pass
        for row in rows:
            try:
                method(*self._pack([row]), *args)
            except Exception as e:                       # noqa: BLE001
                self._errors[f"row:{row[0]}"] = f"{type(e).__name__}: {e}"[:200]

    # -- hearing ----------------------------------------------------------------------------
    def attach(self) -> None:
        W.on_write(self._hear)

    def detach(self) -> None:
        W.off_write(self._hear)

    def _hear(self, w: dict) -> None:
        path = str(w.get("path") or "")
        if path.startswith("rel:"):
            return
        eid, prop = _PyVM._split(w)
        if eid.startswith(CH._TRIG_PREFIX):
            self._trig_v += 1
        self._seq += 1
        line = {"eid": eid, "prop": prop, "new": _plain(w.get("new")), "old": _plain(w.get("old")),
                "writer": w.get("writer") or "", "chain": w.get("chain"), "seq": self._seq}
        if self._learner is not None:
            self._heard[self._seq] = w
            if len(self._heard) > CH.QUEUE_MAX * 2:      # the core drops the oldest past QUEUE_MAX
                for k in list(self._heard)[: len(self._heard) - CH.QUEUE_MAX]:
                    self._heard.pop(k, None)
        ent = (self.est.docs or {}).get(eid)
        if not prop and w.get("new") is not None and w.get("old") is None and ent is not None:
            line["born"] = {"cls": ent.cls or "", "props": _plain(dict(ent.properties or {})), "position": _pos(ent)}
        cls = (ent.cls or "") if ent is not None else None
        try:
            js = _dumps(line)
        except Exception as e:                           # noqa: BLE001 -- said, the write still queued bare
            self._errors[f"write:{eid}.{prop}"] = f"{type(e).__name__}: {e}"[:200]
            line["new"] = line["old"] = None
            line.pop("born", None)
            js = _dumps(_clean(line))
        self._buf.append((js, float(self.clock()), eid, cls))
        self._wake.set()

    def _flush(self) -> None:
        while self._buf:
            s, now, eid, cls = self._buf.popleft()
            try:
                self.core.hear(s, now)
            except Exception as e:                       # noqa: BLE001 -- one write never stops the VM
                self._errors[f"hear:{eid}"] = f"{type(e).__name__}: {e}"[:200]
            if cls is not None and self._cls.get(eid) != cls:      # a class change is no write: follow it
                self._cls[eid] = cls
                self.core.set_cls(eid, cls)

    # -- defining (ch.VM's, unchanged) -------------------------------------------------------
    def define(self, name: str, when, where, write, owner: str | None = None) -> str:
        spec = CH._validate(when, where, write)
        eid = MEMBRANE_NS + f"{CH.CLASS}:{CH._slug(name)}"
        props = {"name": str(name), **spec, "owner": owner or "",
                 "from": [str(a[1]) for a in spec["when"] if len(a) > 1], "to": spec["write"][0]}
        props["owner_sig"] = CH._sig(self.est, eid) if owner == CH.SELF else ""
        old = (self.est.docs or {}).get(eid)
        if old is not None:
            for k, v in props.items():
                old.properties[k] = v
            self.est.upsert(old)
        else:
            self.est.upsert(QuantumEntity(eid=eid, cls=CH.CLASS, properties=props))
        self._trig_v += 1                         # its own definition, heard or not
        with self._run:
            self._flush()
            self.core.forget_state(eid)
        return eid

    def triggers(self) -> list:
        return [d for eid, d in list((self.est.docs or {}).items())
                if d.cls == CH.CLASS and eid.startswith(CH._TRIG_PREFIX)]

    def _valid(self, tr):
        """ch.VM._valid, its errors kept in this wrapper's dict."""
        p = tr.properties or {}
        fp = json.dumps([p.get("when"), p.get("where"), p.get("write")], default=str)
        hit = self._spec.get(tr.eid)
        if hit is None or hit[0] != fp:
            try:
                hit = (fp, CH._validate(p.get("when"), p.get("where"), p.get("write")))
            except (CH.Refused, TypeError, ValueError) as e:
                hit = (fp, None)
                self._errors[tr.eid] = f"invalid: {e}"[:200]
            self._spec[tr.eid] = hit
        return hit[1]

    _is_selfs = _PyVM._is_selfs

    def _sync(self) -> None:
        if self._trig_sent == self._trig_v or self.core.busy():
            return
        v = self._trig_v
        out = []
        for t in self.triggers():
            out.append({"eid": t.eid, "spec": self._valid(t), "mine": bool(self._is_selfs(t))})
        self.core.set_triggers(json.dumps(out))
        self._trig_sent = v

    # -- firing -----------------------------------------------------------------------------
    def _apply(self, f: dict, now: float) -> None:
        teid, prop, value = f["trigger"], f["prop"], f["value"]
        ent = (self.est.docs or {}).get(str(f.get("eid") or ""))
        if ent is None:
            self.core.error(teid, f"no target {f.get('eid')!r}")
            return
        try:
            value = (CH._call_envelope(value) if prop == "invoke" and isinstance(value, dict) and "request" in value
                     else CH._writable(value))
            if prop == "position" and (not isinstance(value, list) or
                                       (ent.position is not None and len(value) != len(ent.position))):
                raise CH.Refused("a position is a list of the field's dimension")
        except CH.Refused as e:
            self.core.error(teid, f"{f.get('expr')!r}: {e}"[:200])
            return
        mine = bool(f.get("mine"))
        tr = (self.est.docs or {}).get(teid)
        with W.resume(f.get("chain")), W.as_writer(f"trigger:{teid}"):
            mode = "live"
            if mine and self._learner is not None and tr is not None:
                mode = self._learner.on_fire(tr, f"{ent.eid}.{prop}", value)   # #576 C/H: shadow or asleep
            if mode == "skip":
                return
            was = ent.properties.get(prop)
            if mode == "shadow":                     # the prediction lands on the candidate itself
                ch_now = W.current() or {}
                tr.properties["predicted"] = {"path": self._learner._expected(f"{ent.eid}.{prop}"), "value": value,
                                              "chain": ch_now.get("id"), "ts": now}
                self.est.upsert(tr)
            elif prop == "position":
                ent.position = value
                self.est.upsert(ent)
            else:
                ent.properties[prop] = value          # in place: a concurrent writer's keys survive
                self.est.upsert(ent)
            if mine and mode == "live" and self._learner is not None:
                self._learner.landed(f"{ent.eid}.{prop}", value, was)   # #740: Blurp's own write, landed
        self.core.fired_ok(teid)
        self.recent.append({"trig": teid, "from": f.get("from") or ent.eid, "to": ent.eid, "ts": now, "mode": mode})

    def _apply_one(self, f: dict, now: float) -> None:
        """ch.VM's `_one`: a firing that raises is that trigger's error, and its remaining bindings
        for this write are skipped; the others carry on."""
        try:
            self._apply(f, now)
        except Exception as e:                       # noqa: BLE001
            self.core.error(f["trigger"], f"{type(e).__name__}: {e}"[:200])
            self.core.skip_trigger()

    # -- running ----------------------------------------------------------------------------
    def _steps(self, max_n: int) -> int:
        n = 0
        while n < max_n and not self.paused():
            with self._run:
                self._flush()
                self._sync()
                now = float(self.clock())
                r = self.core.step(now, json.dumps(W.current()))
                self._traced()
                if r is None:
                    break
                if r.startswith("#"):
                    n += 1
                    w = self._heard.pop(int(r[1:]), None)
                    if self._learner is not None and w is not None:
                        with W.resume(w.get("chain")):
                            try:
                                self._learner.observe(w)        # #576 C: score the predictions, then guess
                            except Exception as e:             # noqa: BLE001
                                self._errors["learner"] = f"{type(e).__name__}: {e}"[:200]
                    continue
                self._apply_one(json.loads(r), now)
        return n

    def drain(self, max_n: int = 1000) -> int:
        return self._steps(max_n)

    def sweep(self, now: float | None = None) -> None:
        if self.paused():
            return
        now = float(now if now is not None else self.clock())
        with self._run:
            self._flush()
            if self.core.busy():
                return
            self._sync()
            if self.on_sweep is not None:
                self.on_sweep(now)
            self._refresh()
            self.core.start_sweep(now, json.dumps(W.current()))
            while self.core.busy():
                r = self.core.step(now, json.dumps(W.current()))
                if r is None or r.startswith("#"):
                    break
                self._apply_one(json.loads(r), now)
                self._flush()

    def _refresh(self) -> None:
        """The sweep reads the live store, as ch.VM does: what changed in place (a position the field
        moved, a property edited before its upsert) has no write behind it, so the mirror re-reads it.
        Only what the sweep's triggers can read (Memo's review: a whole-store copy per sweep): the entities
        they can bind or name, and -- only when an `around` trigger exists -- every position."""
        want = json.loads(self.core.sweep_eids())
        self._refresh_rows(want["eids"], want["positions"])
        if want["all"]:
            named = set(want["eids"])
            self._send(self.core.refresh_many, self._rows([e for e in list((self.est.docs or {}).keys())
                                                           if e not in named], props=False))

    def _refresh_rows(self, eids, positions: bool = True) -> None:
        if eids:
            self._send(self.core.refresh_many, self._rows(eids, positions=positions))

    def request_sweep(self) -> None:
        self._sweep_wanted = True
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop = False

        def run():
            while not self._stop:
                self._wake.wait(5.0)
                self._wake.clear()
                if self._stop:
                    break
                try:
                    self.drain()
                    if self._sweep_wanted:
                        self._sweep_wanted = False
                        self.sweep()
                except Exception as e:              # noqa: BLE001
                    self._errors["worker"] = repr(e)[:200]
        self._thread = threading.Thread(target=run, daemon=True, name="ch-vm-native")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=5)

    # -- reading ----------------------------------------------------------------------------
    def _r(self) -> dict:
        with self._run:                       # Memo's review: the core's lock is only ever taken under _run
            return json.loads(self.core.report())

    @property
    def errors(self) -> dict:
        """ch.VM's errors: this wrapper's (invalid specs, the learner, the worker) and the core's."""
        return {**self._errors, **self._r()["errors"]}

    @property
    def fired(self) -> dict:
        return self._r()["fired"]

    @property
    def refused(self) -> dict:
        return self._r()["refused"]

    @property
    def dropped(self) -> int:
        return self._r()["dropped"]

    def report(self) -> dict:
        r = self._r()
        return {"triggers": len(self.triggers()), "queued": r["queued"] + len(self._buf), "dropped": r["dropped"],
                "fired": r["fired"], "refused": r["refused"], "errors": self.errors}
