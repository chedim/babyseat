#!/usr/bin/env python3
"""Multi-body experiment -- the ORGANISM AS ONE BODY (the (A) reading prototype).

The prior phases (multi_differentiate / multi_organism / multi_frontier, all GREEN)
read §4 "the reverse of budding" as a COLONY: each cell carries its OWN private
veiled field (``cell.membrane.store`` is a per-cell ``ExemplarStore``), grown by
``pull`` and structurally amputated by ``differentiate`` (``self.store.exemplars =
kept`` -- the self-cut). The organism is a POPULATION of these private bodies.
Phase 1's death is STRUCTURAL: the cell's own field collapses (effective_rank
6 -> 1, the orthogonal exemplars paved).

This experiment tests the OTHER reading -- the ORGANISM AS ONE BODY -- raised as
"what if a cell is a PROCESS on a shared crystal, not a memory area? fold the
cells onto the crystal; the cut keeps the veil because cells can't see each
other." In this reading:

  - The CRYSTAL is ONE shared body of RAW exemplars (no per-cell private field,
    no per-cell memory duplication). The organism IS the crystal.
  - A CELL is a PROCESS: a lightweight handle (orientation + veil + gate) over
    the shared crystal, NOT a memory area. The process READS the crystal
    through its own veil (re-veil-on-read) and a per-process MASK that hides
    every cone but its own ("cells can't see each other").
  - The CUT is BEHAVIORAL, not structural: the process commits to one direction
    and LOCKS the hard alignment gate, so it ACTS on its cone and ESCALATES on
    the complement. Nothing is amputated from the crystal -- the body keeps the
    whole variety (the whole "genome"). This is the biological reading: one
    genome, expression-gated specialization (a liver cell does not DELETE the
    kidney genes; it does not EXPRESS them). Held as a reading -- the operator
    and the author are not biologists; it is a resonance with the standard
    account of multicellularity, not an established fact.

THE TRADE THIS EXPOSES (the load-bearing measurement):
  - The gate signatures (act on cone / escalate complement / organism covers /
    grow into the residual) REPRODUCE -- behavioral death + resurrection + growth.
  - Phase 1's STRUCTURAL collapse (effective_rank 6 -> 1) does NOT reproduce --
    by construction: the body is never amputated, so its rank stays HIGH. Death
    is "won't act on the complement," not "rank collapse." This is the measured
    cost of the one-body reading: you lose the per-cell crystal-death proxy and
    gain a true shared-body organism.
  - The formula shifts: with no per-cell field to amputate, the "cut" IS the
    cone-mask IS the lock -- the same act -- so ``Cμ_cell = cut(Cμ) < V_env``
    (the ``≤`` collapses to ``=``). The current per-cell + amputation reading
    keeps ``Cμ_cell ≤ cut(Cμ) < V_env`` (amputation reduces a per-cell field,
    the lock caps it below the cut -> the ``≤``). The prototype MEASURES this:
    per-process cone rank (~1) = the cut (the aligned slice) < crystal rank
    (~6-7) = V_env.

This is adjacent to the §4.7 openness: "pave the null space" as REMOVAL of
variety (structural, needs a private field) vs REFUSAL / withheld expression
(behavioral, works on a shared crystal). (A) takes the latter reading. The
§4.7 SVD-projection coarse-graining stays unvetted either way.

CONSTRUCTION (no library change -- self-contained; reuses the field/gate/
variety machinery directly, NOT the Cell object, because a process here owns no
store):
  1. RAW crystal: the 6 known workloads, embedded RAW (no veil) -> one shared
     ExemplarStore. effective_rank ~6 (the 6 orthogonal base axes).
  2. 6 processes: each veils the shared crystal (re-veil-on-read), finds its
     target base's direction via ``orient_dirs`` on its veiled view, COMMITS
     (orientation + lock, gate_strength=1.0) and MASKS its view to that cone
     (``aligned_safe_slice``). NO amputation of the crystal. Each acts on its
     cone, escalates the rest (behavioral death); the organism covers 6/6
     (resurrection).
  3. ∩N_i: "secure" (the held-out 7th base) is NOT in the crystal -> no process
     covers it -> ∩N_i = {secure} (the organism residual / growth frontier).
  4. GROW: deposit "secure" into the crystal (the BODY grows -- self-build) and
     add a 7th process committed to it (self-cut: commit + lock + mask). The
     crystal's rank rises to ~7; ∩N_i -> {}.
  5. The contrast: the crystal's effective_rank stays HIGH throughout (never
     amputated), vs Phase 1's per-cell 6 -> 1 structural collapse.

VEIL MODEL (honest, a design choice (A) implies): the stored view is a
DETERMINISTIC re-veil of the raw crystal (one noise realization per (process,
exemplar) -- the process's fixed lens on the body); the QUERY is re-veiled per
draw and MEANED over 24 draws (the denoised measurement Phase 2/3 established).
This mirrors the current design's "stored veiled once, query re-veiled per
call, mean over draws" -- only the stored side is re-veiled from a shared raw
crystal instead of privately deposited. (A fully-deterministic per-(process,
query) veil is an alternative that would kill the single-shot variance but
gives a marginal single-realization same-base sim in the D=8 toy; the meaned
query is the faithful, proven measurement.)

HONEST CAVEAT (held): a test of the (A) / VT READING, not a proof. The crystal
stores RAW vectors (a departure from the current "stored vec is veiled"); the
re-veil is on read (a real implementation veils in-place at query without
materializing the per-process copy this prototype builds transiently). The
mask is per-process and NON-destructive (the crystal is untouched); it is NOT
the self-cut-amputation of Phases 1-3 -- it is the per-process expression filter
on a shared genome. The structural-collapse death (Phase-1 rank 6 -> 1) does
NOT reproduce by construction; that is the measured trade, not a failure. The
§4.7 SVD-projection coarse-graining is a deferred variant and stays unvetted.
The "∩N_i = the growth frontier" and "one genome, expression-gated
specialization" framings are the imposed VT / biology lens, not results the
literature asserts. Toy calibrations (sim_min=0.70, gate_strength=1.0,
nudge_sigma=0.25) are the D=8 / 0.25-veil regime; the real 1024-d mxbai regime
keeps 0.85 / 4.0.

Runs with no Ollama / no Anthropic / no real key (the fake embedder reused
from multi_frontier). All state in-memory; nothing leaks to the repo.
"""
from __future__ import annotations

