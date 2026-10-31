"""GOALS AS A GRAPH (#738, the operator 2026-10-30): a PLANNER splits each goal into
DECISIONS (Decision cards; they escalate to the operator) and TASKS (Task entities;
a separate WORKER pool runs the ready ones). The graph's machinery is CLIFFHANGER:
the triggers below, seeded once like tool:clock -- no Python scheduling loop. The
planning judgment is a model call made from cliffhanger (a trigger writes
`membrane::llm.call`).

THE GRAPH
- The goal: the existing Goal entity, the root.
- Tasks: `task:<goal>:<key>` entities (class Task) with `goal`, `title`, `detail`,
  `status` and `open_blockers` (their count of unfinished blockers).
- Edges: a blocker's `blocks` list (the Decision cards' existing edge key; a task's
  too), mirrored as `blocks` relation rows for the graph's readers. A plan whose
  edges close a cycle is refused whole.
- Waits (#773): `wait:<goal>:<key>` entities (class Wait) with a `condition` -- a field
  property equal to a value ({field, prop, equals}) or a method that passes ({method,
  args}) -- and the planner's `every_s` and `deadline_min`. A wait arms when what it
  follows is done, the `every` trigger (WAIT_TICK_S) writes its `check` adapter branch and
  the check runs each every_s; it is done when the condition holds -- the tasks it blocks
  count it like a finished task -- and fails after deadline_min: the failure is escalated
  into the chat (#729's route), which starts the model's turn there.
- Task state, the code trees' executor pattern (#614): open -> owed (-execute->
  tool:workers) -> running (-executing->) -> done / failed / stuck, with
  -result-> a result entity carrying the worker's receipt (a failure is one too).

THE CASCADE (each step a write firing the next trigger -- never a loop):
  a blocker's status becomes done (a task) or closed / dropped (a card)
    -> each task it blocks: open_blockers - 1                (unblock-by-task / -card)
  open_blockers reaches 0 on an open task -> status owed     (ready)
    -> the adapter links it to the worker pool; a worker takes it, writes its result
  a task ends (done / failed / stuck), a card is answered or dropped, a goal is
  planted -> the goal's `replan` -> the planner's llm.call    (replan-on-*, plan)

THE PLAN is the planner's one write: `<goal>.plan = {tasks: [{key, title, detail,
after: [keys or eids]}], cards: [{key, name, summary, var, pick, blocks: [keys]}],
waits: [{key, title, condition, every_s, deadline_min, after: [keys], blocks: [keys]}],
drop: [keys]}` -- an adapter branch (effects are writes, #576 E) applies it: tasks and
cards are made or updated, edges set, cycles refused, open_blockers counted, every new
card escalated into the operator's latest chat (#729's route). The receipt lands in
`plan_result`.

The planner and worker turns each start a NEW chain (writes.source): the loop rule
refuses a trigger already in its chain, so a goal's second task finishing in the chain
of its first would never re-plan.

CONSTANTS (approved by the operator 2026-10-30): WORKERS = 3; no cap on tasks per goal or
on graph depth; the planner runs on the loops' model pick (#708)."""
from __future__ import annotations

import hashlib
import json
import re
import time

from .entities import CLASS_COMPONENT, MEMBRANE_NS, QuantumEntity

TASK = "Task"
TASK_NS = MEMBRANE_NS + "task:"
WORKERS_EID = MEMBRANE_NS + "tool:workers"
WORKERS = 3                 # the worker pool's size (approved by the operator 2026-10-30, #738)
PLANNERS = 2                # the planner lane: two goals planned at once, never one goal twice (operator 2026-10-30)
OPEN, OWED, RUNNING, DONE, FAILED, STUCK = "open", "owed", "running", "done", "failed", "stuck"
ENDED = (DONE, FAILED, STUCK)
DROPPED = "dropped"
TASK_ENDED = (DONE, DROPPED)   # no longer blocking its dependents
CARD_ENDED = ("closed", "dropped")
EXECUTE, EXECUTING, RESULT, BLOCKS = "execute", "executing", "result", "blocks"
# #773: the third node class. A wait blocks tasks until its condition holds; it arms
# when what it follows is done, is checked on the `every` trigger, and fails past its
# deadline (the planner replans then). The planner chooses each wait's `every_s` and
# `deadline_min` (operator, 2026-10-30); WAIT_TICK_S is only the trigger's beat, the
# shortest interval a wait can ask for.
WAIT = "Wait"
WAIT_NS = MEMBRANE_NS + "wait:"
ARMED = "armed"
WAIT_TICK_S = 10
SOURCE = "goal-graph"
LANE_PLANNER, LANE_WORKER = "planner", "worker"

