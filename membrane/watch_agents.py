"""THE REACTIVE WATCH LAYER (the operator's 2026-10-02 design):
membrane is the LLM's ONLY I/O tool -- the model perceives the world
through entity state, watch events, and recall, and acts through the
MCP lane. This module is the always-watched lane's brain:

- THE OPERATOR ENTITY: planted at boot (idempotent, like tool:clock),
  fires on EVERY inbound message (the facts stamp + the position EMA
  toward the message's sentence vec -- zero embed calls, the vec is
  already computed); its loop IS the main chat (no separate agent
  session for the operator's own words).
- THE WATCH OPS: per-entity ``watch_ops`` -- {'op': 'away'|'near',
  'rel': '<eid>'|'self'|'operator', 'y': distance|'z': distance}:
  away = the entity's distance from rel GREW beyond y (a reece event,
  fires on the CROSsing), near = the distance SHRANK below z (an
  approach event, fires on the crossing), around = rel '*' -- ANY live
  entity within z (the sensor's hearing; a burst GROUPS into one
  event: entered/left lists, the 3000-neighbors flood is one event).
  Every op carries a hysteresis: after a fire the watch sleeps until
  the geometry actually CHANGES (a farther/other crossing), so a
  hovering entity never spams.
- THE EVENT QUEUE: drop-oldest cap (64; a flood keeps the NEWEST --
  the stale geometry is not worth reacting to) + grouped batches.
- THE LOOP RUNNER: 3 concurrent non-operator thinking loops + 1
  dedicated operator lane (the operator NEVER starves: its admit is
  unconditional). Each loop = one thinking turn against the seat's
  own /v1 (the gate, recall, MCP tools, capture all run -- the loop
  is a REAL turn, nothing special-cased except its session/agent and
  its task text); the model REACTS (MCP tool calls) OR IGNORES (a
  plain verdict text); the verdict stamps the entity
  (last_decision).
- THE FRONTEND SURFACES: loop sessions are real child sessions whose
  AGENT NAME carries the processed entity's id ('watch:
  f:membrane/server.py' -- the operator's ask); the /agent list +
  background-subagents capability + the banner 'loops:' line are the
  render.

Kill switch: MEMBRANE_WATCH_LOOPS=0 -- the loops never spawn (the
fires still land: the queue + the banner report them reactively
later); MEMBRANE_OPERATOR=0 -- the operator entity never fires.
"""

from __future__ import annotations

import json
import os
import threading
import re
import time
from collections import namedtuple

import numpy as np

from . import efield as EF
from .efstore import h16
from .entities import QuantumEntity

OPERATOR_EID = "membrane::operator"
DEFAULT_OP_Y = 0.40
DEFAULT_OP_Z = 0.25
COOLDOWN_S = 30.0           # a fired op's minimum silence
EVENT_MAX = 64              # the queue's drop-oldest cap


def enabled_loops() -> bool:
    return os.environ.get("MEMBRANE_WATCH_LOOPS", "1") not in ("0", "false")


def enabled_operator() -> bool:
    return os.environ.get("MEMBRANE_OPERATOR", "1") not in ("0", "false")


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


# ---- the operator entity ----------------------------------------------------
def plant_operator(est, facts: dict | None = None) -> bool:
    """Plant (or refresh) the ALWAYS-WATCHED operator entity. Idempotent
    (an existing doc survives -- like the clock plant); properties carry
    the message counters + the ops (the operator's own watch_ops: the
    near-sense of its world)."""
    eid = OPERATOR_EID
    ent = est.docs.get(eid)
    if ent is None:
        from .entities import QuantumEntity
        ent = QuantumEntity(eid=eid, cls="operator" if "operator" in
                            getattr(EF, "CLASSES", []) else "Component",
                            properties={"name": "operator"})
        est.docs[eid] = ent
    props = ent.properties or {}
    props.setdefault("messages", 0)
    props.setdefault("always_watched", True)
    props.setdefault("watch_ops", [{"op": "near", "rel": "*", "z": 0.12}])
    if facts:
        props.update(facts)
    est.upsert(ent, embedder=None)
    return True


def operator_on_message(est, text: str, vec=None, embedder=None,
                        queue: "WatchEvents | None" = None) -> dict:
    """THE OPERATOR'S TICK: one inbound message. The facts stamp lands
    (messages += 1, last_text head, last_text_ts) and the position EMA
    drifts toward the message's sentence vector (the vec arrives free:
    the field's apply computed it); the queue gets the event (the main
    chat IS the reaction -- the loop is the turn itself)."""
    if not enabled_operator():
        return {"fired": False, "reason": "MEMBRANE_OPERATOR=0"}
    ent = est.docs.get(OPERATOR_EID)
    if ent is None:
        plant_operator(est)
        ent = est.docs.get(OPERATOR_EID)
    props = ent.properties or {}
    props["messages"] = int(props.get("messages") or 0) + 1
    props["last_text"] = " ".join(str(text or "").split())[:160]
    props["last_text_ts"] = time.time()
    if vec is None and embedder is not None and text:
        try:
            vec = embedder.embed([text])[0]
        except Exception:
            vec = None
    if vec is not None and np.size(vec):
        v = _unit(vec)
        if ent.position is None:
            ent.position = list(map(float, v))
        else:
            old = _unit(ent.position)
            ent.position = [float(x) for x in _unit(0.6 * old + 0.4 * v)]
    est.upsert(ent, embedder=None)
    if queue is not None:
        queue.put({"event_id": f"op-{int(props['last_text_ts'] * 1000)}",
                   "kind": "operator-message",
                   "entity": OPERATOR_EID,
                   "text_head": props["last_text"],
                   "ts": props["last_text_ts"]})
    return {"fired": True, "messages": props["messages"]}


# ---- the watch ops sweep ----------------------------------------------------
def _props_of(est, eid: str) -> dict:
    ent = est.docs.get(eid) or (est.find(eid) if hasattr(est, "find")
                               else None)
    if ent is None:
        # the eid may live under the full namespace form
        for k in list(est.docs):
            if k.endswith(eid):
                ent = est.docs[k]
                break
    if ent is None:
        return {}
    return ent.properties or {}


def _entity_positions(est) -> dict:
    """eid -> unit position for every live positioned entity (one
    snapshot per sweep)."""
    out = {}
    for eid, ent in list(est.docs.items()):
        pos = getattr(ent, "position", None)
        if pos and len(pos) >= 2:
            out[eid] = _unit(pos)
    return out


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    """The distance metric on the unit sphere: the CHORD (1 - cos) --
    monotone in the angle, stable when either position is un-normalized
    upstream (a malformed position degrades to 1.0 = 'far', never a
    crash)."""
    if a is None or b is None or not len(a) or not len(b) \
            or len(a) != len(b):
        return 1.0
    an, bn = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if an < 1e-12 or bn < 1e-12:
        return 1.0
    return float(1.0 - float(np.dot(a, b) / (an * bn)))


def _group_distance(entity_pos, rel_pos) -> float:
    return _dist(entity_pos, rel_pos)


def _rel_eid(est, rel: str, positions: dict) -> str | None:
    """A watch op's `rel`, any spelling -> the positioned entity it names
    (the resolver; the old suffix match missed 'm:'/lowercase/'f:x' and
    picked an arbitrary entity for a bare word)."""
    if rel in positions:
        return rel
    try:
        hit = est.resolve(rel, exact=True)
    except Exception:
        hit = None
    if hit is not None and hit.eid in positions:
        return hit.eid
    return None


