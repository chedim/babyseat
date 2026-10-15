"""Rung 4: PROSE about an item. Do deltas between two descriptions of the same item
carry the TRANSITION (not just the end state)?

Classes are built so the after-text alone cannot separate them:
  power:  started (off->on) | stopped (on->off) | stayed_on | stayed_off
  health: broke (ok->bad)   | fixed (bad->ok)   | stayed_ok | stayed_bad
'started' and 'stayed_on' END the same ("the pump is running"), so the after-state
caps near 0.5 on 4 classes; anything the delta adds above that is the transition.
Every mention is a random paraphrase with random context noise. Leave-one-item-out.
"""
import random
import sys

import numpy as np

import delta_exp as X

X.MODEL = sys.argv[1] if len(sys.argv) > 1 else "mxbai-embed-large"
rng = random.Random(3)

ITEMS = ["bilge pump", "nav lights", "anchor alarm", "watermaker", "autopilot", "fridge",
         "cabin heater", "starlink dish", "inverter", "windlass", "shore charger", "VHF radio",
         "deck wash pump", "cabin fan", "masthead light", "hot water heater"]

ON = ["the {i} is running", "{i} is on now", "the {i} kicked on", "{i}: active",
      "looks like the {i} is going", "the {i} came on", "{i} powered and working away",
      "I can hear the {i} running"]
OFF = ["the {i} is off", "{i} is not running", "the {i} shut off", "{i}: idle",
       "the {i} is quiet", "{i} switched off", "nothing from the {i} now", "the {i} stopped"]
OK = ["the {i} works fine", "{i} checks out ok", "the {i} is healthy", "{i}: no faults",
      "the {i} behaves normally", "tested the {i}, all good", "{i} is in good shape"]
BAD = ["the {i} is broken", "{i} threw a fault", "the {i} failed", "{i}: error state",
       "the {i} is acting up", "something is wrong with the {i}", "{i} is dead"]
NOISE = ["", "", "at {t}, ", "{t} -- ", "the seat says ", "log: ", "fyi ", "update: ",
         "checked at {t}: ", "heads up, "]


def say(fmts, item):
    t = f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}"
    return (rng.choice(NOISE) + rng.choice(fmts)).format(i=item, t=t)


def build(kind, per_item=36):
    a_set, b_set = (OFF, ON) if kind == "power" else (OK, BAD)
    names = (("started", "stopped", "stayed_on", "stayed_off") if kind == "power"
             else ("broke", "fixed", "stayed_ok", "stayed_bad"))
    plan = {names[0]: (a_set, b_set), names[1]: (b_set, a_set),
            names[2]: (b_set, b_set), names[3]: (a_set, a_set)}
    if kind == "health":   # broke = ok->bad, fixed = bad->ok, stayed_ok, stayed_bad
        plan = {"broke": (OK, BAD), "fixed": (BAD, OK), "stayed_ok": (OK, OK), "stayed_bad": (BAD, BAD)}
    P, texts = [], []
    for si, item in enumerate(ITEMS):
        for n in range(per_item):
            lab = names[n % 4]
            fa, fb = plan[lab]
            texts += [say(fa, item), say(fb, item)]
            P.append({"s": si, "lab": lab, "a": len(texts) - 2, "b": len(texts) - 1})
    return P, texts


def run(kind):
    P, texts = build(kind)
    E = X.embed(texts)
    A = np.stack([E[p["a"]] for p in P]); B = np.stack([E[p["b"]] for p in P])
    y = [p["lab"] for p in P]; g = np.asarray([p["s"] for p in P])
    R = np.random.default_rng(5).standard_normal((2048, E.shape[1] if E.shape[1] <= 1024 else 1024))
    print(f"\n{kind.upper()}: {len(P)} pairs over {len(ITEMS)} items, e.g.")
    for p in P[:4]:
        print(f"   [{p['lab']:10s}] {texts[p['a']]!r} -> {texts[p['b']]!r}")
    print("  leave-one-item-out, 1-NN / centroid (chance 0.25; after-only caps near 0.5)")
    for name, D in (("delta", B - A), ("after", B), ("pair", np.hstack([A, B]))):
        f = X.score(D, y, g)
        line = f"    {name:6s} {f[0]:.3f} / {f[1]:.3f}"
        if name == "delta" and E.shape[1] <= 1024:
            V = np.where(X._unit(D) @ R.T >= 0, 1.0, -1.0).astype(np.float32)
            h = X.score(V, y, g, hamming=True)
            line += f"    bits {h[0]:.3f} / {h[1]:.3f}"
        print(line)
    m, p99 = X.null_band(B - A, y, g, draws=100)
    print(f"    null (delta): mean {m[0]:.3f}/{m[1]:.3f}, p99 {p99[0]:.3f}/{p99[1]:.3f}")


if __name__ == "__main__":
    run("power")
    run("health")
