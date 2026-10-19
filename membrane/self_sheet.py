"""SELF'S CHARACTER SHEET (#249, part of #161): the 👶 panel's numbers.

What used to be an external dashboard ("Self's record", rebuilt hourly by a
script outside the seat) computed in the seat, from self's record, status()
and the toybox -- read-only, cached CACHE_S, served at GET
/membrane/self/sheet. Laid out like an RPG character sheet:

  header         name, Lv (the rung), the exp bar, age and WORKING hours
  attributes     one measured skill per built rung (Feel .. Understand);
                 None when nothing measures it yet, negative when it is
  quest          the current rung's staged criteria, with counts and skill
  achievements   each rung's pass record (date, working hour, skill)
  growth         rungs over working hours, against the fastest possible and
                 a child's milestones rescaled (an analogy, said so)
  time           alive = down + blocked by a known bug + working
  inventory      the toys: kind, health, looks, error earlier -> later, who
                 changed them (the toybox's ground truth)
  relationships  the others it has met: people, the seat's model, toy
                 players -- how predictable each is -- and the body set

WORKING HOURS. Running time is the time the record kept growing: a gap over
GAP_S between two lines (and the tail since the last line) is time self was
not running (the seat down, restarting, or self asleep). From running time
the windows a KNOWN BUG held a rung shut are taken out: BUGS, a small data
list with each fix's time. A window opens when the rung below the stuck one
passed and applies only on a seat whose rung passed before the fix (a seat
born later never had the bug).

THE DOCK (the operator's addition): dock() is the toys alone, light enough
to poll every few seconds (GET /membrane/toybox/state, cached DOCK_CACHE_S):
each toybox toy's live reading, its last change and who made it, its health
and its stats -- the icons under the 👶 tab where people play with them.
"""
from __future__ import annotations

import datetime as dt
import time

from . import self_loop as SL

NAME = "self"                 # until the name ceremony (#161 layer 3)
GAP_S = 120.0                 # a record gap longer than this: self was not running
CACHE_S = 30.0                # the sheet is recomputed at most this often
DOCK_CACHE_S = 2.0            # ...the dock's toys this often
H = 3600.0


def _utc(*a) -> float:
    return dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp()


# the bugs that held a rung shut while self ran. opens_after: the rung whose
# pass opens the window (the next one was stuck); fixed: when the fix was
# live; until: "fixed" (the window ends at the fix) or "first_person" (it ends
# when self first sent a line to a real person, which the fix made possible)
# or "rung7_restart" (#254: it opens at the HONEST rung 6 pass and ends at the
# record's rung7_restart line, the fixed rung 7's first cycle; open until then)
BUGS = (
    {"issue": 191, "what": "rung 5 could not notice a shift at the audit's floor",
     "opens_after": 4, "fixed": _utc(2026, 10, 16, 21, 43, 37), "until": "fixed"},   # PR #191 live on the seat
    {"issue": 208, "what": "rung 6 talked to the empty operator slot",
     "opens_after": 5, "fixed": _utc(2026, 10, 18, 18, 8, 44), "until": "first_person"},  # PR #209 merged
    {"issue": 254, "what": "rung 7 graded the LLM's guess, not self's",
     "opens_after": 6, "fixed": None, "until": "rung7_restart"},   # the fix's time is on each seat's record
)
RESTART7 = "restarted {day}: was grading the LLM, now grades self"
VOIDED = "rung {rung} passed {at} under the #254 bug: graded the LLM, not self; doesn't count"

# the closest human ability and the age (months) it typically shows up --
# rough, from developmental psychology; the growth curve's child scale
CHILD = {1: (0.1, "tells its own touch from another's (newborns, Rochat & Hespos 1997)"),
         2: (3.0, "learns its kicks move a mobile (Rovee-Collier)"),
         3: (4.0, "forms perceptual categories (Quinn & Eimas)"),
         4: (3.5, "anticipates timed events (Haith, anticipatory looking)"),
         5: (5.0, "is surprised when the world breaks its expectation (Baillargeon)"),
         6: (3.0, "tells perfect self-contingency from imperfect social contingency (Bahrick & Watson)"),
         7: (9.0, "predicts what others will do (the nine-month shift, Tomasello)"),
         8: (18.0, "passes the mirror test (Amsterdam 1972)"),
         9: (48.0, "revises what it knows about its own knowing (metacognition, Flavell)"),
         10: (48.0, "tells its own story from memory (autobiographical narrative, Nelson & Fivush)")}