def sweep_watch_ops(est, queue: "WatchEvents", rel_positions: dict,
                    now: float | None = None,
                    cooldown_s: float | None = None) -> list[dict]:
    """ONE sweep across every entity's watch_ops. Positions arrive as
    eid -> unit vec (the operator + any positioned rel); the sweep per
    op:
    - away:  dist(eid, rel) - op's armed baseline >= y -> FIRE (the
      entity MOVED OUT) + cool-down;
    - near:  dist(eid, rel) <= z -> fire on the CROSSING (was outside
      at the last sweep);
    - around (rel '*'): any OTHER entity within z -> the burst GROUPS:
      one event {entered: [eids], left: [eids], n: count} per sensor.
    State per (eid, op): the last distances + the fired flags ride the
    entity doc (watch_state) -- persistence rides the entity.
    """
    now = float(now if now is not None else time.time())
    cd = float(cooldown_s if cooldown_s is not None else COOLDOWN_S)
    positions = _entity_positions(est)
    positions.update(rel_positions or {})
    fired: list[dict] = []
    grouped_sensors: dict[str, dict] = {}
    for eid, ent in list(est.docs.items()):
        props = ent.properties or {}
        ops = props.get("watch_ops") or []
        if not ops or eid not in positions:
            continue
        my_pos = positions[eid]
        state = dict(props.get("watch_state") or {})
        for op in ops[:8]:
            if not op.get("armed"):
                continue                        # dormant: planted before the sweep ran; re-plant to arm
            kind = op.get("op") or ""
            rel = op.get("rel") or "operator"
            rel_eid = (OPERATOR_EID if rel == "operator"
                       else eid if rel == "self" else
                       _rel_eid(est, rel, positions))
            if kind in ("away", "near"):
                rel_pos = positions.get(rel_eid) \
                    if rel != "*" else None
                if rel_pos is None:
                    continue
                d = _dist(my_pos, rel_pos)
                st = dict(state.get(f"{kind}:{rel_eid}") or {})
                y = float(op.get("y") or DEFAULT_OP_Y)
                z = float(op.get("z") or DEFAULT_OP_Z)
                last_d = float(st.get("last_d", d))
                if kind == "away":
                    crossed = (d - last_d) >= y and \
                        (now - float(st.get("last_fire", 0.0)) >= cd)
                else:
                    crossed = (d <= z and last_d > z and
                               now - float(st.get("last_fire", 0.0))
                               >= cd)
                if crossed:
                    ev = {"event_id": f"{kind}-{eid}-{int(now * 1000)}",
                          "kind": kind, "entity": eid,
                          "rel": rel_eid, "distance": round(d, 4),
                          "prev": round(last_d, 4), "ts": now}
                    fired.append(ev)
                    queue.put(ev)
                    try:
                        est.fire_watch(eid, ev)
                    except Exception:
                        pass
                    try:
                        from .fieldpulse import of as _pulse_of
                        _fp = _pulse_of(getattr(est, "_srv", None))
                        if _fp is not None:
                            _fp.watch(eid, kind)
                    except Exception:
                        pass
                    st["last_fire"] = now
                st["last_d"] = d
                state[f"{kind}:{rel_eid}"] = st
            elif kind == "around":
                z = float(op.get("z") or 0.12)
                within = sorted(
                    other for other, other_pos in positions.items()
                    if other != eid and _dist(my_pos, other_pos) <= z)
                st = dict(state.get(f"around") or {})
                prev_within = set(st.get("within") or [])
                entered = [e for e in within if e not in prev_within]
                left = [e for e in prev_within if e not in set(within)]
                # THE GROUPING: a burst (3000 entries) = ONE event with
                # the entered/left lists + the counts; fire only when
                # something CHANGED (the static neighborhood is silence)
                if entered or left:
                    ev = {"event_id": f"around-{eid}-{int(now * 1000)}",
                          "kind": "around", "entity": eid,
                          "group": {"entered": entered[:64],
                                    "left": left[:64],
                                    "entered_n": len(entered),
                                    "left_n": len(left),
                                    "within_n": len(within)},
                          "ts": now}
                    fired.append(ev)
                    queue.put(ev)
                    try:
                        est.fire_watch(eid, ev)
                    except Exception:
                        pass
                    try:
                        from .fieldpulse import of as _pulse_of
                        _fp = _pulse_of(getattr(est, "_srv", None))
                        if _fp is not None:
                            _fp.watch(eid, kind)
                    except Exception:
                        pass
                    st["last_fire"] = now
                st["within"] = within[:256]
                state["around"] = st
        if state != dict(props.get("watch_state") or {}):
            props["watch_state"] = state
            est.upsert(ent, embedder=None)
    return fired


# ---- the event queue --------------------------------------------------------
class WatchEvents:
    """The watch fires' queue: put/ take under a lock, DROP-OLDEST at
    max (a flood keeps the newest -- stale geometry is not worth a
    reaction), GROUPED batches for the 'around' floods (the sweep
    groups; the queue's put sees one event)."""

    def __init__(self, max_: int = EVENT_MAX):
        self.max = max(1, int(max_))
        self._lk = threading.Lock()
        self._items: list[dict] = []

    def put(self, ev: dict) -> None:
        with self._lk:
            if len(self._items) >= self.max:
                self._items.pop(0)     # drop-OLDEST
            self._items.append(dict(ev))

    def take(self) -> list[dict]:
        with self._lk:
            out, self._items = self._items, []
        return out

    def depth(self) -> int:
        with self._lk:
            return len(self._items)


# ---- the loop runner --------------------------------------------------------
class LoopDesc:
    """One thinking loop's identity: the lane kind + the entity whose
    event it processes (the AGENT NAME carries it) + whether it is the
    dedicated operator lane."""

    def __init__(self, kind: str, eid: str = "",
                 is_operator: bool = False):
        self.kind = kind
        self.eid = eid
        self.is_operator = bool(is_operator)


def loop_agent_name(kind: str, eid: str = "") -> str:
    """THE AGENT-NAME CONTRACT: the frontend shows the processed
    entity's id IN the agent name ('watch: f:membrane/router.py').
    The title's tail IS the eid (no invented short-name)."""
    return f"{kind}{(': ' + eid) if eid else ''}"


# ---- the boot drainer + the loop turns -------------------------------------
def loop_agent_name(kind: str, eid: str = "") -> str:
    """THE AGENT-NAME CONTRACT: the frontend shows the processed
    entity's id IN the agent name ('watch: f:membrane/router.py').
    The title's tail IS the eid (no invented short-name)."""
    return f"{kind}{(': ' + eid) if eid else ''}"


