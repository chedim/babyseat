"""#161: the rung ruler -- does each rung's target process have structure to learn?

Computational mechanics (Crutchfield): a process's excess entropy E is the
mutual information between its past and its future, I(past; future), in
bits; its statistical complexity C_mu is the entropy of its epsilon-machine's
causal states. A fair coin has E = C_mu = 0: nothing in its past helps.

This script measures E (and C_mu, by a small CSSR) on the time series each of
self's rungs 1-7 is tested on, read from a COPY of self's record (never the
live file), plus, optionally, the operator's touch sequence from
experiments/rung7_attention (branch exp/rung7-attention):

    D=<a git-ignored data dir or a scratch dir>
    cp <seat>/.membrane/self/record.jsonl $D/record.jsonl
    python experiments/rung_ruler/ruler.py $D/record.jsonl
    # optional: the operator's touches (needs serve-msgs.json + toybox/ copies in $D
    # and attention.py from exp/rung7-attention)
    python experiments/rung_ruler/ruler.py $D/record.jsonl --attention <path>/attention.py

Estimators (pinned by tests/test_rung_ruler_161.py):

- H(L): the entropy of the overlapping length-L blocks, in bits, with a
  bias correction: Grassberger (2003) by default, Miller-Madow or plug-in on
  request.
- h(L) = H(L) - H(L-1), h_mu = h(Lmax), E = sum_{L=1..Lmax} (h(L) - h_mu)
  = H(Lmax) - Lmax * h_mu. At Lmax = 2 this is I(X_t; X_t+1).
- Lmax: the largest L with K^L <= n / 10 (K the alphabet size): every block
  is seen ~10 times on average if they were all equally likely; capped at 10.
- CI: circular block bootstrap (block length max(4 Lmax, n^(1/3))), the
  2.5-97.5 percentiles re-centred on the estimate (block junctions bias the
  resampled E low, so the bootstrap gives the spread, not the centre).
- null: the same estimator on shuffled orders of the series (marginal kept,
  order destroyed); E - mean(E_null) is the structure in the ORDER.
- CSSR (Shalizi & Klinkner 2004), simple: histories grow from the empty one;
  a child history joins its parent's state if its next-symbol counts pass a
  chi-square homogeneity test at alpha, else the best-fitting other state, else
  a new state; then states are split until their transitions are
  deterministic. Its Lmax is shorter (K^L <= n / 50): with fewer samples a
  single false split at the longest length cascades. Occupancy of the length-Lmax histories in the data gives the
  causal-state distribution; C_mu is its entropy. States below 1% occupancy
  are reported as transient and left out.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

LN2 = math.log(2.0)
EULER = 0.5772156649015329


# ---------------------------------------------------------------- special functions

def digamma(x: float) -> float:
    """psi(x) for x > 0: recurrence up to x >= 6, then the asymptotic series."""
    r = 0.0
    while x < 6.0:
        r -= 1.0 / x
        x += 1.0
    f = 1.0 / (x * x)
    return r + math.log(x) - 0.5 / x - f * (1 / 12 - f * (1 / 120 - f * (1 / 252 - f * (1 / 240 - f / 132))))


def _gammq(a: float, x: float) -> float:
    """The regularized upper incomplete gamma Q(a, x) (Numerical Recipes)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1.0:                                    # series for P, then 1 - P
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1.0
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-15:
                break
        return max(0.0, 1.0 - s * math.exp(-x + a * math.log(x) - gln))
    b = x + 1.0 - a                                     # continued fraction for Q
    c, d = 1.0 / 1e-300, 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def chi2_sf(x: float, k: int) -> float:
    """P(chi2_k > x)."""
    return _gammq(k / 2.0, x / 2.0)


# ---------------------------------------------------------------- block entropies

def _codes(x, L: int, K: int) -> np.ndarray:
    a = np.asarray(x, dtype=np.int64)
    n = len(a) - L + 1
    if L <= 0 or n <= 0:
        return np.zeros(max(n, 0), dtype=np.int64)
    c = np.zeros(n, dtype=np.int64)
    for i in range(L):
        c = c * K + a[i:i + n]
    return c


def _alphabet(x) -> int:
    return int(max(x)) + 1 if len(x) else 1


def _grassberger_G(n: int) -> float:
    return digamma(n) + 0.5 * (-1) ** n * (digamma((n + 1) / 2.0) - digamma(n / 2.0))


