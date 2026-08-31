#!/usr/bin/env python3
"""Multi-frontier experiment -- the ORGANISM RESIDUAL / GROWTH-FRONTIER TEST (stage 7).

Phase 1 (experiments/multi_differentiate.py, GREEN) built ``Cell.differentiate`` and
showed the DEATH: a standalone differentiated specialist cuts its field to one
direction (``self.membrane.store.exemplars = kept`` -- the self-cut, the reverse of
budding) and locks the hard alignment gate, so it ACTS on its owned base and
ESCALATES on its orthogonal complement. Phase 2 (experiments/multi_organism.py,
GREEN) showed the RESURRECTION: a POPULATION of differentiated specialists, one
per base, covers what no single one can -- the §4 "alive only composed."

This experiment reads the THIRD movement -- the organism GROWING INTO ITS NULL
SPACE, the "edges of null space" in the self-building self-cutting crystal image:

  ∩N_i  =  the intersection of the per-cell nulls
         =  the directions EVERY specialist refuses
         =  the directions NO specialist covers
         =  the organism's RESIDUAL / growth frontier (the edge of its null space).

It is the complement of the organism's coverage. When the organism covers the
whole known variety it is EMPTY (the GREEN completeness signal); when a
direction exists in the gas that no specialist has crystallized it is NON-EMPTY -- that is
the residual, the learning signal (``core principle.md`` §"The Residual as the
Learning Signal": the residual is the source of learning -- the model must grow
where it is insufficient). The organism then SELF-BUILDS (``Cell.pull`` --
crystallize the gas) and SELF-CUTS (``Cell.differentiate`` -- pave + lock) a NEW
specialist into the residual, and ∩N_i collapses back to empty. The organism
grows by its cells dying into the frontier -- the same §4 "alive only composed,"
now read as GROWTH, not just coverage.

THE CONSTRUCTION (no library change -- reuses ``bud`` + ``pull`` +
``differentiate`` exactly as Phase 1/2 built them):

  1. The 6 KNOWN specialists are built by the EXACT multi_organism construction
     (proven GREEN): grow a generalist parent over the 6-base workload, bud 6
     children (``direction_index=0..5``), each pulls a FRESH 6-base buffer so it
     grows ONLY its cone, then ``differentiate``. The organism covers the 6 known
     bases (6/6, dense cones). This is bit-identical to Phase 2 -- no new risk.
  2. The held-out base "secure" is the NEW GAS -- it is NOT in the 6-base
     environment the organism has served. ∩N_i over the 7 bases (6 known +
     secure) = {secure}: every specialist ACTS on its owned known base and has NO
     secure exemplar, so it ESCALATES on secure. secure is in EVERY specialist's
     null -> secure ∈ ∩N_i. That intersection is the organism's residual / growth
     frontier -- the edge of its null space, the new gas it has not grown into.
  3. GROW INTO THE RESIDUAL: a fresh, DE-NOVO cell ``pull``s the new secure gas
     (crystallizes it -- the self-build) and then ``differentiate``s toward its
     one grown direction (the self-cut: pave to the secure cone + lock). The
     organism self-built + self-cut a specialist into its residual.
  4. ∩N_i over the 7 bases collapses to {} -- the organism grew one step along
     the edge of its null space.

The 7th specialist is DE NOVO (grown from the new gas), not budded from the
6-base parent (which has not seen secure). HONEST NOTE: in the live system the
new specialist would BUD from the cell nearest the frontier (inheriting some
competence); here it is de novo to test the growth-into-residual cleanly,
isolating the self-build + self-cut from the inheritance. The growth and the
∩N_i collapse are the same either way.

THE ORGANISM IS NOT A ROUTER (held from Phase 2). Each specialist self-routes via
its own curiosity gate (off-cone -> not curious -> not pulled); coverage is the
union of the gate ``decide`` act-verdicts; ∩N_i is its complement. No
``RoutingView``, no ``CellPool`` change, no router object.

HONEST CAVEAT (held): this is a test of the §4 / VT READING, not a proof. ∩N_i is
computed over the toy's DISCRETE base set (7 axes); in a continuous field it is
the cone-complement (a region, not a list) -- the discrete proxy is honest for
the toy. The "edges of null space = ∩N_i = the growth frontier" framing is the
VT lens (``core principle.md``: the residual is the learning signal), an
imposed interpretation, not a result the literature asserts. The self-cut is
SELECTION (``aligned_safe_slice``); the §4.7 SVD-projection coarse-graining is a
deferred ``mode=`` variant and stays unvetted regardless. The calibrations
(``sim_min=0.70``, ``gate_strength=1.0``) are the toy's D=8 / 0.25-veil regime;
the real 1024-d mxbai regime keeps 0.85/4.0.

Runs with no Ollama / no Anthropic / no real key (fake embedder + base-keyed
responder, reused from multi_cell.py with one held-out base added locally). All
field/audit under a temp dir; nothing leaks to the repo.
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.buffer import BufferView, SituationBuffer
from membrane.cell import Cell
from membrane.variety import orient_dirs

# Reuse the layer-1/2 harness: the fake geometry, the workload, the cell builder,
# the per-cell gate verdict, and the formatter. The frontier adds only the held-out
# 7th base + the ∩N_i computation + the growth step on top of the same machinery.
from experiments.multi_cell import (  # noqa: E402
    BASES, BASE_KEYS, WORKLOAD, D, _f,
)
from experiments.multi_differentiate import _gate_verdict, _aligned_verdict  # noqa: E402

# The held-out 7th base -- the NEW GAS the organism has not grown into. Dims 0-5
# are the 6 known bases (multi_cell.BASES); dim 6 = secure; dim 7 stays free as the
# irreducible null the organism NEVER covers (the hμ beyond the known variety).
SECURE_KEY = "secure"
EXT_BASES = dict(BASES)
EXT_BASES[SECURE_KEY] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
EXT_BASE_KEYS = list(EXT_BASES)

# Stems that map to the held-out base (so the fake embedder lands secure gas on
# dim 6, not on the hash fallback).
SECURE_STEMS = {"secur", "auth", "vuln", "hard", "permiss"}

# 5 paraphrases of the held-out base -- the NEW GAS the organism grows into.
SECURE_WORKLOAD = [
    "secure the api endpoint",
    "authenticate the user login",
    "fix the vulnerability now",
    "harden the access control",
    "add permission checks",
]

# One representative probe per base -- a workload text the specialist will have
# PULLED (so its exemplar is in the field). The gate's nearest-neighbour verdict
# tests recall, not novelty. The 6 known-base probes are multi_differentiate's;
# the secure probe is new.
PROBES = {
    "parse":    "how do I parse the json file",
    "deploy":   "deploy the service to prod",
    "test":     "run the test suite",
    "debug":    "debug the crash on startup",
    "refactor": "refactor the handler module",
    "docs":     "write the readme docs",
    "secure":   "secure the api endpoint",
}


def _base_of7(text: str) -> str | None:
    """Which base intent a query belongs to: the 6 known stems (multi_cell) OR
    a secure stem. Mirrors ``multi_cell.base_of`` over the extended set."""
    from experiments.multi_cell import STEMS
    t = text.lower()
    for stem, key in STEMS.items():
        if stem in t:
            return key
    for stem in SECURE_STEMS:
        if stem in t:
            return SECURE_KEY
    return None


class _FakeEmbedder7:
    """The fake embedder over the 7-base set: paraphrases of a base map to that
    base's axis (dim 0-5 known, dim 6 secure); unknown text hashes as before.
    Bit-identical to ``multi_cell.FakeEmbedder`` on the 6 known bases."""
    def embed(self, texts):
        import hashlib
        out = []
        for t in texts:
            key = _base_of7(t)
            if key is not None:
                out.append(np.array(EXT_BASES[key], dtype=float))
            else:
                h = hashlib.sha1(t.encode()).digest()
                out.append(np.array(
                    [(h[i % len(h)] / 255.0) - 0.5 for i in range(D)], dtype=float))
        return out


class _Responder7:
    """A base-keyed responder over the 7-base set (``do-<base>``). The cell
    (observer mode) forwards to this and learns -- it never authors the
    response. Bit-identical to ``BaseKeyedResponder`` on the 6 known bases."""
    def respond(self, request_body, hint=None, auth=None):
        from membrane.runtime import _chat_response, _last_user
        q = _last_user(request_body) or ""
        key = _base_of7(q) or "generic"
        return _chat_response(request_body.get("model", "cell"), f"do-{key}")


def _build_cell7(tag, cfg, tmp):
    """One autonomous cell over the 7-base geometry -- mirrors
    ``multi_cell._build_cell`` with the extended embedder/responder. Bit-identical
    to ``_build_cell`` on the 6 known bases (the 7-base embedder maps the 6 known
    stems to the same BASES axes)."""
    from membrane.runtime import StubJudge
    return Cell(
        tag=tag,
        responder=_Responder7(),
        field_path=os.path.join(tmp, f"field-{tag}.json"),
        audit_path=os.path.join(tmp, f"audit-{tag}.jsonl"),
        embedder=_FakeEmbedder7(), judge=StubJudge(),
        sim_min=0.85, consensus_min=0.60, dedup_sim=0.95,
        observer=True, vague_filter=False, variety_gate=False,
        nudge_sigma=cfg["nudge_sigma"], nudge_seed=cfg["nudge_seed"],
        curiosity_on=cfg["curiosity_on"],
        curiosity_min=cfg.get("curiosity_min", 2.0),
    )


def _owned_base7(cell: Cell) -> str:
    """Which base a differentiated specialist OWNS -- argmax alignment of its
    committed orientation to the 7 BASES axes (multi_organism._owned_base over
    the extended set). For an un-oriented generalist this is still the closest
    base to its field mean direction."""
    if cell.orientation is None:
        from membrane.variety import principal_dirs
        mu, _Vt, _var = principal_dirs(cell.membrane.store)
        v = mu if mu is not None else np.zeros(D)
    else:
        v = np.asarray(cell.orientation, dtype=float)
    v = v / (np.linalg.norm(v) + 1e-12)
    al = {k: float(np.asarray(EXT_BASES[k]) @ v) for k in EXT_BASE_KEYS}
    return max(al, key=al.get)


N_DRAWS = 24


def _mean_sim(cell: Cell, text: str, n: int = N_DRAWS) -> float:
    """Mean nearest-neighbour sim over ``n`` veil draws -- the DENOISED coverage
    signal. ``_gate_verdict`` re-veils the probe fresh every call
    (``_situation_vec`` is stochastic per call -- the documented toy behavior),
    so a SINGLE-SHOT verdict on a marginal owned sim flips on re-roll. Averaging
    over draws recovers the cone's stable signal -- the same point Phase 2 made
    for dispatch ("it self-routes over the workload, not on a single shot"),
    applied here to the coverage measurement itself."""
    return float(np.mean([_gate_verdict(cell, text)[1] for _ in range(n)]))


def _covered_by(cell: Cell, base: str, n: int = N_DRAWS) -> tuple[bool, float]:
    """(covered, mean_sim) -- covered if the mean nearest-neighbour sim over ``n``
    veil draws clears the cell's ``sim_min`` (0.70 in the toy)."""
    s = _mean_sim(cell, base, n)
    return s >= cell.membrane.sim_min, s


