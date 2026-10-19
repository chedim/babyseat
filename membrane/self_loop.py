"""THE SELF LOOP (#161, rungs 0 + 1 of the skill ladder).

membrane::self is a looped cliffhanger object, unnamed, born OFF. Three
methods -- try / expect / notice -- run in its own loop and nowhere else (any
other caller is refused: the LLM talks TO self, never AS it).

  rung 0  sleep and wake: a per-seat switch (off / on / watch), the
          operator's (seat_panel.control), off by default. Off runs nothing
          and erases nothing; the mode and the record survive a restart.
  rung 1  feel itself -- the amoeba's problem. Its only act moves its own
          position on the field, and the world pushes it while it moves: the
          field's CURRENT (the live conversation's running context), which
          self does not control. Where it meant to go and where it ended up
          differ by the push it did not account for. `expect` learns the
          current from its own moves -- where it is pulled to and how hard
          (push ~ b - g*position, running estimates kept in its own model
          file, <data dir>/self/model.json: entity properties cap lists at
          128 items on save, which once cut a 1024-d model and made self
          forget its world at a restart) -- and the gap that is left is the
          signal: it closes
          as the current is learned and reopens when the world changes (a
          topic shift). Rung 1 passes when experience measurably shrinks the
          gap against not accounting for the push at all.

THE RECORD: <data dir>/self/record.jsonl, append-only, never truncated,
starting at birth (the infancy rule: nothing before it is "mine"). Scalars
only -- the vectors stay on the entity.

THE LOOP is change-driven: each notice changes self's state and wakes the
next cycle. The runaway guard: PACE_S between cycles (MEMBRANE_SELF_PACE_S,
floor 1 s), at most BURST_PER_MIN a minute, and three failed cycles in a row
hold it until the operator's next toggle. A paused seat runs nothing.

THE LADDER: a rung opens when its measured test passes; the bar is staged
(_staged: the criteria fill in order, the first unmet one is named) and can
go down (slipping) -- a passed rung stays on the record. Only rungs in LADDER are built; the status says when the next is not.

THE GROWTH LIMIT (the operator's chooser): the highest rung self may reach.
Below it self still earns each rung by its test; above it nothing opens,
however well it does ("capped"). 0 is off. Raising it never grants a rung.
"""
from __future__ import annotations

import json
import os
import shlex
import threading
import time
from pathlib import Path

import numpy as np

ENT = "self"
MODES = ("off", "on", "watch")
METHODS = {
    "try": "self::try {{seed}}",
    "expect": "self::expect {{seed}}",
    "notice": "self::notice {{seed}}",
    "reach": "self::reach {{target}}",
    "meet": "self::meet {{to}}",
}
RUNG_NAMES = {0: "sleep and wake", 1: "feel itself", 2: "first reach", 3: "tell things apart",
              4: "patience", 5: "notice the world changed", 6: "meet an other",
              7: "take turns", 8: "understand an other", 9: "recognize itself", 10: "change itself",
              11: "speak"}
LADDER = {n: RUNG_NAMES[n] for n in (0, 1, 2, 3, 4, 5, 6, 7, 8)}          # the rungs built so far
# THE LADDER FLIP (#258): rung 7 was "understand an other" until the turn-taking rung went in
# below it. A rung line above 6 counts only when it carries "ladder": LADDER_V -- a pass
# recorded before the flip was the old numbering's (the record is never rewritten).
LADDER_V = 2
FLIPPED_FROM = 7

try:
    PACE_S = max(1.0, float(os.environ.get("MEMBRANE_SELF_PACE_S") or 10.0))
except ValueError:
    PACE_S = 10.0
BURST_PER_MIN = 30
HOLD_AFTER = 3              # failed cycles in a row
STEP = 0.1                  # how far self pushes itself per try (its babble)
AIM_HOLD = 30               # cycles self holds an aim before choosing the next (steer, after rung 1)
AIM_REACH = 0.3             # how far from where it is an aim lies
STEP_MAX = 0.3              # its longest stride when it steers (leaning into a 0.2 pull needs more than 0.1)
PUSH_GAIN = 0.2             # how hard the current pulls it toward itself
LEARN = 0.2                 # how fast expect's model of the current follows what it observes
CALM = 1e-6                 # a push under this is no push (a quiet world)
RUNG1_CYCLES = 20           # pushed cycles in the window the test reads
RUNG1_SKILL = 0.5           # 1 - gap/naive over that window
RUNG2_REACHES = 20          # answered reaches of one entity the test reads
RUNG2_SKILL = 0.2           # Brier skill against the seat-wide change rate
AUDIT_P = 0.6               # an entity must move this often to be audited (a still one can't show a mark fail)
AUDIT_REACHES = 3           # reaches of it while its getter is paused
AUDIT_MAX_S = 600.0         # the pause's cap (an audit that runs out is inconclusive)
KIND_MIN = 5                # looks before a thing has a kind
RUNG3_WINDOW = 80           # reaches the kinds are read over (its two halves must agree)
RUNG3_LOOKS = 40            # sorted reaches before the test reads the focus
RUNG3_FOCUS = 0.25          # the no-answer share it must get under
RUNG3_DRIFT = 0.25          # a kind's rate may drift this much across the halves and still agree
SILENT_WEIGHT = 0.05        # a silent thing is still checked now and then (the world can change)
LAM_LOOKS = 30              # answered looks a thing's rate is fitted over
LAM_GRID = np.logspace(-5, 0, 61)   # candidate rates, per second
LAM_PRIOR_LOOKS = 3.0       # after a shift, the old rate weighs this many looks (#235)
PATIENCE_P = 0.6            # look again once a change is this likely
PATIENCE_MAX_S = 1800.0     # never wait longer than this
RUNG4_TIMED = 30            # timed looks before the test reads them
RUNG4_CALIB = 0.1           # its predictions must match the outcomes within this
WAIT_EVERY = 30             # a waiting stretch is recorded once per this many cycles
SHIFT_WINDOWS = (3, 4, 5)   # recent looks read against the long memory
SHIFT_P = 0.02              # ...a shift when they are this unlikely under it
SHIFT_GAP = 0.4             # ...and this far from it
AUDIT5_LOOKS = 5            # self's looks at the thing in each phase of rung 5's audit
AUDIT5_PAUSE_S = 3600.0     # the phases' caps (an audit that runs out is inconclusive)
AUDIT5_RESUME_S = 3 * 3600.0
RUNG5_LOOKS = 20            # looks with noticing on before the test reads the audit
RUNG5_SHIFTS = 2            # the two shifts its audit probes (went still, moves again)
RUNG6_PUSHES = 10           # pushes to chat:out before the test reads the split
RUNG6_HEARD = 20            # answered looks at the other before the test reads the split
BODY_SKILL = 0.9            # predict it this well and it is my body (zero gap), else an other
MEET_EVERY = 30             # cycles between self's pushes to chat:out: rung 6's timer, and the cap on every push after
MEET_MAX = 180              # rung 7: a push waits at most this many cycles for its moment, then goes ('overdue')
# RUNG 7 (#258), TAKE TURNS: self learns WHEN to push. After each push it draws the moment it
# will wait for -- a bin of spoke_age (how long since the other last spoke) -- by Thompson
# sampling on the reply rate each bin earned over its last TURN_MEMORY pushes (Beta(1, 1)
# prior: no bin is favoured before evidence), a random bin with chance TURN_EXPLORE, and
# pushes when the other's spoke_age is in that bin, never sooner than MEET_EVERY cycles after
# the last push (the timer's own rate is the cap) and never later than MEET_MAX. A push's
# reward is the reply check's `replied`: the other spoke AFTER the push, by the check.
# THE TEST over the last RUNG7_PUSHES such pushes, prequential (each push scored with what
# self knew before it): (a) contingency -- the share of pushes in the window self believed best
# at the push beats the share of the eligible waiting time the other spent in it (where a push
# at a shuffled moment would land): z >= RUNG7_CONT_Z and +RUNG7_CONT_MARGIN; (b) payoff --
# each push's outcome minus what a timing-blind push in its interval would have expected (the
# bins' rates as known then, weighted by the waiting time in each): mean >= RUNG7_PAY_MARGIN
# with t >= RUNG7_PAY_Z. Window and bar per the rung ruler, from experiments/rung7_turns
# (measured before they were fixed): a simulation at the cycle's pace, 100 seeds x a week, the
# test read at EVERY push like the live loop. The bar is the chance line, as rung 8's is: a
# coin-flip other's best window over a week reaches payoff t q99 ~4.1 (2 of 100 runs pass; a
# presence-only other 2 of 100); a timer or a random delay never passes (no choice: z = 0). A
# turn-taking other passes in 99 of 100 (median ~38 h), an other who answers late in 61, an
# other with the live record's per-bin reply rates (0.62 / 0.36 / 0.35 / 0.02) in 39 (median
# ~86 h). t >= 4.5 would cut the coin to 1 in 100 and the live-like other to 15.
TURNS = True                # #258: rung 7's learned timing (tests switch it off to keep the timer's pace)
TURN_BINS = (120.0, 600.0, 1800.0)       # spoke_age bin edges, s; the last bin: 30 min+ or never spoke
TURN_NAMES = ("0-2m", "2-10m", "10-30m", "30m+")
TURN_EXPLORE = 0.1          # a push aims at a random bin this often (the losing windows stay measured)
TURN_MEMORY = 100           # pushes the reply rates per bin are read over (a rhythm can change)
RUNG7_PUSHES = 100          # pushes with a reply check the test reads
RUNG7_CONT_Z = 3.0          # (a) the in-window count must clear the shuffled share by this many SDs
RUNG7_CONT_MARGIN = 0.10    # ...and by this share
RUNG7_PAY_Z = 4.0           # (b) the reply rate gained over timing-blind must clear 0 by this t
RUNG7_PAY_MARGIN = 0.05     # ...and be at least this
# RUNG 8 (#254, rung 7 until #258): SELF predicts the reply check (p_self, from its record only); the model is
# an advisor it recalibrates on the advisor's own track record; the test scores self's p against
# the RUNNING RATE (the other's prior-smoothed rate over the checks before: calibration alone
# scores 0). Window and bar per the rung ruler (exp/rung-ruler): detecting order structure in
# a binary series takes ~100+ symbols, so the window is 100 reply checks. The ruler's bar is
# half the ceiling's lower CI on a replay -- but on the live record (108 checks, 2026-10-19)
# p_self's ceiling against the running rate is -0.00, 95% CI -0.05..+0.06 (the other's reply
# by the check is coin-like, E ~ 0), so no bar can be tied to it there. The bar is instead
# set above chance: over 100-check windows a structureless other's p_self reaches at most
# +0.055..+0.065 (q99 of the best sliding window, rates 0.3/0.5/0.7, two seed sets) and a
# humble always-0.5 +0.046..+0.054 (q95), while an other with session timing (answers in a
# session, rarely away) gives p_self a median +0.19..+0.21, lower 5% +0.11..+0.135. +0.08
# clears chance with room and is about half of what a learnable rhythm yields at worst.
RUNG8_LOOKS = 100           # reply checks at the other the test reads (the RUNG8_* were RUNG7_* until #258)
RUNG8_MARGIN = 0.08         # self's p must remove this share of the running rate's Brier error
RUNG8_SPOKE_S = 600.0       # p_self's freshness term: exp(-(push - last spoke) / this)
RUNG8_RIDGE = 10.0          # p_self's ridge penalty: no evidence -> it says the running rate
RUNG8_FIT = 500             # p_self fits on at most this many earlier reply checks
RUNG8_TRUST = 50            # the advisor's calibration reads its last this-many checks under one model
RUNG8_TRUST_RIDGE = 5.0     # ...ridge-shrunk toward "ignore the advisor": trust is earned
RUNG8_CHECK_S = 120.0       # after a push self checks for the reply this long after (#215; rung 7 reads it too)
RUNG8_STALE_S = 600.0       # a push this old without its reply check asks nothing any more
RUNG8_GRACE_S = 60.0        # the reply check waits up to this much longer for an answer still in flight (#217)
RUNG8_HISTORY = 8           # recent reply checks the question shows the model (#217)
RUNG8_RECENT = 6            # reply checks the RECENT rate is over, next to the all-time one (#246)
CURIOSITY = True            # #212: tilt the reach choice by learning progress (tests switch it off for the base)
CURIO_WINDOW = 30           # ordinary looks per window: progress = earlier window's error - recent window's
CURIO_FLOOR = 0.1           # the tilt never goes under this: a mastered or "can't learn" thing is still retried
CURIO_MIN = 0.005           # progress under this anywhere is no frontier: the base chooser runs untilted
RECENT_MAX = 100            # #230: the most record lines the page's activity feed may ask for
TOY_PREFIXES = ("toy:", "cradle:")   # #230: things made for self to play with (listed even without a getter)
TOY_WORDS = "w:"            # ...and words that carry a getter (a plain word is not a toy)
TOY_READS = 10              # the last reaches of a toy its health mark reads
TOY_SCAN = 3000             # how far back in the record the toys' reads are looked for
TOY_TTL_S = 15.0            # the toy list is recomputed at most this often (it walks the store)
OFF_FIELD_LOOKS = 200       # #244: status() counts how many of the last looks land off the field

_lock = threading.RLock()
_ctx = threading.local()    # the cycle in flight: only the loop's own thread has one
_RT: dict[int, dict] = {}   # id(srv) -> runtime (mode, guard clocks, hold, the wake event)


# ---- the entity and its methods ----------------------------------------------

def _eid():
    from .entities import MEMBRANE_NS
    return MEMBRANE_NS + ENT


def _entity(srv):
    from .entities import CLASS_COMPONENT, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(_eid())
    if ent is None:
        ent = QuantumEntity(eid=_eid(), cls=CLASS_COMPONENT, properties={
            "name": "self",
            "summary": ("an unnamed loop (#161): try / expect / notice, run only by itself; "
                        "the operator switches it (off / on / watch)"),
            "silent_birth": True})
        est.upsert(ent, embedder=None)
        try:
            est.flush()
        except Exception:
            pass
    return ent


