"""BLURP ANEW (#576 D2): the VM's learner on Blurp's body.

THE PURSE. The learner (ch_learn, in the seat's VM) charges each Blurp write and
refunds each hit into the seat's purse. Blurp's loop -- its own process, or the
seat's thread -- EXCHANGES with it on every step through the context call
(self_api.context(only="learn", report=...)): it reports whether it is awake and
takes the net energy since the last exchange, which it applies to its energy.
Until Blurp has said it is awake, nothing is spent, so nothing is guessed or
fired; when it reports falling asleep, the learner prunes (stay positive).

BRANCHES AND GRANTS. A skill is a BRANCH: the kind a candidate's target belongs
to (`Component:toy`, `ha`, `Component:chat`; for a learned call, the called
object's). A branch is OPEN while Blurp guesses there, EARNED when a guess there
pays for itself (a proposal), and GRANTED by the operator's Accept of
`branch:<kind>` (seat_panel.control) -- only on a granted branch may a proposal
act. The grants are the seat's (`ch-grants.json` beside the store).

THE SHEET. augment() lays out the skill tree -- while toys are the world, the
child tree (SKILLS: each skill earned by its kind of guess, accepted as
`branch:skill:<id>`, the toys opening under the skills; the roots earned on
Blurp's body following the current, ch_body) -- the level as the skills granted,
and the Code tab as Blurp's triggers (their line, state, balance, firings)."""
from __future__ import annotations

import collections
import json
import os
import threading
import time
from pathlib import Path

from . import ch as CH
from . import ch_learn as L
from . import writes as W

branch_of = L.branch_of
GRANTS_FILE = "ch-grants.json"


class Purse:
    """Blurp's energy, as the seat's learner sees it: charges and refunds held
    until Blurp's loop takes them; spending only while Blurp says it is awake."""

    def __init__(self):
        self._lk = threading.Lock()
        self.delta = 0.0
        self.awake = False
        self.spent = 0.0
        self.back = 0.0

    def charge(self, cost: float, why: str = "") -> None:
        with self._lk:
            self.delta -= float(cost)
            self.spent += float(cost)

    def refund(self, amount: float, why: str = "") -> None:
        with self._lk:
            self.delta += float(amount)
            self.back += float(amount)

    def can_spend(self) -> bool:
        return self.awake

    def take(self) -> float:
        with self._lk:
            d, self.delta = self.delta, 0.0
        return d


# ---- grants ----------------------------------------------------------------------------------

def _grants_file(srv) -> Path | None:
    p = getattr(getattr(srv, "entity_store", None), "path", None)
    return Path(p).parent / GRANTS_FILE if p else None


def grants(srv) -> set:
    hit = getattr(srv, "_ch_grants", None)
    if hit is not None:
        return hit
    out = set()
    f = _grants_file(srv)
    if f is not None:
        try:
            out = {str(g["branch"]) for g in json.loads(f.read_text()).get("grants") or [] if g.get("branch")}
        except (OSError, ValueError, AttributeError, TypeError):
            out = set()
    srv._ch_grants = out
    return out


_GRANT_LOCK = threading.Lock()


