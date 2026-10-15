"""What the field stores vs what the experiment measured.

  prev   e(reading) - e(previous reading)                    (rung 1)
  state  e(reading) - running state, the field's own rule:
         state0 = e(sensor name), state <- unit(state + 0.5 (x - state))
Labels: the reading's change vs the previous reading (up/down/flat), as in rung 1.
"""
import numpy as np, delta_exp as X

GAIN = 0.5
for stable in (True, False):
    X.STABLE = stable
    rows = X.simulate(); P = X.pairs_of(rows)
    E = X.embed([r["text"] for r in rows]); idx = {id(r): i for i, r in enumerate(rows)}
    seeds = X.embed([s[0].replace("_", " ") for s in X.SENSORS])
    state_before = {}
    by = {}
    for r in rows: by.setdefault(r["s"], []).append(r)
    for s, rs in by.items():
        st = seeds[s]
        for r in rs:
            state_before[id(r)] = st
            x = E[idx[id(r)]]
            st = st + GAIN * (x - st); st = st / np.linalg.norm(st)
    B = np.stack([E[idx[id(p["b"])]] for p in P])
    D_prev = B - np.stack([E[idx[id(p["a"])]] for p in P])
    D_state = B - np.stack([state_before[id(p["b"])] for p in P])
    y = [p["dir"] for p in P]; g = np.asarray([p["s"] for p in P])
    R = np.random.default_rng(5).standard_normal((2048, 1024))
    bits = lambda D: np.where(X._unit(D) @ R.T >= 0, 1.0, -1.0).astype(np.float32)
    print(f"phrasing {'STABLE' if stable else 'NOISY '}   1-NN / centroid (chance 0.333)")
    for name, D in (("prev ", D_prev), ("state", D_state)):
        f = X.score(D, y, g); h = X.score(bits(D), y, g, hamming=True)
        print(f"  {name}  float {f[0]:.3f} / {f[1]:.3f}    bits2048 {h[0]:.3f} / {h[1]:.3f}")
