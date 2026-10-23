"""THE PROJECT DECISION MAP (#470): "how did we get here -> what are we doing
-> what do we need to do next", a reversed kanban whose cards are decisions.

A card is a Decision entity (the existing class, converged on: no parallel
store) with a ``status`` that moves right to left:

  open (a question, the future) -> in_flight (the work, now) -> decided (the past)

A Decision with no status is a legacy one, already decided. A card asks for
ONE variable (``var``: int, float, bool, string or an option from a list) and
arrives with the agent's ``pick``. Agents make cards with the entity tools
(entity_add + entity_edit properties); only the operator answers, through
:func:`answer`, which sets ``value``/``set_by``/``set_at`` and appends the
decision to the operator ledger (recalled verbatim, as law). entity_edit
refuses those keys on a Decision, so an agent can ask but never decide.

The edges are eid lists on the card: ``blocks`` (future -> future),
``led_to`` (past -> what it led to) and ``carried_by`` (in flight -> the work
carrying it). The board is one view of this subgraph of the field; the field
view is another.
"""
from __future__ import annotations

import time

STATUS_OPEN = "open"
STATUS_IN_FLIGHT = "in_flight"
STATUS_DECIDED = "decided"
STATUSES = (STATUS_OPEN, STATUS_IN_FLIGHT, STATUS_DECIDED)
AGENT_STATUSES = (STATUS_OPEN, STATUS_IN_FLIGHT)    # what entity_edit may set
ANSWER_OWNED = ("value", "set_by", "set_at")        # only answer() writes these
EDGE_KEYS = ("blocks", "led_to", "carried_by")
VAR_TYPES = ("int", "float", "bool", "string", "option")

_TRUE = ("true", "yes", "y", "on", "1")
_FALSE = ("false", "no", "n", "off", "0")


def refusal(cls: str, key: str, value) -> str | None:
    """Why entity_edit may not set ``key`` on an entity of class ``cls``
    (None = it may)."""
    from .entities import CLASS_DECISION
    if cls != CLASS_DECISION:
        return None
    if key in ANSWER_OWNED:
        return "answer-owned (only the operator answers a Decision card)"
    if key == "status" and value not in AGENT_STATUSES:
        return (f"a card's status is set to one of {', '.join(AGENT_STATUSES)} "
                f"here; only the operator's answer makes it decided")
    return None


def validate(var, value):
    """(the value coerced to the card's type, None) or (None, why not)."""
    t = (var or {}).get("type") if isinstance(var, dict) else None
    if t not in VAR_TYPES:
        return None, f"the card's var type {t!r} is not one of {', '.join(VAR_TYPES)}"
    if t == "bool":
        if isinstance(value, bool):
            return value, None
        s = str(value).strip().lower()
        if s in _TRUE:
            return True, None
        if s in _FALSE:
            return False, None
        return None, f"{value!r} is not yes or no"
    if t in ("int", "float"):
        if isinstance(value, bool):
            return None, f"{value!r} is not a number"
        try:
            num = float(str(value).strip())
        except (TypeError, ValueError):
            return None, f"{value!r} is not a number"
        if t == "int":
            if num != int(num):
                return None, f"{value!r} is not a whole number"
            return int(num), None
        return num, None
    s = str(value if value is not None else "").strip()
    if not s:
        return None, "the value is empty"
    if t == "option":
        opts = [str(o) for o in (var.get("options") or [])]
        if s not in opts:
            return None, f"{s!r} is not one of {', '.join(map(repr, opts))}"
    return s[:2000], None


def _status(props: dict) -> str:
    s = props.get("status")
    return s if s in STATUSES else STATUS_DECIDED


def _card_of(ent) -> dict:
    p = ent.properties or {}
    return {"eid": ent.eid, "title": p.get("name") or ent.eid,
            "description": p.get("summary") or "", "status": _status(p),
            "var": p.get("var") if isinstance(p.get("var"), dict) else None,
            "pick": p.get("pick"), "value": p.get("value"),
            "set_by": p.get("set_by"), "set_at": p.get("set_at"),
            "chat": p.get("chat")}


def board(est) -> dict:
    """Every Decision card and the edges between them (and to the work)."""
    from .entities import CLASS_DECISION
    cards, edges = [], []
    for ent in list((getattr(est, "docs", None) or {}).values()):
        if ent.cls != CLASS_DECISION or (ent.properties or {}).get("merged_into"):
            continue
        cards.append(_card_of(ent))
        for rel in EDGE_KEYS:
            to = (ent.properties or {}).get(rel)
            for t in (to if isinstance(to, list) else [to] if to else []):
                edges.append({"from": ent.eid, "to": str(t), "rel": rel})
    cards.sort(key=lambda c: (STATUSES.index(c["status"]), c["title"]))
    return {"cards": cards, "edges": edges}


def _shown(value, var: dict) -> str:
    if isinstance(value, bool):
        s = "yes" if value else "no"
    else:
        s = str(value)
    unit = str(var.get("unit") or "").strip()
    return f"{s} {unit}" if unit else s


def answer(est, ledger, spec: str, value, conv: str = "") -> dict:
    """The operator answers a card: validate the value against its var, mark
    it decided, append the decision to the ledger. Nothing changes on a
    refusal."""
    from .entities import CLASS_DECISION
    if est is None:
        return {"ok": False, "error": "entity layer unavailable"}
    ent = est.resolve(str(spec or ""), exact=True)
    if ent is None or ent.cls != CLASS_DECISION:
        return {"ok": False, "error": f"no Decision card {spec!r}"}
    p = ent.properties or {}
    if _status(p) == STATUS_DECIDED:
        return {"ok": False, "error": "this card is already decided (changing a past "
                                      "decision comes with timelines, #470 step 3)"}
    raw = p.get("var")
    var: dict = raw if isinstance(raw, dict) else {}
    v, err = validate(var, value)
    if err:
        return {"ok": False, "error": err}
    p.update({"value": v, "status": STATUS_DECIDED, "set_by": "operator",
              "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    if conv:
        p["chat"] = p.get("chat") or conv
    ent.properties = p
    est.upsert(ent)
    title = p.get("name") or ent.eid
    if ledger is not None:
        ledger.append(f"decided: {title} = {_shown(v, var)}", conv=conv)
    return {"ok": True, "card": _card_of(ent)}