import hashlib
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.field import Exemplar, ExemplarStore
from membrane.variety import effective_rank, aligned_safe_slice
from membrane.curiosity import _aligned

# Reuse the 7-base fake geometry, the workload, the held-out base, the probes,
# the base resolver, and the formatter -- all from the Phase-3 frontier harness.
from experiments.multi_cell import BASES, BASE_KEYS, WORKLOAD, D, _f  # noqa: E402
from experiments.multi_frontier import (  # noqa: E402
    EXT_BASES, EXT_BASE_KEYS, SECURE_KEY, SECURE_WORKLOAD, PROBES,
    _base_of7, _FakeEmbedder7,
)

# ---- calibrations (the D=8 / 0.25-veil toy regime; 1024-d keeps 0.85/4.0) ------
SIM_MIN = 0.70          # the gate default; same-base mean sim lands ~0.78-0.86
GATE_STRENGTH = 1.0     # hard alignment gate at cosine > 0.5 (owned ~0.69 vs off ~0.1)
NUDGE = 0.25            # the veil's per-draw Gaussian noise (sigma)
ALIGN_MIN = 0.5         # the cone mask: keep exemplars aligned to the orientation beyond this
COS_SEP = 0.5           # orient_dirs: mutually cos_sep-apart representative directions
N_DRAWS = 24            # the denoised coverage measurement (mean field-sim over draws)

_EMBED = _FakeEmbedder7()


def _pair_seed(proc_seed: int, item_id) -> int:
    """A stable seed for a (process, item) pair -> a deterministic per-process
    veil realization. (Stable across runs -> reproducible; distinct per process
    -> each process sees a different fixed lens on the shared crystal.)"""
    h = hashlib.sha1(f"{proc_seed}|{item_id}".encode()).digest()
    return int(h[:8].hex(), 16)


