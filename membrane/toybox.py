"""THE TOYBOX (#239, part of #161): self's toys as tested, built-in kit.

The seat's model used to write every toy for self as an ad-hoc entity script,
and almost each one broke in a new way (state in /tmp, a relay that misparsed
block ids, a getter that dumped the whole entity, a die whose getter clobbered
its own tick state, scripts refused at the length cap). Toys are lab apparatus
for the ladder: they must be correct, replayable and honest about causation.
So the toys ship with the seat; the model and the operator only switch them on
or off and set their parameters -- nobody writes toy code.

EVERY TOY
  - is an entity whose getter (and tick, for a toy with a clock of its own) is
    a two-line stub into this module (STUB_GET / STUB_TICK);
  - answers a read with a MINIMAL reading -- its own state only, never the
    seed, the log or who caused what;
  - keeps its state in <data dir>/toybox/<name>.json (atomic writes; survives
    a restart) and its change log in <name>.log.jsonl (append-only);
  - draws from a SEEDED rng (the seed param, by default derived from its name;
    draw k is random.Random(f"{seed}:{k}")), so a run replays;
  - flags every change of its state with who caused it: `by: self` (self's own
    look or reach -- the getter is told the reader, entity_read_back's `by`),
    `by: world` (its own clock or randomness), `by: model` (the seat's model,
    through the toybox object's poke) or `by: <person>` (the operator at the
    CLI, `operator`; a person on the page, by their name or token -- #244).
    Rung 6's body/other split can be checked against that ground truth, and
    self's reach record carries it (`by` on each reach of a toy: who changed
    it since self's last look -- never in the reading self predicts from);
  - has a spot in the field from the moment it is switched on (place(): its
    summary embedded by the seat's embedder; quietly none without one).

PLAY (#244): others act on the toys -- poke(name, action, arg, by):
    tower  knock <k> (k and every standing block above it fall)
           rebuild <k> (block k alone stands again)
    die    roll (a fresh face)
    drift  nudge [up|down] (one step, bouncing off a wall; no direction: a coin)
    burst  nudge (a fresh value)
    ball   roll (back to self: only while it is on your side)
    body   nothing: it stays self-only, the control
fast and slow take no pokes either (their clock is the point). A poke that
changes nothing (knock a fallen block, rebuild a standing one, roll a ball
self already has) is refused.

THE KIT (KINDS): fast / slow / burst / drift / die change on their own clock
(the thinker's tick; a read never changes them), body changes only when self
looks at it, and the tower changes only when self reaches one of its blocks.

THE BALL (#264): rung 7's turn toy -- serve and return with an object, before
words. Its state is whose side it is on (`self` / `other`), its last mover
(`by`) and when (`at`), and the last PERSON who rolled it (`other_by`,
`other_at`: a reply's ground truth). Self rolls it to the other on its turn
(roll_self, `by: self` -- the only way that tag lands on it); a person rolls
it back (poke); the model may roll it too (`by: model`), which is never the
other's turn. It has no clock, and a look never moves it. Exactly one shared
ball (BALL) is provisioned once, by toy_unlocks when rung 6 passes (#267). The
other's last act -- a person's poke on ANY toy -- is kept in one small file
(last_person_poke): what rung 7's timing reads.

THE MIRROR (#312): rung 9's apparatus -- recognize itself. Self feeds it its
own move each cycle (mirror_feed: one scalar, self_loop._mirror_move), and
it has two sides: the GLASS shows the move from `delay` feeds back, the
STRANGER self's own move from `lag` (MIRROR_LAG) feeds back -- the same
statistics, nothing to do with what self does now -- each plus seeded
gaussian jitter (SD `jitter`). Its state keeps the moves it needs (a restart
keeps them); its log flags the glass's change `by: self` and the stranger's
`by: world`. It has no getter (self's ordinary looks never read it: it sees
it by feeding it), no clock, and takes no pokes yet.

THE SURFACE: `membrane toybox list|add|remove|reset|migrate|poke` (cli_main),
the `toybox` object's methods list / add(spec) / remove(name) / reset(name) /
poke(toy, action) (the `toybox::` no-shell dialect, night.run_command_entity),
and POST /membrane/toybox/poke (server.py: the 👶 panel's play buttons).
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import random
import re
import shlex
import sys
import threading
import time
import zlib
from pathlib import Path

ENT = "toybox"
STUB_GET = "from membrane import toybox as _tb\nresult = _tb.getter(state)"
STUB_TICK = "from membrane import toybox as _tb\nresult = _tb.ticker(state)"

KINDS = {
    "fast": {"about": "changes on its own clock faster than self can look: every look finds it changed, none of it self's doing",
             "params": {"every": 5.0}, "clock": True},
    "slow": {"about": "changes once a period (an hour by default): long runs of identical looks",
             "params": {"period": 3600.0}, "clock": True},
    "burst": {"about": "bursty: some windows are active and it changes every few seconds inside them, the rest it sits still",
              "params": {"window": 600.0, "active": 0.35, "every": 45.0}, "clock": True},
    "drift": {"about": "a bounded random walk that steps at random intervals of its own, whether anyone looks or not",
              "params": {"min_s": 45.0, "max_s": 600.0, "step": 5, "lo": 0, "hi": 100}, "clock": True},
    "die": {"about": "a memoryless change: probability p per second, on its own clock, never on a read",
            "params": {"p": 1 / 60, "faces": 6}, "clock": True},
    "body": {"about": "changes only because self looked at it -- the body/other test case",
             "params": {}, "clock": False},
    "tower": {"about": "n blocks: self topples a block (and the ones above it) by reaching it, and lifts a fallen one by reaching it; only self's reaches change it",
              "params": {"n": 5}, "clock": False},
    "ball": {"about": "a ball self and the other roll back and forth (rung 7's turns, #264): self rolls it to "
                      "you on its turn; roll it back when it is on your side",
             "params": {}, "clock": False},
    "mirror": {"about": "self's mirror (rung 9, #312): the glass shows self's own moves a moment late, and a "
                        "stranger beside it shows self's moves from half an hour ago -- the same moves, only "
                        "the glass follows what self does now",
               "params": {"delay": 1, "lag": 187, "jitter": 0.1}, "clock": False},
}
MIRROR_LAG = KINDS["mirror"]["params"]["lag"]   # #312: the stranger's replay, feeds back (self_loop.MIRROR_LAG)
ACTIONS = {"tower": ("knock", "rebuild"), "die": ("roll",), "drift": ("nudge",), "burst": ("nudge",),
           "ball": ("roll",)}
RESERVED_BY = ("self", "world")         # only the toy itself and self's own looks write these
NOT_PEOPLE = RESERVED_BY + ("model",)   # #264: every other tag is a person's
BALL = "toy:ball"                       # #264: the one shared ball, provisioned once
LAST_PERSON = ".last-person.json"       # #264: the other's last act, on any toy
MIGRATE_MAP = {"w:fast": "fast", "w:slow": "slow", "w:burst": "burst", "w:drift": "drift",
               "w:body": "body", "toy:die": "die"}
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,59}$")
TOWER_MAX = 20

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_SEAT = {"srv": None}


# ---- where it lives ------------------------------------------------------------

def root() -> Path:
    from .settings import data_dir
    return data_dir() / "toybox"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _path(name: str) -> Path:
    return root() / f"{_safe(name)}.json"


def _log_path(name: str) -> Path:
    return root() / f"{_safe(name)}.log.jsonl"


def _forget() -> None:
    """Nothing is kept in memory between calls (the files are the toy); the
    per-toy locks are dropped -- what a process restart does."""
    with _locks_guard:
        _locks.clear()


@contextlib.contextmanager
def _locked(name: str):
    """One writer per toy: a thread lock, and a file lock so the CLI's reset
    and the seat's tick never interleave."""
    with _locks_guard:
        lk = _locks.setdefault(name, threading.Lock())
    with lk:
        root().mkdir(parents=True, exist_ok=True)
        fh = open(root() / f".{_safe(name)}.lock", "a")
        try:
            try:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            yield
        finally:
            fh.close()


