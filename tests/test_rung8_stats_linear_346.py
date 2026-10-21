"""#346: _rung8_stats (the panel's rung 8 numbers, read by status() -- which
the cycle's _show calls every cycle) found each ask's reply check by scanning
the record from its start: time quadratic in the record's length (on a 16 MB
record, over a second a cycle). It now finds them in one pass. Its numbers
must not change: the outputs below were pinned from the old code, and an
oracle -- the old function verbatim -- is compared on many random records.
"""
import json
import os
import random
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import self_loop as SL  # noqa: E402
from membrane.entities import MEMBRANE_NS  # noqa: E402

C = MEMBRANE_NS + "Component:"
ALICE = C + "chat:in:alice"
BOB = C + "chat:in:bob"
LAMP = C + "w:lamp"
T0 = 1_760_000_000.0


def _old_rung8_stats(recs):
    """The pre-#346 _rung8_stats, verbatim but for its first-look scan."""
    def first_look_after(eid, ts):
        return next((r for r in recs if r.get("kind") == "reach" and r.get("eid") == eid
                     and float(r.get("ts") or 0) > ts), None)
    to = SL._other(recs)
    asked = [r for r in recs if r.get("kind") == "push" and r.get("asked") and r.get("to") == to]
    ct = SL._to_carry(recs, to)
    checked, paired = ct.get("checked", 0), ct.get("paired", 0)
    dropped = SL._dropped(recs)
    for p in asked:
        if float(p.get("ts") or 0) in dropped:
            continue
        look = first_look_after(to, float(p.get("ts") or 0))
        if look is None:
            continue
        checked += 1
        paired += int(look.get("p_llm") is not None and look.get("answer") in ("changed", "unchanged"))
    allrows = SL._rung8_series(recs, to) if to else []
    rows = allrows[-SL.RUNG8_LOOKS:]
    llm = [r for r in rows if r.get("p_llm") is not None]

    def brier(rs, key):
        return round(sum((r["o"] - float(r[key])) ** 2 for r in rs) / len(rs), 4) if rs else None
    model = next((r["model"] for r in reversed(allrows) if r.get("p_llm") is not None), None)
    return {"asked": len(asked) + ct.get("asked", 0), "checked": checked, "paired": paired,
            "pair_rate": round(paired / checked, 4) if checked else None,
            "checks": len(rows),
            "brier_self": brier(rows, "p_self"), "brier_mix": brier(rows, "p"),
            "brier_rate": brier(rows, "rate"), "brier_model": brier(llm, "p_llm"),
            "advisor_weight": SL._advisor_cal(allrows, model)[1],
            "advisor_model": model,
            "base_rate": round(SL._base_rate(recs, to)[0], 4) if to else None,
            "recent_rate": round(SL._recent_rate(recs, to)[0], 4) if to else None}


