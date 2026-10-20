"""TOYS UNLOCK AS SELF GROWS (#267, part of #161): the operator's rewards.

#295, THE SKILL TREE: a kind unlocks when self EARNS an achievement (UNLOCK_IDS:
the achievement whose test is the rung in UNLOCKS -- tower: tell things apart,
burst: patience, die and drift: notice the world changed, ball: meet an other,
mirror: expect a reply, ...). Achievements are earned out of order, so the
toys are too; the record's unlock line carries the achievement's `id` beside
its `rung` (the test's number, as before).

Each toybox kind has an unlock rung (UNLOCKS). Body, fast and slow come with
birth; the rest unlock when self passes a rung, and each unlock is also the
next lesson (the mirror, unlocked at rung 8, is the apparatus of rung 9:
recognize itself). "Passes rung N" is the ladder's own rule,
self_loop._passed: a voided pass doesn't count (#254), and a pass above rung
6 counts only with "ladder": 2 (#258).

THE RECORD. On a pass that unlocks something sync() appends one line to
self's record, once per rung:

    {"ts": ..., "kind": "unlock", "rung": 8, "toys": ["mirror"]}

(`added`: the toys it provisioned; `late`: the pass was already old news --
an upgraded seat catching up, which the page does not celebrate). Birth is
no pass: body, fast and slow are open from the start, with no line and
nothing switched on (the operator adds them, as before). On a pass, the
unlocked BUILT-IN kinds are provisioned under their
default names once, when the line is written: a kind the seat already has
(by any name) is left alone, and a toy the operator later removes is not
brought back.

NO TOY EVER DISAPPEARS. A toy whose kind is still locked stays
(grandfathered: the dock and the sheet say so). Adding a locked kind is
refused with the rung that unlocks it (toybox.add: the CLI and the toybox
object). Before self is born nothing is locked: the toybox is the operator's
bench, and whatever is on it at birth is grandfathered.

BUILT LATE (#264). A kind unlocked before it was built (the ball: its rung 6
unlock line could be written while it was a name only) is provisioned once,
the first time it is built, with one `unlock_built` line -- the same
"never brought back" rule.

PLACEHOLDERS. mirror, blocks and rattle are kinds with a name, an icon, an
unlock rung and one line of what each will teach -- no mechanics yet (those
are built with their rungs). They are never provisioned, never poked, and
adding one is refused (locked, or unlocked but not built yet).

ACCEPTED, NOT ONLY EARNED (#299). A kind unlocks when its achievement is
earned AND the operator has accepted it (_opened): an earned achievement
waiting for its accept opens nothing, toys included. The unlock line is
written when the accept lands (self_loop.accept syncs at once); `late` is
measured from the later of the pass and the accept.

GRADING. No rung's test reads an unlock line or a toy's unlock: an unlock
never makes a rung easier to pass.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from . import self_loop as _SL
from .self_sheet import NAME as _NAME

UNLOCKS = {"body": 0, "fast": 0, "slow": 0, "tower": 3, "burst": 4, "die": 5, "drift": 5,
           "ball": 6, "mirror": 8, "blocks": 9, "rattle": 10}
ICONS = {"body": "🫧", "fast": "⚡", "slow": "🐢", "tower": "🗼", "burst": "✦", "die": "🎲", "drift": "〰️",
         "ball": "⚽", "mirror": "🪞", "blocks": "🧱", "rattle": "🔊"}
TEACHES = {                 # read by people (the dock, the sheet): self goes by its name (#277)
    "body": f"changes only when {_NAME} looks: its own body, the control",
    "fast": f"changes faster than {_NAME} can look: none of it {_NAME}'s doing",
    "slow": "changes once an hour: long runs of the same answer",
    "tower": f"{_NAME} topples and lifts its blocks: its first act on the world",
    "burst": "busy windows and still ones: when to look",
    "die": "a memoryless change: surprise, and noticing the world changed",
    "drift": "a random walk on its own clock: noticing a slow change",
    "ball": "passed back and forth: taking turns",
    "mirror": f"shows {_NAME} its own trace beside another's: recognizing itself (rung 9)",
    "blocks": f"pieces {_NAME} can put together and take apart: changing itself (rung 10)",
    "rattle": f"makes a sound when {_NAME} shakes it: its first voice (rung 11, speak)",
}
UNLOCK_IDS = {k: _SL.RUNG_ID.get(n) for k, n in UNLOCKS.items()}   # #295: kind -> the achievement (None: birth)
PLACEHOLDERS = ("mirror", "blocks", "rattle")   # locked kinds with no mechanics yet
NEXT_LOCKED = 2             # the dock shows this many locked toys (the next ones), so it stays tidy on a phone
LATE_S = 300.0              # an unlock written this long after its pass is a catch-up, not news
LOCKED_PREFIX = "locked:"   # the dock's name for a locked toy (never a toybox name)


def achievement_of(kind: str) -> str | None:
    """#295: the achievement whose pass unlocks a kind (None: birth)."""
    return UNLOCK_IDS.get(str(kind or "").strip().lower())


