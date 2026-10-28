"""BLURP'S CHARACTER SHEET (#249, part of #161): the 👶 panel's numbers.

Computed in the seat from Blurp's record (self_loop.history(): the dated
archives sleep moved the raw lines to, then the lines since), status() and
energy -- read-only, cached CACHE_S, served at GET /membrane/self/sheet.

#576 retired the old learner and its ladder: the tree, the skills, the
traits, the growth curve and the toys' unlocks are empty until the new
learner fills them (#576 D2). The sheet keeps its shape, so the page renders
it as it is:

  header   name, "Lv 0", the mode, Blurp's age (since its birth line)
  energy   #345: energy.report() -- energy 0..1, asleep, the reserves
           (energy, disk, awake, record), the sleep pull and the limiting
           reserve
  code     the Code tab: Blurp's own methods (method_of self), each as
           method_inspect reads it -- none since #576 retired its methods

THE DOCK: dock() is the toys alone, light enough to poll every few seconds
(GET /membrane/toybox/state, cached DOCK_CACHE_S): each toybox toy's live
reading, its last change and who made it -- the icons under the 👶 tab where
people play with them.
"""
from __future__ import annotations

import datetime as dt
import time

from . import self_loop as SL
from . import self_name as SN

NAME = SN.DEFAULT             # the operator named it (2026-10-19, #277); since #283 the DEFAULT -- each seat
                              # names its own (SN.current()), and the prose below is said with it (SN.say_all)
CACHE_S = 30.0                # the sheet is recomputed at most this often
DOCK_CACHE_S = 2.0            # ...the dock's toys this often
H = 3600.0


def iso(t) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


def _r(x, n=3):
    return None if x is None else round(float(x), n)


def _header(st: dict, born: float | None, now: float) -> dict:
    return {"name": SN.current(), "level": 0, "rung_name": "", "title": "Lv 0",
            "learning": [], "next": [], "next_name": None,
            "bar": 0.0, "waiting_on": None, "slipping": False, "mode": st.get("mode", "off"), "skill": None,
            "born": iso(born), "age_h": _r((now - born) / H, 3) if born is not None else 0.0}


def _code(srv) -> list[dict]:
    """The Code tab: Blurp's cliffhanger code -- its own methods (method_of
    self), each as method_inspect reads it (command, params, steps, runs)."""
    est = getattr(srv, "entity_store", None)
    if est is None:
        return []
    from .night import method_inspect
    me = SL._eid()
    names = sorted({(d.properties or {}).get("method_name") for d in list((est.docs or {}).values())
                    if me in ((d.properties or {}).get("method_of") or [])} - {None})
    out = []
    for name in names:
        r = method_inspect(est, me, name)
        if r.get("found"):
            out.append({k: r[k] for k in ("command", "params", "steps", "runs", "read_only") if r.get(k)}
                       | {"name": name, "command": r.get("command") or ""})
    return out


def _shape() -> dict:
    """The sheet's sections, empty (#576: the old learner's are retired)."""
    return {"level": 0, "tree": [], "accepted": [], "awaiting": [], "attributes": [], "skills": [], "slipping": [],
            "quest": {"rung": None, "criteria": []}, "achievements": [], "growth": {}, "time": {}, "inventory": [],
            "relationships": {"others": [], "body": [], "other": []}, "toys": []}


def compute(srv, now: float | None = None) -> dict:
    """The whole sheet, fresh (no cache)."""
    now = time.time() if now is None else float(now)
    recs = SL.history(srv)                                # #345: the whole record -- the archive + the lines since
    st = SL.status(srv)
    from . import energy as E
    born = next((float(r["ts"]) for r in recs if r.get("kind") == "birth" and r.get("ts") is not None), None)
    return {"empty": not recs, "generated": now, **_shape(), "header": _header(st, born, now),
            "energy": E.report(srv), "code": _code(srv)}


def empty() -> dict:
    """The sheet's shape with nothing in it (a seat without self)."""
    now = time.time()
    return {"empty": True, "generated": now, **_shape(), "header": _header({}, None, now),
            "energy": None, "code": []}