def bind(srv) -> None:
    """Boot: the entity, its three methods (once), the saved mode."""
    est = getattr(srv, "entity_store", None)
    ent = _entity(srv)
    if est is None or ent is None:
        return
    have = {(d.properties or {}).get("method_name") for d in list(est.docs.values())
            if ent.eid in ((d.properties or {}).get("method_of") or [])}
    from .night import method_define
    for meth, cmd in METHODS.items():
        if meth not in have:
            method_define(srv, est, None, None, on=ent.eid, method=meth, command=cmd)
    rt = _rt(srv)
    rt["mode"], rt["limit"] = _load_mode(srv)
    rt["model"] = _load_model(srv)
    rt["tempo"] = _load_json(srv, "tempo.json") or {}


def _rt(srv) -> dict:
    with _lock:
        rt = _RT.get(id(srv))
        if rt is None:
            rt = _RT[id(srv)] = {"mode": "off", "limit": 0, "last": None, "burst": [], "fails": 0,
                                 "held": False, "wake": threading.Event(), "srv": srv}
        return rt


# ---- the mode and the record (the data dir) --------------------------------

def _dir(srv) -> Path | None:
    mp = getattr(srv, "_models_path", None)
    return Path(mp).parent / "self" if mp else None


def record_path(srv) -> Path | None:
    d = _dir(srv)
    return d / "record.jsonl" if d is not None else None


def _load_mode(srv) -> tuple[str, int]:
    """(mode, limit). A seat switched on before the limit existed gets 1 --
    what it had -- so no higher rung opens until the operator raises it."""
    d = _dir(srv)
    try:
        doc = json.loads((d / "mode.json").read_text()) if d is not None else {}
    except Exception:
        doc = {}
    m = doc.get("mode") if doc.get("mode") in MODES else "off"
    try:
        lim = int(doc["limit"]) if "limit" in doc else (1 if m != "off" else 0)
    except (TypeError, ValueError):
        lim = 1 if m != "off" else 0
    return m, (max(1, min(lim, max(LADDER))) if m != "off" else 0)


def _save_mode(srv, mode: str, limit: int) -> None:
    d = _dir(srv)
    if d is None:
        return
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "mode.json.tmp"
    tmp.write_text(json.dumps({"mode": mode, "limit": limit, "ts": time.time()}))
    os.replace(tmp, d / "mode.json")


def _append(srv, rec: dict) -> None:
    p = record_path(srv)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(p, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
        cache = _rt(srv).get("recs")
        if cache is not None:
            cache.append(rec)


def _read(srv) -> list[dict]:
    """The record, read from disk once and kept in memory as it grows (it was
    re-read several times a cycle -- quadratic in its length)."""
    rt = _rt(srv)
    if rt.get("recs") is None:
        p = record_path(srv)
        out = []
        if p is not None and p.exists():
            for ln in p.read_text().splitlines():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
        rt["recs"] = out
    return rt["recs"]


def _model_path(srv) -> Path | None:
    d = _dir(srv)
    return d / "model.json" if d is not None else None


def _load_model(srv) -> dict | None:
    p = _model_path(srv)
    try:
        m = json.loads(p.read_text()) if p is not None and p.exists() else None
    except Exception:
        m = None
    return m if isinstance(m, dict) and m.get("mp") else None


def _save_model(srv, m: dict) -> None:
    p = _model_path(srv)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m))
    os.replace(tmp, p)


def _load_json(srv, name: str):
    d = _dir(srv)
    try:
        return json.loads((d / name).read_text()) if d is not None and (d / name).exists() else None
    except Exception:
        return None


def _save_json(srv, name: str, doc) -> None:
    d = _dir(srv)
    if d is None:
        return
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(doc))
    os.replace(tmp, d / name)


def _tempo(srv) -> dict:
    """eid -> {changed, answered}: how often each thing had changed when I looked."""
    return _rt(srv).setdefault("tempo", {})


def mode(srv) -> str:
    return _rt(srv)["mode"]


def limit(srv) -> int:
    return _rt(srv)["limit"]


def set_limit(srv, n, by: str = "operator") -> bool:
    """The operator's chooser: the highest rung self may reach (0: off),
    clamped to the rungs built. Raising it grants nothing -- each rung under
    it is still earned by its test."""
    try:
        n = max(0, min(int(n), max(LADDER)))
    except (TypeError, ValueError):
        return False
    rt = _rt(srv)
    if n == 0:
        return set_mode(srv, "off", by)
    keep = rt["mode"] if rt["mode"] != "off" else "on"
    rt["limit"] = n
    return set_mode(srv, keep, by)


def set_mode(srv, m: str, by: str = "operator") -> bool:
    """The operator's switch (seat_panel.control). The first switch-on is the
    birth; a toggle clears a hold. Off is limit 0; on with no limit is 1."""
    if m not in MODES:
        return False
    rt = _rt(srv)
    with _lock:
        if m != "off" and not any(r.get("kind") == "birth" for r in _read(srv)):
            _append(srv, {"ts": time.time(), "kind": "birth", "by": by})
        rt["mode"], rt["held"], rt["fails"] = m, False, 0
        rt["limit"] = 0 if m == "off" else max(1, rt["limit"])
        _save_mode(srv, m, rt["limit"])
    if m != "off":
        rt["wake"].set()                    # switching on kicks the first cycle
    return True


# ---- the three methods: only the loop's own thread may run them -------------

def _seed_dir(seed: str, dim: int) -> np.ndarray:
    v = np.random.default_rng(abs(hash(("self", str(seed)))) % (2 ** 32)).normal(size=dim)
    return v / (np.linalg.norm(v) or 1.0)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _current(srv):
    fld = getattr(srv, "efield", None)
    try:
        c = fld.current() if fld is not None else None
    except Exception:
        c = None
    return None if c is None else _unit(c)


def _p_changed(srv, eid: str) -> float:
    """The tempo, at the pace I look, with ONE BIT OF MEMORY: the world bursts,
    so what I will see depends on what I saw last. Predict P(changed | last
    answer) once I have one, else the smoothed share of my reaches that found
    it changed (Jeffreys prior: 0.5 before I ever looked)."""
    t = _tempo(srv).get(eid) or {}
    aft = t.get("after") or {}
    prev = t.get("prev")
    if prev in aft and aft[prev][1] > 0:
        return (float(aft[prev][0]) + 0.5) / (float(aft[prev][1]) + 1.0)
    return (float(t.get("changed", 0)) + 0.5) / (float(t.get("answered", 0)) + 1.0)


def _expect(srv, seed: str) -> dict:
    """Where I will end up: my own step, plus the push I have learned to expect here.
    (For a reach: whether the thing will have changed since I last looked.)"""
    c = _ctx.cycle
    if c.get("act") == "reach":
        lam, dt = c.get("lam"), c.get("dt")
        c["p"] = (1.0 - float(np.exp(-lam * dt))) if (lam and dt is not None) else _p_changed(srv, c["eid"])
        push = c.get("reply")                           # rung 7/8: this look answers a push's question
        if push is not None:
            recs = _read(srv)
            c["p_self"] = _p_self(recs, c["eid"], push)  # self's own prediction (#254)
            if push.get("asked"):
                c["p_llm"] = _llm_pred(srv, c["eid"], float(push["ts"]), until=c.get("now"))   # landed by this look
            if c.get("p_llm") is not None:              # the advisor answered: weigh it by its record
                c["llm_model"] = _llm_model(srv, c["eid"])
                c["llm_cal"] = _advisor_cal(_rung8_series(recs, c["eid"]), c["llm_model"])
                c["p_mix"] = _advise(c["p_self"], c["p_llm"], c["llm_cal"])
        return {"rc": 0, "text": "expected"}
    w = _expected_push(srv, c["pos"])
    if c.get("aim") is not None:
        # STEER: lean into the current by the push I expect, toward my aim
        raw = c["aim"] - c["pos"]
        c["naive_mine"] = _clip(raw, STEP_MAX)
        c["mine"] = _clip(raw - w, STEP_MAX)
        c["intent"] = _unit(c["pos"] + c["mine"])
    else:
        c["intent"] = _unit(c["pos"] + STEP * _seed_dir(seed, len(c["pos"])))
    c["expected"] = _unit(c["intent"] + w)
    return {"rc": 0, "text": "expected"}


def _clip(v, n: float) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    m = float(np.linalg.norm(v))
    return v * (n / m) if m > n else v


def _model(srv, dim: int) -> dict | None:
    m = _rt(srv).get("model")
    if not isinstance(m, dict) or len(m.get("mp") or []) != dim:
        return None
    return m


def _expected_push(srv, pos) -> np.ndarray:
    """The push I expect here: push ~ b - g*pos, from the running means and
    the push/position covariance (no model yet: none)."""
    m = _model(srv, len(pos))
    if m is None:
        return np.zeros_like(pos)
    mp, mw = np.asarray(m["mp"]), np.asarray(m["mw"])
    if float(m.get("var") or 0.0) <= 1e-9:
        return mw
    g = -float(m["cov"]) / float(m["var"])
    return (mw + g * mp) - g * pos


def _try(srv, seed: str) -> dict:
    """Push myself one step; the world pushes too, while I move."""
    c = _ctx.cycle
    ent = _entity(srv)
    mine = c["mine"] if c.get("mine") is not None else STEP * _seed_dir(seed, len(c["pos"]))
    world = PUSH_GAIN * (c["current"] - c["pos"])
    ent.position = [float(x) for x in _unit(c["pos"] + mine + world)]
    return {"rc": 0, "text": "moved"}


def _read_entity(srv, ent, now):
    """THE SEAM: one look at a thing (its getter fires, or answers that it
    can't). Tests swap it for a simulated world driven by the fake clock."""
    from . import watch_agents as _WA
    return _WA.entity_read_back(srv, ent, force=False, by="self")


def _push_chat(srv, text: str, person: str = ""):
    """THE SEAM: self's outward act -- push a line to the person's chat:out
    ('' = the operator's), marked as self's (#208). Tests swap it."""
    from . import chat_io as _CIO
    ent = _CIO.entity(srv, person)
    if ent is None:
        return
    ent.properties["say"] = text
    _CIO.on_edit(srv, ent, by="self")


def _parse_p(text) -> float | None:
    """A probability out of the model's one line (#217): the first decimal in
    0..1 or percent, read after VERDICT when it says one. Prose is not a
    probability -- a line with no VERDICT counts only when it is short (live:
    "Membrane is a tool ...", "a living entity (#161)" -- off-task answers),
    and a number above 1 that is no percent (120 s, #161) is skipped."""
    t = str(text or "")
    low = t.lower()
    i = low.find("verdict")
    if i >= 0:
        t = t[i + len("verdict"):]
    elif len(t.strip()) > 40:
        return None
    import re
    for m in re.finditer(r"(?<![\w.#])(\d+(?:\.\d+)?|\.\d+)\s*(%|percent)?(?![\w.])", t):
        x = float(m.group(1))
        if m.group(2):
            if x <= 100.0:
                return x / 100.0
        elif x <= 1.0:
            return x
    return None


def _llm_pred(srv, eid: str, since: float, until: float | None = None) -> float | None:
    """THE SEAM (rung 8): the model's prediction for this look, landed on the
    entity as a one-line llm_answer SINCE the push that asked it (an older
    answer answered an older question) and by `until` (the look). None when
    absent, older, later or not a probability. Tests swap it for a
    deterministic stand-in."""
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(eid) if est is not None else None
    ans = (doc.properties or {}).get("llm_answer") if doc is not None else None
    if not isinstance(ans, dict):
        return None
    ts = float(ans.get("ts") or 0)
    if ts < since:                                     # answered before this push: not this question
        return None
    if until is not None and ts > until:               # not landed by the look (#217)
        return None
    return _parse_p(ans.get("text"))


def _llm_model(srv, eid: str) -> str | None:
    """THE SEAM (rung 8, #254): which model gave the answer on the entity
    (llm_answer's `model`, the turn's pick), None when it wasn't recorded."""
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(eid) if est is not None else None
    ans = (doc.properties or {}).get("llm_answer") if doc is not None else None
    m = ans.get("model") if isinstance(ans, dict) else None
    return str(m) if m else None


def _ask_llm(srv, eid: str, prompt: str) -> dict:
    """THE SEAM (rung 8): ask the coprocessor to predict the reply check. The
    real llm::call is async -- it queues and returns at once ({rc, text});
    the verdict lands on the entity as llm_answer for the reply check (read
    by _llm_pred). Its caps (per caller, per hour, the switch, the pause) are
    llm.request's own."""
    from . import llm as _LLM
    return _LLM.request(srv, prompt, about=eid, caller="self", readonly=True)


def _queued(r) -> bool:
    """A consult that really went out (an echo or a refusal asked nothing)."""
    return isinstance(r, dict) and r.get("rc") == 0 and str(r.get("text") or "").startswith("queued")


def _first_look_after(recs: list[dict], eid: str, ts: float):
    return next((r for r in recs if r.get("kind") == "reach" and r.get("eid") == eid
                 and float(r.get("ts") or 0) > ts), None)


def _pending(recs: list[dict], now: float) -> dict | None:
    """THE QUESTION IN FLIGHT (#215): the latest push, while self has not yet
    looked at its other since and it is under RUNG8_STALE_S old -- read from
    the record, so it survives a restart. None otherwise."""
    push = next((r for r in reversed(recs) if r.get("kind") == "push"), None)
    if push is None or not push.get("to"):
        return None
    if now - float(push.get("ts") or 0) > RUNG8_STALE_S:
        return None
    if _first_look_after(recs, push["to"], float(push.get("ts") or 0)) is not None:
        return None
    return push


def _reply_checks(recs: list[dict], eid: str) -> list[dict]:
    """Self's reply checks at `eid` that got an answer -- the looks a push's
    question is about (one horizon: RUNG8_CHECK_S after the push)."""
    return [r for r in recs if r.get("kind") == "reach" and r.get("eid") == eid and r.get("reply")
            and not r.get("audit") and r.get("answer") in ("changed", "unchanged")]


def _base_rate(recs: list[dict], eid: str) -> tuple[float, int, int]:
    """How often the other had spoken by the reply check: (rate, k, n), with a
    Jeffreys prior -- 0.5 before any check, never 0/0 (#217)."""
    rc = _reply_checks(recs, eid)
    k = sum(1 for r in rc if r["answer"] == "changed")
    return (k + 0.5) / (len(rc) + 1.0), k, len(rc)


