"""#345 part 2: consolidation -- nothing raw stays through sleep.

At the moment Blurp falls asleep every raw line of the waking period moves,
unchanged, to its dated archive (self/archive/record-<UTC date of its own
ts>.jsonl), and the working record is emptied down to ONE summary line: the
windows its tests read (copies of archived lines) and the few counts no
window can give back (the cycle count, the all-time counts the panel shows,
the reply checks before the windows). Readers see summary + new lines
exactly as they saw the whole record within their windows (the equivalence
tests below run every test, skill and sheet function on a long synthetic
record before and after). Anything open at sleep -- a turn awaiting its
reply check -- is dropped and named on the sleep line; a dropped turn is not
a missed reply. A seat that dies mid-consolidation finishes it on load: no
line lost, none doubled. Every time here is injected: nothing sleeps.
"""
import json
import os
import random
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import energy as E  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import toy_unlocks as TU  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

T0 = 1_760_000_000.0                  # 2025-10-09 08:53:20 UTC
FREE = 1_000_000_000
C = MEMBRANE_NS + "Component:"
ALICE = C + "chat:in:alice"
BOB = C + "chat:in:bob"
BALL = C + "toy:ball"
THINGS = [C + "toy:die", C + "toy:tower", C + "toy:slow", C + "toy:fast", C + "w:lamp", BALL, ALICE]


class Crash(BaseException):
    """The seat dying mid-step (not an error the code may catch)."""


def _env():
    d = tempfile.mkdtemp()
    base = {k: v for k, v in os.environ.items() if k not in ("MEMBRANE_SELF_NAME", "MEMBRANE_DATA_DIR")}
    base["MEMBRANE_DATA_DIR"] = d
    return mock.patch.dict(os.environ, base, clear=True)