def grant(srv, branch: str, by: str = "operator") -> bool:
    """The operator's Accept of `branch:<kind>`: Blurp may act there. -> newly granted?
    Saved first, live after (an unwritable home grants nothing); one at a time."""
    branch = str(branch or "").strip()
    with _GRANT_LOCK:
        g = grants(srv)
        if not branch or branch in g:
            return False
        f = _grants_file(srv)
        if f is not None:
            try:
                doc = json.loads(f.read_text()) if f.exists() else {}
            except (OSError, ValueError):
                doc = {"grants": [{"branch": b} for b in sorted(g)]}   # never drop what memory holds
            rows = [r for r in doc.get("grants") or [] if isinstance(r, dict)]
            rows.append({"branch": branch, "by": str(by or "operator"), "ts": time.time()})
            try:
                tmp = f.with_name(f"{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_text(json.dumps({"grants": rows}, indent=1))
                tmp.replace(f)
            except OSError:
                return False
        g.add(branch)
    try:
        unlock(srv)                                    # a granted skill opens the toys under it
    except Exception as e:                             # noqa: BLE001
        print(f"[blurp] the next toy not switched on ({e!r})")
    return True


def granted(srv, path: str) -> bool:
    return L.branch(getattr(srv, "entity_store", None), path) in grants(srv)


def grant_times(srv) -> dict:
    """branch -> when it was granted (the grants file's rows)."""
    f = _grants_file(srv)
    try:
        rows = json.loads(f.read_text()).get("grants") or [] if f is not None else []
    except (OSError, ValueError, AttributeError):
        rows = []
    return {str(r["branch"]): r.get("ts") for r in rows if isinstance(r, dict) and r.get("branch")}


# ---- the toys, in order (#727) ---------------------------------------------------------------

# THE CHILD SKILL TREE (operator, 2026-10-30): the old ladder's 13 skills (02ffab9^
# self_loop.ACHIEVEMENTS: ids, names, branches, prerequisites, the child analogies), each
# EARNED by its kind of guess paying for itself on any toy (ch_learn.kind_of) over earned
# prerequisites, and ACCEPTED by the operator (`branch:skill:<id>`). `kind` None: no kind of
# guess is built for it yet (the tree shows it, not built).
SKILLS = {
    "feel": {"name": "feel itself", "branch": "self", "prereqs": (), "kind": "feel", "child_age": "newborn",
             "child_ability": "tells its own touch from another's (Rochat & Hespos 1997)",
             "test": "its own move of its body lands where it guessed (closer to the current or not)"},
    "reach": {"name": "look", "branch": "world", "prereqs": (), "kind": "reach", "child_age": "~2-3 mo",
              "child_ability": "learns what to expect of each thing it looks at (Fantz 1964, habituation)",
              "test": "it guesses where the current will be"},
    "kinds": {"name": "tell things apart", "branch": "world", "prereqs": ("reach",), "kind": "kinds",
              "child_age": "~3-4 mo", "child_ability": "forms perceptual categories (Quinn & Eimas)",
              "test": "it tells its own move of the body from the current's push"},
    "patience": {"name": "patience", "branch": "world", "prereqs": ("kinds",), "kind": "patience",
                 "child_age": "~3.5 mo", "child_ability": "anticipates timed events (Haith, anticipatory looking)",
                 "test": "it guesses a toy's own later move"},
    "shift": {"name": "notice the world changed", "branch": "world", "prereqs": ("reach",), "kind": None,
              "child_age": "~3.5-5 mo",
              "child_ability": "is surprised when the world breaks its expectation (Baillargeon, violation of expectation)",
              "test": "not built yet"},
    "nudge": {"name": "first reach", "branch": "world", "prereqs": ("feel",), "kind": "nudge", "child_age": "~2-3 mo",
              "child_ability": "learns its kicks move a mobile (Rovee-Collier, contingency learning)",
              "test": "it guesses what its own nudge does to a toy"},
    "meet": {"name": "meet an other", "branch": "others", "prereqs": ("reach", "nudge"), "kind": "meet",
             "child_age": "~3-5 mo",
             "child_ability": "tells perfect self-contingency from imperfect social contingency (Bahrick & Watson)",
             "test": "it guesses what someone else's poke does to a toy"},
    "turns": {"name": "time its turn", "branch": "others", "prereqs": ("meet",), "kind": None, "child_age": "~3-5 mo",
              "child_ability": ("times its turn in a proto-conversation: serve and return "
                                "(Bateson 1975; Trevarthen 1979; Jaffe et al. 2001)"),
              "test": "not built yet"},
    "expect": {"name": "expect a reply", "branch": "others", "prereqs": ("meet",), "kind": None, "child_age": "~2-3 mo",
               "child_ability": "expects a caregiver's response: the still-face upset (Tronick et al. 1978)",
               "test": "not built yet"},
    "mirror": {"name": "recognize itself", "branch": "self", "prereqs": ("feel", "nudge"), "kind": "mirror",
               "child_age": "~18 mo", "child_ability": "passes the mirror test (Amsterdam 1972)",
               "test": "it guesses what the mirror shows"},
    "change": {"name": "change itself", "branch": "self", "prereqs": ("mirror", "patience"), "kind": None,
               "child_age": "~4-5 y",
               "child_ability": ("picks the way it learns by its own track record, and keeps a change only "
                                 "when it works better: adaptive strategy choice (Siegler & Shrager 1984)"),
               "test": "not built yet"},
    "speak": {"name": "speak", "branch": "others", "prereqs": ("turns", "expect"), "kind": None, "child_age": "~12 mo",
              "child_ability": ("names a thing to an other and gets a response: first referential words "
                                "and pointing to share (Bates, Camaioni & Volterra 1975)"),
              "test": "not built yet"},
    "invent": {"name": "invent", "branch": "self", "prereqs": ("change", "speak"), "kind": None, "child_age": "~5 y",
               "child_ability": "makes up games with its own rules", "test": "not built yet"},
}

TOY_ORDER = ("tower", "ball", "mirror")   # #337's order; no self-moving toys (slow/fast/burst/die/drift): nothing earns them (#727)
TOY_UNDER = {"tower": None, "ball": "nudge", "mirror": "nudge"}   # the skill whose grant opens each toy
                     # (card 2026-10-30: the tower from the start, the ball and the mirror under first reach)


_SEAT_TOYS: dict = {"key": None, "have": {}}


def _seat_toys() -> dict:
    """kind -> the toy the seat has of it (`toy:<kind>` first; an older name, e.g. w:ball, else).
    Read from the toybox files only when its roster moved (#728: never per toy write).
    The roster generation is in-process only: a `membrane toybox add/reset` from the CLI
    (another process) shows here after the next grant or in-process add, or a restart."""
    from . import toybox as TB
    key = (TB.roster(), str(TB.root()))
    if _SEAT_TOYS["key"] == key:
        return _SEAT_TOYS["have"]
    try:
        rows = TB.list_toys()
    except Exception:
        rows = []
    out: dict = {}
    for r in sorted(rows, key=lambda r: (str(r.get("name")) != f"toy:{r.get('kind')}", str(r.get("name")))):
        out.setdefault(str(r.get("kind") or ""), str(r.get("name") or ""))
    _SEAT_TOYS.update(key=key, have=out)
    return out


def toys(srv) -> list[dict]:
    """The toys in #337's order, each {kind, name, branch, state, under}: granted (its
    branch accepted, before the skills), current (open: Blurp learns on it -- its
    skill `under` is granted, or it needs none), locked (until that skill's Accept)."""
    from . import toybox as TB
    have, g = _seat_toys(), grants(srv)
    key = (TB.roster(), str(TB.root()), frozenset(g))  # what moved, never id(have): CPython reuses ids
    hit = getattr(srv, "_ch_toys", None)
    if hit is not None and hit[0] == key:
        return hit[1]                                  # #728: the open set moves only on a grant or a new toy
    out = []
    for kind in TOY_ORDER:
        name = have.get(kind) or f"toy:{kind}"
        under = TOY_UNDER.get(kind)
        state = ("granted" if name in g else
                 "current" if under is None or f"skill:{under}" in g else "locked")
        out.append({"kind": kind, "name": name, "branch": name, "state": state, "under": under,
                    "on_seat": kind in have})
    srv._ch_toys = (key, out)
    return out


def level(srv) -> int:
    """Blurp's level: its granted skills while it learns toys (#728: a command grant
    stays on file for later, it does not count), every grant once commands are back."""
    if L.WORLD == "toys":
        return sum(1 for i in SKILLS if f"skill:{i}" in grants(srv))
    return len(grants(srv))


def toy_open(srv, name: str) -> bool:
    """May Blurp learn on this toy now (granted, or current)? A toy outside the order
    (a kind it doesn't list) is never open."""
    return any(t["name"] == name and t["state"] in ("granted", "current") for t in toys(srv))


def unlock(srv) -> list[str]:
    """Switch on each open toy the seat lacks (toybox.add): what a grant unlocks."""
    from . import toybox as TB
    added = []
    for t in toys(srv):
        if t["state"] == "current" and not t["on_seat"]:
            r = TB.add(srv, t["kind"])
            if r.get("ok"):
                added.append(t["name"])
    return added


# ---- the body --------------------------------------------------------------------------------

def attach(srv) -> None:
    """Boot: Blurp's learner on the seat's VM, its purse closed until Blurp reports."""
    vm = getattr(srv, "ch_vm", None)
    if vm is None or vm.learner is not None:
        return
    srv.ch_purse = Purse()
    vm.learner = L.Learner(vm, srv.ch_purse, granted=lambda path: granted(srv, path),
                           suggest=lambda tr, path, value: _suggest(srv, tr, path, value),
                           adopted=lambda w: adopted(srv, w), toy_open=lambda name: toy_open(srv, name))


ANEW = "child-tree"                 # Blurp's generation: a seat on an older one starts Blurp anew once
ANEW_FILE = "ch-anew.json"


OLD_BODY = CH.MEMBRANE_NS + "Component:self:body"   # #731's second body, gone in #732


def retire_old_body(srv) -> dict:
    """#732: Blurp's body is CH.SELF. Remove #731's `Component:self:body` and Blurp's
    triggers that name it (they could only ever write to it). Boot; a no-op once gone."""
    est = getattr(srv, "entity_store", None)
    vm = getattr(srv, "ch_vm", None)
    gone = 0
    if vm is not None:
        for t in [t for t in vm.triggers() if vm._is_selfs(t)]:
            if OLD_BODY in json.dumps([(t.properties or {}).get(k) for k in ("when", "where", "write")]) and vm.est.remove(t.eid):
                gone += 1
    if est is not None and OLD_BODY in (est.docs or {}) and est.remove(OLD_BODY):
        gone += 1
    if gone:
        print(f"[blurp] #732: the old body retired ({gone} entities: it and the triggers naming it)")
    return {"removed": gone}


def anew(srv) -> dict:
    """Boot: the first time this generation runs on the seat, Blurp starts anew (operator,
    2026-10-30) -- the grants archived beside the store (never deleted), Blurp's own
    triggers removed, the toys back to their start (toybox archives each run); the
    record, energy and mode stay. Later boots change nothing. -> {anew, ...}"""
    try:
        retire_old_body(srv)                           # #732: before anything reads the body
    except Exception:
        pass
    f = _grants_file(srv)
    if f is None:
        return {"anew": False, "why": "no store"}
    mark = f.with_name(ANEW_FILE)
    try:
        if json.loads(mark.read_text()).get("generation") == ANEW:
            return {"anew": False}
    except (OSError, ValueError, AttributeError):
        pass
    now = time.time()
    with _GRANT_LOCK:
        if f.exists():
            f.replace(f.with_name(f"ch-grants.{int(now)}.json"))
        srv._ch_grants = set()
    gone = []
    vm = getattr(srv, "ch_vm", None)
    if vm is not None:
        for t in [t for t in vm.triggers() if vm._is_selfs(t)]:
            if vm.est.remove(t.eid):
                gone.append(t.eid)
        if vm.learner is not None:
            for d in (vm.learner._bal, vm.learner._hits, vm.learner._pending, vm.learner._guessed):
                d.clear()
    from . import toybox as _TB
    toys = [r["name"] for r in _TB.list_toys() if _TB.reset(r["name"], now=now).get("ok")]
    mark.write_text(json.dumps({"generation": ANEW, "ts": now, "triggers": len(gone), "toys": toys}))
    print(f"[blurp] anew ({ANEW}): grants archived, {len(gone)} triggers removed, toys reset {toys}")
    return {"anew": True, "triggers": gone, "toys": toys}


# ---- suggestions (#576 H) --------------------------------------------------------------------

TURNS_KEPT = 512                    # turns / chats remembered (a buffer bound)
SUGGEST_VALUE_MAX = 60              # a suggested string value shown inline up to this; longer: as data
_TURNS: "collections.OrderedDict[str, str]" = collections.OrderedDict()
_LK = threading.Lock()


def note_turn(chain_id: str, conv: str) -> None:
    """An LLM turn's chain belongs to this conversation (TurnRunner.run)."""
    if not chain_id or not conv:
        return
    with _LK:
        _TURNS[chain_id] = conv
        _TURNS.move_to_end(chain_id)
        while len(_TURNS) > TURNS_KEPT:
            _TURNS.popitem(last=False)


def suggestion_text(target_path: str, value, est=None, cand: str = "") -> str:
    """A proposal as the LLM reads it: `set <eid>.<prop> = <value>` or
    `call <method> on <object> with <args>`."""
    if target_path.endswith(".invoke") and isinstance(value, dict):
        meth = target_path[:-len(".invoke")]
        name = ""
        if est is not None:
            m = (est.docs or {}).get(meth)
            name = str(((m.properties or {}) if m is not None else {}).get("method_name") or "")
        return f"call {name or meth} on {value.get('object')} with {value.get('args') or {}!r}"[:240]
    return f"set {target_path} = {_shown(value, cand)}"


def _shown(v, cand: str = "") -> str:
    """A value as the banner may carry it: short ones whole; a long or multi-line
    string as a one-line preview `-> <candidate>.predicted.value`, where the LLM
    reads the whole of it (the banner is small; the operator's form, #583)."""
    if isinstance(v, str) and (len(v) > SUGGEST_VALUE_MAX or "\n" in v):
        head = " ".join(v.split())[:SUGGEST_VALUE_MAX]
        return repr(head + "…") + (f" -> {cand}.predicted.value" if cand else "")
    return repr(v)


def _suggest(srv, tr, target_path: str, value) -> None:
    ch = (W.current() or {}).get("id")
    with _LK:
        conv = _TURNS.get(ch or "")
        if not conv:
            return                                     # not an LLM turn: nobody to suggest to
        line = suggestion_text(target_path, value, getattr(srv, "entity_store", None), cand=tr.eid)
        rows = _box(srv, "_ch_suggest").setdefault(conv, [])
        if line not in rows:
            rows.append(line)
            del rows[:-L.GUESS_PER_CHAIN]              # the newest few (the approved per-chain number)
        # what the LLM must do to adopt it, kept for this chat's later turns (#583)
        offered = _box(srv, "_ch_offered").setdefault(conv, [])
        want = (tr.eid, srv.ch_vm.learner._expected(target_path), value)
        if want not in offered:
            offered.append(want)
            del offered[:-L.GUESS_PER_CHAIN]


def _box(srv, name: str) -> "collections.OrderedDict":
    box = getattr(srv, name, None)
    if box is None:
        box = collections.OrderedDict()
        setattr(srv, name, box)
    while len(box) > TURNS_KEPT:                       # chats that went quiet are forgotten
        box.popitem(last=False)
    return box


def adopted(srv, w: dict) -> list[str]:
    """The candidates whose suggestion this LLM write adopts (in any later turn of
    the chat it was offered in): each pays once."""
    ch = ((w.get("chain") or {}).get("id")) or ""
    with _LK:
        conv = _TURNS.get(ch)
        offered = getattr(srv, "_ch_offered", None) or {}
        rows = offered.get(conv) if conv else None
        if not rows:
            return []
        out, keep = [], []
        for cand, p, v in rows:
            if p == w.get("path") and v == w.get("new"):
                out.append(cand)
            else:
                keep.append((cand, p, v))
        offered[conv] = keep
        return out


def take_suggestions(srv, conv: str) -> list[str]:
    """This conversation's suggestions, for its next banner -- each shown once."""
    with _LK:
        box = getattr(srv, "_ch_suggest", None) or {}
        return box.pop(conv, []) if conv else []


def close_purse(srv) -> None:
    """Blurp's process stopped or died: no more spending until it reports again
    (the debt stays for its next exchange)."""
    p = getattr(srv, "ch_purse", None)
    if p is not None:
        p.awake = False


def exchange(srv, report: dict | None) -> dict:
    """Blurp's step: it reports {awake, asleep, mode}; it takes the net energy."""
    p = getattr(srv, "ch_purse", None)
    vm = getattr(srv, "ch_vm", None)
    if p is None or vm is None or vm.learner is None:
        return {"energy_delta": 0.0}
    report = report or {}
    was = p.awake
    p.awake = bool(report.get("awake")) and str(report.get("mode") or "") in ("on", "watch")
    pruned = []
    if was and not p.awake and report.get("asleep"):
        with vm._run:                                  # sleep: stay positive (not mid-drain)
            pruned = vm.learner.prune()
    if p.awake:
        try:
            _show_body(srv, vm)
        except Exception:
            pass                                       # the body is display only: never the exchange's failure
    return {"energy_delta": p.take(), "pruned": len(pruned), "awake": p.awake}


SEAT_FID = "seat:self"      # where the body rests before its first firing (the map's seat)


def _show_body(srv, vm) -> None:
    """The body on the map (fieldpulse.self_cycle, the old cycle's channel): Blurp is
    where `self` is (#732) -- at the entity its own move wrote into `self.at`, at the
    seat before any move -- looking at where the current is (`self.current`). What its
    triggers watched never places it. Points are field vectors; the beat places them."""
    from . import fieldpulse as FP
    from .eids import field_id
    fp, fld = FP.of(srv), getattr(srv, "efield", None)
    if fld is None:
        return

    def vec(fid):
        st = fld.get(fid)
        v = getattr(st, "state", None) if st is not None else None
        return None if v is None else [float(x) for x in v]
    me = (vm.est.docs or {}).get(CH.SELF)
    p = (me.properties or {}) if me is not None else {}
    here, there = str(p.get(L.SELF_MOVE) or ""), str(p.get(L.SELF_PUSH) or "")
    at = (vec(field_id(here)) if here else None) or vec(SEAT_FID)
    if at is None:
        return
    look = vec(field_id(there)) if there and there != here else None
    prev = getattr(srv, "_ch_body_at", None) or at
    srv._ch_body_at = at
    if fp is not None:
        fp.self_cycle({"from": prev, "at": at, "aim": look}, {"mode": "on", "rung": level(srv)})   # the tag reads its level
    _read_body(srv, fld, at)


def _read_body(srv, fld, at) -> dict | None:
    """Card 1, C: the body against the current, told to the learner (ch_body.read)."""
    from . import ch_body as BODY
    cur = fld.current() if hasattr(fld, "current") else None
    if cur is None:
        return None                                    # nobody spoke lately: no current to follow
    gen = fld.current_gen()                            # #731: moves only when a pull or a drop did
    seen = getattr(srv, "_ch_body_near", None)         # (its generation, its nearest): an unchanged
    if seen is not None and seen[0] == gen:            # current costs no field search
        near = seen[1]
    else:
        try:
            hits = fld.recall(cur, k=1, by="state") or []
        except Exception:
            hits = []
        near = hits[0][0] if hits else ""
        srv._ch_body_near = (gen, near)
    return BODY.read(srv, at, cur, near, gen)


# ---- the sheet -------------------------------------------------------------------------------

def _mine(srv) -> list:
    vm = getattr(srv, "ch_vm", None)
    if vm is None:
        return []
    return [t for t in vm.triggers() if vm._is_selfs(t)]


def code(srv) -> list[dict]:
    vm = getattr(srv, "ch_vm", None)
    lr = getattr(vm, "learner", None) if vm is not None else None
    out = []
    for t in _mine(srv):
        p = t.properties or {}
        out.append({"name": t.eid, "line": CH.render(t), "branch": p.get("branch") or "", "kind": p.get("kind") or "",
                    "state": lr.state(t.eid) if lr is not None else "shadow",
                    "balance": round(lr.balance(t.eid) / L.WRITE_COST, 2) if lr is not None else 0.0,
                    "hits": lr.hits(t.eid) if lr is not None else 0,
                    "fired": vm.fired.get(t.eid, 0)})
    out.sort(key=lambda c: (-c["balance"], c["line"]))
    return out


def tree(srv) -> list[dict]:
    """The child skill tree (SKILLS): a skill is earned when a guess of its kind pays for
    itself (a proposal) and its prerequisites are earned, or once it is accepted; locked
    while a prerequisite is not earned; open otherwise. The LLM's command branches stay
    gated off (#727), so they are not shown."""
    if L.WORLD != "toys":
        return _branch_tree(srv)                       # commands are back: the per-branch tree (kept)
    g = grants(srv)
    by: dict[str, list] = {}
    for c in code(srv):
        by.setdefault(c.get("kind") or "", []).append(c)
    nodes, earned = [], set()
    for i, sk in SKILLS.items():                       # prerequisites come first
        cs = by.get(sk["kind"], []) if sk["kind"] else []
        n_pos = sum(1 for c in cs if c["state"] == "proposal")
        accepted = f"skill:{i}" in g
        ready = all(p in earned for p in sk["prereqs"])
        state = "earned" if accepted or (n_pos and ready) else "open" if ready else "locked"
        if state == "earned":
            earned.add(i)
        nodes.append({"id": f"branch:skill:{i}", "name": sk["name"], "branch": sk["branch"],
                      "prereqs": [f"branch:skill:{p}" for p in sk["prereqs"]],
                      "built": sk["kind"] is not None, "state": state, "accepted": accepted,
                      "bar": None if state == "earned" else (min(1.0, max(c["balance"] for c in cs) + 1.0) if cs else 0.0),
                      "child_age": sk["child_age"], "child_ability": sk["child_ability"],
                      "test": (f"{sk['test']}: {len(cs)} guesses, {n_pos} paying for themselves"
                               + (" -- granted" if accepted else "")) if sk["kind"] else sk["test"]})
    return nodes


def _branch_tree(srv) -> list[dict]:
    """The tree while the LLM's commands are the world (#576 D2): one node per branch,
    earned when a guess there pays for itself, accepted by `branch:<kind>`."""
    g = grants(srv)
    by: dict[str, list] = {}
    for c in code(srv):
        by.setdefault(c["branch"] or "?", []).append(c)
    nodes = []
    for b in sorted(set(by) | g):
        cs = by.get(b, [])
        n_pos = sum(1 for c in cs if c["state"] == "proposal")
        earned = b in g or n_pos > 0
        nodes.append({"id": f"branch:{b}", "name": b.split(":")[-1], "branch": "world", "prereqs": [],
                      "built": True, "state": "earned" if earned else "open", "accepted": b in g,
                      "bar": None if earned else (min(1.0, max(c["balance"] for c in cs) + 1.0) if cs else 0.0),
                      "test": f"{len(cs)} guesses, {n_pos} paying for themselves"
                              + (" -- granted" if b in g else "")})
    return nodes


def overlay(srv, limit: int = 100) -> dict:
    """The field map's Blurp layer (GET /membrane/map/blurp): Blurp's recent firings
    in FIELD ids, each with its trigger's state NOW -- shadow, proposal, or suggest
    (a proposal on an accepted branch) -- and the counts over its triggers."""
    from .eids import field_id
    vm = getattr(srv, "ch_vm", None)
    lr = getattr(vm, "learner", None) if vm is not None else None
    if vm is None or lr is None:
        return {"firings": [], "counts": {"shadow": 0, "proposal": 0, "suggest": 0}, "triggers": 0}

    held = grants(srv)

    def state(tr) -> str:                              # one reading for lines and counts: the trigger's branch
        st = lr.state(tr.eid)
        return "suggest" if st == "proposal" and str((tr.properties or {}).get("branch") or "") in held else st
    docs = vm.est.docs or {}
    rows = []
    for f in list(vm.recent)[-limit:]:
        tr = docs.get(f["trig"])
        if tr is None or not vm._is_selfs(tr):
            continue                                   # Blurp's own triggers only
        rows.append({"from": field_id(f["from"]), "to": field_id(f["to"]), "trig": f["trig"],
                     "state": state(tr), "ts": f["ts"]})
    counts = {"shadow": 0, "proposal": 0, "suggest": 0}
    for t in _mine(srv):
        st = state(t)
        counts[st] = counts.get(st, 0) + 1
    return {"firings": rows, "counts": counts, "triggers": sum(counts.values())}


def augment(srv, sheet: dict) -> dict:
    """The sheet (or card) Blurp's process made, with what the seat's VM knows."""
    if not isinstance(sheet, dict) or getattr(getattr(srv, "ch_vm", None), "learner", None) is None:
        return sheet                                   # no learner on this seat: the sheet as Blurp made it
    out = dict(sheet)
    lv = level(srv)
    out["level"] = lv
    lr = srv.ch_vm.learner                             # what it saw and why it passed: a Blurp earning nothing is read, not guessed
    out["learner"] = {**dict(lr.counts),
                      "candidates": sum(1 for t in srv.ch_vm.triggers() if (t.properties or {}).get("owner") == CH.SELF)}
    if isinstance(out.get("header"), dict):
        out["header"] = {**out["header"], "level": lv, "title": f"Lv {lv}"}
    if "tree" in out or "header" in out:
        nodes = tree(srv)
        out["tree"] = nodes
        out["awaiting"] = [n["id"] for n in nodes if n["state"] == "earned" and not n["accepted"]]
        out["accepted"] = [n["id"] for n in nodes if n["accepted"]]
        out["code"] = code(srv)
    if "tree" in out or "header" in out:               # #727: the toys, the current one and the next
        cs, at = code(srv), grant_times(srv)
        rows = []
        for t in toys(srv):
            mine = [c for c in cs if c["branch"] == t["branch"]]
            rows.append({**t, "guesses": len(mine), "paying": sum(1 for c in mine if c["state"] == "proposal"),
                         "granted_at": at.get(t["branch"]), "built": True, "have": t["on_seat"],
                         "under_name": SKILLS[t["under"]]["name"] if t.get("under") else None})
        out["toys"] = rows
        out["toy_current"] = next((t["kind"] for t in rows if t["state"] == "current"), None)
        out["toy_next"] = next((t["kind"] for t in rows if t["state"] == "locked"), None)
    return out