def entropy_counts(counts, correction: str = "grassberger") -> float:
    """The entropy (bits) of a sample's symbol counts, bias-corrected."""
    counts = [int(c) for c in counts if c > 0]
    N = sum(counts)
    if N == 0:
        return 0.0
    if correction == "grassberger":
        return (math.log(N) - sum(c * _grassberger_G(c) for c in counts) / N) / LN2
    plug = -sum(c / N * math.log2(c / N) for c in counts)
    if correction == "mm":
        return plug + (len(counts) - 1) / (2.0 * N * LN2)
    if correction == "plugin":
        return plug
    raise ValueError(correction)


def block_entropy(x, L: int, correction: str = "grassberger", K: int | None = None) -> float:
    """H(L), bits, over the overlapping length-L blocks of x."""
    if L <= 0:
        return 0.0
    K = K or _alphabet(x)
    _, counts = np.unique(_codes(x, L, K), return_counts=True)
    return entropy_counts(counts, correction)


def choose_lmax(n: int, K: int, per_block: float = 10.0, cap: int = 10) -> int:
    L = 1
    while L < cap and K ** (L + 1) <= n / per_block:
        L += 1
    return L


def cssr_lmax(n: int, K: int) -> int:
    """CSSR's longest history: ~50 samples per history if all were equally
    likely (one false split at the longest length cascades through the
    determinization, so it needs far more than a block entropy)."""
    return choose_lmax(n, K, per_block=50.0)


def excess_entropy(x, lmax: int, correction: str = "grassberger", K: int | None = None) -> dict:
    """E = sum_{L=1..lmax} (h(L) - h_mu), h_mu = h(lmax). Also returns H(L), h(L)."""
    K = K or _alphabet(x)
    H = [0.0] + [block_entropy(x, L, correction, K) for L in range(1, lmax + 1)]
    h = [H[L] - H[L - 1] for L in range(1, lmax + 1)]
    h_mu = h[-1]
    return {"H": H, "h": h, "h_mu": h_mu, "E": sum(v - h_mu for v in h), "lmax": lmax}


def shuffle_null(x, lmax: int, reps: int = 200, seed: int = 0, correction: str = "grassberger") -> dict:
    rng = random.Random(seed)
    y, K = list(x), _alphabet(x)
    es = []
    for _ in range(reps):
        rng.shuffle(y)
        es.append(excess_entropy(y, lmax, correction, K)["E"])
    es.sort()
    return {"mean": float(np.mean(es)), "sd": float(np.std(es)), "q95": es[int(0.95 * (reps - 1))]}


def block_bootstrap_ci(x, lmax: int, reps: int = 500, seed: int = 0, block: int | None = None,
                       alpha: float = 0.05, correction: str = "grassberger") -> tuple[float, float]:
    """Circular block bootstrap CI of E. The junctions between resampled
    blocks cut the very dependence E measures, so the bootstrap distribution
    sits LOW (golden mean, n=4000, Lmax 4: ~0.04 bit); the bootstrap is used
    for the spread only: CI = E + (percentiles - bootstrap median)."""
    n, K = len(x), _alphabet(x)
    b = block or max(4 * lmax, int(math.ceil(n ** (1 / 3))))
    b = min(b, n)
    rng = np.random.default_rng(seed)
    a = np.asarray(x, dtype=np.int64)
    ext = np.concatenate([a, a[:b]])
    nb = int(math.ceil(n / b))
    es = []
    for _ in range(reps):
        starts = rng.integers(0, n, nb)
        y = np.concatenate([ext[s:s + b] for s in starts])[:n]
        es.append(excess_entropy(y, lmax, correction, K)["E"])
    est = excess_entropy(a, lmax, correction, K)["E"]
    med = float(np.median(es))
    return est + float(np.quantile(es, alpha / 2)) - med, est + float(np.quantile(es, 1 - alpha / 2)) - med


# ---------------------------------------------------------------- CSSR

def _next_counts(x, lmax: int, K: int) -> dict:
    """history (oldest..newest, length 0..lmax) -> next-symbol counts."""
    out: dict = defaultdict(lambda: [0] * K)
    n = len(x)
    for t in range(n):
        for L in range(0, min(lmax, t) + 1):
            out[tuple(x[t - L:t])][x[t]] += 1
    return out


