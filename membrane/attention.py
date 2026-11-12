"""THE ATTENTION BUDGET (2026-10-08, the operator's call): the model manages
its own watches -- at the limit it must choose what to let go of; membrane
never chooses for it.

ONE budget across the listener's three watch senses:
  board         the spatial watches (watch.WatchBoard: probe + radius; the
                board keeps its own cap of MAX_WATCHES inside this one)
  watch_ops     the relational ops on entities (near / away / around)
  watch_fields  the own-motion filters on entities (pos / align / vel)
The header's tracks are not counted (the operator's display, not the
listeners' attention).

over() is the planting sites' check: None = room, else the refusal with the
INVENTORY -- every live watch with its sense, entity, what, owner, age and
hits -- so the model can see what to drop.
"""
from __future__ import annotations

import time

ATTENTION_BUDGET = 12


def _docs(est):
    return list((getattr(est, "docs", None) or {}).items())


def inventory(est, board) -> list[dict]:
    now = time.time()
    out: list[dict] = []
    for w in (getattr(board, "watches", None) or {}).values():
        out.append({"sense": "board", "id": f"#{w.wid}", "entity": "",
                    "what": f"near {w.label!r} (radius {w.radius:.2f})",
                    "owner": getattr(w, "owner", "") or "",
                    "age_s": int(now - float(getattr(w, "created", 0) or now)),
                    "hits": int(getattr(w, "hits", 0) or 0),
                    "charge": round(float(getattr(w, "charge", 0) or 0), 2)})
    for eid, ent in _docs(est):
        props = getattr(ent, "properties", None) or {}
        for i, o in enumerate(props.get("watch_ops") or []):
            if not isinstance(o, dict):
                continue
            arg = o.get("z", o.get("y"))
            out.append({"sense": "watch_ops", "id": f"{eid}#op{i}", "entity": eid,
                        "what": f"{o.get('op')} {o.get('rel')} {arg}",
                        "owner": o.get("owner", "") or "",
                        "age_s": int(now - float(o.get("ts") or now)), "hits": None})
        for i, f in enumerate(props.get("watch_fields") or []):
            if not isinstance(f, dict):
                continue
            out.append({"sense": "watch_fields", "id": f"{eid}#wf{i}", "entity": eid,
                        "what": f"{f.get('verb')} {f.get('arg')}",
                        "owner": f.get("owner", "") or "",
                        "age_s": int(now - float(f.get("ts") or now)), "hits": None})
    return out


def used(est, board) -> int:
    return len(inventory(est, board))


def over(est, board, adding: int, budget: int = ATTENTION_BUDGET) -> dict | None:
    """None when `adding` more watches fit; else the honest refusal with the
    inventory (adding <= 0 -- a replace in place -- always fits)."""
    if adding <= 0:
        return None
    inv = inventory(est, board)
    if len(inv) + adding <= budget:
        return None
    return {"ok": False,
            "error": (f"attention budget ({budget}) full: {len(inv)} watches live, "
                      f"{adding} more asked -- drop one you no longer need "
                      f"(field_watch drop / entity_edit watch_ops=[] or "
                      f"watch_fields=[]), then plant again.\n" + render(inv)),
            "inventory": inv}


def render(inv: list[dict]) -> str:
    """The inventory as the refusal's text (the error string is what
    reaches the model through every relay)."""
    def age(s):
        s = int(s or 0)
        return f"{s // 3600}h" if s >= 3600 else f"{s // 60}m" if s >= 60 else f"{s}s"
    lines = ["live watches:"]
    for x in inv:
        hits = f", {x['hits']} hits" if x.get("hits") is not None else ""
        lines.append(f"- {x['sense']} {x['id']}: {x['what']} (owner "
                     f"{x.get('owner') or 'operator'}, age {age(x.get('age_s'))}{hits})")
    return "\n".join(lines)


# ---- THE ALERT BUDGET (2026-10-08, the seat's own review) -------------------
# "More observers = more next-turn interrupts = longer turns = 300s watchdog
# deaths. An alert budget per turn -- consolidate into the WHERE WE ARE block
# instead of N interrupt turns, with a digest mode this seat can dial."
ALERT_EXEMPT = ("intent-planted", "intent-landed")   # the operator's own asks


def is_exempt(ev: dict) -> bool:
    """A goal's plant, its landing and the pulse's rewake of a still-open
    goal skip the budget: a goal is the operator's or Memo's ask, not noise.
    The rewake is already paced by the pulse (min age, backoff on ignored
    passes, parking, one batch per beat); charging it to the watch budget let
    counter wakes starve every goal after its first pass (#551)."""
    return str(ev.get("kind") or "") in ALERT_EXEMPT


