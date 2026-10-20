"""SELF'S CHARACTER SHEET (#249, part of #161): the 👶 panel's numbers.

What used to be an external dashboard ("Self's record", rebuilt hourly by a
script outside the seat) computed in the seat, from self's record, status()
and the toybox -- read-only, cached CACHE_S, served at GET
/membrane/self/sheet. Laid out like an RPG character sheet:

  level, tree    #295: the achievements earned, and the skill tree -- each
                 achievement's branch, prerequisites (and why), state,
                 earned time and skill, staged bar, what it waits on, and
                 the child's age and ability its test measures (tree())
  header         name, "Lv N · <latest earned skill>" (#300: never a rung), what
                 it is learning, the exp bar, age and WORKING hours
  attributes     #300: traits of self as a whole, measured from its record --
                 Curiosity, Insight, Adaptability, Focus, Sociability (traits());
                 each a value, what it measures and a trend; they gate nothing
  skills         #300: only the skills it is learning now (open; #299: and
                 the ones waiting on the operator's accept, `awaiting_accept`),
                 each with its staged criteria, bar, what it waits on, trend
  slipping       #300: the earned skills whose test no longer holds
  quest          the skill the exp bar reads, staged (kept for older readers)
  achievements   each rung's pass record (date, working hour, skill)
  growth         rungs over working hours, against the fastest possible and
                 a child's milestones rescaled (an analogy, said so)
  time           alive = down + blocked by a known bug + at a wall + working
  inventory      the toys: kind, health, looks, error earlier -> later, who
                 changed them (the toybox's ground truth)
  relationships  the others it has met: people, the seat's model, toy
                 players -- how predictable each is -- and the body set
  toys           #267: every toybox kind, unlocked or locked, the rung that
                 unlocks it and what it will teach (toy_unlocks)

WORKING HOURS. Running time is the time the record kept growing: a gap over
GAP_S between two lines (and the tail since the last line) is time self was
not running (the seat down, restarting, or self asleep). From running time
the windows a KNOWN BUG held a rung shut are taken out: BUGS, a small data
list with each fix's time. A window opens when the rung below the stuck one
passed and applies only on a seat whose rung passed before the fix (a seat
born later never had the bug). A bug that cost only PART of self's time
(#259) carries a share: working hours lose share x its running time, and
where windows overlap the costliest open one's share applies, never two.
#305, THE WALLS: so are the stretches self had no BUILT skill it could be
learning (BUILT_AT: each test's merge, on the seat from its next restart);
at any moment the single costliest reason counts -- a wall all of it, a full
bug wins a tie, a partial one gives way.

THE DOCK (the operator's addition): dock() is the toys alone, light enough
to poll every few seconds (GET /membrane/toybox/state, cached DOCK_CACHE_S):
each toybox toy's live reading, its last change and who made it, its health
and its stats -- the icons under the 👶 tab where people play with them.
"""
from __future__ import annotations

import datetime as dt
import time

from . import self_loop as SL
from . import self_name as SN

NAME = SN.DEFAULT             # the operator named it (2026-10-19, #277); since #283 the DEFAULT -- each seat
                              # names its own (SN.current()), and the prose below is said with it (SN.say_all)
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
# record's rung7_restart line, the fixed rung 7's first cycle; open until then).
# #258: that rung 7 (understand an other) is rung 8 since the ladder flip; the
# window and its line keep their names (the record is never rewritten)
# share: the part of self's running time the bug cost (default 1.0: all of it).
# #259 held no rung shut: curiosity spent a share of self's looks on the
# heartbeat's restart glitches. It opens at opens_at (the #212 curiosity merge)
# on a seat past rung opens_after; until "fix_line": it ends at the record's first
# `fix` line naming its issue,
# the fixed loop's first cycle; open until then. Its share is from the replay
# of the live record: the tilt picked the heartbeat at 0.352 of its due looks
# against 0.279 untilted, and it was due at ~91% of the timed looks.
BUGS = (
    {"issue": 191, "what": "rung 5 could not notice a shift at the audit's floor",
     "opens_after": 4, "fixed": _utc(2026, 10, 16, 21, 43, 37), "until": "fixed"},   # PR #191 live on the seat
    {"issue": 208, "what": "rung 6 talked to the empty operator slot",
     "opens_after": 5, "fixed": _utc(2026, 10, 18, 18, 8, 44), "until": "first_person"},  # PR #209 merged
    {"issue": 254, "what": "rung 7 (understand an other, now rung 8) graded the LLM's guess, not " + NAME + "'s",
     "opens_after": 6, "fixed": None, "until": "rung7_restart"},   # the fix's time is on each seat's record
    {"issue": 259, "what": "curiosity chased the heartbeat's restart glitches",
     "opens_after": 2, "opens_at": _utc(2026, 10, 18, 20, 17, 24), "fixed": None,
     "until": "fix_line", "share": 0.06},       # #212 merged; the fix's time is on each seat's record
)
# #305, THE WALLS: when each achievement's test was BUILT -- the merge of the PR
# that built the test it is (or was) earned by. A test that was there but broken
# counts as built (BUGS takes that time); a test REPLACED by one that reads other
# turns (a pass under the old one would not count, or its turns are no longer
# read) is built only when the version that counts merged. The seat runs new code
# only after a restart, so on a seat it is built at the end of the first record
# gap over RESTART_S after the merge (from birth on a seat born after it). An
# achievement not listed (mirror, change, speak, invent) is not built.
BUILT_AT = {
    # rungs 0 + 1: born off, the switch, and it feels the current
    "feel": {"pr": 164, "merged": _utc(2026, 10, 16, 13, 7, 29)},
    # rung 2: the world's tempo, with a blind audit
    "reach": {"pr": 174, "merged": _utc(2026, 10, 16, 14, 55, 23)},
    # rung 3; #184 (rate-based kinds, 17:26Z) and #185 (its skill bar) merged after self's pass under #177
    "kinds": {"pr": 177, "merged": _utc(2026, 10, 16, 16, 23, 5)},
    # rungs 4 + 5 merged together (#178 went into a branch, not main); #184 came after patience's pass
    "patience": {"pr": 180, "merged": _utc(2026, 10, 16, 16, 29, 14)},
    # its first test was there but could not notice a shift at the audit's floor: BUGS #191's window
    "shift": {"pr": 180, "merged": _utc(2026, 10, 16, 16, 29, 14)},
    # rung 6; it talked to the empty operator slot until #209: BUGS #208's window
    "meet": {"pr": 187, "merged": _utc(2026, 10, 16, 19, 24, 2)},
    # take turns: #261 (#258, 07:26Z) timed chat pushes, #272 (#264, 12:53Z) moved the turns to the ball;
    # #281 (#278) gave the ball its own clock and rung 7 reads only the current clock's turns
    "turns": {"pr": 281, "merged": _utc(2026, 10, 19, 15, 19, 43)},
    # understand an other: #256 (#254's fix, 06:11Z) graded self not the model, #261 moved it up to rung 8,
    # #262 scored the reply, #272 the ball's turns; #281 (#278): it reads only the current clock's checks
    "expect": {"pr": 281, "merged": _utc(2026, 10, 19, 15, 19, 43)},
}
RESTART_S = 3 * SL.PACE_S     # a record gap this long is a restart: the loop writes a line every PACE_S
WALL_WHY = "no built skill to learn: {next} not built yet"
RESTART7 = "restarted {day}: was grading the LLM, now grades " + NAME
VOIDED = "rung {rung} passed {at} under the #254 bug: graded the LLM, not " + NAME + "; doesn't count"
# #258: a pass above rung 6 from before the ladder flip was the old numbering's
OLD_NAMES = {7: "understand an other"}
VOIDED_OLD = ("the old rung {rung} ({name}, now rung {now}) passed {at} under the #254 bug: "
              "graded the LLM, not " + NAME + "; doesn't count")