def _same(a, b, alpha: float) -> float:
    """p-value of a chi-square homogeneity test between two count vectors."""
    cols = [i for i in range(len(a)) if a[i] + b[i] > 0]
    if len(cols) <= 1:
        return 1.0
    na, nb = sum(a), sum(b)
    tot = na + nb
    stat = 0.0
    for i in cols:
        ci = a[i] + b[i]
        for row, nr in ((a, na), (b, nb)):
            e = nr * ci / tot
            if e > 0:
                stat += (row[i] - e) ** 2 / e
    return chi2_sf(stat, len(cols) - 1)


def cssr(x, lmax: int, alpha: float = 0.001, min_count: int = 5, K: int | None = None,
         transient: float = 0.01) -> dict:
    x = [int(v) for v in x]
    K = K or _alphabet(x)
    nc = _next_counts(x, lmax, K)
    states: list[list[tuple]] = [[()]]
    of: dict[tuple, int] = {(): 0}

    def dist(s):
        tot = [0] * K
        for h in states[s]:
            for i, v in enumerate(nc[h]):
                tot[i] += v
        return tot

    # sufficiency: grow histories one symbol into the past
    for L in range(0, lmax):
        for s in range(len(states)):
            for h in [h for h in states[s] if len(h) == L]:
                parent = of[h]
                for a in range(K):
                    child = (a,) + h
                    if child not in nc or sum(nc[child]) < min_count:
                        continue
                    c = nc[child]
                    if _same(c, dist(parent), alpha) > alpha:
                        dest = parent
                    else:
                        best, bp = None, alpha
                        for t in range(len(states)):
                            if t == parent:
                                continue
                            p = _same(c, dist(t), alpha)
                            if p > bp:
                                best, bp = t, p
                        if best is None:
                            states.append([])
                            best = len(states) - 1
                        dest = best
                    states[dest].append(child)
                    of[child] = dest

    def succ(h, a):
        g = h + (a,)
        for L in range(min(len(g), lmax), -1, -1):
            k = g[len(g) - L:]
            if k in of:
                return of[k]
        return None

    # recursion: split until every state's transitions are deterministic
    changed = True
    while changed:
        changed = False
        for s in range(len(states)):
            for a in range(K):
                groups: dict = {}
                for h in states[s]:
                    if nc[h][a] == 0:
                        continue
                    groups.setdefault(succ(h, a), []).append(h)
                if len(groups) > 1:
                    keys = sorted(groups, key=lambda k: -sum(sum(nc[h]) for h in groups[k]))
                    for k in keys[1:]:
                        states.append(groups[k])
                        for h in groups[k]:
                            states[s].remove(h)
                            of[h] = len(states) - 1
                    changed = True
                    break
            if changed:
                break

    occ = Counter()
    for t in range(lmax, len(x) + 1):
        h = tuple(x[t - lmax:t])
        for L in range(lmax, -1, -1):
            k = h[len(h) - L:]
            if k in of:
                occ[of[k]] += 1
                break
    tot = sum(occ.values())
    probs = [c / tot for c in occ.values() if tot and c / tot >= transient]
    z = sum(probs)
    probs = [p / z for p in probs] if z else []
    return {"states": len(probs), "C_mu": -sum(p * math.log2(p) for p in probs if p > 0), "lmax": lmax}


# ---------------------------------------------------------------- the Brier ceiling

def prequential_brier(x, k: int, start: int | None = None) -> float:
    """Mean Brier error of an order-k Jeffreys predictor of a binary series,
    each symbol predicted from the ones before it only (scored from `start`,
    default k). Order 0 is the running rate; the gap between order 0 and the
    best order is what the ORDER of the series can buy, in the units the
    rung tests use."""
    start = k if start is None else max(start, k)
    cnt: dict = defaultdict(lambda: [0, 0])
    err, m = 0.0, 0
    for t in range(len(x)):
        if t >= k:
            c = cnt[tuple(x[t - k:t])]
            if t >= start:
                p = (c[1] + 0.5) / (c[0] + c[1] + 1.0)
                err += (x[t] - p) ** 2
                m += 1
            c[x[t]] += 1
    return err / m if m else float("nan")


def order_ceiling(x, kmax: int) -> dict:
    """Brier of the running rate vs the best order 1..kmax, all scored from
    t = kmax; skill = 1 - B_order / B_rate (in-sample choice of k: optimistic)."""
    b0 = prequential_brier(x, 0, start=kmax)
    best = min(((prequential_brier(x, k, start=kmax), k) for k in range(1, kmax + 1)), default=(b0, 0))
    return {"B_rate": b0, "B_order": best[0], "k": best[1], "skill": 1.0 - best[0] / b0 if b0 > 0 else 0.0}


