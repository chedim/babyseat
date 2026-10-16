"""A TOY WORLD WITH A KNOWN BODY (#161, layer 1's first gate): `self` babbles
acts at random; some entities answer them reliably and at once (its body),
some agents answer late and unreliably (others), the rest churn on their own
at rates from rare to frantic (world). Does the contingency ledger recover the
truth from the event stream alone?

    python sandbox.py            # the report: recovery, the null, stability, learning curve
"""
from __future__ import annotations

import random

ACTS = ("move", "write", "read", "watch", "push")


class World:
    def __init__(self, seed: int = 0, p_act: float = 0.05):
        self.r = random.Random(seed)
        self.p_act = p_act
        # body: who answers which acts (prob, latency 0..1)
        self.body = {"b:position": (ACTS, 0.95), "b:script_data": (("write",), 0.9),
                     "b:watch": (("watch",), 0.9), "b:chat_out": (("push",), 0.9)}
        # others: answer any act half the time, 3..10 steps later; also act alone
        self.others = {"o:operator": 0.5, "o:argus": 0.4}
        # world: spontaneous churn, rare to frantic
        self.world = {f"w:{i:02d}": rate for i, rate in
                      enumerate([0.001, 0.003, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3] * 3)}
        self.truth = {"body": sorted(self.body), "other": sorted(self.others),
                      "world": sorted(self.world)}

    def run(self, steps: int):
        r = self.r
        acts, changes = [], []
        for t in range(steps):
            if r.random() < self.p_act:
                k = r.choice(ACTS)
                ta = t + r.random()
                acts.append((ta, k))
                for e, (kinds, p) in self.body.items():
                    if k in kinds and r.random() < p:
                        changes.append((ta + r.uniform(0.0, 1.0), e))
                for e, p in self.others.items():
                    if r.random() < p:
                        changes.append((ta + r.uniform(3.0, 10.0), e))
            for e in self.body:                     # a body also twitches on its own
                if r.random() < 0.002:
                    changes.append((t + r.random(), e))
            for e in self.others:                   # others have lives of their own
                if r.random() < 0.01:
                    changes.append((t + r.random(), e))
            for e, rate in self.world.items():
                if r.random() < rate:
                    changes.append((t + r.random(), e))
        return sorted(acts), sorted(changes)


def _score(found, truth):
    f, t = set(found), set(truth)
    p = len(f & t) / len(f) if f else (1.0 if not t else 0.0)
    rc = len(f & t) / len(t) if t else 1.0
    return p, rc


def report(seeds=range(10), steps=8000) -> str:
    import contingency as C
    lines = ["| seed | body P/R | other P/R | null body | halves Jaccard |", "|---|---|---|---|---|"]
    agg = {"bp": [], "br": [], "op": [], "or": [], "null": [], "jac": []}
    for s in seeds:
        w = World(seed=s)
        acts, ch = w.run(steps)
        led = C.Ledger(window=12.0)
        led.observe(acts, ch, horizon=steps)
        cls = C.classify(led.stats())
        bp, br = _score(cls["body"], w.truth["body"])
        op, orr = _score(cls["other"], w.truth["other"])
        nl = C.Ledger(window=12.0)
        nl.observe(C.shuffled(acts, steps, seed=s), ch, horizon=steps)
        null_body = len(C.classify(nl.stats())["body"])
        h = steps // 2
        a, b = C.Ledger(window=12.0), C.Ledger(window=12.0)
        a.observe([x for x in acts if x[0] < h], [x for x in ch if x[0] < h], horizon=h)
        b.observe([(t - h, k) for t, k in acts if t >= h], [(t - h, e) for t, e in ch if t >= h], horizon=h)
        ba, bb = set(C.classify(a.stats())["body"]), set(C.classify(b.stats())["body"])
        jac = len(ba & bb) / len(ba | bb) if ba | bb else 1.0
        for k, v in (("bp", bp), ("br", br), ("op", op), ("or", orr), ("null", null_body), ("jac", jac)):
            agg[k].append(v)
        lines.append(f"| {s} | {bp:.2f}/{br:.2f} | {op:.2f}/{orr:.2f} | {null_body} | {jac:.2f} |")
    m = {k: sum(v) / len(v) for k, v in agg.items()}
    lines.append(f"| **mean** | **{m['bp']:.2f}/{m['br']:.2f}** | **{m['op']:.2f}/{m['or']:.2f}** | "
                 f"**{m['null']:.1f}** | **{m['jac']:.2f}** |")
    # the learning curve: how many acts until the body is found?
    curve = ["", "acts seen -> body recall (mean over seeds):"]
    for n_steps in (500, 1000, 2000, 4000, 8000):
        rs = []
        for s in seeds:
            w = World(seed=s)
            acts, ch = w.run(n_steps)
            led = C.Ledger(window=12.0)
            led.observe(acts, ch, horizon=n_steps)
            rs.append((len(acts), _score(C.classify(led.stats())["body"], w.truth["body"])[1]))
        curve.append(f"  ~{sum(a for a, _ in rs) // len(rs):4d} acts: {sum(r for _, r in rs) / len(rs):.2f}")
    return "\n".join(lines + curve)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    print(report())