# ---- the machinery: cliffhanger triggers (seeded once; readable in the Code tab) ----------------
TRIGGERS = [
    ("goal-graph unblock by task",
     [["changed", "?b.status"]],
     [["isa", "?b", TASK], ["isa", "?t", TASK],
      ["filter", "b.status == 'done' and t.status == 'open' and t.eid in (b.blocks or [])"]],
     ["?t.open_blockers", "t.open_blockers - 1"]),
    ("goal-graph unblock by card",
     [["changed", "?b.status"]],
     [["isa", "?b", "Decision"], ["isa", "?t", TASK],
      ["filter", "b.status == 'closed' and t.status == 'open' and t.eid in (b.blocks or [])"]],
     ["?t.open_blockers", "t.open_blockers - 1"]),
    ("goal-graph ready",
     [["changed", "?t.open_blockers"], ["below", "t.open_blockers", 0.5]],
     [["isa", "?t", TASK], ["filter", "t.status == 'open'"]],
     ["?t.status", "'owed'"]),
    ("goal-graph replan on task end",
     [["changed", "?t.status"]],
     [["isa", "?t", TASK], ["isa", "?g", "Goal"],
      ["filter", "g.eid == t.goal and (t.status == 'done' or t.status == 'failed' or t.status == 'stuck')"]],
     ["?g.replan", "'task ' + t.eid + ' ' + t.status"]),
    ("goal-graph replan on card",
     [["changed", "?d.status"]],
     [["isa", "?d", "Decision"], ["isa", "?g", "Goal"],
      ["filter", "g.eid == d.goal and (d.status == 'closed' or d.status == 'dropped')"]],
     ["?g.replan", "'card ' + d.eid + ' ' + d.status"]),
    # #773: the waits
    ("goal-graph unblock by wait",
     [["changed", "?b.status"]],
     [["isa", "?b", WAIT], ["isa", "?t", TASK],
      ["filter", "b.status == 'done' and t.status == 'open' and t.eid in (b.blocks or [])"]],
     ["?t.open_blockers", "t.open_blockers - 1"]),
    ("goal-graph wait follows a task",
     [["changed", "?b.status"]],
     [["isa", "?b", TASK], ["isa", "?w", WAIT],
      ["filter", "b.status == 'done' and w.status == 'open' and w.eid in (b.blocks or [])"]],
     ["?w.open_blockers", "w.open_blockers - 1"]),
    ("goal-graph wait follows a card",
     [["changed", "?b.status"]],
     [["isa", "?b", "Decision"], ["isa", "?w", WAIT],
      ["filter", "b.status == 'closed' and w.status == 'open' and w.eid in (b.blocks or [])"]],
     ["?w.open_blockers", "w.open_blockers - 1"]),
    ("goal-graph wait armed",
     [["changed", "?w.open_blockers"], ["below", "w.open_blockers", 0.5]],
     [["isa", "?w", WAIT], ["filter", "w.status == 'open'"]],
     ["?w.status", "'armed'"]),
    ("goal-graph wait check",
     [["every", WAIT_TICK_S]],
     [["isa", "?w", WAIT], ["filter", "w.status == 'armed'"]],
     ["?w.check", "'due'"]),
    ("goal-graph plan",
     [["changed", "?g.replan"]],
     [["isa", "?g", "Goal"], ["filter", "(g.status or 'open') == 'open' and g.replan"]],
     [MEMBRANE_NS + "llm.call", "{'about': g.eid, 'prompt': 'plan the goal: ' + g.replan, 'lane': 'planner'}"]),
]


def seed(srv) -> int:
    """Plant tool:workers and define the triggers (idempotent: define overwrites the same
    name). -> how many triggers are defined."""
    est = srv.entity_store
    if (est.docs or {}).get(WORKERS_EID) is None:
        est.upsert(QuantumEntity(eid=WORKERS_EID, cls=CLASS_COMPONENT, properties={
            "name": "tool:workers", "summary": "the worker pool: ready goal tasks run here (#738)",
            "executor": True, "silent_birth": True}), embedder=None)
    vm = getattr(srv, "ch_vm", None)
    if vm is None:
        return 0
    for name, when, where, write in TRIGGERS:
        vm.define(name, when, where, write)
    return len(TRIGGERS)


# ---- identity ----------------------------------------------------------------------------------
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", str(s).lower()).strip("-")[:48] or "t"


def _gid(goal: str) -> str:
    return hashlib.sha1(goal.encode()).hexdigest()[:8]


def task_eid(goal: str, key: str) -> str:
    return f"{TASK_NS}{_gid(goal)}:{_slug(key)}"


def is_task(eid: str) -> bool:
    return str(eid or "").startswith(TASK_NS)


def wait_eid(goal: str, key: str) -> str:
    return f"{WAIT_NS}{_gid(goal)}:{_slug(key)}"


def is_wait(eid: str) -> bool:
    return str(eid or "").startswith(WAIT_NS)


def _rel(srv):
    from .night import _relations_backend_of
    try:
        return _relations_backend_of(srv)
    except Exception:
        return None, ""


