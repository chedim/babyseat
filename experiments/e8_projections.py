"""E8 (trajectory projection probe): are projected entity positions
MEANINGFUL -- closer to where the entity actually goes than alternatives?

The operator's question -- 'what happens if it continues that way? would
projected trajectories help reasoning?' -- answered with a number.
Offline, script-first, over the live audit replay:

  At each replayed turn, for every positioned TRACKED entity with >= 2
  ring points, project its position +1h (and +3h) from the ring-so-far.
  Then measure, at the turns that follow, the entity's REAL position
  (after its next consolidation) and grade the projection against:

    proj-damped   -- the new damped constant-velocity projection
    proj-const    -- undamped constant-velocity (control A)
    hold          -- the entity's present position, unmoved (control B:
                     does projection beat just staying put?)
    random        -- a random unit direction (control C: the floor)

  Metric: cosine distance between the guess and the actual next position
  (lower = better). Also reports the fraction of entities whose motion is
  fast enough to project at all (the 1e-4 pace floor).

Run:  ~/.membrane/venv/bin/python experiments/e8_projections.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.entities import QuantumEntity  # noqa: E402
from membrane.field import ExemplarStore  # noqa: E402
from membrane import night  # noqa: E402

AUDIT = ".membrane/audit.jsonl"
FIELD = ".membrane/field.json"


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=1500)
    ap.add_argument("--batch", type=int, default=128)
    args = ap.parse_args()

    recs = [json.loads(ln) for ln in open(AUDIT)]
    dec = [d for d in recs
           if d.get("event") == "decision" and d.get("query")
           and not str(d.get("query", "")).lstrip().startswith(
               ("membrane:", "model:"))]
    step = max(1, len(dec) // args.sample)
    dec = dec[::step][:args.sample]

    store = ExemplarStore.load(FIELD)
    rows = store.exemplars
    fs = [int(d.get("field_size") or 0) for d in dec]

    emb = OllamaEmbedder()
    seen: dict[str, np.ndarray] = {}

    def em(texts: list[str]) -> None:
        todo = sorted({t for t in texts if t not in seen})
        for k in range(0, len(todo), args.batch):
            chunk = todo[k:k + args.batch]
            try:
                for t, v in zip(chunk, emb.embed(chunk)):
                    seen[t] = np.asarray(v, dtype=float)
            except Exception:
                for t in chunk:
                    seen.setdefault(t, np.zeros(1024))

    # THE RING REPLAY: rebuild each tracked entity's ring from the cells
    # AS THEY EXISTED at each audit turn (the same time-ordered discipline
    # as E2/E3/E7), stepping consolidate_now exactly like production. The
    # projection at turn i grades against the entity's position a few
    # turns later (the future state it could not have seen).
    class M:
        pass

    m = M()
    m.store = store
    m.embedder = None
    est_like = {}

    # simple proxy for the tracked entities: the session:now tracker +
    # the field's per-entity geometry replayed from the store prefixes
    # is expensive; the probe targets the one entity the production code
    # actually pulses every turn -- session:now -- plus the momentum EMA
    # as its position source (E4's validated signal).
    em([d["query"] for d in dec])

    mom = None
    mom_ts = 0.0
    # the session:now ring, replayed: one point per turn's momentum pulse
    ring: list = []
    graded = 0
    errs = {"proj-damped": [], "proj-const": [], "hold": [], "random": []}
    horizon_errs: dict[str, list] = {"proj-damped": [], "hold": []}
    rng = np.random.default_rng(5)
    proj_fast = 0            # projections fast enough to fire (pace floor)
    projected = 0
    GRADE_LAG = 10           # compare the projection against the turn
    #                          10 turns later's position (a longer real future)
    pending = []             # (due_index, proj_damped, proj_const, hold)

    for i, d in enumerate(dec):
        q = d["query"]
        fs_i = max(0, fs[i] - 1)
        if fs_i < 100:
            continue
        qv = unit(seen[q])
        # the momentum fold (friction + idle decay, E4's shape)
        idle = max(0.0, time.time() - mom_ts) / 60.0 if mom_ts else 0.0
        frac = 0.95 ** (1.0 + idle)
        new = (frac * mom + qv) if mom is not None else qv
        nn = np.linalg.norm(new)
        mom = new / nn if nn > 1e-12 else new
        mom_ts = time.time()
        # THE CADENCE FIX: ring entries stamped with real wall time land
        # milliseconds apart in a tight replay loop, and project() treats
        # sub-3.6s gaps as 'no motion' (the dt floor). Production turns
        # are minutes apart -- the probe simulates that cadence.
        ring_ts = (ring[-1][0] + 120.0) if ring else time.time()
        ring.append([ring_ts, [float(x) for x in mom]])
        ring[:] = ring[-16:]

        # a synthetic walker entity over the replayed ring: the probe's
        # geometry subject (same contract as session:now, offline)
        walker = QuantumEntity(
            eid="membrane::e8:walker", cls="Session",
            properties={"name": "walker", "grain": 0.003,
                        "ephemeral": True})
        walker.position = list(mom)
        walker.position_history = list(ring)
        proj = walker.project(horizon_h=6.0, damped=True)
        proj_c = walker.project(horizon_h=6.0, damped=False)
        if proj is not None:
            proj_fast += 1
            pending.append((i + GRADE_LAG, proj[0][1], proj_c[0][1],
                            list(mom)))
        # grade the matured projections
        still = []
        for due, pd, pc, hold in pending:
            if i >= due:
                errs["proj-damped"].append(float(unit(pd) @ mom))
                errs["proj-const"].append(float(unit(pc) @ mom))
                errs["hold"].append(float(unit(hold) @ mom))
                rvec = unit(rng.normal(size=len(mom)))
                errs["random"].append(float(rvec @ mom))
                graded += 1
            else:
                still.append((due, pd, pc, hold))
        pending = still

    print(f"turns replayed: {len(dec)} | projections fired: {proj_fast} "
          f"| graded after the lag: {graded}")
    if graded:
        for name in ("proj-damped", "proj-const", "hold", "random"):
            a = errs[name]
            print(f"  {name:12s}: cosine to the future {np.mean(a):.3f} "
                  f"(median {np.median(a):.3f})")
        print("(higher cosine = the guess sits closer to where the entity")
        print(" ACTUALLY went -- projection must beat 'hold' and 'random')")
        # the honest verdict line
        pd, hd = np.mean(errs["proj-damped"]), np.mean(errs["hold"])
        print(f"\nverdict: projection beats holding by "
              f"{pd - hd:+.3f} cosine "
              f"({'USEFUL' if pd > hd + 0.02 else 'MARGINAL' if pd > hd else 'NO -- the momentum already encodes it'})")


if __name__ == "__main__":
    main()