def _ach_name(n: int) -> str:
    i = _SL.RUNG_ID.get(n)
    return _SL.ACHIEVEMENTS[i]["name"] if i else _SL.RUNG_NAMES.get(n, "")


def rung_of(kind: str) -> int:
    """The rung whose pass unlocks a kind (a kind not in the table: birth)."""
    return int(UNLOCKS.get(str(kind or "").strip().lower(), 0))


def unlocked(passed) -> list[str]:
    """The kinds unlocked by the passed rungs (a set, as self_loop._passed
    gives it; an int is a level: every rung up to it), by rung then table
    order."""
    if isinstance(passed, int):
        passed = set(range(passed + 1))
    got = {0} | set(passed or ())
    return [k for k in sorted(UNLOCKS, key=lambda k: (UNLOCKS[k], list(UNLOCKS).index(k)))
            if UNLOCKS[k] in got]


def _recs(srv) -> list[dict]:
    from . import self_loop as SL
    if srv is None or SL.record_path(srv) is None:
        return []
    with SL._lock:
        return list(SL._read(srv))


def born(recs: list[dict]) -> bool:
    """Self is born (a birth line, or a rung already on the record)."""
    return any(r.get("kind") in ("birth", "rung") for r in recs)


def passed(srv) -> set:
    return _opened(_recs(srv))


def _opened(recs: list[dict]) -> set:
    """#299: the rungs whose achievement is earned AND accepted (and 0, birth)
    -- what unlocks toys; self_loop._passed is the earned ones alone."""
    from . import self_loop as SL
    return {0} | {SL.ACHIEVEMENTS[i]["rung"] for i in SL._accepted(recs)}


def _accept_ts(recs: list[dict]) -> dict:
    """rung -> when the operator's accept of its achievement landed (the
    migration is no accept: a migrated achievement's news is its pass)."""
    from . import self_loop as SL
    out: dict = {}
    for r in recs:
        n = SL.ACHIEVEMENTS.get(r.get("id"), {}).get("rung") if r.get("kind") == "accept" else None
        if n is not None:
            out[n] = max(out.get(n, 0.0), float(r.get("ts") or 0))
    return out


def refusal(srv, kind: str) -> str | None:
    """Why a kind can't be added right now (toybox.add), else None."""
    from . import self_loop as SL
    kind = str(kind or "").strip().lower()
    recs = _recs(srv)
    n = rung_of(kind)
    if born(recs) and kind in UNLOCKS and n not in _opened(recs):
        from . import self_name as _SN                 # #283: said with this seat's name
        if n in SL._passed(recs):                      # #299: earned, waiting for the operator's accept
            return _SN.say(f"{kind} {ICONS.get(kind, '')} is locked: {_NAME} earned '{_ach_name(n)}', and it "
                           f"unlocks once the operator accepts that -- {TEACHES.get(kind, '')}").replace("  ", " ")
        return _SN.say(f"{kind} {ICONS.get(kind, '')} is locked: it unlocks when {_NAME} earns "
                       f"'{_ach_name(n)}' and the operator accepts it -- {TEACHES.get(kind, '')}").replace("  ", " ")
    if kind in PLACEHOLDERS:
        return (f"{kind} {ICONS[kind]} is unlocked but not built yet: its mechanics come with "
                f"'{_ach_name(n + 1)}'")
    return None


