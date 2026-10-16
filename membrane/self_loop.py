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

THE LADDER: a rung opens when its measured test passes; the bar is the
weakest criterion and can go down (slipping) -- a passed rung stays on the
record. Only rungs in LADDER are built; the status says when the next is not.
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
}
LADDER = {0: "sleep and wake", 1: "feel itself"}

try:
    PACE_S = max(1.0, float(os.environ.get("MEMBRANE_SELF_PACE_S") or 10.0))
except ValueError:
    PACE_S = 10.0
BURST_PER_MIN = 30
HOLD_AFTER = 3              # failed cycles in a row
STEP = 0.1                  # how far self pushes itself per try
PUSH_GAIN = 0.2             # how hard the current pulls it toward itself
LEARN = 0.2                 # how fast expect's model of the current follows what it observes
CALM = 1e-6                 # a push under this is no push (a quiet world)
RUNG1_CYCLES = 20           # pushed cycles in the window the test reads
RUNG1_SKILL = 0.5           # 1 - gap/naive over that window

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
    rt["mode"] = _load_mode(srv)
    rt["model"] = _load_model(srv)


def _rt(srv) -> dict:
    with _lock:
        rt = _RT.get(id(srv))
        if rt is None:
            rt = _RT[id(srv)] = {"mode": "off", "last": None, "burst": [], "fails": 0,
                                 "held": False, "wake": threading.Event(), "srv": srv}
        return rt


# ---- the mode and the record (the data dir) --------------------------------

def _dir(srv) -> Path | None:
    mp = getattr(srv, "_models_path", None)
    return Path(mp).parent / "self" if mp else None


def record_path(srv) -> Path | None:
    d = _dir(srv)
    return d / "record.jsonl" if d is not None else None


def _load_mode(srv) -> str:
    d = _dir(srv)
    try:
        m = json.loads((d / "mode.json").read_text()).get("mode") if d is not None else None
    except Exception:
        m = None
    return m if m in MODES else "off"


def _save_mode(srv, mode: str) -> None:
    d = _dir(srv)
    if d is None:
        return
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "mode.json.tmp"
    tmp.write_text(json.dumps({"mode": mode, "ts": time.time()}))
    os.replace(tmp, d / "mode.json")


def _append(srv, rec: dict) -> None:
    p = record_path(srv)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(p, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def _read(srv) -> list[dict]:
    p = record_path(srv)
    if p is None or not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


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


def mode(srv) -> str:
    return _rt(srv)["mode"]


def set_mode(srv, m: str, by: str = "operator") -> bool:
    """The operator's switch (seat_panel.control). The first switch-on is the
    birth; a toggle clears a hold."""
    if m not in MODES:
        return False
    rt = _rt(srv)
    with _lock:
        if m != "off" and not any(r.get("kind") == "birth" for r in _read(srv)):
            _append(srv, {"ts": time.time(), "kind": "birth", "by": by})
        rt["mode"], rt["held"], rt["fails"] = m, False, 0
        _save_mode(srv, m)
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


def _expect(srv, seed: str) -> dict:
    """Where I will end up: my own step, plus the push I have learned to expect here."""
    c = _ctx.cycle
    c["intent"] = _unit(c["pos"] + STEP * _seed_dir(seed, len(c["pos"])))
    c["expected"] = _unit(c["intent"] + _expected_push(srv, c["pos"]))
    return {"rc": 0, "text": "expected"}


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
    mine = STEP * _seed_dir(seed, len(c["pos"]))
    world = PUSH_GAIN * (c["current"] - c["pos"])
    ent.position = [float(x) for x in _unit(c["pos"] + mine + world)]
    return {"rc": 0, "text": "moved"}


def _notice(srv, seed: str) -> dict:
    """Where I ended up; the push I did not account for; learn it."""
    c = _ctx.cycle
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
    fn = {"try": _try, "expect": _expect, "notice": _notice}[parts[0]]   # looked up per call
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
    from .night import method_call
    _ctx.cycle = {"srv": srv, "pos": pos, "current": cur}
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
    rec = {"ts": now, "kind": "cycle", "n": n, "act": "move", "step": STEP,
           "push": c["push"], "gap": gap, "naive": naive,
           "change": "world" if c["push"] > CALM else "knowledge"}
    _append(srv, rec)
    est.upsert(ent, embedder=None)
    _open_rungs(srv)
    _show(srv, ent, pos, c, rec)
    rt["wake"].set()                      # my state changed: that is what wakes the next try
    return rec


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
                       "at": c["observed"], "pull": _pull(srv, len(pos))},
                      {"n": rec["n"], "gap": rec["gap"], "naive": rec["naive"], "push": rec["push"],
                       "rung": st["rung"], "bar": st["bar"], "skill": st["skill"],
                       "mode": st["mode"], "slipping": st["slipping"], "waiting_on": st["waiting_on"],
                       "next": st["next"], "cycles": st["cycles"], "held": st["held"],
                       "rung_name": st["rung_name"]})
    except Exception:
        pass


# ---- the ladder and the exp bar ----------------------------------------------

def _rung1(recs: list[dict]) -> dict:
    pushed = [r for r in recs if r.get("kind") == "cycle" and r.get("change") == "world"][-RUNG1_CYCLES:]
    nv = sum(r["naive"] for r in pushed)
    skill = (1.0 - sum(r["gap"] for r in pushed) / nv) if nv > 0 else 0.0
    crit = {"pushes": min(1.0, len(pushed) / RUNG1_CYCLES),
            "skill": max(0.0, min(1.0, skill / RUNG1_SKILL))}
    return {"crit": crit, "skill": round(skill, 4), "passed": all(v >= 1.0 for v in crit.values())}


def _open_rungs(srv) -> None:
    recs = _read(srv)
    if not any(r.get("kind") == "rung" and r.get("rung") == 1 for r in recs) and _rung1(recs)["passed"]:
        _append(srv, {"ts": time.time(), "kind": "rung", "rung": 1, "skill": _rung1(recs)["skill"]})


def status(srv) -> dict:
    rt = _rt(srv)
    recs = _read(srv)
    born = any(r.get("kind") == "birth" for r in recs)
    passed = [r["rung"] for r in recs if r.get("kind") == "rung"]
    rung = max(passed) if passed else 0
    r1 = _rung1(recs)
    if rt["mode"] != "off" and _current(srv) is None:
        # no current right now (a fresh boot: nobody has spoken yet) -- say
        # so, or the switch reads "on" over a loop that is rightly idle
        r1c = r1["crit"]
        bar, waiting = (min(r1c.values()) if any(r.get("kind") == "cycle" for r in recs) else 0.0), "the world"
    else:
        waiting = min(r1["crit"], key=r1["crit"].get)
        bar = r1["crit"][waiting]
        if bar >= 1.0:
            waiting = None
    nxt = rung + 1 if rung + 1 in LADDER else None
    return {"mode": rt["mode"], "born": born, "held": rt["held"],
            "cycles": sum(1 for r in recs if r.get("kind") == "cycle"),
            "rung": rung, "rung_name": LADDER.get(rung), "next": nxt,
            "bar": round(bar, 4), "waiting_on": waiting, "skill": r1["skill"],
            "slipping": bool(rung >= 1 and not r1["passed"])}


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
