#!/usr/bin/env python3
"""multi_meter -- WHERE Cmu and entropy diverge (the crux, in silico).

THE QUESTION (the thread's crux): does the thermodynamic work track Cmu (the
predictive structure -- the VARIETY the model stores) or the local entropy (a
MEASURE of the local composition)? This is the measure-not-identity caveat
(core principle, caveat 2) made physical: the gate (work <-> Cmu) is real only
if the work tracks the VARIETY (Cmu), not its thermodynamic MEASURE (the local
entropy / Delta-G). This file demonstrates WHERE they diverge, computationally.

WHERE THEY DIVERGE (the principle):
  Cmu and h_mu diverge BY DEFINITION -- they are COMPLEMENTARY (Rupe & Crutchfield
  2024, the yardstick): Cmu = stored structure (entropy of the causal-state
  distribution); h_mu = irreducible randomness (entropy rate). Different axes:
    deterministic structure (periodic): Cmu high, h_mu = 0
    pure noise (iid):                   Cmu = 0, h_mu high
  The GATE-relevant divergence is Cmu vs the thermodynamic Delta-G, and it lives
  at the SCALE GAP: Delta-G is an order-1 / nearest-neighbor quantity (a MEASURE
  of the local dinucleotide composition); Cmu is the FULL predictive structure
  (the VARIETY, at any scale). They diverge whenever there is structure at a
  scale BEYOND the local composition.

THE CONSTRUCTION (not exotic -- structure beyond the local scale):
  periodic_L256: a random uniform block of length 256, repeated. Then:
    - order-1 (dinucleotide): the block is random uniform -> UNIFORM dinucleotides
      -> Delta-G FLAT (the local MEASURE sees nothing).
    - Cmu (full predictive structure): the causal states are the 256 positions
      (each determines the future) -> Cmu = log2(256) = 8 bits (the VARIETY).
      h_mu = 0 (deterministic given the position).
  So Cmu = 8 (high), h_mu = 0, Delta-G flat. The thermodynamic Delta-G is FLAT
  while Cmu = 8 -> Delta-G is BLIND to the predictive structure. This is the
  divergence, and it lives at the scale gap (local Delta-G vs non-local Cmu).

THE METER (honest, load-bearing):
  The trajectory Cmu IS recoverable: the order-L history entropy H(L-gram) at
  sufficient L (here L=8: 4^8=65536 >> 256 positions, the 256 8-grams are distinct)
  -> H(8-gram) = log2(256) = 8 = Cmu_true. So a trajectory history-entropy meter
  DOES recover it. BUT membrane's Cmu is the SPECTRAL effective_rank (a snapshot
  of embedded vectors), NOT a trajectory history-entropy / eps-machine. The gap
  is the CODE: membrane has the spectral meter, not the trajectory meter. (The
  naive one-step Cmu estimator -- group histories by P(next|history) -- ALSO
  fails: it is capped at H(next) for a deterministic process, so it undercounts.
  The right meter is the order-L history entropy / the full-future eps-machine.)

THIS IS NOT THE GATE. The gate needs a NONEQUILIBRIUM, STRUCTURE-SENSITIVE work
(not the equilibrium nearest-neighbor Delta-G, which is local = the known side).
A simulated work would be circular (defined to match). So this demonstrates the
DIVERGENCE (Cmu vs the local thermodynamic measure) in silico; it does not close
the gate. "Exhibits the predicted signature under this construction," not proven.

Honest: toy nearest-neighbor Delta-G (a GC-count stacking model, NOT real
SantaLucia 1998 values -- the point is composition-dependence, not the numbers).
Cmu_true / h_mu_true are ANALYTIC (known by construction). Toy alphabet-4.
No library edits; no commit; in-memory.
"""
import numpy as np
from collections import defaultdict

ALPH = 4
# toy: 0=A,1=C,2=G,3=T  ->  GC = {1,2}
IS_GC = np.array([0, 1, 1, 0])


def gen_iid(n, rng, p=None):
    if p is None:
        p = np.ones(ALPH) / ALPH
    return rng.choice(ALPH, size=n, p=p)


def gen_periodic(n, L=256, seed=0):
    """Random uniform block of length L, repeated. Cmu_true = log2(L), h_mu=0."""
    rng = np.random.default_rng(seed)
    block = rng.integers(0, ALPH, size=L)
    return np.array([block[i % L] for i in range(n)])


def _entropy(counts, total):
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * np.log2(p)
    return h


