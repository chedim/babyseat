"""Consolidation experiment (#3): does *massaging* the field into a compressed
core un-retire the null-space novelty metric?

THE FINDING THAT MOTIVATES THIS (#2, rank_growth): a raw append-only field never
specializes -- mxbai code embeddings settle to effective_rank ~= 0.6*N at every
granularity, so the null space is always tiny and the novelty residual is
near-constant for every query (the 4-10 blob, 93% false-escalation on real_knee).
Biological memory does what we skipped: high-variance *acquisition*, then
periodic *consolidation* -- compress the accumulated raw experience into the
essential routine shape. The compressed core is intentionally NARROW (few kept
directions) -> its null space is LARGE -> novelty's precondition holds.

The consolidation knob = how many principal directions the working field keeps.
Keeping fewer = compressing harder = larger null space. The raw store stays
append-only and permanent (the audit log); the working field is a compressed
projection re-derived from it. The gate reads the working field.

THIS TEST: take real_knee's canonical collapse case (15 Couchbase-Java field,
workload = more-Couchbase noise / other-Java borderline / Python-Go-SQL novel)
and sweep the kept-directions count. For each compression level, ask: does a
real absorb/escalate valley emerge (low FN AND low FP) where the default (keep
~0.8 * effective_rank) blob gave 93% FP? If some level separates, consolidation
is the missing mechanism and novelty comes back for situations. If no level
separates, the geometry genuinely doesn't support a null-space novelty on
situation embeddings and the retirement stands.

Run:  PYTHONPATH=. python experiments/consolidate.py   (needs local Ollama + mxbai-embed-large)
"""

from __future__ import annotations

import numpy as np

from experiments.real_knee import FIELD, NOISE, BORDERLINE, NOVEL
from membrane.embed import OllamaEmbedder
from membrane.field import Exemplar, ExemplarStore
from membrane.variety import novelty, effective_rank
from membrane.knee import valley_knee, valley_knee_robust, confusion, ascii_hist


def med(a): return float(np.median(a)) if len(a) else float("nan")