def _edge(srv, subj: str, rel: str, obj: str, receipt: dict | None = None, drop: bool = False) -> None:
    be, origin = _rel(srv)
    if be is None:
        return
    from . import relations as R
    if drop:
        from .night import _supersede_edge
        _supersede_edge(be, origin, subj, obj, rel=rel, receipt=receipt or {"op": "done"})
    else:
        R.upsert(be, origin, subj, rel, obj, SOURCE, ts=time.time())


# ---- the plan (the planner's one write) ---------------------------------------------------------
def _blockers_of(est, task: str) -> list:
    return [d for d in list((est.docs or {}).values())
            if task in ((d.properties or {}).get("blocks") or [])]


def _unfinished(d) -> bool:
    """Still blocking: a task until done or dropped (a dropped task's dependents are the
    planner's to re-plan, never stuck behind it), a card until closed or dropped."""
    st = str((d.properties or {}).get("status") or "open")
    return st not in (TASK_ENDED if d.cls in (TASK, WAIT) else CARD_ENDED)


def _cycle(edges: dict) -> list | None:
    """A cycle in {node: [nodes it blocks]}, or None."""
    color: dict = {}

    def visit(n, path):
        color[n] = 1
        for m in edges.get(n, ()):
            if color.get(m) == 1:
                return path + [n, m]
            if not color.get(m):
                c = visit(m, path + [n])
                if c:
                    return c
        color[n] = 2
        return None
    for n in list(edges):
        if not color.get(n):
            c = visit(n, [])
            if c:
                return c
    return None


