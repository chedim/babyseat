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
            ok = self._vet(mem, vec, ib)
            if ok:
                commit_vecs.append((text, vec))
        if not commit_vecs:
            return self._idle * 2        # the fresh band is exhausted for now
        # ---- commit (quarantined) ----
        for text, vec in commit_vecs:
            idx = mem.absorb_exemplar(
                situation=f"[dream] {text}", action=text, outcome="dream",
                vec=list(vec), safe=True, tags=[], self_authored=True)
            if idx is not None:
                self.stats["committed"] += 1
                from .opencode import _write_audit
                _write_audit(mem, {"event": "dream", "op": "commit",
                                   "text": text[:200], "ex_idx": idx})
        self.stats["waves"] += 1
        # ---- GC horizon ----
        self._gc(mem)
        # sleep until likely idle again; a busy session wakes us anyway
        return self._idle

    def _vet(self, mem, vec: np.ndarray, ib) -> bool:
        """STEER + TOPIC + FRESH + VALUE -- the lab critic, on the live field."""
        steer = ib.nearest(vec)[1] if ib is not None else -1.0
        if steer < STEER_SIM_LOCAL:
            return False
        nov = novelty(vec, mem.store, keep_fraction=NOV_KEEP_FRACTION)
        _, top_sim = mem.store.top_k(vec, k=1)[0]
        topic_ok = (0.0 <= nov) and (top_sim >= TOPIC_FLOOR)
        fresh_ok = top_sim < mem.dedup_sim
        grows = effective_rank_with(mem.store, vec) > \
            thermo_state(mem.store).get("eff_rank", 0.0) + 1e-6
        return bool(topic_ok and fresh_ok and grows)

    def _gc(self, mem) -> None:
        """Horizon: quarantine only. Dreams a real turn confirmed already had
        their flag flipped in place; refuted ones are removed by the vote."""
        now = time.time()
        doomed = []
        for i, e in enumerate(mem.store.exemplars):
            if not (e.self_authored and e.outcome == "dream"):
                continue
            born = float(getattr(e, "extra", {}).get("dream_ts", 0.0) or now)
            if now - born > GC_HORIZON_S:
                doomed.append(i)
        if doomed:
            mem.store.forget(doomed)
            self.stats["gc"] += len(doomed)
            from .opencode import _write_audit
            _write_audit(mem, {"event": "dream", "op": "gc",
                               "removed": len(doomed)})


def vote_on_turn(srv, ex) -> None:
    """REALITY'S ELECTION, called from the per-learn hook for every REAL
    (non-dream) exemplar: each near quarantined dream gets the turn's signed
    vote. GOOD (the dream's own direction matches the turn's) -> promote to
    canon; BAD (a near dream that cannot serve the turn) -> the re-ask ->
    remove. One embed of the new exemplar's vec only (it is already a vec) --
    no model call, bounded by MAX_DREAMS_HOT comparisons."""
    mem = srv.membrane
    ib = getattr(srv, "_intent_board", None)
    try:
        vec = np.asarray(ex.vec, dtype=float)
        n = float(np.linalg.norm(vec))
        if n <= 1e-12:
            return
        vec = vec / n
        # candidates: quarantined dreams near this REAL turn
        dreams = [(i, e) for i, e in enumerate(mem.store.exemplars)
                  if e.self_authored and e.outcome == "dream" and e.vec]
        if not dreams:
            return
        sims = [(i, e, float(vec @ np.asarray(e.vec, dtype=float)
                             / max(float(np.linalg.norm(e.vec)), 1e-12)))
                for i, e in dreams]
        near = [(i, e, s) for i, e, s in sims if s >= 0.72]
        if not near:
            return
        from .opencode import _write_audit
        # the turn's OWN geometric identity, against the open intents (the
        # need: which commitment -- if any -- this turn serves)
        promoted = 0
        if ib is not None:
            need, nsim = ib.nearest(vec)
        else:
            need, nsim = None, 0.0
        for i, e, s in near:
            # GOOD: the dream sits near an open intention AND the turn
            # matches that direction (use-with-outcome; v2 D5b)
            if need is not None and nsim >= STEER_SIM_LOCAL:
                e.self_authored = False
                e.outcome = "confirmed"
                promoted += 1
                _write_audit(mem, {"event": "dream", "op": "promote",
                                   "ex_idx": i, "sim": round(s, 3),
                                   "by_turn": (ex.situation or "")[:160]})
            else:
                # BAD: near the turn but serves nothing open -> refuted
                mem.store.forget([i])
                _write_audit(mem, {"event": "dream", "op": "refute",
                                   "ex_idx": i, "sim": round(s, 3),
                                   "by_turn": (ex.situation or "")[:160]})
        if getattr(srv, "dreamer", None) is not None:
            srv.dreamer.stats["promoted"] += promoted
    except Exception:
        traceback.print_exc()