def loop_task_text(ev: dict) -> str:
    """THE LOOP'S TASK: the event described + the react-or-ignore
    covenant (the model's choice IS the turn; tools via the membrane
    lane; a plain text verdict = ignore)."""
    kind = str(ev.get("kind") or "event")
    ent = str(ev.get("entity") or "?")
    if kind == "around":
        g = ev.get("group") or {}
        head = (f"watch event: '{ent}' sensed {g.get('entered_n', 0)} "
                f"entering and {g.get('left_n', 0)} leaving its "
                f"z-radius")
        detail = (f"entered: {g.get('entered', [])[:8]}"
                  f"{'' if len(g.get('entered') or []) <= 8 else ' ...'}"
                  f" left: {g.get('left', [])[:8]}")
    elif kind == "away":
        head = (f"watch event: '{ent}' moved {ev.get('distance', '?')} "
                f"away from {ev.get('rel', '?')} (was "
                f"{ev.get('prev', '?')})")
        detail = ""
    elif kind == "near":
        head = (f"watch event: '{ent}' closed in to "
                f"{ev.get('distance', '?')} of {ev.get('rel', '?')}")
        detail = ""
    elif kind == "peer-miss":
        head = (f"peer escalation missed: '{ev.get('peer', '?')}' had "
                f"nothing on {ev.get('situation', '')[:80]!r}")
        detail = ""
    elif kind == "script-error":
        head = (f"script error on '{ent}' ({ev.get('trigger', '?')}): "
                f"{ev.get('error', '')[:80]}")
        detail = ""
    elif kind == "script-cron-overrun":
        head = (f"cron overrun on '{ent}': ran {ev.get('ran_s', '?')}s "
                f"(interval {ev.get('interval_s', '?')}s); the next "
                "run was blocked")
        detail = ""
    elif kind == "watch-attached":
        head = f"watch attached: '{ent}' ({ev.get('lane', '?')})"
        detail = str(ev.get("detail") or "")
    elif kind == "watch-detached":
        head = f"watch detached: '{ent}' ({ev.get('lane', '?')})"
        detail = str(ev.get("detail") or "")
    elif kind == "llm-call":
        head = f"a model call ({ev.get('caller') or '?'}) about '{ent}':"
        detail = str(ev.get("prompt") or "")
        w = ev.get("wake")
        if isinstance(w, dict):
            # a WAKE (llm.py): the seat's own deadline came due
            _at = time.strftime("%a %H:%M", time.localtime(float(w.get("at") or 0)))
            _set = time.strftime("%a %H:%M", time.localtime(float(w.get("set_ts") or 0)))
            head = (f"YOUR WAKE {w.get('id', '')} ({ev.get('caller') or '?'}), set {_set} for {_at}"
                    + (" -- firing LATE (the seat was down or busy)" if w.get("late") else "")
                    + f", about '{ent}':")
            detail += "\nYou asked to be woken for this. Read the entity's state now before acting -- time has passed."
        if ev.get("readonly"):
            # a FIELD-CALL (llm.py): the field asks, read-only, one line back
            return (f"{head}\n{detail}\n"
                    "The field is asking (origin field-call): look with the read tools if you need to -- "
                    "you can't write or call again from here. Answer in ONE line: VERDICT -- what you found "
                    "or decided. It lands on the entity as llm_answer for its script's next fire.")
    elif kind == "restart-pending":
        due = time.strftime("%H:%M", time.localtime(float(ev.get("due") or 0)))
        head = (("REMINDER -- " if ev.get("reminder") else "") +
                f"a seat restart is pending (due {due}): {ev.get('why') or 'new code staged'}")
        detail = ("The engineer staged new code" + (f" ({', '.join(ev.get('files') or [])})" if ev.get("files") else "")
                  + "; it loads when the seat restarts, and the restart is yours. When nothing of yours is "
                  "mid-flight (a command, a write, a goal step), restart it: exec(action='call', "
                  "object='presence', method='restart', args={why: '<one line>'}). Need longer? Say STEP "
                  "with what you are finishing -- you will be reminded before it is due. Past due, the "
                  "engineer asks the operator.")
    elif kind == "operator-message":
        head = f"operator messaged: {ev.get('text_head', '')[:80]}"
        detail = ""
    elif kind == "intent-planted":
        goal = str(ev.get("text") or "")
        if ev.get("rewake"):
            head = f"goal still open (the board's pulse): {goal}"
            detail = f"goal {ev.get('entity', '?')} is still open."
        else:
            head = (f"goal planted ({'new' if ev.get('created') else 'pinned'}"
                    f"): {goal}")
            detail = (f"the board's open commitments steer your dreams and "
                      f"think-ahead; this one landed on "
                      f"{ev.get('entity', '?')}.")
        lands = ev.get("landings") or []
        if not lands and ev.get("last_landing"):
            lands = [{"op": "", "decision": ev.get("last_landing")}]
        if lands:
            detail += "\nYour last passes on it (oldest first):"
            for x in lands[-3:]:
                op = str(x.get("op") or "").upper() or "-"
                detail += f"\n  - {op}: {str(x.get('decision') or '')[:240]}"
            detail += "\nContinue from there -- do not redo landed steps."
        detail += ("\nWhen the goal is DONE and you hold the receipt "
                   "(a command's rc=0 output, the entity state read "
                   f"back), close it: `fulfill` with spec {ev.get('entity', '?')} "
                   "+ the evidence. Not done: take its NEXT concrete step. "
                   "Waiting on a person: say BLOCKED and it parks until "
                   "the operator speaks.")
    elif kind == "boundary":
        head = (f"boundary crossed: '{ev.get('entity', '?')}' "
                f"{'entered' if ev.get('entered') else 'exited'} "
                f"its {ev.get('boundary', '?')} "
                f"(count {ev.get('count', '?')})")
        detail = str(ev.get("detail") or "")
    elif kind == "job-done":
        # jobs.py: a job this loop started has finished
        head = f"your job #{ev.get('job', '?')} finished ({ev.get('what', '?')}, {ev.get('secs', '?')} s)" + \
               (f" -- about '{ent}'" if ent else "")
        detail = f"Its result:\n{str(ev.get('result') or '')[:1500]}"
    elif kind == "decision":
        # decision_map.answer: the operator answered this Decision card
        head = f"decided: {ev.get('title') or ent} = {ev.get('value', '?')} (card '{ent}')"
        if ev.get("work_needed"):
            head += (" -- it needs work: set its status to in_flight, do the work, then set its "
                     "receipt and status closed")
        detail = f"It blocks: {', '.join(map(str, ev['blocks']))}" if ev.get("blocks") else ""
    elif kind == "getter-applied":
        # WHAT CHANGED (2026-10-11 sweep: the loop was woken with an empty task)
        head = f"a reading changed on '{ent}' (its getter ran)"
        st = ev.get("state") if isinstance(ev.get("state"), dict) else {}
        detail = ("New values: " + ", ".join(f"{k}={str(v)[:40]}" for k, v in list(st.items())[:8])) if st else ""
    elif kind == "field:changed":
        head = (f"the field moved '{ent}': your watch_fields filter '{ev.get('verb', '?')}' crossed"
                + (" (it was frozen hot; this is the burst after the melt)" if ev.get("frozen") else ""))
        detail = f"{ev.get('n_changes', 1)} change(s) over {ev.get('span_s', 0)} s."
    else:
        head = f"watch event ({kind}) on '{ent}'"
        detail = ""
    # THE LAST PASSES (2026-10-11: each watch pass started fresh and redid
    # the same reads): what the earlier passes on this entity decided
    prev = ev.get("previous") or []
    if prev:
        detail += "\nYour last passes on it (oldest first):"
        for x in prev[-3:]:
            at = time.strftime("%H:%M", time.localtime(float(x.get("ts") or 0)))
            detail += f"\n  - {at} {str(x.get('verdict') or '-').upper()}: {str(x.get('decision') or '')[:240]}"
        detail += "\nContinue from there -- do not redo what landed; if this event changes nothing, ignore it."
    # CONSEQUENCES (consequences.py): what followed the earlier decisions here
    cons = ev.get("consequences") or []
    if cons:
        detail += "\nWhat followed your earlier decisions here (facts, oldest first):"
        for ln in cons[-3:]:
            detail += f"\n  - {str(ln)[:300]}"
        detail += "\nWeigh them before deciding again: a decision that didn't hold wants a different one."
    return (f"{head}\n{detail}\n"
            "Decide: REACT (one concrete step through the membrane tools "
            "-- find / entity(action='read') to look; entity add/edit, "
            "exec(action='link') to act -- re-link a command entity you "
            "already have before writing a new one; memory(action='learn'); "
            "goal(action='close')) or IGNORE. Budget: at most "
            f"{LOOP_TOOL_BUDGET} tool calls this pass; the pulse brings "
            "the rest back. Reply briefly (a few plain sentences, no "
            "essay) and END with exactly one verdict line:\n"
            "  DONE <the receipt>        -- finished (and fulfilled)\n"
            "  STEP <what moved>         -- progress, more to do\n"
            "  BLOCKED <on whom / what>  -- waiting: on the operator (default), "
            "'on <entity-id>' (wakes when it moves) or 'retry after 2h'\n"
            "  ignored: <reason>         -- nothing to do\n"
            "The field is your memory; this event is its push.")


_VERDICT_RE = re.compile(r"^(done|step|blocked|ignored)\b[\s:,.\-–—]*(.*)$",
                         re.IGNORECASE | re.DOTALL)
FULFILL_NUDGE = ("You ended with DONE, but goal {eid} is still OPEN on the "
                 "board, so the pulse would keep bringing it back. Call "
                 "goal(action='close', spec='{eid}', evidence=<the receipt>) now. "
                 "If it is not really done, end with STEP or BLOCKED instead.")


def _plain(line: str) -> str:
    return re.sub(r"[*_`>#~]+", "", line).strip().lstrip("-").strip()


def parse_verdict(text: str) -> tuple[str, str]:
    """(verdict, detail) from a loop turn's text: the LAST verdict-shaped
    line wins (markdown stripped), a leading 'ignored' still counts, no
    text at all is 'aborted' (the run died mid-tool), anything else is an
    unparsed 'reacted'."""
    lines = [_plain(x) for x in str(text or "").splitlines()]
    lines = [x for x in lines if x]
    if not lines:
        return "aborted", ""
    for line in reversed(lines[-3:]):
        m = _VERDICT_RE.match(line)
        if m:
            return m.group(1).lower(), m.group(2).strip()[:240]
    m = _VERDICT_RE.match(lines[0])
    if m and m.group(1).lower() == "ignored":
        return "ignored", m.group(2).strip()[:240]
    return "reacted", " ".join(lines)[-240:]


_WAKE = threading.local()                       # the running wake's turn facts, for its ledger line


