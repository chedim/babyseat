import numpy as np, delta_exp as X
X.STABLE = True
rows = X.simulate(); P = X.pairs_of(rows)
E = X.embed([r["text"] for r in rows]); idx = {id(r): i for i, r in enumerate(rows)}
A = np.stack([E[idx[id(p["a"])]] for p in P]); B = np.stack([E[idx[id(p["b"])]] for p in P])
D = X._unit(B - A); y = [p["dir"] for p in P]; g = np.asarray([p["s"] for p in P])
R = np.random.default_rng(5).standard_normal((8192, 1024))      # the codebook's own projection
print("float delta     1-NN %.3f" % X.score(D, y, g)[0])
for dim in (8192, 4096, 2048, 1024, 512):
    V = np.where(D @ R[:dim].T >= 0, 1.0, -1.0).astype(np.float32)
    print(f"bits {dim:5d}      1-NN %.3f" % X.score(V, y, g, hamming=True)[0])