def _load(name: str) -> dict:
    try:
        return json.loads(_path(name).read_text())
    except FileNotFoundError:
        raise KeyError(f"no toy named {name!r} (membrane toybox list)") from None


def _save(st: dict) -> None:
    p = _path(st["name"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(st, sort_keys=True))
    os.replace(tmp, p)


def _note(st: dict, ts: float, by: str, what: str, **kw) -> None:
    """One change, flagged with who caused it (self | world | model |
    <person>), on the log."""
    st["counts"][by] = int(st["counts"].get(by, 0)) + 1
    rec = {"ts": ts, "by": by, "what": what, **kw}
    with open(_log_path(st["name"]), "a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    if is_person(by):
        _mark_person(ts, by, st["name"])


def is_person(by) -> bool:
    """#264: a tag that is a person's -- anyone but self, the world and the model."""
    return bool(by) and str(by) not in NOT_PEOPLE


_person_guard = threading.Lock()


def _mark_person(ts: float, by: str, name: str) -> None:
    """Keep the latest person's poke on any toy (never an older one)."""
    with _person_guard:
        cur = last_person_poke()
        if cur is not None and float(cur["ts"]) > float(ts):
            return
        p = root() / LAST_PERSON
        tmp = p.with_name(p.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"ts": float(ts), "by": by, "toy": name}, sort_keys=True))
        os.replace(tmp, p)


def last_person_poke() -> dict | None:
    """#264: the other's last act -- the latest poke on any toy by a person
    ({ts, by, toy}); None before any. What rung 7's timing reads."""
    try:
        d = json.loads((root() / LAST_PERSON).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("ts"), (int, float)) else None


def _rng(st: dict) -> random.Random:
    r = random.Random(f"{st['seed']}:{st['draws']}")
    st["draws"] += 1
    return r


def _other(st: dict, lo: int, hi: int) -> int:
    """A fresh value in lo..hi, never the current one (a change is a change)."""
    cur = st.get("value")
    v = _rng(st).randint(lo, hi - 1)
    return v + 1 if cur is not None and v >= cur else v


# ---- params ----------------------------------------------------------------------

def default_seed(name: str) -> int:
    return zlib.crc32(name.encode()) & 0x7FFFFFFF


def _params(kind: str, given: dict | None) -> tuple[dict, int | None]:
    """The kind's params with the given ones over them, typed and checked.
    -> (params, seed). ValueError names what is wrong."""
    spec = KINDS[kind]["params"]
    given = dict(given or {})
    seed = given.pop("seed", None)
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            raise ValueError(f"seed must be an integer, not {seed!r}") from None
    bad = sorted(k for k in given if k not in spec)
    if bad:
        takes = ", ".join(sorted(spec)) or "no params"
        raise ValueError(f"{kind} takes {takes} (and seed); not {', '.join(bad)}")
    out = dict(spec)
    for k, v in given.items():
        try:
            out[k] = int(v) if isinstance(spec[k], int) and not isinstance(spec[k], bool) else float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a number, not {v!r}") from None
    checks = {
        "every": lambda v: v > 0, "period": lambda v: v > 0, "window": lambda v: v > 0,
        "active": lambda v: 0 <= v <= 1, "min_s": lambda v: v > 0, "step": lambda v: v > 0,
        "p": lambda v: 0 < v <= 1, "faces": lambda v: 2 <= v <= 1000, "n": lambda v: 1 <= v <= TOWER_MAX,
        "delay": lambda v: 1 <= v <= 10, "lag": lambda v: 10 <= v <= 10000, "jitter": lambda v: 0 <= v <= 10,
    }
    for k, ok in checks.items():
        if k in out and not ok(out[k]):
            raise ValueError(f"{k}={out[k]} is out of range")
    if kind == "drift":
        if out["max_s"] < out["min_s"]:
            raise ValueError("max_s must be at least min_s")
        if out["hi"] - out["lo"] < 2 * out["step"]:
            raise ValueError("lo..hi must hold at least two steps")
    return out, seed


# ---- the dynamics ------------------------------------------------------------------

def _die_wait(st: dict) -> float:
    """A memoryless wait: exponential at the rate whose chance of a change
    within one second is p."""
    p = float(st["params"]["p"])
    if p >= 1.0:
        return 1.0
    return _rng(st).expovariate(-math.log1p(-p))