CAVEAT = ("The child scale is an analogy, not a measurement: each open point is the age a child "
          "typically shows the closest ability, rescaled by one factor (hours of self per month of a "
          "child, the median over the rungs passed) so a child's proportions are kept.")


def fastest_cycles() -> dict:
    """The fewest cycles each built rung's test needs once the rung below
    passed (self_loop's constants): the fastest-possible line."""
    r2 = SL.RUNG2_REACHES + SL.AUDIT_REACHES
    return {1: SL.RUNG1_CYCLES, 2: r2, 3: SL.RUNG3_WINDOW - r2, 4: SL.RUNG4_TIMED,
            5: SL.RUNG5_LOOKS, 6: SL.RUNG6_PUSHES * SL.MEET_EVERY, 7: SL.RUNG7_LOOKS * SL.MEET_EVERY}


def _needs() -> dict:
    return {1: f"{SL.RUNG1_CYCLES} pushed cycles",
            2: f"{SL.RUNG2_REACHES} answered looks at one thing + a {SL.AUDIT_REACHES}-look blind audit",
            3: f"{SL.RUNG3_WINDOW} looks so its kinds can agree across halves",
            4: f"{SL.RUNG4_TIMED} timed looks",
            5: f"{SL.RUNG5_LOOKS} looks with noticing on, both audit shifts caught",
            6: f"{SL.RUNG6_PUSHES} lines sent, {SL.MEET_EVERY} cycles apart",
            7: f"{SL.RUNG7_LOOKS} reply checks, one per line"}


def iso(t) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


def gaps(ts: list) -> list:
    """The record's gaps longer than GAP_S, as (from, to)."""
    return [(a, b) for a, b in zip(ts, ts[1:]) if b - a > GAP_S]


def running(gs: list, t0: float, t1: float) -> float:
    """Seconds self was running between t0 and t1 (the gaps taken out)."""
    if t1 <= t0:
        return 0.0
    down = sum(max(0.0, min(b, t1) - max(a, t0)) for a, b in gs)
    return (t1 - t0) - down


def _r(x, n=3):
    return None if x is None else round(float(x), n)


def _short(eid) -> str:
    return SL._short(str(eid or ""))


def _answered(r) -> bool:
    return r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged") and not r.get("audit")


def _brier(rs: list, key: str = "p") -> float | None:
    rs = [r for r in rs if r.get(key) is not None]
    if not rs:
        return None
    return sum((float(r[key]) - (r["answer"] == "changed")) ** 2 for r in rs) / len(rs)


def _halves(rs: list) -> tuple:
    """Self's prediction error on a thing, its earlier half of looks vs the later."""
    br = [(float(r.get("p") or 0) - (r["answer"] == "changed")) ** 2 for r in rs]
    h = len(br) // 2
    if not h:
        return None, None
    return _r(sum(br[:h]) / h), _r(sum(br[h:]) / (len(br) - h))


def _toybox_rows() -> dict:
    try:
        from . import toybox as TB
        return {t["name"]: t for t in TB.list_toys()}
    except Exception:
        return {}


# ---- the clocks -------------------------------------------------------------

