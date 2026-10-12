"""The dreamer: the seat's idle THOUGHT cycle.

The loop (no model, no responder call -- all numpy + one embed per candidate):
  1. PROPOSE   -- the trusted corpus's n-gram (membrane/generator.py
                  FieldNGram) continued from a trusted model sentence's lead
                  tokens: the word-granularity generator, steered by the kNN
                  neighborhood. The corpus is the entity field's DISCOURSE
                  (DreamSource, efmind.py) -- the model's applied sentences.
  2. STEER     -- the dream must serve an OPEN INTENTION (membrane/intent.py
                  board, cos >= STEER_SIM): thought without commitment is
                  daydreaming; the intention board is the steering wheel.
  3. VET       -- SurpriseSource.critic, two gates on the candidate vec:
       topic : novelty residual inside the field's on-manifold band AND the
               candidate near enough to the field (TOPIC_FLOOR) -- the dream
               must be about what the field KNOWS;
       fresh : not a near-duplicate of anything stored (below DUP_SIM).
       physics: a branch whose path lands near a known FailureMode is
               kept as IMPOSSIBLE -- learned physics, never a candidate.
  4. COMMIT    -- survivors are TIMELINE ENTITIES (the dreamer v2 substrate):
                  quarantine is a STATUS on the doc, not a field cell. A dream
                  never authors a turn on its own authority (the render
                  surface is suggestions).
  5. ELECT     -- reality's vote, delivered via vote_on_turn (the per-learn
                  hook): a REAL turn landing near a dream with its topic is a
                  GOOD outcome (CONFIRM); a near turn serving nothing is the
                  REFUTE. Promotion is USE-WITH-GOOD-OUTCOME, never proximity,
                  never self-declared.

DUTY CYCLE: a closed-loop idle controller -- one dream wave only when the
proxy has been idle `idle` seconds; the sleep stretches when the fresh band
exhausts and shrinks when confirmations flow. Hard caps: MAX_DREAMS_HOT
quarantined branches at once (a backed-up board pauses proposing).

Everything here NEVER runs on the chat path: the thread catches its own
exceptions, and a stop() joins cleanly. --no-dreamer leaves the field
exactly as before.
"""

from __future__ import annotations

import random
import threading
import time
import traceback

import numpy as np