def _life(seed, n, summary=False, jitter=True):
    """A random waking record: cycles, pushes (asked or not, to alice or bob,
    chat or the ball), looks at both and at a lamp (reply checks, audits,
    advisor answers, stamps missing or out of order), sleeps that drop a
    turn, rung passes and their voids -- optionally after a summary line."""
    rng = random.Random(seed)
    recs = []
    if summary:
        recs.append({"kind": SL.SUMMARY, "id": "s", "at": T0, "n": 0,
                     "carry": {"cycle": 40, "push": 9,
                               "to": {ALICE: {"asked": 7, "checked": 5, "paired": 2},
                                      BOB: {"asked": 3, "checked": 3, "paired": 1}},
                               "r8": {SL._r8_key(ALICE, "chat", 1): [11, 4],
                                      SL._r8_key(BOB, "chat", 1): [6, 1]}},
                     "stalls": []})
    ts = T0 + 10
    asked_ts = []
    for i in range(n):
        ts += rng.uniform(0.5, 30.0)
        t = ts
        if jitter and rng.random() < 0.05:
            t = ts - rng.uniform(0, 120)                  # a stamp out of order
        x = rng.random()
        if x < 0.40:
            recs.append({"ts": t, "kind": "cycle", "n": i, "change": rng.choice(["world", "none"])})
        elif x < 0.55:
            to = rng.choice([ALICE, ALICE, BOB])
            p = {"ts": t, "kind": "push", "n": i, "to": to, "eid": MEMBRANE_NS + "self",
                 "asked": rng.random() < 0.6, "bin": rng.randrange(3)}
            if rng.random() < 0.2:
                p["medium"], p["clock"] = "toy", rng.choice([1, 2])
            if rng.random() < 0.5:
                p["spoke_age"] = rng.uniform(0, 900)
            if jitter and rng.random() < 0.03:
                p.pop("ts")                               # a push with no stamp
            if p.get("asked"):
                asked_ts.append(float(p.get("ts") or 0))
            recs.append(p)
        elif x < 0.93:
            e = rng.choice([ALICE, ALICE, BOB, LAMP])
            r = {"ts": t, "kind": "reach", "n": i, "eid": e, "p": rng.random(),
                 "answer": rng.choice(["changed", "unchanged", "unchanged", None])}
            if rng.random() < 0.08:
                r["audit"] = True
            if rng.random() < 0.5:
                r["reply"] = True
                r["replied"] = rng.random() < 0.35
                if rng.random() < 0.1:
                    r.pop("replied")                      # a check from before #258
                if rng.random() < 0.7:
                    r["p_self"] = round(rng.uniform(0.05, 0.95), 4)
                if rng.random() < 0.5:
                    r["p_llm"] = round(rng.uniform(0.01, 0.99), 3)
                    r["llm_model"] = rng.choice(["m-a", "m-b"])
                    if rng.random() < 0.5:
                        r["llm_cal"] = [round(rng.uniform(-1, 1), 4), round(rng.uniform(0, 1), 4)]
                        r["p_mix"] = round(rng.uniform(0.05, 0.95), 4)
                if rng.random() < 0.2:
                    r["medium"], r["clock"] = "toy", rng.choice([1, 2])
            recs.append(r)
        elif x < 0.96:
            d = {"turn": rng.choice(asked_ts), "to": ALICE} if asked_ts and rng.random() < 0.7 else {}
            recs.append({"ts": t, "kind": "sleep", "dropped": d})
        elif x < 0.98:
            recs.append({"ts": t, "kind": "rung", "rung": rng.randrange(1, 9), "voided": rng.random() < 0.3})
        else:
            recs.append({"ts": t, "kind": "rung_void", "rung": rng.randrange(1, 9), "why": "test"})
    return recs


def _ask(ts, to=ALICE, **kw):
    return {"ts": ts, "kind": "push", "to": to, "asked": True, **kw}


def _look(ts, eid=ALICE, **kw):
    return {"ts": ts, "kind": "reach", "eid": eid, **kw}


# the old code's outputs, pinned (#346): name -> (record, its _rung8_stats)
_EMPTY = {"asked": 0, "checked": 0, "paired": 0, "pair_rate": None, "checks": 0, "brier_self": None,
          "brier_mix": None, "brier_rate": None, "brier_model": None, "advisor_weight": 0.0,
          "advisor_model": None, "base_rate": None, "recent_rate": None}


