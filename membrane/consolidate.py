"""Consolidation cycles: the world's own time.

A background daemon thread (mirroring ``pool.CellPool``'s stop/join pattern) that
runs the world on its OWN clock, independent of the viewer's feed-tick. Each cycle:

  FEED phase  -- feeds the world ``feed_rate`` times via the SAME story-gen path
                 the ``/feed`` endpoint uses (LLM ``/api/chat`` + embedder
                 ``/api/embed`` + absorb). This is the dial that "controls the
                 models": feed_rate > 0 makes real LLM + embed calls every cycle.

  DIGEST phase -- CPU consolidation: refresh the field's ``thermo_state``, sweep a
                 ``budget`` of cells recomputing each one's ``novelty`` (each an
                 SVD -- the same per-candidate SVD the curiosity path does), and
                 rebuild the n-gram the self-author cell speaks from when the field
                 grew. No field mutation, no LLM -- pure numeric digestion. The
                 ``budget`` is ramped by a CLOSED LOOP that targets ``cpu_target``
                 (the fraction of each cycle's wall time spent digesting): measure
                 the digest wall-time, compare to the smoothed cycle time, nudge the
                 budget up if under target, down if over. So the CPU slider truly
                 "ramps the consolidation speed up based on target CPU usage."

Armed/disarmed by ``srv.consolidating`` (the viewer's PLAY/PAUSE). Disarmed, the
thread idles on a cheap ``Event.wait`` (the world is paused). Stopped in
``main()``'s ``finally`` like the cell pool, so a mid-cycle digest cannot hang the
proxy exit (the thread is a daemon; ``stop`` joins with a bounded timeout).

THREAD-SAFETY: the FEED phase goes through ``proxy._run_feeds`` which holds
``srv.feed_lock``, so the background feeder and a live HTTP ``/feed`` never race
the rolling narrative (``srv.story_history``) or double-feed. The DIGEST phase is
read-only on the store (``thermo_state`` / ``novelty`` rebuild arrays; numpy
releases the GIL during the SVD, so a digest does not block request threads) and
atomically swaps the rebuilt ``FieldNGram`` reference -- worst case a live
``generate`` turn uses a one-cycle-stale n-gram, never a torn one.
"""
from __future__ import annotations

import threading
import time
import traceback

import numpy as np


def _digest(srv, mem, budget: float) -> dict:
    """One CPU-consolidation pass. No model, no field mutation.

    Refreshes the global ``thermo_state`` (one SVD -- keeps /state's free_energy /
    eff_rank fresh between viewer polls), sweeps ``budget`` cells recomputing each
    one's ``novelty`` (one SVD per cell -- the per-candidate SVD the curiosity path
    already pays), and rebuilds the ``FieldNGram`` (the local "model" the
    self-author cell speaks from) when the field grew since the last build. Returns
    a small stats dict the cycle rolls into ``srv.consolidation_stats``."""
    from .variety import thermo_state, novelty

    store = mem.store
    n = len(store)
    if n < 2:
        return {"swept": 0, "mean_novelty": 0.0, "settled": 0,
                "eff_rank": 0.0, "free_energy": 0.0}
    thermo = thermo_state(store)                       # one SVD -> global refresh
    budget = int(max(0, min(round(budget), n)))
    cursor = int(getattr(srv, "_digest_cursor", 0)) % n
    nov_sum = 0.0
    settled = 0
    THRESH = 1.0                                       # a "settled" memory sits on-manifold
    for k in range(budget):
        ex = store.exemplars[(cursor + k) % n]
        v = np.asarray(ex.vec, dtype=float)
        try:
            nv = float(novelty(v, store))
        except Exception:
            nv = 0.0
        nov_sum += nv
        if nv < THRESH:
            settled += 1
    srv._digest_cursor = (cursor + budget) % n
    # rebuild the n-gram if the field grew since the last build -- a real
    # consolidation side-effect: the next self-author act comes from refreshed memory.
    try:
        if (getattr(mem, "_gen_lm", None) is None
                or len(store) != getattr(mem, "_gen_size", 0)):
            actions = [e.action for e in store.exemplars if e.action]
            if len(set(actions)) >= 8:
                from .generator import FieldNGram
                mem._gen_lm = FieldNGram(actions)
                mem._gen_size = len(store)
    except Exception:
        pass
    return {"swept": budget,
            "mean_novelty": nov_sum / max(budget, 1),
            "settled": settled,
            "eff_rank": float(thermo.get("eff_rank", 0.0)),
            "free_energy": float(thermo.get("free_energy", 0.0))}


