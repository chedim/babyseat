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
              7: "understand an other", 8: "recognize itself", 9: "change itself", 10: "speak"}
LADDER = {n: RUNG_NAMES[n] for n in (0, 1, 2, 3, 4, 5, 6, 7)}          # the rungs built so far

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
MEET_EVERY = 30             # cycles between self's pushes to chat:out (not every try)
RUNG7_LOOKS = 20            # paired looks (habit + model) at the other the test reads
RUNG7_MARGIN = 0.15         # the model must remove this share of habit's Brier error to pass
RUNG7_CHECK_S = 120.0       # after a push self checks for the reply this long after (#215)
RUNG7_STALE_S = 600.0       # a push this old without its reply check asks nothing any more
CURIOSITY = True            # #212: tilt the reach choice by learning progress (tests switch it off for the base)
CURIO_WINDOW = 30           # ordinary looks per window: progress = earlier window's error - recent window's
CURIO_FLOOR = 0.1           # the tilt never goes under this: a mastered or "can't learn" thing is still retried
CURIO_MIN = 0.005           # progress under this anywhere is no frontier: the base chooser runs untilted

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
        push = c.get("reply")                           # rung 7: this look answers a push's question
        if push is not None and push.get("asked"):
            c["p_llm"] = _llm_pred(srv, c["eid"], float(push["ts"]))   # the model's read, asked at the push
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


def _llm_pred(srv, eid: str, since: float) -> float | None:
    """THE SEAM (rung 7): the model's prediction for this look, landed on the
    entity as a one-line llm_answer SINCE the push that asked it (an older
    answer answered an older question). None when absent, older or not a
    number. Tests swap it for a deterministic stand-in."""
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(eid) if est is not None else None
    ans = (doc.properties or {}).get("llm_answer") if doc is not None else None
    if not isinstance(ans, dict):
        return None
    if float(ans.get("ts") or 0) < since:             # answered before this push: not this question
        return None
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", str(ans.get("text") or ""))
    if m is None:
        return None
    return min(1.0, max(0.0, float(m.group(1))))


def _ask_llm(srv, eid: str, prompt: str) -> dict:
    """THE SEAM (rung 7): ask the coprocessor to predict the reply check. The
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
    looked at its other since and it is under RUNG7_STALE_S old -- read from
    the record, so it survives a restart. None otherwise."""
    push = next((r for r in reversed(recs) if r.get("kind") == "push"), None)
    if push is None or not push.get("to"):
        return None
    if now - float(push.get("ts") or 0) > RUNG7_STALE_S:
        return None
    if _first_look_after(recs, push["to"], float(push.get("ts") or 0)) is not None:
        return None
    return push


def _rung7_prompt(srv, eid: str, n: int, now: float) -> str:
    """What to ask the coprocessor at a push: will the other have spoken by
    self's reply check? Only facts self already holds in its record --
    timings and changed/unchanged answers, never the chat's words."""
    recs = _read(srv)
    hist = []
    for p in [r for r in recs if r.get("kind") == "push" and r.get("to") == eid][-10:]:
        look = _first_look_after(recs, eid, float(p["ts"]))
        if look is not None and look.get("answer") in ("changed", "unchanged"):
            hist.append(f"look {round(float(look['ts']) - float(p['ts']))}s after: "
                        + ("they had spoken" if look["answer"] == "changed" else "silent"))
    looks = [r for r in recs if r.get("kind") == "reach" and r.get("eid") == eid
             and r.get("answer") in ("changed", "unchanged")]
    spoke = next((r for r in reversed(looks) if r["answer"] == "changed"), None)
    last = (f"Self last saw them speak {round(now - float(spoke['ts']))}s ago. " if spoke
            else "Self has not seen them speak yet. ")
    return (f"You are predicting whether {eid} (the person self talks to) will have spoken by "
            f"self's next look at them. Self just pushed a line to them (push {n}); its next look "
            f"comes about {round(RUNG7_CHECK_S)}s from now. " + last
            + "After self's earlier pushes (oldest first): " + ("; ".join(hist) or "(none yet)") + ". "
            + "Reply with ONE number, the probability (0..1) that they will have spoken by that look.")


def _meet(srv, seed: str, now: float | None = None) -> None:
    """RUNG 6's act: push a line to chat:out (the other answers). Practised
    from the cycle once rung 6 is available, paced at MEET_EVERY cycles so it
    doesn't spam chat:out's gap guard.

    THE OTHER is the person who actually chats (#208): chat_io's own
    recent_person (the owner of the newest owned chat), the operator only on
    a seat with no people. It is resolved once per meet, so the push target
    and the reach target move together when the person changes.

    RUNG 7 (#215): once rung 7 is available, each push asks the model once
    (async, capped by llm.request) whether the other will have spoken by
    self's reply check; the push line records whether it went out (`asked`)."""
    try:
        n = int(seed or 0)
    except (TypeError, ValueError):
        return                                   # not a cycle's seed: only the loop meets
    if n % MEET_EVERY != 1:
        return
    import time
    from . import chat_io as _CIO
    now = time.time() if now is None else now
    person = _CIO.recent_person(srv)                        # '' = the operator (no people)
    resp = _CIO.entity_in(srv, person)                      # the other, as a reach target
    to = resp.eid if resp is not None else _CIO.name_in(person)
    _rt(srv)["other"] = to                                  # rung 7 predicts this one
    _push_chat(srv, "self is here", person)
    rec = {"ts": now, "kind": "push", "n": n, "eid": _CIO.name_of(person), "to": to}
    if _available(srv, 7):                                  # rung 7: ask the model AT the push (#215)
        rec["asked"] = _queued(_ask_llm(srv, to, _rung7_prompt(srv, to, n, now)))
    _append(srv, rec)