def _seat(d=None):
    d = Path(d or tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv, d


def _fresh(srv, d):
    """The same seat after a restart: a new runtime, the files as they are."""
    SL._RT.pop(id(srv), None)
    return _seat(d)


_ram = None


def setUpModule():
    global _ram
    _ram = mock.patch.object(E, "_avail_ram", return_value=None)
    _ram.start()


def tearDownModule():
    _ram.stop()


# ---- a long synthetic record: every kind of line the readers read ----------------------

class Life:
    """A seeded Blurp's record, written step by step: cycles, looks at seven
    things (timed, steered, noticing, audits), turns in chat and with the
    ball and their reply checks (self's p, the advisor's), words, the mirror,
    shifts, dreams and their checks, passes, accepts, unlocks, voids, waits,
    errors, stalls and restarts."""

    def __init__(self, seed=345):
        self.rng = random.Random(seed)
        self.ts = T0
        self.n = 0
        self.lines = []
        self.last_ans = {}              # eid -> its latest answered look (no audit)
        self.pending = None             # a turn awaiting its check: (push, due step)
        self.change_ts = []
        self.settings = {"lam_looks": 30, "lam_prior_looks": 3.0}
        self.mirror_k = 0
        self.at = {}                    # step -> its ts (the passes the #333 voids name)
        self.lines.append({"ts": self.ts, "kind": "birth", "by": "operator"})

    def put(self, rec):
        self.lines.append(rec)
        return rec

    def phase(self):
        return "chat" if self.n < 2400 else ("toy" if self.n < 4400 else "speak")

    def step(self):
        r = self.rng
        self.n += 1
        n = self.n
        gap = 10.0
        if n % 700 == 0:
            gap = 2400.0                # a restart: a stall in the record
        elif n % 333 == 0:
            gap = 150.0                 # a short pause (the sheet's gap)
        self.ts += gap
        ts = self.ts
        still = r.random() < 0.05
        cyc = {"ts": ts, "kind": "cycle", "n": n, "act": "move", "step": 0.1, "push": r.random() * 0.2,
               "gap": r.random() * 0.1, "naive": r.random() * 0.3 + 0.01,
               "change": "still" if still else ("world" if r.random() < 0.8 else "knowledge")}
        if still:
            cyc["still"] = True
        elif n > 60:
            cyc.update({"aim_n": n - n % 30, "aim_err": r.random() * 0.2, "aim_naive": r.random() * 0.3 + 0.01})
        self.put(cyc)
        self.special(n, ts)
        self.turn(n, ts)
        if self.rng.random() < 0.92:
            self.look(n, ts)
        if n > 900:
            self.mirror(n, ts)
        if n > 500 and n % 37 == 0:
            self.dream(n, ts)
        if r.random() < 0.004:
            self.put({"ts": ts, "kind": "shift", "eid": r.choice(THINGS[:5]), "was": 0.5, "now": 0.1, "looks": 3})
        if r.random() < 0.01:
            self.put({"ts": ts, "kind": "wait", "n": n})
        if r.random() < 0.003:
            self.put({"ts": ts, "kind": "error", "n": n, "act": "reach", "why": "boom"})

    def special(self, n, ts):
        """The rare lines: passes, accepts, unlocks, voids, audits, fixes."""
        rungs = {80: ("feel", 1), 300: ("reach", 2), 520: ("kinds", 3), 700: ("patience", 4),
                 760: ("shift", 5), 820: ("meet", 6), 2100: ("turns", 7), 2600: ("expect", 8),
                 2900: ("mirror", 9)}
        if n == 40:
            self.put({"ts": ts, "kind": "accept_migrated", "ids": []})
        if n in rungs:
            i, k = rungs[n]
            self.put({"ts": ts, "kind": "achieve", "id": i, "rung": k, "skill": 0.5})
            self.put({"ts": ts + 1, "kind": "accept", "id": i, "by": "operator"})
            if k in (3, 4, 5, 6):
                self.put({"ts": ts + 1, "kind": "unlock", "rung": k, "id": i, "toys": [["tower", "burst", "die", "ball"][k - 3]]})
        if n == 600:
            self.put({"ts": ts, "kind": "rung", "rung": 7, "skill": 0.1})          # an old ladder's pass
            self.put({"ts": ts, "kind": "rung_void", "rung": 7, "voids_ts": ts, "why": "#254"})
        if n == 3000:                   # #333: the old wiring's passes, voided once, as a seat's boot writes them
            for k, at in ((6, 820), (7, 2100), (8, 2600), (9, 2900)):
                self.put({"ts": ts, "kind": "rung_void", "rung": k, "id": SL.RUNG_ID[k], "voids_ts": self.at[at],
                          "issue": 333, "why": "#333"})
        if n in rungs:
            self.at[n] = ts
        if n == 830:
            self.put({"ts": ts, "kind": "rung7_restart", "issue": 254, "why": "x"})
            self.put({"ts": ts, "kind": "fix", "issue": 259, "why": "x"})
        if n == 250:
            self.put({"ts": ts, "kind": "audit", "phase": "start", "eid": THINGS[0], "rung": 2})
        if n == 270:
            self.put({"ts": ts, "kind": "audit", "phase": "end", "eid": THINGS[0], "reaches": 3, "changes": 0,
                      "passed": True})
        if n == 740:
            self.put({"ts": ts, "kind": "audit", "rung": 5, "phase": "end", "eid": THINGS[1], "still": True,
                      "again": True, "passed": True})

    def look(self, n, ts, eid=None, reply=None):
        r = self.rng
        eid = eid or r.choice(THINGS[:5] if self.phase() != "chat" or r.random() < 0.7 else THINGS)
        prev = self.last_ans.get(eid)
        answer = "none" if r.random() < 0.08 else ("changed" if r.random() < 0.45 else "unchanged")
        rec = {"ts": ts, "kind": "reach", "n": n, "eid": eid, "p": round(r.random(), 4), "answer": answer,
               "why": "" if answer != "none" else "no reading",
               "dt": round(ts - prev["ts"], 3) if prev is not None else None, "fid": "f:" + eid[-4:]}
        if n > 300 and reply is None and r.random() < 0.7:
            rec["timed"] = True
            rec["lam"] = 0.001
        if n > 200:
            rec["steer"] = True
        if n > 400:
            rec["watch5"] = True
        if r.random() < 0.01:
            rec["audit"] = True
        elif r.random() < 0.01:
            rec["audit5"] = "pause"
        if "toy:" in eid and r.random() < 0.5:
            rec["by"] = ["self"]
            rec["log_at"] = n
        if reply is not None:
            push, replied = reply
            rec["reply"] = True
            rec["replied"] = replied
            if push.get("medium") == "toy":
                rec["medium"] = "toy"
                rec["clock"] = 2
            ball = push.get("medium") == "toy"          # the ball's checks: now and then one recorded
            if n > 450 and (not ball or r.random() < 0.95):    # without its own p (as before #254)
                rec["p_self"] = round(r.random(), 4)
            if push.get("asked") and r.random() < 0.6:
                rec["p_llm"] = round(r.random(), 4)
                rec["llm_model"] = r.choice(["m1", "m2"] * 20 + (["m3"] if push.get("medium") == "toy" else []))
                # m3: a rare advisor of the ball's turns -- its last checks lie far back
                if n > 700 and (not ball or r.random() < 0.95):
                    rec["llm_cal"] = [0.1, 0.2]
                    rec["p_mix"] = round(r.random(), 4)
        if 1000 < n < 2500 and reply is None and n % 4 == 0:   # #333 part 3b: nudge checks, long ago (no draw)
            rec["nudged"] = 1 + (n // 4) % 2
            rec["p_n"] = round((n * 37 % 100) / 100, 4)
        self.put(rec)
        if answer in ("changed", "unchanged") and not rec.get("audit"):
            self.last_ans[eid] = rec
        return rec

    def turn(self, n, ts):
        r = self.rng
        if self.pending is not None:
            push, due = self.pending
            if n >= due:
                self.pending = None
                if r.random() < 0.95:               # the check (a few never come: stale)
                    self.look(n, ts + 1, push["to"], (push, r.random() < 0.45))
            return
        ph = self.phase()
        every = 3 if ph != "speak" else 4
        if n < 100 or n % every:
            return
        to = BALL if ph == "toy" else (BOB if (ph == "chat" and r.random() < 0.15) else ALICE)
        push = {"ts": ts + 2, "kind": "push", "n": n, "eid": "membrane::Config:chat:out", "to": to}
        if ph == "toy":
            push.update({"medium": "toy", "clock": 2, "eid": MEMBRANE_NS + "self", "toy": "ball"})
        if n > 300:
            b = r.randrange(4)
            push.update({"bin": b, "aim": r.randrange(4), "best": r.choice([None, 0, 1, 2, 3]),
                         "dwell": [r.randrange(3) for _ in range(4)], "why": "aim"})
        if r.random() < 0.8:
            push["spoke_age"] = round(r.random() * 900, 1)
        if n > 500:
            push["asked"] = r.random() < 0.7
        if ph == "speak":
            about = next((x for x in reversed(self.lines) if x.get("kind") == "reach"
                          and x.get("answer") in ("changed", "unchanged") and not x.get("audit")
                          and x.get("eid") not in (to, MEMBRANE_NS + "self")), None)
            coin = r.random()
            if about is None or coin < 0.1:
                push["speech"] = "mute"
            elif coin < 0.55:
                ab = {"eid": about["eid"], "answer": about["answer"], "ts": about["ts"]}
                if r.random() < 0.03:
                    ab["ts"] = about["ts"] - 5          # a false utterance now and then
                push.update({"speech": "words", "words": SL._speak_words(ab["eid"], ab["answer"]), "about": ab})
            else:
                push["speech"] = "plain"
        self.put(push)
        if to == BOB and r.random() < 0.5:
            return                                  # bob is never looked at again: no check, ever
        self.pending = (push, n + r.choice([1, 2, 3]))

    def mirror(self, n, ts):
        r = self.rng
        self.mirror_k += 1
        toy = "mirror" if self.mirror_k % 50 else "mirror2"
        rec = {"ts": ts, "kind": "mirror", "n": n, "toy": toy, "s": round(r.gauss(0, 1), 4),
               "glass": round(r.gauss(0, 1), 4), "stranger": round(r.gauss(0, 1), 4)}
        if self.mirror_k > 25:
            for side in ("glass", "stranger"):
                rec["p_" + side] = round(r.gauss(0, 1), 4)
                rec["b_" + side] = round(r.gauss(0, 1), 4)
        self.put(rec)

    def dream(self, n, ts):
        r = self.rng
        s = r.choice(list(SL.CHANGE_SETTINGS))
        grid = SL.CHANGE_SETTINGS[s]
        cur = self.settings[s]
        i = grid.index(cur)
        to = grid[i + 1] if i + 1 < len(grid) else grid[i - 1]
        adopt = r.random() < 0.06
        rec = {"ts": ts, "kind": "change", "setting": s, "from": cur, "to": to, "base": dict(self.settings),
               "looks": 1000, "brier_from": 0.2, "brier_to": 0.19, "gain": 0.002 if adopt else -0.001,
               "z": 4.0 if adopt else 0.5, "verdict": "adopted" if adopt else "rejected", "reason": "x", "n": n}
        if r.random() < 0.02:
            rec["setting"] = "bogus"
        self.put(rec)
        if adopt:
            self.settings[s] = to
        self.change_ts.append(ts)
        if len(self.change_ts) > 30 and r.random() < 0.6:   # a check of an old proposal (some stay unchecked)
            of = self.change_ts[-30]
            self.put({"ts": ts, "kind": "change_check", "of": of, "setting": s, "from": cur, "to": to,
                      "verdict": "rejected", "first": of + 1, "looks": 1000, "brier_from": 0.2, "brier_to": 0.2,
                      "gain": r.random() * 0.002 - 0.001, "z": 1.0, "blocks": [0.001] * 10, "miss": False})

    def take(self, steps):
        k = len(self.lines)
        for _ in range(steps):
            self.step()
        return self.lines[k:]


def _write(d, lines):
    p = Path(d) / "self" / "record.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")


STEPS = 6000


def _twin(steps=STEPS, seed=345):
    """Two seats with the same long record: A keeps it whole, B consolidates."""
    life = Life(seed)
    life.take(steps)
    a, da = _seat()
    b, db = _seat()
    _write(da, life.lines)
    _write(db, life.lines)
    SL._RT.pop(id(a), None)
    SL._RT.pop(id(b), None)
    a, _ = _seat(da)
    b, _ = _seat(db)
    from membrane import self_world as SW
    from membrane import toybox as TB
    for kind in ("die", "slow"):                       # toys on both seats (one toybox: the data dir)
        TB.add(a, kind, locked_ok=True)
        SW.join(b, [TB._put(b.entity_store, "toy:" + kind, kind, n=0)], "toy")
    # TB.add saves the store; the bare _put only arms its 10 s save debounce, so a
    # restart of B inside those 10 s read a store without its toys (#408): flush
    # it, as TB.add does, so the files are what the seat holds
    b.entity_store.flush()
    return life, (a, da), (b, db)


# ---- every reader, before and after ------------------------------------------------------

TEMPO = {e: {"changed": 5, "answered": 12} for e in THINGS}


def _strip(x):
    """A value fit for == (numpy scalars to floats, tuples to lists)."""
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_strip(v) for v in x]
    if hasattr(x, "tolist"):
        return x.tolist()
    return x