def _cases():
    return {
        "empty": [],
        "no_push": [_look(T0, answer="changed"), {"ts": T0 + 1, "kind": "cycle"}],
        "asked_never_looked": [_ask(T0), _look(T0 + 5, BOB, answer="changed")],
        "looks_before_and_after": [
            _look(T0, answer="changed", p_llm=0.4), _ask(T0 + 10),
            _look(T0 + 10, answer="changed", p_llm=0.9),           # same stamp: not after
            _look(T0 + 20, answer="unchanged", p_llm=0.2, llm_model="m", reply=True, replied=False, p_self=0.3),
            _ask(T0 + 30), _look(T0 + 40, answer=None, p_llm=0.5),
            _ask(T0 + 50), _look(T0 + 60, answer="changed", reply=True, replied=True, p_self=0.6),
        ],
        "out_of_order_stamps": [
            _ask(T0 + 100), _look(T0 + 50, answer="changed", p_llm=0.1),
            _look(T0 + 150, answer="unchanged", p_llm=0.7), _ask(T0 + 60),
            _ask(T0 + 200), _look(T0 + 120, answer="changed", p_llm=0.3),
        ],
        "audit_and_missing_stamps": [
            {"kind": "push", "to": ALICE, "asked": True}, _look(None, answer="changed", p_llm=0.5),
            _ask(T0), _look(T0 + 1, answer="changed", audit=True, p_llm=0.5),
        ],
        "dropped_at_sleep": [
            _ask(T0), {"ts": T0 + 5, "kind": "sleep", "dropped": {"turn": T0, "to": ALICE}},
            _look(T0 + 10, answer="changed", p_llm=0.5), _ask(T0 + 20),
            {"ts": T0 + 25, "kind": "sleep", "dropped": {}},
            _look(T0 + 30, answer="unchanged", p_llm=0.5, reply=True, replied=True, p_self=0.4),
        ],
        "voided_rungs": [
            {"ts": T0, "kind": "rung", "rung": 8, "voided": True}, {"ts": T0 + 1, "kind": "rung_void", "rung": 8},
            _ask(T0 + 2), _look(T0 + 3, answer="changed", p_llm=0.8, llm_model="m", reply=True, replied=True,
                                p_self=0.7, llm_cal=[0.1, 0.5], p_mix=0.75),
            {"ts": T0 + 4, "kind": "rung", "rung": 8}, {"ts": T0 + 5, "kind": "rung_void", "rung": 8, "why": "x"},
        ],
        "other_switches": [
            _ask(T0, ALICE), _look(T0 + 1, ALICE, answer="changed", p_llm=0.5),
            _ask(T0 + 2, BOB), _look(T0 + 3, BOB, answer="unchanged", p_llm=0.5),
            _ask(T0 + 4, BOB), _look(T0 + 5, ALICE, answer="changed"),
        ],
        "summary_only": [{"kind": SL.SUMMARY, "id": "s", "at": T0, "n": 0, "stalls": [],
                          "carry": {"push": 4, "to": {ALICE: {"asked": 4, "checked": 3, "paired": 1}}}}],
        "summary_then_lines": _life(7, 60, summary=True),
        "random_a": _life(1, 400),
        "random_b": _life(2, 400, summary=True),
        "random_c": _life(3, 900, jitter=False),
    }