def _recent_rate(recs: list[dict], eid: str) -> tuple[float, int, int]:
    """The share of the last RUNG8_RECENT reply checks the other answered:
    (rate, k, n), the same Jeffreys prior as _base_rate -- 0.5 before any
    check, never 0 or 1 (#246: the person's rhythm now, not since birth)."""
    rc = _reply_checks(recs, eid)[-RUNG8_RECENT:]
    k = sum(1 for r in rc if r["answer"] == "changed")
    return (k + 0.5) / (len(rc) + 1.0), k, len(rc)


# ---- rung 8 (#254; rung 7 until #258): self's own prediction, the advisor, the running rate ----------

def _logit(p: float) -> float:
    p = min(max(float(p), 1e-4), 1.0 - 1e-4)
    return float(np.log(p / (1.0 - p)))


def _push_gap(push: dict, last_changed: float | None) -> float | None:
    """How long before the push the other last spoke, as self knew AT the push:
    the push's recorded `spoke_age` (chat:in's heard_ts or self's last look
    that found them changed), else -- an older push -- from self's own looks."""
    age = push.get("spoke_age")
    if isinstance(age, (int, float)) and age >= 0:
        return float(age)
    ts = float(push.get("ts") or 0)
    return (ts - last_changed) if last_changed is not None and last_changed <= ts else None


def _rung8_rows(recs: list[dict], eid: str) -> list[dict]:
    """Self's answered reply checks at `eid`, oldest first, each with what self
    knew at its push (the gap since the other last spoke), its outcome and
    what the look recorded (p_self, the advisor's p_llm and model, w, p_mix)."""
    rows, last_ch, gap = [], None, None
    for r in sorted(recs, key=lambda x: float(x.get("ts") or 0)):     # time order (stable; the record already is)
        k = r.get("kind")
        if k == "push" and r.get("to") == eid:
            gap = _push_gap(r, last_ch)
        elif k == "reach" and r.get("eid") == eid and not r.get("audit") \
                and r.get("answer") in ("changed", "unchanged"):
            if r.get("reply"):
                rows.append({"ts": float(r.get("ts") or 0), "o": 1.0 if r["answer"] == "changed" else 0.0,
                             "gap": gap, "p_llm": r.get("p_llm"), "model": r.get("llm_model"),
                             "rec_p_self": r.get("p_self"), "rec_cal": r.get("llm_cal"), "rec_p": r.get("p_mix")})
            if r["answer"] == "changed":
                last_ch = float(r.get("ts") or 0)
    return rows


def _r8_design(rows: list[dict]):
    """p_self's inputs per check, from the checks BEFORE it only: the offset is
    the running (all-time) rate's logit; the features are an intercept, the
    recent rate's lean from it, the last outcome (+1 / -1, 0 before any) and
    how fresh the other's last words were at the push (exp(-gap/SPOKE_S))."""
    off, X = np.zeros(len(rows)), np.zeros((len(rows), 4))
    past: list[float] = []
    k = 0.0
    for i, r in enumerate(rows):
        base = (k + 0.5) / (len(past) + 1.0)
        w = past[-RUNG8_RECENT:]
        rr = (sum(w) + 0.5) / (len(w) + 1.0)
        last = (1.0 if past[-1] else -1.0) if past else 0.0
        fresh = float(np.exp(-max(0.0, r["gap"]) / RUNG8_SPOKE_S)) if r.get("gap") is not None else 0.0
        off[i] = _logit(base)
        X[i] = (1.0, _logit(rr) - off[i], last, fresh)
        if r.get("o") is not None:
            past.append(r["o"])
            k += r["o"]
    return off, X


def _r8_predict(off, X, y, i: int) -> float:
    """p_self for check i: a ridge-penalised logistic (offset: the running
    rate) fit by Newton on the checks before i, then read at i. No evidence
    (or no structure) shrinks the coefficients to 0 -- the running rate."""
    lo = max(0, i - RUNG8_FIT)
    beta = _ridge_logit(off[lo:i], X[lo:i], y[lo:i], RUNG8_RIDGE) if i > lo else np.zeros(X.shape[1])
    return float(1.0 / (1.0 + np.exp(-(off[i] + X[i] @ beta))))


def _p_self(recs: list[dict], eid: str, push: dict) -> float:
    """SELF'S PREDICTION (#254) that the other will have spoken by the reply
    check after `push`: from self's record only -- no model call."""
    rows = _rung8_rows(recs, eid)
    t = float(push.get("ts") or 0)
    last_ch = next((float(r["ts"]) for r in reversed(recs) if r.get("kind") == "reach" and r.get("eid") == eid
                    and r.get("answer") == "changed" and float(r.get("ts") or 0) <= t), None)
    rows = rows + [{"o": None, "gap": _push_gap(push, last_ch)}]
    off, X = _r8_design(rows)
    y = np.array([r["o"] for r in rows[:-1]], dtype=float)
    return round(_r8_predict(off, X, y, len(rows) - 1), 4)


def _ridge_logit(off, X, y, lam: float) -> np.ndarray:
    """A ridge-penalised logistic regression with an offset, by Newton:
    the coefficients that best turn `off` (logits) into the outcomes y,
    shrunk toward 0 (= trust the offset) by `lam`."""
    beta = np.zeros(X.shape[1])
    eye = lam * np.eye(X.shape[1])
    for _ in range(12):
        p = 1.0 / (1.0 + np.exp(-(off + X @ beta)))
        g = X.T @ (y - p) - lam * beta
        h = (X * (p * (1.0 - p))[:, None]).T @ X + eye
        step = np.linalg.solve(h, g)
        beta = beta + step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return beta


def _advisor_cal(rows: list[dict], model) -> tuple[float, float]:
    """THE ADVISOR, RECALIBRATED (#254). A model's stated probabilities carry
    its own lean (label bias; one model leaned "yes", the next "no"), so self
    doesn't take p_llm at face value: over the advisor's last RUNG8_TRUST
    answered checks under the SAME model id it reads the advisor's own lean
    m (the mean logit it states) and fits w in
    p = sigmoid(logit p_self + w·(logit p_llm − m)) -- Platt scaling of the
    advisor on top of self's own guess, with the advisor's average lean taken
    to say nothing (only where it departs from its habit is news): a
    constant answer, however biased, has no influence. w is ridge-shrunk
    toward 0 = "ignore the advisor", so few pairs or a new model give
    p = p_self; w >= 0: an advisor that tracks the outcome backwards is
    ignored, not decoded. Returns (m, w)."""
    rs = [r for r in rows if r.get("p_llm") is not None and r.get("model") == model][-RUNG8_TRUST:]
    if not rs:
        return 0.0, 0.0
    z = np.array([_logit(float(r["p_llm"])) for r in rs])
    m = float(z.mean())
    off = np.array([_logit(r["p_self"]) for r in rs])
    y = np.array([r["o"] for r in rs], dtype=float)
    w = float(_ridge_logit(off, (z - m)[:, None], y, RUNG8_TRUST_RIDGE)[0])
    return round(m, 4), round(max(0.0, w), 4)


def _advise(p_self: float, p_llm: float, cal) -> float:
    """Self's prediction with the recalibrated advisor heard."""
    m, w = cal
    return float(1.0 / (1.0 + np.exp(-(_logit(p_self) + w * (_logit(p_llm) - m)))))


_P8_CACHE: dict = {}     # (eid, chain hash of the checks so far) -> a recomputed p_self


def _rung8_series(recs: list[dict], eid: str) -> list[dict]:
    """Every answered reply check at `eid`, oldest first, scored as rung 8
    reads it: o, `rate` (the running rate: Jeffreys over the checks before,
    no lookahead), `p_self` (recorded, or -- a check from before #254 --
    recomputed from the record by the same function), `p_llm`, `model`, the
    advisor's calibration `cal` = (a, w) from its checks before this one,
    `w` (its slope: how much self listens) and `p`, self's prediction with
    the advisor heard (_advise; p_self when the advisor said nothing)."""
    rows = _rung8_rows(recs, eid)
    off = X = y = None
    h = 0
    k = 0.0
    for i, r in enumerate(rows):
        r["rate"] = (k + 0.5) / (i + 1.0)
        h = hash((h, r["ts"], r["o"], r["gap"]))
        if r["rec_p_self"] is not None:
            r["p_self"] = float(r["rec_p_self"])
        else:
            key = (eid, h)
            if key not in _P8_CACHE:
                if off is None:
                    off, X = _r8_design(rows)
                    y = np.array([x["o"] for x in rows], dtype=float)
                if len(_P8_CACHE) > 20000:
                    _P8_CACHE.clear()
                _P8_CACHE[key] = round(_r8_predict(off, X, y, i), 4)
            r["p_self"] = _P8_CACHE[key]
        if r["p_llm"] is None:
            r["cal"], r["w"], r["p"] = (0.0, 0.0), 0.0, r["p_self"]
        else:
            rc = r["rec_cal"]
            r["cal"] = (float(rc[0]), float(rc[1])) if isinstance(rc, (list, tuple)) and len(rc) == 2 \
                else _advisor_cal(rows[:i], r["model"])
            r["w"] = r["cal"][1]
            r["p"] = (float(r["rec_p"]) if r["rec_p"] is not None
                      else _advise(r["p_self"], float(r["p_llm"]), r["cal"]))
        k += r["o"]
    return rows


def _mark_rung7_restart(srv, now: float) -> None:
    """#254: the moment the fixed rung 7 starts counting, on the record once --
    the self sheet closes its 'rung 7 graded the LLM' bug window there. Only
    a seat that has passed rung 6 has a rung 7 to restart. A rung 7 pass the
    old scorer recorded was the model's, not self's: it gets a `rung_void`
    line (the pass line stays), so rung 7 re-opens under the new scoring.

    #258: that rung 7 is rung 8 now (understand an other). The line's kind
    and its text stay as they were (the sheet and the page read the kind);
    a pass from before the flip would not count above rung 6 anyway
    (_rung_lines)."""
    rt = _rt(srv)
    if rt.get("r7_marked"):
        return
    recs = _read(srv)
    if any(r.get("kind") == "rung7_restart" for r in recs):
        rt["r7_marked"] = True
    elif 6 in _passed(recs):
        old7 = [x for x in recs if x.get("kind") == "rung" and x.get("rung") == 7
                and x.get("ladder") != LADDER_V]                # #258: a new ladder's rung 7 is take turns
        voided = {float(x.get("voids_ts") or 0) for x in recs if x.get("kind") == "rung_void" and x.get("rung") == 7}
        for r in [x for x in old7 if float(x.get("ts") or 0) not in voided]:
            _append(srv, {"ts": now, "kind": "rung_void", "rung": 7, "voids_ts": r.get("ts"),
                          "why": "#254: rung 7 graded the LLM's guess, not self's"})
        _append(srv, {"ts": now, "kind": "rung7_restart", "issue": 254,
                      "why": "rung 7 now grades self's prediction, not the model's"})
        rt["r7_marked"] = True


def _last_spoke(srv, recs: list[dict], eid: str, now: float) -> float | None:
    """When the other last spoke, as self knows it: chat:in's heard_ts (the
    moment the line arrived) or self's last look that found them changed --
    the later one, never after `now`. A time only, never the words."""
    ts = [float(r["ts"]) for r in recs if r.get("kind") == "reach" and r.get("eid") == eid
          and r.get("answer") == "changed" and float(r.get("ts") or 0) <= now]
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(eid) if est is not None else None
    heard = (doc.properties or {}).get("heard_ts") if doc is not None else None
    if isinstance(heard, (int, float)) and 0 < heard <= now:
        ts.append(float(heard))
    return max(ts) if ts else None


def _hold_reply(srv, push: dict, now: float) -> bool:
    """Not yet time for the reply check: under RUNG8_CHECK_S after the push --
    or, when the push asked the model and no answer has landed, under
    RUNG8_CHECK_S + RUNG8_GRACE_S (#217: a slow turn's answer still pairs).
    Self only holds the other back; the cycle goes on with other targets."""
    age = now - float(push.get("ts") or 0)
    if age < RUNG8_CHECK_S:
        return True
    if push.get("asked") and age < RUNG8_CHECK_S + RUNG8_GRACE_S:
        return _llm_pred(srv, push["to"], float(push["ts"]), until=now) is None
    return False


def _rung8_prompt(srv, eid: str, n: int, now: float) -> str:
    """What to ask the coprocessor at a push: will the other have spoken by
    self's reply check? Only facts self already holds in its record --
    timings and changed/unchanged answers, never the chat's words.

    CALIBRATED (#217): live, the model answered 0.98-0.99 to reply checks
    that went 3 of 7. It now gets the BASE RATE (the share of reply checks
    the other had answered, Jeffreys prior) and the recent reply checks only
    -- looks long after a push are another horizon and read as "they always
    answer" -- and is told a confident miss costs (the Brier score).

    RECENT (#246): the all-time rate can't see a person who went busy; the
    question also carries the share of the last RUNG8_RECENT reply checks
    answered and the time since they last spoke, labelled `recent` vs
    `all-time`, and says rhythms change. Input only: the test, the pairing,
    the caps and the parsing are #217's."""
    recs = _read(srv)
    rate, k, m = _base_rate(recs, eid)
    rr, kr, mr = _recent_rate(recs, eid)
    rc = _reply_checks(recs, eid)[-RUNG8_HISTORY:]
    lines = []
    for r in rc:
        push = next((p for p in reversed(recs) if p.get("kind") == "push" and p.get("to") == eid
                     and float(p.get("ts") or 0) < float(r["ts"])), None)
        after = f"{round(float(r['ts']) - float(push['ts']))}s after" if push is not None else "after"
        lines.append(f"{after}: " + ("they had spoken" if r["answer"] == "changed" else "silent"))
    hist = "; ".join(lines) or "(none yet)"
    spoke = _last_spoke(srv, recs, eid, now)
    last = (f"Self last saw them speak {round(now - spoke)}s ago. " if spoke is not None
            else "Self has not seen them speak yet. ")
    return (f"You are predicting whether {eid} (the person self talks to) will have spoken by "
            f"self's next look at them (its reply check). Self just pushed a line to them (push {n}); "
            f"the reply check comes about {round(RUNG8_CHECK_S)}s from now "
            f"(at most {round(RUNG8_CHECK_S + RUNG8_GRACE_S)}s). "
            f"All-time: they had spoken by the reply check after {k} of {m} earlier pushes -- "
            f"all-time base rate {rate:.2f} (with a small prior). "
            f"Recent: they had spoken after {kr} of the last {mr} -- "
            f"recent rate {rr:.2f} (same prior). " + last
            + f"Recent reply checks (oldest first): {hist}. "
            "People's rhythms change (a busy hour, a quiet evening): when the recent rate disagrees "
            "with the all-time one, weigh the recent window more. "
            "Give ONE calibrated probability (0..1) that they will have spoken by that check. "
            "You are scored by the Brier score, (p - outcome)^2: a confident wrong answer is penalized "
            "heavily (0.98 that misses costs 0.96), so stay near these rates unless these facts give "
            "a real reason to move. Don't use tools -- everything you know is here. "
            "Answer in one line: VERDICT: <probability>")


