"""A Petri-style dish of competing predictors (#161 lab; see README.md).

Petri (github.com/chedim/petri): funclets live in a dish; one is applied with
probability proportional to its copy count; a funclet that fires is consumed
unless it returns `this`; `*` multiplies. Here a funclet is a PREDICTOR VARIANT
of "will this thing have changed when I look?", and a dish lives per thing.

Every predictor speaks the same tiny protocol, time-ordered, no lookahead:
    p = m.predict(dt)    # before the look; dt = seconds since the last look (or None)
    m.update(dt, y)      # after it; y = 1 changed, 0 unchanged

THE DISH RULE (constants below, all pinned so a run replays):
  * every variant starts with INIT copies;
  * prediction: mode "mix" -- the copy-weighted mean of the variants' p
    (Petri's "applied with probability proportional to copies", in expectation);
    mode "sample" -- ONE variant drawn proportional to copies (seeded RNG),
    copy dynamics identical to "mix" (judged against the hidden mean), so the
    two differ ONLY in the output; mode "fire" -- strict Petri: one variant
    drawn and only it is judged (better than the hidden mean -> `this` + `*`,
    else consumed);
  * after the outcome, each judged variant's Brier (p_i - y)^2 is compared
    with the reference Brier (the copy-weighted mean's): better by more than
    TIE -> it returns `this` and multiplies, c += max(1, round(GROW*c)); worse
    by more than TIE -> it is consumed, c -= max(1, round(GROW*c)); a tie keeps
    its copies;
  * never under FLOOR copies (nothing goes extinct: exploration);
  * total never over CAP: when over, every count is scaled by CAP/total
    (floored, kept >= FLOOR), then the largest trimmed by one until it fits.
"""

from __future__ import annotations

import math
import random

import numpy as np

INIT = 10          # starting copies per variant
GROW = 0.25        # share of its copies a variant gains (wins) or loses (losses) per look
TIE = 1e-9         # Brier differences under this are a tie
FLOOR = 1          # nothing goes extinct
CAP = 1000         # population cap per dish

LAM_GRID = np.logspace(-5, 0, 61)   # the same candidate rates as self_loop.LAM_GRID


# ---- the variants (funclets) -------------------------------------------------

class OneBit:
    """Self's one bit of memory: P(changed | the previous answer), Jeffreys
    smoothed; the smoothed marginal before a transition exists."""
    name = "onebit"

    def __init__(self):
        self.ch = self.n = 0
        self.prev = None
        self.after = {}

    def predict(self, dt):
        if self.prev in self.after and self.after[self.prev][1] > 0:
            c, n = self.after[self.prev]
            return (c + 0.5) / (n + 1.0)
        return (self.ch + 0.5) / (self.n + 1.0)

    def update(self, dt, y):
        y = int(bool(y))
        if self.prev is not None:
            e = self.after.setdefault(self.prev, [0, 0])
            e[0] += y
            e[1] += 1
        self.ch += y
        self.n += 1
        self.prev = y


class BaseRate:
    """The thing's smoothed share of changed looks so far."""
    name = "base"

    def __init__(self):
        self.ch = self.n = 0

    def predict(self, dt):
        return (self.ch + 0.5) / (self.n + 1.0)

    def update(self, dt, y):
        self.ch += int(bool(y))
        self.n += 1


class Rate:
    """Poisson rate in time: P(changed | dt) = 1 - exp(-lambda dt), lambda the
    (weighted) maximum likelihood on LAM_GRID over past looks -- the last
    `window` looks unweighted (self's rung-4 fit is window=30), or every look
    weighted 0.5**(age/half_life) (truncated at 6 half-lives). The smoothed
    base rate while it has no lambda or no dt."""

    def __init__(self, window=None, half_life=None):
        self.window, self.half_life = window, half_life
        self.name = f"rate_w{window}" if window else f"rate_h{half_life}"
        self.obs = []
        self.lam = None
        self.base = BaseRate()

    def _fit(self):
        if self.window:
            obs, w = self.obs[-self.window:], None
        else:
            obs = self.obs[-int(6 * self.half_life):]
            w = 0.5 ** (np.arange(len(obs))[::-1] / self.half_life)
        dt = np.array([o[0] for o in obs])[None, :]
        ch = np.array([o[1] for o in obs], dtype=float)[None, :]
        p = np.clip(1.0 - np.exp(-LAM_GRID[:, None] * dt), 1e-9, 1 - 1e-9)
        ll = ch * np.log(p) + (1 - ch) * np.log(1 - p)
        ll = (ll * w[None, :]).sum(axis=1) if w is not None else ll.sum(axis=1)
        self.lam = float(LAM_GRID[int(np.argmax(ll))])

    def predict(self, dt):
        if self.lam is None or dt is None:
            return self.base.predict(dt)
        return 1.0 - math.exp(-self.lam * dt)

    def update(self, dt, y):
        self.base.update(dt, y)
        if dt:
            self.obs.append((float(dt), int(bool(y))))
            self._fit()