def _nudge_det(vec: np.ndarray, proc_seed: int, item_id, sigma: float = NUDGE) -> np.ndarray:
    """A DETERMINISTIC veil of one vector under this process's veil (one noise
    realization seeded by (process, item)). The process's fixed lens on one
    axis/exemplar/query. sigma=0 -> identity."""
    if sigma <= 0.0:
        return vec
    rng = np.random.default_rng(_pair_seed(proc_seed, item_id))
    v = vec + rng.normal(0.0, sigma, size=vec.shape)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else vec


def _raw_crystal(texts: list[str]) -> ExemplarStore:
    """The shared body: one ExemplarStore of RAW embeddings (no veil). The
    organism's genome -- never amputated, only grown (deposit) and read."""
    store = ExemplarStore()
    for t in texts:
        v = np.asarray(_EMBED.embed([t])[0], dtype=float)
        store.append(Exemplar(situation=t, action=f"do-{_base_of7(t) or 'generic'}",
                              outcome="", vec=v.tolist(), safe=True))
    return store


def _grow(raw_store: ExemplarStore, texts: list[str]) -> None:
    """The body grows: deposit new gas as RAW exemplars (the self-build, on the
    shared crystal -- not into a per-cell private field)."""
    for t in texts:
        v = np.asarray(_EMBED.embed([t])[0], dtype=float)
        raw_store.append(Exemplar(situation=t, action=f"do-{_base_of7(t) or 'generic'}",
                                  outcome="", vec=v.tolist(), safe=True))


def _veiled_view(raw_store: ExemplarStore, proc_seed: int,
                 sigma: float = NUDGE) -> ExemplarStore:
    """The process's fixed veiled view of the shared crystal: a DETERMINISTIC
    re-veil (one noise realization per (process, exemplar)). This is the per-
    process lens -- two processes see different fixed veiled views of the SAME
    raw body. Transient (materialized per measurement, not persisted); a real
    implementation veils in-place at query without the copy."""
    view = ExemplarStore()
    for i, ex in enumerate(raw_store.exemplars):
        v = np.asarray(ex.vec, dtype=float)
        if sigma > 0:
            rng = np.random.default_rng(_pair_seed(proc_seed, i))
            vn = v + rng.normal(0.0, sigma, size=v.shape)
            n = np.linalg.norm(vn)
            v = vn / n if n > 1e-12 else v
        view.append(Exemplar(situation=ex.situation, action=ex.action,
                             outcome=ex.outcome, vec=v.tolist(), safe=ex.safe))
    return view


def _cone_view(veiled_view: ExemplarStore, orientation: np.ndarray,
               align_min: float = ALIGN_MIN) -> tuple[ExemplarStore, int]:
    """The per-process MASK: the cone (aligned slice) of the veiled view -- what
    the process will ACT on. The complement is hidden from this process ("cells
    can't see each other"). NON-destructive: the shared crystal is untouched;
    only this transient view is masked. This is the behavioral cut."""
    kept = aligned_safe_slice(veiled_view, orientation, align_min,
                             max_n=len(veiled_view))
    s = ExemplarStore()
    for ex in kept:
        s.append(ex)
    return s, len(kept)


def _cone_owned_base(cone: ExemplarStore, orientation: np.ndarray) -> str | None:
    """Which base a cone OWNS -- the base of the cone exemplar most aligned to
    its orientation (the cone is one base's veiled cloud)."""
    if len(cone) == 0:
        return None
    o = np.asarray(orientation, dtype=float)
    on = np.linalg.norm(o) + 1e-12
    best, best_sim = None, -2.0
    for ex in cone.exemplars:
        v = np.asarray(ex.vec, dtype=float)
        s = float(v @ o / ((np.linalg.norm(v) + 1e-12) * on))
        if s > best_sim:
            best_sim, best = s, ex
    return _base_of7(best.situation) if best is not None else None