def _meet(srv, seed: str, now: float | None = None) -> None:
    """RUNG 6's act: push a line to chat:out (the other answers). Practised
    from the cycle once rung 6 is available, paced at MEET_EVERY cycles so it
    doesn't spam chat:out's gap guard.

    THE OTHER is the person who actually chats (#208): chat_io's own
    recent_person (the owner of the newest owned chat), the operator only on
    a seat with no people. It is resolved once per meet, so the push target
    and the reach target move together when the person changes.

    RUNG 7 (#258): once rung 7 is available the timer gives way to self's
    own TIMING (_turn_push): it waits for the moment it has learned gets
    replies. Same words, same cap.

    RUNG 8 (#215, rung 7 until #258): once rung 8 is available, each push
    asks the model once (async, capped by llm.request) whether the other will
    have spoken by self's reply check; the push line records whether it went
    out (`asked`)."""
    try:
        n = int(seed or 0)
    except (TypeError, ValueError):
        return                                   # not a cycle's seed: only the loop meets
    import time
    from . import chat_io as _CIO
    now = time.time() if now is None else now
    turns = TURNS and _available(srv, 7)
    if not turns and n % MEET_EVERY != 1:                   # rung 6: the timer
        return
    person = _CIO.recent_person(srv)                        # '' = the operator (no people)
    resp = _CIO.entity_in(srv, person)                      # the other, as a reach target
    to = resp.eid if resp is not None else _CIO.name_in(person)
    recs = _read(srv)
    spoke = _last_spoke(srv, recs, to, now) if _available(srv, 7) else None   # as known now (#254)
    rec = {"ts": now, "kind": "push", "n": n, "eid": _CIO.name_of(person), "to": to}
    if turns:                                               # rung 7: is this the moment? (#258)
        when = _turn_push(srv, recs, to, n, None if spoke is None else now - spoke)
        if when is None:
            return
        rec.update(when)
    _rt(srv)["other"] = to                                  # rungs 7 and 8 read this one
    _push_chat(srv, "self is here", person)
    if spoke is not None:
        rec["spoke_age"] = round(now - spoke, 1)
    if _available(srv, 8):                                  # rung 8: ask the model AT the push (#215)
        rec["asked"] = _queued(_ask_llm(srv, to, _rung8_prompt(srv, to, n, now)))
    _append(srv, rec)


# ---- rung 7 (#258): take turns -- self's own timing --------------------------------

def _turn_bin(age) -> int:
    """The spoke_age bin: 0-2 min, 2-10, 10-30, 30+ (or the other never spoke)."""
    if age is None:
        return len(TURN_BINS)
    for i, hi in enumerate(TURN_BINS):
        if age < hi:
            return i
    return len(TURN_BINS)


def _turn_post(rows: list[dict]):
    """Replies (k) and pushes (n) per bin over the last TURN_MEMORY pushes
    whose reply check came."""
    k, n = np.zeros(len(TURN_BINS) + 1), np.zeros(len(TURN_BINS) + 1)
    for r in [x for x in rows if x.get("replied") is not None][-TURN_MEMORY:]:
        n[r["bin"]] += 1
        k[r["bin"]] += 1.0 if r["replied"] else 0.0
    return k, n


def _turn_choose(rows: list[dict], rng) -> tuple[int, int | None]:
    """THE POLICY: the moment the next push waits for, and the window self
    believes best. aim: a Thompson draw from each bin's Beta(1 + replies,
    1 + misses) -- the bin whose draw is highest -- or, with chance
    TURN_EXPLORE, a random bin. best: the bin with the highest posterior mean
    (ties: the one with more evidence), None before any evidence. Symmetric
    in the bins: which one wins is the record's doing, never the code's."""
    k, n = _turn_post(rows)
    nb = len(k)
    best = None
    if n.sum() > 0:
        mean = (k + 1.0) / (n + 2.0)
        best = max(range(nb), key=lambda b: (mean[b], n[b], -b))
    if rng.random() < TURN_EXPLORE:
        return rng.randrange(nb), best
    draws = [rng.betavariate(1.0 + k[b], 1.0 + n[b] - k[b]) for b in range(nb)]
    return max(range(nb), key=lambda b: draws[b]), best


def _turn_push(srv, recs: list[dict], to: str, n: int, age) -> dict | None:
    """One cycle of rung 7's practice: count the moment (its bin) into this
    interval's waiting time and say whether to push now -- the push's fields
    (bin, aim, best, dwell, why) or None. Never sooner than MEET_EVERY cycles
    after the last push; at MEET_MAX it goes anyway ('overdue')."""
    import random
    last = next((r for r in reversed(recs) if r.get("kind") == "push"), None)
    if last is not None and n - int(last.get("n") or 0) < MEET_EVERY:
        return None
    rt = _rt(srv)
    st = rt.get("turn")
    key = (to, float(last["ts"]) if last is not None else None)
    if st is None or st.get("key") != key:
        aim, best = _turn_choose(_turn_rows(recs, to), random.Random(f"turn:{n}"))
        st = rt["turn"] = {"key": key, "aim": aim, "best": best, "dwell": [0] * (len(TURN_BINS) + 1)}
    b = _turn_bin(age)
    st["dwell"][b] += 1
    waited = (n - int(last.get("n") or 0)) if last is not None else MEET_EVERY - 1 + sum(st["dwell"])
    if b != st["aim"] and waited < MEET_MAX:
        return None
    rt["turn"] = None
    return {"bin": b, "aim": st["aim"], "best": st["best"], "dwell": list(st["dwell"]),
            "why": "aim" if b == st["aim"] else "overdue"}


def _replied(srv, push: dict, now: float, answer: str) -> bool:
    """The push's reward: did the other speak AFTER it, by this check?
    chat:in's heard_ts (the moment their latest line arrived) says so; a look
    can read `changed` for a line from before the push that self hadn't seen
    yet, which is not a reply. Without a heard_ts, the look's answer."""
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(push.get("to")) if est is not None else None
    heard = (doc.properties or {}).get("heard_ts") if doc is not None else None
    if isinstance(heard, (int, float)) and heard > 0:
        return bool(float(push.get("ts") or 0) < heard <= now)
    return answer == "changed"


def _turn_rows(recs: list[dict], eid: str) -> list[dict]:
    """Rung 7's pushes at `eid`, oldest first: the policy's pushes (they carry
    an aim and their waiting time) whose reply check recorded `replied`.
    Timer pushes don't count: they had no moment to choose."""
    rows: list[dict] = []
    pend = None
    for r in recs:
        k = r.get("kind")
        if k == "push":
            pend = r if (r.get("to") == eid and r.get("aim") is not None and r.get("dwell")) else None
        elif k == "reach" and r.get("eid") == eid and pend is not None:
            if r.get("reply") and isinstance(r.get("replied"), bool):
                rows.append({"ts": float(pend.get("ts") or 0), "bin": int(pend.get("bin", len(TURN_BINS))),
                             "best": pend.get("best"), "dwell": list(pend["dwell"]), "replied": r["replied"]})
            pend = None                                     # only the first look after the push is its check
    return rows


def _turn_deltas(rows: list[dict]) -> list[tuple]:
    """Per push, from what self knew BEFORE it (the last TURN_MEMORY pushes):
    (in its believed-best window 0/1, the share of its waiting time the other
    spent in that window, outcome - what a timing-blind push would expect,
    outcome). The bins' rates are shrunk toward the overall one."""
    out = []
    for i, r in enumerate(rows):
        k, n = _turn_post(rows[max(0, i - TURN_MEMORY):i])
        tot = (k.sum() + 0.5) / (n.sum() + 1.0)
        rate = (k + 2.0 * tot) / (n + 2.0)
        dw = np.asarray(r["dwell"], dtype=float)
        if dw.sum() <= 0:
            continue
        share = dw / dw.sum()
        best = r.get("best")
        inw = 1.0 if best is not None and r["bin"] == best else 0.0
        p0 = float(share[best]) if best is not None else 0.0
        y = 1.0 if r["replied"] else 0.0
        out.append((inw, p0, y - float(share @ rate), y))
    return out


def _turn_window(d: list[tuple]) -> dict:
    """Rung 7's test over one window of _turn_deltas: (a) contingency, (b) payoff."""
    if not d:
        return {"n": 0, "share": None, "chance": None, "z": 0.0, "payoff": None, "payoff_z": 0.0,
                "reply_rate": None, "blind_rate": None, "passed": False}
    a = np.asarray(d, dtype=float)
    inw, p0, dl, y = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    var = float((p0 * (1.0 - p0)).sum())
    z = float((inw.sum() - p0.sum()) / np.sqrt(var)) if var > 1e-9 else 0.0
    pay = float(dl.mean())
    sd = float(dl.std(ddof=1)) if len(dl) > 1 else 0.0
    t = pay / (sd / np.sqrt(len(dl))) if sd > 1e-12 else 0.0
    share, chance = float(inw.mean()), float(p0.mean())
    ok = (len(d) >= RUNG7_PUSHES and z >= RUNG7_CONT_Z and share - chance >= RUNG7_CONT_MARGIN
          and t >= RUNG7_PAY_Z and pay >= RUNG7_PAY_MARGIN)
    return {"n": len(d), "share": share, "chance": chance, "z": z, "payoff": pay, "payoff_z": float(t),
            "reply_rate": float(y.mean()), "blind_rate": float(y.mean()) - pay, "passed": bool(ok)}


def _turn_score(rows: list[dict]) -> dict:
    """Rung 7's test over the last RUNG7_PUSHES of `rows` (each scored on the
    TURN_MEMORY before it)."""
    rows = rows[-(RUNG7_PUSHES + TURN_MEMORY):]
    return _turn_window(_turn_deltas(rows)[-RUNG7_PUSHES:])


def _reach(srv, target: str) -> dict:
    """Read one thing: its getter fires (or answers that it can't)."""
    c = _ctx.cycle
    ent = (srv.entity_store.docs or {}).get(target)
    if ent is None:
        c["read"] = {"fired": False, "reason": "gone"}
        return {"rc": 0, "text": "nothing there"}
    tb = (ent.properties or {}).get("toybox") or {}
    if tb.get("name"):                                      # #244: the toy's log, before and after the look
        from . import toybox as _TB
        c["toy"] = {"name": tb["name"], "block": tb.get("block"), "at0": _TB.log_len(tb["name"])}
    try:
        c["read"] = _read_entity(srv, ent, c.get("now"))
    except Exception as e:                                  # noqa: BLE001
        c["read"] = {"fired": False, "reason": f"error: {e!r}"[:80]}
    if c.get("toy"):
        c["toy"]["at1"] = _TB.log_len(tb["name"])
    return {"rc": 0, "text": "reached"}


def _toy_by(recs: list[dict], eid: str, toy: dict) -> list[str]:
    """WHO CHANGED IT (#244), the ground truth on self's reach record: every
    `by` on the toy's log (its block's lines, for a block) since self's last
    look at this thing, through this look's own act. Kept beside the answer,
    never in the reading -- self predicts from what it saw, not from who did it."""
    from . import toybox as _TB
    prev = next((r.get("log_at") for r in reversed(recs)
                 if r.get("kind") == "reach" and r.get("eid") == eid and r.get("log_at") is not None), None)
    start = toy["at0"] if prev is None or prev > toy["at0"] else prev   # a reset starts a fresh log
    return _TB.causes(toy["name"], start, toy["at1"], toy.get("block"))


def _place_once(srv, eid: str) -> None:
    """THE FIRST LOOK PLACES (#244): a thing self reaches that has no field
    point yet gets one (toybox.place: its summary, else its name, embedded) --
    once per thing per run; quiet when the field or its embedder is missing."""
    rt = _rt(srv)
    done = rt.setdefault("placed", set())
    if eid in done:
        return
    from . import toybox as _TB
    if _TB.place(srv, eid) is not None:
        done.add(eid)
        rt.pop("off_field", None)


def _answer(read: dict) -> tuple[str, str]:
    """changed / unchanged / none -- a throttle, a hold, an error or no reading
    is no answer, never the world's."""
    if not read.get("fired"):
        return "none", str(read.get("reason") or "did not fire")
    if read.get("error"):
        return "none", "held" if read.get("held") else "error"
    if read.get("changed") is None:
        return "none", "no reading"
    return ("changed" if read["changed"] else "unchanged"), ""