def _residual(specs: list[Cell], bases: list[str]) -> list[str]:
    """∩N_i over ``bases`` -- the bases NO specialist covers (the organism
    residual / growth frontier). A base is covered if some specialist's MEAN
    field-sim clears ``sim_min`` (the robust, denoised signal -- NOT a single-shot
    verdict, which the stochastic veil makes noisy; NOT orientation-alignment,
    the stochastic pull bias)."""
    return [b for b in bases
            if all(not _covered_by(c, b)[0] for c in specs)]


def _refused_by_all(specs: list[Cell], base_key: str) -> bool:
    """The alignment gate (``_aligned``) refuses this base on EVERY specialist --
    the structural confirmation the base is in every specialist's null (∈ ∩N_i),
    distinct from the coverage verdict. (For an un-oriented generalist the gate
    is identity, so this is only meaningful for the differentiated specialists.
    Off-base alignment is far below the gate threshold, so the single-shot check
    is robust here -- unlike the marginal coverage sim.)"""
    return all(not _aligned_verdict(c, PROBES[base_key]) for c in specs
               if c.orientation is not None)


def _coverage_table(specs: list[Cell], owned: list[str], bases: list[str],
                    known: list[str], title: str) -> dict[str, list[int]]:
    print(f"\n=== {title} ===")
    print(f"  (COVERED = some specialist's MEAN field-sim over {N_DRAWS} veil draws "
          f"clears sim_min=0.70; else IN ∩N_i)")
    cov: dict[str, list[int]] = {}
    best: dict[str, float] = {}
    for b in bases:
        sims = [_covered_by(c, b) for c in specs]
        cov[b] = [i for i, (ok, _s) in enumerate(sims) if ok]
        best[b] = max((s for _ok, s in sims), default=0.0)
    print("  base        coverage   via              mean-sim  kind")
    for b in bases:
        actors = cov[b]
        label = "COVERED" if actors else "IN ∩N_i"
        via = f"spec-{actors[0]} ({owned[actors[0]]})" if actors else "no specialist"
        kind = "known" if b in known else "NEW GAS"
        print(f"  {b:<10}  {label:<9}  {via:<16} {_f(best[b]):>5}  [{kind}]")
    return cov


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="multi-frontier-")
    print("multi-frontier experiment -- the organism residual / growth frontier")
    print("(stage 7: ∩N_i = the directions no specialist covers = the edge of the "
          "organism's null space)\n")

    seed_cfg = {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True}
    devnull = open(os.devnull, "w")

    # ---- 1. the 6 KNOWN specialists -- the EXACT multi_organism construction ----
    # Grow a generalist parent over the 6-BASE workload; bud 6 children
    # (direction_index=0..5); each pulls a FRESH 6-base buffer so it grows ONLY
    # its cone; then differentiate. Bit-identical to Phase 2 -> 6/6 coverage with
    # dense cones (the proven-GREEN construction; no new toy-noise risk).
    parent = _build_cell7("parent", seed_cfg, tmp)
    parent.membrane.sim_min = 0.70
    buf6 = SituationBuffer()
    for text in WORKLOAD:
        buf6.deposit(text)
    with contextlib.redirect_stderr(devnull):
        parent.pull(BufferView(buf6))
    dirs = orient_dirs(parent.membrane.store, len(BASES), 0.5)
    print(f"parent grew {len(parent.membrane.store)} exemplars over the 6-base "
          f"workload; distinct directions (orient_dirs) = {len(dirs)} "
          f"(need {len(BASES)} to bud one specialist per known base)")

    print(f"\nbudding {len(BASES)} specialists for the KNOWN bases "
          f"(the exact Phase-2 construction: inherit a parent direction, pull its "
          f"own cone, then differentiate -- sim_min=0.70, gate_strength=1.0):")
    specs: list[Cell] = []
    owned: list[str] = []
    for i in range(len(BASES)):
        bud = Cell.bud(parent, f"spec-{i}",
                       os.path.join(tmp, f"field-spec-{i}.json"),
                       os.path.join(tmp, f"audit-spec-{i}.jsonl"),
                       direction_index=i, orientation_strength=2.0)
        fresh = SituationBuffer()
        for text in WORKLOAD:
            fresh.deposit(text)
        with contextlib.redirect_stderr(devnull):
            bud.pull(BufferView(fresh))
        orient = bud.differentiate(direction_index=0, gate_strength=1.0)
        if orient is None:
            print(f"  spec-{i}: NOT READY (bud grew too few separable directions) -- RED")
            print(f"\nartifacts: {tmp}")
            return
        ob = _owned_base7(bud)
        specs.append(bud)
        owned.append(ob)
        print(f"  spec-{i} -> owns '{ob}'  "
              f"(field cut to {len(bud.membrane.store)} exemplars)")

    distinct = set(owned)
    print(f"\ndistinct owned bases = {len(distinct)}/{len(BASES)}  {sorted(distinct)}")
    if len(distinct) != len(BASES):
        print("  WARNING: ownership is not one-per-known-base (veil noise collapsed "
              "two specialists onto one base). Noted honestly.")

    # ---- 2. ∩N_i over the 7 bases = {secure} (the organism residual) ----------
    # The organism covers the 6 known bases; secure is the NEW GAS -- it is in no
    # specialist's field, so every specialist ESCALATES on it. secure is in EVERY
    # specialist's null -> secure ∈ ∩N_i. That intersection is the organism's
    # residual / growth frontier -- the edge of its null space.
    cov = _coverage_table(specs, owned, EXT_BASE_KEYS, list(BASE_KEYS),
                          "∩N_i -- the organism residual (bases NO specialist ACTs on)")
    resid = _residual(specs, EXT_BASE_KEYS)
    print(f"\n  ∩N_i (organism residual) = {resid if resid else '{}'}")
    print(f"  organism covers {len(EXT_BASE_KEYS) - len(resid)}/{len(EXT_BASE_KEYS)} "
          f"bases; the residual is the edge of the organism's null space (the new gas)")
    known_cov = len([b for b in BASE_KEYS if cov[b]])
    if SECURE_KEY in resid:
        refused = _refused_by_all(specs, SECURE_KEY)
        print(f"  [confirm] '{SECURE_KEY}' is refused by the alignment gate on "
              f"{'ALL' if refused else 'NOT all'} differentiated specialists "
              f"(every per-cell null -> ∈ ∩N_i)")
    sig_residual = (SECURE_KEY in resid) and (known_cov == len(BASES)) and (len(resid) == 1)

    # ---- 3. GROW INTO THE RESIDUAL: a de-novo specialist crystallizes secure ---
    # The organism self-builds (``pull`` the new secure gas -- crystallize it) and
    # self-cuts (``differentiate``: pave to the secure cone + lock) at the edge of
    # its null space. A FRESH generalist (no inherited orientation) pulls the
    # secure gas -> grows a 1-base field -> differentiate toward it. HONEST: the
    # live system buds this from the frontier cell; here it is de novo (isolates
    # the self-build + self-cut from the inheritance).
    print(f"\nGROWING INTO THE RESIDUAL: a de-novo cell pulls the new '{SECURE_KEY}' "
          f"gas (self-build), then differentiates toward it (self-cut):")
    bud7 = _build_cell7(f"spec-{SECURE_KEY}", seed_cfg, tmp)
    bud7.membrane.sim_min = 0.70
    fresh_secure = SituationBuffer()
    for text in SECURE_WORKLOAD:
        fresh_secure.deposit(text)
    with contextlib.redirect_stderr(devnull):
        bud7.pull(BufferView(fresh_secure))
    print(f"  spec-{SECURE_KEY} crystallized {len(bud7.membrane.store)} secure "
          f"exemplars from the new gas")
    orient7 = bud7.differentiate(direction_index=0, gate_strength=1.0)
    if orient7 is None:
        print(f"  spec-{SECURE_KEY}: NOT READY (grew too few separable directions) -- RED")
        print(f"\nartifacts: {tmp}")
        return
    ob7 = _owned_base7(bud7)
    specs.append(bud7)
    owned.append(ob7)
    print(f"  spec-{SECURE_KEY} -> owns '{ob7}'  "
          f"(self-cut to {len(bud7.membrane.store)} exemplars + locked)")

    # ---- 4. ∩N_i over the 7 bases collapses to {} (the organism grew) ----------
    _coverage_table(specs, owned, EXT_BASE_KEYS, list(BASE_KEYS),
                    "∩N_i AFTER growing into the residual")
    resid2 = _residual(specs, EXT_BASE_KEYS)
    print(f"\n  ∩N_i (organism residual) = {resid2 if resid2 else '{}'}")
    print(f"  organism covers {len(EXT_BASE_KEYS) - len(resid2)}/{len(EXT_BASE_KEYS)} bases "
          f"(grew from {len(EXT_BASE_KEYS) - len(resid)}/{len(EXT_BASE_KEYS)})")
    sig_grew = (len(resid2) == 0) and (len(EXT_BASE_KEYS) - len(resid2)
                                       > len(EXT_BASE_KEYS) - len(resid))

    # ---- 5. verdict -----------------------------------------------------------
    print("\n=== organism residual / growth-frontier test -- verdict ===")
    print(f"  residual (∩N_i = {{secure}} before growth):   "
          f"{'GREEN' if sig_residual else 'RED'}  "
          f"the new gas is the edge of the organism's null space (6/6 known "
          f"covered, secure refused by every specialist)")
    print(f"  growth (∩N_i -> {{}} after the new specialist): "
          f"{'GREEN' if sig_grew else 'RED'}  "
          f"the organism self-built + self-cut a specialist into its residual")
    sig_owner = ob7 == SECURE_KEY
    print(f"  new specialist owns secure:                 "
          f"{'GREEN' if sig_owner else 'RED'}  owns '{ob7}' "
          f"(self-built from the new gas, self-cut toward it)")
    overall = sig_residual and sig_grew and sig_owner
    if overall:
        print("\n  OVERALL: GREEN -- the organism grows into its null space: ∩N_i "
              "(the residual no specialist covers) is the growth frontier, and the "
              "organism self-builds (pull) + self-cuts (differentiate) a specialist "
              "into it, collapsing ∩N_i back to empty.")
    else:
        print("\n  OVERALL: RED (see above)")
    print("\n  HONEST CAVEAT: a test of the §4 / VT READING, not a proof. ∩N_i is")
    print("  computed over the toy's DISCRETE 7-base set; in a continuous field it")
    print("  is the cone-complement (a region, not a list). The 'edges of null space")
    print("  = ∩N_i = the growth frontier' framing is the VT lens (the residual as")
    print("  the learning signal), an imposed interpretation. The self-cut is")
    print("  SELECTION; the §4.7 SVD-projection coarse-graining is a deferred `mode=`")
    print("  variant and stays unvetted. The new specialist is DE NOVO (grown from")
    print("  the new gas), not budded from the frontier cell -- isolates the")
    print("  self-build + self-cut from the inheritance. Toy calibrations")
    print("  (sim_min=0.70, gate_strength=1.0) are the D=8/0.25-veil regime; the real")
    print("  1024-d regime keeps 0.85/4.0.")

    print(f"\nartifacts (per-specialist field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


if __name__ == "__main__":
    main()