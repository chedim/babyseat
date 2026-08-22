"""Filter-knee experiment: prove Cortex's novelty filter has a real cutoff to find,
not a threshold we impose -- and that the right finder matters on a real workload.

Setup: a field that has experienced a k-dimensional manifold (what Cortex "can
solve"). The incoming workload is TRIMODAL -- the realistic shape:

    clear-noise  : on-manifold (the field can solve these)            -> low novelty  (absorb)
    borderline   : on-manifold + a moderate off-manifold push           -> mid novelty  (escalate)
    clear-novel   : uniform on the full sphere                         -> high novelty (escalate)

borderline + clear-novel are both SIGNAL (should escalate). The filter's job is
ONE cutoff: absorb the routine mass, escalate everything above. So the cutoff
lives in the FIRST valley -- between the routine peak and the novel mass -- not
the biggest gap in the whole distribution.

The trap: two-class Otsu (max between-class variance) assumes bimodal and grabs
the BIGGEST gap. On trimodal data it lands in the borderline/clear-novel valley
and absorbs the entire borderline band -- the wrong boundary. The right finder
is the FIRST VALLEY after the routine peak (min density = max uncertainty = where
"not sure" lives). We show both, validate against ground-truth labels, and show
the two filter errors:
  - absorb signal  (FN): signal routed as noise -> wrong answer, no LLM net (SAFETY)
  - pass noise     (FP): noise escalated -> wasted tokens (EFFICIENCY)
and the risk-dependent shift: the valley is the conservative anchor; risky actions
hold/tighten it (escalate anything not clearly routine); safe/idempotent actions
can LOOSEN it into the borderline band (absorb more, save tokens, accept that some
borderline signal gets absorbed -- cheap to undo). The reversibility guard as a
risk-scaled filter.

Run:  PYTHONPATH=. python experiments/filter_knee.py   (numpy only)
"""

from __future__ import annotations

import numpy as np

from cortex.field import Exemplar, ExemplarStore
from cortex.variety import novelty

D = 16
K = 4
N_FIELD = 80
N_WORK = 600
SIGNAL_FRAC = 0.4
NOISE = 0.05
rng = np.random.default_rng(7)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-12)


def _rand_orthonormal(d: int, k: int) -> np.ndarray:
    A = rng.standard_normal((d, k))
    Q, _ = np.linalg.qr(A)
    return Q


def _rand_null_dir(basis: np.ndarray) -> np.ndarray:
    v = rng.standard_normal(D)
    v -= basis @ (basis.T @ v)
    return _unit(v)


def make_field() -> tuple[ExemplarStore, np.ndarray, np.ndarray]:
    mu = rng.standard_normal(D) * 3.0
    basis = _rand_orthonormal(D, K)
    store = ExemplarStore()
    for _ in range(N_FIELD):
        coeffs = rng.standard_normal(K)
        v = _unit(mu + basis @ coeffs + NOISE * rng.standard_normal(D))
        store.append(Exemplar("field situation", "actA", "ok", v.tolist(), safe=True))
    return store, mu, basis


def make_workload(mu: np.ndarray, basis: np.ndarray,
                  c_border_lo: float = 3.0, c_border_hi: float = 4.5) -> list[tuple[str, np.ndarray]]:
    """Trimodal: clear-noise | borderline | clear-novel. borderline+novel are SIGNAL."""
    work = []
    n_signal = int(SIGNAL_FRAC * N_WORK)
    n_noise = N_WORK - n_signal
    n_border = n_signal // 2
    n_strong = n_signal - n_border

    for _ in range(n_noise):                                   # clear-noise: on-manifold
        coeffs = rng.standard_normal(K)
        v = _unit(mu + basis @ coeffs + NOISE * rng.standard_normal(D))
        work.append(("noise", v))
    for _ in range(n_border):                                   # borderline: on-manifold + mid push
        coeffs = rng.standard_normal(K)
        perp = _rand_null_dir(basis)
        c = rng.uniform(c_border_lo, c_border_hi)
        v = _unit(mu + basis @ coeffs + c * perp)
        work.append(("signal", v))
    for _ in range(n_strong):                                   # clear-novel: uniform sphere
        v = _unit(rng.standard_normal(D))
        work.append(("signal", v))
    rng.shuffle(work)
    return work


def otsu_threshold(values: np.ndarray, n_bins: int = 64) -> float:
    """Max between-class variance. Assumes BIMODAL -- picks the biggest gap.
    On trimodal data this lands in the WRONG valley (shown here as the trap)."""
    hist, edges = np.histogram(values, bins=n_bins)
    p = hist / max(hist.sum(), 1)
    centers = (edges[:-1] + edges[1:]) / 2
    omega = np.cumsum(p)
    mu_c = np.cumsum(p * centers)
    global_mu = mu_c[-1]
    w0, w1 = omega, 1.0 - omega
    mu0 = mu_c / (w0 + 1e-12)
    mu1 = (global_mu - mu_c) / (w1 + 1e-12)
    sigma_b = w0 * w1 * (mu0 - mu1) ** 2
    idx = int(np.argmax(sigma_b[:-1]))
    return float(centers[idx])