class AlertGate:
    """At most `budget` watch events per rolling window become interrupt
    turns; the rest fold into a DIGEST grouped per entity (the next turn's
    WHERE WE ARE lines). mode 'digest' folds everything but the exempt goal
    events. The dial is the entity Attention:seat; membrane keeps the gate."""

    MODES = ("interrupt", "digest")
    DIGEST_MAX = 12
    EID = "Attention:seat"
    # what a seat holds until its first start has made the entity (packs/attention.c1f carries the same numbers)
    DEFAULTS = {"budget": 6, "window_min": 10, "mode": "interrupt"}

    def __init__(self, est=None, budget=None, window_s=None, mode=None) -> None:
        self.est = est
        # an explicit value (a test's, a standalone gate's) stands in for the entity's
        self._own = {k: v for k, v in (("budget", budget),
                                       ("window_min", None if window_s is None else float(window_s) / 60.0),
                                       ("mode", mode)) if v is not None}
        self._spent: list[float] = []
        self._digest: dict[str, dict] = {}
        self._last_charged = False

    # the dial: the entity Attention:seat, written by the model (Attention:seat.dial = {...}; packs/attention.c1f)
    def _dial(self, key: str):
        if key in self._own:
            return self._own[key]
        d = (getattr(self.est, "docs", None) or {}).get(self.EID) if self.est is not None else None
        v = (d.properties or {}).get(key) if d is not None else None
        return self.DEFAULTS[key] if v is None else v

    @property
    def budget(self) -> int:
        return int(self._dial("budget"))

    @property
    def window_s(self) -> float:
        return float(self._dial("window_min")) * 60.0

    @property
    def mode(self) -> str:
        return str(self._dial("mode"))

    def settings(self) -> dict:
        return {"budget": self.budget, "window_min": round(self.window_s / 60.0, 2), "mode": self.mode}

    # the gate
    def allow(self, ev: dict, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        self._last_charged = False
        if is_exempt(ev):
            return True
        self._spent = [t for t in self._spent if now - t < self.window_s]
        if self.mode == "interrupt" and len(self._spent) < self.budget:
            self._spent.append(now)
            self._last_charged = True
            return True
        key = str(ev.get("entity") or ev.get("kind") or "?")
        row = self._digest.get(key)
        if row is None:
            row = self._digest[key] = {"entity": key, "kinds": {}, "n": 0, "first": now}
        kind = str(ev.get("kind") or "?") + (f":{ev['verb']}" if ev.get("verb") else "")
        row["kinds"][kind] = row["kinds"].get(kind, 0) + 1
        row["n"] += 1
        row["last"] = now
        return False

    def refund(self) -> None:
        """The last allowed event never ran (the loop pool was full and it
        went back on the queue): its budget slot is returned -- if it took
        one (an exempt event never did)."""
        if self._last_charged and self._spent:
            self._spent.pop()
        self._last_charged = False

    def digest(self, now: float | None = None) -> list[str]:
        rows = sorted(self._digest.values(), key=lambda r: -r.get("last", 0))
        out = []
        for r in rows[:self.DIGEST_MAX]:
            kinds = ", ".join(f"{k} ×{n}" for k, n in r["kinds"].items())
            out.append(f"{r['entity']} — {kinds}")
        if len(rows) > self.DIGEST_MAX:
            out.append(f"+{len(rows) - self.DIGEST_MAX} more entities")
        return out

    def take_digest(self, now: float | None = None) -> list[str]:
        out = self.digest(now)
        self._digest = {}
        return out


def migrate_dial(est, path: str) -> bool:
    """A seat that still has the old attention.json brings its dial over once, as the Attention:seat entity (the file is
    kept beside it as attention.json.migrated). -> migrated?"""
    import json
    import os
    if est is None or not os.path.exists(path):
        return False
    try:
        saved = json.loads(open(path).read() or "{}")
    except (OSError, ValueError):
        return False
    from .entities import QuantumEntity
    props = {k: saved[k] for k in ("budget", "window_min", "mode") if saved.get(k) is not None}
    if props and AlertGate.EID not in (est.docs or {}):
        est.upsert(QuantumEntity(eid=AlertGate.EID, cls="Attention",
                                 properties={"name": "alert dial", "silent_birth": True, **props}), embedder=None)
    os.replace(path, path + ".migrated")
    return True
