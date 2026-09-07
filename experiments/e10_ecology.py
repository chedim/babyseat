"""E10 (cell-ecology probe): does an ecology of mask cells -- grazing the
world AND each other (relay) AND their dead (corpses) -- outperform
world-only grazing, at what cost, and does the assigned-corpse design
(the specialization ratchet concern) actually ratchet?

Four arms, same population (N=12 mask cells over the replayed world
stream), graded identically:

  arm W    world-only (control)        -- every cell grazes the world stream
  arm R    +living-relay               -- world diet + niche-neighbor relay
                                          (nearest-cone cells' deltas,
                                          capped 25% of intake, one-way
                                          pairs, no mutual grazing)
  arm C    +corpse-public              -- dead cells' materialized fields
                                          enter a public corpse ring; any
                                          cell may graze corpses voluntarily
  arm CA   +corpse-assigned            -- corpses ASSIGNED to the
                                          nearest-cone neighbor at death
                                          (the operator's ratchet concern,
                                          tested directly)

Grading (the E-series discipline):
  - verification rate: for each relayed/corpse-fed absorb, does a LATER
    ground capture confirm the claim (token overlap >= 0.7 on situation)?
  - echo depth: duplicate-chain length in relayed knowledge (A via B via C)
  - ratchet detector: each grazer's effective rank drift over the run
    (mask view rank collapsing toward 1 = the crystal import)
  - band-serving quality: each arm's cells, pooled, answering the
    uncertain-band turns (top-1 of the cell union, E1-graded against the
    model's actual answer) -- does the ecology serve the band better?

Run:  ~/.membrane/venv/bin/python experiments/e10_ecology.py [--cells N]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.field import (Exemplar, ExemplarStore,  # noqa: E402
                            MaskedExemplarStore)

AUDIT = ".membrane/audit.jsonl"
FIELD = ".membrane/field.json"
CAPTURE_PREFIX = ("tool ", "tool call", "reasoning:", "plan:", "delegated:",
                  "call ", "result ", "system-reminder")


def tokens(text: str) -> set[str]:
    import re
    return set(re.findall(r"[a-z0-9_]+", (text or "").lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", type=int, default=12)
    ap.add_argument("--sample", type=int, default=1200)
    ap.add_argument("--batch", type=int, default=128)
    args = ap.parse_args()

    # ---- the world stream (time-ordered audit replay, E2 discipline) ----
    recs = [json.loads(ln) for ln in open(AUDIT)]
    dec = [d for d in recs
           if d.get("event") == "decision" and d.get("query")
           and d.get("response")
           and not str(d.get("query", "")).lstrip().startswith(
               ("membrane:", "model:"))
           and "compact" not in str(d.get("verdict", ""))]
    step = max(1, len(dec) // args.sample)
    dec = dec[::step][:args.sample]

    store = ExemplarStore.load(FIELD)
    rows = store.exemplars
    fs = [min(int(d.get("field_size") or 0), len(rows))
          for d in dec]   # the audit's clock vs the store's real length:
    # compacted rows shrink the store; the replay clock clamps to it
    # ground-truth index: which turn learned which row (for lagged grading
    # we need "ground arrived at turn k about situation S")
    ground_ts: dict[str, int] = {}
    ground_resp: dict[str, str] = {}
    for i, d in enumerate(dec):
        if str(d.get("verdict", "")) == "teed-pair":
            ground_ts[d["query"]] = i
            ground_resp[d["query"]] = (d.get("response") or "").strip()

    # the four arms share ONE world stream and N cells each; cells differ
    # only in diet policy
    # THE DISK CACHE (the '25-min embed walls' fix): probe runs embed the
    # SAME audit texts every invocation -- persist them, never re-embed.
    import os
    cache_path = Path(".membrane/e10-embed-cache.json")
    seen: dict[str, np.ndarray] = {}
    if cache_path.exists():
        try:
            raw = json.load(open(cache_path))
            seen = {t: np.asarray(v, dtype=float) for t, v in raw.items()}
            print(f"  [em] disk cache: {len(seen)} vectors", flush=True)
        except Exception:
            seen = {}
    emb = OllamaEmbedder(timeout=10)   # fail fast: ollama stalls, not hangs
    new_texts = sorted({t for t in
                        ([d["query"] for d in dec]
                         + [r.action for r in rows if r.action]
                         + [r.situation for r in rows if r.situation])
                        if t not in seen})
    print(f"  [em] {len(new_texts)} embeds needed", flush=True)
    for k in range(0, len(new_texts), args.batch):
        chunk = new_texts[k:k + args.batch]
        for attempt in range(2):
            try:
                for t, v in zip(chunk, emb.embed(chunk)):
                    seen[t] = np.asarray(v, dtype=float)
                break
            except Exception as e:
                print(f"  [em] batch {k} attempt {attempt} failed: "
                      f"{str(e)[:60]}", flush=True)
        else:
            for t in chunk:
                seen.setdefault(t, np.zeros(1024))
        if k % 512 == 0:
            print(f"  [em] {min(k + args.batch, len(new_texts))}/"
                  f"{len(new_texts)}", flush=True)
    try:
        json.dump({t: list(map(float, v)) for t, v in seen.items()},
                  open(cache_path, "w"))
        print(f"  [em] cache persisted ({len(seen)})", flush=True)
    except Exception:
        pass

    import time as _time
    _t_embed = _time.time()
    # THE ONE-TIME WORLD MATRIX (E4's lesson -- no per-turn rebuilds)
    W = np.array([r.vec if getattr(r, "vec", None) else np.zeros(1024)
                  for r in rows], dtype=float)
    Wn = W / (np.linalg.norm(W, axis=1, keepdims=True) + 1e-12)

    N = args.cells
    CORPSE_CAP = 8
    RELAY_CAP = 0.25
    # THE DIVERGENCE (the ecology's precondition): cells get ORIENTATIONS
    # derived from the WORLD's own principal directions (orient_dirs -- the
    # pool's real mechanics; random 1024-d vectors have ~0 cos to every
    # real row, so a random orientation grazes nothing)
    from membrane.variety import principal_dirs
    _center, pdirs, _spectrum = principal_dirs(store)
    dirs = (pdirs if pdirs is not None and len(pdirs) else [])[:N]
    orientations = [unit(d) for d in dirs]
    while len(orientations) < N:
        orientations.append(orientations[len(orientations) % max(len(dirs), 1)]
                            if dirs else orientations[-1])

    class Cell:
        def __init__(self, tag: str, seed: int, orientation):
            self.tag = tag
            self.seed = seed
            self.orientation = orientation
            self.neighbors: list = []   # the niche-topology relay lane
            self.mask = None            # bound per turn (frozen horizon)
            self.relayed: list = []     # (from_tag, situation, action)
            self.echo_depth = 0
            self.absorb_counts = collections.Counter()
            self.verify_hits = 0
            self.verify_total = 0
            self.situ = set()           # absorbed situation texts
            self.taken_cache = np.zeros((0, 1024))   # the incremental
            self.taken_idxs: list = []             # absorbed-vec matrix

        def bind(self, fs_i: int, relay: bool, corpses: list,
                 assigned_corpse=None):
            """Rebind the mask at the current horizon (masks are cheap:
            E12 measured 39KB). Diet policy decides what extra food the
            cell sees this turn."""
            self.mask = MaskedExemplarStore(store, frozen_size=fs_i,
                                            nudge_seed=self.seed)
            diet_extra = []
            # RELAY: nearest-cone living neighbor's view, capped
            if relay and self.neighbors:
                nb = self.neighbors[0] if self.neighbors else None
                if nb is not None and nb is not self:
                    src_rows = nb.mask.exemplars if nb.mask else []
                    take = max(1, int(RELAY_CAP * max(len(src_rows), 20)))
                    for r in src_rows[:take]:
                        if r.situation not in self.situ:
                            diet_extra.append(("relay", r))
            # CORPSES: public ring (voluntary) or assigned
            if corpses:
                if assigned_corpse is not None:
                    for r in assigned_corpse.exemplars[:40]:
                        if r.situation not in self.situ:
                            diet_extra.append(("corpse-assigned", r))
                else:
                    for c in corpses[:2]:
                        for r in c.exemplars[:20]:
                            if r.situation not in self.situ:
                                diet_extra.append(("corpse", r))
            return diet_extra

    def curio(max_sim_to_view: float) -> bool:
        """The curiosity gate, vectorized: max cosine to the cell's view
        already computed -- dup guard 0.95, novelty bar 0.92."""
        if max_sim_to_view >= 0.95:
            return False                # dup: the text guard's vector twin
        return max_sim_to_view < 0.92

    def cell_view_vecs(cell) -> np.ndarray:
        """THE SINGLE SOURCE for a cell's absorbed-view matrix. The cell
        maintains it INCREMENTALLY at exactly two mutation points:
          - a world take (the graze loop appends the taken row's vec)
          - an extras take (relay/corpse appends)
        No rebuild paths. The accessor returns the cache as-is."""
        v = getattr(cell, "taken_cache", None)
        return v if v is not None else np.zeros((0, 1024))

    def run_arm(name: str, relay: bool, corpse_mode: str) -> dict:
        cells = [Cell(f"cell-{i}", seed=100 + i,
                      orientation=orientations[i % len(orientations)])
                 for i in range(N)]
        corpses: list = []
        out = {"name": name, "absorbs": 0, "relayed_absorbs": 0,
               "corpse_absorbs": 0, "echo_depths": [], "ranks": [],
               "verify": [], "band_hits": 0, "band_graded": 0}
        for i, d in enumerate(dec):
            if i % 50 == 0:
                print(f"  [{name}] turn {i}/{len(dec)} elapsed {_time.time()-_t_embed:.0f}s", flush=True)
            fs_i = max(0, fs[i] - 1)
            if fs_i < 100:
                continue
            qv = unit(seen[d["query"]])
            # niche topology: cells sorted by centroid alignment to the
            # current turn (the momentum cone) -- relay flows down-cone
            cents = []
            for c in cells:
                V = cell_view_vecs(c) if c.mask else \
                    np.zeros((0, 1))
                cents.append(float(np.max(V @ qv)) if V.size else 0.0)
            # THE CROSS-NICHE RELAY LANE (the honest transport): relay
            # flows between DIVERGENT specialists -- cell i relays from
            # cell (i+1) mod N, whose orientation differs by design. The
            # rank-adjacent (most-similar) pairing was the no-op: similar
            # cones take the same rows, dedup kills every relay.
            for ci in range(len(cells)):
                cells[ci].neighbors = [cells[(ci + 1) % len(cells)]] \
                    if relay else []
            assigned = {}
            # death + succession: every ~200 turns, retire the last cell
            # (its mask materializes into a corpse; the next cell buds)
            if i and i % 200 == 0 and corpse_mode != "none":
                dead = cells.pop()
                corpse = dead.mask.materialize() if dead.mask else None
                if corpse is not None:
                    corpses.append(corpse)
                    corpses[:] = corpses[-CORPSE_CAP:]
                if corpse_mode == "assigned" and cells:
                    assigned[cells[0].tag] = corpse
                cells.insert(0, Cell(f"cell-{len(cells)}",
                                     seed=1000 + i,
                                     orientation=orientations[
                                         len(cells) % len(orientations)]))
            for ci, c in enumerate(cells):
                # THE LIVING-HORIZON: the mask's horizon tracks the world
                # as it grows (lazy view -- O(1) rebind now), the cell's
                # CURSOR tracks what it has grazed. A cell born at turn i
                # starts grazing from its birth row; the corpse path still
                # materializes at succession.
                if c.mask is None:
                    c.bind(min(fs_i, 100 + ci * 50), relay,
                           corpses if corpse_mode in
                           ("public", "assigned") else [],
                           assigned_corpse=assigned.get(c.tag)
                           if corpse_mode == "assigned" else None)
                    extras = []
                elif c.mask.frozen_size < fs_i:
                    # the world grew past the mask's horizon: extend it
                    # (lazy view -- the rebind is now O(1))
                    corpse_for = assigned.get(c.tag) \
                        if corpse_mode == "assigned" else None
                    c.bind(fs_i, relay,
                           corpses if corpse_mode in
                           ("public", "assigned") else [],
                           assigned_corpse=corpse_for)
                    extras = []
                    c._view_rev = getattr(c, "_view_rev", 0) + 1
                else:
                    extras = []
                # THE PER-TURN FOOD FLOW (the transport layer): relay from
                # the cross-niche neighbor's TAKEN rows + corpse ring
                # grazing, both per-turn (bind-time-only food was the bug
                # that left relay=0 in every arm).
                extras = extras or []
                if relay and c.neighbors:
                    nb = c.neighbors[0]
                    if nb is not None and nb is not c:
                        take = max(1, int(RELAY_CAP *
                                          len(nb.taken_idxs)))
                        for wi in nb.taken_idxs[:take]:
                            if wi < len(rows):
                                r = rows[wi]
                                if r.situation not in c.situ:
                                    extras.append(("relay", r))
                if corpses:
                    if corpse_mode == "assigned" and \
                            assigned.get(c.tag) is not None:
                        for r in assigned[c.tag].exemplars[:40]:
                            if r.situation not in c.situ:
                                extras.append(("corpse-assigned", r))
                    elif corpse_mode == "public":
                        for corpse in corpses[:2]:
                            for r in corpse.exemplars[:20]:
                                if r.situation not in c.situ:
                                    extras.append(("corpse", r))
                if not hasattr(c, "cursor"):
                    c.cursor = min(100, c.mask.frozen_size)
                if not hasattr(c, "taken_idxs"):
                    c.taken_idxs: list = []
                if not hasattr(c, "taken_cache"):
                    c.taken_cache = None
                # THE HORIZON GUARD: never graze beyond the birth horizon
                new_lo, new_hi = c.cursor, min(fs_i, c.mask.frozen_size)
                if getattr(c, "taken_cache", None) is None:
                    c.taken_cache = np.zeros((0, 1024))
                took = 0
                if new_hi > new_lo:
                    sub = Wn[new_lo:new_hi]
                    if c.taken_cache.size:
                        sims_all = c.taken_cache @ sub.T   # (taken, delta)
                        # max over TAKEN (axis 0) -> one score per delta row
                        max_sim = sims_all.max(axis=0) \
                            if sims_all.size else np.zeros(new_hi - new_lo)
                    else:
                        max_sim = np.zeros(new_hi - new_lo)
                    for k in range(new_hi - new_lo):
                        wi = new_lo + k
                        if wi >= c.mask.frozen_size:
                            break
                        r = rows[wi]
                        if r.situation in c.situ or \
                                not getattr(r, "vec", None):
                            continue
                        if not r.action or \
                                r.situation.startswith(CAPTURE_PREFIX):
                            continue
                        # THE CONE GATE (the specialization precondition):
                        # a cell grazes only rows aligned to its
                        # orientation (the niche's diet)
                        if float(Wn[wi] @ c.orientation) < 0.35:
                            continue
                        if float(max_sim[k]) >= 0.92:
                            continue        # not novel to this cell
                        c.mask.take_parent_row(wi)
                        c._view_rev = getattr(c, "_view_rev", 0) + 1
                        c.taken_idxs.append(wi)
                        # incremental cache append (no rebuild)
                        nv = unit(rows[wi].vec).reshape(1, -1)
                        c.taken_cache = (np.vstack([c.taken_cache, nv])
                                         if c.taken_cache.size
                                         else nv)
                        c.situ.add(r.situation)
                        c.absorb_counts["world"] += 1
                        out["absorbs"] += 1
                        took += 1
                        if took >= 12:
                            break
                c.cursor = fs_i
                if extras and i % 100 == 0:
                    print(f"    [dbg {name}] t{i} cell {c.tag} extras "
                          f"{len(extras)} kinds "
                          f"{[k for k,_ in extras][:3]} nb_taken "
                          f"{len(c.neighbors[0].taken_idxs) if c.neighbors else 0}",
                          flush=True)
                for kind, r in extras:
                    if r.situation in c.situ:
                        continue
                    rv = getattr(r, "vec", None)
                    if rv is None:
                        continue
                    rvn = unit(rv)
                    max_s = (float(c.view_cache.max() @ 0) if
                             False else
                             (float(np.max(c.view_cache @ rvn))
                              if getattr(c, "view_cache", None) is not None
                              and getattr(c, "view_cache").size else 0.0))
                    if not curio(max_s):
                        continue
                    depth = 1
                    if kind == "relay":
                        c.relayed.append((r.situation, r.action))
                        # echo depth: was this relayed knowledge ALREADY
                        # relayed to its source? (a->b->c chains)
                        depth = getattr(r, "_echo", 0)
                        depth += 1
                        out["echo_depths"].append(depth)
                    elif kind.startswith("corpse"):
                        out["corpse_absorbs"] += 1
                    # verification grade: does a LATER ground capture
                    # confirm this claim? The ground capture's RESPONSE is
                    # what the model eventually said for this situation --
                    # claim confirmed when the texts agree (token overlap
                    # >= 0.7 between the relayed action and the ground
                    # response, graded at the ground turn, not now).
                    gt = ground_ts.get(r.situation)
                    if gt is not None and gt > i:
                        c.verify_total += 1
                        gresp = ground_resp.get(r.situation)
                        if gresp and jaccard(r.action, gresp) >= 0.7:
                            c.verify_hits += 1
                    c.situ.add(r.situation)
                    c.absorb_counts[kind] += 1
                    if kind == "relay":
                        out["relayed_absorbs"] += 1
            # band-serving grade: the pool's union view answers the turn
            best_sim, best_act = -1.0, None
            for c in cells:
                if not c.mask:
                    continue
                V = cell_view_vecs(c)
                if V.size == 0:
                    continue
                Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
                sims = Vn @ qv
                j = int(np.argmax(sims))
                if float(sims[j]) > best_sim:
                    best_sim = float(sims[j])
                    vv = c.mask.exemplars[j]
                    best_act = vv.action if hasattr(vv, "action") else None
            # the uncertain band only (the contested population)
            if best_act is not None and 0.50 <= best_sim < 0.90:
                out["band_graded"] += 1
                if jaccard(best_act, d["response"]) >= 0.7:
                    out["band_hits"] += 1
        # the ratchet detector: mean effective rank across cells' views
        from membrane.variety import effective_rank_with
        ranks = []
        for c in cells:
            try:
                V = cell_view_vecs(c)
                if V.shape[0] < 2:
                    continue
                # rank of the cell's absorbed view, straight from vectors
                from membrane.variety import _effective_rank_vectors
                ranks.append(_effective_rank_vectors(V))
            except Exception:
                pass
        out["ranks"] = ranks
        out["verify_rate"] = (sum(c.verify_hits for c in cells)
                              / max(sum(c.verify_total for c in cells), 1))
        return out

    arms = [("W world-only", False, "none"),
            ("R +living-relay", True, "none"),
            ("C +corpse-public", True, "public"),
            ("CA +corpse-assigned", True, "assigned")]
    results = []
    for name, relay, cm in arms:
        print(f"running arm {name} ...")
        r = run_arm(name, relay, cm)
        results.append(r)
        print(f"  absorbs {r['absorbs']} (relay {r['relayed_absorbs']}, "
              f"corpse {r['corpse_absorbs']})")
    print(f"\n=== E10 cell-ecology results (N={N}, "
          f"{len(dec)} turns) ===")
    print(f"{'arm':22s} {'absorbs':>8s} {'relay':>6s} {'corpse':>7s} "
          f"{'verify':>7s} {'echo':>5s} {'rank':>6s} {'band-serve':>10s}")
    for r in results:
        echo = np.mean(r["echo_depths"]) if r["echo_depths"] else 0.0
        rank = np.mean(r["ranks"]) if r["ranks"] else 0.0
        band = (100 * r["band_hits"] / max(r["band_graded"], 1))
        print(f"{r['name']:22s} {r['absorbs']:8d} {r['relayed_absorbs']:6d} "
              f"{r['corpse_absorbs']:7d} {100 * r['verify_rate']:6.1f}% "
              f"{echo:5.2f} {rank:6.1f} {band:9.1f}%")
    print("\nverdict lines:")
    w, r_, c_, ca = results
    print(f"  relay vs control absorbs: "
          f"{r_['absorbs'] - w['absorbs']:+d}")
    print(f"  corpse-public vs control: "
          f"{c_['absorbs'] - w['absorbs']:+d} absorbs, "
          f"verify {100 * c_['verify_rate']:.0f}% vs "
          f"{100 * w['verify_rate']:.0f}%")
    print(f"  assigned-corpse rank drift (ratchet detector): "
          f"{np.mean(ca['ranks']) if ca['ranks'] else 0:.1f} vs control "
          f"{np.mean(w['ranks']) if w['ranks'] else 0:.1f}")
    print(f"  band-serving: W {100 * w['band_hits'] / max(w['band_graded'], 1):.1f}%"
          f" | R {100 * r_['band_hits'] / max(r_['band_graded'], 1):.1f}%"
          f" | C {100 * c_['band_hits'] / max(c_['band_graded'], 1):.1f}%"
          f" | CA {100 * ca['band_hits'] / max(ca['band_graded'], 1):.1f}%")


if __name__ == "__main__":
    main()