class _LoopDrainer:
    """The queue's consumer: takes events (skipping the operator's --
    the main chat IS that lane's reaction), admits a loop worker, runs
    the loop turn, stamps the verdict. The runs are REAL turns against
    the seat's /v1 (run_loop_turn the boot wires)."""

    def __init__(self, srv, poll_s: float = 0.5):
        self.srv = srv
        self.poll_s = poll_s
        self.alive = True
        self._t: threading.Thread | None = None

    def start(self):
        self._t = threading.Thread(target=self._run, daemon=True,
                                   name="membrane-loops")
        self._t.start()
        return self

    def stop(self):
        self.alive = False

    def _run(self):
        # THE POLL'S SLEEP (the 100%-idle-CPU exhibit, named at LAST):
        # take() returns instantly and the loop had NO sleep anywhere
        # -- the drainer spun 100% of a core on an empty pool since
        # the day it was born (every seat, the whole arc). The poll's
        # cadence rides the ambient pulse: day 0.5s, night x4 (the
        # watcher's ear is slightly slower in sleep, that's the point).
        _night_x = 4.0
        try:
            from .thinker import NIGHT_SWEEP_X
            _night_x = float(NIGHT_SWEEP_X)
        except Exception:
            pass
        while self.alive:
            evs = []
            pool = getattr(self.srv, "events_pool", None)
            if pool is not None:
                evs = pool.take()
            _poll_s = self.poll_s
            try:
                if getattr(self.srv, "ambient_mode", "day") == "night":
                    _poll_s = self.poll_s * _night_x
            except Exception:
                pass
            time.sleep(_poll_s)
            for i, ev in enumerate(evs):
                if self.handle(ev) == "requeue":
                    # the pool is full: THIS event and the rest of the
                    # batch go back (the old break dropped the rest)
                    for rest in evs[i + 1:]:
                        pool.put(rest)
                    break

    def handle(self, ev: dict) -> str:
        """One event -> at most one loop turn (_handle), and its line in the
        wake ledger (wakes.py: outcome learning's training set)."""
        _WAKE.info = None
        feats = shadow = None
        if not is_operator_event(ev) and self._quota_park(ev):
            return "held"               # out of tokens: the event waits, unlogged
        if not is_operator_event(ev):
            try:                               # what the wake arrived with, BEFORE its turn stamps the entity
                from . import wakes as _WK
                feats = _WK.features(self.srv, ev, time.time())
            except Exception:
                feats = None
            try:                               # THE SHADOW GATE (outcomes.py): its guess, BLIND --
                from . import outcomes as _OC  # it never enters the task text; nothing is skipped
                shadow = _OC.predict(self.srv, ev)
            except Exception:
                shadow = None
        out = self._handle(ev)
        if shadow is not None:
            try:
                _OC.learn(self.srv, ev, shadow, out)
            except Exception:
                pass
        if feats is not None:
            try:
                info = getattr(_WAKE, "info", None) or {}
                if shadow is not None:
                    info = {**info, "shadow": {k: shadow.get(k) for k in ("predict", "p_ignore", "level", "n", "sig")}}
                _WK.log(self.srv, ev, "folded" if out == "skip" else out, feats=feats, **info)
            except Exception:
                pass
        return out

    def _quota_park(self, ev: dict, model: str | None = None) -> bool:
        """OUT OF TOKENS (quota.py): the Goal worker's model is paused (or
        `model`, the one a turn just ran out on) -- the event goes back to
        the pool and its goal stays open, named on the paused model's list
        (membrane home's ⚠). False: the model is free, run it."""
        try:
            from . import quota as _Q
            if model is None:
                ent = _Q.mind_paused(self.srv, "loops")
                if ent is None:
                    return False
                model = ent["model"]
            pool = getattr(self.srv, "events_pool", None)
            if pool is not None:
                pool.put(ev)
            eid = str(ev.get("entity") or "")
            est = getattr(self.srv, "entity_store", None)
            doc = (getattr(est, "docs", None) or {}).get(eid) if eid else None
            if doc is not None:
                _Q.park(model, eid, str((doc.properties or {}).get("name") or eid))
            return True
        except Exception:
            return False

    def _handle(self, ev: dict) -> str:
        """One event -> at most one loop turn. Returns 'skip', 'requeue'
        or the verdict."""
        # THE WAKE CALL: a watch fire is a real sound -- the night mind
        # opens its eyes before its next beat.
        try:
            from .thinker import note_wakeup_signal
            note_wakeup_signal(self.srv, str(ev.get("kind") or ""), ev)
        except Exception:
            pass
        if is_operator_event(ev):
            return "skip"           # the operator lane: the chat is it
        # OFFLINE (offline.py, a walk): no model to answer -- the event waits
        try:
            from . import offline as _OFF
            if _OFF.is_offline():
                pool = getattr(self.srv, "events_pool", None)
                if not ev.get("_held"):
                    ev = {**ev, "_held": True}
                    _OFF.note_held()
                if pool is not None:
                    pool.put(ev)
                return "held"
        except Exception:
            pass
        # THE ALERT BUDGET (2026-10-08): past the seat's dial the event
        # folds into the digest (the next turn's WHERE WE ARE) instead of
        # spending a whole interrupt turn
        gate = getattr(self.srv, "_alert_gate", None)
        if ev.get("kind") in ("restart-pending", "llm-call", "job-done"):
            gate = None                 # never folded into a digest: the restart is his; a call has its own caps
        if gate is not None and not gate.allow(ev):
            return "skip"
        loops = getattr(self.srv, "loops", None)
        pool = getattr(self.srv, "events_pool", None)
        if loops is None:
            return "skip"           # no pool wired: nothing to admit
        ent = str(ev.get("entity") or "")
        agent = loop_agent_name("field-call" if ev.get("readonly") else "watch", ent or ev.get("kind"))
        desc = LoopDesc("watch", eid=ent or ev.get("event_id", ""))
        holds = getattr(loops, "holds", None)
        if ent and callable(holds) and holds(desc.eid):
            if gate is not None:
                gate.refund()
            if pool is not None:
                pool.put(ev)        # its entity is in another loop's hands: it waits, alone
            return "held"
        if not loops.admit(desc):
            if gate is not None:
                gate.refund()       # re-queued, not run: no charge
            if pool is not None:
                pool.put(ev)
            return "requeue"
        from .fieldpulse import of as _pulse_of
        _fp = _pulse_of(self.srv)
        titles = getattr(loops, "titles", None)
        if isinstance(titles, list):
            titles.append(agent)
        if _fp is not None:
            _fp.focus(agent, ent, "start")
        try:
            return self._turn(ev, ent, agent)
        except Exception:
            return "error"
        finally:
            loops.release(desc)
            if isinstance(titles, list) and agent in titles:
                titles.remove(agent)
            if _fp is not None:
                _fp.focus(agent, ent, "end")

    def _turn(self, ev: dict, ent: str, agent: str) -> str:
        srv = self.srv
        sid = srv.create_loop_session(agent, agent)
        if str(ev.get("kind")) == "job-done":
            try:                                   # jobs.py: the woken pass carries its depth
                from . import jobs as _jobs
                _jobs.PENDING_DEPTH[sid] = int(ev.get("job_depth") or 1)
            except Exception:
                pass
        try:                                   # the orb carries its session (membrane home opens it)
            from .fieldpulse import of as _pulse_of
            _fp = _pulse_of(srv)
            if _fp is not None:
                _fp.loop_session(agent, ent, sid)
        except Exception:
            pass
        if est0 := getattr(srv, "entity_store", None):
            if str(ev.get("kind")) != "intent-planted" and ent:     # a goal carries its own landings
                doc = (est0.docs or {}).get(ent)
                if doc is not None and (doc.properties or {}).get("watch_decisions"):
                    ev = {**ev, "previous": list(doc.properties["watch_decisions"])}
            doc = (est0.docs or {}).get(ent) if ent else None
            if doc is not None and (doc.properties or {}).get("consequences"):
                from . import consequences as _CQ
                ev = {**ev, "consequences": _CQ.on_entity(doc)}
        _called = str(ev.get("kind")) == "llm-call"
        if _called:                             # llm.py: the depth rides the thread while the called turn runs
            from . import llm as _LLM
            _prev_depth = getattr(_LLM._ctx, "depth", 0)
            _LLM._ctx.depth = int(ev.get("depth") or 1)
        try:
            out = srv.run_loop_turn(sid, loop_task_text(ev), agent) or {}
        finally:
            if _called:
                _LLM._ctx.depth = _prev_depth
        if isinstance(out.get("quota"), dict):
            # the model ran out during the turn (quota.py): no verdict, the
            # goal stays open, the event waits for the model
            self._quota_park(ev, str(out["quota"].get("model") or ""))
            return "held"
        text = str(out.get("text") or out.get("verdict_text") or "")
        if _called:
            try:
                _LLM.answered(srv, ev, text, model=out.get("model"))
            except Exception:
                pass
        verdict, detail = parse_verdict(text)
        is_goal = str(ev.get("kind")) == "intent-planted"
        est = getattr(srv, "entity_store", None)
        goal = (est.docs or {}).get(ent) if (is_goal and est is not None) else None

        def _open() -> bool:
            return goal is not None and \
                ((goal.properties or {}).get("status") or "open") == "open"

        # DONE but the board still holds it: one nudge in the same
        # session (the review: 'Done -- armed' with no fulfill ran the
        # goal 6 more times)
        if verdict == "done" and _open():
            out2 = srv.run_loop_turn(sid, FULFILL_NUDGE.format(eid=ent),
                                     agent) or {}
            t2 = str(out2.get("text") or out2.get("verdict_text") or "")
            if t2.strip():
                text = text + "\n" + t2
                v2, d2 = parse_verdict(t2)
                if _open():
                    verdict, detail = (v2, d2) if v2 != "done" else ("done", detail)
        if verdict == "aborted":
            trail = [str(x)[:120] for x in (out.get("trail") or [])][-4:]
            detail = ("the pass ended mid-tool with no verdict"
                      + (f"; last tools: {' | '.join(trail)}" if trail else ""))
        decision = (detail or text.strip())[:240]
        if est is not None and ent:
            try:
                est.stamp_watch_decision(
                    ent, {"verdict": verdict, "decision": decision,
                          "event_id": ev.get("event_id"),
                          "session": sid, "ts": time.time()})
            except Exception:
                pass
            _WAKE.info = {"session": sid, "decision": decision[:160],
                          "tools": len(out.get("trail") or [])}
            if verdict in ("done", "step") and not ev.get("readonly"):
                try:                           # consequences.py: looked at again 1 h / 24 h / 72 h on
                    from . import consequences as _CQ
                    _CQ.record(srv, ent, decision, who=agent, session=sid, kind=verdict)
                except Exception:
                    pass
        if goal is not None:
            props = goal.properties or {}
            if verdict == "blocked":
                props["blocked_ts"] = time.time()
                props["blocked_on"] = decision
                props.pop("blocked_until", None)
                props.pop("blocked_entity", None)
                props.update(blocked_condition(decision))
            elif verdict in ("step", "done"):
                for k in ("blocked_ts", "blocked_on", "blocked_until", "blocked_entity"):
                    props.pop(k, None)
            goal.properties = props
            # THE GOAL'S CLAIM RECEIPT (the landing ledger the backoff
            # and the next pass read)
            try:
                from .opencode import nerve_paused
                if not nerve_paused(srv):
                    notify_intent_landed(
                        est, getattr(srv, "events_pool", None), ent,
                        {"op": verdict, "step": "loop-reaction",
                         "decision": decision, "session": sid})
            except Exception:
                try:
                    notify_intent_landed(est, None, ent,
                                         {"op": verdict, "step": "loop-reaction",
                                          "decision": decision, "session": sid})
                except Exception:
                    pass
        # THE INJECT: a pass that
        # MOVED something speaks one line into the operator's own session
        if verdict in ("done", "step", "blocked", "reacted") and decision:
            try:
                runner = getattr(srv, "_loop_runner", None)
                _inj = getattr(srv, "_inject_to_primary", None)
                if runner is not None and _inj is not None:
                    _inj(runner, "[event-thinker] "
                                 f"{ent or 'field'}: {verdict.upper()} {decision}")
            except Exception:
                pass
        return verdict