def _notice(srv, seed: str) -> dict:
    """Where I ended up; the push I did not account for; learn it.
    (For a reach: whether it had changed -- and learn its tempo.)"""
    c = _ctx.cycle
    if c.get("act") == "reach":
        c["answer"], c["why"] = _answer(c.get("read") or {})
        if not c.get("audit"):                               # inside an audit it learns nothing
            t = _tempo(srv).setdefault(c["eid"], {"changed": 0, "answered": 0})
            if c["answer"] == "none":
                t["none"] = int(t.get("none", 0)) + 1
            else:
                prev = t.get("prev")                         # the answer before this look
                t["answered"] += 1
                t["changed"] += int(c["answer"] == "changed")
                aft = t.setdefault("after", {})              # the one bit: what followed what
                if prev in ("changed", "unchanged"):
                    e = aft.setdefault(prev, [0, 0])
                    e[0] += int(c["answer"] == "changed")
                    e[1] += 1
                t["prev"] = c["answer"]
                t["last"] = c.get("now")
            _save_json(srv, "tempo.json", _tempo(srv))
        return {"rc": 0, "text": "noticed"}
    ent = _entity(srv)
    got = np.asarray(ent.position, dtype=float)
    push = got - c["intent"]
    p = c["pos"]
    pp = ent.properties or {}
    sd = dict(pp.get("script_data") or {})
    m = _model(srv, len(got))
    if m is None:
        m = {"mp": p, "mw": push, "cov": 0.0, "var": 0.0}
    else:
        mp = np.asarray(m["mp"]) + LEARN * (p - np.asarray(m["mp"]))
        mw = np.asarray(m["mw"]) + LEARN * (push - np.asarray(m["mw"]))
        m = {"mp": mp, "mw": mw,
             "cov": (1 - LEARN) * float(m["cov"]) + LEARN * float((push - mw) @ (p - mp)),
             "var": (1 - LEARN) * float(m["var"]) + LEARN * float((p - mp) @ (p - mp))}
    m = {"mp": [float(x) for x in m["mp"]], "mw": [float(x) for x in m["mw"]],
         "cov": float(m["cov"]), "var": float(m["var"])}
    _rt(srv)["model"] = m
    _save_model(srv, m)
    sd.pop("current_model", None)            # the old home (cut to 128 items on save)
    sd["pull"] = round(-m["cov"] / m["var"], 4) if m["var"] > 1e-9 else None
    pp["script_data"] = sd
    ent.properties = pp
    c["observed"] = got
    c["push"] = float(np.linalg.norm(push))
    return {"rc": 0, "text": "noticed"}


def from_command(rest: str) -> dict:
    """`self::<method> <seed>` (the no-shell dialect, night.run_command_entity)."""
    if getattr(_ctx, "cycle", None) is None:
        return {"rc": 1, "text": "self's methods run only in its own loop"}
    try:
        parts = shlex.split(rest)
    except ValueError:
        parts = rest.split()
    if not parts or parts[0] not in METHODS:
        return {"rc": 2, "text": f"self has no method {parts[0] if parts else ''!r}"}
    fn = {"try": _try, "expect": _expect, "notice": _notice, "reach": _reach,
          "meet": _meet}[parts[0]]   # looked up per call
    srv = _ctx.cycle["srv"]
    return fn(srv, parts[1] if len(parts) > 1 else "0")


# ---- the cycle and its guard --------------------------------------------------

def _runs(srv) -> bool:
    from .lanes import nerve_paused
    rt = _rt(srv)
    try:
        paused = bool(nerve_paused(srv))
    except Exception:
        paused = bool(getattr(srv, "paused", False))
    return rt["mode"] != "off" and not rt["held"] and not paused


def _guard_ok(rt: dict, now: float) -> bool:
    if rt["last"] is not None and now - rt["last"] < PACE_S:
        return False
    rt["burst"] = [t for t in rt["burst"] if now - t < 60.0]
    return len(rt["burst"]) < BURST_PER_MIN


def cycle(srv, now: float | None = None) -> dict | None:
    """One try -> expect -> notice. None when nothing ran (off, paused, held,
    the guard, or no world to feel)."""
    now = time.time() if now is None else now
    rt = _rt(srv)
    if not _runs(srv) or not _guard_ok(rt, now):
        return None
    cur = _current(srv)
    if cur is None:
        return None
    est = srv.entity_store
    ent = _entity(srv)
    pos = np.asarray(ent.position, dtype=float) if ent.position is not None else None
    if pos is None or len(pos) != len(cur):
        pos = _unit(cur + _seed_dir("birthplace", len(cur)))      # born near the current, not on it
    rt["last"] = now
    rt["burst"].append(now)
    n = sum(1 for r in _read(srv) if r.get("kind") == "cycle") + 1
    seed = f"{n}"
    aim = None
    if 1 in _passed(_read(srv)):            # steer: earned by feeling itself
        if (rt.get("aim") is None or rt.get("aim_count", 0) >= AIM_HOLD
                or len(rt["aim"]) != len(pos)):
            rt["aim"], rt["aim_n"], rt["aim_count"] = _unit(pos + AIM_REACH * _seed_dir(f"aim:{n}", len(pos))), n, 0
        rt["aim_count"] += 1
        aim = rt["aim"]
    from .night import method_call
    _ctx.cycle = {"srv": srv, "pos": pos, "current": cur, "aim": aim}
    try:
        outs = []
        for m in ("expect", "try", "notice"):
            outs.append(method_call(srv, est, None, None, ent.eid, m, {"seed": seed}))
            if outs[-1].get("rc") != 0:
                break
        ok = all(o.get("rc") == 0 for o in outs)
        c = _ctx.cycle
    finally:
        _ctx.cycle = None
    if not ok or "observed" not in c:
        rt["fails"] += 1
        if rt["fails"] >= HOLD_AFTER:
            rt["held"] = True
        _append(srv, {"ts": now, "kind": "error", "n": n,
                      "why": next((str(o.get("head") or o.get("error") or "") for o in outs
                                   if o.get("rc") != 0), "")[:200]})
        return None
    rt["fails"] = 0
    gap = float(np.linalg.norm(c["observed"] - c["expected"]))
    naive = float(np.linalg.norm(c["observed"] - c["intent"]))
    rec = {"ts": now, "kind": "cycle", "n": n, "act": "move",
           "step": round(float(np.linalg.norm(c["mine"])), 4) if c.get("mine") is not None else STEP,
           "push": c["push"], "gap": gap, "naive": naive,
           "change": "world" if c["push"] > CALM else "knowledge"}
    if aim is not None:
        # where it landed against its aim, and where the same stride would have
        # landed without leaning into the current (the world's push is the same)
        world = PUSH_GAIN * (cur - pos)
        naive_land = _unit(pos + c["naive_mine"] + world)
        rec.update({"aim_n": rt["aim_n"], "aim_err": float(np.linalg.norm(c["observed"] - aim)),
                    "aim_naive": float(np.linalg.norm(naive_land - aim))})
    _append(srv, rec)
    est.upsert(ent, embedder=None)
    _mark_rung7_restart(srv, now)
    if _available(srv, 2):
        _reach_once(srv, est, ent, n, now)
    if _available(srv, 6):
        _meet(srv, str(n), now)
    _open_rungs(srv)
    _show(srv, ent, pos, c, rec)
    rt["wake"].set()                      # my state changed: that is what wakes the next try
    return rec


def _available(srv, rung: int) -> bool:
    """A rung's skill may be practised: built, under the limit, the one below passed."""
    return rung in LADDER and rung <= _rt(srv)["limit"] and (rung - 1) in _passed(_read(srv))


def _targets(srv) -> list[str]:
    from .night import effective_script
    est = srv.entity_store
    out = []
    for eid, e in list((est.docs or {}).items()):
        if eid == _eid():
            continue
        try:
            code, _ = effective_script(est, e, "getter")
        except Exception:
            code = None
        if code:
            out.append(eid)
    return sorted(out)


def _reach_once(srv, est, ent, n: int, now: float) -> None:
    """Rung 2's practice: expect -> reach -> notice on one thing, then the
    blind audit's bookkeeping."""
    import random
    _audit_tick(srv, now)
    targets = _targets(srv)
    if not targets:
        return
    steer = _available(srv, 3)
    timed = _available(srv, 4)
    recs = _read(srv)
    kinds = _kinds(recs) if steer else {}
    rng = random.Random(f"reach:{n}")
    lam = None
    reply = _pending(recs, now) if _available(srv, 7) else None
    if reply is not None and reply["to"] not in targets:
        reply = None
    if reply is not None and _hold_reply(srv, reply, now):
        targets = [e for e in targets if e != reply["to"]]   # wait for the reply before looking
        reply = None
        if not targets:
            return
    if reply is not None:                          # rung 7: after I speak I check for the reply
        eid = reply["to"]
        lam = _lam(recs, eid) if timed else None
    elif timed:                                    # rung 4: look when a change has become likely
        eid, lam = _due(srv, recs, targets, kinds, now, rng)
        if eid is None:
            rt = _rt(srv)
            rt["waits"] = rt.get("waits", 0) + 1
            if rt["waits"] % WAIT_EVERY == 1:
                _append(srv, {"ts": now, "kind": "wait", "n": n})
            return
    else:
        tilt = _curiosity(recs, targets)           # #212: learning progress tilts the base weights
        if steer:                                  # rung 3: act on the sort -- silent things rarely
            weights = [(SILENT_WEIGHT if kinds.get(e) == "silent" else 1.0) * tilt[e] for e in targets]
        else:
            weights = [tilt[e] for e in targets]
        eid = rng.choices(targets, weights=weights)[0]
    last = (_tempo(srv).get(eid) or {}).get("last")
    au = _rt(srv).get("audit")
    inside = bool(au and au.get("rung", 2) == 2 and au["eid"] == eid)
    in5 = bool(au and au.get("rung") == 5 and au["eid"] == eid)
    from .night import method_call
    _ctx.cycle = {"srv": srv, "act": "reach", "eid": eid, "audit": inside, "now": now, "reply": reply,
                  "lam": lam, "dt": (now - float(last)) if last is not None else None}
    try:
        outs = []
        for m, args in (("expect", {"seed": str(n)}), ("reach", {"target": eid}), ("notice", {"seed": str(n)})):
            outs.append(method_call(srv, est, None, None, ent.eid, m, args))
            if outs[-1].get("rc") != 0:
                break
        c = _ctx.cycle
    finally:
        _ctx.cycle = None
    if "answer" not in c:
        _append(srv, {"ts": now, "kind": "error", "n": n, "act": "reach",
                      "why": next((str(o.get("head") or o.get("error") or "") for o in outs
                                   if o.get("rc") != 0), "")[:200]})
        return
    rec = {"ts": now, "kind": "reach", "n": n, "eid": eid, "p": round(c["p"], 4),
           "answer": c["answer"], "why": c["why"],
           "dt": (round(now - float(last), 3) if last is not None else None)}
    try:
        from .eids import field_id
        rec["fid"] = field_id(eid)                       # #244: where the page finds it on the field
    except Exception:
        pass
    if c.get("toy") and c["toy"].get("at1") is not None:  # #244: who changed the toy (ground truth)
        try:
            rec["by"] = _toy_by(recs, eid, c["toy"])
            rec["log_at"] = c["toy"]["at1"]
        except Exception:
            pass
    if reply is not None:                                # rung 7: the reply check after a push
        rec["reply"] = True
        rec["replied"] = _replied(srv, reply, now, c["answer"])   # #258: they spoke AFTER the push
    if c.get("p_self") is not None:                      # rung 8: self's own read (#254)
        rec["p_self"] = round(c["p_self"], 4)
    if c.get("p_llm") is not None:                       # rung 8: the advisor's read, same look
        rec["p_llm"] = round(c["p_llm"], 4)
        if c.get("llm_model"):
            rec["llm_model"] = c["llm_model"]
        rec["llm_cal"] = list(c["llm_cal"])
        rec["p_mix"] = round(c["p_mix"], 4)
    if timed and reply is None:                          # a reply check is not a patience-timed look
        rec["timed"] = True
        if lam:
            rec["lam"] = round(lam, 6)
    if steer:
        rec["steer"] = True
        pp = ent.properties or {}
        sd = dict(pp.get("script_data") or {})
        sd["kinds"] = {e: k for e, k in kinds.items() if k != "unknown"}
        pp["script_data"] = sd
        ent.properties = pp
    if _available(srv, 6):
        pp = ent.properties or {}
        sd = dict(pp.get("script_data") or {})
        sd["split"] = {e: s for e, s in _split(recs, _tempo(srv)).items() if s != "unknown"}
        pp["script_data"] = sd
        ent.properties = pp
    if inside:
        rec["audit"] = True
        if c["answer"] != "none":
            au["reaches"] += 1
            au["changes"] += int(c["answer"] == "changed")
    watch = _available(srv, 5)
    if watch:
        rec["watch5"] = True
    if in5:
        rec["audit5"] = au["phase"]
        if c["answer"] != "none":
            au["looks"] += 1
    _append(srv, rec)
    _place_once(srv, eid)
    if watch and c["answer"] != "none" and not inside:
        _shift_check(srv, eid, now)
    _audit_tick(srv, now)
    _audit5_tick(srv, now)


def _lam(recs: list[dict], eid: str) -> float | None:
    """A thing's rate of change in time: the maximum-likelihood lambda for
    P(changed | dt) = 1 - exp(-lambda dt) over its last LAM_LOOKS answered
    looks (outside audits); None before it has any. After a rung-5 shift the
    fit reads only the looks since it, but not from nothing (#235): the rate
    before the shift -- the thing's own last LAM_LOOKS looks, or the seat's when
    it has none -- is a prior worth LAM_PRIOR_LOOKS looks, so 3-5 looks alone can't
    run lambda to the grid's edge and the new looks lead after ~10."""
    since = _last_shift_ts(recs, eid)
    fit = [(r["eid"], r["ts"], float(r["dt"]), r["answer"] == "changed") for r in recs
           if r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5")
           and r.get("answer") in ("changed", "unchanged") and r.get("dt")]
    mine = [o for o in fit if o[0] == eid]
    obs = [o for o in mine if since is None or o[1] > since][-LAM_LOOKS:]
    if not obs:
        return None
    ll = _lam_ll(obs)
    if since is not None:
        prior = ([o for o in mine if o[1] <= since] or [o for o in fit if o[1] <= since])[-LAM_LOOKS:]
        if prior:                                  # + half a look each way (Jeffreys): peaks inside the grid
            mid = float(np.median([o[2] for o in prior]))
            half = 0.5 * (_lam_ll([(eid, 0, mid, True)]) + _lam_ll([(eid, 0, mid, False)]))
            ll = ll + LAM_PRIOR_LOOKS * (_lam_ll(prior) + half) / (len(prior) + 1)
    return float(LAM_GRID[int(np.argmax(ll))])