def create(kind: str, name: str, params: dict | None = None, now: float | None = None) -> dict:
    """A fresh toy's state, written (any old one is replaced: reset archives
    first, add refuses a taken name)."""
    if kind not in KINDS:
        raise ValueError(f"no toy kind {kind!r}; the kit: {', '.join(KINDS)}")
    if not NAME_RX.match(name or ""):
        raise ValueError(f"name {name!r}: lowercase letters, digits and : _ . - (up to 60)")
    prm, seed = _params(kind, params)
    now = time.time() if now is None else float(now)
    st = {"v": 1, "name": name, "kind": kind, "params": prm,
          "seed": default_seed(name) if seed is None else seed,
          "created": now, "draws": 0, "counts": {"self": 0, "world": 0}}
    if kind in ("fast", "slow", "burst", "body"):
        st["value"] = _other(st, 0, 99)
    if kind == "fast":
        st["next_at"] = now + prm["every"]
    elif kind == "slow":
        st["next_at"] = now + prm["period"]
    elif kind == "burst":
        st["next_at"] = now + prm["every"]
    elif kind == "drift":
        st["value"] = (prm["lo"] + prm["hi"]) // 2
        st["next_at"] = now + _rng(st).uniform(prm["min_s"], prm["max_s"])
    elif kind == "die":
        st["value"] = _rng(st).randint(1, prm["faces"])
        st["next_at"] = now + _die_wait(st)
    elif kind == "tower":
        st["blocks"] = [{"standing": True, "by": None, "at": None, "falls": 0} for _ in range(prm["n"])]
    elif kind == "ball":
        st.update(side="self", by=None, at=None, other_by=None, other_at=None)   # self serves first
    elif kind == "mirror":
        st.update(moves=[], glass=None, stranger=None)
    with _locked(name):
        _save(st)
        lp = _log_path(name)
        if lp.exists():
            lp.unlink()                  # a fresh toy starts a fresh log (reset archived the old one)
    return st


def _reading(st: dict, block: int | None = None) -> dict:
    k = st["kind"]
    if k == "tower":
        if block is None:
            return {"standing": sum(1 for b in st["blocks"] if b["standing"])}
        return {"standing": bool(st["blocks"][block - 1]["standing"])}
    if k == "die":
        return {"face": st["value"]}
    if k == "ball":
        return {"side": st["side"]}
    if k == "mirror":
        return {"glass": st.get("glass"), "stranger": st.get("stranger")}
    return {"value": st["value"]}


def read(name: str, by: str = "", now: float | None = None, block: int | None = None) -> dict:
    """One look at a toy (its getter): the minimal reading, after whatever
    the look itself does -- body changes on self's look, a tower block is
    toppled or lifted by self's reach; every other look changes nothing."""
    now = time.time() if now is None else float(now)
    mine = by == "self"
    with _locked(name):
        st = _load(name)
        k = st["kind"]
        if k == "tower" and block is not None:
            n = len(st["blocks"])
            if not 1 <= int(block) <= n:
                raise ValueError(f"{name} has blocks 1..{n}, not {block}")
            block = int(block)
        if mine and k == "body":
            st["value"] = _other(st, 0, 99)
            _note(st, now, "self", "change", value=st["value"])
            _save(st)
        elif mine and k == "tower" and block is not None:
            bl = st["blocks"]
            if bl[block - 1]["standing"]:
                fell = [i + 1 for i in range(block - 1, len(bl)) if bl[i]["standing"]]
                for i in fell:
                    bl[i - 1].update(standing=False, by="self", at=now, falls=int(bl[i - 1]["falls"]) + 1)
                _note(st, now, "self", "topple", block=block, blocks=fell)
            else:
                bl[block - 1].update(standing=True, by="self", at=now)
                _note(st, now, "self", "lift", block=block)
            _save(st)
        return _reading(st, block)


def who(by) -> str:
    """A poker's tag: the person's name as given (or `model`); '' is the
    operator (chat_io's convention); a name that would pass for the toy's own
    tags (self / world) is kept apart as person:<name>."""
    b = " ".join(str(by or "").split())[:60]
    if not b:
        return "operator"
    return f"person:{b}" if b.lower() in RESERVED_BY else b


def _resolve(name: str, arg):
    """`toy:tower:b3` names block 3 of toy:tower (when there's no toy by that name)."""
    name = str(name or "").strip().lower()
    if not _path(name).exists():
        m = re.match(r"^(.+):b(\d+)$", name)
        if m and _path(m.group(1)).exists() and arg in (None, ""):
            return m.group(1), m.group(2)
    return name, arg