class Consolidator:
    """The world's own clock: a daemon thread that, when armed, feeds the world
    (the models) and digests it (CPU) on a closed-loop CPU target."""

    def __init__(self, srv, idle: float = 1.0) -> None:
        self.srv = srv
        self._idle = idle
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="membrane-consolidator",
                                        daemon=True)
        # closed-loop controller state: ramp `budget` (cells swept/cycle) so the
        # digest's share of each cycle's wall time converges on `cpu_target`.
        self._budget = 4.0
        self._ema_cycle = 1.0
        self._ema_digest = 0.0
        self._sleep = idle

    def start(self) -> None:
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout=join_timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._sleep = self._cycle()
            except Exception:
                traceback.print_exc()
                self._sleep = self._idle
            if self._stop.wait(self._sleep):
                break

    def _cycle(self) -> float:
        srv = self.srv
        if not getattr(srv, "consolidating", False):
            return self._idle                       # disarmed -- the world is paused
        mem = srv.membrane
        from .proxy import _run_feeds, _feed_count  # lazy: avoid a circular import
        t0 = time.time()
        # ---- FEED phase: the world feeds itself from the genesis cache on its own
        # clock. feed_rate is a CONTINUOUS rate (feeds/cycle); _feed_count resolves
        # the fractional tail to an integer count this cycle. Each feed is a cached
        # chronicle scene -> a cheap embed, not a model call (the model is called
        # only when the cache runs low, ~1 call per _CHAPTER feeds). --
        fr = _feed_count(float(getattr(srv, "feed_rate", 0.0)))
        stories: list = []
        fed = 0
        if fr > 0:
            st = _run_feeds(srv, mem, fr)
            stories = st.get("stories", [])
            fed = int(st.get("fed", 0))
        # ---- DIGEST phase: CPU consolidation (no model, no field mutation) -----
        t_d0 = time.time()
        digest = _digest(srv, mem, self._budget)
        t_digest = time.time() - t_d0
        t_cycle = time.time() - t0
        # ---- closed loop: ramp budget toward the CPU-target duty fraction -------
        self._ema_cycle = 0.7 * self._ema_cycle + 0.3 * t_cycle
        self._ema_digest = 0.7 * self._ema_digest + 0.3 * t_digest
        duty = self._ema_digest / max(self._ema_cycle, 1e-6)
        target = float(getattr(srv, "cpu_target", 0.0))
        n = max(1, len(mem.store))
        self._budget += (target - duty) * n * 0.5
        self._budget = max(0.0, min(self._budget, float(n)))
        cycles = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0)) + 1
        srv.consolidation_stats = {
            "cycles": cycles,
            "cpu_actual": round(duty, 3),
            "cpu_target": round(target, 3),
            "swept": digest.get("swept", 0),
            "budget": round(self._budget, 1),
            "mean_novelty": round(digest.get("mean_novelty", 0.0), 3),
            "settled": digest.get("settled", 0),
            "eff_rank": round(digest.get("eff_rank", 0.0), 2),
            "free_energy": round(digest.get("free_energy", 0.0), 2),
            "fed": fed,
            "cache": len(getattr(srv, "story_cache", [])),
            "stories": stories,
        }
        # cap spin: never cycle faster than ~5/s when both phases are tiny
        return max(0.05, 0.2 - t_cycle)