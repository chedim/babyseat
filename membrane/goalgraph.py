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
drop: [keys]}` -- an adapter branch (effects are writes, #576 E) applies it: tasks and
cards are made or updated, edges set, cycles refused, open_blockers counted, every new
card escalated into the operator's latest chat (#729's route). The receipt lands in
`plan_result`.

The planner and worker turns each start a NEW chain (writes.source): the loop rule
refuses a trigger already in its chain, so a goal's second task finishing in the chain
of its first would never re-plan.

CONSTANTS for the operator (listed on the PR): WORKERS. No cap on tasks per goal or on
depth is set."""
from __future__ import annotations

import hashlib
import json
import re
import time

from .entities import CLASS_COMPONENT, MEMBRANE_NS, QuantumEntity

TASK = "Task"
TASK_NS = MEMBRANE_NS + "task:"
WORKERS_EID = MEMBRANE_NS + "tool:workers"
WORKERS = 3                 # the worker pool's size (operator: 3, 2026-10-30, #738)
PLANNERS = 1                # the planner lane (#738: one)
OPEN, OWED, RUNNING, DONE, FAILED, STUCK = "open", "owed", "running", "done", "failed", "stuck"
ENDED = (DONE, FAILED, STUCK)
CARD_ENDED = ("closed", "dropped")
EXECUTE, EXECUTING, RESULT, BLOCKS = "execute", "executing", "result", "blocks"
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
    st = str((d.properties or {}).get("status") or "open")
    return st not in ((DONE,) if d.cls == TASK else CARD_ENDED)


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
    keys = {str(t["key"]): task_eid(goal, str(t["key"])) for t in tasks}
    card_keys = {str(c["key"]): None for c in cards}
    for c in cards:
        ex = [d for d in (est.docs or {}).values() if d.cls == "Decision"
              and (d.properties or {}).get("goal") == goal and (d.properties or {}).get("plan_key") == str(c["key"])]
        card_keys[str(c["key"])] = ex[0].eid if ex else None

    def ref(x: str) -> str | None:
        x = str(x)
        if x in keys:
            return keys[x]
        if x in card_keys:
            return card_keys[x] or f"card:{x}"
        if (est.docs or {}).get(task_eid(goal, x)) is not None:
            return task_eid(goal, x)               # a task key of an earlier plan
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
    graph: dict = {}
    for d in list((est.docs or {}).values()):
        if d.cls == TASK and (d.properties or {}).get("goal") == goal:
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
        eid = keys.get(str(k)) or task_eid(goal, str(k))
        d = (est.docs or {}).get(eid)
        if d is not None and (d.properties or {}).get("status") in (OPEN, OWED):
            d.properties = {**(d.properties or {}), "status": "dropped"}
            est.upsert(d, embedder=None)
            dropped.append(eid)
    # the counts, last (each write may fire `ready`)
    for d in list((est.docs or {}).values()):
        if d.cls != TASK or (d.properties or {}).get("goal") != goal or (d.properties or {}).get("status") != OPEN:
            continue
        n = sum(1 for b in _blockers_of(est, d.eid) if _unfinished(b))
        if (d.properties or {}).get("open_blockers") != n:
            d.properties = {**(d.properties or {}), "open_blockers": n}
            est.upsert(d, embedder=None)
    g = est.docs[goal]
    g.properties = {**(g.properties or {}), "planned_ts": time.time()}
    est.upsert(g, embedder=None)
    for c in new_cards:
        _escalate_card(srv, goal, c)
    return {"ok": True, "tasks": made, "cards": new_cards, "dropped": dropped}


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
    tasks, cards = [], []
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
        elif d.cls == "Decision":
            cards.append({"eid": d.eid, "name": p.get("name"), "status": p.get("status"),
                          "blocks": p.get("blocks") or [], "value": p.get("value")})
    return {"tasks": tasks, "cards": cards,
            "running": [t["eid"] for t in tasks if t["status"] == RUNNING],
            "ready": [t["eid"] for t in tasks if t["status"] == OWED]}


# ---- the lanes' texts ------------------------------------------------------------------------------
PLANNER_TEXT = """You are the PLANNER for goal {goal}: {text}
Why you were called: {why}

The goal's graph now:
{graph}

Work the goal as a graph. Write ONE plan with entity_edit on the goal:
  entity_edit spec='{goal}' properties={{"plan": {{"tasks": [{{"key": "k1", "title": "...", "detail": "what to do and the receipt that proves it", "after": ["<key or eid it waits on>"]}}], "cards": [{{"key": "c1", "name": "The question (no quotes)", "summary": "...", "var": {{"type": "option", "options": ["a", "b"]}}, "pick": "a", "blocks": ["k1"]}}], "drop": ["<key no longer needed>"]}}}}
A task is one worker turn of concrete work. A choice only the operator can make is a card that blocks the tasks needing it (it is escalated to the operator's chat for you). Keep existing keys to update a task; a running or finished task keeps its state. Read the entity back for plan_result.
When every task is done and the receipts prove the goal, close it with fulfill and the receipt. When the graph needs nothing new, write no plan.
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
    out += [f"- card {c['eid']} [{c['status']}] {c['name']}" + (f" = {c['value']}" if c.get("value") is not None else "")
            for c in v["cards"]]
    return "\n".join(out) or "(empty: nothing planned yet)"


def planner_text(est, goal: str, why: str) -> str:
    g = (est.docs or {}).get(goal)
    p = (g.properties or {}) if g is not None else {}
    text = p.get("text") or p.get("summary") or p.get("name") or goal
    return PLANNER_TEXT.format(goal=goal, text=str(text)[:1200], why=str(why)[:400], graph=_graph_lines(est, goal))


def worker_text(est, task: str) -> str:
    d = (est.docs or {}).get(task)
    p = (d.properties or {}) if d is not None else {}
    done = [f"{b.eid}: {str((b.properties or {}).get('receipt') or (b.properties or {}).get('value') or '')[:160]}"
            for b in _blockers_of(est, task)]
    return WORKER_TEXT.format(task=task, goal=p.get("goal"), title=p.get("title") or task,
                              detail=p.get("detail") or "", done="; ".join(done) or "nothing")

