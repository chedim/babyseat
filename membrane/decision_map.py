"""THE PROJECT DECISION MAP (#470): "how did we get here -> what are we doing
-> what do we need to do next", a reversed kanban whose cards are decisions.

A card is a Decision entity (the existing class, converged on: no parallel
store) with a ``status`` that moves right to left:

  open (a question) -> decided (answered) -> in_flight (the work) -> closed (receipt)

A card tagged work_needed=False closes on the answer; one needing work stays
decided until a watcher loop moves it to in_flight, and it closes only once a
receipt is on it. The operator may also drop an open card. An agent may work on an
undecided card without committing to it: reversible drafts go in its ``speculative``
property; the card stays open (no in_flight, no close) until the answer, which keeps
the drafts only if it is the pick they assumed. (Cards without a work_needed tag are
legacy and may still go open -> in_flight.) (An untagged answer
stays decided, as before.)

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
STATUS_CLOSED = "closed"
STATUS_DROPPED = "dropped"
PLANTED_BY_LOOP = "loop"          # card.planted_by: a watch loop asked it
STATUSES = (STATUS_OPEN, STATUS_IN_FLIGHT, STATUS_DECIDED, STATUS_CLOSED, STATUS_DROPPED)
AGENT_STATUSES = (STATUS_OPEN, STATUS_IN_FLIGHT, STATUS_CLOSED)    # what entity_edit may set
LOCKED = (STATUS_CLOSED, STATUS_DROPPED)
ANSWER_OWNED = ("value", "set_by", "set_at")        # only answer() writes these
SPECULATIVE = "speculative"     # reversible drafts an agent attaches to an undecided card
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
    cur = (props or {}).get("status")
    if key == "status" and cur in LOCKED:
        return f"the card is {cur}; changing a past decision comes with timelines (#470 step 3)"
    if key == "status" and cur == STATUS_DECIDED and value not in (STATUS_IN_FLIGHT, STATUS_CLOSED):
        return ("the card is decided; changing a past decision comes with "
                "timelines (#470 step 3)")
    if key == "status" and value == STATUS_OPEN and (props or {}).get("value") is not None:
        return ("the card is answered; changing a past decision comes with "
                "timelines (#470 step 3)")
    if key == "status" and value == STATUS_IN_FLIGHT and cur == STATUS_DECIDED \
            and (props or {}).get("work_needed") is not True:
        return "the card was decided as needing no work; it cannot go in flight"
    if key == "status" and value == STATUS_IN_FLIGHT and cur == STATUS_OPEN \
            and (props or {}).get("work_needed") is not None:
        return ("the card is not decided yet: attach reversible drafts in its "
                "speculative property and wait for the answer")
    if key == SPECULATIVE and cur not in (None, STATUS_OPEN):
        return "speculative drafts attach only to an open card"
    if key == "status" and value == STATUS_CLOSED:
        if cur not in (STATUS_DECIDED, STATUS_IN_FLIGHT):
            return "only a decided or in-flight card closes"
        if not str((props or {}).get("receipt") or "").strip():
            return "a card closes with a receipt: set its receipt property first"
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
            "chat": p.get("chat"), "message": p.get("message"),
            "work_needed": p.get("work_needed"), "receipt": p.get("receipt")}


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


def _announce(est, props: dict, ev: dict) -> None:
    """Tell someone a card was decided or dropped. A card a WATCH LOOP planted
    reports to the loops' events pool (the loop that asked picks it up); every
    other card tells the CHAT: ``est._decision_chat(text, ev)`` posts one
    message and starts one turn (the proxy wires it). With no chat wired the
    pool is the fallback, so the answer is never lost."""
    sink = getattr(est, "_decision_chat", None)
    if sink is not None and props.get("planted_by") != PLANTED_BY_LOOP:
        title, value = ev.get("title") or ev.get("entity"), ev.get("value", "?")
        text = f"decided: {title} = {value} (card '{ev.get('entity')}')"
        if value == "dropped":
            text = f"dropped: {title} (card '{ev.get('entity')}')"
        if ev.get("work_needed"):
            text += " -- it needs work: set its status to in_flight, do the work, then set its receipt and status closed"
        if ev.get("blocks"):
            text += f". It blocks: {', '.join(map(str, ev['blocks']))}"
        try:
            sink(text, ev)
            return
        except Exception:
            pass
    pool = getattr(est, "_events_pool", None)
    if pool is not None:
        pool.put(ev)


def stamp_planter(est, res: dict) -> None:
    """A new Decision card records who planted it: ``loop`` when the writer is a
    watch loop's turn, else ``chat``. A card that already has a planter keeps it."""
    from . import writes as _W
    from .entities import CLASS_DECISION
    if est is None or not res.get("ok"):
        return
    ent = est.resolve(str(res.get("eid") or ""), exact=True)
    if ent is None or ent.cls != CLASS_DECISION or (ent.properties or {}).get("planted_by"):
        return
    p = dict(ent.properties or {})
    p["planted_by"] = PLANTED_BY_LOOP if _W.writer() == "llm:loop" else "chat"
    est.edit(ent.eid, properties={"planted_by": p["planted_by"]})


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


