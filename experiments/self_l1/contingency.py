"""THE CONTINGENCY LEDGER (#161, layer 1): which things change because *I*
acted? For every act kind and every entity: how often the entity changes in
the `window` after that act (p_act), against how often it changes in any
window of that length on its own (p_base, from its rate outside act windows).

    body   -- reliable and immediate: it answers my acts nearly always, at a
              steady short delay (perfect contingency: "this is me")
    other  -- answers my acts above chance, but unreliably or at a variable
              delay (imperfect contingency: "this responds to me, it isn't me")
    world  -- everything else (its changes owe nothing to my acts)

Pure functions over an event stream: acts [(t, kind)], changes [(t, eid)].
Nothing here touches membrane; the same ledger later reads the seat's real
act log and field pulses."""
from __future__ import annotations

import math
import random
from collections import defaultdict

BODY_P = 0.8          # a body part answers at least this often
BODY_LAT_MAD = 0.75   # ...at a steady delay: the median absolute deviation
                      # of its delays (robust: a stray answer to the NEXT act
                      # counted against this one must not unmake a body part)
OTHER_DP = 0.10       # an other raises the change rate at least this much
Z_MIN = 4.0           # and above chance by this many binomial sigmas
MIN_KIND_ACTS = 20    # THE REAL SEAT'S LESSON (rung 2): its log has 48 act
                      # kinds, most of them a handful of calls. Three calls of
                      # one kind, one coincidence, and p_act=1.00 against a
                      # zero baseline crowned a "body part" out of nothing. A
                      # kind must be exercised this many times before it may
                      # claim an entity.


class Ledger:
    def __init__(self, window: float = 2.0, min_acts: int = MIN_KIND_ACTS):
        self.window = float(window)
        self.min_acts = int(min_acts)
        self.horizon = 1.0
        self.n_act: dict = defaultdict(int)                 # kind -> acts
        self.hits: dict = defaultdict(int)                  # (kind, eid) -> acts answered
        self.lat: dict = defaultdict(list)                  # (kind, eid) -> first-change delays
        self.base: dict = {}                                 # eid -> p_base for one window
        self.eids: set = set()

    def observe(self, acts, changes, horizon: float) -> None:
        acts = sorted(acts)
        self.horizon = max(1.0, float(horizon))
        by_e: dict = defaultdict(list)
        for t, e in sorted(changes):
            by_e[e].append(t)
            self.eids.add(e)
        w = self.window
        for t, k in acts:
            self.n_act[k] += 1
        # the act windows (merged) -- the baseline is measured OUTSIDE them
        spans, cur = [], None
        for t, _ in acts:
            if cur and t <= cur[1]:
                cur[1] = max(cur[1], t + w)
            else:
                cur = [t, t + w]
                spans.append(cur)
        covered = sum(min(b, horizon) - a for a, b in spans if a < horizon)
        free = max(1e-9, horizon - covered)

        def inside(ts):
            i = 0
            for a, b in spans:
                while i < len(ts) and ts[i] <= a:
                    i += 1
                j = i
                while j < len(ts) and ts[j] <= b:
                    j += 1
                yield from ts[i:j]
                i = j
        import bisect
        for e, ts in by_e.items():
            n_in = sum(1 for _ in inside(ts))
            rate = (len(ts) - n_in) / free
            self.base[e] = 1.0 - math.exp(-rate * w)
            for t, k in acts:
                i = bisect.bisect_right(ts, t)
                if i < len(ts) and ts[i] <= t + w:
                    self.hits[(k, e)] += 1
                    self.lat[(k, e)].append(ts[i] - t)

    def stats(self) -> dict:
        """Per entity: the act kind it answers best, and how."""
        out = {}
        floor = 1.0 / self.horizon            # p_base=0 is not a licence to
        for e in self.eids:                   # divide by zero: the smallest
            pb = self.base.get(e, 0.0)        # rate the horizon can resolve
            best = None
            for k, n in self.n_act.items():
                if not n or n < self.min_acts:
                    continue
                h = self.hits.get((k, e), 0)
                p = h / n
                pbf = max(pb, floor)
                z = (h - n * pbf) / math.sqrt(max(1e-12, n * pbf * (1 - pbf)))
                lat = self.lat.get((k, e)) or []
                m = sum(lat) / len(lat) if lat else None
                sd = (math.sqrt(sum((x - m) ** 2 for x in lat) / len(lat)) if lat else None)
                row = {"act": k, "n": n, "p_act": p, "p_base": pb, "dp": p - pb, "z": z,
                       "lat_mean": m, "lat_std": sd, "lat_mad": _mad(lat)}
                if best is None or row["dp"] > best["dp"]:
                    best = row
            out[e] = best or {"act": None, "n": 0, "p_act": 0.0, "p_base": pb, "dp": 0.0,
                              "z": 0.0, "lat_mean": None, "lat_std": None, "lat_mad": None}
        return out


def _mad(xs) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    med = s[len(s) // 2]
    d = sorted(abs(x - med) for x in s)
    return d[len(d) // 2]


def classify(stats: dict) -> dict:
    body, other, world = [], [], []
    for e, s in sorted(stats.items()):
        sig = s["z"] >= Z_MIN
        if sig and s["p_act"] >= BODY_P and (s["lat_mad"] or 0.0) <= BODY_LAT_MAD:
            body.append(e)
        elif sig and s["dp"] >= OTHER_DP:
            other.append(e)
        else:
            world.append(e)
    return {"body": body, "other": other, "world": world}


def shuffled(acts, horizon: float, seed: int = 0) -> list:
    """The null: the same act kinds at random times."""
    r = random.Random(seed)
    return sorted((r.uniform(0, horizon), k) for _, k in acts)
