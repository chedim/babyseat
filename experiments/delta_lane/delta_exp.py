"""Do embedding DELTAS carry the kind of change, on their own, across sensors?

Rung 0 (instrument): the scorer must recover a planted answer and read chance on noise.
Rung 1 (the question): sensor readings rendered as TEXT (the seat's style), embedded by
mxbai; leave-one-sensor-out: can a delta from sensors A..J name the change on sensor K
(never seen) better than the states can?

Features compared (all leave-one-sensor-out, cosine):
  delta    e(after) - e(before)
  after    e(after) alone                  (state baseline)
  pair     [e(before), e(after)]           (holds everything delta holds; content-dominated)
  vsa      sign(R @ delta), Hamming        (what vsa.py would actually store)
Classifiers: 1-NN (recall-shaped) and class centroid (= the VSA bundle of a change kind).
Null: the same scorer with labels permuted within sensor (200 draws) -> chance band.
"""
import json
import random
import sys
import urllib.request

import numpy as np

OLLAMA = "http://localhost:11434/api/embed"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "mxbai-embed-large"
rng = random.Random(7)
nrng = np.random.default_rng(7)


# ---------------------------------------------------------------- the sensors
# each: name, (lo, hi), step scale, alarm threshold (value > thr => ALERT), renderers
STABLE = False   # True: one phrasing per sensor (a real sensor renders the same way every time)


def _r(fmts):
    return lambda v, ts, alarm: (fmts[0] if STABLE else rng.choice(fmts)).format(
        v=v, ts=ts, a=" ALERT" if alarm else "")

SENSORS = [
    ("cabin_temp", (12, 34), 1.2, 30, _r(["cabin temp {v:.1f}C{a} @{ts}", "temp/cabin: {v:.1f} °C{a} ({ts})", "salon thermometer reads {v:.1f}C{a}"])),
    ("house_bank", (11.6, 13.4), 0.12, 13.2, _r(["house bank {v:.2f}V{a} @{ts}", "batt/house: {v:.2f} volts{a} ({ts})", "battery bank at {v:.2f}V{a}"])),
    ("depth", (1.5, 30), 1.5, 25, _r(["depth {v:.1f}m under keel{a} @{ts}", "sounder: {v:.1f} m{a} ({ts})", "water depth {v:.1f} meters{a}"])),
    ("wind", (0, 40), 2.5, 30, _r(["wind {v:.0f}kn{a} @{ts}", "anemometer: {v:.0f} knots{a} ({ts})", "true wind speed {v:.0f} kt{a}"])),
    ("memguard", (300, 6000), 350, 5000, _r(["memguard: pid 88782 {v:.0f}MB{a} cpu 0.1", "rss/seat: {v:.0f} MB{a} ({ts})", "seat resident memory {v:.0f}MB{a}"])),
    ("disk", (40, 99), 3, 92, _r(["sysmon disk {v:.0f}%{a} @{ts}", "disk/root: {v:.0f}% used{a} ({ts})", "root volume {v:.0f} percent full{a}"])),
    ("bilge", (0, 30), 2, 20, _r(["bilge level {v:.0f}cm{a} @{ts}", "bilge/main: {v:.0f} cm{a} ({ts})", "bilge water at {v:.0f}cm{a}"])),
    ("cpu", (0.05, 8), 0.6, 6, _r(["load avg {v:.2f}{a} @{ts}", "cpu/load1: {v:.2f}{a} ({ts})", "system load {v:.2f}{a}"])),
    ("fridge", (-2, 12), 0.9, 8, _r(["fridge {v:.1f}C{a} @{ts}", "temp/fridge: {v:.1f} °C{a} ({ts})", "fridge compartment {v:.1f}C{a}"])),
    ("water_tank", (0, 100), 6, 95, _r(["fresh water tank {v:.0f}%{a} @{ts}", "tank/fresh: {v:.0f}%{a} ({ts})", "freshwater {v:.0f} percent{a}"])),
    ("humidity", (20, 99), 4, 90, _r(["humidity {v:.0f}%RH{a} @{ts}", "hygro/cabin: {v:.0f} %RH{a} ({ts})", "relative humidity {v:.0f}%{a}"])),
    ("solar", (0, 400), 30, 350, _r(["solar input {v:.0f}W{a} @{ts}", "pv/array: {v:.0f} watts{a} ({ts})", "solar panels producing {v:.0f}W{a}"])),
]
STEPS = 110