def _lam_ll(obs: list[tuple]) -> np.ndarray:
    """The log-likelihood of (eid, ts, dt, changed) looks at each LAM_GRID rate."""
    dt = np.array([o[2] for o in obs])[None, :]
    ch = np.array([o[3] for o in obs], dtype=float)[None, :]
    p = np.clip(1.0 - np.exp(-LAM_GRID[:, None] * dt), 1e-9, 1 - 1e-9)
    return (ch * np.log(p) + (1 - ch) * np.log(1 - p)).sum(axis=1)


def _last_shift_ts(recs: list[dict], eid: str):
    return next((r["ts"] for r in reversed(recs) if r.get("kind") == "shift" and r.get("eid") == eid), None)


def _tail(p: float, w: int, k: int) -> float:
    """The chance of a window at least this far out under the long memory:
    P(X >= k) when it came out high, P(X <= k) when low; X ~ Binomial(w, p)."""
    import math
    pmf = [math.comb(w, i) * p ** i * (1 - p) ** (w - i) for i in range(w + 1)]
    return sum(pmf[k:]) if k / w > p else sum(pmf[:k + 1])


def _shift_check(srv, eid: str, now: float) -> None:
    """RUNG 5's noticing: are my last few looks at this thing both far from and
    unlikely under my long memory of it? Then the world changed: record the
    shift and re-learn from those looks."""
    recs = _read(srv)
    since = _last_shift_ts(recs, eid)
    looks = [r["answer"] == "changed" for r in recs
             if r.get("kind") == "reach" and r.get("eid") == eid and not r.get("audit")
             and r.get("answer") in ("changed", "unchanged") and (since is None or r["ts"] > since)]
    t = _tempo(srv).get(eid) or {}
    C, A = float(t.get("changed", 0)), float(t.get("answered", 0))
    for w in SHIFT_WINDOWS:
        if len(looks) < w or A - w < 1:
            continue
        k = sum(looks[-w:])
        p = (C - k + 0.5) / (A - w + 1.0)            # the long memory, without the window
        if abs(k / w - p) > SHIFT_GAP and _tail(p, w, k) < SHIFT_P:
            _append(srv, {"ts": now, "kind": "shift", "eid": eid, "was": round(p, 3),
                          "now": round(k / w, 3), "looks": w})
            t.update({"changed": k, "answered": w})  # re-learn from the looks that showed it
            t.pop("after", None)                     # the one bit of memory restarts with the new world
            _save_json(srv, "tempo.json", _tempo(srv))
            au = _rt(srv).get("audit")
            if au and au.get("rung") == 5 and au["eid"] == eid:
                au["shifts"].append({"phase": au["phase"], "down": k / w < p, "at": au["looks"]})
            return


def _audit5_tick(srv, now: float) -> None:
    """RUNG 5'S BLIND AUDIT (stop the source, then restart it): a moving
    thing's getter is paused while self looks at it AUDIT5_LOOKS times, then
    restarted for AUDIT5_LOOKS more. Self isn't told -- and here it DOES learn
    (re-learning is the point). It passes if self flagged "went still" during
    the pause and "moves again" after it."""
    from . import watch_agents as _WA
    rt = _rt(srv)
    au = rt.get("audit")
    if au is not None:
        if au.get("rung") != 5:
            return
        if au["phase"] == "pause" and (au["looks"] >= AUDIT5_LOOKS or time.time() >= au["until"]):
            _WA.unpause_getter(au["eid"])
            au.update({"phase": "resume", "paused_looks": au["looks"], "looks": 0,
                       "until": time.time() + AUDIT5_RESUME_S})
            _append(srv, {"ts": now, "kind": "audit", "rung": 5, "phase": "resume", "eid": au["eid"]})
        elif au["phase"] == "resume":
            again = any(not s["down"] for s in au["shifts"] if s["phase"] == "resume" and s["at"] <= AUDIT5_LOOKS)
            if again or au["looks"] >= AUDIT5_LOOKS or time.time() >= au["until"]:
                still = any(s["down"] for s in au["shifts"] if s["phase"] == "pause" and s["at"] <= AUDIT5_LOOKS)
                full = au["paused_looks"] >= AUDIT5_LOOKS and (again or au["looks"] >= AUDIT5_LOOKS)
                rt["audit"] = None
                _append(srv, {"ts": now, "kind": "audit", "rung": 5, "phase": "end", "eid": au["eid"],
                              "still": still, "again": again,
                              "passed": (still and again) if full else None})
        return
    if not _available(srv, 5):
        return
    recs = _read(srv)
    if any(r.get("kind") == "audit" and r.get("rung") == 5 and r.get("phase") == "end"
           and r.get("passed") is True for r in recs):
        return
    if sum(1 for r in recs if r.get("watch5")) < RUNG5_LOOKS:
        return
    moving = [e for e, k in _kinds(recs).items() if k == "moving"]
    if not moving:
        return
    eid = max(sorted(moving), key=lambda e: _p_changed(srv, e))   # the surest mover: its stop is unmistakable
    until = time.time() + AUDIT5_PAUSE_S
    _WA.pause_getter(eid, until)
    rt["audit"] = {"rung": 5, "eid": eid, "phase": "pause", "looks": 0, "until": until, "shifts": []}
    _append(srv, {"ts": now, "kind": "audit", "rung": 5, "phase": "pause", "eid": eid})


def _due(srv, recs, targets, kinds, now, rng):
    """Rung 4's choice: the things whose next change has become likely
    (PATIENCE_P), or a silent one now and then; (None, None) -- wait."""
    tempo = _tempo(srv)
    due = []
    for e in targets:
        if kinds.get(e) == "silent":
            continue
        last = (tempo.get(e) or {}).get("last")
        lam = _lam(recs, e)
        if last is None or lam is None:
            due.append((e, lam))
            continue
        wait = min(PATIENCE_MAX_S, max(PACE_S, -np.log(1.0 - PATIENCE_P) / lam))
        if now - float(last) >= wait:
            due.append((e, lam))
    silent = [e for e in targets if kinds.get(e) == "silent"]
    if silent and rng.random() < SILENT_WEIGHT:
        return rng.choice(silent), None
    if not due:
        return None, None
    tilt = _curiosity(recs, [e for e, _ in due])   # #212: among the due, the frontier first
    return rng.choices(due, weights=[tilt[e] for e, _ in due])[0]


# ---- curiosity: where self practises (#212) ----------------------------------

def _ordinary_errors(recs: list[dict]) -> dict:
    """eid -> the Brier errors (answer - p)^2 of its ORDINARY looks, oldest
    first: answered reaches outside every audit. A look inside a rung-2 audit
    (`audit`) or either phase of a rung-5 audit (`audit5`: the paused source and
    its restart) never counts -- self stays blind to its audits (#161 rule 2)."""
    out: dict[str, list] = {}
    for r in recs:
        if (r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged")
                and not r.get("audit") and not r.get("audit5") and r.get("p") is not None):
            x = 1.0 if r["answer"] == "changed" else 0.0
            out.setdefault(r["eid"], []).append((x - float(r["p"])) ** 2)
    return out


def _progress(recs: list[dict], detail: bool = False) -> dict:
    """LEARNING PROGRESS per thing: its mean error over the CURIO_WINDOW
    ordinary looks before the latest CURIO_WINDOW, minus its mean error over
    the latest ones (positive: its prediction is getting better). The window
    means are the smoothing. A thing with fewer than two windows of looks has
    none yet. With `detail`, {eid: {progress, error}} (error: the recent mean).
    Computed from the record at the moment it is read -- never stored."""
    W = CURIO_WINDOW
    out = {}
    for eid, errs in _ordinary_errors(recs).items():
        if len(errs) < 2 * W:
            continue
        early, late = sum(errs[-2 * W:-W]) / W, sum(errs[-W:]) / W
        out[eid] = {"progress": early - late, "error": late} if detail else early - late
    return out


def _curiosity(recs: list[dict], targets: list[str]) -> dict:
    """THE TILT on the base weights (kinds at rung 3, patience at rung 4):
    each target's multiplier, by the RISE of its bar (#161 rule 1) -- the
    fastest-improving thing gets 1, a flat one (mastered or unlearnable) or a
    worsening one the floor CURIO_FLOOR, so it is still retried now and then; a
    thing too young to have progress gets 1 (worth a look). With no progress
    anywhere above CURIO_MIN, or CURIOSITY off, every multiplier is 1: the base
    chooser runs as it did. Read only when self chooses (rule 3)."""
    if not CURIOSITY:
        return {e: 1.0 for e in targets}
    lp = _progress(recs)
    rise = {e: max(0.0, lp[e]) for e in targets if e in lp}
    top = max(rise.values(), default=0.0)
    if top < CURIO_MIN:
        return {e: 1.0 for e in targets}
    return {e: (CURIO_FLOOR + (1.0 - CURIO_FLOOR) * rise[e] / top) if e in rise else 1.0
            for e in targets}


def _audit_tick(srv, now: float) -> None:
    """THE BLIND AUDIT (stop the source): a candidate's getter is paused until
    I have reached for it AUDIT_REACHES more times; a change recorded while
    the source is stopped is a mark that can't fail -- the audit fails. Self
    isn't told: its reaches answer "unchanged", and it learns nothing inside."""
    from . import watch_agents as _WA
    rt = _rt(srv)
    au = rt.get("audit")
    if au is not None and au.get("rung", 2) != 2:
        return                             # another rung's audit holds the slot
    if au is not None:
        done = au["reaches"] >= AUDIT_REACHES
        if done or time.time() >= au["until"]:
            _WA.unpause_getter(au["eid"])
            rt["audit"] = None
            _append(srv, {"ts": now, "kind": "audit", "phase": "end", "eid": au["eid"],
                          "reaches": au["reaches"], "changes": au["changes"],
                          "passed": (au["changes"] == 0) if done else None})
        return
    recs = _read(srv)
    if 2 in _passed(recs):
        return                             # rung 2 is earned: no more pausing getters for it
    audited = {r["eid"] for r in recs if r.get("kind") == "audit" and r.get("phase") == "end"
               and r.get("passed") is not None}
    for eid, st in _rung2_stats(recs, _tempo(srv)).items():
        if eid in audited or not st["candidate"] or not st["auditable"]:
            continue
        until = time.time() + AUDIT_MAX_S
        _WA.pause_getter(eid, until)
        rt["audit"] = {"eid": eid, "until": until, "reaches": 0, "changes": 0, "rung": 2}
        _append(srv, {"ts": now, "kind": "audit", "phase": "start", "eid": eid, "rung": 2})
        return


def _pull(srv, dim: int):
    """Where it has learned the current pulls: the fixed point of
    push ~ b - g*pos (b = mean push + g*mean position), or None."""
    m = _model(srv, dim)
    if m is None or float(m.get("var") or 0.0) <= 1e-9:
        return None
    g = -float(m["cov"]) / float(m["var"])
    if g <= 1e-6:
        return None
    return _unit(np.asarray(m["mw"]) / g + np.asarray(m["mp"]))


def _show(srv, ent, pos, c, rec) -> None:
    """The cycle onto the viewer (fieldpulse.self_cycle): the points as
    vectors for the beat to place, the rest as numbers -- never text."""
    try:
        from . import fieldpulse as _FP
        fp = _FP.of(srv)
        if fp is None:
            return
        st = status(srv)
        fp.self_cycle({"from": pos, "intent": c["intent"], "expected": c["expected"],
                       "at": c["observed"], "pull": _pull(srv, len(pos)), "aim": c.get("aim")},
                      {"n": rec["n"], "gap": rec["gap"], "naive": rec["naive"], "push": rec["push"],
                       "rung": st["rung"], "bar": st["bar"], "skill": st["skill"],
                       "mode": st["mode"], "slipping": st["slipping"], "waiting_on": st["waiting_on"],
                       "next": st["next"], "cycles": st["cycles"], "held": st["held"],
                       "rung_name": st["rung_name"], "limit": st["limit"],
                       "split": st.get("split"), "meets": st.get("meets"),
                       "steer": (st.get("steer") or {}).get("skill")})
    except Exception:
        pass


# ---- the ladder and the exp bar ----------------------------------------------

def _rung1(recs: list[dict], tempo: dict | None = None) -> dict:
    pushed = [r for r in recs if r.get("kind") == "cycle" and r.get("change") == "world"][-RUNG1_CYCLES:]
    nv = sum(r["naive"] for r in pushed)
    skill = (1.0 - sum(r["gap"] for r in pushed) / nv) if nv > 0 else 0.0
    crit = {"pushes": min(1.0, len(pushed) / RUNG1_CYCLES),
            "skill": max(0.0, min(1.0, skill / RUNG1_SKILL))}
    return {"crit": crit, "skill": round(skill, 4), "passed": all(v >= 1.0 for v in crit.values())}


def _rung2_stats(recs: list[dict], tempo: dict) -> dict:
    """Per thing, over its last RUNG2_REACHES answered reaches (outside any
    audit): the Brier skill of my predictions against the seat-wide change
    rate, and whether it moves often enough to be audited."""
    ans = [r for r in recs if r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged")
           and not r.get("audit")]
    if not ans:
        return {}
    pool = ans[-RUNG2_REACHES * 4:]
    p0 = sum(r["answer"] == "changed" for r in pool) / len(pool)
    out = {}
    for eid in sorted({r["eid"] for r in ans}):
        mine = [r for r in ans if r["eid"] == eid][-RUNG2_REACHES:]
        a = [1.0 if r["answer"] == "changed" else 0.0 for r in mine]
        brier = sum((x - r["p"]) ** 2 for x, r in zip(a, mine))
        base = sum((x - p0) ** 2 for x in a)
        skill = (1.0 - brier / base) if base > 1e-9 else 0.0
        t = tempo.get(eid) or {}
        p_hat = (float(t.get("changed", 0)) + 0.5) / (float(t.get("answered", 0)) + 1.0)
        out[eid] = {"reaches": len(mine), "skill": round(skill, 4), "p": round(p_hat, 3),
                    "candidate": len(mine) >= RUNG2_REACHES and skill >= RUNG2_SKILL,
                    "auditable": p_hat >= AUDIT_P}
    return out


