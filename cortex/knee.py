"""Knee-finders for the novelty filter: where does the absorb/escalate cutoff live?

The filter absorbs the routine mass (low novelty) and escalates everything above.
So the cutoff lives in the FIRST VALLEY of the novelty distribution (min density =
max uncertainty = where 'not sure' lives), not the biggest gap. On a real (trimodal)
workload, two-class Otsu grabs the biggest gap and gets the wrong boundary; the
first-valley finder gets the right one. Both are exposed here for the contrast.
"""

from __future__ import annotations

import numpy as np


def otsu_threshold(values: np.ndarray, n_bins: int = 64) -> float:
    """Max between-class variance. Assumes BIMODAL -- picks the BIGGEST gap.

    On trimodal data this lands in the WRONG valley (absorbs the borderline band).
    Kept as the trap to contrast against ``valley_knee``.
    """
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

    Finds the first local maximum (the routine peak), then the first local minimum
    after it (the valley between 'clearly routine' and 'everything above'). Works
    on bimodal AND trimodal: the second valley (borderline/clear-novel) is
    irrelevant -- both sides escalate.

    KNOWN WEAKNESS: ``peaks[0]`` is the first local max, full stop -- so a tiny
    wiggle in the LEFT TAIL (a few very-low-novelty routine points) registers as
    a spurious first peak, and the "valley after it" is an edge artifact BELOW the
    real routine mass. On the real mxbai novelty distribution this landed at 4.46
    (below the noise median 5.23) and escalated ~all the noise (93% FP). See
    ``valley_knee_robust`` for the fix.
    """
    hist, edges = np.histogram(values, bins=n_bins)
    if smooth > 1:
        hist = np.convolve(hist, np.ones(smooth) / smooth, mode="same")
    centers = (edges[:-1] + edges[1:]) / 2
    peaks = [i for i in range(1, len(hist) - 1) if hist[i] >= hist[i - 1] and hist[i] >= hist[i + 1]]
    mins = [i for i in range(1, len(hist) - 1) if hist[i] <= hist[i - 1] and hist[i] <= hist[i + 1]]
    if not peaks:
        return float(centers[int(np.argmax(hist))])
    p0 = peaks[0]
    later_mins = [m for m in mins if m > p0]
    if not later_mins:
        return float(centers[-1])
    return float(centers[later_mins[0]])


def valley_knee_robust(values: np.ndarray, n_bins: int = 60, smooth: int = 5,
                       prominence: float = 0.3) -> float:
    """First valley after the first *significant* peak -- immune to left-tail wiggles.

    Same idea as ``valley_knee`` (first valley = absorb/escalate boundary) but the
    routine peak must be a real mass, not a tail wiggle: only peaks at least
    ``prominence`` * (tallest peak) count. The left-tail wiggle that fooled
    ``valley_knee`` is below that bar, so the first *significant* peak is the
    actual routine mass, and the valley after it is the real cutoff. Unsupervised
    (no labels); ``prominence`` is the one knob.
    """
    hist, edges = np.histogram(values, bins=n_bins)
    if smooth > 1:
        hist = np.convolve(hist, np.ones(smooth) / smooth, mode="same")
    centers = (edges[:-1] + edges[1:]) / 2
    gmax = hist.max()
    peaks = [i for i in range(1, len(hist) - 1)
             if hist[i] >= hist[i - 1] and hist[i] >= hist[i + 1] and hist[i] >= prominence * gmax]
    if not peaks:                                   # fall back to any peak if none qualify
        peaks = [i for i in range(1, len(hist) - 1) if hist[i] >= hist[i - 1] and hist[i] >= hist[i + 1]]
    if not peaks:
        return float(centers[int(np.argmax(hist))])
    p0 = peaks[0]
    later_mins = [i for i in range(1, len(hist) - 1)
                 if hist[i] <= hist[i - 1] and hist[i] <= hist[i + 1] and i > p0]
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
        lines.append(f"{c:8.2f} |{bar:<{width}} {h:4d}{''.join(marks)}")
    return "\n".join(lines)


def confusion(values, labels, cutoff):
    """Filter confusion. predicted escalate = novelty > cutoff.

    absorb_signal (FN) is the SAFETY error (signal routed as noise, no LLM net).
    pass_noise  (FP) is the EFFICIENCY error (noise escalated, wasted tokens).
    """
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