def simulate():
    """Per sensor: a walk with labeled moves. dir label: up/down/flat (relative to the
    sensor's own step scale); alarm label: on/off/none (threshold crossing)."""
    rows = []
    for si, (name, (lo, hi), scale, thr, render) in enumerate(SENSORS):
        v = rng.uniform(lo + (hi - lo) * 0.3, lo + (hi - lo) * 0.6)
        h, m = rng.randint(0, 23), rng.randint(0, 59)
        prev = None
        for t in range(STEPS):
            kind = rng.choice(["up", "down", "flat"])
            if prev is not None:
                if kind == "up":
                    v = v + scale * rng.uniform(1.5, 3.0)
                elif kind == "down":
                    v = v - scale * rng.uniform(1.5, 3.0)
                else:
                    v = v + scale * rng.uniform(-0.15, 0.15)
                if v > hi or v < lo:          # bounce: relabel honestly from the move
                    v = min(max(v, lo), hi)
            m = (m + rng.randint(1, 7)) % 60
            h = (h + (1 if m < 7 else 0)) % 24
            ts = f"{h:02d}:{m:02d}"
            alarm = v > thr
            rows.append({"s": si, "t": t, "v": v, "alarm": alarm, "text": render(v, ts, alarm)})
            prev = v
    return rows


def pairs_of(rows):
    out = []
    by = {}
    for r in rows:
        by.setdefault(r["s"], []).append(r)
    for s, rs in by.items():
        scale = SENSORS[s][2]
        for a, b in zip(rs, rs[1:]):
            dv = (b["v"] - a["v"]) / scale
            d = "up" if dv > 0.75 else ("down" if dv < -0.75 else "flat")
            al = "on" if (b["alarm"] and not a["alarm"]) else ("off" if (a["alarm"] and not b["alarm"]) else "none")
            out.append({"s": s, "a": a, "b": b, "dir": d, "alarm": al})
    return out


# ---------------------------------------------------------------- embedding
def embed(texts, batch=64):
    out = []
    for i in range(0, len(texts), batch):
        body = json.dumps({"model": MODEL, "input": texts[i:i + batch]}).encode()
        req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            out.extend(json.load(r)["embeddings"])
    E = np.asarray(out, dtype=np.float32)
    return E / np.linalg.norm(E, axis=1, keepdims=True)


# ---------------------------------------------------------------- the scorer
def _unit(X):
    n = np.linalg.norm(X, axis=1, keepdims=True)
    return X / np.where(n < 1e-9, 1.0, n)


def score(X, y, groups, hamming=False):
    """Leave-one-group-out. Returns (1-NN balanced acc, centroid balanced acc)."""
    y = np.asarray(y)
    labels = sorted(set(y))
    if not hamming:
        X = _unit(X)
    nn_pred = np.empty(len(y), dtype=object)
    ce_pred = np.empty(len(y), dtype=object)
    for g in sorted(set(groups)):
        te = groups == g
        tr = ~te
        if hamming:                      # +-1 vectors: agreement = (x.y/dim + 1)/2
            S = (X[te] @ X[tr].T / X.shape[1] + 1.0) / 2.0
        else:
            S = X[te] @ X[tr].T
        nn_pred[te] = y[tr][S.argmax(axis=1)]
        cents = []
        for lab in labels:
            c = X[tr][y[tr] == lab].mean(axis=0)
            cents.append(np.sign(c) if hamming else c / (np.linalg.norm(c) or 1.0))
        C = np.stack(cents)
        if hamming:
            Sc = (X[te] @ C.T / X.shape[1] + 1.0) / 2.0
        else:
            Sc = X[te] @ C.T
        ce_pred[te] = np.asarray(labels, dtype=object)[Sc.argmax(axis=1)]

    def bacc(p):
        return float(np.mean([np.mean(p[y == lab] == lab) for lab in labels]))
    return bacc(nn_pred), bacc(ce_pred)