def _readers(srv, now):
    """What every test, skill and panel function says over this seat's record."""
    recs = SL._read(srv)
    out = {}
    for n, fn in SL.PROGRESS.items():
        out[f"rung{n}"] = fn(recs, TEMPO)
    out["passed"] = sorted(SL._passed(recs))
    out["earn_times"] = SL._earn_times(recs)
    out["earned_rows"] = SL._earned_rows(recs)
    out["accepted"] = sorted(SL._accepted(recs))
    out["opens"] = sorted(SL._opens(recs))
    out["open_ids"] = SL._open_ids(recs)
    out["awaiting"] = SL._awaiting(recs)
    out["other"] = SL._other(recs)
    out["cycles"] = SL._cycles(recs)
    out["dreams"] = SL._dreams(recs)
    out["kinds"] = SL._kinds(recs)
    out["split"] = SL._split(recs, TEMPO)
    out["rung2_stats"] = SL._rung2_stats(recs, TEMPO)
    out["progress"] = SL._progress(recs, detail=True)
    out["curiosity"] = SL._curiosity(recs, THINGS)
    out["curiosity_stats"] = SL._curiosity_stats(recs)
    out["nudge_progress"] = SL._nudge_progress(recs, detail=True)    # #333 part 3b: the aims' tilt
    out["aim_tilt"] = SL._aim_tilt(recs, THINGS)
    out["checks"] = {e: v[-2 * SL.NUDGE_CURIO_WINDOW:] for e, v in SL._check_errors(recs).items()}
    out["steer_stats"] = SL._steer_stats(recs)
    out["rung7_stats"] = SL._rung7_stats(recs)
    out["rung8_stats"] = SL._rung8_stats(recs)
    out["reply_check_stats"] = SL._reply_check_stats(recs)    # #333 part 3f: the old wiring's rung 8, as history
    out["speak_stats"] = SL._speak_stats(recs)
    out["latest_unlock"] = SL._latest_unlock(recs)
    out["candidate"] = SL._candidate(recs, {"lam_looks": 30, "lam_prior_looks": 3.0}, 3)
    out["dream"] = SL._dream(recs, {"lam_looks": 30, "lam_prior_looks": 3.0}, now, SL._dreams(recs))
    idx = SL._rate_looks(recs)
    out["rate_looks"] = [recs[i] for i in idx[-SL.CHANGE_LOOKS:]]
    errs = SL._ordinary_errors(recs)
    out["ordinary"] = {e: v[-2 * SL.CURIO_WINDOW:] for e, v in errs.items()}
    for t in ("mirror", "mirror2"):
        out["mirror_rows_" + t] = SL._mirror_rows(recs, t, SL.MIRROR_FIT + SL.MIRROR_OWN + SL.MIRROR_PAST)
    last_push = next((r for r in reversed(recs) if r.get("kind") == "push"), None)
    for k in range(4):
        out[f"pending{k}"] = SL._pending(recs, last_push["ts"] + 50 * k)
    for e in THINGS + [BOB]:
        o = out.setdefault("eid:" + e, {})
        grid, priors = SL.CHANGE_SETTINGS["lam_looks"], SL.CHANGE_SETTINGS["lam_prior_looks"]
        for w in (grid[0], SL.LAM_LOOKS, grid[-1]):           # the shortest, today's and the longest window
            for pr in (priors[0], priors[-1]):
                o[f"lam{w}/{pr}"] = SL._lam(recs, e, looks=w, prior=pr)
        o["shift"] = SL._last_shift_ts(recs, e)
        o["medium"] = SL._cur_medium(recs, e)
        for m in ("chat", "toy"):
            o["clock_" + m] = SL._cur_clock(recs, e, m)
            o["checks_" + m] = SL._reply_checks(recs, e, m)[-SL.RUNG8_LOOKS:]
            for c in (1, 2):
                o[f"turns_{m}{c}"] = SL._turn_rows(recs, e, m, c)[-(SL.RUNG7_NUDGES + SL.TURN_MEMORY):]
        o["base_rate"] = SL._base_rate(recs, e)
        o["recent_rate"] = SL._recent_rate(recs, e)
        series = SL._rung8_series(recs, e)
        o["series"] = series[-SL.RUNG8_LOOKS:]
        for model in ("m1", "m2", "m3"):
            o["cal_" + model] = SL._advisor_cal(series, model)
        if last_push is not None:
            o["p_self"] = SL._p_self(recs, e, {**last_push, "to": e})
        o["turn_score"] = SL._turn_score(SL._turn_rows(recs, e))
        o["speak_rows"] = SL._speak_rows(recs, e)[-SL.SPEAK_PUSHES:]
        o["speak_about"] = SL._speak_about(recs, e)
        o["last_changed"] = max((r["ts"] for r in recs if r.get("kind") == "reach" and r.get("eid") == e
                                 and r.get("answer") == "changed"), default=None)
        o["log_at"] = next((r.get("log_at") for r in reversed(recs) if r.get("kind") == "reach"
                            and r.get("eid") == e and r.get("log_at") is not None), None)
    out["tu"] = {"born": TU.born(recs), "opened": sorted(TU._opened(recs)), "latest": TU.latest(recs),
                 "by_rung": TU.by_rung(recs), "locked": TU.locked_rows(recs, {"ball"}),
                 "grand": TU.grandfathered(recs, "mirror"), "accept_ts": TU._accept_ts(recs)}
    out["recent"] = SL.recent(srv, SL.RECENT_MAX)
    out["last_line"] = next((r for r in reversed(recs) if r.get("kind") not in SL.OPERATOR_KINDS), None)
    with mock.patch("time.time", return_value=now):
        st = SL.status(srv)
    out["status"] = st
    return _strip(out)