def poke(name: str, action: str, arg=None, by: str = "", now: float | None = None) -> dict:
    """PLAY (#244): someone other than self acts on a toy -- tagged `by`
    (who(): the model, the operator, a person). -> {ok, name, action, by,
    reading} | {ok: False, error}."""
    now = time.time() if now is None else float(now)
    by = who(by)
    action = str(action or "").strip().lower()
    name, arg = _resolve(name, arg)
    arg = None if arg is None or str(arg).strip() == "" else str(arg).strip().lower()
    if not _path(name).exists():
        from . import toy_unlocks as _TU                 # #267: a placeholder or a locked toy
        return {"ok": False, "error": _TU.poke_refusal(name) or f"no toy named {name!r} (membrane toybox list)"}
    with _locked(name):
        st = _load(name)
        k = st["kind"]
        acts = ACTIONS.get(k, ())
        if not acts:
            why = ("body changes only on self's own look -- it is self's, the control" if k == "body" else
                   "the mirror shows self's own moves (and a stranger's beside them); it takes no pokes"
                   if k == "mirror" else f"{k} runs on its own clock; it takes no pokes")
            return {"ok": False, "error": f"{name}: {why}"}
        if action not in acts:
            return {"ok": False, "error": f"{name} ({k}) answers to {' / '.join(acts)}, not {action!r}"}
        block = None
        if k == "tower":
            n = len(st["blocks"])
            try:
                block = int(arg)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{action} which block? {name} has blocks 1..{n}"}
            if not 1 <= block <= n:
                return {"ok": False, "error": f"{name} has blocks 1..{n}, not {block}"}
            bl = st["blocks"]
            if action == "knock":
                if not bl[block - 1]["standing"]:
                    return {"ok": False, "error": f"block {block} of {name} is already down"}
                fell = [i + 1 for i in range(block - 1, n) if bl[i]["standing"]]
                for i in fell:
                    bl[i - 1].update(standing=False, by=by, at=now, falls=int(bl[i - 1]["falls"]) + 1)
                _note(st, now, by, "topple", block=block, blocks=fell)
            else:
                if bl[block - 1]["standing"]:
                    return {"ok": False, "error": f"block {block} of {name} is standing"}
                bl[block - 1].update(standing=True, by=by, at=now)
                _note(st, now, by, "lift", block=block)
        elif k == "ball":
            if st["side"] == "self":
                return {"ok": False, "error": f"{name} is on self's side: it rolls it to you on its turn"}
            st.update(side="self", by=by, at=now)
            if is_person(by):                                # the other's turn (a reply's ground truth)
                st.update(other_by=by, other_at=now)
            _note(st, now, by, "roll", side="self")
        elif k == "die":
            cur, faces = st["value"], int(st["params"]["faces"])
            v = _rng(st).randint(1, faces - 1)
            st["value"] = v + 1 if v >= cur else v
            _note(st, now, by, "change", act="roll", value=st["value"])
        elif k == "drift":
            prm = st["params"]
            if arg in ("up", "+", "1", "+1"):
                step = prm["step"]
            elif arg in ("down", "-", "-1"):
                step = -prm["step"]
            elif arg is None:
                step = prm["step"] if _rng(st).random() < 0.5 else -prm["step"]
            else:
                return {"ok": False, "error": f"nudge {name} up or down, not {arg!r}"}
            v = st["value"] + step
            if not prm["lo"] <= v <= prm["hi"]:
                v = st["value"] - step                       # a wall: it bounces
            st["value"] = v
            _note(st, now, by, "change", act="nudge", value=v)
        else:                                                # burst
            st["value"] = _other(st, 0, 99)
            _note(st, now, by, "change", act="nudge", value=st["value"])
        _save(st)
        out = {"ok": True, "name": name, "action": action, "by": by,
               **({"block": block} if block else {}), "reading": _reading(st, block)}
    if is_person(by):
        _pull_field(name, block, by)
    return out


def _pull_field(name: str, block: int | None, by: str) -> None:
    """PLAYING COUNTS AS SPEAKING (#282): a person's poke pulls the field's
    current toward the toy they played with (its spot; a tower's block's own),
    under their own session (toy:<person>), one event's worth -- as a chat
    line would (efield.pull_current). Only a person's: self's rolls, the
    world's changes and the model's pokes never come here. Never raises."""
    srv = _SEAT.get("srv")
    fld = getattr(srv, "efield", None) if srv is not None else None
    if fld is None or not hasattr(fld, "pull_current"):
        return
    try:
        from .eids import field_id
        from .entities import CLASS_COMPONENT, entity_id
        eid = entity_id(CLASS_COMPONENT, f"{name}:b{block}" if block else name)
        st = fld.get(field_id(eid))
        if st is None or st.state is None:
            place(srv, eid)                          # a toy with no spot yet gets one
            st = fld.get(field_id(eid))
        if st is None or st.state is None:
            return
        fld.pull_current(f"toy:{by}", st.state)
    except Exception as e:                      # noqa: BLE001
        print(f"[toybox] {name}: the poke did not pull the field ({e!r})", file=sys.stderr)


def roll_self(name: str, now: float | None = None) -> dict:
    """SELF'S TURN (#264): self rolls the ball to the other -- the one way
    `by: self` lands on a ball (a poke tags a would-be `self` as a person).
    Never refused for being on the other's side: an unreturned ball is
    fetched and rolled again. -> {ok, name, by, reading} | {ok: False, error}."""
    now = time.time() if now is None else float(now)
    name = str(name or "").strip().lower()
    if not _path(name).exists():
        return {"ok": False, "error": f"no toy named {name!r}"}
    with _locked(name):
        st = _load(name)
        if st["kind"] != "ball":
            return {"ok": False, "error": f"{name} is a {st['kind']}, not a ball"}
        st.update(side="other", by="self", at=now)
        _note(st, now, "self", "roll", side="other")
        _save(st)
        return {"ok": True, "name": name, "by": "self", "reading": _reading(st)}


def mirror_feed(name: str, move: float, now: float | None = None) -> dict:
    """#312: self feeds its mirror this cycle's move (one scalar); the glass
    then shows the move from `delay` feeds back, the stranger the one from
    `lag` back, each with seeded jitter (None until it has one that old).
    Self's loop is the only caller. -> {ok, name, glass, stranger} | {ok: False, error}."""
    now = time.time() if now is None else float(now)
    name = str(name or "").strip().lower()
    if not _path(name).exists():
        return {"ok": False, "error": f"no toy named {name!r}"}
    with _locked(name):
        st = _load(name)
        if st["kind"] != "mirror":
            return {"ok": False, "error": f"{name} is a {st['kind']}, not a mirror"}
        prm = st["params"]
        d, lag, j = int(prm["delay"]), int(prm["lag"]), float(prm["jitter"])
        moves = (list(st.get("moves") or []) + [float(move)])[-(d + lag + 1):]
        st["moves"] = moves
        for side, back, by in (("glass", d, "self"), ("stranger", d + lag, "world")):
            if len(moves) > back:
                v = round(moves[-1 - back] + j * _rng(st).gauss(0.0, 1.0), 4)
                st[side] = v
                _note(st, now, by, "show", side=side, value=v)
            else:
                st[side] = None
        _save(st)
        return {"ok": True, "name": name, **_reading(st)}


def log_len(name: str) -> int:
    """How many changes the toy's log holds (self_loop marks its looks by it)."""
    try:
        with open(_log_path(name)) as fh:
            return sum(1 for ln in fh if ln.strip())
    except OSError:
        return 0