# ---------------------------------------------------------------- symbolization

def binary_answers(answers) -> list[int]:
    """changed -> 1, unchanged -> 0, no answer dropped (a look that saw nothing)."""
    return [1 if a == "changed" else 0 for a in answers if a in ("changed", "unchanged")]


def ternary_answers(answers) -> list[int]:
    return [{"changed": 1, "unchanged": 0}.get(a, 2) for a in answers]


def median_split(v) -> list[int]:
    m = float(np.median(v))
    return [1 if x > m else 0 for x in v]


def tercile(v) -> list[int]:
    q1, q2 = np.quantile(v, [1 / 3, 2 / 3])
    return [0 if x <= q1 else (1 if x <= q2 else 2) for x in v]


# ---------------------------------------------------------------- the series

def _reaches(recs, eid=None, ordinary=True):
    return [r for r in recs if r.get("kind") == "reach" and (eid is None or r.get("eid") == eid)
            and not (ordinary and (r.get("audit") or r.get("audit5")))]


def series(recs: list[dict]) -> list[dict]:
    """The time series each rung's test predicts, oldest first."""
    rungs = {r["rung"]: r for r in recs if r.get("kind") == "rung"}
    out = []
    push = [float(r["push"]) for r in recs if r.get("kind") == "cycle" and r.get("change") == "world"]
    out.append({"rung": "1", "what": "the current's push, per cycle (> median)", "x": median_split(push)})
    out.append({"rung": "1t", "what": "the current's push, per cycle (terciles)", "x": tercile(push)})
    e2 = (rungs.get(2) or {}).get("eid", "membrane::Component:field:vitals")
    out.append({"rung": "2", "what": f"looks at {e2.split(':', 3)[-1]}: changed?",
                "x": binary_answers(r["answer"] for r in _reaches(recs, e2))})
    out.append({"rung": "3", "what": "every reach: changed / unchanged / no answer",
                "x": ternary_answers(r["answer"] for r in _reaches(recs))})
    out.append({"rung": "4", "what": "timed looks: found news?",
                "x": binary_answers(r["answer"] for r in _reaches(recs) if r.get("timed"))})
    sh = Counter(r["eid"] for r in recs if r.get("kind") == "shift" and r.get("eid") != e2)
    e5 = sh.most_common(1)[0][0] if sh else e2
    out.append({"rung": "5", "what": f"looks at {e5.split(':', 3)[-1]} (most shifts after rung 2's): changed?",
                "x": binary_answers(r["answer"] for r in _reaches(recs, e5))})
    e6 = (rungs.get(6) or {}).get("eid")
    if e6:
        out.append({"rung": "6", "what": f"looks at {e6.split(':', 3)[-1]} (the other rung 6 passed on)",
                    "x": binary_answers(r["answer"] for r in _reaches(recs, e6))})
    to = next((r.get("to") for r in reversed(recs) if r.get("kind") == "push"), None)
    if to and to != e6:
        out.append({"rung": "6'", "what": f"looks at {to.split(':', 3)[-1]} (the current other)",
                    "x": binary_answers(r["answer"] for r in _reaches(recs, to))})
    rc = [r for r in _reaches(recs, to) if r.get("reply")]
    x7 = binary_answers(r["answer"] for r in rc)
    out.append({"rung": "7", "what": f"reply checks at {str(to).split(':', 3)[-1]}: answered by 2 min?", "x": x7})
    if len(x7) > 77:
        out.append({"rung": "7 (first 77)", "what": "the same, the 77 checks of experiments/rung7_recent",
                    "x": x7[:77]})
    for s in out:
        n = int(str(s["rung"])[0])
        s["passed"] = n in rungs
    return out


def touch_series(attention_py: str, data_dir: str) -> list[dict]:
    """The operator's chat turns (+ toy pokes), from exp/rung7-attention's
    loader: did the turn touch an entity (1) or not (0)?"""
    spec = importlib.util.spec_from_file_location("attention", attention_py)
    A = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(A)
    turns, _, _ = A.build(data_dir)
    x = [1 if t["ents"] else 0 for t in turns]
    return [{"rung": "touch", "what": "operator turns: touched an entity?", "x": x, "passed": None}]


# ---------------------------------------------------------------- the report