def start_loop_drainer(srv, poll_s: float = 0.5) -> _LoopDrainer:
    """The boot seam: the drainer thread consumes the events queue."""
    return _LoopDrainer(srv, poll_s=poll_s).start()


def is_operator_event(ev: dict) -> bool:
    """THE OPERATOR LANE TEST: the operator's fires never occupy a
    loop worker (the main chat's turn IS the reaction)."""
    return str((ev or {}).get("kind") or "") == "operator-message"


def loops_banner_line(srv) -> str | None:
    """THE BANNER'S LINE: 'loops: N running (titles), M queued' -- the
    ambient mind's load on every seat; None when nothing runs."""
    loops = getattr(srv, "loops", None)
    pool = getattr(srv, "events_pool", None)
    run = loops.running() if loops is not None else 0
    queued = pool.depth() if pool is not None else 0
    wake = ""
    try:                                       # llm.py: the seat's next self-scheduled wake
        from . import llm as _LLM
        nw = _LLM.next_wake(srv)
        if nw:
            wake = f"next wake {_LLM._hm(float(nw['at']), time.time())} on {str(nw.get('about') or '?').replace('membrane::', '')}"
    except Exception:
        pass
    if not run and not queued:
        return ("loops: idle, " + wake) if wake else None
    titles = []
    try:
        titles = (_loop_titles(srv) or [])[:3]
    except Exception:
        pass
    bits = [f"{run} running"]
    if titles:
        bits.append("(" + ", ".join(titles) + ")")
    if queued:
        bits.append(f"{queued} queued")
    if wake:
        bits.append(wake)
    return "loops: " + ", ".join(bits)


def _loop_titles(srv) -> list[str]:
    """The running loops' agent titles (the store keeps them when the
    boot records them)."""
    store = getattr(getattr(srv, "loops", None), "titles", None)
    if store is None:
        return []
    return [str(t) for t in store]


class LoopRunner:
    """The loop pool: 3 concurrent non-operator loops + 1 dedicated
    operator lane (operator admit NEVER fails -- the main chat's
    reactive lane cannot starve behind batch work).

    THE PAUSE (the operator's stable point, 2026-10-06): while
    srv.paused the LISTENER admits are refused (the events hold in the
    pool -- the drop-oldest cap is the flood covenant as always) and
    drain on resume; the operator lane is unaffected (the pause itself
    is the operator's channel)."""

    def __init__(self, max_loops: int = 3):
        self.max = max(1, int(max_loops))
        self._lk = threading.Lock()
        self._held: set[str] = set()       # running non-operator loop keys

    def admit(self, loop: LoopDesc) -> bool:
        if loop.is_operator:
            return True                    # dedicated: always available
        srv = getattr(self, "_srv", None)  # bare pools (tests) never pause
        if srv is not None:
            from .opencode import nerve_paused
            if nerve_paused(srv):
                return False               # the stable point: hold
        with self._lk:
            if len(self._held) >= self.max:
                return False
            key = loop.eid or loop.kind
            # ONE LOOP PER ENTITY (2026-10-11: the set admitted a second
            # loop on an entity already held, and the first release
            # un-held it under the second): it waits, and runs with the
            # first one's outcome in hand
            if loop.eid and key in self._held:
                return False
            self._held.add(key)
            return True

    def holds(self, eid: str) -> bool:
        with self._lk:
            return bool(eid) and eid in self._held

    def release(self, loop: LoopDesc) -> None:
        with self._lk:
            self._held.discard(loop.eid or loop.kind)

    def running(self) -> int:
        with self._lk:
            return len(self._held)

# ---- the attach/detach events + the watch_ops CRUD -------------------------
GOAL_TEXT_MAX = 1200            # the goal rides whole (it was cut to 120)
LOOP_TOOL_BUDGET = 8


def notify_intent_planted(est, pool, goal_eid: str, text: str,
                          created: bool = True, rewake: bool = False,
                          last_landing: str = "",
                          landings: list | None = None) -> dict:
    """THE INTENT-PLANTED EVENT (the receive-lane's wire, 2026-10-06):
    a commitment landed on the board -- the listener pool gets it as an
    event and a thinking loop RECEIVES it (its next turn's message:
    'goal planted: ... -- PLAN or IGNORE'). Every planter feeds this:
    the `intend` tool, the `membrane: intend that` frame, an entity
    script's {intent} verdict, AND THE BOOT'S re-derivation (a wake
    rides created=False -- the goal predates the boot; the board is
    the durable steering). The goal entity stamps the event's
    provenance (intent_planted_ts rides the goal's doc -- the boot's
    wakes never double-fire)."""
    ev = {"kind": "intent-planted", "entity": str(goal_eid or ""),
          "text": str(text or "")[:GOAL_TEXT_MAX], "created": bool(created),
          "ts": time.time()}
    if rewake:
        ev["rewake"] = True
        ev["last_landing"] = str(last_landing or "")[:240]
    if landings:
        ev["landings"] = [
            {"op": str((x or {}).get("op") or ""),
             "decision": str((x or {}).get("decision") or "")[:240]}
            for x in list(landings)[-3:]]
    if pool is not None:
        pool.put(ev)
    try:
        ent = est.docs.get(goal_eid) if hasattr(est, "docs") else None
        if ent is None and est is not None and hasattr(est, "find"):
            ent = est.find(goal_eid)
        if ent is not None:
            props = ent.properties or {}
            props["intent_planted_ts"] = ev["ts"]
            props["intent_planted_boot"] = str(
                getattr(getattr(est, "_srv", None), "_boot_ts", "") or "")
            est.upsert(ent, embedder=None)
    except Exception:
        pass
    return ev


# ---- the board's pulse (the open goals come back) --------------------------
# THE ONE-PASS BOARD (2026-10-08, the ThinkPhone's 'not progressing'): the
# boot queued each open goal ONCE; 34 goals -> 35 loop turns -> the pool
# drained at 09:01 and the seat sat idle for an hour with the board
# unchanged. The pulse re-queues the open goals whose last wake is stale:
# IDLE-ONLY (an empty pool + no running loop -- it never competes with a
# live event), a few per beat, oldest wake first, and BACKING OFF a goal
# its loops keep ignoring (each trailing 'ignored' landing doubles its
# wait, capped x16) so a parked goal does not burn the cloud credits.
GOAL_REWAKE_S = float(os.environ.get("MEMBRANE_GOAL_REWAKE_S", "1800"))
GOAL_PULSE_S = float(os.environ.get("MEMBRANE_GOAL_PULSE_S", "300"))
GOAL_REWAKE_BATCH = 3


def _ignored_streak(props: dict) -> int:
    """The trailing QUIET passes: everything but a STEP (real progress).
    'ignored', 'aborted', 'blocked', an unparsed 'reacted', a 'done' that
    never closed -- each one doubles the next wait (the review: 'reacted'
    passes that changed nothing never backed off, 2026-10-08)."""
    n = 0
    for land in reversed(list(props.get("landings") or [])):
        if str((land or {}).get("op") or "") == "step":
            break
        n += 1
    return n


_RETRY_RE = re.compile(r"\bretry\s+(?:after|in)\s+(\d+(?:\.\d+)?)\s*"
                       r"(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)\b",
                       re.IGNORECASE)
_ON_ENTITY_RE = re.compile(r"\bon\s+((?:membrane::)?[A-Za-z][\w.-]*:[^\s,;()]+)")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def blocked_condition(detail: str, now: float | None = None) -> dict:
    """A BLOCKED verdict's wake condition (the seat's ask, 2026-10-08):
    'retry after 2h' -> a timer; 'on <entity-id>' -> wake when it moves;
    neither -> {} (waits for the operator)."""
    now = time.time() if now is None else now
    out: dict = {}
    m = _RETRY_RE.search(detail or "")
    if m:
        unit = m.group(2).lower()
        out["blocked_until"] = now + float(m.group(1)) * _UNIT_S[unit[0]]
    m = _ON_ENTITY_RE.search(detail or "")
    if m:
        out["blocked_entity"] = m.group(1).rstrip(".")
    return out


def _entity_moved_since(srv, spec: str, ts: float) -> bool:
    """Has the entity moved after ts -- in the field (its last-seen) or in
    the store (a stamp on its doc)?"""
    try:
        from .efield import last_seen
        from .night import canon_eid
        fld = getattr(srv, "efield", None)
        if fld is not None:
            for key in {spec, canon_eid(spec) or spec}:
                st = fld.get(key)
                if st is not None and (last_seen(st) or 0) > ts:
                    return True
    except Exception:
        pass
    est = getattr(srv, "entity_store", None)
    try:
        doc = est._resolve_spec(spec) if est is not None else None
    except Exception:
        doc = None
    if doc is not None:
        props = doc.properties or {}
        for k in ("run_ts", "updated_ts", "value_ts", "last_ts", "ts",
                  "intent_planted_ts", "fulfilled_ts"):
            try:
                if float(props.get(k) or 0) > ts:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _parked(srv, props: dict, now: float | None = None) -> bool:
    """A BLOCKED goal waits for its condition: a timer, an entity moving,
    or (plain BLOCKED) the operator. The operator speaking unparks every
    kind."""
    bts = float(props.get("blocked_ts") or 0)
    if not bts:
        return False
    if float(getattr(srv, "_operator_last_ts", 0) or 0) > bts:
        return False
    now = time.time() if now is None else now
    until = float(props.get("blocked_until") or 0)
    ent = str(props.get("blocked_entity") or "")
    if until and now >= until:
        return False
    if ent and _entity_moved_since(srv, ent, bts):
        return False
    return True


