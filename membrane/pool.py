"""The cell pool: a self-balancing background population of curiosity-driven cells
GRAZING the world's field, with clean shutdown + a CPU-bound lifecycle.

Stage 1 made a ``Cell`` a synchronous object the experiment drove directly. Stage 2
made a POPULATION of cells run in the background of the live proxy, each its own daemon
thread. This is stage 3: the population is no longer fixed-N -- it SELF-BALANCES. A
``Consolidator`` calls ``balance(target, world_dirs)`` each cycle and the pool buds
cells when the world's variety (``orient_dirs``) grows, retires them when it shrinks or
the pool is over the CPU cap. The cap is ``round(cpu_target * K)`` -- the population is
bounded by the CPU % setting, growing with variety up to that ceiling.

GRAZE, NOT PULL: a cell no longer grazes the user-turn buffer (which re-embeds the whole
unbounded log every tick). It grazes the WORLD's field (``srv.membrane.store``) -- whose
exemplar vectors are already computed -- applying its own veil (``_nudge``, pure
post-embed noise) to each pre-computed vec. Specialization is emergent (veil-driven),
the cost is numpy-only (no embed call), and the feeding boundary (the user-turn buffer)
stays untouched. See ``Cell.graze`` (membrane/cell.py).

DECENTRALIZED + ISOLATED: each cell owns its own ``Membrane`` (own field / audit / veil /
rng, single-writer thread); the only shared structure read is the world's
``ExemplarStore`` (one writer -- the consolidator -- N readers that snapshot
``list(store.exemplars)`` under the GIL, the same discipline consolidate.py:30-34
documents). No cell touches another cell. The pool never routes -- it just runs each
cell's own graze loop and sizes the population.

ANTI-THRASH: a fresh bud starts at ~0 free-energy, so retiring by lowest free-energy
every cycle would kill fresh buds before they grow. ``balance`` adjusts by AT MOST ONE
cell per call (rate-limit hysteresis) and a cell is not retire-eligible until it has
grazed ``_MIN_GRAZES_BEFORE_RETIRE`` times (a grace period) -- unless its covered
world-direction has collapsed (mature + its direction no longer in ``world_dirs``), in
which case it is eligible immediately. ``balance`` is called from the consolidator's
single thread, so the lock guards the cell/thread lists against ``stop()`` at shutdown.
"""
from __future__ import annotations

import os
import threading
import traceback

import numpy as np


# a cell must graze this many times before it is retire-eligible (the grace period that
# keeps a fresh bud -- which starts at ~0 free-energy -- from being retired before it
# has grown a cone). A cell whose covered world-direction has collapsed is eligible
# regardless of age.
_MIN_GRAZES_BEFORE_RETIRE = 3

# the curiosity gate strength assigned at bud (non-destructive maturation). The gate
# threshold is ``strength/(1+strength)`` -> 1.0 means a cell grazes only exemplars with
# cosine > 0.5 to its assigned cone, which matches the world's ``orient_dirs(cos_sep=0.5)``
# separation: each world exemplar aligns to at most one cell, so buds partition the field
# into distinct cones (distinct colors) without overlapping. Looser than the destructive
# ``differentiate`` (gate_strength=4.0 -> 0.8, the experiment's near-death cone): a living
# specialist keeps enough aligned exemplars to compute a dominant direction + stay colored.
_GATE_STRENGTH = 1.0