def _panel(srv, now):
    """The sheet, the dock and the card (energy aside: the record's size is
    the one thing consolidation changes on purpose)."""
    with mock.patch("time.time", return_value=now):
        sh = dict(SS.compute(srv, now))
        dock = dict(SS.dock(srv, now, fresh=True))
        card = dict(SS.card(srv, now))
    sh.pop("energy", None)
    for k in ("energy", "asleep", "pull", "limiting", "record_cap"):
        card.pop(k, None)
    return _strip({"sheet": sh, "dock": dock, "card": card})


class TestEquivalence(unittest.TestCase):
    """Every reader returns the same before and after consolidation."""

    @classmethod
    def setUpClass(cls):
        cls.env = _env()
        cls.env.start()
        cls.life, (cls.a, cls.da), (cls.b, cls.db) = _twin()
        cls.now = cls.life.ts + 30
        cls.out = SL.consolidate(cls.b, cls.now)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()

    def assertSame(self, x, y):
        self.assertEqual(sorted(x), sorted(y))
        for k in x:
            self.assertEqual(x[k], y[k], k)

    def test_it_consolidated(self):
        self.assertIsNotNone(self.out)
        self.assertEqual(self.out["archived"], len(self.life.lines))
        raw = (self.db / "self" / "record.jsonl").read_text().splitlines()
        self.assertEqual(len(raw), 1)                            # one summary line, nothing raw
        self.assertEqual(json.loads(raw[0])["kind"], "summary")
        self.assertLess(SL.record_bytes(self.b), SL.record_bytes(self.a) / 2)

    def test_every_reader_is_the_same(self):
        self.assertSame(_readers(self.a, self.now), _readers(self.b, self.now))

    def test_the_sheet_dock_and_card_are_the_same(self):
        self.assertSame(_panel(self.a, self.now), _panel(self.b, self.now))

    def test_the_same_as_it_goes_on_and_sleeps_again(self):
        """New lines after the summary, then a second consolidation, then more."""
        life = Life(345)
        life.take(STEPS)
        more = life.take(500)
        for srv in (self.a, self.b):
            for rec in more:
                SL._append(srv, rec)
        now = life.ts + 30
        self.assertSame(_readers(self.a, now), _readers(self.b, now))
        SL.consolidate(self.b, now)                              # it sleeps again: only the new lines go
        more = life.take(300)
        for srv in (self.a, self.b):
            for rec in more:
                SL._append(srv, rec)
        now = life.ts + 30
        want = _readers(self.a, now)
        self.assertSame(want, _readers(self.b, now))
        self.assertSame(_panel(self.a, now), _panel(self.b, now))
        b2, _ = _fresh(self.b, self.db)                          # a restart reads the same summary back
        self.assertSame(want, _readers(b2, now))