def _park_kind(props: dict) -> str:
    if props.get("blocked_until"):
        return "timer"
    if props.get("blocked_entity"):
        return "entity"
    return "operator"


def dormant_line(srv, now: float | None = None,
                 min_age_s: float | None = None) -> str:
    """One line: the open goals that are asleep and why (backoff, parked),
    without waking any (the seat's ask: show the dormant set at a boot)."""
    est = getattr(srv, "entity_store", None)
    if est is None:
        return ""
    from .entities import CLASS_GOAL
    now = time.time() if now is None else now
    min_age_s = GOAL_REWAKE_S if min_age_s is None else min_age_s
    backoff: list[int] = []
    parked = {"operator": 0, "entity": 0, "timer": 0}
    soonest = None
    for doc in list((getattr(est, "docs", None) or {}).values()):
        if doc is None or doc.cls != CLASS_GOAL:
            continue
        props = doc.properties or {}
        if (props.get("status") or "open") != "open":
            continue
        if _parked(srv, props, now):
            k = _park_kind(props)
            parked[k] += 1
            if k == "timer":
                u = float(props.get("blocked_until") or 0)
                soonest = u if soonest is None else min(soonest, u)
            continue
        streak = _ignored_streak(props)
        last = float(props.get("intent_planted_ts") or 0)
        if streak and now - last < min_age_s * (2 ** min(streak, 4)):
            backoff.append(2 ** min(streak, 4))
    total = len(backoff) + sum(parked.values())
    if not total:
        return ""
    bits = []
    if backoff:
        lo, hi = min(backoff), max(backoff)
        bits.append(f"{len(backoff)} in backoff (x{lo}" + (f"-x{hi})" if hi != lo else ")"))
    if parked["operator"]:
        bits.append(f"{parked['operator']} blocked on the operator")
    if parked["entity"]:
        bits.append(f"{parked['entity']} waiting on an entity")
    if parked["timer"]:
        when = time.strftime("%H:%M", time.localtime(soonest)) if soonest else "?"
        bits.append(f"{parked['timer']} on a timer (next {when})")
    return f"asleep: {total} goal(s) -- " + ", ".join(bits)


def _due_goals(srv, est, now: float, min_age_s: float) -> list:
    from .entities import CLASS_GOAL
    loops = getattr(srv, "loops", None)
    held = set(getattr(loops, "_held", ()) or ())
    due = []
    for doc in list((getattr(est, "docs", None) or {}).values()):
        if doc is None or doc.cls != CLASS_GOAL:
            continue
        props = doc.properties or {}
        if (props.get("status") or "open") != "open" or doc.eid in held:
            continue
        if _parked(srv, props, now):
            continue
        last = float(props.get("intent_planted_ts") or 0)
        wait = min_age_s * (2 ** min(_ignored_streak(props), 4))
        if now - last >= wait:
            due.append((last, doc, props))
    due.sort(key=lambda t: t[0])
    return due


def _wake(est, pool, doc, props) -> None:
    lands = list(props.get("landings") or [])
    notify_intent_planted(
        est, pool, doc.eid, str(props.get("name") or ""),
        created=False, rewake=True,
        last_landing=str((lands[-1] or {}).get("decision") or "")
        if lands else "", landings=lands)


def rewake_open_goals(srv, now: float | None = None,
                      min_age_s: float | None = None,
                      batch: int | None = None) -> int:
    """One pulse beat: re-queue up to `batch` stale open goals. Returns
    how many were queued (0 when busy, paused or nothing is due)."""
    est = getattr(srv, "entity_store", None)
    pool = getattr(srv, "events_pool", None)
    if est is None or pool is None:
        return 0
    now = time.time() if now is None else now
    min_age_s = GOAL_REWAKE_S if min_age_s is None else min_age_s
    batch = GOAL_REWAKE_BATCH if batch is None else batch
    if min_age_s <= 0 or batch <= 0:
        return 0
    try:
        from .lanes import nerve_paused
        if nerve_paused(srv):
            return 0
    except Exception:
        pass
    try:                                # out of tokens (quota.py): the goals sleep on
        from . import quota as _Q
        if _Q.mind_paused(srv, "loops") is not None:
            return 0
    except Exception:
        pass
    loops = getattr(srv, "loops", None)
    if pool.depth() > 0 or (loops is not None and loops.running() > 0):
        return 0
    due = _due_goals(srv, est, now, min_age_s)
    for _, doc, props in due[:batch]:
        _wake(est, pool, doc, props)
    return min(len(due), batch)


def boot_wake_goals(srv, now: float | None = None,
                    min_age_s: float | None = None) -> int:
    """THE BOOT'S WAKE (2026-10-08, the review: every restart re-fired
    every open goal, fresh or parked -- 47 duplicate runs, 37% of the
    loops' tool calls): the boot picks up only the goals the PULSE would
    (stale, backoff, parked), as continuations with their ledger. No idle
    requirement and no batch cap -- a goal whose loop died with the old
    boot is due by then anyway."""
    est = getattr(srv, "entity_store", None)
    pool = getattr(srv, "events_pool", None)
    if est is None or pool is None:
        return 0
    now = time.time() if now is None else now
    min_age_s = GOAL_REWAKE_S if min_age_s is None else min_age_s
    due = _due_goals(srv, est, now, min_age_s)
    for _, doc, props in due:
        _wake(est, pool, doc, props)
    return len(due)


def start_goal_pulse(srv, every_s: float | None = None):
    """The boot seam: a daemon beat over rewake_open_goals."""
    every_s = GOAL_PULSE_S if every_s is None else every_s
    if every_s <= 0 or GOAL_REWAKE_S <= 0:
        return None

    def _beat():
        while True:
            time.sleep(every_s)
            try:
                n = rewake_open_goals(srv)
                if n:
                    import sys
                    print(f"[seat] the board's pulse: {n} open goal(s) "
                          "re-queued to the listeners", file=sys.stderr)
            except Exception:
                pass

    t = threading.Thread(target=_beat, daemon=True, name="membrane-goal-pulse")
    t.start()
    return t


def notify_intent_landed(est, pool, goal_eid: str, receipt: dict) -> dict:
    """THE LANDING'S CLAIM RECEIPT (the seat's plan: 'claim receipts on
    the goal'): a listener's landing for a goal (the loop's verdict
    carries the goal's eid) stamps the GOAL's doc
    (landings: [{ts, receipt}] capped 8) -- the goal's own ledger of
    what its steering produced. The receipts are the fulfill-by's
    evidence (the operator fulfills on receipts, never on vibes).

    THE STORM GUARD (the repeat-loop exhibit, 2026-10-06: the seat
    'keeps sending messages' -- five self-spoken turns in two minutes):
    this stamp deliberately does NOT put an event into the pool -- a
    LANDING IS BOOKKEEPING, NOT A STIMULUS; its event fed the drainer
    and every receipt spawned a fresh reacting loop turn (a reaction
    producing receipts producing the same reaction). The goal's ledger
    + the verdict-stamped entities are the readers' evidence; the
    pool hears only the PLANT."""
    try:
        ent = (est.docs or {}).get(goal_eid) if est is not None else None
        if ent is not None:
            props = ent.properties or {}
            lands = list(props.get("landings") or [])
            lands.append({"ts": time.time(),
                          **{k: v for k, v in (receipt or {}).items()
                             if not isinstance(v, (dict, list))
                             and len(str(v or "")) < 200}})
            del lands[:-8]                     # bounded ledger
            props["landings"] = lands
            ent.properties = props
            est.upsert(ent, embedder=None)
    except Exception:
        pass
    return {"kind": "intent-landed", "entity": str(goal_eid or "")}


def notify_watch_attached(est, pool, entity_key: str, lane: str,
                          detail=None) -> dict:
    """THE ATTACH EVENT: a watch planted (any lane) = an event to the
    model's pool (a sensor armed is a decision; the loops see it)."""
    ev = {"kind": "watch-attached", "entity": entity_key, "lane": lane,
          "detail": detail, "ts": time.time()}
    if pool is not None:
        pool.put(ev)
    try:
        ent = est.docs.get(entity_key) if hasattr(est, "docs") else None
        if ent is None and hasattr(est, "find"):
            ent = est.find(entity_key)
        if ent is not None:
            props = ent.properties or {}
            props["watch_attached"] = {"lane": lane, "ts": ev["ts"]}
            est.upsert(ent, embedder=None)
    except Exception:
        pass
    return ev


def notify_watch_detached(est, pool, keys, lane: str,
                          detail=None) -> int:
    """THE DETACH EVENT(s): one event per dropped watch key."""
    n = 0
    for key in list(keys or []):
        ev = {"kind": "watch-detached", "entity": key, "lane": lane,
              "detail": detail, "ts": time.time()}
        if pool is not None:
            pool.put(ev)
        n += 1
    return n


