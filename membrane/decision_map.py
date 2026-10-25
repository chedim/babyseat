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

import math
import threading
import time

STATUS_OPEN = "open"
STATUS_IN_FLIGHT = "in_flight"
STATUS_DECIDED = "decided"
STATUSES = (STATUS_OPEN, STATUS_IN_FLIGHT, STATUS_DECIDED)
AGENT_STATUSES = (STATUS_OPEN, STATUS_IN_FLIGHT)    # what entity_edit may set
ANSWER_OWNED = ("value", "set_by", "set_at")        # only answer() writes these
EDGE_KEYS = ("blocks", "led_to", "carried_by")
VAR_TYPES = ("int", "float", "bool", "string", "option")
MESSAGE_ID_MAX = 200                # a chat message id's clip (ids are short; never a paste)
LEDGER_TITLE_MAX = 160              # the title's clip in the ledger line (one line, never a paste)
_ANSWER_LOCK = threading.Lock()     # one answer at a time: a card is decided once

_TRUE = ("true", "yes", "y", "on", "1")
_FALSE = ("false", "no", "n", "off", "0")


def refusal(cls: str, key: str, value, props: dict | None = None) -> str | None:
    """Why entity_edit may not set ``key`` on an entity of class ``cls`` whose
    properties are ``props`` (None = it may)."""
    from .entities import CLASS_DECISION
    if cls != CLASS_DECISION:
        return None
    if key in ANSWER_OWNED:
        return "answer-owned (only the operator answers a Decision card)"
    # an answered card is locked; a legacy Decision (no status yet) may become a card
    if key == "status" and (props or {}).get("status") == STATUS_DECIDED:
        return ("the card is decided; changing a past decision comes with "
                "timelines (#470 step 3)")
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
        if not math.isfinite(num):
            return None, f"{value!r} is not a finite number"
        if t == "int":
            if num != int(num):
                return None, f"{value!r} is not a whole number"
            return int(num), None
        return num, None
    s = str(value if value is not None else "").strip()
    if not s:
        return None, "the value is empty"
    if t == "option":
        if not isinstance(var.get("options"), list):
            return None, "the card's options are not a list"
        opts = [str(o) for o in var["options"]]
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
            "chat": p.get("chat"), "message": p.get("message")}


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


def set_chat(est, spec: str, chat: str) -> dict:
    """Record the chat a card opened (the board's click): only the `chat`
    property changes, and a card keeps the chat it already has."""
    from .entities import CLASS_DECISION
    chat = str(chat or "").strip()
    if not chat:
        return {"ok": False, "error": "chat required"}
    ent = est.resolve(str(spec or ""), exact=True) if est is not None else None
    if ent is None or ent.cls != CLASS_DECISION:
        return {"ok": False, "error": f"no Decision card {spec!r}"}
    p = ent.properties or {}
    p["chat"] = p.get("chat") or chat
    ent.properties = p
    est.upsert(ent)
    return {"ok": True, "card": _card_of(ent)}