PINNED = json.loads(r"""{
 "asked_never_looked": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 1,
  "base_rate": 0.5,
  "brier_mix": null,
  "brier_model": null,
  "brier_rate": null,
  "brier_self": null,
  "checked": 0,
  "checks": 0,
  "pair_rate": null,
  "paired": 0,
  "recent_rate": 0.5
 },
 "audit_and_missing_stamps": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 2,
  "base_rate": 0.5,
  "brier_mix": null,
  "brier_model": null,
  "brier_rate": null,
  "brier_self": null,
  "checked": 2,
  "checks": 0,
  "pair_rate": 1.0,
  "paired": 2,
  "recent_rate": 0.5
 },
 "dropped_at_sleep": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 2,
  "base_rate": 0.75,
  "brier_mix": 0.36,
  "brier_model": 0.25,
  "brier_rate": 0.25,
  "brier_self": 0.36,
  "checked": 1,
  "checks": 1,
  "pair_rate": 1.0,
  "paired": 1,
  "recent_rate": 0.75
 },
 "empty": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 0,
  "base_rate": null,
  "brier_mix": null,
  "brier_model": null,
  "brier_rate": null,
  "brier_self": null,
  "checked": 0,
  "checks": 0,
  "pair_rate": null,
  "paired": 0,
  "recent_rate": null
 },
 "looks_before_and_after": {
  "advisor_model": "m",
  "advisor_weight": 0.0,
  "asked": 3,
  "base_rate": 0.5,
  "brier_mix": 0.125,
  "brier_model": 0.04,
  "brier_rate": 0.4062,
  "brier_self": 0.125,
  "checked": 3,
  "checks": 2,
  "pair_rate": 0.3333,
  "paired": 1,
  "recent_rate": 0.5
 },
 "no_push": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 0,
  "base_rate": null,
  "brier_mix": null,
  "brier_model": null,
  "brier_rate": null,
  "brier_self": null,
  "checked": 0,
  "checks": 0,
  "pair_rate": null,
  "paired": 0,
  "recent_rate": null
 },
 "other_switches": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 2,
  "base_rate": 0.5,
  "brier_mix": null,
  "brier_model": null,
  "brier_rate": null,
  "brier_self": null,
  "checked": 1,
  "checks": 0,
  "pair_rate": 1.0,
  "paired": 1,
  "recent_rate": 0.5
 },
 "out_of_order_stamps": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 3,
  "base_rate": 0.5,
  "brier_mix": null,
  "brier_model": null,
  "brier_rate": null,
  "brier_self": null,
  "checked": 2,
  "checks": 0,
  "pair_rate": 1.0,
  "paired": 2,
  "recent_rate": 0.5
 },
 "random_a": {
  "advisor_model": "m-a",
  "advisor_weight": 0.0363,
  "asked": 14,
  "base_rate": 0.2647,
  "brier_mix": 0.3395,
  "brier_model": 0.3641,
  "brier_rate": 0.2317,
  "brier_self": 0.2734,
  "checked": 12,
  "checks": 16,
  "pair_rate": 0.1667,
  "paired": 2,
  "recent_rate": 0.2143
 },
 "random_b": {
  "advisor_model": "m-a",
  "advisor_weight": 0.0,
  "asked": 36,
  "base_rate": 0.625,
  "brier_mix": 0.2844,
  "brier_model": 0.16,
  "brier_rate": 0.3356,
  "brier_self": 0.127,
  "checked": 28,
  "checks": 3,
  "pair_rate": 0.1786,
  "paired": 5,
  "recent_rate": 0.625
 },
 "random_c": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 25,
  "base_rate": 0.1667,
  "brier_mix": 0.4181,
  "brier_model": null,
  "brier_rate": 0.1562,
  "brier_self": 0.4181,
  "checked": 20,
  "checks": 2,
  "pair_rate": 0.05,
  "paired": 1,
  "recent_rate": 0.1667
 },
 "summary_only": {
  "advisor_model": null,
  "advisor_weight": 0.0,
  "asked": 0,
  "base_rate": null,
  "brier_mix": null,
  "brier_model": null,
  "brier_rate": null,
  "brier_self": null,
  "checked": 0,
  "checks": 0,
  "pair_rate": null,
  "paired": 0,
  "recent_rate": null
 },
 "summary_then_lines": {
  "advisor_model": "m-b",
  "advisor_weight": 0.0,
  "asked": 11,
  "base_rate": 0.2812,
  "brier_mix": 0.4396,
  "brier_model": 0.0418,
  "brier_rate": 0.1134,
  "brier_self": 0.249,
  "checked": 9,
  "checks": 4,
  "pair_rate": 0.3333,
  "paired": 3,
  "recent_rate": 0.1
 },
 "voided_rungs": {
  "advisor_model": "m",
  "advisor_weight": 0.0,
  "asked": 1,
  "base_rate": 0.75,
  "brier_mix": 0.0625,
  "brier_model": 0.04,
  "brier_rate": 0.25,
  "brier_self": 0.09,
  "checked": 1,
  "checks": 1,
  "pair_rate": 1.0,
  "paired": 1,
  "recent_rate": 0.75
 }
}""")


class TestRung8StatsUnchanged(unittest.TestCase):
    def test_pinned_outputs(self):
        cases = _cases()
        self.assertEqual(set(PINNED), set(cases))
        for name, recs in cases.items():
            with self.subTest(name):
                self.assertEqual(SL._rung8_stats(recs), PINNED[name])

    def test_empty_record(self):
        self.assertEqual(SL._rung8_stats([]), _EMPTY)

    def test_same_as_the_old_scan_on_random_records(self):
        for seed in range(60):
            recs = _life(1000 + seed, random.Random(seed).randrange(0, 500), summary=seed % 3 == 0,
                         jitter=seed % 2 == 0)
            with self.subTest(seed=seed):
                self.assertEqual(SL._rung8_stats(recs), _old_rung8_stats(recs))


class _Counted(dict):
    """A record line that counts how often a reader looks into it."""
    gets = 0

    def get(self, *a):
        _Counted.gets += 1
        return dict.get(self, *a)


class TestRung8StatsScalesLinearly(unittest.TestCase):
    """The reads per line must stay flat as the record grows: 4x the lines
    (and 4x the asks) may cost about 4x the reads, never the old 16x."""

    def _reads(self, n):
        recs = [_Counted(r) for r in _life(5, n, jitter=False)]
        _Counted.gets = 0
        SL._rung8_stats(recs)
        return _Counted.gets

    def test_four_times_the_record_costs_about_four_times(self):
        small, big = self._reads(1500), self._reads(6000)
        self.assertLess(big / small, 6.0, (small, big))


if __name__ == "__main__":
    unittest.main()