WATCH_OPS_CAP = 8


def edit_watch_ops(srv, ent, ops: list, replace: bool = False) -> dict:
    """THE CRUD (entity_edit's watch_ops param lands here): append
    (default) / replace (replace=True) / clear ([]); the CAP: an
    entity carries <= 8 ops (the flood covenant -- the overflow is an
    honest refusal). Fires the attach/detach events per op change."""
    props = ent.properties or {}
    pool = getattr(srv, "events_pool", None)
    est = getattr(srv, "entity_store", None)
    if not isinstance(ops, list):
        return {"ok": False, "error": "watch_ops must be a list"}
    ops = [o for o in ops if isinstance(o, dict)
           and str(o.get("op") or "") in ("away", "near", "around")]
    # THE EMPTY-LIST FORM IS THE CLEAR (an append of nothing is a
    # user error anyway; an explicit [] means 'drop every op')
    if not ops:
        stored = est.docs.get(ent.eid)
        if stored is not None:
            (stored.properties or {}).pop("watch_ops", None)
        props.pop("watch_ops", None)
        ent.properties = props
        if pool is not None:
            notify_watch_detached(est, pool, [ent.eid], lane="watch_ops")
        est.upsert(ent, embedder=None)
        return {"ok": True, "watch_ops": None, "cleared": True}
    cur: list[dict] = []
    if not replace:
        cur = [dict(o) for o in (props.get("watch_ops") or [])]
    # THE ATTENTION BUDGET (2026-10-08): one limit across the watch senses;
    # at the limit the refusal carries the inventory -- the model chooses
    # what to drop (a replace in place costs only its difference)
    from .attention import over as _over
    have = len(props.get("watch_ops") or [])
    _room = min(len(ops), max(0, WATCH_OPS_CAP - len(cur)))
    refused = _over(est, getattr(srv, "_watch_board", None),
                    len(cur) + _room - have)
    if refused:
        return refused
    for o in ops:
        o = dict(o)
        o.setdefault("ts", time.time())
        # PLANTING IS ARMING (2026-10-11): ops planted before the sweep was
        # wired have no `armed` -- they wait dormant until planted again
        o["armed"] = True
        if len(cur) >= WATCH_OPS_CAP:
            return {"ok": False,
                    "error": f"watch_ops cap ({WATCH_OPS_CAP}) reached; "
                             "drop one (entity_edit watch_ops=[] clears)"}
        cur.append(o)
        if pool is not None:
            notify_watch_attached(est, pool, ent.eid, lane="watch_ops",
                                  detail=o)
    props["watch_ops"] = cur
    ent.properties = props
    est.upsert(ent, embedder=None)
    return {"ok": True, "watch_ops": cur}


# ---- the actor layer (the operator's 2026-10-02 design) --------------------
def plant_actor(est, endpoint: str, url: str = "") -> bool:
    """The endpoint actor: one entity per LLM endpoint (kind endpoint;
    the model's responses attach to the one that spoke)."""
    eid = f"membrane::ep:{endpoint}"
    ent = est.docs.get(eid)
    if ent is None:
        ent = QuantumEntity(eid=eid, cls="Component",
                            properties={"name": f"ep:{endpoint}",
                                        "endpoint": endpoint})
        est.docs[eid] = ent
    props = ent.properties or {}
    props.setdefault("responses", 0)
    if url:
        props["url"] = url
    est.upsert(ent, embedder=None)
    return True


def actor_on_event(est, role: str, text: str, vec=None,
                   via: str | None = None, reply_to: str | None = None,
                   model: str = "", route_reason: str = "",
                   session: str = "", entity: str = "",
                   embedder=None, queue: "WatchEvents | None" = None
                   ) -> dict:
    """ONE event's actor lane: BOTH event roles (a user arrival AND a
    model response) land here -- no crutch; the transport updates its
    actor. The response side carries THE ROUTE-BACK METADATA (reply_to
    = the msg entity it answers, via = the endpoint that spoke,
    route_reason + model). A msg child entity mints per event (the
    chat's history IS the entity graph)."""
    role = "model" if role == "model" else role
    via_key = via or ("ep:ollama" if role == "model" else None)
    # 1. the operator's facts stamp + EMA (user + model both move it)
    op = est.docs.get(OPERATOR_EID)
    ts = time.time()
    if op is None:
        plant_operator(est)
        op = est.docs.get(OPERATOR_EID)
    props = op.properties or {}
    props["messages"] = int(props.get("messages") or 0) + 1
    props["last_role"] = role
    props["last_text"] = " ".join(str(text or "").split())[:160]
    props["last_text_ts"] = ts
    if vec is not None and np.size(vec):
        v = _unit(vec)
        if op.position is None:
            op.position = [float(x) for x in v]
        else:
            old = _unit(op.position)
            op.position = [float(x) for x in _unit(0.6 * old + 0.4 * v)]
    est.upsert(op, embedder=None)
    # 2. the MSG CHILD (the message entity: the chat's history as a
    # DOUBLE-LINKED LIST of entities: prev_msg/next_msg -- the
    # conversation's tail edge carried by the ACTOR (last_msg): one
    # read of the actor = the whole chain, one field to walk either
    # way)
    mid = f"membrane::msg:{h16(f'{session}|{ts}|{role}')}"
    prev_eid = props.get("last_msg")
    child = QuantumEntity(eid=mid, cls="Component",
                          properties={"name": f"msg:{role}",
                                      "parent": (via_key and
                                                 f"membrane::ep:{via_key.split(':', 1)[-1]}")
                                      or OPERATOR_EID,
                                      "role": role,
                                      "text_head": " ".join(
                                          str(text or "").split())[:160],
                                      "ts": ts,
                                      "session": session})
    if prev_eid:
        child.properties["prev_msg"] = prev_eid
        peer = est.docs.get(prev_eid)
        if peer is not None:
            pp = peer.properties or {}
            pp["next_msg"] = mid            # the back-edge
            peer.properties = pp
            est.upsert(peer, embedder=None)
    ent = child
    if vec is not None and np.size(vec):
        ent.position = [float(x) for x in _unit(vec)]
    if reply_to:
        ent.properties["reply_to"] = reply_to
    if via_key:
        ent.properties["via"] = via_key
        ent.properties["parent"] = f"membrane::ep:{via_key.split(':', 1)[-1]}"
        # THE ENDPOINT COUNTS (the actor's ledger)
        ep = est.docs.get(f"membrane::ep:{via_key.split(':', 1)[-1]}")
        if ep is not None:
            ep_props = ep.properties or {}
            ep_props["responses"] = int(ep_props.get("responses") or 0) + 1
            ep_props["last_response_ts"] = ts
            ep.properties = ep_props
            est.upsert(ep, embedder=None)
    if model:
        ent.properties["model"] = model
    if route_reason:
        ent.properties["route_reason"] = route_reason
    if entity:
        ent.properties["entity"] = entity
    est.docs[mid] = ent
    est.upsert(ent, embedder=None)
    # the ACTOR's tail pointer: one read of the actor = the chain's end
    props["last_msg"] = mid
    est.upsert(op, embedder=None)
    # 3. the WATCH-LOOP decision path: a 'watch' role's event stamps
    # the entity's last decision (the loop's verdict lives with the
    # entity's watch provenance)
    if role == "watch" and entity:
        mprops = ent.properties or {}
        verdict = "ignored" if str(mprops.get("text_head", "")).lower() \
            .startswith("ignored") else "reacted"
        try:
            est.stamp_watch_decision(entity, {"verdict": verdict,
                                              "ts": ts,
                                              "msg": mid})
        except Exception:
            pass
    # 4. the queue (the main chat IS the operator lane's reaction)
    if queue is not None:
        queue.put({"event_id": f"op-{int(ts * 1000)}",
                   "kind": "operator-message", "entity": OPERATOR_EID,
                   "text_head": props["last_text"], "ts": ts})
    return {"fired": True, "ts": ts, "msg_eid": mid}


# ---- the ONE-EXEC lane (the run demolition's replacement) -------------------
_GETTER_LRU_S = 5.0
_getter_last_ts: dict = {}
# THE AUDIT'S PAUSE (#161 rung 2): eid -> until. A paused getter does not run;
# every read of it answers "unchanged" -- the source stopped, so a mark of its
# change must stop too (in memory; it expires by itself)
_getter_paused: dict = {}


def pause_getter(eid: str, until: float) -> None:
    _getter_paused[eid] = float(until)


def unpause_getter(eid: str) -> None:
    _getter_paused.pop(eid, None)


_GETTER_KEYS = {"state", "render", "patch", "run", "veto", "delete", "data",
                "intent", "error", "patch_refused", "run_result", "applied"}


# A SAME READING IS NOT NEWS (2026-10-11: a watch on the phone's battery
# sensor read it, the read fired the getter, the getter posted
# getter-applied, the event woke the next watch -- a fresh session every
# 1-4 minutes redoing the same reads, 40% -> 40%). Only a changed value
# posts the event; a key that is only a clock (ts, *_at, updated, ...)
# changes every run and says nothing.
_CLOCK_KEY = re.compile(r"(^|_)(ts|time|timestamp|at|updated|checked|age|now|epoch)$", re.I)


