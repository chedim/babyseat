"""Consolidation cycles: the world's own time.

A background daemon thread (mirroring ``pool.CellPool``'s stop/join pattern) that
runs the world on its OWN clock, independent of the viewer's feed-tick. Each cycle:

  FEED phase  -- feeds the world ``feed_rate`` times via the SAME story-gen path
                 ``/world/step`` uses (LLM ``/api/chat`` + embedder
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

Armed/disarmed by ``srv.world_running`` (the viewer's RUN/STOP). STOPPED, the
thread idles on a cheap ``Event.wait`` (the world is paused). Stopped in
``main()``'s ``finally`` like the cell pool, so a mid-cycle digest cannot hang the
proxy exit (the thread is a daemon; ``stop`` joins with a bounded timeout).

THREAD-SAFETY: the FEED phase goes through ``proxy._run_feeds`` which holds
``srv.feed_lock``, so the background feeder, a live HTTP ``/world/step``, and a
``/adjust_narrative`` (which holds the same lock) never race the rolling
narrative (``srv.story_history``) or double-feed. The DIGEST phase is read-only
on the store (``thermo_state`` / ``novelty`` rebuild arrays; numpy releases the
GIL during the SVD, so a digest does not block request threads) and atomically
swaps the rebuilt ``FieldNGram`` reference -- worst case a live ``generate`` turn
uses a one-cycle-stale n-gram, never a torn one.
"""
from __future__ import annotations

import threading
import time
import traceback
import sys

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
    # Train ONLY on trusted (LLM-seeded) actions -- self-authored beats (the cell's
    # own n-gram stammer) are absorbed into the field but excluded here, else the
    # n-gram trains on its own output and spirals to gibberish.
    try:
        trusted = [e for e in store.exemplars
                   if e.action and not getattr(e, "self_authored", False)]
        n_trusted = len(trusted)
        if (getattr(mem, "_gen_lm", None) is None
                or n_trusted != getattr(mem, "_gen_size", 0)):
            actions = [e.action for e in trusted]
            if len(set(actions)) >= 8:
                from .generator import FieldNGram
                mem._gen_lm = FieldNGram(actions)
                mem._gen_size = n_trusted
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
        if not getattr(srv, "world_running", False):
            return self._idle                       # STOPPED -- the world is paused
        mem = srv.membrane
        from .proxy import _run_feeds, _feed_count  # lazy: avoid a circular import
        t0 = time.time()
        # ---- FEED phase: when the world is RUNNING it feeds itself autonomously
        # (the chronicle that follows the on-stage character). pov-follow is just
        # the natural behaviour of cells-act (beat_author == "cells").
        # abort_if_disarmed so a STOP takes effect within a feed or two, not
        # after a whole genesis chapter. The initial training burst feeds LLM
        # chapters too (the seeding phase). --
        stories: list = []
        fed = 0
        fr = _feed_count(float(getattr(srv, "feed_rate", 0.0)))
        if fr > 0:
            st = _run_feeds(srv, mem, fr, abort_if_disarmed=True, progression=True)
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
        mem.cast.cycles = cycles            # mirror so the next cast.save persists the clock
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
        # ---- self-balancing observer organism (the cell pool) ----------------
        # When --cells is on, a pool parallels this Membrane: its cells graze
        # ``mem.store`` (the world's field) and self-balance to the world's variety,
        # capped by ``cpu_target``. Bud when variety (``orient_dirs``) exceeds the
        # cell count; retire the stalest/collapsed when over the cap. The population
        # caps at ``round(cpu_target * 30)`` -- the "bound by the CPU %" ceiling.
        # Maturation is NON-DESTRUCTIVE: ``balance`` commits each new bud to a distinct
        # world direction (sets ``orientation`` -> the curiosity gate) so it grazes
        # only that cone -- specialization WITHOUT the ``Cell.differentiate`` field
        # cut (the experiment's death move, which trims a grown generalist to a
        # near-empty cone and leaves it directionless). No field mutation here.
        pool = getattr(srv, "cell_pool", None)
        if pool is not None:
            from .variety import orient_dirs
            cpu_cap = max(1, round(target * 30))            # K=30: 0.2->6, 1.0->30
            world_dirs = orient_dirs(mem.store, cpu_cap + 1, 0.5)
            pool_target = min(len(world_dirs), cpu_cap)
            try:
                pool.balance(pool_target, world_dirs)
            except Exception:
                traceback.print_exc()
        # ---- initial training burst: run a few cycles after the first chapter
        # lands, then auto-pause so the world is "trained" and waits for PLAY.
        # Gated on story_history so a model-down boot keeps retrying until a scene
        # actually feeds before the countdown starts. --initial-burst 0 disables.
        # Suppressed when the user has already started the world themselves
        # (``_user_run``): once the user drives it, training does not yank it back
        # to stopped.
        if getattr(srv, "initial_burst", 0) > 0 and getattr(srv, "story_history", None):
            srv.initial_burst -= 1
            if srv.initial_burst <= 0:
                # the default lifecycle after training: the autonomous chronicle
                # is authored by the on-stage characters' own n-gram voices
                # (cells) -- no LLM. The user can override via /world/controls
                # anytime (persisted, takes effect immediately).
                srv.beat_author = "cells"
                try:
                    from .proxy import _save_settings
                    _save_settings(srv, srv.backend,
                                   getattr(srv, "warmup_model", None))
                except Exception as e:
                    print(f"[consolidate] post-training settings persist failed "
                          f"({e})", file=sys.stderr)
                if not getattr(srv, "_user_run", False):
                    srv.world_running = False        # training done -> auto-pause
                    print("[consolidate] training complete -> cells authored "
                          "(auto-paused; press RUN)", file=sys.stderr)
                else:
                    print("[consolidate] training complete -> cells authored "
                          "(user-started; staying RUNNING)", file=sys.stderr)
        # cap spin: never cycle faster than ~5/s when both phases are tiny.
        # cycle_speed (set from the viewer's 1x/2x/4x playback controls) scales
        # the target cycle period -- 2x -> 0.1s, 4x -> 0.05s (the floor). 1x is
        # the default ~5 cycles/s.
        speed = float(getattr(srv, "cycle_speed", 1.0)) or 1.0
        return max(0.05, (0.2 / speed) - t_cycle)