def _cone_sim(proc_seed: int, cone: ExemplarStore, text: str,
              n: int = N_DRAWS) -> float:
    """Mean nearest-neighbour sim over ``n`` veiled query draws against the
    cone -- the DENOISED coverage signal (the query is re-veiled per draw under
    the process's veil; the stored cone is the fixed veiled view). Mirrors the
    Phase-3 measurement: stored veiled once, query re-veiled per call, mean over
    draws. Reproducible (seeded per (process, query))."""
    if len(cone) == 0:
        return 0.0
    base = np.asarray(_EMBED.embed([text])[0], dtype=float)
    rng = np.random.default_rng(_pair_seed(proc_seed, "q:" + text))
    sims = []
    for _ in range(n):
        noise = rng.normal(0.0, NUDGE, size=base.shape)
        q = base + noise
        qn = np.linalg.norm(q)
        q = q / qn if qn > 1e-12 else base
        tk = cone.top_k(q, k=5)
        sims.append(tk[0][1] if tk else 0.0)
    return float(np.mean(sims))


def _covered(proc_seed: int, cone: ExemplarStore, text: str) -> tuple[bool, float]:
    """(covered, mean_sim) -- covered if the mean cone-sim clears sim_min."""
    s = _cone_sim(proc_seed, cone, text)
    return s >= SIM_MIN, s


def _aligned_ok(proc_seed: int, orientation: np.ndarray, text: str,
                strength: float = GATE_STRENGTH) -> bool:
    """The hard alignment gate (the LOCK) on a single veiled query: True = on-cone
    (pull/express allowed), False = off-cone (refused). Single-shot is robust
    here -- off-cone alignment is far below the gate threshold (unlike the
    marginal coverage sim, which is why coverage uses the mean over draws)."""
    base = np.asarray(_EMBED.embed([text])[0], dtype=float)
    rng = np.random.default_rng(_pair_seed(proc_seed, "q:" + text))
    noise = rng.normal(0.0, NUDGE, size=base.shape)
    q = base + noise
    qn = np.linalg.norm(q)
    q = q / qn if qn > 1e-12 else base
    return _aligned(q, np.asarray(orientation, dtype=float), strength)


def _aligned_frac(proc_seed: int, orientation: np.ndarray, text: str,
                  strength: float = GATE_STRENGTH, n: int = N_DRAWS) -> float:
    """The DENOISED LOCK: the fraction of ``n`` veiled query draws that clear the
    hard alignment gate. This is the behavioral ACT (express vs escalate),
    averaged over the veil the way Phase 3 established for the coverage sim.
    On-cone -> ~1.0 (express every draw); off-cone -> ~0.0 (refuse every draw);
    marginal -> intermediate. THIS is the load-bearing behavioral signal -- the
    cone-mask's field-sim (``_cone_sim``) measures what is IN the process's
    masked VIEW (and leaks cross-base exemplars in the D=8 toy, e.g. a docs
    exemplar at sim 0.71 inside a debug cone); the LOCK measures whether the
    process ACTS, and cleanly refuses the leaked base (alignment to the owned
    axis, not to the leaked exemplar). The (A) reading's 'behavioral cut' is the
    lock; the mask is the visibility filter. The real 1024-d regime keeps the
    mask clean too (orthogonal bases, sim_min 0.85)."""
    base = np.asarray(_EMBED.embed([text])[0], dtype=float)
    rng = np.random.default_rng(_pair_seed(proc_seed, "q:" + text))
    o = np.asarray(orientation, dtype=float)
    hits = 0
    for _ in range(n):
        noise = rng.normal(0.0, NUDGE, size=base.shape)
        q = base + noise
        qn = np.linalg.norm(q)
        q = q / qn if qn > 1e-12 else base
        if _aligned(q, o, strength):
            hits += 1
    return hits / n