# ---- the archive -----------------------------------------------------------------------

def _archive_text(d):
    """Every archived line in the manifest's order (the order they were written)."""
    arch = Path(d) / "self" / "archive"
    cur = {}
    out = []
    for ln in (arch / "manifest.jsonl").read_text().splitlines():
        for name, count in json.loads(ln)["runs"]:
            fh = cur.setdefault(name, iter((arch / name).read_text().splitlines()))
            out.extend(next(fh) for _ in range(count))
    return out


class TestTheArchive(unittest.TestCase):
    def setUp(self):
        self.env = _env()
        self.env.start()
        self.addCleanup(self.env.stop)

    def _seat_with(self, lines):
        srv, d = _seat()
        _write(d, lines)
        return _fresh(srv, d)

    def test_every_raw_line_moves_unchanged_to_its_own_day(self):
        life = Life(7)
        life.take(5000)                                      # ~19 h from 08:53 UTC: past midnight
        srv, d = self._seat_with(life.lines)
        before = (d / "self" / "record.jsonl").read_text().splitlines()
        SL.consolidate(srv, life.ts + 5)
        arch = d / "self" / "archive"
        days = sorted(p.name for p in arch.glob("record-*.jsonl"))
        self.assertGreater(len(days), 1)                     # the synthetic life spans days
        for name in days:
            for ln in (arch / name).read_text().splitlines():
                day = SL._day(json.loads(ln)["ts"])
                self.assertEqual(name, f"record-{day}.jsonl")
        self.assertEqual(_archive_text(d), before)           # byte for byte, in order, once

    def test_history_is_the_whole_record(self):
        life = Life(8)
        life.take(1200)
        srv, d = self._seat_with(life.lines)
        full = list(SL._read(srv))
        SL.consolidate(srv, life.ts + 5)
        more = life.take(100)
        for rec in more:
            SL._append(srv, rec)
        self.assertEqual(SL.history(srv), full + more)
        SL.consolidate(srv, life.ts + 5)                      # a second sleep archives only the new lines
        self.assertEqual(SL.history(srv), full + more)
        self.assertEqual(len(_archive_text(d)), len(full) + len(more))

    def test_the_activity_feed_never_shows_the_summary(self):
        life = Life(9)
        life.take(30)
        srv, d = self._seat_with(life.lines)
        want = SL.recent(srv, SL.RECENT_MAX)
        SL.consolidate(srv, life.ts + 5)
        self.assertEqual(SL.recent(srv, SL.RECENT_MAX), want)
        self.assertTrue(all(r.get("kind") != "summary" for r in SL.recent(srv, SL.RECENT_MAX)))

    def test_nothing_new_nothing_to_do(self):
        life = Life(10)
        life.take(200)
        srv, d = self._seat_with(life.lines)
        self.assertIsNotNone(SL.consolidate(srv, life.ts + 5))
        self.assertIsNone(SL.consolidate(srv, life.ts + 6))  # only the summary: nothing new to archive
        self.assertEqual(len(_archive_text(d)), len(life.lines))

    def test_nothing_on_the_cycle_path_reads_the_archive(self):
        life = Life(11)
        life.take(1500)
        srv, d = self._seat_with(life.lines)
        SL.consolidate(srv, life.ts + 5)
        SL._RT.pop(id(srv), None)
        srv, _ = _seat(d)
        SL._entity(srv).position = [1.0, 0.0, 0.0]               # somewhere to stand: a still world's cycle runs
        with mock.patch.object(SL, "_archive_lines", side_effect=AssertionError("the archive was read")), \
                mock.patch.object(SL, "history", side_effect=AssertionError("the archive was read")):
            SL.set_mode(srv, "on")
            SL.status(srv)
            for k in range(3):
                self.assertIsNotNone(SL.step(srv, now=life.ts + 60 + 20 * k))   # a cycle each, its own n
        cycles = [r for r in SL._read(srv) if r.get("kind") == "cycle"]
        self.assertEqual([r["n"] for r in cycles[-3:]], [life.n + 1, life.n + 2, life.n + 3])   # n continues


