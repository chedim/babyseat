"""Rung 3: the boat's REAL Home Assistant history (agDroid recorder, salvaged copy).

Readings rendered as the seat would see an HA state: '<entity_id>: <value> <unit>'
(one stable phrasing per sensor). Per sensor: consecutive numeric states -> pairs;
label from the sensor's OWN scale: the smallest third of |change| = flat, else up/down.

Tests (balanced accuracy, 1-NN / centroid, permutation null):
  CROSS   leave-one-sensor-out (transfer to a sensor never seen)
  SELF    within each sensor: first half of its history -> recall for the second half
Features: delta vs previous reading, delta vs running state (the field's rule),
after-state only, the before+after pair; float and 2048 sign bits.
"""
import random
import sqlite3
import sys

import numpy as np

import delta_exp as X

X.MODEL = "mxbai-embed-large"      # delta_exp reads argv[1] as the model; here it is the DB

DB = sys.argv[1]
N_SENSORS = int(sys.argv[2]) if len(sys.argv) > 2 else 20
PAIRS_PER = int(sys.argv[3]) if len(sys.argv) > 3 else 150
SIG = int(sys.argv[4]) if len(sys.argv) > 4 else 0   # >0: render values at SIG significant figures
GAIN = 0.5
rng = random.Random(11)


def load():
    con = sqlite3.connect(f"file:{DB}?immutable=1", uri=True)
    units = {}
    try:
        units = dict(con.execute("SELECT statistic_id, unit_of_measurement FROM statistics_meta"))
    except sqlite3.DatabaseError:
        pass
    rows = con.execute("""
        SELECT sm.entity_id, s.state, s.last_updated_ts
        FROM states s JOIN states_meta sm ON sm.metadata_id = s.metadata_id
        WHERE sm.entity_id LIKE 'sensor.%' AND s.state GLOB '[0-9-]*'
          AND NOT s.state GLOB '*[a-zA-Z:]*' AND s.last_updated_ts IS NOT NULL""")
    series = {}
    for eid, st, ts in rows:
        try:
            v = float(st)
        except ValueError:
            continue
        series.setdefault(eid, []).append((ts, v, st))
    out = {}
    for eid, s in series.items():
        s.sort()
        dedup = [s[0]]
        for r in s[1:]:
            if r[1] != dedup[-1][1]:
                dedup.append(r)
        if len(dedup) > 2 * PAIRS_PER and len(set(r[1] for r in dedup)) > 30:
            out[eid] = dedup
    return out, units


ANGLE = ("azimuth", "heading", "direction", "bearing", "course")


def pick(series, units):
    """The busiest sensors, but: no wrapping angles (359 -> 1 is a small move UP
    that reads as a big drop), no exact twins (one cell voltage exported under two entity ids,
    the same signal under two names), and at most 2 per 'family' (the id's stem),
    so the cross-sensor test is not near-duplicate sensors finding each other."""
    fam, seen = {}, set()
    for eid in sorted(series, key=lambda e: -len(series[e])):
        if any(a in eid for a in ANGLE) or (units.get(eid) or "") == "\u00b0":
            continue
        fp = tuple(round(r[1], 6) for r in series[eid][:300])
        if fp in seen:
            continue
        seen.add(fp)
        stem = eid.split(".", 1)[1].rsplit("_", 1)[0]
        if len(fam.setdefault(stem, [])) < 2:
            fam[stem].append(eid)
    chosen = [e for es in fam.values() for e in es]
    return sorted(chosen, key=lambda e: -len(series[e]))[:N_SENSORS]