def apply_plan(srv, goal: str, plan) -> dict:
    """Apply the planner's plan to the goal's graph. -> {ok, tasks, cards, ...} or
    {ok: False, error} (nothing written)."""
    est = srv.entity_store
    g = (est.docs or {}).get(goal)
    if g is None:
        return {"ok": False, "error": f"no goal {goal}"}
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except ValueError as e:
            return {"ok": False, "error": f"the plan is not JSON: {e}"}
    if not isinstance(plan, dict):
        return {"ok": False, "error": "a plan is {tasks: [...], cards: [...], drop: [...]}"}
    tasks = [t for t in plan.get("tasks") or [] if isinstance(t, dict) and t.get("key")]
    cards = [c for c in plan.get("cards") or [] if isinstance(c, dict) and c.get("key")]
    waits = [w for w in plan.get("waits") or [] if isinstance(w, dict) and w.get("key")]
    for w in waits:
        why = _wait_error(w)
        if why:
            return {"ok": False, "error": f"wait {w['key']}: {why}"}
    keys = {str(t["key"]): task_eid(goal, str(t["key"])) for t in tasks}
    wkeys = {str(w["key"]): wait_eid(goal, str(w["key"])) for w in waits}
    card_keys = {str(c["key"]): None for c in cards}
    for c in cards:
        ex = [d for d in (est.docs or {}).values() if d.cls == "Decision"
              and (d.properties or {}).get("goal") == goal and (d.properties or {}).get("plan_key") == str(c["key"])]
        card_keys[str(c["key"])] = ex[0].eid if ex else None

    def ref(x: str) -> str | None:
        x = str(x)
        if x in keys:
            return keys[x]
        if x in wkeys:
            return wkeys[x]
        if x in card_keys:
            return card_keys[x] or f"card:{x}"
        if (est.docs or {}).get(task_eid(goal, x)) is not None:
            return task_eid(goal, x)               # a task key of an earlier plan
        if (est.docs or {}).get(wait_eid(goal, x)) is not None:
            return wait_eid(goal, x)               # a wait key of an earlier plan
        return x if (est.docs or {}).get(x) is not None else None

    # the edges this plan wants: blocker -> [tasks]
    want: dict = {}
    for t in tasks:
        for a in t.get("after") or []:
            b = ref(a)
            if b is None:
                return {"ok": False, "error": f"task {t['key']}: unknown 'after' {a!r} (a key of this plan or an eid)"}
            want.setdefault(b, []).append(keys[str(t["key"])])
    for c in cards:
        for k in c.get("blocks") or []:
            t = ref(k)
            if t is None or not is_task(t):
                return {"ok": False, "error": f"card {c['key']}: blocks {k!r} is not a task of this goal"}
            want.setdefault(card_keys[str(c["key"])] or f"card:{c['key']}", []).append(t)
    for w in waits:
        weid = wkeys[str(w["key"])]
        for a in w.get("after") or []:
            b = ref(a)
            if b is None:
                return {"ok": False, "error": f"wait {w['key']}: unknown 'after' {a!r} (a key of this plan or an eid)"}
            want.setdefault(b, []).append(weid)
        for k in w.get("blocks") or []:
            t = ref(k)
            if t is None or not is_task(t):
                return {"ok": False, "error": f"wait {w['key']}: blocks {k!r} is not a task of this goal"}
            want.setdefault(weid, []).append(t)
    graph: dict = {}
    for d in list((est.docs or {}).values()):
        if d.cls in (TASK, WAIT) and (d.properties or {}).get("goal") == goal:
            graph[d.eid] = list((d.properties or {}).get("blocks") or [])
    for b, ts in want.items():
        graph.setdefault(b, [])
        graph[b] = sorted(set(graph[b]) | set(ts))
    cyc = _cycle(graph)
    if cyc:
        return {"ok": False, "error": "refused: these edges close a cycle: " + " -> ".join(cyc)}
    # tasks: made or updated (a running or ended task keeps its state)
    made = []
    for t in tasks:
        eid = keys[str(t["key"])]
        d = (est.docs or {}).get(eid)
        p = {"name": str(t.get("title") or t["key"])[:120], "title": str(t.get("title") or t["key"])[:200],
             "detail": str(t.get("detail") or "")[:2000], "goal": goal, "plan_key": str(t["key"])}
        if d is None:
            est.upsert(QuantumEntity(eid=eid, cls=TASK, properties={**p, "status": OPEN, "blocks": [],
                                                                    "silent_birth": True}), embedder=None)
            made.append(eid)
        else:
            again = (d.properties or {}).get("status") in (STUCK, FAILED)    # #777: listed again = retried
            # retried: open again, its blocker count re-counted so `ready` fires afresh
            d.properties = {**(d.properties or {}), **p,
                            **({"status": OPEN, "open_blockers": None} if again else {})}
            est.upsert(d, embedder=None)
    # waits: made or updated (an armed or ended wait keeps its state)
    made_waits = []
    for w in waits:
        eid = wkeys[str(w["key"])]
        d = (est.docs or {}).get(eid)
        p = {"name": str(w.get("title") or w["key"])[:120], "title": str(w.get("title") or w["key"])[:200],
             "condition": w["condition"], "every_s": float(w["every_s"]),
             "deadline_min": float(w["deadline_min"]), "goal": goal, "plan_key": str(w["key"])}
        if d is None:
            est.upsert(QuantumEntity(eid=eid, cls=WAIT, properties={**p, "status": OPEN, "blocks": [],
                                                                    "silent_birth": True}), embedder=None)
            made_waits.append(eid)
        else:
            d.properties = {**(d.properties or {}), **p}
            est.upsert(d, embedder=None)
    # cards: made (and escalated) or kept
    new_cards = []
    for c in cards:
        k = str(c["key"])
        if card_keys[k] is None:
            r = est.add(cls="Decision", name=str(c.get("name") or k)[:120], summary=str(c.get("summary") or "")[:300])
            if not r.get("ok"):
                return {"ok": False, "error": f"card {k}: {r.get('error')}", "tasks": made}
            card_keys[k] = r["eid"]
            new_cards.append(r["eid"])
            props = {"status": "open", "goal": goal, "plan_key": k, "work_needed": False}
            for f in ("var", "pick"):
                if c.get(f) is not None:
                    props[f] = c[f]
            e = est.edit(r["eid"], properties=props)
            if not e.get("ok") or e.get("properties_refused"):
                return {"ok": False, "error": f"card {k}: {e.get('error') or e.get('properties_refused')}",
                        "tasks": made, "cards": new_cards}
    # edges: on the blocker (its `blocks`), mirrored as relation rows
    for b, ts in want.items():
        beid = card_keys.get(b[5:]) if b.startswith("card:") else b
        d = (est.docs or {}).get(beid)
        if d is None:
            continue
        old = list((d.properties or {}).get("blocks") or [])
        new = old + [x for x in ts if x not in old]
        if new != old:
            d.properties = {**(d.properties or {}), "blocks": new}
            est.upsert(d, embedder=None)
        for x in ts:
            _edge(srv, beid, BLOCKS, x)
    dropped = []
    for k in plan.get("drop") or []:
        eid = keys.get(str(k)) or wkeys.get(str(k)) or task_eid(goal, str(k))
        if (est.docs or {}).get(eid) is None:
            eid = wait_eid(goal, str(k))
        d = (est.docs or {}).get(eid)
        # a stuck or failed node drops too: its dependents must never wait on it for good
        if d is not None and (d.properties or {}).get("status") in (OPEN, OWED, ARMED, STUCK, FAILED):
            d.properties = {**(d.properties or {}), "status": DROPPED}
            est.upsert(d, embedder=None)
            dropped.append(eid)
    recount(srv, goal)                  # the counts, last (each write may fire `ready`)
    g = est.docs[goal]
    g.properties = {**(g.properties or {}), "planned_ts": time.time()}
    est.upsert(g, embedder=None)
    for c in new_cards:
        _escalate_card(srv, goal, c)
    return {"ok": True, "tasks": made, "cards": new_cards, "waits": made_waits, "dropped": dropped}


