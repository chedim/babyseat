"""Rung 4b: prose SEQUENCES per item -- the field's delta (mention - running state,
GAIN 0.5, seeded from the item's name) vs the previous-mention delta, per step.
Label per step: started / stopped / stayed_on / stayed_off (power), and the
health twin. Leave-one-item-out 1-NN / centroid, chance 0.25."""
import random, numpy as np
import delta_exp as X
import prose as PR
X.MODEL = "mxbai-embed-large"
rng = random.Random(9)
GAIN = 0.5

def run(kind, steps=40):
    A_SET, B_SET = (PR.OFF, PR.ON) if kind == "power" else (PR.OK, PR.BAD)
    names = {("a", "b"): "started" if kind == "power" else "broke",
             ("b", "a"): "stopped" if kind == "power" else "fixed",
             ("b", "b"): "stayed_on" if kind == "power" else "stayed_bad",
             ("a", "a"): "stayed_off" if kind == "power" else "stayed_ok"}
    texts, seqs = [], []
    for si, item in enumerate(PR.ITEMS):
        st, seq = rng.choice("ab"), []
        for _ in range(steps):
            nxt = st if rng.random() < 0.5 else ("b" if st == "a" else "a")
            texts.append(PR.say(B_SET if nxt == "b" else A_SET, item))
            seq.append((len(texts) - 1, names[(st, nxt)])); st = nxt
        seqs.append(seq)
    E = X.embed(texts); seeds = X.embed(PR.ITEMS)
    Dp, Ds, y, g = [], [], [], []
    for si, seq in enumerate(seqs):
        state = seeds[si]
        for t, (i, lab) in enumerate(seq):
            if t > 0:
                Dp.append(E[i] - E[seq[t - 1][0]]); Ds.append(E[i] - state); y.append(lab); g.append(si)
            state = state + GAIN * (E[i] - state); state = state / np.linalg.norm(state)
    g = np.asarray(g)
    print(f"{kind}: {len(y)} steps over {len(PR.ITEMS)} items")
    for name, D in (("prev-mention", np.stack(Dp)), ("running-state", np.stack(Ds))):
        f = X.score(D, y, g); print(f"   {name:14s} {f[0]:.3f} / {f[1]:.3f}")

run("power"); run("health")