# ---- falling asleep consolidates; the record reserve recovers --------------------------

class TestSleepConsolidates(unittest.TestCase):
    def setUp(self):
        self.env = _env()
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_falling_asleep_archives_the_day(self):
        life = Life(12)
        life.take(800)
        srv, d = _seat()
        _write(d, life.lines)
        srv, _ = _fresh(srv, d)
        now = life.ts + 10
        E.tick(srv, now, free=FREE)
        E.tick(srv, now + 1, cpu_s=1 / E.CPU_PER_S, free=FREE)          # out of energy
        self.assertTrue(E.asleep(srv))
        raw = (d / "self" / "record.jsonl").read_text().splitlines()
        self.assertEqual(len(raw), 1)
        self.assertEqual(json.loads(raw[0])["kind"], "summary")
        sleep = [r for r in SL._read(srv) if r.get("kind") == "sleep"][-1]
        self.assertEqual(sleep["why"], "energy")
        self.assertIn("dropped", sleep)                           # what was open (TestOpenTurnsAreDropped)
        self.assertEqual(SL.history(srv)[-1], sleep)             # the sleep line itself is archived too

    def test_the_record_reserve_recovers_and_it_stays_awake(self):
        """Part 1's gap: a full record put it to sleep, it woke rested and fell
        asleep again at once. Now sleep empties the record."""
        life = Life(13)
        life.take(6000)
        srv, d = _seat()
        _write(d, life.lines)
        srv, _ = _fresh(srv, d)
        full = SL.record_bytes(srv)
        now = life.ts + 10
        E.tick(srv, now, free=FREE, avail=2 * full)               # the cap: the record as it is (half the RAM)
        self.assertTrue(E.asleep(srv))
        self.assertEqual([r for r in SL._read(srv) if r.get("kind") == "sleep"][-1]["why"], "record")
        self.assertLess(SL.record_bytes(srv), full / 2)
        E.tick(srv, now + 10, free=FREE, avail=2 * full)          # energy was full: it wakes rested
        self.assertFalse(E.asleep(srv))
        E.tick(srv, now + 20, free=FREE)
        self.assertFalse(E.asleep(srv))                           # ...and stays up: the record has room again
        self.assertGreater(E.report(srv)["reserves"]["record"], 0.5)