class EMA:
    """Exponential moving average of the change frequency, half-life in looks."""

    def __init__(self, half_life):
        self.name = f"ema_h{half_life}"
        self.a = 1.0 - 0.5 ** (1.0 / half_life)
        self.m = 0.5

    def predict(self, dt):
        return self.m

    def update(self, dt, y):
        self.m += self.a * (int(bool(y)) - self.m)


class LastK:
    """The smoothed share of changed among the last k looks."""

    def __init__(self, k):
        self.name = f"last{k}"
        self.k = k
        self.ys = []

    def predict(self, dt):
        ys = self.ys[-self.k:]
        return (sum(ys) + 0.5) / (len(ys) + 1.0)

    def update(self, dt, y):
        self.ys.append(int(bool(y)))


def make_variants():
    """The founding population: one of each funclet."""
    return [OneBit(), BaseRate(), Rate(window=30), Rate(half_life=10), Rate(half_life=50),
            EMA(3), EMA(10), EMA(30), LastK(3), LastK(5)]


# ---- the dish ------------------------------------------------------------------

class Dish:
    def __init__(self, variants, init=INIT, grow=GROW, cap=CAP, mode="mix", seed=0):
        assert mode in ("mix", "sample", "fire")
        self.variants = list(variants)
        self.copies = [int(init)] * len(self.variants)
        self.grow, self.cap, self.mode = grow, cap, mode
        self.rng = random.Random(seed)
        self.name = f"dish_{mode}"
        self._ps = None
        self._pick = None

    def _mean(self, ps):
        tot = float(sum(self.copies))
        return sum(c * p for c, p in zip(self.copies, ps)) / tot

    def predict(self, dt):
        self._ps = [float(v.predict(dt)) for v in self.variants]
        if self.mode == "mix":
            return self._mean(self._ps)
        self._pick = self.rng.choices(range(len(self.variants)), weights=self.copies)[0]
        return self._ps[self._pick]

    def _step(self, c, sign):
        d = max(1, round(self.grow * c))
        return max(FLOOR, c + sign * d)

    def update(self, dt, y):
        y = int(bool(y))
        ps = self._ps if self._ps is not None else [float(v.predict(dt)) for v in self.variants]
        ref = (self._mean(ps) - y) ** 2
        judged = [self._pick] if self.mode == "fire" else range(len(ps))
        for i in judged:
            b = (ps[i] - y) ** 2
            if b < ref - TIE:
                self.copies[i] = self._step(self.copies[i], +1)
            elif b > ref + TIE:
                self.copies[i] = self._step(self.copies[i], -1)
        self._cap()
        for v in self.variants:
            v.update(dt, y)
        self._ps = self._pick = None

    def _cap(self):
        tot = sum(self.copies)
        if tot <= self.cap:
            return
        self.copies = [max(FLOOR, int(c * self.cap / tot)) for c in self.copies]
        while sum(self.copies) > self.cap:
            i = max(range(len(self.copies)), key=lambda j: self.copies[j])
            self.copies[i] -= 1


# ---- the comparators -------------------------------------------------------------

class StaticMix:
    """Equal weights forever: the same variants with no selection."""
    name = "static_mix"

    def __init__(self, variants):
        self.variants = list(variants)

    def predict(self, dt):
        return sum(float(v.predict(dt)) for v in self.variants) / len(self.variants)

    def update(self, dt, y):
        for v in self.variants:
            v.update(dt, y)


class Hedge:
    """Exponential weights: w_i proportional to exp(-eta * cumulative Brier_i);
    prediction = the weighted mean."""

    def __init__(self, variants, eta=2.0):
        self.variants = list(variants)
        self.eta = eta
        self.name = f"hedge_eta{eta:g}"
        self.loss = np.zeros(len(self.variants))
        self._ps = None

    def predict(self, dt):
        self._ps = np.array([float(v.predict(dt)) for v in self.variants])
        w = np.exp(-self.eta * (self.loss - self.loss.min()))
        return float((w * self._ps).sum() / w.sum())

    def update(self, dt, y):
        ps = self._ps if self._ps is not None else np.array([float(v.predict(dt)) for v in self.variants])
        self.loss += (ps - int(bool(y))) ** 2
        for v in self.variants:
            v.update(dt, y)
        self._ps = None


class Single:
    """One variant alone (for the hindsight-best comparator)."""

    def __init__(self, v):
        self.v, self.name = v, v.name

    def predict(self, dt):
        return float(self.v.predict(dt))

    def update(self, dt, y):
        self.v.update(dt, y)


def run(factory, seq):
    """Time-ordered: predict each look, then learn its outcome. seq: [(dt, y)]."""
    m = factory()
    out = []
    for dt, y in seq:
        out.append(float(m.predict(dt)))
        m.update(dt, y)
    return out