def recount(srv, goal: str) -> int:
    """Each open task of the goal: open_blockers = its blockers still unfinished. Run after
    every plan AND every planner turn (one that writes no plan too: a card dropped under an
    UNCHANGED planner must not leave its tasks blocked for good). -> tasks re-counted."""
    est = srv.entity_store
    n_changed = 0
    for d in list((est.docs or {}).values()):
        if d.cls not in (TASK, WAIT) or (d.properties or {}).get("goal") != goal \
                or (d.properties or {}).get("status") != OPEN:
            continue
        n = sum(1 for b in _blockers_of(est, d.eid) if _unfinished(b))
        if (d.properties or {}).get("open_blockers") != n:
            d.properties = {**(d.properties or {}), "open_blockers": n}
            est.upsert(d, embedder=None)
            n_changed += 1
    return n_changed


def _escalate_card(srv, goal: str, card: str) -> None:
    """A decision the graph waits on goes to the operator's latest chat (#729's route)."""
    try:
        from . import watch_agents as WA
        est = srv.entity_store
        d = est.docs[card]
        name = (d.properties or {}).get("name") or card
        WA.escalate(srv, {"kind": "decision-needed", "entity": card, "goal": goal, "title": name,
                          "ts": time.time()}, card, f"goal {goal} waits on this decision: {name}")
        d.properties = {**(d.properties or {}), "escalated_ts": time.time()}
        est.upsert(d, embedder=None)
    except Exception:
        pass


def plan_wanted(srv, goal: str, why: str) -> None:
    """A goal asks its planner (the plant hook: a goal born or re-pinned)."""
    est = srv.entity_store
    g = (est.docs or {}).get(goal)
    if g is None:
        return
    g.properties = {**(g.properties or {}), "replan": f"{why} at {time.strftime('%H:%M:%S')}"}
    est.upsert(g, embedder=None)


# ---- the worker pool -----------------------------------------------------------------------------
def owe(srv, task: str) -> bool:
    """A ready task goes to the worker pool: -execute-> tool:workers + its pool event."""
    est = srv.entity_store
    d = (est.docs or {}).get(task)
    if d is None or (d.properties or {}).get("status") != OWED:
        return False
    _edge(srv, task, EXECUTE, WORKERS_EID)
    pool = getattr(srv, "events_pool", None)
    if pool is None:
        return False
    pool.put({"kind": "task", "entity": task, "goal": (d.properties or {}).get("goal"), "ts": time.time(),
              "event_id": f"task-{int(time.time() * 1000)}"})
    return True


def take(srv, task: str) -> bool:
    """A worker takes an owed task: running, -executing->."""
    est = srv.entity_store
    d = (est.docs or {}).get(task)
    if d is None or (d.properties or {}).get("status") != OWED:
        return False
    _edge(srv, task, EXECUTE, WORKERS_EID, receipt={"op": "taken"}, drop=True)
    _edge(srv, task, EXECUTING, WORKERS_EID)
    d.properties = {**(d.properties or {}), "status": RUNNING, "started_ts": time.time()}
    est.upsert(d, embedder=None)
    return True


_VERDICT = re.compile(r"^\s*(DONE|FAILED|STUCK)\b[:\s-]*(.*)$", re.I | re.M)


def has_verdict(text: str) -> bool:
    """The worker's turn ended with a DONE / FAILED / STUCK line."""
    return bool(_VERDICT.findall(str(text or "")))


def parse_worker(text: str) -> tuple[str, str]:
    """The worker's last verdict line: (done | failed | stuck, its detail). No line: stuck."""
    hits = _VERDICT.findall(str(text or ""))
    if not hits:
        return STUCK, "the worker ended without DONE / FAILED / STUCK: " + str(text or "").strip()[-200:]
    v, detail = hits[-1]
    return v.lower(), detail.strip()[:600]


def finish(srv, task: str, verdict: str, detail: str, session: str = "") -> str | None:
    """The worker's result onto the task: a result entity (-result->), and the status
    the triggers react to. -> the result's eid."""
    est = srv.entity_store
    d = (est.docs or {}).get(task)
    if d is None:
        return None
    verdict = verdict if verdict in ENDED else STUCK
    p = d.properties or {}
    n = int(p.get("runs") or 0) + 1
    res = f"{MEMBRANE_NS}result:{task[len(MEMBRANE_NS):].replace(':', '-')}:{n}"
    est.upsert(QuantumEntity(eid=res, cls=CLASS_COMPONENT, properties={
        "name": f"{verdict} {p.get('title') or task}"[:80], "task": task, "goal": p.get("goal"),
        "outcome": verdict, "receipt": str(detail or "")[:2000], "session": session, "ts": time.time(),
        "silent_birth": True}), embedder=None)
    _edge(srv, task, EXECUTING, WORKERS_EID, receipt={"op": verdict, "why": str(detail)[:160]}, drop=True)
    _edge(srv, task, RESULT, res)
    d.properties = {**p, "runs": n, "result": res, "receipt": str(detail or "")[:600], "status": verdict,
                    "ended_ts": time.time()}
    est.upsert(d, embedder=None)
    return res