FLIPPED = ("the old rung {rung} ({name}, now rung {now}) passed {at}, before the ladder flip (#258); "
           "doesn't count: rung {rung} is now '{new}'")

# the closest human ability and the age (months) it typically shows up --
# rough, from developmental psychology; the growth curve's child scale
CHILD = {1: (0.1, "tells its own touch from another's (newborns, Rochat & Hespos 1997)"),
         2: (3.0, "learns its kicks move a mobile (Rovee-Collier)"),
         3: (4.0, "forms perceptual categories (Quinn & Eimas)"),
         4: (3.5, "anticipates timed events (Haith, anticipatory looking)"),
         5: (5.0, "is surprised when the world breaks its expectation (Baillargeon)"),
         6: (3.0, "tells perfect self-contingency from imperfect social contingency (Bahrick & Watson)"),
         7: (4.0, "times its turn in a proto-conversation: serve and return, 2-6 months "
                  "(Bateson 1975; Trevarthen 1979; Jaffe et al. 2001)"),
         8: (2.5, "expects a caregiver's response: the still-face upset (Tronick et al. 1978)"),   # #300
         9: (18.0, "passes the mirror test (Amsterdam 1972)"),
         10: (48.0, "revises what it knows about its own knowing (metacognition, Flavell)"),
         11: (12.0, "names a thing to an other and gets a response: first referential words and pointing "
                    "to share (Bates, Camaioni & Volterra 1975)")}   # #311: what speak's test measures
CAVEAT = ("The child scale is an analogy, not a measurement: each open point is the age a child "
          "typically shows the closest ability, rescaled by one factor (hours of " + NAME + " per month of a "
          "child, the median over the rungs passed) so a child's proportions are kept.")


def fastest_cycles() -> dict:
    """The fewest cycles each built rung's test needs once the rung below
    passed (self_loop's constants): the fastest-possible line."""
    r2 = SL.RUNG2_REACHES + SL.AUDIT_REACHES
    return {1: SL.RUNG1_CYCLES, 2: r2, 3: SL.RUNG3_WINDOW - r2, 4: SL.RUNG4_TIMED,
            5: SL.RUNG5_LOOKS, 6: SL.RUNG6_PUSHES * SL.MEET_EVERY, 7: SL.RUNG7_PUSHES * _turn_every(),
            8: SL.RUNG8_LOOKS * _turn_every(), 11: SL.SPEAK_PUSHES * SL.MEET_EVERY}   # #311: speak is in chat


def _turn_every() -> int:
    """Cycles between self's turns from rung 7 on: the ball's clock (#278) while
    the turns are with the ball, else the chat's."""
    return SL.TOY_MEET_EVERY if SL.TOY_TURNS else SL.MEET_EVERY


def _needs() -> dict:
    return {1: f"{SL.RUNG1_CYCLES} pushed cycles",
            2: f"{SL.RUNG2_REACHES} answered looks at one thing + a {SL.AUDIT_REACHES}-look blind audit",
            3: f"{SL.RUNG3_WINDOW} looks so its kinds can agree across halves",
            4: f"{SL.RUNG4_TIMED} timed looks",
            5: f"{SL.RUNG5_LOOKS} looks with noticing on, both audit shifts caught",
            6: f"{SL.RUNG6_PUSHES} lines sent, {SL.MEET_EVERY} cycles apart",
            7: f"{SL.RUNG7_PUSHES} timed turns with the ball with a reply check, at least {_turn_every()} cycles apart",
            8: f"{SL.RUNG8_LOOKS} reply checks, one per line",
            11: (f"{SL.SPEAK_PUSHES} timed lines with a reply check, at least {SL.SPEAK_EACH} with words "
                 f"and {SL.SPEAK_EACH} plain")}


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


LEARN_MIN_LOOKS = 10          # #276: fewer answered looks than this: too new to say
LEARN_MASTERED = 0.05         # ...a later-half error at most this: mastered
LEARN_MARGIN = 0.03           # ...this far below the rate-only guess: learning; within it: the rate only
LEARN_WORDS = {
    "new": "too new to say",
    "mastered": "mastered: self predicts it",
    "worse": "getting worse: not understood",
    "learning": "learning: predicts it better than its rate alone",
    "rate": "knows how often it changes, nothing more yet",
    "lost": "not understood: worse than guessing its rate",
}