def _reach(srv, target: str) -> dict:
    """Read one thing: its getter fires (or answers that it can't)."""
    c = _ctx.cycle
    ent = (srv.entity_store.docs or {}).get(target)
    if ent is None:
        c["read"] = {"fired": False, "reason": "gone"}
        return {"rc": 0, "text": "nothing there"}
    try:
        c["read"] = _read_entity(srv, ent, c.get("now"))
    except Exception as e:                                  # noqa: BLE001
        c["read"] = {"fired": False, "reason": f"error: {e!r}"[:80]}
    return {"rc": 0, "text": "reached"}


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
    if reply is not None and now - float(reply["ts"]) < RUNG7_CHECK_S:
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
    if reply is not None:                                # rung 7: the reply check after a push
        rec["reply"] = True
    if c.get("p_llm") is not None:                       # rung 7: the model's read, same look
        rec["p_llm"] = round(c["p_llm"], 4)
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
    if watch and c["answer"] != "none" and not inside:
        _shift_check(srv, eid, now)
    _audit_tick(srv, now)
    _audit5_tick(srv, now)


def _lam(recs: list[dict], eid: str) -> float | None:
    """A thing's rate of change in time: the maximum-likelihood lambda for
    P(changed | dt) = 1 - exp(-lambda dt) over its last LAM_LOOKS answered
    looks (outside audits); None before it has any."""
    since = _last_shift_ts(recs, eid)
    obs = [(float(r["dt"]), r["answer"] == "changed") for r in recs
           if r.get("kind") == "reach" and r.get("eid") == eid
           and not r.get("audit") and not r.get("audit5")
           and r.get("answer") in ("changed", "unchanged") and r.get("dt")
           and (since is None or r["ts"] > since)][-LAM_LOOKS:]
    if not obs:
        return None
    dt = np.array([o[0] for o in obs])[None, :]
    ch = np.array([o[1] for o in obs], dtype=float)[None, :]
    lam = LAM_GRID[:, None]
    p = np.clip(1.0 - np.exp(-lam * dt), 1e-9, 1 - 1e-9)
    ll = (ch * np.log(p) + (1 - ch) * np.log(1 - p)).sum(axis=1)
    return float(LAM_GRID[int(np.argmax(ll))])


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
    """Understand an other: over paired looks at it (a habit `p` and a model
    `p_llm` on the same look), the model removes RUNG7_MARGIN of habit's Brier
    error -- predicting the other's answers better than habit alone. Only
    looks at the CURRENT other count (#208, see _rung6)."""
    to = _other(recs)
    pairs = [r for r in recs if r.get("kind") == "reach" and not r.get("audit")
             and (to is None or r.get("eid") == to)
             and r.get("answer") in ("changed", "unchanged")
             and r.get("p_llm") is not None][-RUNG7_LOOKS:]
    crit = {"looks": min(1.0, len(pairs) / RUNG7_LOOKS)}
    if not pairs:
        return {"crit": {**crit, "skill": 0.0}, "skill": 0.0, "passed": False}
    a = [1.0 if r["answer"] == "changed" else 0.0 for r in pairs]
    b_habit = sum((x - r["p"]) ** 2 for x, r in zip(a, pairs)) / len(pairs)
    b_llm = sum((x - r["p_llm"]) ** 2 for x, r in zip(a, pairs)) / len(pairs)
    skill = (b_habit - b_llm) / b_habit if b_habit > 1e-9 else 0.0
    crit["skill"] = max(0.0, min(1.0, skill / RUNG7_MARGIN))
    return {"crit": crit, "skill": round(skill, 4),
            "eid": pairs[-1]["eid"], "passed": all(v >= 1.0 for v in crit.values())}


PROGRESS = {1: _rung1, 2: _rung2, 3: _rung3, 4: _rung4, 5: _rung5, 6: _rung6, 7: _rung7}   # rung -> its test over the record


def _passed(recs: list[dict]) -> set:
    return {0} | {r["rung"] for r in recs if r.get("kind") == "rung"}


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
            line = {"ts": time.time(), "kind": "rung", "rung": n, "skill": p["skill"]}
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
            "slipping": slipping, "ladder": ladder,
            "world": {e: k for e, k in _kinds(recs).items() if k != "unknown"},
            "split": {e: s for e, s in spl.items() if s != "unknown"},
            "meets": meets,
            "steer": _steer_stats(recs),
            "curiosity": _curiosity_stats(recs)}


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