def analyse(x, reps: int = 300, seed: int = 0, lmax: int | None = None) -> dict:
    K = _alphabet(x)
    n = len(x)
    lmax = lmax or choose_lmax(n, K)
    est = excess_entropy(x, lmax)
    null = shuffle_null(x, lmax, reps=reps, seed=seed)
    lo, hi = block_bootstrap_ci(x, lmax, reps=reps, seed=seed)
    mi1 = excess_entropy(x, 2)["E"] if n >= 8 else float("nan")
    mi1_null = shuffle_null(x, 2, reps=reps, seed=seed) if n >= 8 else {"q95": float("nan")}
    m = cssr(x, cssr_lmax(n, K), K=K)
    return {"n": n, "K": K, "lmax": lmax, "H1": est["H"][1], "h_mu": est["h_mu"], "E": est["E"],
            "ci": (lo, hi), "E_null": null["mean"], "E_null_q95": null["q95"], "dE": est["E"] - null["mean"],
            "mi1": mi1, "mi1_q95": mi1_null["q95"], "states": m["states"], "C_mu": m["C_mu"],
            "rate": (sum(x) / n) if K == 2 else None,
            "ceiling": order_ceiling(x, max(1, cssr_lmax(n, K))) if K == 2 else None}


def power(x, n_win: int, wins: int = 200, reps: int = 100, seed: int = 0) -> dict:
    """Would a window of n_win symbols of this series show its structure?
    Share of random contiguous windows whose E clears its own shuffle null's
    95th percentile, at the Lmax n_win allows."""
    if len(x) < 2 * n_win:
        return {}
    rng = random.Random(seed)
    K = _alphabet(x)
    lmax = choose_lmax(n_win, K)
    hits, es = 0, []
    for w in range(wins):
        s = rng.randrange(0, len(x) - n_win)
        y = x[s:s + n_win]
        e = excess_entropy(y, lmax, K=K)["E"]
        q = shuffle_null(y, lmax, reps=reps, seed=w)["q95"]
        hits += e > q
        es.append(e)
    return {"share": hits / wins, "E_med": float(np.median(es)), "lmax": lmax}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("record", help="a COPY of <seat>/.membrane/self/record.jsonl")
    ap.add_argument("--attention", help="attention.py from exp/rung7-attention (optional)")
    ap.add_argument("--reps", type=int, default=300)
    a = ap.parse_args(argv)
    recs = [json.loads(ln) for ln in open(a.record) if ln.strip()]
    ss = series(recs)
    if a.attention:
        ss += touch_series(a.attention, str(Path(a.record).parent))
    n7 = len(next(s["x"] for s in ss if s["rung"] == "7"))
    print("| rung | series | passed | n | K | rate | Lmax | H(1) | h_mu | E [95% CI] | E_null (q95) | E - E_null "
          "| I(x_t; x_t+1) (null q95) | causal states | C_mu | windows of n7 that clear the null "
          "| Brier: rate -> best order k (order skill) |")
    print("|" + "---|" * 17)
    for s in ss:
        r = analyse(s["x"], reps=a.reps)
        pw = power(s["x"], n7)
        rate = f"{r['rate']:.2f}" if r["rate"] is not None else "-"
        pas = {True: "yes", False: "no", None: "-"}[s["passed"]]
        pws = f"{pw['share']:.0%} (E med {pw['E_med']:.3f}, Lmax {pw['lmax']})" if pw else "-"
        c = r["ceiling"]
        cs = f"{c['B_rate']:.3f} -> {c['B_order']:.3f} (k={c['k']}, {c['skill']:+.3f})" if c else "-"
        print(f"| {s['rung']} | {s['what']} | {pas} | {r['n']} | {r['K']} | {rate} | {r['lmax']} | {r['H1']:.3f} "
              f"| {r['h_mu']:.3f} | {r['E']:.3f} [{r['ci'][0]:.3f}, {r['ci'][1]:.3f}] "
              f"| {r['E_null']:.3f} ({r['E_null_q95']:.3f}) | {r['dE']:+.3f} "
              f"| {r['mi1']:.3f} ({r['mi1_q95']:.3f}) | {r['states']} | {r['C_mu']:.3f} | {pws} | {cs} |", flush=True)
        if s["rung"].startswith("7") or s["rung"] == "touch":
            print("    ", "".join(map(str, s["x"])))


if __name__ == "__main__":
    main()