def _one_line(s, cap: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= cap else s[: cap - 1] + "…"


def answer(est, ledger, spec: str, value, conv: str = "", message: str = "") -> dict:
    """The operator answers a card: validate the value against its var, append
    the decision to the ledger, then mark the card decided. Nothing changes on
    a refusal or a failed ledger write; one answer at a time. ``message`` (the
    cockpit's ✓ while the card's chat is open) is the id of that chat's newest
    message: where the card was decided. It is kept only with its ``conv``."""
    with _ANSWER_LOCK:
        return _answer(est, ledger, spec, value, conv, message)


def _answer(est, ledger, spec: str, value, conv: str = "", message: str = "") -> dict:
    from .entities import CLASS_DECISION
    if est is None:
        return {"ok": False, "error": "entity layer unavailable"}
    ent = est.resolve(str(spec or ""), exact=True)
    if ent is None or ent.cls != CLASS_DECISION:
        return {"ok": False, "error": f"no Decision card {spec!r}"}
    p = ent.properties or {}
    if _status(p) == STATUS_DECIDED:
        return {"ok": False, "conflict": True,
                "error": "this card is already decided (changing a past "
                         "decision comes with timelines, #470 step 3)"}
    raw = p.get("var")
    var: dict = raw if isinstance(raw, dict) else {}
    v, err = validate(var, value)
    if err:
        return {"ok": False, "error": err}
    title = _one_line(p.get("name") or ent.eid, LEDGER_TITLE_MAX)
    shown = _one_line(_shown(v, var), LEDGER_TITLE_MAX)
    if ledger is not None and not ledger.append(f"decided: {title} = {shown}", conv=conv):
        return {"ok": False, "error": "the ledger could not be written; the card stays open"}
    p.update({"value": v, "status": STATUS_DECIDED, "set_by": "operator",
              "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    if conv:
        p["chat"] = p.get("chat") or conv     # a card keeps the chat it already has
        message = str(message or "").strip()[:MESSAGE_ID_MAX]
        if message and p["chat"] == conv:     # the message is in the card's own chat
            p["message"] = message
    ent.properties = p
    est.upsert(ent)
    return {"ok": True, "card": _card_of(ent)}


def goals(est, active=()) -> dict:
    """The cockpit's goals (#470): the open Goal entities, the seat's active
    set first (in its rank order, ``active`` eids, flagged), then the rest,
    the newest first."""
    from .entities import CLASS_GOAL
    act = [str(a) for a in (active or ())]
    out = []
    for ent in list((getattr(est, "docs", None) or {}).values()):
        p = ent.properties or {}
        if ent.cls != CLASS_GOAL or p.get("merged_into") or p.get("status", "open") != "open":
            continue
        out.append({"eid": ent.eid, "title": p.get("name") or ent.eid,
                    "summary": p.get("summary") or "", "active": ent.eid in act,
                    "born": p.get("born")})

    def rank(g):
        born = g["born"] if isinstance(g["born"], (int, float)) else 0.0
        return (act.index(g["eid"]) if g["active"] else len(act), -born, g["title"])
    out.sort(key=rank)
    return {"goals": out}


# ---- AN ANSWER COMES BACK (2026-10-25) -------------------------------------
# A card is answered the way a watch fires: the asker hears it without
# spending a watch. The field already knows who asked -- the entity_add call
# that made the card is a captured observation event, and it carries its
# conversation (#429: 'sid:<serve session>'). Nothing is stamped on the card.

ADD_TOOLS = ("entity_add",)           # the MCP name (Claude's, after its mcp__<server>__ prefix)
ADD_FAMILY = ("entity", "add")        # the local models' family call: entity(action='add')


def _norm(s) -> str:
    return " ".join(str(s or "").lower().split())


def _made_it(ev: dict, name: str, eid: str) -> bool:
    tool = str(ev.get("tool") or "").rsplit("__", 1)[-1]
    a = ev.get("args") if isinstance(ev.get("args"), dict) else {}
    if tool not in ADD_TOOLS and not (tool == ADD_FAMILY[0] and
                                      str(a.get("action") or "") == ADD_FAMILY[1]):
        return False
    if eid and eid in str(ev.get("result_head") or ""):
        return True
    return bool(name) and _norm(a.get("name")) == name


def birth_conversation(fld, ent) -> str:
    """The conversation `ent` was born in, from the field's own records: the
    earliest captured add call that made it (by its name, or its eid in the
    call's result) and the `conv` that call's event carries. '' when the
    field holds none (an old card, a card a script made, capture off)."""
    b = getattr(fld, "b", None)
    if b is None or ent is None:
        return ""
    name = _norm((ent.properties or {}).get("name"))
    best = None
    try:
        rows = b.scan("events", {"origin": getattr(fld, "origin", ""), "stream": "observation"})
        for _k, ev in rows:
            if ev.get("conv") and _made_it(ev, name, ent.eid):
                if best is None or (ev.get("seq") or 0) < (best.get("seq") or 0):
                    best = ev
    except Exception:
        return ""
    return str((best or {}).get("conv") or "")


def _deliver(runner, conv: str, text: str, eid: str, what: str) -> str:
    if not conv.startswith("sid:") or runner is None:
        return ""                     # a client's own chat (opencode, Claude Code): no seat session
    from . import jobs as _jobs
    return _jobs.deliver_event(runner, conv[4:], text, kind="decision", entity=eid,
                               tool="decision", key=f"decision{int(time.time() * 1000) % 10**9}",
                               title=what, card_input={"card": eid})


def _blockers_of(est, target: str) -> list:
    from .entities import CLASS_DECISION
    out = []
    for d in list((getattr(est, "docs", None) or {}).values()):
        if d.cls != CLASS_DECISION or (d.properties or {}).get("merged_into"):
            continue
        for t in _eids(est, (d.properties or {}).get("blocks")):
            if t == target:
                out.append(d)
    return out


def _eids(est, v) -> list:
    out = []
    for x in (v if isinstance(v, list) else [v] if v else []):
        e = est.resolve(str(x), exact=True)
        out.append(e.eid if e is not None else str(x))
    return out


def answered(srv, eid: str, runner=None) -> dict:
    """After an answer: the decision goes back to the conversation the card
    was born in (jobs.deliver_event: a continuation in a chat, a pass on its
    own entity for a loop), and each entity the card `blocks` is pulsed once
    every card blocking it is decided -- a Goal wakes its loop (the board's
    rewake), anything else hears it in its own birth conversation."""
    from .entities import CLASS_GOAL
    est = getattr(srv, "entity_store", None)
    fld = getattr(srv, "efield", None)
    runner = runner if runner is not None else getattr(srv, "_loop_runner", None)
    out = {"delivered": "", "pulsed": []}
    ent = (getattr(est, "docs", None) or {}).get(eid) if est is not None else None
    if ent is None:
        return out
    p = ent.properties or {}
    var = p.get("var") if isinstance(p.get("var"), dict) else {}
    title = _one_line(p.get("name") or eid, LEDGER_TITLE_MAX)
    shown = _one_line(_shown(p.get("value"), var), LEDGER_TITLE_MAX)
    out["conv"] = conv = birth_conversation(fld, ent)
    out["delivered"] = _deliver(runner, conv, f"[membrane] decided: {title} = {shown} "
                                f"(your card {eid}) -- continue", eid, f"decided · {title} = {shown}")
    for t in _eids(est, p.get("blocks")):
        target = est.docs.get(t)
        if target is None:
            continue
        holders = _blockers_of(est, t)
        if any(_status(h.properties or {}) != STATUS_DECIDED for h in holders):
            continue
        tp = target.properties or {}
        if target.cls == CLASS_GOAL:
            if tp.get("status", "open") != "open":
                continue
            from . import watch_agents as _WA
            _WA._wake(est, getattr(srv, "events_pool", None), target, tp)
        else:
            names = ", ".join(_one_line((h.properties or {}).get("name") or h.eid, 60) for h in holders)
            tname = _one_line(tp.get("name") or t, LEDGER_TITLE_MAX)
            _deliver(runner, birth_conversation(fld, target),
                     f"[membrane] unblocked: {tname} ({t}) -- every card blocking it is decided "
                     f"({names}) -- continue", t, f"unblocked · {tname}")
        out["pulsed"].append(t)
    return out


def answered_async(srv, eid: str, runner=None) -> None:
    """The routes' follow-up, off the request thread (the field scan is a walk)."""
    def go():
        try:
            answered(srv, eid, runner=runner)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True, name="decision-answered").start()