def learning(rs: list) -> dict:
    """How well self has learned a thing, from its answered looks (#276).
    The yardstick is a RATE-ONLY guess -- always saying the thing's change
    share q, over the later half of the looks -- which scores q(1-q)."""
    rs = [r for r in rs if r.get("answer") in ("changed", "unchanged")]
    early, late = _halves(rs)
    out = {"looks": len(rs), "error_early": early, "error_late": late}
    if len(rs) < LEARN_MIN_LOOKS or late is None:
        v = "new"
    else:
        tail = rs[len(rs) // 2:]
        q = sum(r["answer"] == "changed" for r in tail) / len(tail)
        guess = q * (1 - q)
        out["rate_guess"] = _r(guess)
        if late <= LEARN_MASTERED:
            v = "mastered"
        elif late > early + LEARN_MARGIN and late > guess:
            v = "worse"
        elif late < guess - LEARN_MARGIN:
            v = "learning"
        elif late <= guess + LEARN_MARGIN:
            v = "rate"
        else:
            v = "lost"
    out["verdict"] = v
    out["text"] = LEARN_WORDS[v] + (f" ({len(rs)} looks)" if v == "new" else "")
    return out


def _toybox_rows() -> dict:
    try:
        from . import toybox as TB
        return {t["name"]: t for t in TB.list_toys()}
    except Exception:
        return {}


# ---- the clocks -------------------------------------------------------------

def _clocks(recs: list, now: float) -> dict:
    """Born, the gaps, the pass times (rung 6 honest), the bug windows. The
    operator's lines (#299: accepts) are not self running: left out."""
    srt = sorted((r for r in recs if r.get("kind") not in SL.OPERATOR_KINDS), key=lambda r: float(r.get("ts") or 0))
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
    first_person = next((float(r["ts"]) for r in srt if r.get("kind") == "push" and SL._medium(r) == "chat"
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
            if r.get("kind") == "push" and SL._medium(r) == "chat" and e and not e.endswith(":operator"):
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
    fix_lines = {}                                    # issue -> its first `fix` line (#259)
    for r in srt:
        if r.get("kind") == "fix":
            fix_lines.setdefault(r.get("issue"), float(r["ts"]))
    bugs = []
    for b in BUGS:
        if b["until"] == "rung7_restart":
            start = honest.get(b["opens_after"])
            fixed = restart7
        elif b["until"] == "fix_line":
            start = passed_at.get(b["opens_after"])
            start = None if start is None else max(start, b["opens_at"])
            fixed = fix_lines.get(b["issue"])
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
            bugs.append((start, stop, {**b, "open": fixed is None, "share": float(b.get("share", 1.0))}))
    avail, ready, built_on, walls = _availability(born, end, ts, honest)
    cuts = sorted({x for a, z, _ in bugs for x in (a, z)} | {x for a, z, _ in walls for x in (a, z)})

    def cost(t):
        """The running time the bugs and the walls cost up to t, as (bugs,
        walls): on each stretch between the windows' ends the single costliest
        reason -- never two (#259, #305). A wall costs all of it; a full bug
        wins a tie (its test was there, broken), a partial one gives way."""
        bug = wall = 0.0
        for a, z in zip(cuts, cuts[1:]):
            if a >= t:
                break
            share = max((w["share"] for x, y, w in bugs if x <= a and z <= y), default=0.0)
            if share < 1.0 and any(x <= a and z <= y for x, y, _ in walls):
                wall += running(gs, a, min(z, t))
            elif share:
                bug += share * running(gs, a, min(z, t))
        return bug, wall

    def bug_s(t):
        return cost(t)[0]

    def wall_s(t):
        return cost(t)[1]

    def working(t):
        return running(gs, born, t) - sum(cost(t))
    return {"born": born, "end": end, "gaps": gs, "first": first, "passed_at": passed_at, "honest": honest,
            "real6": real6, "first_person": first_person, "restart7": restart7, "bugs": bugs, "bug_s": bug_s, "working": working,
            "walls": walls, "wall_s": wall_s, "cost": cost, "avail": avail, "ready": ready, "built_on": built_on,
            "srt": srt}


def _availability(born: float, end: float, ts: list, honest: dict) -> tuple:
    """#305: when each achievement became LEARNABLE on this seat -- its
    prerequisites earned (ready) AND its test built (BUILT_AT, on the seat from
    the first restart after the merge) -- and the walls: the stretches no
    built achievement was learnable. One earned before its listed build (under
    an earlier test that still counts) was learnable from when it was ready."""
    restarts = [b for a, b in zip(ts, ts[1:]) if b - a > RESTART_S]
    got = {SL.RUNG_ID[n]: t for n, t in honest.items() if n in SL.RUNG_ID}
    built_on, ready, avail = {}, {}, {}
    for i, a in SL.ACHIEVEMENTS.items():
        m = BUILT_AT.get(i)
        if m is not None:
            built_on[i] = born if m["merged"] <= born else next((b for b in restarts if b > m["merged"]), None)
        else:
            built_on[i] = None
        ready[i] = None if any(p not in got for p in a["prereqs"]) \
            else max([born] + [got[p] for p in a["prereqs"]])
        r, b = ready[i], built_on[i]
        if r is None:
            avail[i] = None
        elif i in got and (b is None or got[i] < max(r, b)):
            avail[i] = min(r, got[i])
        else:
            avail[i] = None if b is None else max(r, b)
    pts = sorted({born, end} | {t for d in (got, ready, avail) for t in d.values()
                                if t is not None and born < t < end})
    walls: list = []
    for a, z in zip(pts, pts[1:]):
        if any(avail[i] is not None and avail[i] <= a < got.get(i, float("inf")) for i in SL.ACHIEVEMENTS):
            continue
        nxt = [x["name"] for i, x in SL.ACHIEVEMENTS.items()
               if (i not in got or got[i] > a) and ready[i] is not None and ready[i] <= a]
        why = WALL_WHY.format(next=", ".join(nxt) or "the next skill")
        if walls and walls[-1][1] == a and walls[-1][2]["why"] == why:
            walls[-1] = (walls[-1][0], z, walls[-1][2])
        else:
            walls.append((a, z, {"why": why}))
    walls = [(a, z, {**w, "open": z >= end}) for a, z, w in walls]
    return avail, ready, built_on, walls


# ---- the sections -------------------------------------------------------------

def _time(c: dict) -> dict:
    if c["born"] is None:
        return {"alive_h": 0.0, "running_h": 0.0, "down_h": 0.0, "bugs_h": 0.0, "walls_h": 0.0, "working_h": 0.0,
                "bugs": [], "walls": []}
    born, end, gs = c["born"], c["end"], c["gaps"]
    run = running(gs, born, end)
    bug, wall = c["cost"](end)

    def wall_cost(a, z):                                  # the part of a wall no full bug already took
        return c["wall_s"](z) - c["wall_s"](a)
    return {"alive_h": _r((end - born) / H), "running_h": _r(run / H), "down_h": _r((end - born - run) / H),
            "bugs_h": _r(bug / H), "walls_h": _r(wall / H), "working_h": _r((run - bug - wall) / H),
            "bugs": [{"issue": b["issue"], "what": b["what"], "from": iso(a), "to": iso(z),
                      "open": bool(b.get("open")), "share": b["share"],
                      "running_h": _r(running(gs, a, z) / H),
                      "cost_h": _r(b["share"] * running(gs, a, z) / H)} for a, z, b in c["bugs"]],
            "walls": [{"from": iso(a), "to": iso(z), "why": w["why"], "open": w["open"],
                       "running_h": _r(running(gs, a, z) / H), "cost_h": _r(wall_cost(a, z) / H)}
                      for a, z, w in c["walls"]]}


def _achievements(c: dict) -> list:
    out = []
    if c["born"] is None:
        return out
    for n in sorted(c["first"]):
        r = c["first"][n]
        t = c["honest"].get(n, float(r["ts"]))
        row = {"rung": n, "id": SL.RUNG_ID.get(n), "name": SL.RUNG_NAMES.get(n, ""), "at": iso(float(r["ts"])),
               "working_h": _r(c["working"](t) / H), "wall_h": _r((t - c["born"]) / H),
               "skill": r.get("skill"), "eid": _short(r.get("eid")) if r.get("eid") else None}
        if n == 6 and c["real6"] is not None:
            row["earned_at"] = iso(c["real6"])
            row["note"] = ("first passed against the empty operator slot (#208); its hours count from "
                           "when its test first held with the person who actually chats")
        out.append(row)
    return out


def _learnable(c: dict, i) -> dict:
    """#305: a skill's row -- when it became learnable (its prerequisites
    earned and its test on the seat), how long it waited on us before that
    (running hours from its prerequisites' last pass), and the working hours
    from then to its earning (None until earned)."""
    out = {"id": i, "available_at": None, "waited_h": None, "learn_h": None}
    if c["born"] is None or i is None:
        return out
    r, av = c["ready"].get(i), c["avail"].get(i)
    rid = {v: k for k, v in SL.RUNG_ID.items()}.get(i)
    got = c["honest"].get(rid)
    out["available_at"] = iso(av)
    if r is not None:
        out["waited_h"] = _r(running(c["gaps"], r, av if av is not None else c["end"]) / H)
    if av is not None and got is not None:
        out["learn_h"] = _r((c["working"](got) - c["working"](av)) / H)
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
                     **_learnable(c, SL.RUNG_ID.get(n)),
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


# ---- the attributes (#300): traits of self as a whole, measured from its record --------
#
# Not the skills' test scores (those live with each skill), and never a gate: no
# pass line, nothing reads them to decide anything. Each is computed from the record
# alone, over its most recent stretch, and the same formula over the record as it
# stood every SKILL_STEP_S back gives its trend.
#
# A LOOK is a reach line; an ORDINARY look is one answered ("changed"/"unchanged"),
# with a prediction p, outside every blind audit (rung 2's `audit`, rung 5's `audit5`).

TRAIT_LOOKS = 500            # the recent stretch: its last 500 looks (about an hour of working time)
TRAIT_SHIFTS = 15            # Adaptability reads its last 15 noticed shifts
TRAIT_REPLIES = 50           # Sociability's reply rate reads its last 50 reply checks
TRAITS = (
    ("curiosity", "Curiosity", "share", "higher",
     "the share of its recent looks spent on things it is still learning (learning progress above "
     "curiosity's floor)"),
    ("insight", "Insight", "skill", "higher",
     "how well its predictions match what it finds, across everything it looks at: Brier skill "
     "against the recent change rate"),
    ("adaptability", "Adaptability", "looks", "lower",
     "how many looks it takes to notice the world changed: from the first miss to the shift it "
     "records (median of recent shifts)"),
    ("focus", "Focus", "share", "higher",
     "how narrow its attention is: 1 − the spread (entropy) of its recent looks over everything it "
     "has looked at"),
    ("sociability", "Sociability", "share", "higher",
     "the share of its recent acts aimed at others (lines sent, ball rolled), and how often they answer"),
)


def _ordinary(r) -> bool:
    return (r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged") and r.get("p") is not None
            and not r.get("audit") and not r.get("audit5"))


def _miss(r) -> bool:
    """A look its prediction got wrong: it put the outcome below even odds."""
    return (float(r["p"]) < 0.5) == (r["answer"] == "changed")


def _curiosity_v(recs: list):
    """CURIOSITY = (its last TRAIT_LOOKS ordinary looks at things whose learning
    progress -- self_loop._progress, the machinery its curiosity tilt reads -- is
    above CURIO_MIN) / (those looks at things that have a progress reading). A
    thing too new to have one (under 2 x CURIO_WINDOW looks) is left out."""
    lp = SL._progress(recs)
    win = [r["eid"] for r in recs if _ordinary(r)][-TRAIT_LOOKS:]
    judged = [e for e in win if e in lp]
    if not judged:
        return None, {"looks": 0}
    learning = sum(lp[e] > SL.CURIO_MIN for e in judged)
    return learning / len(judged), {"looks": len(judged), "things": len(set(judged)),
                                    "learning": len({e for e in judged if lp[e] > SL.CURIO_MIN})}


def _insight_v(recs: list):
    """INSIGHT = 1 − Brier(p) / Brier(base) over its last TRAIT_LOOKS ordinary
    looks, base = the share of those looks that found a change (the best
    constant guess over the stretch). 0: no better than knowing the overall
    rate; 1: every outcome called with certainty; negative: worse than the rate.
    The detail adds the calibration error (ECE over ten bins of p)."""
    win = [r for r in recs if _ordinary(r)][-TRAIT_LOOKS:]
    if len(win) < 2:
        return None, {"looks": len(win)}
    xs = [1.0 if r["answer"] == "changed" else 0.0 for r in win]
    ps = [float(r["p"]) for r in win]
    rate = sum(xs) / len(xs)
    base = rate * (1.0 - rate)
    brier = sum((x - p) ** 2 for x, p in zip(xs, ps)) / len(xs)
    bins: dict = {}
    for x, p in zip(xs, ps):
        bins.setdefault(min(9, int(p * 10)), []).append((x, p))
    ece = sum(abs(sum(x for x, _ in b) - sum(p for _, p in b)) for b in bins.values()) / len(xs)
    det = {"looks": len(win), "brier": _r(brier, 4), "base": _r(base, 4), "rate": _r(rate, 4), "ece": _r(ece, 4)}
    if base <= 0:
        return None, det
    return 1.0 - brier / base, det


def _adaptability_v(recs: list):
    """ADAPTABILITY = the median, over its last TRAIT_SHIFTS `shift` lines (rung
    5's noticing), of the looks from the first miss to the shift: of the window
    of `looks` ordinary looks at that thing that showed the change (since its
    previous shift), the looks from the first one its prediction got wrong
    (_miss) to the last, inclusive; the whole window when none was a miss."""
    shifts = [r for r in recs if r.get("kind") == "shift"][-TRAIT_SHIFTS:]
    if not shifts:
        return None, {"shifts": 0}
    want = {id(r) for r in shifts}
    looks: dict = {}
    out = []
    for r in recs:
        if _ordinary(r):
            looks.setdefault(r["eid"], []).append(r)
        elif r.get("kind") == "shift":
            seen = looks.pop(r.get("eid"), [])
            if id(r) in want:
                w = seen[-max(1, int(r.get("looks") or 1)):]
                if w:
                    first = next((i for i, x in enumerate(w) if _miss(x)), 0)
                    out.append(len(w) - first)
    if not out:
        return None, {"shifts": 0}
    s = sorted(out)
    m = len(s) // 2
    med = float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2.0
    return med, {"shifts": len(s)}


def _focus_v(recs: list):
    """FOCUS = 1 − H / log(K): H the entropy of which thing each of its last
    TRAIT_LOOKS looks went to, K the things it has ever looked at (2 or more;
    one thing known is full focus). 1: every look at one thing; 0: spread
    evenly over everything it knows. The detail adds the repeat rate (a look at
    the same thing as the look before) and the most-looked-at thing's share."""
    import math
    eids = [r.get("eid") for r in recs if r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5")]
    if not eids:
        return None, {"looks": 0}
    win = eids[-TRAIT_LOOKS:]
    known = len(set(eids))
    n: dict = {}
    for e in win:
        n[e] = n.get(e, 0) + 1
    h = -sum(c / len(win) * math.log(c / len(win)) for c in n.values())
    v = 1.0 if known < 2 else max(0.0, 1.0 - h / math.log(known))
    rep = sum(a == b for a, b in zip(win, win[1:])) / (len(win) - 1) if len(win) > 1 else None
    return v, {"looks": len(win), "things": len(n), "known": known, "repeat": _r(rep, 4),
               "top_share": _r(max(n.values()) / len(win), 4)}


def _sociability_v(recs: list):
    """SOCIABILITY = pushes / (pushes + looks) over its last TRAIT_LOOKS acts --
    a push is an act aimed at an other (a line sent, the ball rolled); a look
    is its attention on the world. The detail adds how often they answer: the
    share of its last TRAIT_REPLIES reply checks that found a reply."""
    acts = [r for r in recs if r.get("kind") == "push"
            or (r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5"))][-TRAIT_LOOKS:]
    if not acts:
        return None, {"acts": 0}
    pushes = sum(r.get("kind") == "push" for r in acts)
    checks = [r for r in recs if r.get("kind") == "reach" and r.get("reply")
              and r.get("answer") in ("changed", "unchanged")][-TRAIT_REPLIES:]
    replied = sum(r["answer"] == "changed" for r in checks) / len(checks) if checks else None
    return pushes / len(acts), {"acts": len(acts), "pushes": pushes, "checks": len(checks),
                                "replied": _r(replied, 4)}


TRAIT_FN = {"curiosity": _curiosity_v, "insight": _insight_v, "adaptability": _adaptability_v,
            "focus": _focus_v, "sociability": _sociability_v}


def traits(recs: list) -> list:
    """THE ATTRIBUTES (#300): each trait's value now (its formula over the
    whole record), what it measures, its detail, and its trend: the same
    formula over the record as it stood every SKILL_STEP_S back (SKILL_POINTS
    points, like the skills' sparklines). None where nothing measures it yet."""
    import bisect
    srt = sorted(recs, key=lambda r: float(r.get("ts") or 0))
    ts = [float(r.get("ts") or 0) for r in srt]
    end = ts[-1] if ts else 0.0
    cuts = [(k * SKILL_STEP_S / H, bisect.bisect_right(ts, end - k * SKILL_STEP_S) if ts else 0)
            for k in range(SKILL_POINTS - 1, -1, -1)]
    out = []
    for key, label, unit, better, what in TRAITS:
        fn = TRAIT_FN[key]
        v, det = fn(srt)
        series = [{"ago_h": ago, "value": _r(fn(srt[:cut])[0], 4) if cut else None} for ago, cut in cuts[:-1]]
        series.append({"ago_h": 0.0, "value": _r(v, 4)})
        hour = next((p["value"] for p in series if p["ago_h"] == 1.0), None)
        out.append({"key": key, "label": label, "value": _r(v, 4), "unit": unit, "better": better, "what": what,
                    "detail": det, "trend_h": _r(v - hour, 4) if v is not None and hour is not None else None,
                    "series": series})
    return out


def _sg(v) -> str:
    return ("−" if v < 0 else "+") + f"{abs(v):.2f}"


def _quest(recs: list, st: dict, progs: dict) -> dict:
    """The quest log (kept for older readers; #300's card shows `skills`): the
    achievement the exp bar reads (status's aim), its criteria staged."""
    aim = st.get("skill_rung")                     # #295: the achievement the exp bar reads (status)
    aim = aim if aim in SL.PROGRESS else None
    if aim is None:
        return {"rung": None, "name": None, "criteria": [], "bar": st.get("bar"), "waiting_on": st.get("waiting_on"),
                "summary": "", "mode": st.get("mode")}
    crits = _criteria(aim, recs, progs)
    return {"rung": aim, "id": SL.RUNG_ID.get(aim), "name": SL.RUNG_NAMES.get(aim, ""), "criteria": crits, "bar": st.get("bar"),
            "waiting_on": st.get("waiting_on"), "slipping": st.get("slipping"), "mode": st.get("mode"),
            "summary": " · ".join(c["text"] + (" ✓" if c["met"] else "") for c in crits)}


def _criteria(aim: int, recs: list, progs: dict) -> list:
    """An achievement's test (by its rung number) as staged criteria, each in words."""
    p = progs[aim]
    ball = SL._cur_medium(recs, SL._other(recs)) == "toy"      # #264: rung 7 takes turns with the ball
    ck = SL._clock(int(p.get("clock") or 1)) if aim == 7 else SL._clock(1)   # #278: the bar of the turns' clock
    count = {1: {"pushes": (SL.RUNG1_CYCLES, "pushed cycles")},
             2: {"reaches": (SL.RUNG2_REACHES, "answered looks at one thing")},
             3: {"looks": (SL.RUNG3_LOOKS, "steered looks")},
             4: {"timed": (SL.RUNG4_TIMED, "timed looks")},
             5: {"looks": (SL.RUNG5_LOOKS, "looks with noticing on")},
             6: {"pushes": (SL.RUNG6_PUSHES, "lines sent"), "heard": (SL.RUNG6_HEARD, "answered looks at the other")},
             7: {"pushes": (SL.RUNG7_PUSHES, "turns with the ball with a reply check (you roll it back)" if ball
                            else "timed lines with a reply check")},
             8: {"looks": (SL.RUNG8_LOOKS, "reply checks")},
             11: {"pushes": (SL.SPEAK_PUSHES, "timed lines with a reply check")}}.get(aim, {})
    skill = {1: (SL.RUNG1_SKILL, "feel skill"), 2: (SL.RUNG2_SKILL, "reach skill"),
             3: (SL.RUNG2_SKILL, "reach skill on a moving thing"), 8: (SL.RUNG8_MARGIN, "skill vs running rate")}
    flags = {"audit": "both audit shifts caught" if aim == 5 else "blind audit passed",
             "kinds": "kinds agree across halves", "split": "the other reads as other"}
    crits = []
    for key, frac in p["crit"].items():
        met = frac >= 1.0
        if key in count and aim == 11:                 # #311: both kinds, each at least SPEAK_EACH
            need, label = count[key]
            text = (f"{min(need, int(p.get('n') or 0))}/{need} {label} ({int(p.get('n_words') or 0)} with words, "
                    f"{int(p.get('n_plain') or 0)} plain; {SL.SPEAK_EACH} of each)")
        elif key in count:
            need, label = count[key]
            text = f"{min(need, round(frac * need))}/{need} {label}"
        elif key == "truth":
            fl = int(p.get("false") or 0)
            text = ("every utterance true of its record" if not fl
                    else f"{fl} utterance{'s' if fl != 1 else ''} not true of its record")
        elif key == "lift":
            rw, rp = p.get("rate_words"), p.get("rate_plain")
            text = (f"replies to words {'–' if rw is None else f'{rw:.2f}'} vs plain {'–' if rp is None else f'{rp:.2f}'} "
                    f"(z {float(p.get('z') or 0):.1f} → {SL.SPEAK_LIFT_Z:.1f}, {_sg(SL.SPEAK_LIFT_MARGIN)})")
        elif key == "timing":
            sh, ch = p.get("timing_share"), p.get("timing_chance")
            text = (f"lines in its window {'–' if sh is None else f'{sh:.2f}'} vs chance "
                    f"{'–' if ch is None else f'{ch:.2f}'} (z {float(p.get('timing_z') or 0):.1f} → "
                    f"{SL.RUNG7_CONT_Z:.1f}, +{SL.RUNG7_CONT_MARGIN:.2f})")
        elif key == "skill" and aim in skill:
            tgt, label = skill[aim]
            text = f"{label} {_sg(float(p['skill']))} → {_sg(tgt)}"
        elif key == "focus":
            v = _sort_value(recs)
            text = f"answered share {'–' if v is None else f'{v:.2f}'} → over {1 - SL.RUNG3_FOCUS:.2f}"
        elif key == "calibration":
            v = _calibration(recs)
            text = f"calibration error {'–' if v is None else f'{v:.2f}'} → ≤ {SL.RUNG4_CALIB:.2f}"
        elif key == "news":
            text = f"timed looks find news ({round(100 * frac)}% of the way)"
        elif key == "contingency":
            sh, ch = p.get("share"), p.get("chance")
            text = (f"{'turns' if ball else 'lines'} in its window {'–' if sh is None else f'{sh:.2f}'} vs chance "
                    f"{'–' if ch is None else f'{ch:.2f}'} (z {float(p.get('z') or 0):.1f} → {ck['cont_z']:.1f}, "
                    f"+{ck['cont_margin']:.2f})")
        elif key == "payoff":
            pay = p.get("payoff")
            text = (f"replies gained over timing-blind {'–' if pay is None else _sg(float(pay))} "
                    f"(t {float(p.get('payoff_z') or 0):.1f} → {ck['pay_z']:.1f}, {_sg(ck['pay_margin'])})")
        else:
            text = flags.get(key, key)
        crits.append({"key": key, "text": text, "met": met, "frac": _r(frac, 4)})
    return crits


def _learning_ids(tr: list, allowed=None) -> list:
    """The skills on the card's "Learning now" (#300): open in the tree and
    practised now (in status's `allowed`: none asleep) -- and (#299) the
    ones only the operator's accept holds back (every prerequisite earned,
    one not accepted yet), which say so."""
    return [n["id"] for n in tr if (n["state"] == "open" and (allowed is None or n["id"] in allowed))
            or _accept_held(n)]


def _accept_held(n: dict) -> bool:
    return n["state"] == "locked" and str(n.get("waiting_on") or "").startswith("your accept of ")


def _skills(recs: list, tempo: dict, st: dict, progs: dict, tr: list) -> list:
    """THE SKILLS ON THE CARD (#300): each skill it is learning now -- its
    test's staged criteria, its bar, what it waits on (the first stage not
    met; status's word for the one the exp bar reads, e.g. "the world"), and
    its skill value's trend. Earned skills are not here (the Skill tree tab
    has them); one that slips is in `slipping`."""
    out = []
    nodes = {nd["id"]: nd for nd in tr}
    for i in _learning_ids(tr, set(st.get("allowed") or ())):
        n = SL.ACHIEVEMENTS[i]["rung"]
        p = progs[n]
        bar, waiting = SL._staged(p.get("crit") or {})
        held = _accept_held(nodes[i])                    # #299: not practised until the operator accepts
        if held:
            waiting = nodes[i]["waiting_on"]
        elif st.get("aim") == i and st.get("waiting_on"):
            waiting = st["waiting_on"]
        out.append({"id": i, "name": SL.ACHIEVEMENTS[i]["name"], "branch": SL.ACHIEVEMENTS[i]["branch"],
                    "awaiting_accept": held,
                    "bar": _r(max(0.0, min(1.0, bar)), 4), "waiting_on": waiting,
                    "criteria": _criteria(n, recs, progs), "trend": _skill_trend(n, recs, tempo, SL.SKILL_PASS.get(n))})
    return out


def _slipping(tr: list, progs: dict, recs: list) -> list:
    """The earned skills whose test no longer holds (#300): a short line each,
    naming the first criterion that fails now. Still earned (the record keeps
    the pass); nothing is taken away."""
    out = []
    for nd in tr:
        if nd["state"] != "earned" or not nd["built"]:
            continue
        n = SL.ACHIEVEMENTS[nd["id"]]["rung"]
        p = progs.get(n)
        if p is not None and not p.get("passed"):
            _, waiting = SL._staged(p.get("crit") or {})
            why = next((c["text"] for c in _criteria(n, recs, progs) if not c["met"]), None)
            out.append({"id": nd["id"], "name": nd["name"], "waiting_on": waiting,
                        "text": nd["name"] + ": its test no longer holds" + (f" ({why})" if why else "")})
    return out


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
    chat_eids = {str(r.get("to")) for r in recs if r.get("kind") == "push" and r.get("to")
                 and SL._medium(r) == "chat"}             # #264: a turn with the ball is no chat
    chat_eids |= {str(r.get("eid")) for r in recs if r.get("kind") == "reach" and "chat:in:" in str(r.get("eid") or "")}
    for e in sorted(chat_eids):
        rs = [r for r in recs if _answered(r) and r.get("eid") == e]
        tail = rs[-SL.RUNG8_LOOKS:]
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
    r8 = st.get("rung8") or {}
    if r8.get("asked") or model_name in rows:
        row = rows.setdefault(model_name, {"who": model_name, "kind": "model", "current": False,
                                           "chat": None, "play": None})
        row["rung8"] = {"asked": r8.get("asked"), "pairs": r8.get("paired"),
                        "brier_model": r8.get("brier_model"), "brier_self": r8.get("brier_self"),
                        "weight": r8.get("advisor_weight"), "model": r8.get("advisor_model")}
    others = sorted(rows.values(), key=lambda x: (not x["current"], x["kind"] == "model",
                                                  -((x.get("chat") or {}).get("looks") or 0), x["who"]))
    return {"others": others,
            "body": sorted(_short(e) for e, s in split.items() if s == "body"),
            "other": sorted(_short(e) for e, s in split.items() if s == "other")}


SKILL_STEP_S = 1800.0        # the skill's trend line: one point per half hour of the record
SKILL_POINTS = 9             # ...over the last four hours
SKILL_LABEL = {1: "feel skill", 2: "reach skill", 3: "reach skill on a moving thing", 4: "news share of timed looks",
               5: "shifts noticed", 6: "the other read as other", 7: "replies gained by its timing",
               8: "skill vs running rate", 11: "replies gained by its words"}


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
    rungs 2-3). #300: each skill on the card carries its own (_skill_trend)."""
    return _skill_trend(st.get("skill_rung"), recs, tempo, st.get("skill_pass"))


def _skill_trend(n, recs: list, tempo: dict, pass_=None) -> dict | None:
    if n not in SL.PROGRESS or not recs:
        return None
    srt = sorted((r for r in recs if r.get("kind") not in SL.OPERATOR_KINDS),   # #299: an accept is not self's time
                 key=lambda r: float(r.get("ts") or 0))
    ts = [float(r.get("ts") or 0) for r in srt]
    import bisect
    end = ts[-1]
    series = []
    for k in range(SKILL_POINTS - 1, -1, -1):
        cut = bisect.bisect_right(ts, end - k * SKILL_STEP_S)
        series.append({"ago_h": k * SKILL_STEP_S / H, "value": _r(_skill_at(n, srt[:cut], tempo), 4) if cut else None})
    now_v = series[-1]["value"]
    hour = next((p["value"] for p in series if p["ago_h"] == 1.0), None)
    return {"rung": n, "label": SKILL_LABEL.get(n, "skill"), "value": now_v, "pass": pass_,
            "trend_h": _r(now_v - hour, 4) if now_v is not None and hour is not None else None, "series": series}


def _header(st: dict, recs: list, c: dict, t: dict, skill: dict | None = None, learning: list | None = None) -> dict:
    nxt = list(st.get("next") or [])              # #295: the open achievements (ids)
    level = st.get("level") or 0
    latest = st.get("rung_name") or SL.RUNG_NAMES[0]    # #295: the latest earned achievement's name (key kept)
    return {"name": SN.current(), "level": level, "rung_name": latest,
            "title": f"Lv {level} · {latest}",           # #300: the card's headline -- skills, never rungs
            "learning": [SL.ACHIEVEMENTS[i]["name"] for i in (learning or [])],
            "next": nxt, "next_name": ", ".join(SL.ACHIEVEMENTS[i]["name"] for i in nxt) or None,
            "allow": st.get("allow"),
            "bar": st.get("bar"), "waiting_on": st.get("waiting_on"), "slipping": st.get("slipping"),
            "held": st.get("held"), "mode": st.get("mode"), "limit": st.get("limit"),
            "skill": skill,
            "born": iso(c["born"]), "age_h": t["alive_h"], "working_h": t["working_h"], "running_h": t["running_h"],
            "cycles": st.get("cycles") or 0,
            "looks": sum(1 for r in recs if r.get("kind") == "reach"),
            "lines_sent": sum(1 for r in recs if r.get("kind") == "push" and SL._medium(r) == "chat"),
            "turns": sum(1 for r in recs if r.get("kind") == "push" and SL._medium(r) == "toy")}


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
    attrs = traits(recs)                                  # #300: traits of self as a whole, not test scores
    tr = tree(recs, st, progs)
    ach = _achievements(c)
    from . import toy_unlocks as TU                       # #267: each pass's new toys, on its achievement
    unl = TU.by_rung(recs)
    for a in ach:
        if unl.get(a["rung"]):
            a["toys"] = unl[a["rung"]]
    quest = _quest(recs, st, progs)
    skills = _skills(recs, tempo, st, progs, tr)
    if c.get("restart7") is not None:                     # #254: rung 7 (rung 8 since #258) restarted on the fix
        note = RESTART7.format(day=dt.datetime.fromtimestamp(c["restart7"], dt.timezone.utc).strftime("%Y-%m-%d"))
        for k in skills:
            if k["id"] == "expect":
                k["note"] = note
        if quest.get("rung") == 8:
            quest["note"] = note
    voided = _voided(recs)
    if voided:
        quest["voided"] = voided
    return {"empty": not recs, "generated": now, "level": st.get("level") or 0, "tree": tr,
            "accepted": list(st.get("accepted") or []), "awaiting": list(st.get("awaiting") or []),   # #299
            "header": _header(st, recs, c, t, _skill(recs, tempo, st), [k["id"] for k in skills if not k.get("awaiting_accept")]), "attributes": attrs,
            "skills": skills, "slipping": _slipping(tr, progs, recs),
            "quest": quest, "achievements": ach,
            "growth": _growth(c, ach), "time": t, "inventory": _inventory(recs, st, now),
            "relationships": _relationships(recs, tempo, st), "toys": _toys_section(recs)}


def tree(recs: list, st: dict, progs: dict | None = None) -> list:
    """THE SKILL TREE (#295), the 👶 panel's data contract: every achievement
    in tree order, its branch, prerequisites (and why each is one), its state
    (earned / open: built, unearned, every prerequisite earned and accepted /
    locked / unbuilt), whether the operator accepted it (#299: `accepted`;
    an earned one not yet is awaiting, and its children stay locked "on your
    accept"), when it was earned and with what skill, its staged bar and
    what it waits on, and the child's age and ability its test measures."""
    got = SL._earn_times(recs)
    acc = SL._accepted(recs)
    asleep = st.get("mode", "off") == "off"
    out = []
    for i, a in SL.ACHIEVEMENTS.items():
        row = {"id": i, "name": a["name"], "branch": a["branch"], "prereqs": list(a["prereqs"]),
               "built": a["built"], "earned_at": None, "skill": None, "bar": 0.0, "waiting_on": None,
               "child_age": a["child_age"], "child_ability": a["child_ability"], "why_prereqs": a["why_prereqs"],
               "rung": a["rung"], "accepted": i in acc}
        if i in got:
            r = got[i]
            sk = r.get("skill")
            row.update(state="earned", earned_at=iso(float(r.get("ts") or 0)), bar=1.0,
                       skill=_r(float(sk), 4) if isinstance(sk, (int, float)) else None)
        elif not a["built"]:
            row.update(state="unbuilt", waiting_on="not built yet")
        elif any(p not in got for p in a["prereqs"]):
            row.update(state="locked", waiting_on=", ".join(SL.ACHIEVEMENTS[p]["name"] for p in a["prereqs"]
                                                            if p not in got))
        elif any(p not in acc for p in a["prereqs"]):
            row.update(state="locked", waiting_on="your accept of " + ", ".join(
                SL.ACHIEVEMENTS[p]["name"] for p in a["prereqs"] if p not in acc))
        else:
            p = (progs or {}).get(a["rung"]) or SL.PROGRESS[a["rung"]](recs, {})
            bar, waiting = SL._staged(p.get("crit") or {})
            row.update(state="open", bar=_r(max(0.0, min(1.0, bar)), 4), waiting_on=waiting,
                       skill=_r(float(p.get("skill") or 0.0), 4))
            if asleep:
                row["waiting_on"] = "sleep"
        out.append(row)
    return out


def _toys_section(recs: list) -> list:
    """#267: every toybox kind, unlocked or locked (toy_unlocks.table)."""
    from . import toy_unlocks as TU
    have = {t["kind"] for t in _toybox_rows().values()}
    rows = TU.table(recs, have)
    for r in rows:
        r["at"] = iso(r["at"]) if r["at"] is not None else None
    return rows


def _voided(recs: list) -> list:
    """The passes on the record that don't count, said plainly: each one a
    `rung_void` line names (#254), and -- #258 -- each pass above rung 6 from
    before the ladder flip (the old numbering's)."""
    def at(t):
        return dt.datetime.fromtimestamp(float(t), dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    out, said = [], set()
    for r in recs:
        if r.get("kind") == "rung_void" and isinstance(r.get("voids_ts"), (int, float)):
            n = r.get("rung")
            old = next((x for x in recs if x.get("kind") == "rung" and x.get("rung") == n
                        and float(x.get("ts") or 0) == float(r["voids_ts"])), None)
            if isinstance(n, int) and n in OLD_NAMES and (old is None or old.get("ladder") != SL.LADDER_V):
                out.append(VOIDED_OLD.format(rung=n, name=OLD_NAMES[n], now=n + 1, at=at(r["voids_ts"])))
            else:
                out.append(VOIDED.format(rung=n, at=at(r["voids_ts"])))
            said.add((n, float(r["voids_ts"])))
    for r in recs:
        n = r.get("rung")
        if (r.get("kind") == "rung" and isinstance(n, int) and n >= SL.FLIPPED_FROM
                and r.get("ladder") != SL.LADDER_V and (n, float(r.get("ts") or 0)) not in said):
            out.append(FLIPPED.format(rung=n, name=OLD_NAMES.get(n, SL.RUNG_NAMES.get(n + 1, "")), now=n + 1,
                                      at=at(r["ts"]), new=SL.RUNG_NAMES.get(n, "")))
    return out


def empty() -> dict:
    """The sheet's shape with nothing in it (a seat without self)."""
    c = {"born": None, "srt": []}
    return {"empty": True, "generated": time.time(),
            "level": 0, "tree": tree([], {}), "accepted": [], "awaiting": [],
            "header": {"name": SN.current(), "level": 0, "rung_name": SL.RUNG_NAMES[0],
                       "title": "Lv 0 · " + SL.RUNG_NAMES[0], "learning": [], "next": [], "mode": "off",
                       "age_h": 0.0, "working_h": 0.0, "skill": None},
            "attributes": traits([]), "skills": [], "slipping": [],
            "quest": {"rung": None, "criteria": []}, "achievements": [],
            "growth": _growth(c, []), "time": _time(c), "inventory": [],
            "relationships": {"others": [], "body": [], "other": []}, "toys": []}


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
    rt["sheet"] = (now, out, name)
    return out


def card(srv, now: float | None = None) -> dict:
    """#283: the zoo's card -- self on this seat in a few fields, cheap
    enough to poll across seats (GET /membrane/self/card): the name, the
    level and its rung's name, the mode (awake = not off) and the record's
    last line (its time, ISO; None before self's first line). The level is
    the sheet's (cached CACHE_S); the mode and the last line are live."""
    now = time.time() if now is None else float(now)
    name = SN.current()
    if srv is None:
        return {"name": name, "level": 0, "rung_name": SL.RUNG_NAMES.get(0, ""), "next": [], "mode": "off",
                "awake": False, "last_activity": None, "generated": now}
    h = sheet(srv, now).get("header") or {}
    mode = SL.mode(srv)
    with SL._lock:
        recs = SL._read(srv)
        mine = next((r for r in reversed(recs) if r.get("kind") not in SL.OPERATOR_KINDS), None)   # #299: not an accept
        last = float(mine.get("ts") or 0) if mine else None
    return {"name": name, "level": h.get("level") or 0, "rung_name": h.get("rung_name") or "",
            "next": list(h.get("next") or []), "mode": mode, "awake": mode != "off",
            "last_activity": iso(last) if last else None, "generated": now}


# ---- the dock: the toys alone, light enough to poll ---------------------------------

def forget_dock(srv) -> None:
    """Drop the dock's cache (a poke landed: the next poll shows it)."""
    if srv is not None:
        SL._rt(srv).pop("dock", None)


def dock(srv, now: float | None = None, fresh: bool = False) -> dict:
    """Each toybox toy, live: its reading, its last change (who, how long
    ago), its health and its stats. Cached DOCK_CACHE_S per seat."""
    now = time.time() if now is None else float(now)
    who = SN.current()                                   # #283: self's name ("name" is a toy's below)
    if srv is None:
        return {"generated": now, "name": who, "toys": [], "locked": [], "level": 0, "tree": tree([], {})}
    rt = SL._rt(srv)
    hit = rt.get("dock")
    if not fresh and hit is not None and 0 <= now - hit[0] < DOCK_CACHE_S and hit[2] == who:
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
    block_of = {t["eid"]: t["block"] for t in ents if t.get("toy") and t.get("block")}
    looks: dict[str, list] = {}
    by_block: dict[tuple, list] = {}
    for r in recs:
        if _answered(r) and r.get("eid") in want:
            looks.setdefault(want[r["eid"]], []).append(r)
            if r["eid"] in block_of:
                by_block.setdefault((want[r["eid"]], block_of[r["eid"]]), []).append(r)
    from . import toy_unlocks as TU                      # #267: grandfathered toys, the next locked ones
    toys = []
    for row in rows:
        name, kind = row["name"], row["kind"]
        if kind == "tower":
            reading = {"blocks": [ch == "|" for ch in str(row.get("reading") or "")]}
        elif kind == "ball":
            reading = {"side": row.get("reading")}               # #264: whose side it is on
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
                     "changes": dict(row.get("counts") or {}), "last": last,
                     "learning": learning(rs),                     # #276: the verdict from those numbers
                     **({"blocks": [{"block": b, **{k: v for k, v in learning(by_block[(name, b)]).items()
                                                    if k in ("verdict", "looks")}}
                                    for b in sorted(b for (n, b) in by_block if n == name)]}
                        if kind == "tower" else {}),
                     "unlock_rung": TU.rung_of(kind), "grandfathered": TU.grandfathered(recs, kind)})
    toys.sort(key=lambda t: t["kind"] != "ball")                 # #264: the ball first (locked rows follow the toys)
    sh = sheet(srv, now)                                 # #295: the tree and the level, as the (cached) sheet has them
    out = SN.say_all({"generated": now, "name": who, "toys": toys,
                      "locked": TU.locked_rows(recs, {r["kind"] for r in rows}),
                      "level": sh.get("level") or 0, "tree": sh.get("tree") or [],
                      "accepted": sh.get("accepted") or [], "awaiting": sh.get("awaiting") or []}, who)
    rt["dock"] = (now, out, who)
    return out