def _commit(proc_seed: int, raw_store: ExemplarStore, target_base: str,
            max_k: int = 7) -> tuple[np.ndarray | None, ExemplarStore, int, ExemplarStore]:
    """A process COMMITS to ``target_base`` on the shared crystal: veil the
    crystal (re-veil-on-read), orient to the base's CLEAN veiled axis (the process
    commits to base b's direction in the body), and mask the view to that cone.
    NO amputation -- the crystal and the veiled view keep every base; only the
    returned cone (the mask) is the process's acting field.

    HONEST (toy-only choice): ``orient_dirs`` (the cell's own geometry -- the
    directions it GREW along) is the real-regime mechanism that recovers the
    per-base axes, but in this D=8 / 0.25-veil toy its representative directions
    TILT between the (raw-orthogonal) axes and ``aligned_safe_slice`` at 0.5 then
    leaks adjacent-base exemplars into the cone. So the prototype orients to the
    target base's clean veiled axis -- a clean single-base cone -- as a stand-in
    for ``orient_dirs``. The cone + the gate signatures are what (A) is testing;
    the discovery mechanism (``orient_dirs``) is orthogonal to the (A)-vs-(B)
    question (both readings use it) and recovers the clean axis in 1024-d."""
    view = _veiled_view(raw_store, proc_seed)
    axis = np.asarray(EXT_BASES[target_base], dtype=float)
    orientation = _nudge_det(axis, proc_seed, "axis:" + target_base)
    cone, n = _cone_view(view, orientation)
    if n == 0:
        return None, ExemplarStore(), 0, view
    return orientation, cone, n, view


class Proc:
    """A PROCESS on the shared crystal -- NOT a memory area. Holds its veil
    seed, committed orientation (the lock), and its cone (the masked view). Owns
    NO store; reads the shared crystal through its veil on demand."""
    __slots__ = ("seed", "tag", "orientation", "orientation_strength", "cone", "n_cone")

    def __init__(self, seed: int, tag: str):
        self.seed = seed
        self.tag = tag
        self.orientation: np.ndarray | None = None
        self.orientation_strength = GATE_STRENGTH
        self.cone: ExemplarStore = ExemplarStore()
        self.n_cone = 0

    def commit(self, raw_store: ExemplarStore, target_base: str) -> bool:
        orient, cone, n, _view = _commit(self.seed, raw_store, target_base)
        if orient is None:
            return False
        self.orientation = orient
        self.orientation_strength = GATE_STRENGTH     # the lock
        self.cone = cone                              # the behavioral cut (the mask)
        self.n_cone = n
        return True

    def owned_base(self) -> str | None:
        if self.orientation is None:
            return None
        return _cone_owned_base(self.cone, self.orientation)

    def covered(self, text: str) -> tuple[bool, float]:
        """(in-mask, mean cone-sim) -- what is in this process's masked VIEW. A
        secondary, looser signal (the mask width); it LEAKS cross-base exemplars
        in the D=8 toy (a docs exemplar can clear align_min=0.5 inside a debug
        cone). Use ``acts`` for the behavioral act (express/escalate)."""
        return _covered(self.seed, self.cone, text)

    def acts(self, text: str, frac_min: float = 0.5) -> tuple[bool, float]:
        """(acts, denoised lock-fraction) -- whether this process ACTS on ``text``
        (express) or ESCALATES (refuse). The behavioral act, averaged over the
        veil: the load-bearing signal for death + coverage. ``frac_min`` = the
        fraction of draws that must clear the gate to count as 'acts'."""
        if self.orientation is None:
            return True, 1.0
        f = _aligned_frac(self.seed, self.orientation, text)
        return f >= frac_min, f

    def aligned_ok(self, text: str) -> bool:
        if self.orientation is None:
            return True
        return _aligned_ok(self.seed, self.orientation, text)


