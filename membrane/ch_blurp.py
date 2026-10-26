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

THE SHEET. augment() lays the branches out as the skill tree, the level as the
branches granted, and the Code tab as Blurp's triggers (their line, state,
balance, firings)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import ch as CH
from . import ch_learn as L

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


def grant(srv, branch: str, by: str = "operator") -> bool:
    """The operator's Accept of `branch:<kind>`: Blurp may act there. -> newly granted?"""
    branch = str(branch or "").strip()
    g = grants(srv)
    if not branch or branch in g:
        return False
    g.add(branch)
    f = _grants_file(srv)
    if f is not None:
        try:
            doc = json.loads(f.read_text()) if f.exists() else {}
        except (OSError, ValueError):
            doc = {}
        rows = [r for r in doc.get("grants") or [] if isinstance(r, dict)]
        rows.append({"branch": branch, "by": str(by or "operator"), "ts": time.time()})
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"grants": rows}, indent=1))
        tmp.replace(f)
    return True


def granted(srv, path: str) -> bool:
    return branch_of(path) in grants(srv)


# ---- the body --------------------------------------------------------------------------------

def attach(srv) -> None:
    """Boot: Blurp's learner on the seat's VM, its purse closed until Blurp reports."""
    vm = getattr(srv, "ch_vm", None)
    if vm is None or vm.learner is not None:
        return
    srv.ch_purse = Purse()
    vm.learner = L.Learner(vm, srv.ch_purse, granted=lambda path: granted(srv, path))


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
    return {"energy_delta": p.take(), "pruned": len(pruned), "awake": p.awake}


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
        out.append({"name": t.eid, "line": CH.render(t), "branch": p.get("branch") or "",
                    "state": lr.state(t.eid) if lr is not None else "shadow",
                    "balance": round(lr.balance(t.eid) / L.WRITE_COST, 2) if lr is not None else 0.0,
                    "hits": lr.hits(t.eid) if lr is not None else 0,
                    "fired": vm.fired.get(t.eid, 0)})
    out.sort(key=lambda c: (-c["balance"], c["line"]))
    return out


def tree(srv) -> list[dict]:
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


def augment(srv, sheet: dict) -> dict:
    """The sheet (or card) Blurp's process made, with what the seat's VM knows."""
    if not isinstance(sheet, dict) or getattr(getattr(srv, "ch_vm", None), "learner", None) is None:
        return sheet                                   # no learner on this seat: the sheet as Blurp made it
    out = dict(sheet)
    level = len(grants(srv))
    out["level"] = level
    if isinstance(out.get("header"), dict):
        out["header"] = {**out["header"], "level": level, "title": f"Lv {level}"}
    if "tree" in out or "header" in out:
        nodes = tree(srv)
        out["tree"] = nodes
        out["awaiting"] = [n["id"] for n in nodes if n["state"] == "earned" and not n["accepted"]]
        out["accepted"] = [n["id"] for n in nodes if n["accepted"]]
        out["code"] = code(srv)
    return out