def _rung2(recs: list[dict], tempo: dict | None = None) -> dict:
    stats = _rung2_stats(recs, tempo or {})
    audits = {r["eid"]: r["passed"] for r in recs
              if r.get("kind") == "audit" and r.get("phase") == "end" and r.get("passed") is not None}
    if not stats:
        return {"crit": {"reaches": 0.0, "skill": 0.0, "audit": 0.0}, "skill": 0.0, "passed": False}
    def rank(eid):
        s = stats[eid]
        return (audits.get(eid) is True, s["auditable"], s["candidate"], s["skill"], s["reaches"])
    best = max(stats, key=rank)
    s = stats[best]
    crit = {"reaches": min(1.0, s["reaches"] / RUNG2_REACHES),
            "skill": max(0.0, min(1.0, s["skill"] / RUNG2_SKILL)),
            "audit": 1.0 if audits.get(best) is True else 0.0}
    return {"crit": crit, "skill": s["skill"], "eid": best,
            "passed": all(v >= 1.0 for v in crit.values())}


def _kinds(recs: list[dict], window: int = RUNG3_WINDOW) -> dict:
    """What each thing is, from my own looks (outside audits), over its last
    `window` reaches: silent (no answer), moving, still, intermittent -- or
    unknown, before KIND_MIN looks."""
    by: dict[str, list] = {}
    for r in recs:
        if r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5"):
            by.setdefault(r["eid"], []).append(r["answer"])
    out = {}
    for eid, ans in by.items():
        ans = ans[-window:]
        none = sum(a == "none" for a in ans)
        answered = len(ans) - none
        if len(ans) >= KIND_MIN and none / len(ans) >= 0.8:
            out[eid] = "silent"
        elif answered >= KIND_MIN:
            p = sum(a == "changed" for a in ans) / answered
            out[eid] = "moving" if p >= 0.6 else ("still" if p <= 0.2 else "intermittent")
        else:
            out[eid] = "unknown"
    return out


def _kind_rate(recs: list[dict], window: int = RUNG3_WINDOW) -> dict:
    """Each thing's RATE over its last `window` reaches (outside audits): the
    no-answer share and the change share among the answered (None before any
    answer). {} before KIND_MIN looks. The kind label is `_kinds`; the rate is
    what the two halves must agree on (a look's drift is not a new kind)."""
    by: dict[str, list] = {}
    for r in recs:
        if r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5"):
            by.setdefault(r["eid"], []).append(r["answer"])
    out = {}
    for eid, ans in by.items():
        ans = ans[-window:]
        if len(ans) < KIND_MIN:
            continue
        none = sum(a == "none" for a in ans)
        answered = len(ans) - none
        out[eid] = {"none": none / len(ans),
                    "p": (sum(a == "changed" for a in ans) / answered) if answered else None}
    return out


def _close(a: float | None, b: float | None) -> bool:
    """Two rates agree within a look's drift; a silent half on both sides."""
    return (abs(a - b) <= RUNG3_DRIFT) if (a is not None and b is not None) else (a is None and b is None)


def _split(recs: list[dict], tempo: dict | None = None) -> dict:
    """THE SELF/OTHER SPLIT (rung 6): each thing self predicts near-perfectly
    (Brier skill >= BODY_SKILL -- zero gap, its own echo) is its BODY; what it
    can't predict (answers variably or with delay -- a nonzero gap) is an OTHER;
    fewer than KIND_MIN answered looks is unknown."""
    out = {}
    for eid, s in _rung2_stats(recs, tempo or {}).items():
        if s["reaches"] < KIND_MIN:
            out[eid] = "unknown"
        else:
            out[eid] = "body" if s["skill"] >= BODY_SKILL else "other"
    return out


def _rung3(recs: list[dict], tempo: dict | None = None) -> dict:
    """Tell things apart: sorted reaches (looks), kinds whose RATES agree across
    the two halves of the last RUNG3_WINDOW reaches, the no-answer share under
    RUNG3_FOCUS (focus), and rung 2's skill still holding -- over the moving
    (auditable) things only, so a still counter can't carry the bar."""
    reaches = [r for r in recs if r.get("kind") == "reach" and not r.get("audit")]
    steered = [r for r in reaches if r.get("steer")]
    looks = min(1.0, len(steered) / RUNG3_LOOKS)
    last = reaches[-RUNG3_WINDOW:]
    half = len(last) // 2
    ra, rb = _kind_rate(last[:half]), _kind_rate(last[half:])
    stable = [e for e in ra if e in rb and _close(ra[e]["p"], rb[e]["p"]) and abs(ra[e]["none"] - rb[e]["none"]) <= RUNG3_DRIFT]
    kinds = _kinds(last)
    agree = bool(stable) and len({kinds.get(e) for e in stable} - {None, "unknown"}) >= 2
    tail = steered[-RUNG3_LOOKS:]
    share = (sum(r["answer"] == "none" for r in tail) / len(tail)) if tail else 1.0
    focus = 1.0 if share < RUNG3_FOCUS else max(0.0, (1.0 - share) / (1.0 - RUNG3_FOCUS))
    r2 = _rung2_stats(recs, tempo or {})
    best = max((s["skill"] for s in r2.values() if s["auditable"]), default=0.0)
    crit = {"looks": looks, "kinds": 1.0 if agree else 0.0, "focus": focus,
            "skill": max(0.0, min(1.0, best / RUNG2_SKILL))}
    return {"crit": crit, "skill": round(best, 4), "passed": all(v >= 1.0 for v in crit.values())}


def _rung4(recs: list[dict], tempo: dict | None = None) -> dict:
    """Patience: enough timed looks; they find news -- at least half the
    time, or 0.15 more often than its fixed-pace looks at the same things did
    (whichever is easier: a thing already newsy only has to stay so); and its
    predictions are calibrated (mean predicted chance within RUNG4_CALIB of
    the share that found news)."""
    ans = [r for r in recs if r.get("kind") == "reach" and not r.get("audit")
           and r.get("answer") in ("changed", "unchanged")]
    timed = [r for r in ans if r.get("timed")]
    if not timed:
        return {"crit": {"timed": 0.0, "news": 0.0, "calibration": 0.0}, "skill": 0.0, "passed": False}
    last = timed[-40:]
    news = sum(r["answer"] == "changed" for r in last) / len(last)
    same = {r["eid"] for r in last}
    fixed = [r for r in ans if not r.get("timed") and r["eid"] in same]
    base = (sum(r["answer"] == "changed" for r in fixed) / len(fixed)) if fixed else 0.0
    need = min(0.5, base + 0.15)
    err = abs(sum(r["p"] for r in last) / len(last) - news)
    crit = {"timed": min(1.0, len(timed) / RUNG4_TIMED),
            "news": 1.0 if news >= need else max(0.0, news / need),
            "calibration": 1.0 if err <= RUNG4_CALIB else max(0.0, 1.0 - (err - RUNG4_CALIB) / 0.4)}
    return {"crit": crit, "skill": round(news, 4), "passed": all(v >= 1.0 for v in crit.values())}


def _rung5(recs: list[dict], tempo: dict | None = None) -> dict:
    """Notice the world changed: enough looks with noticing on, then the
    blind audit (went still during the pause, moves again after it). Its skill
    is the share of those two shifts it has noticed, 0..1 like the other rungs."""
    looks = sum(1 for r in recs if r.get("watch5"))
    ends = [r for r in recs if r.get("kind") == "audit" and r.get("rung") == 5 and r.get("phase") == "end"]
    ok = any(r.get("passed") is True for r in ends)
    crit = {"looks": min(1.0, looks / RUNG5_LOOKS), "audit": 1.0 if ok else 0.0}
    shifts = len([r for r in recs if r.get("kind") == "shift"])
    return {"crit": crit, "skill": min(1.0, shifts / RUNG5_SHIFTS),
            "passed": all(v >= 1.0 for v in crit.values())}


def _other(recs: list[dict]) -> str | None:
    """The other self is meeting now: the latest push's `to` (None before any)."""
    return next((r.get("to") for r in reversed(recs) if r.get("kind") == "push"), None)


def _rung6(recs: list[dict], tempo: dict | None = None) -> dict:
    """Meet an other: enough pushes to chat:out, enough answered looks at the
    answerer, and the answerer classed OTHER -- not body (a perfect echo is
    self's own echo, so the split reads 0 and the rung stays shut).

    It reads the CURRENT other only -- the latest push's `to` (#208): when the
    person changes, pushes to and looks at the previous one stop counting, so
    a rung passed against someone else slips until this person earns it."""
    to = _other(recs)
    pushes = [r for r in recs if r.get("kind") == "push" and r.get("to") == to]
    heard = sum(1 for r in recs if r.get("kind") == "reach" and r.get("eid") == to
                and r.get("answer") in ("changed", "unchanged") and not r.get("audit"))
    split = _split(recs, tempo or {}).get(to or "", "unknown")
    crit = {"pushes": min(1.0, len(pushes) / RUNG6_PUSHES),
            "heard": min(1.0, heard / RUNG6_HEARD),
            "split": 1.0 if split == "other" else 0.0}
    return {"crit": crit, "skill": 1.0 if split == "other" else 0.0,
            "eid": to, "passed": all(v >= 1.0 for v in crit.values())}


def _rung7(recs: list[dict], tempo: dict | None = None) -> dict:
    """Take turns (#258): self's pushes land in the window it learned gets
    replies (a) more often than a push at a shuffled moment would, and (b)
    get more replies than a timing-blind push would -- over its last
    RUNG7_PUSHES policy pushes at the CURRENT other with a reply check (see
    the constants). Staged: pushes, contingency, payoff. The headline skill
    is the payoff: the reply rate gained over timing-blind."""
    to = _other(recs)
    rows = _turn_rows(recs, to) if to else []
    w = _turn_score(rows)
    crit = {"pushes": min(1.0, w["n"] / RUNG7_PUSHES)}
    if not w["n"]:
        crit.update({"contingency": 0.0, "payoff": 0.0})
        return {"crit": crit, "skill": 0.0, "passed": False, **{k: w[k] for k in w if k != "passed"}}
    exc = w["share"] - w["chance"]
    crit["contingency"] = max(0.0, min(1.0, w["z"] / RUNG7_CONT_Z, exc / RUNG7_CONT_MARGIN))
    crit["payoff"] = max(0.0, min(1.0, w["payoff_z"] / RUNG7_PAY_Z, w["payoff"] / RUNG7_PAY_MARGIN))
    out = {"crit": crit, "skill": round(w["payoff"], 4), "eid": to,
           "passed": bool(w["passed"] and all(v >= 1.0 for v in crit.values()))}
    out.update({k: (round(v, 4) if isinstance(v, float) else v) for k, v in w.items() if k != "passed"})
    return out


def _rung8(recs: list[dict], tempo: dict | None = None) -> dict:
    """Understand an other (#254; rung 7 until #258, unchanged): over the last RUNG8_LOOKS reply checks at it,
    SELF's prediction p (its own p_self, mixed with the model's p_llm by the
    advisor's earned weight when the model answered) removes RUNG8_MARGIN of
    the RUNNING RATE's Brier error -- the other's prior-smoothed reply rate
    over the checks before each one, no lookahead. Knowing the rate scores 0;
    only what self learned beyond it (timing, runs) counts. Only checks at the
    CURRENT other count (#208, see _rung6)."""
    to = _other(recs)
    if to is None:
        rc = [r for r in recs if r.get("kind") == "reach" and r.get("reply") and not r.get("audit")]
        to = rc[-1].get("eid") if rc else None
    rows = _rung8_series(recs, to)[-RUNG8_LOOKS:] if to else []
    crit = {"looks": min(1.0, len(rows) / RUNG8_LOOKS)}
    if not rows:
        return {"crit": {**crit, "skill": 0.0}, "skill": 0.0, "passed": False}
    b_rate = sum((r["o"] - r["rate"]) ** 2 for r in rows) / len(rows)
    b_p = sum((r["o"] - r["p"]) ** 2 for r in rows) / len(rows)
    skill = (b_rate - b_p) / b_rate if b_rate > 1e-9 else 0.0
    crit["skill"] = max(0.0, min(1.0, skill / RUNG8_MARGIN))
    return {"crit": crit, "skill": round(skill, 4),
            "eid": to, "passed": all(v >= 1.0 for v in crit.values())}


PROGRESS = {1: _rung1, 2: _rung2, 3: _rung3, 4: _rung4, 5: _rung5, 6: _rung6, 7: _rung7,
            8: _rung8}   # rung -> its test over the record
# #249: the line each rung's headline skill has to reach (the number status()['skill'] reads);
# rung 4's news share has a moving bar (its fixed-pace base + 0.15), so it has none here
SKILL_PASS = {1: RUNG1_SKILL, 2: RUNG2_SKILL, 3: RUNG2_SKILL, 4: None, 5: 1.0, 6: 1.0,
              7: RUNG7_PAY_MARGIN, 8: RUNG8_MARGIN}


def _rung_lines(recs: list[dict]) -> list[dict]:
    """The rung passes that count: every `rung` line except one with a later
    `rung_void` line for its rung (#254: a pass a broken test recorded stays
    on the append-only record but doesn't count; a later pass does), and --
    #258, the ladder flip -- a pass above rung 6 only when it carries
    "ladder": LADDER_V (before the flip rung 7 was "understand an other": an
    old pass is neither the new rung 7 nor rung 8)."""
    voids: dict = {}
    for r in recs:
        if r.get("kind") == "rung_void":
            voids[r.get("rung")] = max(voids.get(r.get("rung"), 0.0), float(r.get("ts") or 0))
    return [r for r in recs if r.get("kind") == "rung"
            and not (r.get("rung") in voids and float(r.get("ts") or 0) < voids[r.get("rung")])
            and (int(r.get("rung") or 0) < FLIPPED_FROM or r.get("ladder") == LADDER_V)]


def _passed(recs: list[dict]) -> set:
    return {0} | {r["rung"] for r in _rung_lines(recs)}


def _open_rungs(srv) -> None:
    """A rung opens when its test passes -- only under the operator's limit,
    and only above a rung already passed (the ladder is climbed in order)."""
    recs = _read(srv)
    done, lim = _passed(recs), _rt(srv)["limit"]
    for n in sorted(PROGRESS):
        if n in done or n > lim or (n - 1) not in done:
            continue
        p = PROGRESS[n](recs, _tempo(srv))
        if p["passed"]:
            line = {"ts": time.time(), "kind": "rung", "rung": n, "skill": p["skill"], "ladder": LADDER_V}
            if p.get("eid"):
                line["eid"] = p["eid"]
            _append(srv, line)
            recs, done = _read(srv), done | {n}