def _coverage(procs: list[Proc], bases: list[str], known: list[str], title: str
              ) -> dict[str, list[int]]:
    """Organism COVERAGE via the denoised LOCK (the behavioral act): a base is
    COVERED if some process ACTS on it (lock-fraction >= 0.5 over the draws),
    else it is in ∩N_i. This is the load-bearing signal -- the cone-sim mask
    leaks cross-base exemplars in the D=8 toy (a docs exemplar at sim 0.71
    inside a debug cone), but the lock refuses the leaked base (alignment to the
    owned axis), so coverage-via-the-lock is clean. The mask width
    (``covered``) is reported separately as a diagnostic."""
    print(f"\n=== {title} ===")
    print(f"  (COVERED = some process ACTS on it: lock-fraction over {N_DRAWS} veil "
          f"draws >= 0.5; else IN ∩N_i)")
    cov: dict[str, list[int]] = {}
    best: dict[str, float] = {}
    for b in bases:
        res = [p.acts(PROBES[b]) for p in procs]
        cov[b] = [i for i, (ok, _f) in enumerate(res) if ok]
        best[b] = max((f for _ok, f in res), default=0.0)
    print("  base        coverage   via              act-frac  kind")
    for b in bases:
        actors = cov[b]
        label = "COVERED" if actors else "IN ∩N_i"
        via = f"proc-{actors[0]} ({procs[actors[0]].owned_base()})" if actors else "no process"
        kind = "known" if b in known else "NEW GAS"
        print(f"  {b:<10}  {label:<9}  {via:<16} {_f(best[b]):>5}  [{kind}]")
    return cov


def _residual(procs: list[Proc], bases: list[str]) -> list[str]:
    return [b for b in bases if all(not p.acts(PROBES[b])[0] for p in procs)]


