"""Rung 5: two DIFFERENT facts about the SAME entity ("the telephone is red" /
"it was an old dial-type phone"). Once we know both sentences are the same entity,
what does subtracting carry?

  name-residual   e(sentence) - e("the <object>")            what the sentence adds
  state-residual  e(sentence) - mean(e(the object's OTHER sentences))   (the field's view)
  raw             e(sentence)                                 (baseline)
  pair-delta      e(s1) - e(s2) for two sentences of one object, label = (aspect1, aspect2)

Questions, leave-one-OBJECT-out (the object never seen):
  ASPECT  which aspect does the sentence state (colour/age/material/size/condition/location)?
  VALUE   within colour: which colour (does 'red' on a phone match 'red' on a car)?
  PAIR    which two aspects do two sentences about one object differ by?
"""
import random
import sys

import numpy as np

import delta_exp as X

X.MODEL = "mxbai-embed-large"
rng = random.Random(5)

OBJECTS = ["telephone", "car", "lamp", "guitar", "watch", "bicycle", "kettle", "radio",
           "sofa", "camera", "typewriter", "suitcase", "clock", "piano", "boat", "coat"]
VAL = {
    "colour": ["red", "blue", "green", "black", "white", "yellow"],
    "age": ["very old", "brand new", "from the 1950s", "antique", "a recent model", "decades old"],
    "material": ["made of wood", "made of brass", "plastic", "made of leather", "steel", "made of glass"],
    "size": ["tiny", "huge", "pocket-sized", "enormous", "small", "bulky"],
    "condition": ["broken", "in perfect condition", "scratched", "falling apart", "freshly restored", "dented"],
    "location": ["in the hallway", "on the boat", "in the attic", "by the window", "in the garage", "in the kitchen"],
}
FIRST = ["the {o} is {v}", "there is a {o}, it's {v}", "I saw the {o}: {v}", "the {o} was {v}",
         "that {o} is {v}", "our {o} is {v}"]
LATER = ["it is {v}", "it was {v}", "it's {v}, by the way", "and it is {v}", "also {v}",
         "the {o} is {v}", "I remember it was {v}"]


HEAVY = "heavy" in sys.argv     # every sentence names the object with its own long context
CTX = {o: c for o, c in zip(OBJECTS, [
    "the one grandpa used to call the harbour master with", "the one we drove to the coast every summer",
    "the one that sat on the chart table during the night watches", "the one I learned three chords on",
    "the one the skipper got for twenty years at sea", "the one chained to the dock rail in the marina",
    "the one that whistles in the galley every morning", "the one that picks up the coastal weather station",
    "the one everybody naps on after a long passage", "the one that took the photos of the hurricane damage",
    "the one great aunt Vera wrote her letters on", "the one packed for the ferry to the islands",
    "the one ticking above the navigation desk", "the one in the old church hall by the harbour",
    "the one moored at the end of the long pier", "the one with the foul-weather hood for the winter crossings"])}


def sentence(o, v, later):
    if HEAVY:
        return rng.choice(["the {o}, {c}, is {v}", "about the {o}, {c}: it is {v}",
                           "you know the {o}, {c}? it was {v}", "the {o} ({c}) is {v}"]).format(o=o, c=CTX[o], v=v)
    return rng.choice(LATER if later else FIRST).format(o=o, v=v)


def build(per_aspect=6):
    S = []      # (object idx, aspect, value, text)
    for oi, o in enumerate(OBJECTS):
        for a, vals in VAL.items():
            for n in range(per_aspect):
                v = rng.choice(vals)
                S.append({"o": oi, "a": a, "v": v, "text": sentence(o, v, later=rng.random() < 0.5)})
    return S


def main():
    S = build()
    E = X.embed([s["text"] for s in S])
    N = X.embed([(f"the {o}, {CTX[o]}" if HEAVY else f"the {o}") for o in OBJECTS])
    g = np.asarray([s["o"] for s in S])
    name_res = E - N[g]
    state_res = np.zeros_like(E)
    for oi in range(len(OBJECTS)):
        idx = np.where(g == oi)[0]
        tot = E[idx].sum(axis=0)
        for i in idx:                     # the object's state WITHOUT this sentence
            state_res[i] = E[i] - (tot - E[i]) / (len(idx) - 1)
    feats = {"raw": E, "name-res": name_res, "state-res": state_res}

    print(f"{len(S)} sentences, {len(OBJECTS)} objects x {len(VAL)} aspects, e.g.")
    for s in S[:3] + S[40:42]:
        print("  ", repr(s["text"]))

    ya = [s["a"] for s in S]
    print("\nASPECT (6 classes, chance 0.167), leave-one-object-out, 1-NN / centroid")
    for k, F in feats.items():
        f = X.score(F, ya, g)
        print(f"  {k:9s} {f[0]:.3f} / {f[1]:.3f}")

    col = np.asarray([s["a"] == "colour" for s in S])
    yv = [s["v"] for s in S if s["a"] == "colour"]
    print(f"\nVALUE within colour ({len(VAL['colour'])} colours, chance 0.167), {col.sum()} sentences")
    for k, F in feats.items():
        f = X.score(F[col], yv, g[col])
        print(f"  {k:9s} {f[0]:.3f} / {f[1]:.3f}")

    # PAIR: two sentences of the same object, different aspects; label = unordered aspect pair
    pairs = []
    for oi in range(len(OBJECTS)):
        idx = list(np.where(g == oi)[0])
        for _ in range(40):
            i, j = rng.sample(idx, 2)
            if S[i]["a"] != S[j]["a"]:
                pairs.append((i, j))
    pi = np.asarray([p[0] for p in pairs]); pj = np.asarray([p[1] for p in pairs])
    yp = ["+".join(sorted((S[i]["a"], S[j]["a"]))) for i, j in pairs]
    gp = g[pi]
    D = E[pi] - E[pj]
    Dsym = np.abs(D)                       # order-free: the pair label is unordered
    print(f"\nPAIR ({len(set(yp))} aspect pairs, chance {1 / len(set(yp)):.3f}), {len(pairs)} pairs")
    for k, F in (("|delta|", Dsym), ("delta", D), ("sum", E[pi] + E[pj]), ("concat", np.hstack([E[pi], E[pj]]))):
        f = X.score(F, yp, gp)
        print(f"  {k:9s} {f[0]:.3f} / {f[1]:.3f}")


if __name__ == "__main__":
    main()
