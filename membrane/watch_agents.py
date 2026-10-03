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

import os
import threading
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
            kind = op.get("op") or ""
            rel = op.get("rel") or "operator"
            rel_eid = (OPERATOR_EID if rel == "operator"
                       else eid if rel == "self" else
                       next((k for k in positions
                             if k.endswith(rel)), None))
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


class LoopRunner:
    """The loop pool: 3 concurrent non-operator loops + 1 dedicated
    operator lane (operator admit NEVER fails -- the main chat's
    reactive lane cannot starve behind batch work)."""

    def __init__(self, max_loops: int = 3):
        self.max = max(1, int(max_loops))
        self._lk = threading.Lock()
        self._held: set[str] = set()       # running non-operator loop keys

    def admit(self, loop: LoopDesc) -> bool:
        if loop.is_operator:
            return True                    # dedicated: always available
        with self._lk:
            if len(self._held) >= self.max:
                return False
            key = loop.eid or loop.kind
            self._held.add(key)
            return True

    def release(self, loop: LoopDesc) -> None:
        with self._lk:
            self._held.discard(loop.eid or loop.kind)

    def running(self) -> int:
        with self._lk:
            return len(self._held)

# ---- the ACTOR layer (the operator's 2026-10-02 design) --------------------
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