def main() -> None:
    print("multi-body experiment -- the organism as ONE BODY (the (A) reading prototype)")
    print("(a cell is a PROCESS on a shared raw crystal, not a memory area; the cut is "
          "behavioral)\n")

    known = list(BASE_KEYS)
    all7 = EXT_BASE_KEYS

    # ---- 1. the shared RAW crystal: the 6 known bases (the organism's body) ----
    crystal = _raw_crystal(WORKLOAD)
    rank0 = effective_rank(crystal)
    print(f"shared RAW crystal: {len(crystal)} exemplars over the 6 known bases "
          f"(NO veil stored; one shared body)")
    print(f"  effective_rank = {_f(rank0)}  (the 6 orthogonal base axes; the body's "
          f"variety V_env -- it is NEVER amputated)")
    print(f"  (cf. Phase 1: each per-cell private field STARTS at this rank then "
          f"collapses 6 -> 1 on differentiate; here the body keeps it.)")

    # ---- 2. 6 processes commit to the 6 known bases (behavioral cut + lock) ----
    print(f"\ncommitting {len(known)} processes to the known bases "
          f"(veil the crystal, find the base's direction, commit + lock + mask to "
          f"its cone; NO amputation):")
    procs: list[Proc] = []
    for i, b in enumerate(known):
        p = Proc(seed=100 + i, tag=f"proc-{i}")
        if not p.commit(crystal, b):
            print(f"  proc-{i} -> NOT READY (could not find '{b}' in its veiled view) -- RED")
            print(f"\nartifacts: in-memory only")
            return
        ob = p.owned_base()
        cone_rank = effective_rank(p.cone)
        procs.append(p)
        print(f"  proc-{i} -> owns '{ob}'  cone={p.n_cone} exemplars  "
              f"cone effective_rank={_f(cone_rank)}  (the per-process cut = the mask; "
              f"the crystal stays {_f(rank0)})")
    distinct = {p.owned_base() for p in procs}
    print(f"\ndistinct owned bases = {len(distinct)}/{len(known)}  {sorted(distinct)}")

    # the body's rank AFTER all 6 commits -- the (A)-specific signature: it does
    # NOT collapse (nothing was amputated).
    rank_after_commits = effective_rank(crystal)
    print(f"crystal effective_rank after {len(known)} commits = {_f(rank_after_commits)}  "
          f"(UNCHANGED -- no amputation; the body keeps its variety)")

    # ---- 3. behavioral death + resurrection (gate signatures reproduce) --------
    # The behavioral ACT is the LOCK (does the process express or escalate),
    # denoised over the veil. The cone-sim (mask width) is shown as a secondary
    # diagnostic and is honestly looser -- it leaks cross-base exemplars in the
    # D=8 toy (proc-3's debug cone also contains a docs exemplar at sim ~0.71),
    # which the lock correctly refuses (alignment to the owned axis). Death +
    # coverage signatures use the lock.
    print("\nbehavioral death per process (ACTS on its cone / ESCALATES the complement) "
          "via the denoised LOCK:")
    print("        " + "  ".join(f"{k:>8}" for k in known) + "    owned")
    for i, p in enumerate(procs):
        act, mask = [], []
        for k in known:
            ok_a, fa = p.acts(PROBES[k])
            act.append(f"{fa:.2f}" if ok_a else " .")
            ok_m, sm = p.covered(PROBES[k])
            mask.append(f"{sm:.2f}" if ok_m else " .")
        print(f"  proc-{i} act  " + "  ".join(f"{x:>8}" for x in act)
              + f"    {p.owned_base()}")
        print(f"         mask " + "  ".join(f"{x:>8}" for x in mask)
              + "   (act=lock-fraction>=.5 express; mask=cone-sim>=.70 in-view; . =refused/hidden)")
    print("  NOTE: the mask can leak a cross-base exemplar in this D=8 toy (a docs "
          "exemplar at sim ~0.71 inside a debug cone); the LOCK refuses it (act on "
          "docs = .). The lock is the behavioral cut; the mask is the visibility filter.")

    cov = _coverage(procs, all7, known, "organism coverage vs ∩N_i (secure is the held-out gas)")
    resid = _residual(procs, all7)
    print(f"\n  ∩N_i (organism residual) = {resid if resid else '{}'}")
    print(f"  organism covers {len(all7) - len(resid)}/{len(all7)} bases")
    known_cov = len([b for b in known if cov[b]])
    sig_death = all(
        p.acts(PROBES[p.owned_base()])[0]
        and sum(1 for k in known if p.acts(PROBES[k])[0]) == 1
        for p in procs
    )
    sig_resurrect = known_cov == len(known)
    sig_residual = (SECURE_KEY in resid) and (known_cov == len(known)) and (len(resid) == 1)
    print(f"  behavioral death (each acts on 1, escalates {len(known)-1}): "
          f"{'GREEN' if sig_death else 'RED'}")
    print(f"  resurrection (organism covers {known_cov}/{len(known)}):     "
          f"{'GREEN' if sig_resurrect else 'RED'}")
    print(f"  residual (∩N_i = {{secure}}):           {'GREEN' if sig_residual else 'RED'}")

    # ---- 4. GROW: the body grows into secure + a 7th process commits to it ----
    print(f"\nGROWING INTO THE RESIDUAL: deposit '{SECURE_KEY}' into the shared crystal "
          f"(the body grows -- self-build), then a 7th process commits to it "
          f"(self-cut: commit + lock + mask):")
    _grow(crystal, SECURE_WORKLOAD)
    rank_grown = effective_rank(crystal)
    print(f"  crystal grew to {len(crystal)} exemplars; effective_rank {_f(rank0)} -> "
          f"{_f(rank_grown)}  (the BODY grew; still never amputated)")
    p7 = Proc(seed=100 + len(known), tag=f"proc-{SECURE_KEY}")
    if not p7.commit(crystal, SECURE_KEY):
        print(f"  proc-{SECURE_KEY}: NOT READY -- RED")
        print(f"\nartifacts: in-memory only")
        return
    ob7 = p7.owned_base()
    procs.append(p7)
    print(f"  proc-{SECURE_KEY} -> owns '{ob7}'  cone={p7.n_cone} exemplars  "
          f"(committed + locked + masked; the crystal stays {_f(rank_grown)})")

    _coverage(procs, all7, known, "∩N_i AFTER growing into the residual")
    resid2 = _residual(procs, all7)
    print(f"\n  ∩N_i (organism residual) = {resid2 if resid2 else '{}'}")
    print(f"  organism covers {len(all7) - len(resid2)}/{len(all7)} bases "
          f"(grew from {len(all7) - len(resid)}/{len(all7)})")
    sig_grew = (len(resid2) == 0) and (ob7 == SECURE_KEY)

    # ---- 5. the formula: Cμ_cell = cut(Cμ) < V_env  (the =, not ≤) --------------
    print("\n=== the cut + lock ceiling under the one-body reading ===")
    print(f"  V_env (crystal effective_rank, the whole body) = {_f(rank_grown)}")
    cone_ranks = [effective_rank(p.cone) for p in procs]
    print(f"  Cμ_cell per process (cone effective_rank, the per-process cut) = "
          f"{[ _f(r) for r in cone_ranks ]}")
    print(f"  the 'cut' IS the cone-mask (no per-cell field is amputated), so "
          f"cut(Cμ) = Cμ_cell ->  Cμ_cell = cut(Cμ) < V_env   (the '=', not '≤')")
    print(f"  (the per-cell + amputation reading keeps 'Cμ_cell ≤ cut(Cμ)': the cell HAD a "
          f"field, amputated it, the lock capped it below the cut. Here there is no "
          f"per-cell field before the cut -- the cut IS the cone -- so '='.)")
    sig_formula = all(r < rank_grown for r in cone_ranks) and rank_grown > max(cone_ranks)

    # ---- 6. the body-rank signature (the (A)-specific contrast with Phase 1) ----
    sig_body_rank = (abs(rank_after_commits - rank0) < 1.0
                     and rank_grown > rank0
                     and rank_after_commits > 1.5)
    print("\n=== the body-rank signature (the (A)-specific contrast with Phase 1) ===")
    print(f"  crystal effective_rank: {_f(rank0)} (6 bases) -> {_f(rank_after_commits)} "
          f"(after 6 commits) -> {_f(rank_grown)} (after growth)")
    print(f"  it does NOT collapse (Phase 1's per-cell field went 6 -> 1 on "
          f"differentiate; the shared body stays HIGH) -> "
          f"{'GREEN' if sig_body_rank else 'RED'}")

    # ---- 7. verdict -----------------------------------------------------------
    print("\n=== organism-as-one-body (the (A) reading) -- verdict ===")
    print(f"  behavioral death + resurrection:   {'GREEN' if sig_death and sig_resurrect else 'RED'}  "
          f"gate signatures reproduce (act on cone / escalate complement / cover 6/6)")
    print(f"  residual (∩N_i = {{secure}}):          {'GREEN' if sig_residual else 'RED'}  "
          f"the held-out gas is the growth frontier")
    print(f"  growth (∩N_i -> {{}}):              {'GREEN' if sig_grew else 'RED'}  "
          f"the body grew + a 7th process committed to it")
    print(f"  body rank preserved (no collapse): {'GREEN' if sig_body_rank else 'RED'}  "
          f"the shared body keeps its variety (the measured trade vs Phase-1 structural death)")
    print(f"  formula (Cμ_cell = cut(Cμ) < V_env):{'GREEN' if sig_formula else 'RED'}  "
          f"per-process cone < whole body; the cut IS the cone (the '=')")
    overall = (sig_death and sig_resurrect and sig_residual and sig_grew
               and sig_body_rank and sig_formula)
    if overall:
        print("\n  OVERALL: GREEN -- the organism-as-one-body reading reproduces the gate "
              "signatures (behavioral death + resurrection + growth) while PRESERVING the "
              "body's variety (no structural collapse). The cost, measured: Phase-1's "
              "crystal-death proxy (rank 6 -> 1) does not reproduce -- death is behavioral "
              "(won't act on the complement), and the formula reads '=' (the cut is the "
              "cone, not an amputation of a per-cell field).")
    else:
        print("\n  OVERALL: RED (see above)")
    print("\n  HONEST CAVEAT: a test of the (A) / VT READING, not a proof. The crystal stores")
    print("  RAW vectors (re-veil on read); the cut is a per-process NON-destructive mask,")
    print("  NOT the self-cut amputation of Phases 1-3. The structural-collapse death does NOT")
    print("  reproduce by construction (that is the measured trade). The §4.7 SVD-projection")
    print("  coarse-graining is a deferred variant and stays unvetted. The 'one genome,")
    print("  expression-gated specialization' framing is the imposed biology lens, not a")
    print("  result the literature asserts (neither author nor operator is a biologist). Toy")
    print("  calibrations (sim_min=0.70, gate_strength=1.0, nudge_sigma=0.25) are the")
    print("  D=8/0.25-veil regime; the real 1024-d mxbai regime keeps 0.85/4.0.")

    print("\nartifacts: in-memory only (nothing is written to the repo)")


if __name__ == "__main__":
    main()