def valley_knee(values: np.ndarray, n_bins: int = 60, smooth: int = 3) -> float:
    """The FIRST VALLEY after the routine peak -- the absorb/escalate boundary.

    Min density = max uncertainty = where 'not sure' lives. Works on bimodal AND
    trimodal: it finds the routine (first) peak, then the first minimum after it,
    i.e. the boundary between 'clearly routine' and 'everything above'. The second
    valley (borderline/clear-novel) is irrelevant -- both sides escalate.
    """
    hist, edges = np.histogram(values, bins=n_bins)
    if smooth > 1:
        hist = np.convolve(hist, np.ones(smooth) / smooth, mode="same")
    centers = (edges[:-1] + edges[1:]) / 2
    # first local maximum = the routine peak
    peaks = [i for i in range(1, len(hist) - 1) if hist[i] >= hist[i - 1] and hist[i] >= hist[i + 1]]
    mins = [i for i in range(1, len(hist) - 1) if hist[i] <= hist[i - 1] and hist[i] <= hist[i + 1]]
    if not peaks:
        return float(centers[int(np.argmax(hist))])
    p0 = peaks[0]
    later_mins = [m for m in mins if m > p0]
    if not later_mins:
        return float(centers[-1])
    return float(centers[later_mins[0]])


def ascii_hist(values: np.ndarray, knees: dict, width: int = 38, n_bins: int = 30) -> str:
    hist, edges = np.histogram(values, bins=n_bins)
    centers = (edges[:-1] + edges[1:]) / 2
    span = edges[1] - edges[0]
    peak = hist.max()
    lines = []
    for c, h in zip(centers, hist):
        bar = "#" * int(round(width * h / peak)) if peak > 0 else ""
        marks = [f"  <-- {name}" for name, k in knees.items() if abs(c - k) <= span / 2]
        lines.append(f"{c:7.2f} |{bar:<{width}} {h:4d}{''.join(marks)}")
    return "\n".join(lines)


def confusion(values, labels, cutoff):
    tp = fn = fp = tn = 0
    n_sig = sum(1 for l in labels if l == "signal")
    n_noi = sum(1 for l in labels if l == "noise")
    for nov, lab in zip(values, labels):
        escalate = nov > cutoff
        if lab == "signal" and escalate: tp += 1
        elif lab == "signal": fn += 1
        elif lab == "noise" and escalate: fp += 1
        else: tn += 1
    return {
        "cutoff": cutoff,
        "absorb_signal (FN/safety)": f"{fn}/{n_sig} = {fn/max(n_sig,1):.0%}",
        "pass_noise (FP/efficiency)": f"{fp}/{n_noi} = {fp/max(n_noi,1):.0%}",
        "absorbed (TN)": tn, "escalated (TP)": tp,
    }


def main() -> None:
    store, mu, basis = make_field()
    work = make_workload(mu, basis)
    labels = [lab for lab, _ in work]
    novs = np.array([novelty(v, store) for _, v in work])

    otsu = otsu_threshold(novs)
    knee = valley_knee(novs)

    print("== novelty over the trimodal workload (clear-noise | borderline | clear-novel) ==\n")
    print("  per-class novelty (min / median / max):")
    for lab in ("noise", "signal"):
        a = novs[[i for i, l in enumerate(labels) if l == lab]]
        print(f"    {lab:7s} {a.min():6.2f} / {np.median(a):6.2f} / {a.max():6.2f}")
    print()
    print(ascii_hist(novs, {"Otsu (BIGGEST gap -- trap)": otsu, "valley (FIRST -- correct)": knee}))
    print(f"\n  Otsu  knee = {otsu:.2f}  (biggest gap -> wrong on trimodal: absorbs the borderline band)")
    print(f"  valley knee = {knee:.2f}  (first valley -> absorb routine, escalate the rest)\n")

    print("== the trap: Otsu at the biggest gap ==")
    c = confusion(novs, labels, otsu)
    for k, v in c.items(): print(f"  {k:30s} {v}")
    print("  (Otsu absorbs the borderline band -> half the signal routed as routine.)\n")

    print("== the fix: cutoff at the first valley (the conservative anchor) ==")
    c = confusion(novs, labels, knee)
    for k, v in c.items(): print(f"  {k:30s} {v}")
    print("  (absorb clear-routine, escalate borderline+novel. Low safety error.)\n")

    print("== risk-scaled filter: the reversibility guard ==")
    print("  risky  -> hold/tighten the valley: escalate anything not clearly routine.")
    print("  safe   -> LOOSEN into the borderline band: absorb more, save tokens, accept")
    print("           some borderline signal absorbed (cheap to undo).")
    for factor, tag in [(1.0, "risky  (valley, tight)"), (1.6, "safe   (loosen)")]:
        c = confusion(novs, labels, knee * factor)
        print(f"  cutoff={c['cutoff']:.2f} [{tag}]  "
              f"absorb_signal(FN/safety)={c['absorb_signal (FN/safety)']:14s}  "
              f"pass_noise(FP/efficiency)={c['pass_noise (FP/efficiency)']}")
    print("  (loosening trades safety for efficiency -- only for cheap-to-undo actions.)")


if __name__ == "__main__":
    main()