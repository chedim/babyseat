import sys, numpy as np
import delta_exp as X
X.STABLE = True
rows = X.simulate(); P = X.pairs_of(rows)
E = X.embed([r["text"] for r in rows]); idx = {id(r): i for i, r in enumerate(rows)}
A = np.stack([E[idx[id(p["a"])]] for p in P]); B = np.stack([E[idx[id(p["b"])]] for p in P])
D = X._unit(B - A); y = np.asarray([p["dir"] for p in P]); g = np.asarray([p["s"] for p in P])
print(f"{'sensor':11s} {'acc':>5s}   nearest-neighbour came from (top 3)")
for s in range(len(X.SENSORS)):
    te, tr = g == s, g != s
    nn = (D[te] @ D[tr].T).argmax(axis=1)
    acc = np.mean([np.mean(y[tr][nn][y[te] == k] == k) for k in ("up", "down", "flat")])
    src = np.bincount(g[tr][nn], minlength=len(X.SENSORS))
    top = ", ".join(f"{X.SENSORS[i][0]} {src[i]}" for i in src.argsort()[::-1][:3])
    print(f"{X.SENSORS[s][0]:11s} {acc:5.2f}   {top}")
