"""#994 live switch: the goal graph runs on the field engine (`membrane_vm.Fields`) instead of its 12 triggers.

`MEMBRANE_FIELDS=1` turns it on. Without it nothing here runs, and `goalgraph.seed` defines the triggers as before.

- **The mirror:** at start, every Goal, Task, Decision and Wait is loaded into the engine. A write to one of them
  (heard from the store's write listener) goes to the engine as an outside write; an entity the engine hasn't
  seen is loaded first.
- **Write back:** what the engine wrote (its getters, its setters, its receipts) is applied to the store with the
  writer `trigger:field:<setter>`. To the rest of the seat it's a machine write, like a trigger's: the adapters
  (worker pool, wait checks, replan) hear it, a replan written by it is not a send-back, Blurp's learner skips it,
  and the journal counts it as the VM's.
- **Effects:** a planner call goes to `membrane::llm.call`, which the LLM adapter runs.
- **The clock:** every `goalgraph.WAIT_TICK_S` the engine's clock ticks, so armed waits come due (`Wait.due`).
- Writes the engine made are never fed back to it: they carry its writer and are skipped.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time

from . import writes as W
from .entities import MEMBRANE_NS

CLASSES = ("Goal", "Task", "Decision", "Wait")
WRITER = "trigger:field:"
LLM_EID = MEMBRANE_NS + "llm"


def enabled() -> bool:
    return os.environ.get("MEMBRANE_FIELDS", "").strip() == "1"


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
        n = 0
        for eid, d in list((self.est.docs or {}).items()):
            if d.cls in CLASSES:
                self.f.load(eid, d.cls, _json(dict(d.properties or {})))
                n += 1
        self.loaded = n

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
        if d is None or d.cls not in CLASSES:
            return
        self.q.put((eid, str(w.get("prop") or ""), w.get("new"), time.time()))

    def _one(self, eid: str, prop: str, new, now: float) -> None:
        d = (self.est.docs or {}).get(eid)
        if d is None:
            return
        if not prop or not self.f.has(eid):          # a birth, or one the mirror hasn't seen: load its state
            self.f.load(eid, d.cls, _json(dict(d.properties or {})))
            if not prop:
                return
        if prop == "position":
            return
        self.f.write(eid, prop, _json(new), now)
        self._apply()

    def _apply(self) -> None:
        out = json.loads(self.f.drain())
        for w in out.get("writes") or []:
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
                self.tick()
                next_tick = time.time() + GG.WAIT_TICK_S


def start(srv, thread: bool = True) -> Live | None:
    """Turn the goal graph's field engine on (MEMBRANE_FIELDS=1). Returns None when off."""
    if not enabled():
        return None
    live = Live(srv)
    srv.fields_live = live
    W.on_write(live.hear)
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