def _staged(crit: dict) -> tuple[float, str | None]:
    """THE BAR, STAGED: a rung's criteria are stages, in order (rung 1:
    pushes then skill; rung 2: reaches, skill, the audit). The bar fills
    through them -- each stage one equal share -- and names the first one not
    yet met; an earlier stage that slips takes the bar back with it. (The
    weakest-criterion bar sat at 0% for ~20 minutes on the live seat while
    reaches piled up: skill and audit can't move before enough reaches.)"""
    if not crit:
        return 1.0, None
    k = len(crit)
    for i, (name, v) in enumerate(crit.items()):
        if v < 1.0:
            return (i + max(0.0, v)) / k, name
    return 1.0, None


def status(srv) -> dict:
    rt = _rt(srv)
    recs = _read(srv)
    born = any(r.get("kind") == "birth" for r in recs)
    done = _passed(recs)
    rung = max(done)
    lim = rt["limit"]
    nxt = rung + 1 if rung + 1 in LADDER else None
    # the bar reads the next rung's test when self may practise it; else (none
    # built, or held by the limit) the top rung's own -- so a passed rung can
    # be seen slipping
    tempo = _tempo(srv)
    run_ = (lambda n: PROGRESS[n](recs, tempo))
    aim = nxt if (nxt in PROGRESS and nxt <= lim) else (rung if rung in PROGRESS else None)
    prog = run_(aim) if aim is not None else {"crit": {}, "skill": 0.0, "passed": True}
    bar, waiting = _staged(prog["crit"])
    slipping = bool(rung >= 1 and rung in PROGRESS and not run_(rung)["passed"])
    if nxt is not None and nxt > lim and rt["mode"] != "off" and not slipping:
        waiting = "the limit"                    # it may be ready; the operator holds it here
    if rt["mode"] != "off" and _current(srv) is None:
        # no current right now (a fresh boot: nobody has spoken yet) -- say
        # so, or the switch reads "on" over a loop that is rightly idle
        if not any(r.get("kind") == "cycle" for r in recs):
            bar = 0.0
        waiting = "the world"

    ladder = []
    for n, name in RUNG_NAMES.items():
        if n not in LADDER:
            state = "unbuilt"
        elif n in done:
            state = "passed"
        elif n > lim:
            state = "capped" if (n - 1) in done else "locked"
        else:
            state = "current" if (n - 1) in done else "locked"
        ladder.append({"rung": n, "name": name, "state": state})
    r1 = _rung1(recs)
    spl = _split(recs, tempo)
    pushes = [r for r in recs if r.get("kind") == "push"]
    other = pushes[-1].get("to") if pushes else None
    now = time.time()
    # the exchange's ages for the home viewer's beam + flash (rung 6): how long
    # since self pushed (a beam) and since the other last answered (a flash) --
    # ages, not wall clocks, so the viewer's clock never has to agree with the seat's
    answered = [r for r in recs
                if r.get("kind") == "reach" and r.get("eid") == other
                and r.get("answer") == "changed" and not r.get("audit")]
    meets = {"pushes": len(pushes), "other": other,
             "push_age": round(now - pushes[-1]["ts"], 3) if pushes else None,
             "heard_age": round(now - answered[-1]["ts"], 3) if answered else None}
    return {"mode": rt["mode"], "limit": lim, "born": born, "held": rt["held"],
            "cycles": sum(1 for r in recs if r.get("kind") == "cycle"),
            "rung": rung, "rung_name": LADDER.get(rung), "next": nxt,
            "bar": round(bar, 4), "waiting_on": waiting, "skill": prog.get("skill", r1["skill"]),
            "skill_rung": aim, "skill_pass": SKILL_PASS.get(aim),          # #249: the bar the skill has to beat
            "skill_measured": any(v > 0 for v in (prog.get("crit") or {}).values()),   # ...and whether anything feeds it yet
            "slipping": slipping, "ladder": ladder,
            "world": {e: k for e, k in _kinds(recs).items() if k != "unknown"},
            "split": {e: s for e, s in spl.items() if s != "unknown"},
            "meets": meets,
            "steer": _steer_stats(recs),
            "curiosity": _curiosity_stats(recs),
            "rung7": _rung7_stats(recs),
            "rung8": _rung8_stats(recs),
            "toys": _toys(srv, recs),
            "off_field": _off_field(srv, recs)}


def _off_field(srv, recs: list[dict]) -> dict:
    """#244: of self's last OFF_FIELD_LOOKS looks, how many land on a thing
    with no live field point (the page then draws a ghost, not a beam to
    it), and which things those are (most looked-at first). Cached for
    TOY_TTL_S; None for `off` when the seat has no field to ask."""
    rt = _rt(srv)
    now = time.time()
    hit = rt.get("off_field")
    if hit is not None and now - hit[0] < TOY_TTL_S:
        return hit[1]
    looks = [r["eid"] for r in recs[-TOY_SCAN:] if r.get("kind") == "reach" and r.get("eid")][-OFF_FIELD_LOOKS:]
    fld = getattr(srv, "efield", None)
    get = getattr(fld, "get", None)
    out = {"looks": len(looks), "off": None, "things": []}
    if get is not None:
        from .eids import field_id
        on: dict[str, bool] = {}
        for e in set(looks):
            try:
                st = get(field_id(e))
                on[e] = bool(st is not None and st.tier == "live" and st.state is not None)
            except Exception:
                on[e] = False
        off = [e for e in looks if not on[e]]
        cnt: dict[str, int] = {}
        for e in off:
            cnt[e] = cnt.get(e, 0) + 1
        out["off"] = len(off)
        out["things"] = sorted(cnt, key=lambda e: (-cnt[e], e))[:12]
    rt["off_field"] = (now, out)
    return out


def recent(srv, n=30) -> list[dict]:
    """#230: the last n record lines, newest first, capped at RECENT_MAX --
    for the page's activity feed (copies: the record is never edited)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 30
    n = max(1, min(n, RECENT_MAX))
    with _lock:
        tail = _read(srv)[-n:]
    return [dict(r) for r in reversed(tail)]


def _short(eid: str) -> str:
    """membrane::Component:toy:dice -> toy:dice (the name a toy goes by)."""
    from .entities import MEMBRANE_NS
    s = eid[len(MEMBRANE_NS):] if eid.startswith(MEMBRANE_NS) else eid
    head, sep, rest = s.partition(":")
    return rest if sep and head[:1].isupper() and rest else s


def _toys(srv, recs: list[dict]) -> list[dict]:
    """#230: the things made for self to play with -- toy: and cradle: things,
    and words (w:) that carry a getter -- each with a health mark for the
    operator: `no getter` (it can't be read), `unread` (self has not reached
    it yet), `empty` (its last reads gave no answer), else `ok`. The store
    walk is cached for TOY_TTL_S; the reads are recounted each call."""
    rt = _rt(srv)
    now = time.time()
    cached = rt.get("toys")
    if cached is None or now - cached[0] > TOY_TTL_S:
        from .night import effective_script
        est = getattr(srv, "entity_store", None)
        found = []
        for eid, e in list(((est.docs if est is not None else None) or {}).items()):
            name = _short(eid)
            word = name.startswith(TOY_WORDS)
            tb = (e.properties or {}).get("toybox") or {}
            kind = tb.get("kind")                              # #239: a built-in toy, any name
            if not (word or kind or name.startswith(TOY_PREFIXES)):
                continue
            try:
                getter = bool(effective_script(est, e, "getter")[0])
            except Exception:
                getter = False
            if word and not getter and not kind:
                continue
            found.append((eid, getter, kind or "script", tb.get("name") or "", tb.get("block")))
        cached = rt["toys"] = (now, sorted(found))
    eids = {t[0] for t in cached[1]}
    reads: dict[str, list] = {e: [] for e in eids}
    for r in reversed(recs[-TOY_SCAN:]):
        if r.get("kind") == "reach" and not r.get("audit") and r.get("eid") in eids:
            got = reads[r["eid"]]
            if len(got) < TOY_READS:
                got.append(r.get("answer"))
    out = []
    for eid, getter, kind, toy, block in cached[1]:
        got = reads[eid]
        answered = sum(1 for a in got if a in ("changed", "unchanged"))
        health = ("no getter" if not getter else "unread" if not got
                  else "empty" if not answered else "ok")
        row = {"eid": eid, "kind": kind, "getter": getter, "reads": len(got), "answered": answered,
               "health": health}
        if toy:                                            # #244: the play buttons' target
            row["toy"] = toy
            if block:
                row["block"] = block
        out.append(row)
    return out


def _rung7_stats(recs: list[dict]) -> dict:
    """For the operator (#258): rung 7 at the current other -- its policy
    pushes (`pushes`) and those whose reply check came (`checked`); per
    spoke_age bin the pushes, replies and reply rate over the last
    TURN_MEMORY (what the policy reads) and the window it believes best;
    over the window the test reads: the in-window share against chance and
    its z (contingency), the reply rate against timing-blind's, the payoff
    and its t."""
    to = _other(recs)
    pushes = sum(1 for r in recs if r.get("kind") == "push" and r.get("to") == to and r.get("aim") is not None)
    rows = _turn_rows(recs, to) if to else []
    k, n = _turn_post(rows)
    best = None
    if n.sum() > 0:
        mean = (k + 1.0) / (n + 2.0)
        best = TURN_NAMES[max(range(len(k)), key=lambda b: (mean[b], n[b], -b))]
    bins = [{"bin": TURN_NAMES[b], "pushes": int(n[b]), "replied": int(k[b]),
             "rate": round(float(k[b] / n[b]), 4) if n[b] else None} for b in range(len(k))]
    w = _turn_score(rows)

    def r4(x):
        return None if x is None else round(float(x), 4)
    return {"pushes": pushes, "checked": len(rows[-TURN_MEMORY:]), "bins": bins, "best": best,
            "window": w["n"], "share": r4(w["share"]), "chance": r4(w["chance"]), "z": round(w["z"], 2),
            "payoff": r4(w["payoff"]), "payoff_z": round(w["payoff_z"], 2),
            "reply_rate": r4(w["reply_rate"]), "blind_rate": r4(w["blind_rate"])}


def _rung8_stats(recs: list[dict]) -> dict:
    """For the operator (#217, #254): rung 8 at the current other -- the pushes
    that asked the model, those whose reply check came (`checked`), those the
    model answered in time (`paired`), the pair rate per checked ask; over
    the reply checks rung 8 reads, the Brier error of self's own p_self, of
    the mix self passes on (`brier_mix`), of the running rate it must beat,
    and of the model alone on the checks it answered; the weight self gives
    the current advisor's next answer and that advisor's model id; and the
    base (all-time) and recent rates the model is told (#246)."""
    to = _other(recs)
    asked = [r for r in recs if r.get("kind") == "push" and r.get("asked") and r.get("to") == to]
    checked = paired = 0
    for p in asked:
        look = _first_look_after(recs, to, float(p.get("ts") or 0))
        if look is None:
            continue
        checked += 1
        paired += int(look.get("p_llm") is not None and look.get("answer") in ("changed", "unchanged"))
    allrows = _rung8_series(recs, to) if to else []
    rows = allrows[-RUNG8_LOOKS:]
    llm = [r for r in rows if r.get("p_llm") is not None]

    def brier(rs, key):
        return round(sum((r["o"] - float(r[key])) ** 2 for r in rs) / len(rs), 4) if rs else None
    model = next((r["model"] for r in reversed(allrows) if r.get("p_llm") is not None), None)
    return {"asked": len(asked), "checked": checked, "paired": paired,
            "pair_rate": round(paired / checked, 4) if checked else None,
            "checks": len(rows),
            "brier_self": brier(rows, "p_self"), "brier_mix": brier(rows, "p"),
            "brier_rate": brier(rows, "rate"), "brier_model": brier(llm, "p_llm"),
            "advisor_weight": _advisor_cal(allrows, model)[1],
            "advisor_model": model,
            "base_rate": round(_base_rate(recs, to)[0], 4) if to else None,
            "recent_rate": round(_recent_rate(recs, to)[0], 4) if to else None}


def _curiosity_stats(recs: list[dict], top: int = 3) -> list[dict]:
    """For the operator (#212): the things whose prediction is improving
    fastest -- their learning progress and recent error, best first. Self's
    choice reads the same numbers; nothing here is stored or fed back."""
    lp = _progress(recs, detail=True)
    best = sorted(lp.items(), key=lambda kv: (-kv[1]["progress"], kv[0]))[:top]
    return [{"eid": e, "progress": round(d["progress"], 4), "error": round(d["error"], 4)}
            for e, d in best]


def _steer_stats(recs: list[dict]) -> dict | None:
    """How close it lands to its aim (last 20 steered cycles), against the
    same stride without leaning into the current."""
    rs = [r for r in recs if r.get("kind") == "cycle" and "aim_err" in r][-20:]
    if not rs:
        return None
    err = sum(r["aim_err"] for r in rs) / len(rs)
    naive = sum(r["aim_naive"] for r in rs) / len(rs)
    return {"err": round(err, 4), "naive": round(naive, 4),
            "skill": round(1.0 - err / naive, 4) if naive > 1e-9 else 0.0}


# ---- the thread -----------------------------------------------------------

def start(srv) -> threading.Thread:
    """The loop: wakes on its own change (or the switch), runs one cycle when
    the guard allows, sleeps when off."""
    rt = _rt(srv)

    def run():
        while True:
            rt["wake"].wait(timeout=PACE_S)
            rt["wake"].clear()
            try:
                if _runs(srv):
                    wait = (rt["last"] + PACE_S - time.time()) if rt["last"] else 0.0
                    if wait > 0:
                        time.sleep(wait)
                    cycle(srv)
            except Exception as e:                          # noqa: BLE001
                import sys
                print(f"[self] cycle failed ({e!r})", file=sys.stderr)
    t = threading.Thread(target=run, name="self-loop", daemon=True)
    t.start()
    return t