def poke_refusal(name: str) -> str | None:
    """A poke on a placeholder's name (toy:mirror, locked:mirror, mirror)."""
    n = str(name or "").strip().lower()
    for pre in (LOCKED_PREFIX, "toy:"):
        if n.startswith(pre):
            n = n[len(pre):]
    if n in PLACEHOLDERS:
        return f"{n} {ICONS[n]} is a placeholder, not built yet: it takes no pokes"
    if str(name or "").startswith(LOCKED_PREFIX):
        return f"{n} is locked: it takes no pokes"
    return None


def _same_dir(srv) -> bool:
    """Provision only into this seat's own toybox: the record and the
    toybox live in the same data dir (a test seat or a second seat
    elsewhere never writes another's toys)."""
    try:
        from .settings import data_dir
        mp = getattr(srv, "_models_path", None)
        return bool(mp) and Path(mp).parent.resolve() == data_dir().resolve()
    except Exception:
        return False


def provision(srv, kinds: list[str], now: float | None = None) -> list[str]:
    """Switch on each unlocked built-in kind the seat has none of (any name).
    -> the names added. Never raises."""
    from . import toybox as TB
    if getattr(srv, "entity_store", None) is None or not _same_dir(srv):
        return []
    try:
        have = {t["kind"] for t in TB.list_toys()}
    except Exception:
        have = set()
    added = []
    for k in kinds:
        if k not in TB.KINDS or k in have:
            continue                         # a placeholder (or a kind not in this kit), or already there
        try:
            out = TB.add(srv, k, now=now)
        except Exception as e:                  # noqa: BLE001
            out = {"ok": False, "error": repr(e)}
        if out.get("ok"):
            added.append(out["name"])
            have.add(k)
        else:
            print(f"[toy_unlocks] {k} not provisioned ({out.get('error')})", file=sys.stderr)
    if added:
        try:
            srv.entity_store.flush()
        except Exception:
            pass
    return added


def sync(srv, now: float | None = None) -> list[dict]:
    """Write each unlock line not on the record yet (one per rung passed that
    unlocks something) and provision its toys. -> the lines written.
    Never raises (the loop calls it every cycle)."""
    try:
        return _sync(srv, now)
    except Exception as e:                      # noqa: BLE001
        print(f"[toy_unlocks] sync failed ({e!r})", file=sys.stderr)
        return []


def _built_late(srv, recs: list[dict], now: float) -> list[dict]:
    """A kind whose unlock line was written before it was BUILT (#264: the
    ball, unlocked at rung 6 by #267 as a name only) is provisioned once,
    the first time it is built: one `unlock_built` line per kind, so it is
    never brought back after the operator removes it."""
    from . import self_loop as SL
    from . import toybox as TB
    seen = set()
    for r in recs:
        if r.get("kind") == "unlock_built":
            seen.update(r.get("toys") or [])
    out = []
    for r in recs:
        if r.get("kind") != "unlock":
            continue
        added = [str(a) for a in r.get("added") or []]
        for k in r.get("toys") or []:
            if k not in TB.KINDS or k in seen or any(a == f"toy:{k}" for a in added):
                continue
            got = provision(srv, [k], now=now)
            line = {"ts": now, "kind": "unlock_built", "rung": r.get("rung"), "toys": [k]}
            if got:
                line["added"] = got
            SL._append(srv, line)
            seen.add(k)
            out.append(line)
    return out


def _sync(srv, now):
    from . import self_loop as SL
    recs = _recs(srv)
    if not born(recs):
        return []
    now = time.time() if now is None else float(now)
    late = _built_late(srv, recs, now)
    if late:
        recs = _recs(srv)
    return late + _sync_new(srv, recs, now)