def _clocks(recs: list, now: float) -> dict:
    """Born, the gaps, the pass times (rung 6 honest), the bug windows."""
    srt = sorted(recs, key=lambda r: float(r.get("ts") or 0))
    born = next((r["ts"] for r in srt if r.get("kind") == "birth"), srt[0]["ts"] if srt else None)
    if born is None:
        return {"born": None, "srt": srt}
    ts = [float(r["ts"]) for r in srt if float(r.get("ts") or 0) >= born]
    end = max(now, ts[-1] if ts else born)
    gs = gaps(ts + [end])
    first: dict[int, dict] = {}
    for r in SL._rung_lines(srt):                    # a voided pass (#254) is no achievement
        if r.get("rung") not in first:
            first[r["rung"]] = r
    passed_at = {n: float(r["ts"]) for n, r in first.items()}

    def to(r):
        return str(r.get("to") or r.get("eid") or "")
    first_person = next((float(r["ts"]) for r in srt if r.get("kind") == "push"
                         and to(r) and not to(r).endswith(":operator")), None)
    # rung 6's first pass against the empty operator slot (#208): the honest
    # pass is when its test first held against the person who actually chats
    real6 = None
    r6 = first.get(6)
    if r6 is not None and str(r6.get("eid") or "").endswith(":operator") and first_person is not None:
        n_push = n_look = 0
        for r in srt:
            if float(r["ts"]) < first_person:
                continue
            e = to(r)
            if r.get("kind") == "push" and e and not e.endswith(":operator"):
                n_push += 1
            if r.get("kind") == "reach" and "chat:in:" in e and not e.endswith(":operator") \
                    and r.get("answer") in ("changed", "unchanged"):
                n_look += 1
            if n_push >= SL.RUNG6_PUSHES and n_look >= SL.RUNG6_HEARD:
                real6 = float(r["ts"])
                break
    honest = dict(passed_at)
    if real6 is not None:
        honest[6] = real6
    restart7 = next((float(r["ts"]) for r in srt if r.get("kind") == "rung7_restart"), None)
    bugs = []
    for b in BUGS:
        if b["until"] == "rung7_restart":
            start = honest.get(b["opens_after"])
            fixed = restart7
        else:
            start = passed_at.get(b["opens_after"])
            fixed = b["fixed"]
        if start is None or (fixed is not None and start >= fixed):
            continue                                  # never had it
        if b["until"] == "fixed":
            stop = fixed
        elif b["until"] == "first_person":
            stop = first_person or fixed
        else:
            stop = fixed if fixed is not None else end   # still open: runs to the record's end
        stop = min(stop, end)
        if stop > start:
            bugs.append((start, stop, {**b, "open": fixed is None}))

    def bug_s(t):
        return sum(running(gs, a, min(b, t)) for a, b, _ in bugs if a < t)

    def working(t):
        return running(gs, born, t) - bug_s(t)
    return {"born": born, "end": end, "gaps": gs, "first": first, "passed_at": passed_at, "honest": honest,
            "real6": real6, "first_person": first_person, "restart7": restart7, "bugs": bugs, "bug_s": bug_s, "working": working,
            "srt": srt}


# ---- the sections -------------------------------------------------------------

def _time(c: dict) -> dict:
    if c["born"] is None:
        return {"alive_h": 0.0, "running_h": 0.0, "down_h": 0.0, "bugs_h": 0.0, "working_h": 0.0, "bugs": []}
    born, end, gs = c["born"], c["end"], c["gaps"]
    run = running(gs, born, end)
    bug = c["bug_s"](end)
    return {"alive_h": _r((end - born) / H), "running_h": _r(run / H), "down_h": _r((end - born - run) / H),
            "bugs_h": _r(bug / H), "working_h": _r((run - bug) / H),
            "bugs": [{"issue": b["issue"], "what": b["what"], "from": iso(a), "to": iso(z),
                      "open": bool(b.get("open")),
                      "running_h": _r(running(gs, a, z) / H)} for a, z, b in c["bugs"]]}


def _achievements(c: dict) -> list:
    out = []
    if c["born"] is None:
        return out
    for n in sorted(c["first"]):
        r = c["first"][n]
        t = c["honest"].get(n, float(r["ts"]))
        row = {"rung": n, "name": SL.RUNG_NAMES.get(n, ""), "at": iso(float(r["ts"])),
               "working_h": _r(c["working"](t) / H), "wall_h": _r((t - c["born"]) / H),
               "skill": r.get("skill"), "eid": _short(r.get("eid")) if r.get("eid") else None}
        if n == 6 and c["real6"] is not None:
            row["earned_at"] = iso(c["real6"])
            row["note"] = ("first passed against the empty operator slot (#208); its hours count from "
                           "when its test first held with the person who actually chats")
        out.append(row)
    return out