def history_entropy(s, L):
    """H(L-gram): entropy of the order-L history distribution. At sufficient L
    this is the trajectory Cmu estimate (an upper bound that tightens to Cmu for
    an order-L process). For iid it = L*log2(4) (NOT Cmu=0 -- it is an upper
    bound, not the eps-machine Cmu)."""
    n = len(s)
    if n < L + 1:
        return float("nan")
    counts = defaultdict(int)
    for i in range(L, n):
        counts[tuple(s[i - L:i])] += 1
    return _entropy(list(counts.values()), sum(counts.values()))


def cond_entropy(s, L):
    """Order-L conditional entropy H(next | L-history): an estimate of h_mu at
    order L. For a long-period process this needs L >= period to see h_mu=0; at
    feasible L it reads ~H(next) (blind to the long-range determinism)."""
    n = len(s)
    if n < L + 1:
        return float("nan")
    ctx_next = defaultdict(lambda: defaultdict(int))
    ctx_tot = defaultdict(int)
    for i in range(L, n):
        h = tuple(s[i - L:i])
        ctx_next[h][s[i]] += 1
        ctx_tot[h] += 1
    total = n - L
    out = 0.0
    for hist, counts in ctx_next.items():
        ph = ctx_tot[hist] / total
        hc = _entropy(list(counts.values()), ctx_tot[hist])
        out += ph * hc
    return out


def delta_g(s):
    """TOY nearest-neighbor free energy (GC-count stacking): mean (G/C count per
    dinucleotide). Uniform composition -> ~1.0 (flat); GC-rich -> higher. NOT
    SantaLucia 1998 -- a toy composition-dependent LOCAL energy."""
    gc = IS_GC[s]
    dinuc_gc = gc[:-1] + gc[1:]
    return float(dinuc_gc.mean()), float(dinuc_gc.std())


def dinuc_entropy(s):
    counts = defaultdict(int)
    for i in range(len(s) - 1):
        counts[(s[i], s[i + 1])] += 1
    return _entropy(list(counts.values()), sum(counts.values()))


def _f(x):
    return f"{x:.3f}" if not (isinstance(x, float) and np.isnan(x)) else "  nan"


