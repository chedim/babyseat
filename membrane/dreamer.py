"""The dreamer: the cells' idle THOUGHT cycle, folded from the lab prototype
(experiments/dream_loop.py, OVERALL GREEN) into a background daemon.

The loop (no model, no responder call -- all numpy + one embed per candidate):
  1. PROPOSE   -- the field n-gram (membrane/generator.py FieldNGram) continued
                  from a trusted action's lead tokens: the word-granularity
                  generator, steered by the kNN neighborhood.
  2. STEER     -- the dream must serve an OPEN INTENTION (membrane/intent.py
                  board, cos >= STEER_SIM): thought without commitment is
                  daydreaming; the intention board is the steering wheel.
  3. VET       -- the gate as critic, three gates on the candidate vec:
       topic : novelty residual inside the field's on-manifold band AND the
               candidate near enough to the field (TOPIC_FLOOR) -- the dream
               must be about what the field KNOWS;
       fresh : not a near-duplicate of anything stored (below DUP_SIM);
       value : effective_rank_with would grow (information, not words).
  4. COMMIT    -- survivors enter via Membrane.absorb_exemplar with
                  self_authored=True: the QUARANTINE (absorbed for recall,
                  excluded from trusted corpora). A dream can never teacher
                  itself into canon on its own authority.
  5. ELECT     -- reality's vote, delivered HERE: a REAL turn landing near a
                  dream with the dream's topic is a GOOD outcome (promote:
                  self_authored -> False, outcome=confirmed); a near turn the
                  dream cannot serve is the re-ask (refute: remove). Promotion
                  is USE-WITH-GOOD-OUTCOME, never proximity, never self-declared.
  6. GC        -- dreams no real turn ever neared by the horizon are forgotten
                  (the discard move; nothing quarantined lives forever).

DUTY CYCLE: a closed-loop idle controller like the Consolidator -- one dream
wave when the proxy has been idle for ``idle`` seconds; the sleep stretches
when nothing is proposed (the n-gram exhausted its fresh band) and shrinks
when confirmations are flowing (demand for candidates). Hard caps:
MAX_DREAMS_HOT quarantined dreams in the field at once (a backed-up board
pauses proposing -- dreams are never evicted to make room for dreams).

Everything here NEVER runs on the chat path: the thread catches its own
exceptions, the store is only touched through the public mutators, and a
stop() joins cleanly. --no-dreamer leaves the field exactly as before.
"""

from __future__ import annotations

import random
import threading
import time
import traceback

import numpy as np

from .generator import FieldNGram
from .intent import STEER_SIM
from .variety import effective_rank_with, novelty, thermo_state
from .entities import (CLASS_TIMELINE, TimelineOps, CLASS_FAILURE)
TL_QUARANTINED = TimelineOps.TL_STATUS_QUARANTINED

DREAM_IDLE_S = 60.0        # a wave only when the proxy was idle this long
STEER_SIM_LOCAL = STEER_SIM       # 0.60 -- the dream_loop D5 steering line
TOPIC_FLOOR = 0.40        # minimum cos to the field (a thought ABOUT something)
NOV_KEEP_FRACTION = 0.75  # keep the null space non-degenerate (the lab lesson)
MAX_PROPOSE = 16          # n-gram attempts per wave
MAX_DREAMS_HOT = 24       # quarantined dreams allowed in the field at once
GC_HORIZON_S = 3 * 86400.0  # unconfirmed dreams older than this are forgotten
SEED_RNG = 42


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