# ---- anything open at sleep is dropped --------------------------------------------------

class TestOpenTurnsAreDropped(unittest.TestCase):
    def setUp(self):
        self.env = _env()
        self.env.start()
        self.addCleanup(self.env.stop)

    def _seat_mid_turn(self):
        life = Life(14)
        life.take(1600)                                           # the turns with the ball are on
        srv, d = _seat()
        _write(d, life.lines)
        srv, _ = _fresh(srv, d)
        t = life.ts + 5
        push = {"ts": t, "kind": "push", "n": life.n + 1, "medium": "toy", "clock": 2, "eid": MEMBRANE_NS + "self",
                "to": BALL, "toy": "ball", "bin": 1, "aim": 1, "best": 1, "dwell": [1, 0, 0, 0], "why": "aim",
                "asked": True}
        SL._append(srv, push)
        self.assertIs(SL._pending(SL._read(srv), t + 10), SL._read(srv)[-1])
        return srv, d, push, t

    def test_a_turn_awaiting_its_check_is_dropped_and_named(self):
        srv, d, push, t = self._seat_mid_turn()
        stats0 = SL._reply_check_stats(SL._read(srv))
        rows0 = SL._turn_rows(SL._read(srv), BALL, "toy", 2)
        E.tick(srv, t + 10, free=FREE)
        E.tick(srv, t + 11, cpu_s=1 / E.CPU_PER_S, free=FREE)
        sleep = [r for r in SL._read(srv) if r.get("kind") == "sleep"][-1]
        self.assertEqual(sleep["dropped"], {"turn": push["ts"], "to": BALL})
        recs = SL._read(srv)
        self.assertIsNone(SL._pending(recs, t + 12))              # nothing awaits its check any more
        # after waking, its next look at the ball is a look, not a missed reply
        SL._append(srv, {"ts": t + 100, "kind": "reach", "n": 9999, "eid": BALL, "p": 0.4, "answer": "unchanged",
                         "why": "", "dt": 50.0})
        recs = SL._read(srv)
        self.assertEqual(SL._turn_rows(recs, BALL, "toy", 2), rows0)
        stats = SL._reply_check_stats(recs)
        self.assertEqual(stats["asked"], stats0["asked"])
        self.assertEqual((stats["checked"], stats["paired"]), (stats0["checked"], stats0["paired"]))

    def test_nothing_open_nothing_dropped(self):
        srv, d = _seat()
        E.tick(srv, T0, free=FREE)
        E.tick(srv, T0 + 1, cpu_s=1 / E.CPU_PER_S, free=FREE)
        self.assertEqual([r for r in SL._read(srv) if r.get("kind") == "sleep"][-1]["dropped"], {})


