"""#258: rung 8 (the #256 predictor) on the corrected outcome.

Rung 8 scored a reply check's `changed` until #258; a look reads `changed`
for a line from before the push too. This replays rung 8's own scoring
(self_loop._rung8_series / _rung8) on a read-only COPY of the live record
twice: once on `changed` (what #256 measured) and once on `replied` (a line
from the other AFTER the push, by the check, from a copy of the chat store),
self alone (p_self recomputed from the record; the model's answers dropped).

    python -I experiments/rung7_turns/rung8_outcome.py $D     # the dir replay.py reads
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

import replay as RP  # noqa: E402
from membrane import self_loop as SL  # noqa: E402


def scored(recs, to, how):
    """The record with each reply check's `replied` set by `how` (and the
    recorded p_self / model answers dropped: self alone, recomputed)."""
    out = []
    for r in recs:
        if r.get("kind") == "reach" and r.get("eid") == to and r.get("reply"):
            r = {k: v for k, v in r.items() if k not in ("p_self", "p_llm", "llm_model", "llm_cal", "p_mix")}
            o = how(r)
            if o is None:
                continue
            r["replied"] = o
        out.append(r)
    return out


def report(name, recs, to, boot=2000):
    rows = SL._rung8_series(recs, to)
    win = rows[-SL.RUNG8_LOOKS:]
    o = np.array([r["o"] for r in win])
    ps = np.array([r["p_self"] for r in win])
    rt = np.array([r["rate"] for r in win])
    b_rate, b_self = ((o - rt) ** 2).mean(), ((o - ps) ** 2).mean()
    rng = np.random.default_rng(1)
    sk = []
    for _ in range(boot):
        i = rng.integers(0, len(o), len(o))
        br = ((o[i] - rt[i]) ** 2).mean()
        sk.append((br - ((o[i] - ps[i]) ** 2).mean()) / br if br > 0 else 0.0)
    lo, hi = np.quantile(sk, [0.025, 0.975])
    allo = np.array([r["o"] for r in rows])
    print(f"{name:9s} checks {len(rows):3d} (window {len(win)}), outcome rate {allo.mean():.2f}; "
          f"Brier running rate {b_rate:.3f}, p_self {b_self:.3f}; skill {(b_rate - b_self) / b_rate:+.3f} "
          f"[95% {lo:+.3f}, {hi:+.3f}]; rung 8 passes: {SL._rung8(recs)['passed']}")
    print("          outcomes: " + "".join(str(int(x)) for x in allo))


def main(d):
    recs, msgs, sess = RP.load(d)
    to = [r for r in recs if r.get("kind") == "push"][-1]["to"]
    speak = RP.lines_of(to, msgs, sess)
    import bisect
    pushes = [float(r["ts"]) for r in recs if r.get("kind") == "push" and r.get("to") == to]

    def replied(r):
        t = float(r["ts"])
        i = bisect.bisect_left(pushes, t)
        if not i:
            return None
        p = pushes[i - 1]
        j = bisect.bisect_right(speak, p)
        return j < len(speak) and speak[j] <= t

    def changed(r):
        return r.get("answer") == "changed" if r.get("answer") in ("changed", "unchanged") else None
    report("changed", scored(recs, to, changed), to)
    on_replied = scored(recs, to, lambda r: replied(r) if r.get("answer") in ("changed", "unchanged") else None)
    report("replied", on_replied, to)
    # the timer era's pushes mostly carry no spoke_age (#254 added it late): p_self then reads
    # "how fresh" from self's own lagged looks. What chat:in's heard_ts would have told it:
    fresh = []
    for r in on_replied:
        if r.get("kind") == "push" and r.get("to") == to:
            j = bisect.bisect_right(speak, float(r["ts"]))
            r = dict(r, spoke_age=round(float(r["ts"]) - speak[j - 1], 1)) if j else r
        fresh.append(r)
    report("+heard_ts", fresh, to)


if __name__ == "__main__":
    main(sys.argv[1])