def causes(name: str, start: int, end: int, block: int | None = None) -> list[str]:
    """Who changed the toy (or its block) between log lines start..end --
    the ground truth self's reach record keeps (sorted, distinct)."""
    out = set()
    try:
        lines = [ln for ln in _log_path(name).read_text().splitlines() if ln.strip()]
    except OSError:
        return []
    for ln in lines[max(0, int(start)):max(0, int(end))]:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if block is not None and e.get("block") != block and block not in (e.get("blocks") or []):
            continue
        out.add(str(e.get("by") or "?"))
    return sorted(out)


def tick(name: str, now: float | None = None) -> bool:
    """The toy's own clock (the thinker's heartbeat): True when it changed.
    Every change here is the world's."""
    now = time.time() if now is None else float(now)
    with _locked(name):
        st = _load(name)
        k, prm = st["kind"], st["params"]
        changed = False
        if k in ("fast", "slow"):
            every = prm["every"] if k == "fast" else prm["period"]
            if now >= st["next_at"]:
                st["value"] = _other(st, 0, 99)
                st["next_at"] += every * (math.floor((now - st["next_at"]) / every) + 1)
                changed = True
        elif k == "burst":
            if burst_live(st["seed"], prm, now) and now >= st["next_at"]:
                st["value"] = _other(st, 0, 99)
                st["next_at"] = now + prm["every"]
                changed = True
        elif k == "drift":
            if now >= st["next_at"]:
                step = prm["step"] if _rng(st).random() < 0.5 else -prm["step"]
                v = st["value"] + step
                if not prm["lo"] <= v <= prm["hi"]:
                    v = st["value"] - step               # a wall: it bounces
                st["value"] = v
                st["next_at"] = now + _rng(st).uniform(prm["min_s"], prm["max_s"])
                changed = True
        elif k == "die":
            if now >= st["next_at"]:
                cur = st["value"]
                v = _rng(st).randint(1, prm["faces"] - 1)
                st["value"] = v + 1 if v >= cur else v
                st["next_at"] = now + _die_wait(st)
                changed = True
        if changed:
            _note(st, now, "world", "change", value=st["value"])
            _save(st)
        return changed


def burst_live(seed, prm: dict, now: float) -> bool:
    """A burst toy's window at `now` is active (it changes inside it) or
    quiet -- the same seeded draw its clock uses (#249: the toy dock)."""
    w = math.floor(float(now) / float(prm["window"]))
    return random.Random(f"{seed}:w{w}").random() < float(prm["active"])


def last_change(name: str) -> dict | None:
    """The toy's latest change from its log ({ts, by, what, ...}), reading only
    the log's tail (a fast toy's log grows all day). None before any change."""
    try:
        with open(_log_path(name), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    for ln in reversed(tail.splitlines()):
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict) and "ts" in rec:
            return rec
    return None


def peek(name: str) -> dict | None:
    """The toy's state without its log (cheap; None when there's no such toy)."""
    try:
        return _load(str(name or "").strip().lower())
    except (KeyError, ValueError, OSError):
        return None


def state(name: str) -> dict:
    """The toy's full state with its change log (the ground truth -- never
    what self reads)."""
    st = _load(name)
    log = []
    lp = _log_path(name)
    if lp.exists():
        for ln in lp.read_text().splitlines():
            try:
                log.append(json.loads(ln))
            except ValueError:
                pass
    st["log"] = log
    return st


# ---- the entity side: the stubs' targets ------------------------------------------

def getter(sstate: dict) -> dict:
    """STUB_GET's body: the reader comes from entity_read_back (`by`)."""
    tb = (sstate.get("properties") or {}).get("toybox") or {}
    by = str((sstate.get("extra") or {}).get("by") or "")
    return {"state": read(tb["name"], by=by, now=sstate.get("now"), block=tb.get("block"))}


def ticker(sstate: dict) -> dict:
    """STUB_TICK's body: the toy's clock; nothing to apply on the entity."""
    tb = (sstate.get("properties") or {}).get("toybox") or {}
    tick(tb["name"], now=sstate.get("now"))
    return {}


def _ents(est, name: str) -> list:
    return [e for e in list((est.docs or {}).values())
            if ((e.properties or {}).get("toybox") or {}).get("name") == name]


def _put(est, name: str, kind: str, block: int | None = None, n: int = 0) -> str:
    from .entities import CLASS_COMPONENT, QuantumEntity, entity_id
    ename = f"{name}:b{block}" if block else name
    if kind == "tower":
        about = (f"block {block} of {n} of the tower {name} (1 is the bottom): reach it to topple it and the "
                 "blocks above it; reach a fallen one to lift it back" if block else
                 f"a tower of {n} blocks ({name}:b1..b{n}); reading it counts the standing ones")
    else:
        about = KINDS[kind]["about"]
    props = {"name": ename, "summary": f"toybox {kind}: {about}", "silent_birth": True,
             "toybox": {"kind": kind, "name": name, **({"block": block} if block else {})}}
    if kind != "mirror":                         # #312: self sees its mirror by feeding it, never by a look
        props["getter_script"] = STUB_GET
    if KINDS[kind]["clock"] and not block:
        props.update(tick_script=STUB_TICK, tick_every=1)
    eid = entity_id(CLASS_COMPONENT, ename)
    est.upsert(QuantumEntity(eid=eid, cls=CLASS_COMPONENT, properties=props), embedder=None)
    return eid


def place(srv, eid: str, text: str | None = None):
    """A spot in the field for an entity (#244): its summary (else its name)
    embedded by the seat's embedder, through the field's place(). True placed,
    False already there, None not possible here (no field, no embedder) --
    never raises."""
    fld = getattr(srv, "efield", None)
    fn = getattr(fld, "place", None)
    if fn is None or not eid:
        return None
    try:
        from .eids import field_id
        if text is None:
            est = getattr(srv, "entity_store", None)
            e = (est.docs or {}).get(eid) if est is not None else None
            p = (e.properties or {}) if e is not None else {}
            text = str(p.get("summary") or p.get("name") or _short(eid))
        return fn(field_id(eid), text)
    except Exception as e:                      # noqa: BLE001
        print(f"[toybox] no field spot for {eid} ({e!r})", file=sys.stderr)
        return None