def _sync_new(srv, recs, now):
    from . import self_loop as SL
    done = {r.get("rung") for r in recs if r.get("kind") == "unlock"}
    passed_ = _opened(recs)                     # #299: earned and accepted
    if all(UNLOCKS[k] in done for k in unlocked(passed_) if UNLOCKS[k] > 0):
        return []
    at = {r["rung"]: float(r.get("ts") or 0) for r in SL._rung_lines(recs)}
    for n, t in _accept_ts(recs).items():       # ...news from the later of the pass and the accept
        at[n] = max(at.get(n, t), t)
    out = []
    for n in sorted(passed_):
        toys = [k for k in unlocked({n}) if UNLOCKS[k] == n]
        if n == 0 or n in done or not toys:      # birth is no pass: its kinds are simply open
            continue
        line = {"ts": now, "kind": "unlock", "rung": n, "id": _SL.RUNG_ID.get(n), "toys": toys}
        if now - at.get(n, now) > LATE_S:
            line["late"] = True
        added = provision(srv, toys, now=now)
        if added:
            line["added"] = added
        SL._append(srv, line)
        out.append(line)
    return out


# ---- what the page reads ----------------------------------------------------------

def latest(recs: list[dict]) -> dict | None:
    """The newest unlock line, for the exp bar's level-up caption."""
    r = next((r for r in reversed(recs) if r.get("kind") == "unlock"), None)
    if r is None:
        return None
    return {"rung": r.get("rung"), "id": _SL.RUNG_ID.get(r.get("rung")), "ts": r.get("ts"), "late": bool(r.get("late")),
            "toys": [{"kind": k, "icon": ICONS.get(k, "")} for k in r.get("toys") or []]}


def locked_rows(recs: list[dict], have=(), cap: int = NEXT_LOCKED) -> list[dict]:
    """The dock's next `cap` locked toys (nothing is locked before birth; a
    kind the seat already has -- kept from before -- is in the dock as itself)."""
    from . import self_loop as SL
    if not born(recs):
        return []
    open_ = set(unlocked(_opened(recs))) | set(have or ())
    rows = [k for k in sorted(UNLOCKS, key=lambda k: (UNLOCKS[k], list(UNLOCKS).index(k))) if k not in open_]
    return [{"name": LOCKED_PREFIX + k, "kind": k, "locked": True, "icon": ICONS.get(k, ""),
             "unlock_rung": UNLOCKS[k], "unlock_id": UNLOCK_IDS[k], "rung_name": _ach_name(UNLOCKS[k]),
             "teaches": TEACHES.get(k, "")} for k in rows[:max(0, int(cap))]]


def grandfathered(recs: list[dict], kind: str) -> bool:
    """A toy kept from before its kind unlocked (no toy ever disappears)."""
    from . import self_loop as SL
    return born(recs) and kind in UNLOCKS and UNLOCKS[kind] not in _opened(recs)


def table(recs: list[dict], have: set) -> list[dict]:
    """The sheet's Toys section: every kind, locked or unlocked, when, and
    whether the seat has one."""
    from . import self_loop as SL
    from . import toybox as TB
    is_born = born(recs)
    open_ = set(unlocked(_opened(recs))) if is_born else set(UNLOCKS)
    when = {}
    for r in recs:
        if r.get("kind") == "unlock":
            for k in r.get("toys") or []:
                when.setdefault(k, r.get("ts"))
    out = []
    for k in sorted(UNLOCKS, key=lambda k: (UNLOCKS[k], list(UNLOCKS).index(k))):
        built = k in TB.KINDS
        state = ("unlocked" if k in open_ else "grandfathered" if k in have else "locked")
        out.append({"kind": k, "icon": ICONS.get(k, ""), "rung": UNLOCKS[k], "unlock_id": UNLOCK_IDS[k],
                    "rung_name": _ach_name(UNLOCKS[k]), "teaches": TEACHES.get(k, ""),
                    "state": state, "built": built, "have": k in have,
                    "at": when.get(k)})
    return out


def by_rung(recs: list[dict]) -> dict:
    """{rung: [kinds]} from the unlock lines (the achievements' marks)."""
    out: dict = {}
    for r in recs:
        if r.get("kind") == "unlock" and r.get("rung") not in out:
            out[r.get("rung")] = list(r.get("toys") or [])
    return out