def main():
    series, units = load()
    chosen = pick(series, units)
    print(f"{len(series)} numeric sensors with history; using {len(chosen)}:")
    texts, P = [], []
    for si, eid in enumerate(chosen):
        s = series[eid]
        u = units.get(eid) or ""
        k = [i for i in range(len(s) - 1)]
        k = sorted(rng.sample(k, min(PAIRS_PER, len(k))))
        dv = np.array([s[i + 1][1] - s[i][1] for i in k])
        q = np.quantile(np.abs(dv), 1 / 3)
        print(f"   {eid:55s} {len(s):6d} states  unit {u!r:8s}  e.g. {s[k[0]][2]} -> {s[k[0] + 1][2]}")
        for i, d in zip(k, dv):
            ra, rb = ((f"{s[i][1]:.{SIG}g}", f"{s[i + 1][1]:.{SIG}g}") if SIG else (s[i][2], s[i + 1][2]))
            if ra == rb:                  # no visible change: no direction (the field's None)
                dropped = globals().setdefault("DROPPED", [0]); dropped[0] += 1
                continue
            ta = f"{eid}: {ra} {u}".strip()
            tb = f"{eid}: {rb} {u}".strip()
            lab = "flat" if abs(d) <= q else ("up" if d > 0 else "down")
            texts += [ta, tb]
            P.append({"s": si, "t": s[i + 1][0], "dir": lab, "a": len(texts) - 2, "b": len(texts) - 1})
    E = X.embed(texts)
    seeds = X.embed([e.split(".", 1)[1].replace("_", " ") for e in chosen])
    # the running state (the field's rule) is replayed over EVERY reading of the
    # sensor up to the pair, not only the sampled ones -- approximated here by the
    # sampled readings in order (the state the field would hold is smoother still)
    Dstate = np.zeros((len(P), E.shape[1]), dtype=np.float32)
    by = {}
    for j, p in enumerate(P):
        by.setdefault(p["s"], []).append(j)
    for s, js in by.items():
        st = seeds[s]
        for j in js:
            p = P[j]
            Dstate[j] = E[p["b"]] - st
            st = st + GAIN * (E[p["b"]] - st)
            st = st / np.linalg.norm(st)
    A = np.stack([E[p["a"]] for p in P]); B = np.stack([E[p["b"]] for p in P])
    feats = {"prev": B - A, "state": Dstate, "after": B, "pair": np.hstack([A, B])}
    R = np.random.default_rng(5).standard_normal((2048, 1024))
    y = [p["dir"] for p in P]
    g = np.asarray([p["s"] for p in P])
    print(f"\nrendering: {'%d significant figures' % SIG if SIG else 'raw HA state strings'}; "
          f"dropped as textually unchanged: {globals().get('DROPPED', [0])[0]}")
    print(f"{len(P)} pairs, labels {dict(zip(*np.unique(y, return_counts=True)))}")

    print("\nCROSS (leave-one-sensor-out), 1-NN / centroid, chance 0.333")
    for name, D in feats.items():
        f = X.score(D, y, g)
        line = f"  {name:6s} float {f[0]:.3f} / {f[1]:.3f}"
        if name in ("prev", "state"):
            V = np.where(X._unit(D) @ R.T >= 0, 1.0, -1.0).astype(np.float32)
            h = X.score(V, y, g, hamming=True)
            line += f"    bits {h[0]:.3f} / {h[1]:.3f}"
        print(line)
    m, p99 = X.null_band(feats["prev"], y, g, draws=100)
    print(f"  null (prev): mean {m[0]:.3f}/{m[1]:.3f}, p99 {p99[0]:.3f}/{p99[1]:.3f}")

    print("\nSELF (each sensor: first half -> second half), 1-NN, chance 0.333")
    yv = np.asarray(y)
    for name, D in feats.items():
        Du = X._unit(D)
        acc = []
        for s, js in by.items():
            js = sorted(js, key=lambda j: P[j]["t"])
            h = len(js) // 2
            tr, te = np.asarray(js[:h]), np.asarray(js[h:])
            pred = yv[tr][(Du[te] @ Du[tr].T).argmax(axis=1)]
            labs = [l for l in ("up", "down", "flat") if (yv[te] == l).any()]
            acc.append(np.mean([np.mean(pred[yv[te] == l] == l) for l in labs]))
        print(f"  {name:6s} {np.mean(acc):.3f}  (per-sensor min {np.min(acc):.2f}, max {np.max(acc):.2f})")


if __name__ == "__main__":
    main()