def add(srv, kind: str, name: str | None = None, params: dict | None = None,
        now: float | None = None, locked_ok: bool = False) -> dict:
    """Switch a toy on: its state, then its entities (a tower: one per block too).
    A kind self has not unlocked yet is refused (#267, toy_unlocks; migrate,
    which only replaces toys the seat already has, passes locked_ok)."""
    from .entities import CLASS_COMPONENT, entity_id
    est = getattr(srv, "entity_store", None)
    if est is None:
        return {"ok": False, "error": "the entity layer is off"}
    kind = str(kind or "").strip().lower()
    from . import toy_unlocks as _TU
    why = None if locked_ok and kind not in _TU.PLACEHOLDERS else _TU.refusal(srv, kind)
    if why:
        return {"ok": False, "error": why}
    if kind not in KINDS:
        return {"ok": False, "error": f"no toy kind {kind!r}; the kit: {', '.join(KINDS)}"}
    name = str(name or f"toy:{kind}").strip().lower()
    if entity_id(CLASS_COMPONENT, name) in (est.docs or {}) or _ents(est, name) or _path(name).exists():
        return {"ok": False, "error": f"{name} is taken: --name another, or remove it first"}
    try:
        st = create(kind, name, params, now=now)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    eids = [_put(est, name, kind, n=st["params"].get("n", 0))]
    if kind == "tower":
        eids += [_put(est, name, kind, block=b, n=st["params"]["n"]) for b in range(1, st["params"]["n"] + 1)]
    for eid in eids:
        place(srv, eid)
    return {"ok": True, "name": name, "kind": kind, "params": st["params"], "seed": st["seed"], "eids": eids}


def _archive(name: str, why: str) -> None:
    """Keep a removed or reset toy's state and log (the ground truth is never deleted)."""
    d = root() / "archive"
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S") + f"-{why}"
    for p, ext in ((_path(name), ".json"), (_log_path(name), ".log.jsonl")):
        if p.exists():
            os.replace(p, d / f"{_safe(name)}-{stamp}{ext}")


def remove(srv, name: str) -> dict:
    name = str(name or "").strip().lower()
    est = getattr(srv, "entity_store", None)
    ents = _ents(est, name) if est is not None else []
    if not ents and not _path(name).exists():
        return {"ok": False, "error": f"no toy named {name!r}"}
    for e in ents:
        est.docs.pop(e.eid, None)
        coll = est._entities_collection() if hasattr(est, "_entities_collection") else None
        if coll is not None:
            try:
                coll.remove(e.eid)
            except Exception:
                pass
    if ents:
        est.save()
    with _locked(name):
        _archive(name, "removed")
    return {"ok": True, "name": name, "removed": [e.eid for e in ents]}


def reset(name: str, now: float | None = None) -> dict:
    """Back to the start (same kind, params and seed); the old run is archived."""
    name = str(name or "").strip().lower()
    try:
        st = _load(name)
    except KeyError as e:
        return {"ok": False, "error": str(e.args[0])}
    with _locked(name):
        _archive(name, "reset")
    st = create(st["kind"], name, dict(st["params"], seed=st["seed"]), now=now)
    return {"ok": True, "name": name, "kind": st["kind"]}


def list_toys() -> list[dict]:
    out = []
    d = root()
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        if p.name.startswith("."):
            continue                                         # the toybox's own marks (#264), not toys
        try:
            st = json.loads(p.read_text())
        except ValueError:
            continue
        if not isinstance(st, dict) or "kind" not in st:
            continue
        row = {"name": st["name"], "kind": st["kind"], "params": st["params"], "seed": st["seed"],
               "counts": st.get("counts") or {}}
        if st["kind"] == "tower":
            row["reading"] = "".join("|" if b["standing"] else "_" for b in st["blocks"])
        elif st["kind"] == "ball":
            row["reading"] = st.get("side")                  # #264: whose side it is on
        elif st["kind"] == "mirror":
            row["reading"] = {"glass": st.get("glass"), "stranger": st.get("stranger")}   # #312
        else:
            row["reading"] = st.get("value")
        out.append(row)
    return out


def _fmt(rows: list[dict]) -> str:
    if not rows:
        return "no toys (toybox add <kind>; the kit: " + ", ".join(KINDS) + ")"
    lines = []
    for r in rows:
        prm = " ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in r["params"].items())
        c = r["counts"]
        others = "".join(f" {k}={v}" for k, v in sorted(c.items()) if k not in RESERVED_BY)
        lines.append(f"{r['name']}  {r['kind']}  {prm}  seed={r['seed']}  now={r['reading']}  "
                     f"changes self={c.get('self', 0)} world={c.get('world', 0)}{others}")
    return "\n".join(lines)


def _parse_spec(tokens: list[str]) -> tuple[str, str | None, dict]:
    toks = [t for tok in tokens for t in str(tok).split()]
    if not toks:
        raise ValueError("add(spec): '<kind> [name=<name>] [param=value ...]'; the kit: " + ", ".join(KINDS))
    kind, name, params = toks[0], None, {}
    for t in toks[1:]:
        k, sep, v = t.partition("=")
        if not sep:
            raise ValueError(f"{t!r} is not param=value")
        if k == "name":
            name = v
        else:
            params[k] = v
    return kind, name, params


# ---- the toybox object (cliffhanger) ------------------------------------------------

METHODS = {"list": "toybox::list", "add": "toybox::add {{spec}}",
           "remove": "toybox::remove {{name}}", "reset": "toybox::reset {{name}}",
           "poke": "toybox::poke {{toy}} {{action}}"}


def _eid() -> str:
    from .entities import MEMBRANE_NS
    return MEMBRANE_NS + ENT


