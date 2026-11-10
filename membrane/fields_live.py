"""#994 live switch: the goal graph runs on the field engine (`membrane_vm.Fields`) instead of its 12 triggers.

On by default whenever the native engine is built; `MEMBRANE_FIELDS=0` turns it off, and then (or without the
engine) nothing here runs and `goalgraph.seed` defines the triggers as before.

- **The mirror:** at start, every Goal, Task, Decision and Wait is loaded into the engine. A write to one of them
  (heard from the store's write listener) goes to the engine as an outside write; an entity the engine hasn't
  seen is loaded first.
- **Write back:** what the engine wrote (its getters, its setters, its receipts) is applied to the store with the
  writer `trigger:field:<setter>`. To the rest of the seat it's a machine write, like a trigger's: the adapters
  (worker pool, wait checks, replan) hear it, a replan written by it is not a send-back, Blurp's learner skips it,
  and the journal counts it as the VM's.
- **Effects:** a planner call goes to `membrane::llm.call`, which the LLM adapter runs.
- **Getters** (`packs/goalgraph.c1f`: `finished`, `open_blockers`, `due`) compute once at start, as a rebuild: the
  values are stored, no setter runs. After that they recalculate when what they read changes.
- **The clock:** the engine ticks on the VM's sweeps (the beat the old `every` trigger fired on), so armed waits
  come due (`Wait.due`); a seat without a VM ticks every `goalgraph.WAIT_TICK_S`.
- Writes the engine made are never fed back to it: they carry its writer and are skipped.
- **Packs:** cliffhanglets in `.c1f` files load at runtime: `membrane/packs/*.c1f`, then `Pack` entities in the field
  (`membrane::pack:<name>`, the text in `c1f`; a field pack replaces a file pack of the same name). Editing a Pack
  entity reloads it; a pack that doesn't parse is refused, the last good one stays, and the error is written on the
  entity (`error`). Every class a loaded pack declares is mirrored, and new getters compute as a rebuild.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from . import writes as W
from .entities import MEMBRANE_NS

CLASSES = ("Goal", "Task", "Decision", "Wait")
# cliffhanglet packs (`.c1f`) loaded at runtime: files shipped in membrane/packs/, and Pack entities in the field
# (`membrane::pack:<name>`, the text in `c1f`), which replace a file pack of the same name and reload when edited
PACK_DIR = Path(__file__).resolve().parent / "packs"
PACK_CLASS = "Pack"
PACK_NS = MEMBRANE_NS + "pack:"


def is_pack(eid: str, d) -> bool:
    """A Pack entity, or any `membrane::pack:<name>` holding `c1f` text (an agent's entity tool may save it under
    another class)."""
    return d.cls == PACK_CLASS or (str(eid).startswith(PACK_NS) and "c1f" in (d.properties or {}))
WRITER = "trigger:field:"
LLM_EID = MEMBRANE_NS + "llm"


def enabled() -> bool:
    """On by default: the goal graph runs on the field. `MEMBRANE_FIELDS=0` is the kill switch, and a seat whose
    native engine isn't built (or predates `Fields.drain`) keeps the triggers, so the goal graph never goes dark."""
    if os.environ.get("MEMBRANE_FIELDS", "").strip() == "0":
        return False
    try:
        from . import membrane_vm as MV
    except Exception:
        return False
    return hasattr(getattr(MV, "Fields", None), "drain")


def _json(v) -> str:
    return json.dumps(v, default=str)


class _Host:
    """The engine's host callbacks: the goal graph's setters bind by class, so none of these lookups is used."""
    def resolve(self, s): return None
    def resolve_exact(self, s): return None
    def dispatch(self, e, m): return None
    def vel(self, e, n, t): return False
    def align(self, e, t): return False
    def live(self, e, p): return None