def null_band(X, y, groups, draws=200, hamming=False):
    """Labels permuted WITHIN each group (keeps class balance per sensor)."""
    y = np.asarray(y)
    res = []
    for _ in range(draws):
        yp = y.copy()
        for g in set(groups):
            idx = np.where(groups == g)[0]
            yp[idx] = nrng.permutation(yp[idx])
        res.append(score(X, yp, groups, hamming))
    a = np.asarray(res)
    return a.mean(axis=0), np.percentile(a, 99, axis=0)


def report(title, feats, y, groups, draws):
    print(f"\n  {title}   (classes {dict(zip(*np.unique(y, return_counts=True)))})")
    print(f"    {'feature':8s}  {'1-NN':>6s}  {'centroid':>8s}   null mean / p99 (1-NN, centroid)")
    for name, X, ham in feats:
        nn, ce = score(X, y, groups, ham)
        m, p99 = null_band(X, y, groups, draws, ham)
        flag = "  <-- above p99" if (nn > p99[0] or ce > p99[1]) else ""
        print(f"    {name:8s}  {nn:6.3f}  {ce:8.3f}   {m[0]:.3f}/{p99[0]:.3f}, {m[1]:.3f}/{p99[1]:.3f}{flag}")


# ---------------------------------------------------------------- rung 0
def rung0():
    print("RUNG 0 -- instrument check (planted vectors, dim 256)")
    n, dim, G = 600, 256, 12
    groups = np.repeat(np.arange(G), n // G)
    y = np.asarray(nrng.choice(["up", "down", "flat"], size=n))
    dirs = {k: nrng.normal(size=dim) for k in ("up", "down", "flat")}
    content = {g: nrng.normal(size=dim) * 3 for g in range(G)}       # per-sensor content dominates
    before = np.stack([content[g] + nrng.normal(size=dim) * 0.3 for g in groups])
    after = before + np.stack([dirs[k] for k in y]) * 0.8 + nrng.normal(size=(n, dim)) * 0.3
    planted = after - before
    noise = nrng.normal(size=(n, dim))
    for name, X in (("planted", planted), ("noise", noise)):
        nn, ce = score(X, y, groups)
        print(f"  {name:8s} 1-NN {nn:.3f}  centroid {ce:.3f}   (expect planted ~1.0, noise ~0.333)")


# ---------------------------------------------------------------- rung 1
def rung1(draws):
    rows = simulate()
    P = pairs_of(rows)
    print(f"\nRUNG 1 -- {len(SENSORS)} sensors x {STEPS} readings, {len(P)} pairs, model {MODEL}")
    for r in rows[:3] + rows[STEPS:STEPS + 2]:
        print("   e.g.", r["text"])
    E = embed([r["text"] for r in rows])
    idx = {id(r): i for i, r in enumerate(rows)}
    A = np.stack([E[idx[id(p["a"])]] for p in P])
    B = np.stack([E[idx[id(p["b"])]] for p in P])
    D = B - A
    groups = np.asarray([p["s"] for p in P])
    R = nrng.normal(size=(8192, D.shape[1])).astype(np.float32)   # the vsa.py move: sign(R @ x)
    V = np.sign(_unit(D) @ R.T).astype(np.float32)
    feats = [("delta", D, False), ("after", B, False), ("pair", np.hstack([A, B]), False), ("vsa", V, True)]
    report("DIRECTION of change: up / down / flat", feats, [p["dir"] for p in P], groups, draws)
    keep = np.asarray([p["alarm"] != "none" for p in P])
    if keep.sum() >= 20:
        feats_al = [(n, X[keep], h) for n, X, h in feats]
        report("ALARM edge only (pairs where the ALERT flips): on / off",
               feats_al, [p["alarm"] for p in P if p["alarm"] != "none"], groups[keep], draws)


if __name__ == "__main__":
    STABLE = len(sys.argv) > 3 and sys.argv[3] == "stable"
    print("phrasing:", "STABLE (one per sensor)" if STABLE else "NOISY (random of 3 per reading)")
    if not STABLE:
        rung0()
    rung1(draws=int(sys.argv[2]) if len(sys.argv) > 2 else 100)
