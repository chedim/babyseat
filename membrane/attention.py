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
    goal skip the budget: a goal is the operator's or Memo's ask, not noise,
    and counter wakes must not starve it (#551). The pulse only rewakes
    OPEN goals, paced by its own min-age and batch."""
    return str(ev.get("kind") or "") in ALERT_EXEMPT


class AlertGate:
    """At most `budget` watch events per rolling `window_s` become interrupt
    turns; the rest fold into a DIGEST grouped per entity (the next turn's
    WHERE WE ARE lines). mode 'digest' folds everything but the exempt goal
    events. The seat sets the dial (the attention tool); membrane keeps it."""

    MODES = ("interrupt", "digest")
    DIGEST_MAX = 12

    def __init__(self, budget: int = 6, window_s: float = 600.0,
                 mode: str = "interrupt") -> None:
        self.budget, self.window_s, self.mode = int(budget), float(window_s), mode
        self._spent: list[float] = []
        self._digest: dict[str, dict] = {}
        self._last_charged = False

    # the dial
    def set(self, budget=None, window_min=None, mode=None) -> dict:
        if mode is not None:
            if str(mode) not in self.MODES:
                raise ValueError(f"mode is one of {self.MODES}")
            self.mode = str(mode)
        if budget is not None:
            b = int(budget)
            if not 0 <= b <= 50:
                raise ValueError("budget is 0..50 interrupt turns per window")
            self.budget = b
        if window_min is not None:
            w = float(window_min)
            if not 1 <= w <= 1440:
                raise ValueError("window_min is 1..1440")
            self.window_s = w * 60.0
        return self.settings()

    def settings(self) -> dict:
        return {"budget": self.budget, "window_min": round(self.window_s / 60.0, 2),
                "mode": self.mode}

    @classmethod
    def from_settings(cls, d) -> "AlertGate":
        g = cls()
        if isinstance(d, dict):
            try:
                g.set(budget=d.get("budget"), window_min=d.get("window_min"), mode=d.get("mode"))
            except (ValueError, TypeError):
                pass
        return g

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