class Dreamer:
    """One field's idle cognition thread."""

    def __init__(self, srv, idle: float = DREAM_IDLE_S) -> None:
        self.srv = srv
        self._idle = idle
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run,
                                        name="membrane-dreamer", daemon=True)
        self._rng = random.Random(SEED_RNG)
        self.stats = {"waves": 0, "committed": 0, "promoted": 0,
                      "refuted": 0, "gc": 0}

    # ---- lifecycle ----
    def start(self) -> None:
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout=join_timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                slept = self._cycle()
            except Exception:
                traceback.print_exc()
                slept = self._idle
            if self._stop.wait(slept):
                break

    # ---- the wave ----
    def _cycle(self) -> float:
        """One dream wave. Returns the seconds to sleep before the next."""
        srv = self.srv
        mem = srv.membrane
        ib = getattr(srv, "_intent_board", None)
        now = time.time()
        idle_for = now - getattr(srv, "last_turn_ts", now)
        if idle_for < self._idle:
            return max(1.0, self._idle - idle_for)   # chat awake: wait
        open_intents = (ib.open_intents() if ib is not None else [])
        if not open_intents:
            # no board / empty board: dreaming is unsteered -> skip (v2's
            # lesson: unsteered dreams confirm near-randomly and read salad)
            return self._idle * 4
        store = mem.store
        trusted = [e.action for e in store.exemplars if not e.self_authored]
        if len(trusted) < 4:
            return self._idle * 4        # a young field has nothing to dream ON
        hot = sum(1 for e in store.exemplars
                  if e.self_authored and e.outcome == "dream")
        if hot >= MAX_DREAMS_HOT:
            return self._idle * 2        # board backed up: let reality vote first
        # ---- propose ----
        ngram = FieldNGram(trusted, n=3)
        commit_vecs: list[tuple[str, np.ndarray]] = []
        seen_texts = {e.action for e in store.exemplars}
        attempts = 0
        while attempts < MAX_PROPOSE and len(commit_vecs) < 3:
            attempts += 1
            anchor = trusted[self._rng.randrange(len(trusted))]
            text = ngram.generate(anchor.split()[:2], self._rng,
                                  max_tokens=12).strip()
            if not text or len(text.split()) < 3 or text in seen_texts:
                continue
            vec = _unit(mem._nudge(mem.embedder.embed([text])[0]))
            verdict = self._vet(mem, vec, ib)
            if verdict == "impossible":
                # the learned-physics lane: recorded, not proposed (the
                # est commit below keeps impossible branches as entities)
                est = getattr(srv, "entity_store", None)
                if est is not None:
                    est.make_timeline(
                        terminal=text, vec=list(vec),
                        status=TimelineOps.TL_STATUS_IMPOSSIBLE)
                    self.stats["impossible"] = \
                        self.stats.get("impossible", 0) + 1
                continue
            if verdict == "quarantined":
                commit_vecs.append((text, vec))
        if not commit_vecs:
            return self._idle * 2        # the fresh band is exhausted for now
        # ---- commit (TIMELINE ENTITIES -- the dreamer v2 substrate) ----
        # The dreamed branch is an entity child of the true timeline
        # (session:now): quarantine is a STATUS on the doc, not a field
        # cell. The entity store write-throughs to the shared layer (the
        # parallel seats see each other's open branches) -- but the branch
        # itself never authors a turn (the render surface is suggestions).
        est = getattr(srv, "entity_store", None)
        if est is None:
            return self._idle * 4      # no entity layer: nothing to commit TO
        for text, vec in commit_vecs:
            need, nsim = (ib.nearest(vec) if ib is not None
                          else (None, 0.0))
            branch_sim = 0.0
            try:
                now_ent = est.docs.get("membrane::session:now")
                np_ = getattr(now_ent, "position", None)
                if np_:
                    import numpy as _np
                    nv = _np.asarray(np_, dtype=float)
                    nn = float(_np.linalg.norm(nv))
                    if nn > 1e-12:
                        vv = _np.asarray(vec, dtype=float)
                        branch_sim = round(
                            float(vv @ (nv / nn)) / max(float(_np.linalg.norm(vv)), 1e-12),
                            4)
            except Exception:
                pass
            ent = est.make_timeline(
                terminal=text, vec=list(vec),
                seed_intent=(need.describe() if need is not None else None),
                branch_sim=branch_sim,
            )
            if ent is not None:
                self.stats["committed"] += 1
                from .opencode import _write_audit
                _write_audit(mem, {"event": "dream", "op": "commit-tl",
                                   "text": text[:200], "eid": ent.eid})
        self.stats["waves"] += 1
        # sleep until likely idle again; a busy session wakes us anyway
        # (the GC is the ephemeral TTL / the elect refutes now -- no janitor)
        return self._idle

    def _vet(self, mem, vec: np.ndarray, ib) -> str | None:
        """STEER + TOPIC + FRESH + VALUE + PHYSICS -- the lab critic, on the
        live field. Returns "ok" (commit as quarantined), "impossible"
        (commit as a learned-physics entity -- near a known FailureMode or
        behind an inherited latch the branch violates), or None (not a
        dream: unsteered/stale/dup/no-growth)."""
        steer = ib.nearest(vec)[1] if ib is not None else -1.0
        if steer < STEER_SIM_LOCAL:
            return None
        # PHYSICS (the impossible lane): a branch whose path lands near a
        # known FailureMode (cos >= 0.85) is a seductive dead end -- kept
        # as impossible (the field's learned physics), never committed as
        # a candidate.
        est = getattr(self.srv, "entity_store", None)
        if est is not None:
            import numpy as _np
            fv = _np.asarray(vec, dtype=float)
            fn = float(_np.linalg.norm(fv))
            if fn > 1e-12:
                fv = fv / fn
                for ent in est.docs.values():
                    if ent.cls != CLASS_FAILURE:
                        continue
                    ev = getattr(ent, "position", None) \
                        or getattr(ent, "embedding", None)
                    if not ev:
                        continue
                    eav = _np.asarray(ev, dtype=float)
                    en = float(_np.linalg.norm(eav))
                    if en > 1e-12 and float(fv @ (eav / en)) >= 0.85:
                        return "impossible"
        nov = novelty(vec, mem.store, keep_fraction=NOV_KEEP_FRACTION)
        _, top_sim = mem.store.top_k(vec, k=1)[0]
        topic_ok = (0.0 <= nov) and (top_sim >= TOPIC_FLOOR)
        fresh_ok = top_sim < mem.dedup_sim
        grows = effective_rank_with(mem.store, vec) > \
            thermo_state(mem.store).get("eff_rank", 0.0) + 1e-6
        if not (topic_ok and fresh_ok and grows):
            return None
        return "quarantined"

    def _gc(self, mem) -> None:
        """Horizon, timeline-shaped: quarantined branches older than the
        horizon are removed from the entity store (the confirmed ones stay
        -- they are the record of what reality voted for). The shared docs
        ride the ephemeral TTL too (server-side); this in-process sweep
        keeps the RAM layer honest."""
        est = getattr(self.srv, "entity_store", None)
        if est is None:
            return
        now = time.time()
        doomed = []
        for ent in list(est.docs.values()):
            if ent.cls != CLASS_TIMELINE:
                continue
            props = ent.properties or {}
            if props.get("status") != TL_QUARANTINED:
                continue
            born = float(props.get("born") or 0.0)
            if born and now - born > GC_HORIZON_S:
                doomed.append(ent.eid)
        for eid in doomed:
            est.remove(eid)
        if doomed:
            self.stats["gc"] += len(doomed)
            from .opencode import _write_audit
            _write_audit(mem, {"event": "dream", "op": "gc-tl",
                               "removed": len(doomed)})