class Live:
    def __init__(self, srv):
        from . import membrane_vm as MV
        self.srv, self.est = srv, srv.entity_store
        self.f = MV.Fields(_Host())
        self.q: queue.Queue = queue.Queue()
        self.busy = threading.Lock()
        self.stopped = threading.Event()
        self.errors = 0
        self.swept = False
        self.pack_eids: dict = {}
        self.classes: set = set(CLASSES)
        self.file_mtimes: dict = {}
        self._scan_files()
        for eid, d in list((self.est.docs or {}).items()):
            if is_pack(eid, d):
                self._pack_entity(eid, d)
        self.loaded = self._mirror()
        self.f.rebuild(time.time())          # the getters compute once; their values are stored, no setter runs
        self._apply()

    # ---- packs ---------------------------------------------------------------------------------------
    def _pack(self, name: str, text: str, eid) -> bool:
        """Load or replace a pack. A broken one is refused (the last good one stays) and the error is written on
        its entity (`error`), or printed for a file pack."""
        try:
            self.f.set_pack(name, text)
            err = ""
        except Exception as e:                    # noqa: BLE001
            err = str(e)
            print(f"[fields] {err}"[:300], file=sys.stderr)
        if eid:
            self.pack_eids[name] = eid
        # every Pack entity shows its pack's state: an import that arrived later clears a waiting pack's error
        waiting = json.loads(self.f.pack_errors())
        for n, e in list(self.pack_eids.items()):
            want = err if n == name and err else waiting.get(n, "")
            d = (self.est.docs or {}).get(e)
            if d is not None and (d.properties or {}).get("error", "") != want:
                with W.as_writer(WRITER + "pack"):
                    d.properties = {**(d.properties or {}), "error": want}
                    self.est.upsert(d, embedder=None)
        self.classes = set(CLASSES) | set(self.f.classes())
        return not err

    def _pack_entity(self, eid: str, d) -> bool:
        p = d.properties or {}
        name = str(p.get("name") or (eid[len(PACK_NS):] if eid.startswith(PACK_NS) else eid))
        return self._pack(name, str(p.get("c1f") or ""), eid)

    def _mirror(self) -> int:
        """Load every entity of a mirrored class the engine hasn't seen (after a pack adds a class too)."""
        n = 0
        for eid, d in list((self.est.docs or {}).items()):
            if d.cls in self.classes:
                if not self.f.has(eid):
                    self.f.load(eid, d.cls, _json(dict(d.properties or {})))
                n += 1
        return n

    # ---- hearing the store ---------------------------------------------------------------------------
    def hear(self, w: dict) -> None:
        """The store's write listener: cheap, never writes, only queues."""
        writer = str(w.get("writer") or "")
        if writer.startswith(WRITER):
            return                                   # the engine's own write coming back
        eid = w.get("eid")
        if not eid or str(w.get("path") or "").startswith("rel:"):
            return
        d = (self.est.docs or {}).get(eid)
        if d is None or (d.cls not in self.classes and not is_pack(eid, d)):
            return
        self.q.put((eid, str(w.get("prop") or ""), w.get("new"), time.time()))

    def _one(self, eid: str, prop: str, new, now: float) -> None:
        d = (self.est.docs or {}).get(eid)
        if d is None:
            return
        if is_pack(eid, d):                          # a pack written: reload it, mirror any new class, rebuild
            if prop in ("", "c1f", "name") and self._pack_entity(eid, d):
                self._mirror()
                self.f.rebuild(now)                  # a new getter on an old world: computed, no setter runs
                self._apply()
            return
        if not prop or not self.f.has(eid):          # a birth, or one the mirror hasn't seen: load its state
            self.f.load(eid, d.cls, _json(dict(d.properties or {})))
            if not prop:
                self.f.settle(now)                   # its getters compute; what references it recalculates
                self._apply()
                return
        if prop == "position":
            return
        self.f.write(eid, prop, _json(new), now)
        self._apply()

    def _apply(self) -> None:
        out = json.loads(self.f.drain())
        for w in out.get("writes") or []:
            if "born" in w:                          # a setter created it (`<Class>:<key>` of its own or imported class)
                if w["eid"] not in (self.est.docs or {}):
                    from .entities import QuantumEntity
                    with W.as_writer(WRITER + "create"):
                        self.est.upsert(QuantumEntity(eid=w["eid"], cls=w["born"], properties={"silent_birth": True}), embedder=None)
                continue
            d = (self.est.docs or {}).get(w["eid"])
            if d is None or (d.properties or {}).get(w["prop"]) == w["new"]:
                continue
            with W.as_writer(WRITER + str(w.get("writer") or "").removeprefix("field:")):
                d.properties = {**(d.properties or {}), w["prop"]: w["new"]}
                self.est.upsert(d, embedder=None)
        for c in out.get("calls") or []:
            llm = (self.est.docs or {}).get(LLM_EID)
            if llm is None:
                continue
            with W.as_writer(WRITER + "plan"):
                llm.properties = {**(llm.properties or {}), "call": c}
                self.est.upsert(llm, embedder=None)

    # ---- running -------------------------------------------------------------------------------------
    def settle(self) -> None:
        """Process everything queued now (tests; the worker does the same)."""
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            self._run_one(item)

    def _run_one(self, item) -> None:
        with self.busy:
            try:
                self._one(*item)
            except Exception as e:                   # noqa: BLE001 -- one bad write never stops the goal graph
                self.errors += 1
                print(f"[fields] write {item[0]}.{item[1]} failed: {e!r}"[:300], file=sys.stderr)

    def tick(self, now: float | None = None) -> None:
        with self.busy:
            try:
                self.f.tick(now or time.time())
                self._apply()
            except Exception as e:                   # noqa: BLE001
                self.errors += 1
                print(f"[fields] tick failed: {e!r}"[:300], file=sys.stderr)

    def _scan_files(self) -> bool:
        """Load the pack files that are new or changed since the last scan (their mtime). -> any loaded."""
        changed = False
        for fp in sorted(PACK_DIR.glob("*.c1f")) if PACK_DIR.is_dir() else []:
            try:
                m = fp.stat().st_mtime
            except OSError:
                continue
            if self.file_mtimes.get(fp.name) == m:
                continue
            self.file_mtimes[fp.name] = m
            if self._pack(fp.stem, fp.read_text(), None):
                changed = True
        return changed

    def on_sweep(self, now: float) -> None:
        """The VM's sweep is the clock (the beat the old `every` trigger fired on): the queue first, then a tick.
        Each beat also reloads pack files edited since the last one."""
        self.settle()
        with self.busy:
            if self._scan_files():
                self._mirror()
                self.f.rebuild(now)
                self._apply()
        self.tick(now)

    def _worker(self) -> None:
        from . import goalgraph as GG
        next_tick = time.time() + GG.WAIT_TICK_S
        while not self.stopped.is_set():
            try:
                item = self.q.get(timeout=max(0.05, next_tick - time.time()))
                self._run_one(item)
            except queue.Empty:
                pass
            if time.time() >= next_tick:
                if not self.swept:                   # no VM sweeping: keep our own beat
                    self.tick()
                next_tick = time.time() + GG.WAIT_TICK_S


def start(srv, thread: bool = True) -> Live | None:
    """Turn the goal graph's field engine on (the default; MEMBRANE_FIELDS=0 is off). Returns None when off."""
    if not enabled():
        return None
    live = Live(srv)
    srv.fields_live = live
    W.on_write(live.hear)
    vm = getattr(srv, "ch_vm", None)
    if vm is not None and hasattr(vm, "on_sweep"):   # the clock ticks on the VM's sweeps, chained after the journal's
        prev = vm.on_sweep
        def _sweep(now, _prev=prev, _live=live):
            if _prev is not None:
                _prev(now)
            _live.on_sweep(now)
        vm.on_sweep = _sweep
        live.swept = True
    if thread:
        threading.Thread(target=live._worker, daemon=True, name="fields-live").start()
    print(f"[fields] the goal graph runs on the field engine: {live.loaded} entities loaded", file=sys.stderr)
    return live


def stop(srv) -> None:
    live = getattr(srv, "fields_live", None)
    if live is not None:
        W.off_write(live.hear)
        live.stopped.set()
        srv.fields_live = None