from .generator import FieldNGram
from .intent import STEER_SIM
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
        # THE PAUSE (the stable point): the dreamer sleeps whole -- a
        # dream is the mind ACTING; on resume the idle cadence rules
        # again (a real wave needs the 60s idle anyway).
        from .opencode import nerve_paused
        if nerve_paused(srv):
            return self._idle
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
        src = getattr(srv, "dream_source", None)      # the entity field's corpus
        if src is None:
            return self._idle * 4       # field off: nothing to dream ON
        trusted = src.trusted_texts()
        est0 = getattr(srv, "entity_store", None)
        hot = sum(1 for e in (est0.docs.values() if est0 is not None else [])
                  if e.cls == CLASS_TIMELINE
                  and (e.properties or {}).get("status") == TL_QUARANTINED)
        if len(trusted) < 4:
            return self._idle * 4        # a young field has nothing to dream ON
        if hot >= MAX_DREAMS_HOT:
            return self._idle * 2        # board backed up: let reality vote first
        try:
            from .fieldpulse import of as _pulse_of
            _fp = _pulse_of(srv)
            if _fp is not None:
                _g = open_intents[0]
                _fp.mind("dreamer", goal=str(getattr(_g, "eid", "") or ""))
                _gp = getattr(_g, "properties", None) or {}
                _fp.dreamt(str(getattr(_g, "eid", "") or ""), str(_gp.get("summary") or _gp.get("name") or _gp.get("text") or ""))
        except Exception:
            pass
        # ---- propose ----
        ngram = FieldNGram(trusted, n=3)
        commit_vecs: list[tuple[str, np.ndarray]] = []
        attempts = 0
        while attempts < MAX_PROPOSE and len(commit_vecs) < 3:
            attempts += 1
            anchor = trusted[self._rng.randrange(len(trusted))]
            text = ngram.generate(anchor.split()[:2], self._rng,
                                  max_tokens=12).strip()
            if not text or len(text.split()) < 3 or text in trusted:
                continue
            vec = src.embed(text)
            verdict = self._vet_on(src, vec, ib)
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
                    nv = np.asarray(np_, dtype=float)
                    nn = float(np.linalg.norm(nv))
                    if nn > 1e-12:
                        vv = np.asarray(vec, dtype=float)
                        branch_sim = round(
                            float(vv @ (nv / nn)) / max(float(np.linalg.norm(vv)), 1e-12),
                            4)
            except Exception:
                pass
            ent = est.make_timeline(
                terminal=text, vec=list(vec),
                seed_intent=(need.text if need is not None else None),
                branch_sim=branch_sim,
            )
            if ent is not None:
                self.stats["committed"] += 1
                from .opencode import _write_audit
                _write_audit(mem, {"event": "dream", "op": "commit-tl",
                                   "text": text[:200], "eid": ent.eid})
                # THE DREAMED TRIGGER (2026-10-11: settable since 10-02, never
                # fired): the entities the branch names run their dreamed_script
                try:
                    from .night import entities_named, fire_trigger
                    for _e in entities_named(srv, text):
                        fire_trigger(srv, _e, "dreamed",
                                     {"dream": text[:300], "outcome": "dreamed", "branch": ent.eid})
                except Exception:
                    pass
        self.stats["waves"] += 1
        # sleep until likely idle again; a busy session wakes us anyway
        # (the elect refutes; the shared docs ride the ephemeral TTL)
        return self._idle

    def _gc(self, mem) -> None:
        """Horizon, timeline-shaped: quarantined branches older than the
        horizon are removed from the entity store (the confirmed ones stay
        -- they are the record of what reality voted for). The shared docs
        ride the ephemeral TTL too (server-side); this in-process sweep
        keeps the RAM layer honest.
        THE CURIOSITY GROWTH: an unconfirmed branch past 2/3 of the
        horizon gets HUNGRIER (+0.2, cap 1.0) -- an unanswered question
        that is about to expire gets the mind's last attention before
        the GC lands."""
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
            if not born:
                continue
            age = now - born
            if age > GC_HORIZON_S:
                doomed.append(ent.eid)
                continue
            # THE HUNGER GROWTH (2/3 horizon, unconfirmed): +0.2, cap 1.0
            if age > GC_HORIZON_S * (2.0 / 3.0):
                try:
                    props["curiosity"] = round(
                        min(1.0, float(props.get("curiosity", 0.7)) + 0.2),
                        3)
                except (TypeError, ValueError):
                    pass
        for eid in doomed:
            est.remove(eid)
        if doomed:
            self.stats["gc"] += len(doomed)
            from .opencode import _write_audit
            _write_audit(mem, {"event": "dream", "op": "gc-tl",
                               "removed": len(doomed)})

    def _vet_on(self, src, vec: np.ndarray, ib) -> str | None:
        """The critic on the entity field: STEER + PHYSICS, then the
        source's topic / fresh / surprise-band check."""
        steer = ib.nearest(vec)[1] if ib is not None else -1.0
        if steer < STEER_SIM_LOCAL:
            return None
        if self._near_failure(vec):
            return "impossible"
        return "quarantined" if src.vet(vec) else None

    def _near_failure(self, vec) -> bool:
        est = getattr(self.srv, "entity_store", None)
        if est is None:
            return False
        fv = np.asarray(vec, dtype=float)
        fn = float(np.linalg.norm(fv))
        if fn <= 1e-12:
            return False
        fv = fv / fn
        for ent in list(est.docs.values()):
            if ent.cls != CLASS_FAILURE:
                continue
            ev = getattr(ent, "position", None) or getattr(ent, "embedding", None)
            if not ev:
                continue
            eav = np.asarray(ev, dtype=float)
            en = float(np.linalg.norm(eav))
            if en > 1e-12 and float(fv @ (eav / en)) >= 0.85:
                return True
        return False


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
        # the order-shaped ballot: the real turn's token state rides the
        # vote (the elect computes the branch side lazily + cached); a
        # lane-less seat passes None -- the vec-only ballot runs.
        turn_state = None
        lane = getattr(mem, "vsa_lane", None)
        if lane is not None:
            try:
                turn_state = lane.encode(ex.situation or "")
            except Exception:
                turn_state = None
        res = est.elect_timeline(vec, intent_sims,
                                 turn_state=turn_state, lane=lane)
        if res.get("confirmed") or res.get("refuted"):
            from .opencode import _write_audit
            _write_audit(mem, {"event": "dream", "op": "elect",
                               **res,
                               "by_turn": (ex.situation or "")[:160]})
        if getattr(srv, "dreamer", None) is not None:
            srv.dreamer.stats["promoted"] += res.get("confirmed", 0)
            srv.dreamer.stats["refuted"] += res.get("refuted", 0)
        # the thinker's digest: a confirmed branch is worth one sentence
        # (a prediction came true); the thinker decides (planner-gated).
        th = getattr(srv, "thinker", None)
        if th is not None:
            for eid in res.get("confirmed_eids", []):
                th.enqueue("dream-outcome", {"op": "elect", "confirmed": 1,
                                             "eid": eid,
                                             "text": (ex.situation or "")[:160]})
    except Exception:
        traceback.print_exc()