def _wait_error(w: dict) -> str | None:
    """Why a planned wait can't be made, or None: its condition, and the planner's
    choice of how often to check (every_s) and when to give up (deadline_min)."""
    why = _condition_error(w.get("condition"))
    if why:
        return why
    try:
        every, deadline = float(w.get("every_s")), float(w.get("deadline_min"))
    except (TypeError, ValueError):
        return "a wait needs every_s (how often to check, seconds) and deadline_min (when to give up)"
    if every < WAIT_TICK_S:
        return f"every_s is at least {WAIT_TICK_S}"
    if deadline <= 0:
        return "deadline_min is above 0"
    return None


def _condition_error(cond) -> str | None:
    """Why a wait's condition can't be checked, or None."""
    if not isinstance(cond, dict):
        return "a wait needs a condition: {field, prop, equals} or {method, args}"
    if cond.get("method"):
        if "." not in str(cond["method"]).strip(".") or not isinstance(cond.get("args", {}), dict):
            return "a method condition is {method: '<object>.<method>', args: {...}}"
        return None
    if cond.get("field") and cond.get("prop") and "equals" in cond:
        return None
    return "a wait needs a condition: {field, prop, equals} or {method, args}"


def _condition_holds(srv, cond: dict) -> tuple[bool, str]:
    """(holds, what was seen) for a wait's condition."""
    est = srv.entity_store
    if cond.get("method"):
        from . import night as N
        obj, _, name = str(cond["method"]).rpartition(".")
        why = N.direct_exec_refusal(est, obj, name)     # the executor: refused as on the adapter's route
        if why:
            return False, f"{cond['method']} refused: {why}"
        r = N.method_call(srv, est, getattr(srv, "efield", None), getattr(srv, "membrane", None),
                          obj, name, dict(cond.get("args") or {}))
        rc = r.get("rc") if isinstance(r, dict) and r.get("rc") is not None else \
            (0 if isinstance(r, dict) and r.get("ok", True) and r.get("status", "ok") == "ok" else 1)
        head = str((r.get("head") or r.get("error") or "") if isinstance(r, dict) else r)[:300]
        return rc == 0, f"{cond['method']} rc {rc}: {head}"
    ent = est.resolve(str(cond["field"]), exact=True)
    if ent is None:
        return False, f"no entity {cond['field']}"
    seen = (ent.properties or {}).get(str(cond["prop"]))
    return seen == cond.get("equals"), f"{ent.eid}.{cond['prop']} = {seen!r}"


def check_wait(srv, wait: str, now: float | None = None) -> str:
    """One check of an armed wait (the `goal-graph wait check` trigger's effect): done when
    its condition holds, failed past its deadline, else still armed. -> the receipt."""
    est = srv.entity_store
    d = (est.docs or {}).get(wait)
    if d is None or d.cls != WAIT or (d.properties or {}).get("status") != ARMED:
        return "not an armed wait"
    now = float(now if now is not None else time.time())
    p = dict(d.properties or {})
    if now - float(p.get("last_check_ts") or 0) < float(p.get("every_s") or WAIT_TICK_S):
        return ""                                # not due: its own interval, the planner's choice
    p.setdefault("armed_ts", now)
    try:
        ok, seen = _condition_holds(srv, p.get("condition") or {})
    except Exception as e:                       # noqa: BLE001 -- a broken check is a check that failed
        ok, seen = False, f"the check raised {type(e).__name__}: {e}"[:300]
    p.update({"last_check_ts": now, "last_check_result": seen})
    if ok:
        p.update({"status": DONE, "receipt": seen, "ended_ts": now})
    elif now - float(p["armed_ts"]) >= 60 * float(p.get("deadline_min") or 0):
        p.update({"status": FAILED, "receipt": f"unmet after {p.get('deadline_min')} min: {seen}",
                  "ended_ts": now})
    d.properties = p
    est.upsert(d, embedder=None)
    if p.get("status") == FAILED:
        _escalate_wait(srv, d)
    elif p.get("status") == DONE and p.get("goal"):
        report(srv, p["goal"])                   # a wait that ends the graph ends it in the chat
    return seen


def _escalate_wait(srv, d) -> None:
    """A failed wait goes to the chat (#729's route, as a card does): one message that
    starts the model's turn there (operator, 2026-10-30)."""
    try:
        from . import watch_agents as WA
        p = d.properties or {}
        title = p.get("title") or d.eid
        WA.escalate(srv, {"kind": "wait-failed", "entity": d.eid, "goal": p.get("goal"), "title": title,
                          "ts": time.time()}, d.eid,
                    f"goal {p.get('goal')}: the wait '{title}' failed -- {p.get('receipt')}")
    except Exception:
        pass