def _score(kind: str, **kw) -> None:
    try:
        from . import score as _SC
        _SC.note(kind, **kw)
    except Exception:
        pass


def reading_changed(before: dict, state: dict) -> bool:
    for k, v in (state or {}).items():
        if _CLOCK_KEY.search(str(k)):
            continue
        if k not in (before or {}) or before[k] != v:
            return True
    return False


def is_watched(props: dict) -> bool:
    """A wake needs a watch (#549): the entity carries a watch spec -- own-motion
    fields, proximity ops, or a watcher (file/signal). A reading nobody
    watches is stored and scored, but wakes no loop."""
    return any((props or {}).get(k) for k in ("watch_fields", "watch_ops", "watch"))


def entity_read_back(srv, ent, force: bool = False, by: str = "model",
                     notify: bool = True) -> dict:
    """THE READ-BACK TRIGGER: a READ of a dynamic entity fires its
    getter_script (the dict return patches script_data + the entity's
    state facts); the LRU (5s per entity) dedups a hammering loop --
    `force=True` (the explicit read tool) bypasses it (a re-run is the
    caller's call); the getter's kind rides the engine's dispatch.
    `notify=False`: the reader is answered with the values itself (a
    job's event) -- no getter-applied for another loop (#188)."""
    props = ent.properties or {}
    est = getattr(srv, "entity_store", None)
    if est is None:
        return {"fired": False, "reason": "the entity layer is off"}
    from .night import effective_script
    code, inherited_from = effective_script(est, ent, "getter")
    if not code:
        return {"fired": False, "reason": "no getter_script"}
    now = time.time()
    if _getter_paused.get(ent.eid, 0.0) > now:
        return {"fired": True, "state": None, "changed": False, "paused": True}
    _getter_paused.pop(ent.eid, None)
    if not force:
        last = _getter_last_ts.get(ent.eid, 0.0)
        if now - last < _GETTER_LRU_S:
            return {"fired": False, "reason": "lru"}
    _getter_last_ts[ent.eid] = now
    from .night import run_entity_update_script, apply_entity_update
    # THE READER IS TOLD (#239): the getter sees who reads (state.extra.by --
    # self / model / field), so a toy can flag a change as self's own
    out = run_entity_update_script(srv, ent, "getter", extra_state={"by": by})  # its own reads fire nothing
    if out.get("error"):
        _score("error")
        from .night import script_held
        res = {"fired": True, "error": out.get("error")}
        if script_held(ent.properties or {}, now):
            res["held"] = True                          # it did not run: held, not broken now
        return res
    # THE CONTRACT SAYS ITSELF (2026-10-08: six rewrites 'fired {}' for nine
    # minutes -- only result['state'] reads back, and nothing said so)
    note = ""
    if not out:
        note = ("the getter set no `result` dict (a top-level variable; a "
                "function's return and stdout are not read) -- set "
                "result['state'] = {...} with the live values")
    else:
        stray = sorted(k for k in out if k not in _GETTER_KEYS)
        if stray and not isinstance(out.get("state"), dict):
            note = (f"the getter's result had keys {stray} -- none is read "
                    "back: put the live values in result['state'] = {...} "
                    f"(raw: {json.dumps(out, default=str)[:160]})")
    # THE FULL VERDICT through the engine (the authorship covenant):
    # {run: true} execs the command, {patch} moves the entity, {render}
    # rides the banner -- ONE apply, every lane speaks
    applied = apply_entity_update(srv, ent, out, trigger="getter")
    state = out.get("state")
    changed = None                                  # no reading: neither changed nor not
    if isinstance(state, dict):
        # THE READING IS DATA (#152): the getter's state lands in script_data
        # raw -- a helper handle (state['me'] = membrane.me) rode in as a live
        # object, its '<..._Me object at 0x..>' repr surfaced in the job's
        # "fresh values" diff, and a new address every fire read as a change.
        # The same json-safe screen as the patch lane: what is stored is what
        # persists, compares and renders.
        from .entities import json_safe as _json_safe
        state = {str(k): _json_safe(v) for k, v in state.items()}   # every key kept
        # STEP 0 of the generalization plan: the reading is experience --
        # a field event about this entity (its own values), and the ring
        try:
            from .consolidate import record_reading, record_class_readings
            record_reading(srv, ent, state, now)
            if inherited_from:
                record_class_readings(srv, est, inherited_from, state, skip=ent.eid, now=now)
        except Exception:
            pass
        merged = dict((ent.properties or {}).get("script_data") or {})
        changed = reading_changed(merged, state)
        if changed and not force and by == "field":
            try:                                # the field read the world without a model's ask
                from . import score as _SC
                _SC.note_changed("reading", ent.eid)
            except Exception:
                pass
        if changed and not merged:
            try:
                from . import score as _SC
                _SC.credit(f"the {ent.eid.split('::')[-1]} getter now reads it for you", ent.eid)
            except Exception:
                pass
        merged.update(state)
        props = ent.properties or {}
        props["script_data"] = merged
        ent.properties = props
        est.upsert(ent, embedder=None)
        # A READ IS NOT AN ECHO (#188): a goal's loop read the eye every
        # pass, its job answered it the fresh values -- and this post woke a
        # SECOND loop on the eye; a counter (beats) changes on every read, so
        # the eye ran a pass per read. The reader's own answer is no news.
        pool = getattr(srv, "events_pool", None) if notify else None
        if pool is not None and changed and is_watched(ent.properties):
            pool.put({"kind": "getter-applied", "entity": ent.eid,
                      "state": dict(list(state.items())[:8]),
                      "ts": now})
    out["applied"] = applied
    res = {"fired": True, "state": state, "run_result": out.get("run_result"), "changed": changed}
    if inherited_from:
        res["inherited_from"] = inherited_from
    if note:
        res["note"] = note
    return res


SWEEP_EVERY_BEATS = 10                  # the proximity sweep's pace on the thinker's hum (~10 s by day)


def sweep_now(srv, now: float | None = None) -> list[dict]:
    """THE PROXIMITY SWEEP, WIRED (2026-10-11: sweep_watch_ops was written on
    10-02 and never called -- near/away/around watches were plantable, counted
    against the attention budget, and never fired): one sweep into the
    seat's event pool. The thinker's hum calls it every SWEEP_EVERY_BEATS."""
    est = getattr(srv, "entity_store", None)
    pool = getattr(srv, "events_pool", None)
    if est is None or pool is None:
        return []
    return sweep_watch_ops(est, pool, {}, now=now)


def dormant_inventory(est) -> dict:
    """What was planted before its trigger ran: proximity ops without `armed`
    and thought/dreamed scripts without an arming stamp -- the list the seat
    triages (planting again arms)."""
    from .entities import MEMBRANE_NS
    ops, scripts = [], []
    for eid, ent in list((getattr(est, "docs", None) or {}).items()):
        if eid == MEMBRANE_NS + "dormant":
            continue                       # the inventory itself is not a planted watch (#171)
        p = ent.properties or {}
        for o in p.get("watch_ops") or []:
            if isinstance(o, dict) and not o.get("armed"):
                ops.append({"entity": eid, "op": o.get("op"), "rel": o.get("rel"),
                            "y": o.get("y"), "z": o.get("z"), "planted": o.get("ts")})
        for k in ("thought", "dreamed"):
            if p.get(f"{k}_script") and not p.get(f"{k}_armed_ts"):
                scripts.append({"entity": eid, "kind": k, "head": str(p[f"{k}_script"])[:120]})
    return {"watch_ops": ops, "scripts": scripts}


def paint_dormant(srv) -> dict:
    """membrane::dormant: the inventory of what waits to be armed (boot)."""
    from .entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return {}
    inv = dormant_inventory(est)
    eid = MEMBRANE_NS + "dormant"
    ent = est.docs.get(eid)
    if ent is None:
        ent = QuantumEntity(eid=eid, cls=CLASS_CONFIG, properties={
            "name": "dormant",
            "summary": ("WHAT WAITS TO BE ARMED: proximity watches (watch_ops) and thought/dreamed "
                        "scripts planted before their triggers were wired (2026-10-11). They don't fire. "
                        "Plant one again to arm it (entity edit with the same watch_ops / script); drop "
                        "it with watch_ops=[] or an empty script.")})
        est.docs[eid] = ent
    pp = ent.properties or {}
    # NOT under `watch_ops` (#171): that is the key real watches use, so the
    # attention ledger counted the mirror as live watches and the next boot
    # re-read it as unarmed ops -- it grew at every restart. A seat carrying
    # the old mirror drops it here.
    pp.pop("watch_ops", None)
    pp.update({"dormant_ops": inv["watch_ops"][:40], "scripts": inv["scripts"][:40],
               "state": (f"{len(inv['watch_ops'])} proximity watch(es) and {len(inv['scripts'])} "
                         "thought/dreamed script(s) wait to be armed" if (inv["watch_ops"] or inv["scripts"])
                         else "nothing waits: every planted watch and trigger script is armed")})
    ent.properties = pp
    try:
        est.upsert(ent, embedder=None)
    except Exception:
        pass
    return inv