def answer(est, ledger, spec: str, value, conv: str = "", message: str = "", work=None) -> dict:
    """The operator answers a card: validate the value against its var, append
    the decision to the ledger, then mark the card decided. Nothing changes on
    a refusal or a failed ledger write; one answer at a time. ``message`` (the
    cockpit's ✓ while the card's chat is open) is the id of that chat's newest
    message: where the card was decided. It is kept only with its ``conv``."""
    with _ANSWER_LOCK:
        return _answer(est, ledger, spec, value, conv, message, work)


def _answer(est, ledger, spec: str, value, conv: str = "", message: str = "", work=None) -> dict:
    from .entities import CLASS_DECISION
    if est is None:
        return {"ok": False, "error": "entity layer unavailable"}
    ent = est.resolve(str(spec or ""), exact=True)
    if ent is None or ent.cls != CLASS_DECISION:
        return {"ok": False, "error": f"no Decision card {spec!r}"}
    p = ent.properties or {}
    # an in_flight card is answered work (open -> decided -> in_flight), past like a
    # decided one; only a legacy in_flight card that was never answered takes one
    if _status(p) not in (STATUS_OPEN, STATUS_IN_FLIGHT) or \
            (_status(p) == STATUS_IN_FLIGHT and p.get("value") is not None):
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
    work = p.get("work_needed") if work is None else work     # the answer may override the tag
    if work is not None:
        p["work_needed"] = bool(work)
    p.update({"value": v, "status": STATUS_CLOSED if work is False else STATUS_DECIDED,
              "set_by": "operator",
              "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    if conv:
        p["chat"] = p.get("chat") or conv     # a card keeps the chat it already has
        message = str(message or "").strip()[:MESSAGE_ID_MAX]
        if message and p["chat"] == conv:     # the message is in the card's own chat
            p["message"] = message
    spec_kept = None
    if p.get(SPECULATIVE):      # drafts built on the agent's pick live only if the answer is that pick
        spec_kept = str(v) == str(p.get("pick"))
        if not spec_kept:
            p["speculative_discarded"] = p.pop(SPECULATIVE)
    ent.properties = p
    est.upsert(ent)
    _announce(est, p, {"kind": "decision", "entity": ent.eid, "title": title, "value": shown,
                       "blocks": p.get("blocks") or [], "work_needed": work is True,
                       "speculative_kept": spec_kept, "ts": time.time()})
    return {"ok": True, "card": _card_of(ent)}


def drop(est, spec: str) -> dict:
    """The operator drops an open card without answering it. Owner-only at the
    route; entity_edit refuses status dropped, so an agent never drops."""
    from .entities import CLASS_DECISION
    with _ANSWER_LOCK:
        if est is None:
            return {"ok": False, "error": "entity layer unavailable"}
        ent = est.resolve(str(spec or ""), exact=True)
        if ent is None or ent.cls != CLASS_DECISION:
            return {"ok": False, "error": f"no Decision card {spec!r}"}
        p = ent.properties or {}
        if _status(p) != STATUS_OPEN:
            return {"ok": False, "conflict": True, "error": "only an open card can be dropped"}
        p.update({"status": STATUS_DROPPED, "set_by": "operator",
                  "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        ent.properties = p
        est.upsert(ent)
        _announce(est, p, {"kind": "decision", "entity": ent.eid, "title": p.get("name") or ent.eid,
                           "value": "dropped", "blocks": p.get("blocks") or [],
                           "work_needed": False, "ts": time.time()})
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
