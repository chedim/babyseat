"""THE PROJECT DECISION MAP (#470): "how did we get here -> what are we doing
-> what do we need to do next", a reversed kanban whose cards are decisions.

A card is a Decision entity (the existing class, converged on: no parallel
store) with a ``status`` that moves right to left:

  open (a question) -> decided (answered) -> in_flight (the work) -> closed (receipt)

A card tagged work_needed=False closes on the answer; one needing work stays
decided until a watcher loop moves it to in_flight, and it closes only once a
receipt is on it. The operator may also drop an open card; an agent may drop one
only with a non-empty ``dropped_why`` set first (#758). An agent may work on an
undecided card without committing to it: reversible drafts go in its ``speculative``
property; the card stays open (no in_flight, no close) until the answer, which keeps
the drafts only if it is the pick they assumed. (Cards without a work_needed tag are
legacy and may still go open -> in_flight.) (An untagged answer
stays decided, as before.)

A Decision with no status is a legacy one, already decided. A card asks for
ONE variable (``var``: int, float, bool, string or an option from a list) and
arrives with the agent's ``pick``. Agents make cards with the entity tools
(entity_add + entity_edit properties); only the operator answers, through the
decisions pack (packs/decisions.c1f: `Decision.answer`), which validates the value, sets
``value``/``set_by``/``set_at`` and appends the decision to the operator ledger (recalled
verbatim, as law). entity_edit refuses those keys on a Decision, so an agent can ask but
never decide on its own. A person who answered in the chat in plain words is passed on by
the agent in that chat by writing `Decision.relay = {value, quote}` (#845): the pack
checks that their quoted words are there, and the record says it was relayed.

The edges are eid lists on the card: ``blocks`` (future -> future),
``led_to`` (past -> what it led to) and ``carried_by`` (in flight -> the work
carrying it). The board is one view of this subgraph of the field; the field
view is another.
"""
from __future__ import annotations

import threading
import time

from . import speech as _SP

STATUS_OPEN = "open"
STATUS_IN_FLIGHT = "in_flight"
STATUS_DECIDED = "decided"
STATUS_CLOSED = "closed"
STATUS_DROPPED = "dropped"
STATUSES = (STATUS_OPEN, STATUS_IN_FLIGHT, STATUS_DECIDED, STATUS_CLOSED, STATUS_DROPPED)
AGENT_STATUSES = (STATUS_OPEN, STATUS_IN_FLIGHT, STATUS_CLOSED, STATUS_DROPPED)    # what entity_edit may set
LOCKED = (STATUS_CLOSED, STATUS_DROPPED)
# only the decisions pack (packs/decisions.c1f) writes these: the answer, its stamps and receipts, and what it derives
ANSWER_OWNED = ("value", "set_by", "set_at", "answer", "answer_id", "answer_result", "answer_log", "relay_id", "relay_result", "value_ts",
                "relayed_by", "answer_quote", "answer_chat", "previous_value")
_ANSWER_LOCK = threading.Lock()     # one drop at a time: a card is dropped once
SPECULATIVE = "speculative"     # reversible drafts an agent attaches to an undecided card
EDGE_KEYS = ("blocks", "led_to", "carried_by")
VAR_TYPES = ("int", "float", "bool", "string", "option")


def refusal(cls: str, key: str, value, props: dict | None = None) -> str | None:
    """Why entity_edit may not set ``key`` on an entity of class ``cls`` whose
    properties are ``props`` (None = it may)."""
    from .entities import CLASS_DECISION
    if cls == "Intent":                   # the intent board's writes (packs/intents.c1f) keep the lane rules the tools had
        from . import diag_requests as _DR
        from . import server as _S
        return _S.intent_write_refusal(key, (_DR.current() or {}).get("agent"))
    if cls != CLASS_DECISION:
        return None
    if key == "relay":                    # the model's hand: pass on what the person said in the chat (the pack proves it)
        from . import diag_requests as _DR
        from . import server as _S
        lane = _S._lane_of((_DR.current() or {}).get("agent"))
        if lane == "listener":
            return ("listener lane: a loop doesn't answer a Decision card -- only the person's chat passes on what "
                    "they said; end with BLOCKED on them")
        if lane == "plan":
            return "plan mode: a card isn't answered from a planning session -- the build-mode turn passes on what the person said"
        return None
    if key in ANSWER_OWNED:
        return ("answer-owned (only the person answers a Decision card: pass on what they "
                "said in the chat by writing relay = {value, quote} on the card)")
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
    if key == "status" and value == STATUS_DROPPED:     # #758: an agent drops a stale card, saying why
        if cur not in (None, STATUS_OPEN) or (props or {}).get("value") is not None:
            return "only an open card can be dropped"
        if not str((props or {}).get("dropped_why") or "").strip():
            return "a card drops with a reason: set its dropped_why property first"
    if key == "status" and value not in AGENT_STATUSES:
        return (f"a card's status is set to one of {', '.join(AGENT_STATUSES)} "
                f"here; only the person's answer makes it decided (what they said in the "
                f"chat goes through the card's relay)")
    return None


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