def replan_lost(srv) -> int:
    """Boot: an open goal whose last replan no planner pass finished (cut off by a
    restart, or its call lost with the pool) is planned again."""
    from . import llm as LLM
    n = 0
    for d in list((srv.entity_store.docs or {}).values()):
        p = d.properties or {}
        if d.cls != "Goal" or (p.get("status") or "open") != "open" or not p.get("replan"):
            continue
        if p.get("planned_replan") == p["replan"]:
            continue
        LLM.request(srv, "plan the goal: " + str(p["replan"]), about=d.eid, caller="boot",
                    extra={"lane": LANE_PLANNER})
        n += 1
    return n


_FULFILLED = re.compile(r"^\s*FULFILLED\b[:\s-]*(.*)$", re.I | re.M)


def planner_claim(text: str) -> str | None:
    """The planner's FULFILLED receipt (the last such line), or None."""
    hits = _FULFILLED.findall(str(text or ""))
    return hits[-1].strip()[:600] if hits else None


def _moving(d) -> bool:
    """A node that will still change by itself: a task open, owed or running, a wait open or
    armed, a card still open."""
    st = str((d.properties or {}).get("status") or OPEN)
    if d.cls == TASK:
        return st in (OPEN, OWED, RUNNING)
    if d.cls == WAIT:
        return st in (OPEN, ARMED)
    return st not in CARD_ENDED


def report(srv, goal: str, claim: str | None = None) -> bool:
    """EVERY PLAN ENDS IN THE CHAT (operator, 2026-10-31: "force all plans to end with
    turn-starting messages into chat"): a goal whose graph has nothing left moving, or
    whose planner says FULFILLED, is told to the chat once, with its receipts; the chat
    turn decides (close it, or send it back). The goal stays open. -> told."""
    est = srv.entity_store
    g = (est.docs or {}).get(goal)
    if g is None or ((g.properties or {}).get("status") or OPEN) != OPEN:
        return False
    nodes = [d for d in list((est.docs or {}).values())
             if (d.properties or {}).get("goal") == goal and d.cls in (TASK, WAIT, "Decision")]
    if claim is None and (not nodes or any(_moving(d) for d in nodes)):
        return False
    sig = hashlib.sha1(json.dumps(sorted([d.eid, str((d.properties or {}).get("status"))] for d in nodes)
                                  + [claim or ""]).encode()).hexdigest()[:16]
    if (g.properties or {}).get("reported_graph") == sig:
        return False
    rows = []
    for d in sorted(nodes, key=lambda d: d.eid):
        p = d.properties or {}
        kind = {TASK: "task", WAIT: "wait"}.get(d.cls, "card")
        what = p.get("receipt") or p.get("value") or ""
        rows.append(f"- {kind} {p.get('title') or p.get('name') or d.eid}: {p.get('status')}"
                    + (f" -- {' '.join(str(what).split())[:200]}" if what else ""))
    head = (f"its planner says it is done -- {claim}" if claim is not None
            else "its graph has nothing left moving")
    line = (f"goal {goal} ({(g.properties or {}).get('name') or goal}): {head}.\n" + "\n".join(rows)[:1500]
            + "\nCheck the receipts against what is live now (the PR head, its CI); close the goal "
            "(goal action='close') with the receipt, or send it back (entity_edit its replan with what "
            "is missing).")
    try:
        from . import watch_agents as WA
        told = WA.escalate(srv, {"kind": "goal-ended", "entity": goal, "goal": goal,
                                 "title": (g.properties or {}).get("name") or goal, "ts": time.time()},
                           goal, line)
    except Exception:
        return False
    if told:
        g.properties = {**(g.properties or {}), "reported_graph": sig, "reported_ts": time.time()}
        est.upsert(g, embedder=None)
    return bool(told)


def reowe(srv) -> int:
    """Boot: a task owed or running when the seat stopped goes back to the pool."""
    est = srv.entity_store
    n = 0
    for d in list((est.docs or {}).values()):
        if d.cls == TASK and (d.properties or {}).get("status") in (OWED, RUNNING):
            d.properties = {**(d.properties or {}), "status": OWED}
            est.upsert(d, embedder=None)
            n += owe(srv, d.eid)
    return n


# ---- reading -------------------------------------------------------------------------------------
def view(est, goal: str) -> dict:
    """The goal's graph: its tasks (what each waits on, its state, its receipt) and cards."""
    tasks, cards, waits = [], [], []
    docs = list((est.docs or {}).values())
    for d in docs:
        p = d.properties or {}
        if p.get("goal") != goal:
            continue
        if d.cls == TASK:
            tasks.append({"eid": d.eid, "title": p.get("title"), "status": p.get("status"),
                          "waits_on": [b.eid for b in _blockers_of(est, d.eid) if _unfinished(b)],
                          "open_blockers": p.get("open_blockers"), "result": p.get("result"),
                          "receipt": p.get("receipt")})
        elif d.cls == WAIT:
            waits.append({"eid": d.eid, "title": p.get("title"), "status": p.get("status"),
                          "condition": p.get("condition"), "blocks": p.get("blocks") or [],
                          "waits_on": [b.eid for b in _blockers_of(est, d.eid) if _unfinished(b)],
                          "last_check": p.get("last_check_result"), "receipt": p.get("receipt")})
        elif d.cls == "Decision":
            cards.append({"eid": d.eid, "name": p.get("name"), "status": p.get("status"),
                          "blocks": p.get("blocks") or [], "value": p.get("value")})
    return {"tasks": tasks, "cards": cards, "waits": waits,
            "running": [t["eid"] for t in tasks if t["status"] == RUNNING],
            "ready": [t["eid"] for t in tasks if t["status"] == OWED]}