def bind(srv) -> None:
    """Boot: the toybox object and its methods (once)."""
    _SEAT["srv"] = srv
    est = getattr(srv, "entity_store", None)
    if est is None:
        return
    try:
        from .entities import CLASS_COMPONENT, QuantumEntity
        ent = est.docs.get(_eid())
        if ent is None:
            ent = QuantumEntity(eid=_eid(), cls=CLASS_COMPONENT, properties={
                "name": "toybox", "silent_birth": True,
                "summary": ("self's toys, built in (#239): add(spec='<kind> [name=..] [param=value ..]') "
                            "switches one on, list() shows them, remove(name) / reset(name); "
                            "poke(toy, action) plays with one as the model (#244: tower 'knock <k>' / "
                            "'rebuild <k>', die 'roll', drift / burst 'nudge', ball 'roll' -- a model's "
                            "roll is never the other's turn; body is self's alone). The kit: "
                            + ", ".join(KINDS) + ". Never write a toy script: add one here.")})
            est.upsert(ent, embedder=None)
        have = {(d.properties or {}).get("method_name") for d in list(est.docs.values())
                if ent.eid in ((d.properties or {}).get("method_of") or [])}
        from .night import method_define
        for meth, cmd in METHODS.items():
            if meth not in have:
                method_define(srv, est, None, None, on=ent.eid, method=meth, command=cmd,
                              read_only=(meth == "list"))
    except Exception as e:                      # noqa: BLE001
        print(f"[toybox] the toybox object not wired ({e!r})", file=sys.stderr)


def from_command(rest: str) -> dict:
    """`toybox::<method> <args>` (the no-shell dialect, night.run_command_entity)."""
    try:
        parts = shlex.split(rest)
    except ValueError:
        parts = rest.split()
    if not parts or parts[0] not in METHODS:
        return {"rc": 2, "text": f"toybox has no method {parts[0] if parts else ''!r}; it answers to "
                                 + ", ".join(METHODS)}
    srv = _SEAT["srv"]
    meth, args = parts[0], parts[1:]
    if meth == "poke":
        # THE MODEL'S HAND (#244): a method call carries no caller, and the
        # toybox object is the seat's model's surface (self never calls it --
        # its own reaches go through the getter), so every poke here is `model`
        toks = [t for a in args for t in str(a).split()]
        if len(toks) < 2:
            return {"rc": 2, "text": "poke(toy, action): e.g. toy='toy:tower' action='knock 3'"}
        out = poke(toks[0], toks[1], toks[2] if len(toks) > 2 else None, by="model")
        if not out["ok"]:
            return {"rc": 2, "text": out["error"]}
        return {"rc": 0, "text": f"{out['action']} {out['name']}"
                                 + (f" block {out['block']}" if out.get("block") else "")
                                 + f": now {json.dumps(out['reading'])} (by model)"}
    if meth == "list":
        return {"rc": 0, "text": _fmt(list_toys())}
    if meth == "add":
        try:
            kind, name, params = _parse_spec(args)
        except ValueError as e:
            return {"rc": 2, "text": str(e)}
        out = add(srv, kind, name=name, params=params)
        if not out["ok"]:
            return {"rc": 2, "text": out["error"]}
        return {"rc": 0, "text": f"added {out['name']} ({kind}, seed {out['seed']}): " + ", ".join(
            e.split("::", 1)[-1] for e in out["eids"])}
    if not args:
        return {"rc": 2, "text": f"{meth}(name): name the toy"}
    out = remove(srv, args[0]) if meth == "remove" else reset(args[0])
    if not out["ok"]:
        return {"rc": 2, "text": out["error"]}
    return {"rc": 0, "text": f"{meth} {out['name']}: done (its history is kept in toybox/archive)"}


# ---- migration: the model-written toys -> the kit -------------------------------------

def _short(eid: str) -> str:
    from .self_loop import _short as s
    return s(eid)


def migration_plan(est) -> list[dict]:
    """What each existing script toy (w:* / toy:*) would become. Nothing changes here."""
    docs = dict(est.docs or {})
    plan: list[dict] = []
    towers: dict[str, list] = {}
    for eid, e in docs.items():
        n = _short(eid)
        if re.match(r"^toy:tower[^:]*$", n) and not (e.properties or {}).get("method_name"):
            towers.setdefault(n, [])
    for eid, e in sorted(docs.items()):
        p = e.properties or {}
        if p.get("toybox"):
            continue
        n = _short(eid)
        scripted = bool(p.get("getter_script") or p.get("tick_script") or p.get("watch_script"))
        mo = p.get("method_of") or []
        mo = mo if isinstance(mo, list) else [mo]
        owner = next((t for t in towers if any(_short(str(o)) == t for o in mo)), None)
        if owner and p.get("method_name"):
            plan.append({"eid": eid, "name": n, "action": "retire", "of": owner,
                         "note": f"a method of the script tower {owner}; the built-in tower needs none"})
            continue
        m = re.match(r"^(toy:tower[^:]*):(.+)$", n)
        if m and m.group(1) in towers:
            b = re.match(r"^b(\d+)$", m.group(2))
            if b:
                towers[m.group(1)].append(int(b.group(1)))
                plan.append({"eid": eid, "name": n, "action": "part", "of": m.group(1),
                             "note": f"block {b.group(1)} of the built-in tower {m.group(1)}"})
            else:
                plan.append({"eid": eid, "name": n, "action": "retire", "of": m.group(1),
                             "note": "a helper of the script tower; the built-in tower needs none"})
            continue
        if n in towers:
            plan.append({"eid": eid, "name": n, "action": "replace", "kind": "tower", "params": {}})
            continue
        if n in MIGRATE_MAP:
            plan.append({"eid": eid, "name": n, "action": "replace", "kind": MIGRATE_MAP[n], "params": {}})
            continue
        if (n.startswith("toy:") or (n.startswith("w:") and scripted)) and not p.get("method_name"):
            plan.append({"eid": eid, "name": n, "action": "keep",
                         "note": "no built-in match: left as it is"})
    for row in plan:
        if row.get("kind") == "tower":
            blocks = towers.get(row["name"]) or []
            row["params"] = {"n": max(blocks) if blocks else KINDS["tower"]["params"]["n"]}
    return plan


def _fmt_plan(plan: list[dict]) -> str:
    if not plan:
        return "no script toys to migrate"
    lines = []
    for r in plan:
        if r["action"] == "replace":
            prm = " ".join(f"{k}={v}" for k, v in r["params"].items())
            lines.append(f"replace  {r['name']}  -> built-in {r['kind']}" + (f" {prm}" if prm else "")
                         + " (same name; the old doc is backed up)")
        elif r["action"] == "part":
            lines.append(f"replace  {r['name']}  -> {r['note']}")
        elif r["action"] == "retire":
            lines.append(f"retire   {r['name']}  ({r['note']}; backed up)")
        else:
            lines.append(f"keep     {r['name']}  ({r['note']})")
    return "\n".join(lines)