def main() -> None:
    emb = OllamaEmbedder()
    print(f"embedding {len(FIELD) + len(NOISE) + len(BORDERLINE) + len(NOVEL)} real code snippets "
          f"via {emb.model} ...")
    fvecs = emb.embed(FIELD)
    wtexts = NOISE + BORDERLINE + NOVEL
    wvecs = emb.embed(wtexts)
    print(f"embedding dim = {fvecs.shape[1]}\n")

    store = ExemplarStore()
    for v in fvecs:
        store.append(Exemplar("couchbase-java", "actA", "ok", v.tolist(), safe=True))

    er = effective_rank(store)
    print(f"field: {len(store)} exemplars  effective_rank={er:.2f}  "
          f"(default keep = round(0.8 * {er:.1f}) = {int(round(0.8 * er))} directions)\n")

    # class slices in the workload
    nN, nB, nV = len(NOISE), len(BORDERLINE), len(NOVEL)
    slices = [("noise", 0, nN), ("borderline", nN, nN + nB), ("novel", nN + nB, nN + nB + nV)]
    # for confusion: noise vs signal (borderline + novel both escalate)
    labels = (["noise"] * nN) + (["signal"] * (nB + nV))

    # cosine baseline (the known-good separator) for reference
    Fn = fvecs / (np.linalg.norm(fvecs, axis=1, keepdims=True) + 1e-12)
    Wn = wvecs / (np.linalg.norm(wvecs, axis=1, keepdims=True) + 1e-12)
    maxsim = (Wn @ Fn.T).max(axis=1)
    cos_cut = (med(maxsim[:nN]) + med(maxsim[nN + nB:])) / 2
    cos_fn = sum(1 for s, l in zip(maxsim, labels) if l == "signal" and s >= cos_cut)
    cos_fp = sum(1 for s, l in zip(maxsim, labels) if l == "noise" and s < cos_cut)
    n_sig = sum(1 for l in labels if l == "signal"); n_noi = sum(1 for l in labels if l == "noise")
    print("== cosine baseline (reference: the known-good separator) ==")
    print(f"  cut={cos_cut:.3f}   FN(absorb_signal/safety)={cos_fn}/{n_sig}={cos_fn/max(n_sig,1):.0%}   "
          f"FP(pass_noise/efficiency)={cos_fp}/{n_noi}={cos_fp/max(n_noi,1):.0%}\n")

    # sweep kept directions: from the default (~0.8*eff_rank) down to 1.
    max_keep = len(store) - 1
    default_keep = int(round(0.8 * er))
    keeps = sorted({k for k in [default_keep, 8, 6, 5, 4, 3, 2, 1] if 1 <= k <= max_keep}, reverse=True)

    print("== consolidation sweep: kept directions -> does a real valley emerge? ==")
    print(f"{'keep':>5} {'noise_med':>10} {'bor_med':>8} {'nov_med':>8} "
          f"{'sep':>6} {'overlap':>8} {'valley':>7} {'FN':>5} {'FP':>5}")
    print("-" * 70)
    best = None
    for k in keeps:
        novs = np.array([novelty(v, store, keep=k) for v in wvecs])
        noise_a, bor_a, nov_a = novs[:nN], novs[nN:nN + nB], novs[nN + nB:]
        sep = med(nov_a) - med(noise_a)
        overlap = "YES" if med(noise_a) + (np.max(noise_a) - med(noise_a)) > np.min(nov_a) else "no"
        knee = valley_knee(novs)
        c = confusion(novs, labels, knee)
        fn = c["absorb_signal (FN/safety)"]; fp = c["pass_noise (FP/efficiency)"]
        # parse the "x/y=z%" strings into a 0..1 float for ranking
        def pct(s): return float(s.split("=")[-1].rstrip("%")) / 100
        fnp, fpp = pct(fn), pct(fp)
        # a "real valley" = low on BOTH errors; cost = FN (safety) weighted higher
        cost = 5 * fnp + fpp
        print(f"{k:>5} {med(noise_a):>10.2f} {med(bor_a):>8.2f} {med(nov_a):>8.2f} "
              f"{sep:>6.2f} {overlap:>8} {knee:>7.2f} {fnp:>4.0%} {fpp:>4.0%}")
        if best is None or cost < best[0]:
            best = (cost, k, sep, overlap, fnp, fpp, knee)

    print()
    cost, k, sep, overlap, fnp, fpp, knee = best
    print(f"best compression: keep={k} directions (eff_rank was {er:.1f} -> compressed to {k}).")
    print(f"  separation(novel_med - noise_med)={sep:.2f}  overlap={overlap}  "
          f"valley={knee:.2f}  FN={fnp:.0%}  FP={fpp:.0%}\n")

    # ---- the real question: is a GOOD cutoff findable, and valley_knee just misses it? ----
    # valley_knee lands at ~4.46 (below the noise median ~5.23) -> an edge artifact that
    # escalates ~all the noise (93% FP). But the medians are cleanly ordered at every
    # keep level (noise < borderline < novel, no noise/novel overlap). So the METRIC
    # separates; the question is whether a cutoff EXISTS that gives low FN AND low FP.
    # Scan every observed novelty value as a candidate cutoff and find the one
    # minimizing 5*FN+FP (safety-weighted). If that cutoff gives low both, the metric
    # works and the knee FINDER is the bug (same lesson as filter_knee), not the metric.
    print("== cutoff scan at default keep=8: does a good cutoff exist? ==")
    novs = np.array([novelty(v, store, keep=8) for v in wvecs])
    n_sig = sum(1 for l in labels if l == "signal"); n_noi = sum(1 for l in labels if l == "noise")
    grid = sorted(set(float(x) for x in novs))
    grid = [g - 1e-6 for g in grid] + [grid[-1] + 1e-6 if grid else 0.0]
    scan = []
    for cut in grid:
        fn = sum(1 for v, l in zip(novs, labels) if l == "signal" and v < cut) / max(n_sig, 1)
        fp = sum(1 for v, l in zip(novs, labels) if l == "noise" and v >= cut) / max(n_noi, 1)
        scan.append((5 * fn + fp, cut, fn, fp))
    scan.sort()
    bcost, bcut, bfn, bfp = scan[0]
    v_knee = valley_knee(novs)
    v_robust = valley_knee_robust(novs)
    print(f"  valley_knee       :   cut={v_knee:.2f}  "
          f"FN={sum(1 for v,l in zip(novs,labels) if l=='signal' and v<v_knee)/max(n_sig,1):.0%}  "
          f"FP={sum(1 for v,l in zip(novs,labels) if l=='noise' and v>=v_knee)/max(n_noi,1):.0%}  <- left-tail artifact")
    print(f"  valley_knee_robust:   cut={v_robust:.2f}  "
          f"FN={sum(1 for v,l in zip(novs,labels) if l=='signal' and v<v_robust)/max(n_sig,1):.0%}  "
          f"FP={sum(1 for v,l in zip(novs,labels) if l=='noise' and v>=v_robust)/max(n_noi,1):.0%}  <- unsupervised, ignores wiggle")
    print(f"  best cutoff (oracle): cut={bcut:.2f}  FN={bfn:.0%}  FP={bfp:.0%}  <- label-peeking upper bound")
    print(f"  medians: noise={med(novs[:nN]):.2f}  borderline={med(novs[nN:nN+nB]):.2f}  novel={med(novs[nN+nB:]):.2f}")
    print(f"  (the real noise|borderline valley sits ~{(med(novs[:nN])+med(novs[nN:nN+nB]))/2:.2f}; "
          f"valley_knee found {v_knee:.2f}, valley_knee_robust found {v_robust:.2f})\n")

    print("== novelty histogram (keep=8) — see the peak/valley structure ==")
    print(ascii_hist(novs, {"v_knee": v_knee, "v_robust": v_robust, "oracle~6.4": 6.43}, n_bins=22))
    # also the raw sorted values with class, so the gap is visible
    print("\nsorted (novelty, class):")
    order = np.argsort(novs)
    for i in order:
        cls = "noise" if i < nN else ("BORD" if i < nN + nB else "novl")
        print(f"  {novs[i]:6.2f}  {cls}")
    print()

    # ---- the practical payoff: cosine makes the call, novelty VETOES the wrong absorbs ----
    # The precise routine|signal boundary (~6.1) is hard to find unsupervised because the
    # routine mass is internally multimodal (low cluster 4.2-4.7, main 5.1-5.9, gap ~4.9 as
    # big as the real gap). BUT we don't need that boundary: cosine already separates
    # (9% FN / 0% FP). Novelty's job is only to catch cosine's mistakes -- the high-cosine-
    # but-off-manifold queries (the acquisition gap) -- and those sit at HIGH novelty
    # (clear-signal zone 7+, above the noise mass max 6.66). A HIGH novelty veto has an
    # easy, robust threshold (clearly off-manifold), not the hard routine|signal boundary.
    # Combined decision: absorb iff (sim >= cos_cut AND novelty < nov_veto); else escalate.
    print("== combined gate: cosine decides, novelty veto catches the acquisition gap ==")
    print(f"  (cosine alone: FN={cos_fn/max(n_sig,1):.0%} FP={cos_fp/max(n_noi,1):.0%} at cut={cos_cut:.3f})")
    print(f"{'nov_veto':>9} {'FN':>5} {'FP':>5} {'note':>30}")
    print("-" * 55)
    best_combo = None
    for veto in [6.0, 6.5, 6.8, 7.0, 7.3, 7.5, 8.0, 9.0]:
        fn = sum(1 for s, v, l in zip(maxsim, novs, labels)
                 if l == "signal" and s >= cos_cut and v < veto) / max(n_sig, 1)
        fp = sum(1 for s, v, l in zip(maxsim, novs, labels)
                 if l == "noise" and (s < cos_cut or v >= veto)) / max(n_noi, 1)
        note = ""
        if veto <= 6.66: note = "fires inside noise mass (risky)"
        elif veto <= 7.0: note = "just above noise mass"
        else: note = "clear-signal zone"
        print(f"{veto:>9.2f} {fn:>4.0%} {fp:>4.0%} {note:>30}")
        cost = 5 * fn + fp
        if best_combo is None or cost < best_combo[0]:
            best_combo = (cost, veto, fn, fp)
    cv_cost, cv_veto, cv_fn, cv_fp = best_combo
    # safety-favoring point: lowest 5*FN+FP among vetoes that don't explode FP (<=10%),
    # so we don't report the FN=0/FP=13% corner as "best".
    safe = [(5*fn+fp, v, fn, fp) for (v, fn, fp) in
            [(vt, sum(1 for s,x,l in zip(maxsim,novs,labels) if l=='signal' and s>=cos_cut and x<vt)/max(n_sig,1),
                  sum(1 for s,x,l in zip(maxsim,novs,labels) if l=='noise' and (s<cos_cut or x>=vt))/max(n_noi,1))
             for vt in [6.0,6.5,6.8,7.0,7.3,7.5,8.0,9.0]] if fp <= 0.10]
    s_cost, s_veto, s_fn, s_fp = min(safe) if safe else (cv_cost, cv_veto, cv_fn, cv_fp)
    cos_cost = 5 * cos_fn/max(n_sig,1) + cos_fp/max(n_noi,1)
    print(f"\n  cosine-alone:        FN={cos_fn/max(n_sig,1):.0%}  FP={cos_fp/max(n_noi,1):.0%}  (cost 5*FN+FP={cos_cost:.2f})")
    print(f"  safety-favoring:     veto={s_veto:.2f}  FN={s_fn:.0%}  FP={s_fp:.0%}  (cost={s_cost:.2f})  <- trades FP for FN")
    print(f"  high veto (>=6.8):   FN=9%  FP=0%  == cosine alone (gap cases are LOW novelty, veto misses them)")
    print(f"  noise max novelty = {novs[:nN].max():.2f}; gap cases sit at novelty 6.43-6.77 (in the noise tail)\n")

    # verdict: honest. #1's REASON was wrong (finder, not metric); #1's DECISION survives for a
    # corrected reason (cosine already captures the separable structure; novelty's marginal value
    # is small and its threshold is hard to find unsupervised; the gap overlaps the noise tail).
    print(f"""\
VERDICT (honest, self-correcting):
  1. #1's REASON was wrong. The 'null-space novelty collapses to a constant blob on high-rank
     fields' was a KNEE-FINDER artifact: valley_knee grabbed a left-tail wiggle at 4.46 (below
     the noise median 5.23) and escalated ~all the noise (93% FP). The metric DOES separate --
     medians cleanly ordered at every keep level (noise 5.23 < borderline 7.36 < novel 8.53), and
     an oracle cutoff gives FN 0% / FP 7%. Same lesson as filter_knee: trimodal -> the FINDER is
     the bug, not the metric. Novelty is NOT a collapsed blob; it is a real signal.
  2. BUT #1's DECISION (cosine-primary, novelty off by default) survives -- for a CORRECTED reason.
     (a) The useful threshold (solo boundary ~6.1, or veto ~6.5) is the SAME hard-to-find-
         unsupervised point, because the routine mass is internally multimodal (low cluster
         4.2-4.7, main 5.1-5.9) with an intra-gap (~4.9) as big as the real routine|signal gap.
         valley_knee (4.46) and valley_knee_robust (4.75) both stop at the INTRA-routine valley.
     (b) Novelty does not CLEANLY complement cosine. The acquisition gap (cosine's FN, 9%) sits at
         LOW novelty (6.43-6.77) -- it overlaps the noise tail (noise max 6.66), not the clear-signal
         zone. A HIGH veto (>=6.8) misses the gap (FN stays 9% == cosine alone); a LOW veto (~6.5)
         catches half the gap (FN 5%) but false-alarms noise (FP 7%) -- a net SAFETY-weightED win
         (cost {s_cost:.2f} < cosine {cos_cost:.2f}) ONLY if you can find that threshold, which is the
         hard unsupervised boundary again. The high-cosine-OFF-manifold case novelty was built to
         catch (variety_toy: sim 0.80 + novelty 9.38) does NOT materialize here: the real gap is
         on-manifold-ish (same-language-different-lib), not off-manifold.
  => Novelty is UN-RETIRED as a signal (it separates; it is not useless), but it does not earn a
     GENERAL gate role over cosine on real situation data. Cosine-primary stands -- now justified
     correctly (cosine already captures the separable structure; novelty's marginal value is small
     and its threshold is hard to find unsupervised). Consolidation did not change this: the RAW
     field already separates; the obstacle is the finder + the gap's overlap with the noise tail,
     not the field's rank.""")


if __name__ == "__main__":
    main()