def vote_on_turn(srv, ex) -> None:
    """REALITY'S ELECTION, timeline-shaped, called from the per-learn hook
    for every REAL (non-dream) exemplar: quarantined branches near this
    turn's vec get the vote -- a branch whose seed_intent the turn serves
    is CONFIRMED (status flips in place); a near branch serving nothing
    open is REFUTED (removed). One vec (already a vec) -- no model call,
    bounded by the open-branch cap."""
    mem = srv.membrane
    est = getattr(srv, "entity_store", None)
    if est is None:
        return
    try:
        import numpy as _np
        vec = _np.asarray(ex.vec, dtype=float)
        n = float(_np.linalg.norm(vec))
        if n <= 1e-12:
            return
        vec = (vec / n).tolist()
        ib = getattr(srv, "_intent_board", None)
        # which open intentions this turn serves (the elect's need check)
        intent_sims: dict = {}
        if ib is not None:
            need, nsim = ib.nearest(_np.asarray(vec))
            if need is not None and nsim >= STEER_SIM_LOCAL:
                intent_sims[need.describe()] = nsim
        res = est.elect_timeline(vec, intent_sims)
        if res.get("confirmed") or res.get("refuted"):
            from .opencode import _write_audit
            _write_audit(mem, {"event": "dream", "op": "elect",
                               **res,
                               "by_turn": (ex.situation or "")[:160]})
        if getattr(srv, "dreamer", None) is not None:
            srv.dreamer.stats["promoted"] += res.get("confirmed", 0)
            srv.dreamer.stats["refuted"] += res.get("refuted", 0)
    except Exception:
        traceback.print_exc()