def sheet(srv, now: float | None = None, fresh: bool = False) -> dict:
    """The sheet, cached for CACHE_S per seat."""
    if srv is None:
        return SN.say_all(empty())
    now = time.time() if now is None else float(now)
    name = SN.current()
    rt = SL._rt(srv)
    hit = rt.get("sheet")
    if not fresh and hit is not None and 0 <= now - hit[0] < CACHE_S and hit[2] == name:
        return hit[1]
    out = SN.say_all(compute(srv, now), name)          # #283: the seat's name, in the prose too
    if rt.get("bound"):                                # #309: never cache a sheet built before the mode loaded
        rt["sheet"] = (now, out, name)
    return out


def card(srv, now: float | None = None) -> dict:
    """#283: the zoo's card -- Blurp on this seat in a few fields, cheap
    enough to poll across seats (GET /membrane/self/card): the name, the
    level and its name (0 and "" since #576), the mode (awake = not off and
    not asleep, #345) and the record's last line (its time, ISO; None before
    Blurp's first line); #345: its energy (0..1), asleep, the sleep pull, the
    limiting reserve and the record cap (bytes; None = unknown)."""
    now = time.time() if now is None else float(now)
    name = SN.current()
    if srv is None:
        return {"name": name, "level": 0, "rung_name": "", "next": [], "mode": "off",
                "awake": False, "last_activity": None, "generated": now,
                "energy": None, "asleep": False, "pull": None, "limiting": None, "record_cap": None}
    mode = SL.mode(srv)
    with SL._lock:
        recs = SL._read(srv)
        mine = next((r for r in reversed(recs) if r.get("kind") not in SL.OPERATOR_KINDS), None)   # #299: not an accept
        last = float(mine.get("ts") or 0) if mine else None
    from . import energy as E
    en = E.report(srv)                                   # #345: live, like the mode
    return {"name": name, "level": 0, "rung_name": "", "next": [], "mode": mode,
            "awake": mode != "off" and not en["asleep"],
            "last_activity": iso(last) if last else None, "generated": now,
            "energy": en["energy"], "asleep": en["asleep"], "pull": en["pull"], "limiting": en["limiting"],
            "record_cap": en["record_cap"]}                # None: the RAM couldn't be read (no cap)


# ---- the dock: the toys alone, light enough to poll ---------------------------------

def forget_dock(srv) -> None:
    """Drop the dock's cache (a poke landed: the next poll shows it)."""
    if srv is not None:
        SL._rt(srv).pop("dock", None)


def dock(srv, now: float | None = None, fresh: bool = False) -> dict:
    """Each toybox toy, live: its reading and its last change (who, how long
    ago). Cached DOCK_CACHE_S per seat."""
    now = time.time() if now is None else float(now)
    who = SN.current()                                   # #283: Blurp's name ("name" is a toy's below)
    if srv is None:
        return {"generated": now, "name": who, "toys": [], "locked": [], "level": 0, "tree": []}
    rt = SL._rt(srv)
    hit = rt.get("dock")
    if not fresh and hit is not None and 0 <= now - hit[0] < DOCK_CACHE_S and hit[2] == who:
        return hit[1]
    from . import toybox as TB
    try:
        rows = TB.list_toys()
    except Exception:
        rows = []
    toys = []
    for row in rows:
        name, kind = row["name"], row["kind"]
        if kind == "tower":
            reading = {"blocks": [ch == "|" for ch in str(row.get("reading") or "")]}
        elif kind == "ball":
            reading = {"side": row.get("reading")}               # #264: whose side it is on
        elif kind == "mirror":
            reading = dict(row.get("reading") or {})             # #312: the glass and the stranger
        else:
            reading = {"value": row.get("reading")}
        active = None
        if kind == "burst":
            try:
                active = TB.burst_live(row["seed"], row["params"], now)
            except Exception:
                active = None
        lc = TB.last_change(name)
        last = None
        if lc:
            last = {"by": lc.get("by"), "what": lc.get("what"), "age_s": _r(max(0.0, now - float(lc["ts"])), 1)}
            if lc.get("block") is not None:
                last["block"] = lc.get("block")
        toys.append({"name": name, "kind": kind, "params": row.get("params") or {}, "reading": reading,
                     "active": active, "changes": dict(row.get("counts") or {}), "last": last})
    toys.sort(key=lambda t: t["kind"] != "ball")                 # #264: the ball first
    out = SN.say_all({"generated": now, "name": who, "toys": toys, "locked": [], "level": 0, "tree": [],
                      "accepted": [], "awaiting": []}, who)
    rt["dock"] = (now, out, who)
    return out