# ---- the lanes' texts ------------------------------------------------------------------------------
PLANNER_TEXT = """You are the PLANNER for goal {goal}: {text}
Why you were called: {why}

The goal's graph now:
{graph}

Work the goal as a graph. Write ONE plan with entity_edit on the goal:
  entity_edit spec='{goal}' properties={{"plan": {{"tasks": [{{"key": "k1", "title": "...", "detail": "what to do and the receipt that proves it", "after": ["<key or eid it waits on>"]}}], "cards": [{{"key": "c1", "name": "The question (no quotes)", "summary": "...", "var": {{"type": "option", "options": ["a", "b"]}}, "pick": "a", "blocks": ["k1"]}}], "waits": [{{"key": "w1", "title": "...", "condition": {{"field": "<entity spec>", "prop": "<name>", "equals": "<value>"}} or {{"method": "<object>.<method>", "args": {{}}}}, "every_s": 60, "deadline_min": 60, "after": ["k1"], "blocks": ["k2"]}}], "drop": ["<key no longer needed>"]}}}}
A task is one worker turn of concrete work; listing a stuck or failed task's key again runs it again, and dropping it (drop: [key]) frees what it blocked. A choice only the operator can make is a card that blocks the tasks needing it (it is escalated to the operator's chat for you). Something that takes time outside a turn (CI going green, a deploy, a reply) is a wait: it arms when what it follows is done, is checked every every_s seconds (at least {tick_s}; a field property equal to a value, or a method that passes), releases the tasks it blocks when it holds, and fails after deadline_min minutes (the failure goes to the chat). Choose both for what it waits on: CI takes minutes, a person's reply hours. Never make a task wait inside its turn. Keep existing keys to update a task; a running or done task keeps its state. Read the entity back for plan_result.
When every task is done and the receipts prove the goal, end with FULFILLED and the receipt. Every plan ends in the chat: when nothing in the graph is moving any more, the chat is told with the receipts, and its turn closes the goal or sends it back; you never close a goal. When the graph needs nothing new, write no plan.
End with one line: PLANNED <what changed> / FULFILLED <receipt> / UNCHANGED <why>."""

WORKER_TEXT = """You are a WORKER on task {task} of goal {goal}.
Task: {title}
{detail}

What it waited on is done: {done}

Do this task now, with your tools, to the end of this turn. Do not plan other work.
End with one line: DONE <the receipt that proves it> / FAILED <what went wrong> / STUCK <what you need: a decision or other work>."""


def _graph_lines(est, goal: str) -> str:
    v = view(est, goal)
    out = [f"- task {t['eid']} [{t['status']}] {t['title']}"
           + (f" (waits on {', '.join(t['waits_on'])})" if t["waits_on"] else "")
           + (f" -- receipt: {str(t['receipt'])[:160]}" if t.get("receipt") else "") for t in v["tasks"]]
    out += [f"- wait {w['eid']} [{w['status']}] {w['title']}"
            + (f" (waits on {', '.join(w['waits_on'])})" if w["waits_on"] else "")
            + (f" -- {str(w.get('receipt') or w.get('last_check') or '')[:160]}" if (w.get("receipt") or w.get("last_check")) else "")
            for w in v["waits"]]
    out += [f"- card {c['eid']} [{c['status']}] {c['name']}" + (f" = {c['value']}" if c.get("value") is not None else "")
            for c in v["cards"]]
    return "\n".join(out) or "(empty: nothing planned yet)"


def planner_text(est, goal: str, why: str) -> str:
    g = (est.docs or {}).get(goal)
    p = (g.properties or {}) if g is not None else {}
    text = p.get("text") or p.get("summary") or p.get("name") or goal
    return PLANNER_TEXT.format(goal=goal, text=str(text)[:1200], why=str(why)[:400], graph=_graph_lines(est, goal),
                               tick_s=WAIT_TICK_S)


def worker_text(est, task: str) -> str:
    d = (est.docs or {}).get(task)
    p = (d.properties or {}) if d is not None else {}
    done = [f"{b.eid}: {str((b.properties or {}).get('receipt') or (b.properties or {}).get('value') or '')[:160]}"
            for b in _blockers_of(est, task)]
    return WORKER_TEXT.format(task=task, goal=p.get("goal"), title=p.get("title") or task,
                              detail=p.get("detail") or "", done="; ".join(done) or "nothing")