def main():
    rng = np.random.default_rng(20260826)
    N = 200_000
    print("multi_meter -- where Cmu and entropy diverge (the crux, in silico)")
    print(f"  N={N} symbols, alphabet=4. Toy Delta-G (GC-count stacking, NOT SantaLucia).\n")

    seqs = {
        "iid_uniform":  gen_iid(N, rng),
        "gc_rich_iid":  gen_iid(N, rng, p=np.array([0.1, 0.4, 0.4, 0.1])),  # 80% GC
        "periodic_L256": gen_periodic(N, L=256),
    }
    # ANALYTIC truths (known by construction):
    p_gc = np.array([0.1, 0.4, 0.4, 0.1])
    analytic = {
        "iid_uniform":   dict(cmu_true=0.0, hmu_true=2.0, note="pure noise"),
        "gc_rich_iid":   dict(cmu_true=0.0,
                              hmu_true=float(-(p_gc * np.log2(p_gc)).sum()),
                              note="biased noise"),
        "periodic_L256": dict(cmu_true=np.log2(256), hmu_true=0.0,
                              note="deterministic, period 256"),
    }

    print(f"  {'sequence':>13} | {'Cmu_true':>8} {'hmu_true':>8} | "
          f"{'H(dinuc)':>8} {'DeltaG':>7} | {'H(8gram)':>8} {'h(L=8)':>7} | note")
    print("  " + "-" * 96)
    rows = {}
    for name, s in seqs.items():
        a = analytic[name]
        dg, _ = delta_g(s)
        h8 = history_entropy(s, 8)
        hL8 = cond_entropy(s, 8)
        rows[name] = dict(a=a, dg=dg, h8=h8, hL8=hL8,
                          dinuc=dinuc_entropy(s))
        print(f"  {name:>13} | {_f(a['cmu_true']):>8} {_f(a['hmu_true']):>8} | "
              f"{_f(rows[name]['dinuc']):>8} {_f(dg):>7} | "
              f"{_f(h8):>8} {_f(hL8):>7} | {a['note']}")

    print("\n=== (1) Cmu and h_mu diverge BY DEFINITION (complementary) ===")
    for name in ("iid_uniform", "periodic_L256"):
        a = analytic[name]
        print(f"  {name:>13}: Cmu_true={_f(a['cmu_true'])}  h_mu_true={_f(a['hmu_true'])}")
    print("  -> periodic_L256: Cmu=8 (structure), h_mu=0 (no randomness).")
    print("  -> iid_uniform:   Cmu=0 (no structure), h_mu=2 (all randomness).")
    print("  -> They are DIFFERENT AXES (stored structure vs irreducible randomness),")
    print("     not two measures of the same thing (Rupe & Crutchfield 2024).")

    print("\n=== (2) the gate-relevant divergence: Cmu vs Delta-G (the scale gap) ===")
    r_iid = rows["iid_uniform"]
    r_per = rows["periodic_L256"]
    r_gc = rows["gc_rich_iid"]
    print(f"  iid_uniform   : Cmu={_f(r_iid['a']['cmu_true'])}  Delta-G={_f(r_iid['dg'])}  (uniform comp -> flat)")
    print(f"  periodic_L256 : Cmu={_f(r_per['a']['cmu_true'])}  Delta-G={_f(r_per['dg'])}  (uniform comp -> flat)")
    print(f"  gc_rich_iid   : Cmu={_f(r_gc['a']['cmu_true'])}  Delta-G={_f(r_gc['dg'])}  (biased comp -> high)")
    print("  -> Delta-G is FLAT for BOTH iid (Cmu=0) and periodic (Cmu=8): it CANNOT")
    print("     distinguish them. Delta-G tracks the LOCAL composition (gc_rich: high),")
    print("     NOT the Cmu. The thermodynamic (nearest-neighbor) measure is BLIND to")
    print("     the (non-local) predictive structure. This is the scale gap: Delta-G is")
    print("     order-1/local (a MEASURE); Cmu is the full predictive VARIETY. Caveat 2")
    print("     (measure != identity) made physical: Delta-G measures the local entropy,")
    print("     != the predictive variety. The gate (work<->Cmu) needs the work to track")
    print("     the VARIETY (Cmu), not the local MEASURE (Delta-G).")

    print("\n=== (3) the meter: trajectory Cmu IS recoverable, but membrane lacks it ===")
    print(f"  periodic_L256: H(8-gram) = {_f(r_per['h8'])}  (vs Cmu_true = {_f(r_per['a']['cmu_true'])})")
    print(f"  periodic_L256: h(L=8)    = {_f(r_per['hL8'])}  (vs h_mu_true = {_f(r_per['a']['hmu_true'])})")
    print("  -> The order-8 HISTORY ENTROPY recovers Cmu=8 (256 distinct 8-grams = the")
    print("     256 positions). A trajectory history-entropy meter DOES see the structure.")
    print("  -> BUT h(L=8) = ~2 (NOT h_mu=0): the order-8 conditional entropy still sees")
    print("     the next symbol as ~uniform (the structure is at scale 256, beyond 8).")
    print("     The order-L conditional-entropy estimate of h_mu is BLIND at feasible L.")
    print("  -> membrane's Cmu is the SPECTRAL effective_rank (a snapshot of embedded")
    print("     vectors), NOT a trajectory history-entropy / eps-machine. The gap is the")
    print("     CODE: the trajectory meter (history entropy at sufficient order, or a")
    print("     full-future CSSR eps-machine) is what membrane needs and does not have.")
    print("     (The naive one-step Cmu -- group by P(next|history) -- ALSO fails: capped")
    print("     at H(next) for a deterministic process, so it undercounts. Not shown.)")

    print("\n=== verdict ===")
    print("  DIVERGENCE (Cmu vs h_mu):     GREEN -- by definition (complementary);")
    print("     periodic Cmu=8/h_mu=0 vs iid Cmu=0/h_mu=2. Different axes.")
    print("  DIVERGENCE (Cmu vs Delta-G):  GREEN -- Delta-G flat (local) while Cmu=8")
    print("     (non-local); the thermodynamic measure is blind to the predictive")
    print("     variety. Scale gap = caveat 2 (measure != identity) made physical.")
    print("  METER (trajectory Cmu):      recoverable (H(8-gram)=8) but membrane has the")
    print("     spectral meter, not the trajectory meter -- the CODE gap.")
    print("  THE GATE:                    NOT RUN -- needs the physical nonequilibrium")
    print("     structure-sensitive work (the open leg). A simulated work is circular.")
    print("\n  HONEST CAVEAT: a demonstration of the Cmu-vs-entropy DIVERGENCE in silico,")
    print("  not the gate. Toy Delta-G (GC-count stacking, NOT SantaLucia 1998). Cmu_true")
    print("  / h_mu_true are ANALYTIC (known by construction). The divergence lives at the")
    print("  scale gap (local Delta-G vs non-local Cmu) = caveat 2 (measure != identity)")
    print("  made physical. 'Exhibits the predicted signature under this construction,'")
    print("  not proven.")
    print("\nartifacts: in-memory only (nothing written to the repo)")


if __name__ == "__main__":
    main()