class CellPool:
    """A self-balancing background population of cells grazing the world's field.

    Each cell runs in its own daemon thread:
    ``while not stop.is_set() and not cell_stop.is_set(): cell.graze(world.store);
    cell_stop.wait(interval)``. ``graze`` is self-extinguishing (returns 0 when nothing
    is curious), so an idle cell costs only the wait. The global ``stop`` event ends the
    whole pool at shutdown; each cell also has its OWN ``cell_stop`` event so a single
    cell can be retired mid-run (``retire_cell``) without stopping its siblings.

    ``bud_cell`` / ``retire_cell`` mutate ``_cells`` / ``_threads`` under ``_lock``;
    ``balance(target, world_dirs)`` drives the population toward ``target`` (the world's
    variety capped by the CPU ceiling), adjusting by at most one cell per call.
    """

    def __init__(self, world, cell_factory, interval: float = 2.0,
                 field_prefix: str = "membrane-field-cell-",
                 audit_prefix: str = "membrane-audit-cell-") -> None:
        self._world = world                 # the world Membrane; cells graze world.store
        self._factory = cell_factory        # (tag, field_path, audit_path, nudge_seed) -> Cell
        self._interval = interval
        self._field_prefix = field_prefix
        self._audit_prefix = audit_prefix
        self._stop = threading.Event()      # global: ends the whole pool
        self._lock = threading.Lock()
        self._cells: list = []
        self._threads: list = []
        self._cell_stops: dict = {}         # id(cell) -> threading.Event
        self._grazes: dict = {}             # id(cell) -> graze count (retire-eligibility age)
        self._counter = 0                   # monotonic: unique field_path / nudge_seed per bud
        self._started = False

    # ---- population -------------------------------------------------------

    def bud_cell(self, orientation=None, orientation_strength: float = _GATE_STRENGTH):
        """Create a fresh curiosity cell (blank field, new veil seed -> emergent
        specialization), append it, and start its thread if the pool is already
        running. Returns the new ``Cell``.

        ``orientation`` (a world direction from ``balance``) commits the cell to a
        cone NON-DESTRUCTIVELY: setting ``orientation`` + ``orientation_strength``
        locks the curiosity gate (``curiosity._aligned``) so the cell grazes ONLY
        exemplars aligned to this cone -- emergent specialization WITHOUT the
        destructive field cut ``Cell.differentiate`` makes (the experiment's "death
        move", which trims a grown generalist to a near-empty cone). Different buds
        get different directions -> a living, colored, specialized population. It is
        set BEFORE the thread starts so the gate is active on the very first graze
        (race-free: a fresh cell's empty-field bootstrap is curious only about
        aligned situations from the first exemplar)."""
        with self._lock:
            self._counter += 1
            n = self._counter
            tag = f"cell-{n}"
            field_path = f"{self._field_prefix}{n}.json"
            audit_path = f"{self._audit_prefix}{n}.jsonl"
            cell = self._factory(tag, field_path, audit_path, n)
            if orientation is not None:
                cell.orientation = np.asarray(orientation, dtype=float)
                cell.orientation_strength = orientation_strength
            stop_evt = threading.Event()
            t = threading.Thread(target=self._run, args=(cell, stop_evt),
                                 name=f"cell-{cell.tag}", daemon=True)
            self._cells.append(cell)
            self._threads.append(t)
            self._cell_stops[id(cell)] = stop_evt
            self._grazes[id(cell)] = 0
            if self._started:
                t.start()
            return cell

    def retire_cell(self, cell, join_timeout: float = 5.0) -> None:
        """Stop one cell, join its thread (bounded), remove it, and delete its
        field/audit/cast files. No-op (and safe) if the cell is not in the pool."""
        with self._lock:
            if cell not in self._cells:
                return
            stop_evt = self._cell_stops.pop(id(cell), None)
            self._grazes.pop(id(cell), None)
            idx = self._cells.index(cell)
            t = self._threads[idx]
            del self._cells[idx]
            del self._threads[idx]
            field_path = cell.membrane.field_path
            audit_path = cell.membrane.audit_path
        # join + cleanup OUTSIDE the lock so a stuck cell cannot block balance/stop.
        if stop_evt is not None:
            stop_evt.set()
        t.join(timeout=join_timeout)
        for p in (field_path, audit_path,
                  str(field_path) + ".cast.json" if field_path else None):
            if not p:
                continue
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    def balance(self, target: int, world_dirs) -> None:
        """Drive the population toward ``target`` (the world's variety capped by the
        CPU ceiling), adjusting by AT MOST ONE cell per call (hysteresis). ``world_dirs``
        is the world's ``orient_dirs``: a new bud is committed to the next world
        direction not already covered by a living cell's orientation (so each cell
        specializes a DIFFERENT cone -> distinct colors), and a mature cell whose
        dominant direction no longer aligns to any current world direction has
        collapsed (the world's variety moved away from it). Buds when under target;
        retires the lowest-free-energy age-eligible (or collapsed) cell when over."""
        from .variety import free_energy
        with self._lock:
            cells = list(self._cells)
        n = len(cells)
        if n < target and not self._stop.is_set():
            # assign the new cell the next world direction not already covered by a
            # living cell's committed orientation -- each bud specializes a different
            # cone (the gate then keeps it grazing only that cone's exemplars).
            orientation = None
            if world_dirs:
                covered = set()
                for c in cells:
                    o = getattr(c, "orientation", None)
                    if o is None:
                        continue
                    covered.add(int(max(range(len(world_dirs)),
                                        key=lambda i: float(np.asarray(o) @ world_dirs[i]))))
                free_idx = next((i for i in range(len(world_dirs))
                                if i not in covered), None)
                if free_idx is not None:
                    orientation = world_dirs[free_idx]
            self.bud_cell(orientation=orientation)
            return
        if n <= target:
            return
        # over target: pick the best retire candidate (lowest free-energy among the
        # age-eligible, preferring collapsed cells). Eligible = grazed enough, OR a
        # mature cell whose cone no longer aligns to any current world direction
        # (collapsed -- the world's variety moved away from it).
        candidates = []
        for c in cells:
            grazes = self._grazes.get(id(c), 0)
            dom = c.dominant_dir()
            mature = dom is not None
            collapsed = False
            if mature and world_dirs:
                maxcos = max(float(dom @ d) for d in world_dirs)
                collapsed = maxcos < 0.5          # cone no longer matches any world dir
            eligible = collapsed or grazes >= _MIN_GRAZES_BEFORE_RETIRE
            if not eligible:
                continue
            try:
                fe = float(free_energy(c.membrane.store))
            except Exception:
                fe = 0.0
            candidates.append((collapsed, fe, c))
        if not candidates:
            return                      # nothing eligible yet -- wait (hysteresis)
        # collapsed first (they are dead), then lowest free-energy.
        candidates.sort(key=lambda t: (0 if t[0] else 1, t[1]))
        self.retire_cell(candidates[0][2])

    # ---- run loop ---------------------------------------------------------

    def start(self) -> None:
        """Spawn every cell thread. Call once at boot (after seeding initial cells via
        ``bud_cell``)."""
        with self._lock:
            self._started = True
            for t in self._threads:
                if not t.is_alive():
                    t.start()

    def _run(self, cell, cell_stop) -> None:
        while not self._stop.is_set() and not cell_stop.is_set():
            try:
                cell.graze(self._world.store)
            except Exception:
                # one crashing cell must not kill the pool or the proxy -- log and
                # keep serving its siblings. Cells are independent: a bad field does
                # not affect the other cells or the live single-Membrane path.
                traceback.print_exc()
            self._grazes[id(cell)] = self._grazes.get(id(cell), 0) + 1
            # wait() on the per-cell event returns immediately once retire_cell sets
            # it; the global _stop is re-checked next iteration (<= interval latency,
            # fine for a daemon thread at shutdown).
            cell_stop.wait(self._interval)

    def stop(self, join_timeout: float = 5.0) -> None:
        """Signal every cell thread to stop and join them (bounded). Idempotent.

        ``join_timeout`` bounds shutdown so a cell stuck mid-``graze`` cannot hang the
        proxy's exit -- the thread is a daemon, so an overrun is abandoned (it dies with
        the process) rather than blocking ``serve_forever``'s teardown."""
        self._stop.set()
        with self._lock:
            threads = list(self._threads)
            stops = list(self._cell_stops.values())
        for s in stops:
            s.set()
        for t in threads:
            t.join(timeout=join_timeout)

    # ---- read accessors (for /state) --------------------------------------

    @property
    def cells(self) -> list:
        """A snapshot of the live cells (for /state)."""
        with self._lock:
            return list(self._cells)