PLANTED_BY = "planted_by"      # stamped at creation by the lane: "watch" (a watch loop) or "chat"


def _tell(est, p: dict, ev: dict, text: str, pool=None) -> None:
    """Where a decision event goes: the watcher pool when a watch loop planted
    the card, else one message into the card's own chat (it starts a turn).
    Only the operator's owner-only route answers or drops (an agent's drop,
    through entity_edit, comes here too via ``tell_drop`` and says so in
    ``dropped_by``), so a turn that message starts cannot fire another."""
    pool = pool if pool is not None else getattr(est, "_events_pool", None)
    sid = str(p.get("chat") or "")
    hook = getattr(est, "_decision_chat", None)
    runner = getattr(getattr(est, "_srv", None), "_loop_runner", None)
    if p.get(PLANTED_BY) == "watch" or not sid or (hook is None and runner is None):
        # a watch card, or a chat card with no chat opened yet (or no runner
        # wired): the pool, as before, so its watched goals still wake
        if pool is not None:
            pool.put(ev)
        return
    if hook is not None:
        hook(sid, text)
        return
    from . import writes as _W
    threading.Thread(target=_W.carry(lambda: runner.run(sid, text)),
                     daemon=True, name="decision-event").start()


def drop(est, spec: str) -> dict:
    """The operator drops an open card without answering it. Owner-only at the
    route; an agent drops through entity_edit, only with a dropped_why (#758)."""
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
        p.update({"status": STATUS_DROPPED, "set_by": "operator", "dropped_by": "operator",
                  "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "value_ts": time.time()})
        ent.properties = p
        est.upsert(ent)
        tell_drop(est, ent)
        return {"ok": True, "card": _card_of(ent)}


def tell_drop(est, ent) -> None:
    """The one tail of every drop: the decision event (operator's drop() and an
    agent's entity_edit alike), so a parked goal or the watcher pool hears it."""
    p = ent.properties or {}
    _tell(est, p, {"kind": "decision", "entity": ent.eid, "title": p.get("name") or ent.eid,
                   "value": "dropped", "blocks": p.get("blocks") or [],
                   "work_needed": False, "ts": time.time()},
          _SP.say("dropped", title=p.get("name") or ent.eid))


def stamp_agent_drop(props: dict) -> None:
    """An agent's drop (status -> dropped via entity_edit): same stamps as drop()."""
    props.update({"dropped_by": "agent", "value_ts": time.time()})


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


def _wait_on(d, reason: str) -> dict:
    p = d.properties or {}
    return {"id": d.eid, "kind": {"Task": "task", "Wait": "wait"}.get(d.cls, "decision"),
            "title": p.get("title") or p.get("name") or d.eid, "reason": reason}


def _goal_waiting_on(docs, goal: str, GG, by_goal: dict | None = None) -> list:
    out = []
    for d in (by_goal.get(goal, ()) if by_goal is not None else docs.values()):
        p = d.properties or {}
        if p.get("goal") != goal or d.eid == goal:
            continue
        st = str(p.get("status") or "open")
        if d.cls == GG.TASK and st in (GG.STUCK, GG.FAILED):
            out.append(_wait_on(d, st + " task"))
        elif d.cls == GG.TASK and GG._unfinished(d) and p.get("blocks"):
            out.append(_wait_on(d, "unfinished blocker task"))
        elif d.cls == GG.WAIT and GG._unfinished(d):
            out.append(_wait_on(d, "unfinished wait"))
        elif d.cls == "Decision" and GG._unfinished(d):
            out.append(_wait_on(d, "open decision"))
    return out


def _watch_nodes(est):
    """#960: live watches set on purpose (owner Memo / a goal worker ``watch:<eid>``) as kind ``watch``
    nodes: a ``watches`` edge to the watched entity, a ``triggers`` edge to the task/goal firing starts.
    Finished (done), dispatch and any other owner (operator, persons, Blurp/self) are left out.
    Scope: only ``watch_ops`` are drawn; ``watch_fields`` and field watches are out of scope here."""
    nodes, edges = [], []
    for ent in list((getattr(est, "docs", None) or {}).values()):
        p = ent.properties or {}
        ops = p.get("watch_ops")
        if not isinstance(ops, list) or p.get("merged_into"):
            continue
        trig = str(p.get("trigger") or "")
        for i, op in enumerate(ops):
            if not isinstance(op, dict) or op.get("done") or op.get("sense") == "dispatch":
                continue
            owner = str(op.get("owner") or "").strip()
            if not (owner.lower() in ("memo", "assistant") or owner.startswith("watch:")):
                continue                  # only the assistant or a goal worker; not operator, other persons, Blurp/self
            wid = "%s#watch%d" % (ent.eid, i)
            nodes.append({"id": wid, "kind": "watch", "owner": owner, "status": "open", "goal": None,
                          "title": "%s watches %s%s" % (owner, p.get("name") or ent.eid,
                                   (": " + str(p["escalate_when"])) if p.get("escalate_when") else ""),
                          "escalate_when": p.get("escalate_when"), "watching": ent.eid,
                          "triggers": trig or None, "set_by": owner, "last_fired": op.get("last_fired") or op.get("ts")})
            edges.append({"from": wid, "to": ent.eid, "rel": "watches"})
            if trig:
                edges.append({"from": wid, "to": trig, "rel": "triggers"})
    return nodes, edges


def work_graph(est) -> dict:
    """THE ONE GRAPH CALL (k4): {nodes: [goals, tasks, decisions], edges}. A node is
    {id, kind: goal|task|decision, title, status, goal}; an edge is {from, to, rel}:
    ``blocks`` (a task's, wait's or card's blocks list) and ``of`` (-> its goal). Unfinished
    Wait nodes (kind ``wait``) are included; finished ones and gone entities are left out.
    Every goal and task carries ``waiting_on``: [{id, kind, title, reason}] -- unfinished
    waits, open cards, unfinished blocker tasks, stuck or failed tasks (goalgraph helpers).
    The drawing reads only this."""
    from .entities import CLASS_DECISION, CLASS_GOAL
    from . import goalgraph as GG
    kinds = {CLASS_GOAL: "goal", "Task": "task", CLASS_DECISION: "decision", GG.WAIT: "wait"}
    nodes, raw, docs = [], [], {}
    for ent in list((getattr(est, "docs", None) or {}).values()):
        p = ent.properties or {}
        kind = kinds.get(ent.cls)
        if not kind or p.get("merged_into"):
            continue
        if kind == "wait" and not GG._unfinished(ent):
            continue                      # only unfinished waits are drawn
        docs[ent.eid] = ent
        nodes.append({"id": ent.eid, "kind": kind, "title": p.get("title") or p.get("name") or ent.eid,
                      "status": _status(p) if kind == "decision" else p.get("status", "open"),
                      "goal": p.get("goal") if kind != "goal" else None})
        if kind != "goal":
            for t in (p.get("blocks") if isinstance(p.get("blocks"), list) else []):
                raw.append({"from": ent.eid, "to": str(t), "rel": "blocks"})
            if p.get("goal"):
                raw.append({"from": ent.eid, "to": str(p["goal"]), "rel": "of"})
    ids = {n["id"] for n in nodes}
    idx = GG.blockers_index(est)                       # one pass each, never a scan per node
    by_goal: dict = {}
    for d in docs.values():
        by_goal.setdefault((d.properties or {}).get("goal"), []).append(d)
    for n in nodes:
        if n["kind"] == "goal":
            n["waiting_on"] = _goal_waiting_on(docs, n["id"], GG, by_goal)
        elif n["kind"] == "task":
            n["waiting_on"] = [_wait_on(b, "unfinished " + ("wait" if b.cls == GG.WAIT else
                               "blocker task" if b.cls == GG.TASK else "decision"))
                               for b in GG._blockers_of(est, n["id"], idx)
                               if b.eid in docs and GG._unfinished(b)]
    wnodes, wedges = _watch_nodes(est)
    nodes.extend(wnodes)
    nodes.sort(key=lambda n: (("goal", "task", "wait", "decision", "watch").index(n["kind"]), n["title"]))
    return {"nodes": nodes, "edges": [e for e in raw if e["from"] in ids and e["to"] in ids] + wedges,
            "runs": _finished_runs(est) if rocket_runs_on() else [],
            "rocket": rocket_runs_on()}


def rocket_runs_on() -> bool:
    """#884: the rocket view of finished runs is OFF by default; MEMBRANE_ROCKET_RUNS=1 turns it on
    (nothing is deleted -- the run results stay in the store, only the feed and the drawing are gated)."""
    import os
    return os.environ.get("MEMBRANE_ROCKET_RUNS", "").strip().lower() in ("1", "true", "yes", "on")


ROCKET_MAX_NODES = 12     # nodes kept per run's miniature graph (default until the operator answers the cap card)
ROCKET_MAX_RUNS = 12      # the operator's cap on command graphs in flight (card: 12 for now)


def _finished_runs(est) -> list:
    """#843: finished code_run / method-call results (existing result entities, no new store), newest
    ROCKET_MAX_RUNS, newest first: {id, root, rc, ts, text, head, nodes: [{id, parent}]} -- the root's
    node tree (its ``args`` children, recursively) for the rocket view's tiny flying graph."""
    from .entities import MEMBRANE_NS
    docs = getattr(est, "docs", None) or {}
    pre = MEMBRANE_NS + "result:"
    res = []
    for eid, ent in list(docs.items()):
        p = ent.properties or {}
        if str(eid).startswith(pre) and p.get("root") and p.get("ts") is not None:
            res.append(ent)
    res.sort(key=lambda e: -float((e.properties or {}).get("ts") or 0))
    out = []
    for ent in res[:ROCKET_MAX_RUNS]:
        p = ent.properties or {}
        nodes, seen, todo = [], set(), [(str(p["root"]), None)]
        while todo and len(nodes) < ROCKET_MAX_NODES:
            nid, par = todo.pop(0)
            if nid in seen:
                continue
            seen.add(nid)
            nodes.append({"id": nid, "parent": par})
            kid = docs.get(nid)
            for c in ((kid.properties or {}).get("args") if kid is not None else None) or []:
                if isinstance(c, str):
                    todo.append((c, nid))
        out.append({"id": ent.eid, "root": str(p["root"]), "rc": p.get("rc"), "ts": float(p.get("ts") or 0),
                    "text": str(p.get("text") or "")[:2000], "head": str(p.get("head") or "")[:2000],
                    "tail": str(p.get("tail") or "")[:2000], "nodes": nodes})
    return out


def entity_history(srv, spec: str) -> dict:
    """THE ONE HISTORY CALL (k5, the Logs tab): one entity's current properties
    (goal-processing receipts live there), its field changes (deltas, oldest
    first) and its relations (live and superseded) -- {ok, eid, cls, properties,
    changes, relations}."""
    est = getattr(srv, "entity_store", None)
    ent = est._resolve_spec(spec) if est is not None and spec else None
    if ent is None:
        return {"ok": False, "error": f"no entity matches {spec!r}"}
    ef = getattr(srv, "efield", None)
    changes = []
    if ef is not None:
        try:
            changes = [dict(x) for x in ef.deltas_of(ent.eid)]
        except Exception:
            changes = []
    rels = []
    be, origin = getattr(ef, "b", None), getattr(ef, "origin", "")
    if be is not None:
        try:
            from . import relations as R
            rels = R.history_of(be, origin, ent.eid)
        except Exception:
            rels = []
    props = {k: v for k, v in (ent.properties or {}).items() if not str(k).startswith("script_")}
    return {"ok": True, "eid": ent.eid, "cls": ent.cls, "properties": props,
            "changes": changes, "relations": rels}


def _item(srv, spec: str):
    est = getattr(srv, "entity_store", None)
    return est, (est._resolve_spec(spec) if est is not None and spec else None)


def item_chat(srv, spec: str) -> dict:
    """k7, the Chat tab: the messages (convgraph's Message entities) that -refers-to-> the
    item, oldest first -- {ok, eid, messages: [{id, who, text, ts}]}."""
    est, ent = _item(srv, spec)
    if ent is None:
        return {"ok": False, "error": f"no entity matches {spec!r}"}
    ef = getattr(srv, "efield", None)
    be, origin = getattr(ef, "b", None), getattr(ef, "origin", "")
    msgs = []
    if be is not None:
        from . import relations as R
        for r in R.of(be, origin, ent.eid, "refers-to"):
            m = (est.docs or {}).get(r.get("subj")) if r.get("obj") == ent.eid else None
            if m is not None:
                p = m.properties or {}
                msgs.append({"id": m.eid, "who": p.get("speaker") or "", "text": p.get("text", ""),
                             "ts": float(p.get("ts") or 0)})
    msgs.sort(key=lambda m: m["ts"])
    return {"ok": True, "eid": ent.eid, "messages": msgs}


def item_say(srv, spec: str, text: str, who: str = "") -> dict:
    """k7: say something in an item's mini-chat -- a Message entity -refers-to-> the item
    (the same entity and edge a chat turn makes)."""
    est, ent = _item(srv, spec)
    text = str(text or "").strip()
    if ent is None:
        return {"ok": False, "error": f"no entity matches {spec!r}"}
    if not text:
        return {"ok": False, "error": "empty message"}
    import time
    import uuid
    from . import convgraph as CG
    ef = getattr(srv, "efield", None)
    be, origin = getattr(ef, "b", None), getattr(ef, "origin", "")
    mid = "item-" + uuid.uuid4().hex[:12]
    eid = CG._mint(est, "item:" + ent.eid, mid, "user", text, ts=time.time(), speaker=who or None)
    CG._edge(be, origin, eid, "refers-to", ent.eid, source="item-chat")
    return {"ok": True, "message": eid}