# ---- restart safety ----------------------------------------------------------------------

class TestRestartSafety(unittest.TestCase):
    """The seat dies at each step of a consolidation; on load it finishes it.
    No line is lost or doubled, in the working record or the archive."""

    def setUp(self):
        self.env = _env()
        self.env.start()
        self.addCleanup(self.env.stop)
        self.life = Life(15)
        self.life.take(1200)
        srv, self.d = _seat()
        _write(self.d, self.life.lines)
        self.srv, _ = _fresh(srv, self.d)
        self.full = list(SL._read(self.srv))
        self.raw = (self.d / "self" / "record.jsonl").read_text().splitlines()
        self.now = self.life.ts + 5

    def _check(self):
        srv, _ = _fresh(self.srv, self.d)
        self.assertFalse((self.d / "self" / SL.CONSOLIDATING).exists())
        lines = (self.d / "self" / "record.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["kind"], "summary")
        self.assertEqual(_archive_text(self.d), self.raw)          # each line archived exactly once
        self.assertEqual(SL.history(srv), self.full)
        return srv

    def _die_at(self, name):
        with mock.patch.object(SL, name, side_effect=Crash()):
            with self.assertRaises(Crash):
                SL.consolidate(self.srv, self.now)

    def test_dies_while_archiving(self):
        real = SL._c_append

        def half(path, lines):                                 # the first day's lines land, then it dies
            real(path, lines)
            raise Crash()
        with mock.patch.object(SL, "_c_append", side_effect=half):
            with self.assertRaises(Crash):
                SL.consolidate(self.srv, self.now)
        self._check()

    def test_dies_before_the_manifest(self):
        self._die_at("_c_manifest")
        self._check()

    def test_dies_before_the_swap(self):
        self._die_at("_c_swap")
        self._check()

    def test_dies_after_the_swap(self):
        self._die_at("_c_done")
        self._check()

    def test_an_error_leaves_the_record_whole(self):
        with mock.patch.object(SL, "_c_manifest", side_effect=OSError("disk full")):
            self.assertIsNone(SL.consolidate(self.srv, self.now))
        self.assertEqual((self.d / "self" / "record.jsonl").read_text().splitlines(), self.raw)
        self.assertFalse((self.d / "self" / SL.CONSOLIDATING).exists())
        self.assertEqual(SL._read(self.srv), self.full)
        arch = self.d / "self" / "archive"
        self.assertEqual(sum(len(p.read_text().splitlines()) for p in arch.glob("record-*.jsonl")), 0)
        SL.consolidate(self.srv, self.now)                        # the next sleep does it
        self._check()


if __name__ == "__main__":
    unittest.main()