def migrate(srv, plan: list[dict]) -> list[str]:
    """Apply a plan: every doc it touches is backed up to toybox/migrated/
    first; the old docs go, the built-in toys take their names."""
    from .entities import json_safe
    est = srv.entity_store
    bdir = root() / "migrated"
    bdir.mkdir(parents=True, exist_ok=True)
    done = []
    for r in plan:
        if r["action"] == "keep":
            continue
        e = est.docs.get(r["eid"])
        if e is None:
            continue
        doc = {"eid": e.eid, "cls": getattr(e, "cls", None), "position": getattr(e, "position", None),
               "properties": {k: json_safe(v) for k, v in (e.properties or {}).items()}}
        (bdir / f"{_safe(_short(e.eid))}.json").write_text(json.dumps(doc, default=str, indent=1))
        est.docs.pop(e.eid, None)
    est.save()
    for r in plan:
        if r["action"] == "replace":
            out = add(srv, r["kind"], name=r["name"], params=r["params"], locked_ok=True)
            done.append(f"{r['name']} -> {r['kind']}: " + ("ok" if out["ok"] else out["error"]))
    return done


# ---- the CLI ----------------------------------------------------------------------------

def _seat_alive() -> bool:
    try:
        from .settings import data_dir
        from .stopseat import seat_pidfiles, seat_pids
        return bool(seat_pids(pidfiles=seat_pidfiles(data_dir())))
    except Exception:
        return False


def _offline_seat():
    import types
    from .entities import EntityStore
    from .settings import data_dir
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(data_dir() / "entities.json"), shared=False)
    srv._models_path = str(data_dir() / "models.json")    # #267: self's record (what is unlocked)
    return srv


def cli_main(argv: list[str]) -> int:
    """`membrane toybox list | add <kind> [--name N] [--param k=v ...] | remove <name> |
    reset <name> | migrate [--dry-run | --yes] | poke <toy> <action> [arg] [--as WHO]`."""
    import argparse
    ap = argparse.ArgumentParser(prog="membrane toybox",
                                 description="self's toys: the built-in kit (" + ", ".join(KINDS) + ")")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="the toys, their params and their change counts")
    a = sub.add_parser("add", help="switch a toy on")
    from .toy_unlocks import PLACEHOLDERS
    a.add_argument("kind", choices=sorted(set(KINDS) | set(PLACEHOLDERS)))  # a placeholder: refused by add, plainly
    a.add_argument("--name", default=None, help="its name (default toy:<kind>)")
    a.add_argument("--param", action="append", default=[], metavar="K=V", help="a param (repeatable); seed=N too")
    r = sub.add_parser("remove", help="switch a toy off (its history is archived, not deleted)")
    r.add_argument("name")
    s = sub.add_parser("reset", help="start a toy over (same params and seed; the old run is archived)")
    s.add_argument("name")
    m = sub.add_parser("migrate", help="list the model-written script toys and what they'd become")
    m.add_argument("--dry-run", action="store_true", help="only list (the default without --yes)")
    m.add_argument("--yes", action="store_true", help="replace them (each old doc is backed up first)")
    p = sub.add_parser("poke", help="play with a toy as the operator: tower knock|rebuild <k>, die roll, "
                                    "drift nudge [up|down], burst nudge, ball roll (back to self; body is self's alone)")
    p.add_argument("toy")
    p.add_argument("action")
    p.add_argument("arg", nargs="?", default=None)
    p.add_argument("--as", dest="who", default="",
                   help="who is playing (default: operator -- the seat's '' person, chat_io's convention)")
    args = ap.parse_args(argv)
    if args.cmd == "poke":
        # a poke writes the toy's own files under its lock, never the entity
        # store: it is safe beside a live seat (whose next look sees it)
        out = poke(args.toy, args.action, args.arg, by=args.who)
        if not out["ok"]:
            print(out["error"], file=sys.stderr)
            return 2
        print(f"{out['action']} {out['name']}" + (f" block {out['block']}" if out.get("block") else "")
              + f": now {json.dumps(out['reading'])} (by {out['by']})")
        return 0
    if args.cmd == "list":
        print(_fmt(list_toys()))
        return 0
    if args.cmd == "reset":
        out = reset(args.name)
        print(out.get("error") or f"reset {out['name']}", file=sys.stderr if not out["ok"] else sys.stdout)
        return 0 if out["ok"] else 2
    writes = args.cmd in ("add", "remove") or (args.cmd == "migrate" and args.yes and not args.dry_run)
    if writes and _seat_alive():
        print("the seat is running and owns the entity store: ask its model to call "
              "toybox.add / toybox.remove (the toybox object), or stop the seat first "
              "(membrane stop)", file=sys.stderr)
        return 2
    srv = _offline_seat()
    est = srv.entity_store
    if args.cmd == "add":
        params = {}
        for kv in args.param:
            k, sep, v = kv.partition("=")
            if not sep:
                print(f"--param {kv!r}: write k=v", file=sys.stderr)
                return 2
            params[k.strip()] = v.strip()
        out = add(srv, args.kind, name=args.name, params=params)
        if not out["ok"]:
            print(out["error"], file=sys.stderr)
            return 2
        est.flush()
        print(f"added {out['name']} ({out['kind']}, seed {out['seed']})")
        return 0
    if args.cmd == "remove":
        out = remove(srv, args.name)
        if not out["ok"]:
            print(out["error"], file=sys.stderr)
            return 2
        est.flush()
        print(f"removed {out['name']} (its history is in {root() / 'archive'})")
        return 0
    plan = migration_plan(est)
    print(_fmt_plan(plan))
    if args.dry_run or not any(r["action"] != "keep" for r in plan):
        return 0
    if not args.yes:
        print("\nnothing changed: re-run with --yes to replace them (or --dry-run to only list)",
              file=sys.stderr)
        return 2
    for line in migrate(srv, plan):
        print(line)
    est.flush()
    print(f"the old docs are backed up in {root() / 'migrated'}")
    return 0