def _growth(c: dict, ach: list) -> dict:
    floors, needs = fastest_cycles(), _needs()
    by = {a["rung"]: a for a in ach}
    raw = {n: c["working"](t) / H for n, t in c["honest"].items()} if c["born"] is not None else {}
    ratios = sorted(raw[n] / CHILD[n][0] for n in raw if n >= 2 and n in CHILD)
    k = ratios[len(ratios) // 2] if ratios else None
    rows, cum = [], 0
    for n in range(1, max(CHILD) + 1):
        built = n in SL.PROGRESS
        if built:
            cum += floors[n]
        mo, what = CHILD[n]
        a = by.get(n)
        rows.append({"rung": n, "name": SL.RUNG_NAMES.get(n, ""), "built": built,
                     "needs": needs.get(n, "not built yet"),
                     "fastest_h": round(cum * SL.PACE_S / H, 3) if built else None,
                     "working_h": a["working_h"] if a else None, "wall_h": a["wall_h"] if a else None,
                     "child_months": mo, "child_milestone": what,
                     "child_h": _r(mo * k, 4) if k else None})
    t = _time(c)
    return {"rows": rows, "now_working_h": t["working_h"], "now_wall_h": t["alive_h"],
            "hours_per_child_month": _r(k, 4) if k else None, "pace_s": SL.PACE_S, "caveat": CAVEAT}


def _sort_value(recs: list):
    reaches = [r for r in recs if r.get("kind") == "reach" and not r.get("audit")]
    tail = [r for r in reaches if r.get("steer")][-SL.RUNG3_LOOKS:]
    return (1.0 - sum(r.get("answer") == "none" for r in tail) / len(tail)) if tail else None


def _calibration(recs: list):
    timed = [r for r in recs if _answered(r) and r.get("timed")][-40:]
    if not timed:
        return None
    news = sum(r["answer"] == "changed" for r in timed) / len(timed)
    return abs(sum(float(r["p"]) for r in timed) / len(timed) - news)


ATTRS = (("feel", "Feel", 1, "skill", "1 − gap ÷ naive: how well it predicts the current's push on its own moves"),
         ("reach", "Reach", 2, "skill", "Brier skill of its looks at one thing against the seat-wide change rate"),
         ("sort", "Sort", 3, "share", "the answered share of its last steered looks (it stops reaching for silent things)"),
         ("patience", "Patience", 4, "error", "calibration: |mean predicted chance − share that found news| over its timed looks"),
         ("notice", "Notice", 5, "share", "the shifts it noticed, of the two its blind audit probes"),
         ("meet", "Meet", 6, "share", "rung 6's staged test: lines sent, answered looks, the other read as other"),
         ("understand", "Understand", 7, "skill", "self's Brier skill over the running rate at predicting the other's replies (its own guess, the model's mixed in by its track record)"))
PASS = {"feel": SL.RUNG1_SKILL, "reach": SL.RUNG2_SKILL, "sort": 1.0 - SL.RUNG3_FOCUS, "patience": SL.RUNG4_CALIB,
        "notice": 1.0, "meet": 1.0, "understand": SL.RUNG7_MARGIN}


def _attributes(recs: list, tempo: dict, st: dict, progs: dict) -> list:
    ladder = {r["rung"]: r["state"] for r in st.get("ladder") or []}
    has = {
        "feel": any(r.get("kind") == "cycle" and r.get("change") == "world" for r in recs),
        "reach": bool(SL._rung2_stats(recs, tempo)),
        "notice": any(r.get("watch5") for r in recs) or any(r.get("kind") == "shift" for r in recs),
        "meet": any(r.get("kind") == "push" for r in recs),
    }
    out = []
    for key, label, n, kind, what in ATTRS:
        p = progs[n]
        if key == "sort":
            v = _sort_value(recs)
        elif key == "patience":
            v = _calibration(recs)
        elif key == "meet":
            v = SL._staged(p["crit"])[0] if has["meet"] else None
        elif key == "understand":
            v = p["skill"] if p["crit"].get("looks", 0) > 0 else None
        else:
            v = p["skill"] if has[key] else None
        better = "lower" if kind == "error" else "higher"
        ps = PASS[key]
        met = v is not None and (v <= ps if better == "lower" else v >= ps)
        state = ladder.get(n, "locked")
        out.append({"key": key, "label": label, "rung": n, "rung_name": SL.RUNG_NAMES.get(n, ""),
                    "value": _r(v, 4), "pass": ps, "kind": kind, "better": better, "met": met,
                    "state": state, "holding": bool(p["passed"]),
                    "slipping": state == "passed" and not p["passed"], "what": what})
    return out


def _sg(v) -> str:
    return ("−" if v < 0 else "+") + f"{abs(v):.2f}"


def _quest(recs: list, st: dict, progs: dict, attrs: dict) -> dict:
    rung, nxt, lim = st.get("rung") or 0, st.get("next"), st.get("limit") or 0
    aim = nxt if (nxt in SL.PROGRESS and nxt <= lim) else (rung if rung in SL.PROGRESS else None)
    if aim is None:
        return {"rung": None, "name": None, "criteria": [], "bar": st.get("bar"), "waiting_on": st.get("waiting_on"),
                "summary": "", "mode": st.get("mode")}
    p = progs[aim]
    count = {1: {"pushes": (SL.RUNG1_CYCLES, "pushed cycles")},
             2: {"reaches": (SL.RUNG2_REACHES, "answered looks at one thing")},
             3: {"looks": (SL.RUNG3_LOOKS, "steered looks")},
             4: {"timed": (SL.RUNG4_TIMED, "timed looks")},
             5: {"looks": (SL.RUNG5_LOOKS, "looks with noticing on")},
             6: {"pushes": (SL.RUNG6_PUSHES, "lines sent"), "heard": (SL.RUNG6_HEARD, "answered looks at the other")},
             7: {"looks": (SL.RUNG7_LOOKS, "reply checks")}}.get(aim, {})
    skill = {1: (SL.RUNG1_SKILL, "feel skill"), 2: (SL.RUNG2_SKILL, "reach skill"),
             3: (SL.RUNG2_SKILL, "reach skill on a moving thing"), 7: (SL.RUNG7_MARGIN, "skill vs running rate")}
    flags = {"audit": "both audit shifts caught" if aim == 5 else "blind audit passed",
             "kinds": "kinds agree across halves", "split": "the other reads as other"}
    crits = []
    for key, frac in p["crit"].items():
        met = frac >= 1.0
        if key in count:
            need, label = count[key]
            text = f"{min(need, round(frac * need))}/{need} {label}"
        elif key == "skill" and aim in skill:
            tgt, label = skill[aim]
            text = f"{label} {_sg(float(p['skill']))} → {_sg(tgt)}"
        elif key == "focus":
            v = attrs["sort"]["value"]
            text = f"answered share {'–' if v is None else f'{v:.2f}'} → over {1 - SL.RUNG3_FOCUS:.2f}"
        elif key == "calibration":
            v = attrs["patience"]["value"]
            text = f"calibration error {'–' if v is None else f'{v:.2f}'} → ≤ {SL.RUNG4_CALIB:.2f}"
        elif key == "news":
            text = f"timed looks find news ({round(100 * frac)}% of the way)"
        else:
            text = flags.get(key, key)
        crits.append({"key": key, "text": text, "met": met, "frac": _r(frac, 4)})
    return {"rung": aim, "name": SL.RUNG_NAMES.get(aim, ""), "criteria": crits, "bar": st.get("bar"),
            "waiting_on": st.get("waiting_on"), "slipping": st.get("slipping"), "mode": st.get("mode"),
            "summary": " · ".join(c["text"] + (" ✓" if c["met"] else "") for c in crits)}


def _inventory(recs: list, st: dict, now: float) -> list:
    looks: dict[str, list] = {}
    last: dict[str, float] = {}
    for r in recs:
        if r.get("kind") == "reach":
            last[r.get("eid")] = float(r.get("ts") or 0)
            if _answered(r):
                looks.setdefault(r.get("eid"), []).append(r)
    counts = _toybox_rows()
    world, split = st.get("world") or {}, st.get("split") or {}
    out = []
    for t in st.get("toys") or []:
        e = t["eid"]
        rs = looks.get(e, [])
        early, late = _halves(rs)
        tb = counts.get(t.get("toy")) if t.get("toy") and not t.get("block") else None
        out.append({"eid": e, "name": _short(e), "kind": t.get("kind"), "health": t.get("health"),
                    "toy": t.get("toy"), "block": t.get("block"), "getter": t.get("getter"),
                    "looks": len(rs), "changed_share": _r(sum(r["answer"] == "changed" for r in rs) / len(rs)) if rs else None,
                    "error_early": early, "error_late": late,
                    "last_look_s": _r(now - last[e], 1) if e in last else None,
                    "changes": dict(tb["counts"]) if tb else None,
                    "world": world.get(e), "seen_as": split.get(e)})
    out.sort(key=lambda x: (-x["looks"], x["name"]))
    return out


def _person(eid: str) -> str:
    s = _short(eid)
    return s.split("chat:in:", 1)[1] if "chat:in:" in s else s


def _relationships(recs: list, tempo: dict, st: dict) -> dict:
    current = SL._other(recs)
    stats = SL._rung2_stats(recs, tempo)
    split = st.get("split") or {}
    rows: dict[str, dict] = {}
    chat_eids = {str(r.get("to")) for r in recs if r.get("kind") == "push" and r.get("to")}
    chat_eids |= {str(r.get("eid")) for r in recs if r.get("kind") == "reach" and "chat:in:" in str(r.get("eid") or "")}
    for e in sorted(chat_eids):
        rs = [r for r in recs if _answered(r) and r.get("eid") == e]
        tail = rs[-SL.RUNG7_LOOKS:]
        who = _person(e)
        rows[who] = {"who": who, "kind": "person", "current": e == current, "eid": e,
                     "chat": {"lines": sum(1 for r in recs if r.get("kind") == "push" and r.get("to") == e),
                              "looks": len(rs),
                              "replied": _r(sum(r["answer"] == "changed" for r in rs) / len(rs), 4) if rs else None,
                              "brier": _r(_brier(tail), 4), "skill": (stats.get(e) or {}).get("skill"),
                              "seen_as": split.get(e)},
                     "play": None}
    # the toy players: who changed a toy between self's looks (the reach's `by`)
    plays: dict[str, list] = {}
    toys: dict[str, set] = {}
    for r in recs:
        if _answered(r):
            for b in r.get("by") or []:
                if b not in ("self", "world"):
                    plays.setdefault(b, []).append(r)
                    toys.setdefault(b, set()).add(_short(r.get("eid")))
    changes: dict[str, int] = {}
    for t in _toybox_rows().values():
        for b, n in (t.get("counts") or {}).items():
            if b not in ("self", "world"):
                changes[b] = changes.get(b, 0) + int(n)
    model_name = "the seat's model"
    for b in sorted(set(plays) | set(changes)):
        who = model_name if b == "model" else b
        row = rows.setdefault(who, {"who": who, "kind": "model" if b == "model" else "person",
                                    "current": False, "chat": None, "play": None})
        rs = plays.get(b, [])
        row["play"] = {"changes": changes.get(b, 0), "looks": len(rs), "brier": _r(_brier(rs), 4),
                       "toys": sorted(toys.get(b, set()))}
    r7 = st.get("rung7") or {}
    if r7.get("asked") or model_name in rows:
        row = rows.setdefault(model_name, {"who": model_name, "kind": "model", "current": False,
                                           "chat": None, "play": None})
        row["rung7"] = {"asked": r7.get("asked"), "pairs": r7.get("paired"),
                        "brier_model": r7.get("brier_model"), "brier_self": r7.get("brier_self"),
                        "weight": r7.get("advisor_weight"), "model": r7.get("advisor_model")}
    others = sorted(rows.values(), key=lambda x: (not x["current"], x["kind"] == "model",
                                                  -((x.get("chat") or {}).get("looks") or 0), x["who"]))
    return {"others": others,
            "body": sorted(_short(e) for e, s in split.items() if s == "body"),
            "other": sorted(_short(e) for e, s in split.items() if s == "other")}


SKILL_STEP_S = 1800.0        # the skill's trend line: one point per half hour of the record
SKILL_POINTS = 9             # ...over the last four hours
SKILL_LABEL = {1: "feel skill", 2: "reach skill", 3: "reach skill on a moving thing", 4: "news share of timed looks",
               5: "shifts noticed", 6: "the other read as other", 7: "skill vs running rate"}


def _skill_at(n: int, recs: list, tempo: dict):
    """The rung's headline skill over the record so far; None while nothing
    feeds its test (every criterion at zero)."""
    p = SL.PROGRESS[n](recs, tempo)
    if not any(v > 0 for v in (p.get("crit") or {}).values()):
        return None
    return float(p["skill"])


def _skill(recs: list, tempo: dict, st: dict) -> dict | None:
    """THE SKILL AT A GLANCE (the operator: "I don't see the skill level
    anywhere"): the number the exp bar's test reads, the line it has to beat,
    and its trend -- the same test over the record as it stood every
    SKILL_STEP_S back (the per-thing tempo is today's: an approximation for
    rungs 2-3)."""
    n = st.get("skill_rung")
    if n not in SL.PROGRESS or not recs:
        return None
    srt = sorted(recs, key=lambda r: float(r.get("ts") or 0))
    ts = [float(r.get("ts") or 0) for r in srt]
    import bisect
    end = ts[-1]
    series = []
    for k in range(SKILL_POINTS - 1, -1, -1):
        cut = bisect.bisect_right(ts, end - k * SKILL_STEP_S)
        series.append({"ago_h": k * SKILL_STEP_S / H, "value": _r(_skill_at(n, srt[:cut], tempo), 4) if cut else None})
    now_v = series[-1]["value"]
    hour = next((p["value"] for p in series if p["ago_h"] == 1.0), None)
    return {"rung": n, "label": SKILL_LABEL.get(n, "skill"), "value": now_v, "pass": st.get("skill_pass"),
            "trend_h": _r(now_v - hour, 4) if now_v is not None and hour is not None else None, "series": series}


def _header(st: dict, recs: list, c: dict, t: dict, skill: dict | None = None) -> dict:
    nxt = st.get("next")
    return {"name": NAME, "level": st.get("rung") or 0, "rung_name": st.get("rung_name") or SL.RUNG_NAMES.get(st.get("rung") or 0),
            "next": nxt, "next_name": SL.RUNG_NAMES.get(nxt) if nxt is not None else None,
            "bar": st.get("bar"), "waiting_on": st.get("waiting_on"), "slipping": st.get("slipping"),
            "held": st.get("held"), "mode": st.get("mode"), "limit": st.get("limit"),
            "skill": skill,
            "born": iso(c["born"]), "age_h": t["alive_h"], "working_h": t["working_h"], "running_h": t["running_h"],
            "cycles": st.get("cycles") or 0,
            "looks": sum(1 for r in recs if r.get("kind") == "reach"),
            "lines_sent": sum(1 for r in recs if r.get("kind") == "push")}


def compute(srv, now: float | None = None) -> dict:
    """The whole sheet, fresh (no cache)."""
    now = time.time() if now is None else float(now)
    with SL._lock:
        recs = list(SL._read(srv))
    st = SL.status(srv)
    tempo = SL._tempo(srv)
    progs = {n: fn(recs, tempo) for n, fn in SL.PROGRESS.items()}
    c = _clocks(recs, now)
    t = _time(c)
    attrs = _attributes(recs, tempo, st, progs)
    ach = _achievements(c)
    quest = _quest(recs, st, progs, {a["key"]: a for a in attrs})
    if c.get("restart7") is not None:                     # #254: rung 7 restarted on the fix
        note = RESTART7.format(day=dt.datetime.fromtimestamp(c["restart7"], dt.timezone.utc).strftime("%Y-%m-%d"))
        for a in attrs:
            if a["key"] == "understand":
                a["note"] = note
        if quest.get("rung") == 7:
            quest["note"] = note
    voided = [VOIDED.format(rung=r.get("rung"), at=dt.datetime.fromtimestamp(float(r["voids_ts"]), dt.timezone.utc)
                            .strftime("%Y-%m-%d %H:%MZ"))
              for r in recs if r.get("kind") == "rung_void" and isinstance(r.get("voids_ts"), (int, float))]
    if voided:
        quest["voided"] = voided
    return {"empty": not recs, "generated": now, "header": _header(st, recs, c, t, _skill(recs, tempo, st)), "attributes": attrs,
            "quest": quest, "achievements": ach,
            "growth": _growth(c, ach), "time": t, "inventory": _inventory(recs, st, now),
            "relationships": _relationships(recs, tempo, st)}


def empty() -> dict:
    """The sheet's shape with nothing in it (a seat without self)."""
    c = {"born": None, "srt": []}
    return {"empty": True, "generated": time.time(),
            "header": {"name": NAME, "level": 0, "rung_name": SL.RUNG_NAMES[0], "next": None, "mode": "off",
                       "age_h": 0.0, "working_h": 0.0, "skill": None},
            "attributes": [], "quest": {"rung": None, "criteria": []}, "achievements": [],
            "growth": _growth(c, []), "time": _time(c), "inventory": [],
            "relationships": {"others": [], "body": [], "other": []}}


def sheet(srv, now: float | None = None, fresh: bool = False) -> dict:
    """The sheet, cached for CACHE_S per seat."""
    if srv is None:
        return empty()
    now = time.time() if now is None else float(now)
    rt = SL._rt(srv)
    hit = rt.get("sheet")
    if not fresh and hit is not None and 0 <= now - hit[0] < CACHE_S:
        return hit[1]
    out = compute(srv, now)
    rt["sheet"] = (now, out)
    return out


# ---- the dock: the toys alone, light enough to poll ---------------------------------

def forget_dock(srv) -> None:
    """Drop the dock's cache (a poke landed: the next poll shows it)."""
    if srv is not None:
        SL._rt(srv).pop("dock", None)


def dock(srv, now: float | None = None, fresh: bool = False) -> dict:
    """Each toybox toy, live: its reading, its last change (who, how long
    ago), its health and its stats. Cached DOCK_CACHE_S per seat."""
    now = time.time() if now is None else float(now)
    if srv is None:
        return {"generated": now, "toys": []}
    rt = SL._rt(srv)
    hit = rt.get("dock")
    if not fresh and hit is not None and 0 <= now - hit[0] < DOCK_CACHE_S:
        return hit[1]
    from . import toybox as TB
    try:
        rows = TB.list_toys()
    except Exception:
        rows = []
    with SL._lock:
        recs = list(SL._read(srv))
    ents = SL._toys(srv, recs) if rows else []
    eids: dict[str, list] = {}
    health: dict[str, str] = {}
    for t in ents:
        if t.get("toy"):
            eids.setdefault(t["toy"], []).append(t["eid"])
            if not t.get("block"):
                health[t["toy"]] = t["health"]
    want = {e: n for n, es in eids.items() for e in es}
    looks: dict[str, list] = {}
    for r in recs:
        if _answered(r) and r.get("eid") in want:
            looks.setdefault(want[r["eid"]], []).append(r)
    toys = []
    for row in rows:
        name, kind = row["name"], row["kind"]
        if kind == "tower":
            reading = {"blocks": [ch == "|" for ch in str(row.get("reading") or "")]}
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
        rs = looks.get(name, [])
        early, late = _halves(rs)
        toys.append({"name": name, "kind": kind, "params": row.get("params") or {}, "reading": reading,
                     "active": active, "health": health.get(name, "unread" if name in eids else "no getter"),
                     "looks": len(rs), "error_early": early, "error_late": late,
                     "changes": dict(row.get("counts") or {}), "last": last})
    out = {"generated": now, "toys": toys}
    rt["dock"] = (now, out)
    return out
