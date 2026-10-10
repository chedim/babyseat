"""THE FIELD'S PULSE (2026-10-08, the viewer v3's live layer).

The serve's /event SSE carried only chat frames; nothing told a viewer the
field moved or which mind was touching what. This publisher coalesces the
field's own events and the minds' focus into compact frames on the same
hub, about once a second:

  field.applied {eids, streams}      entities an event just moved (mentions)
  field.moved   {pid, moves}         dirty entities re-placed into each
                                     cached 3D projection (field3d.place)
  field.born    {eids}               nursery -> live
  field.watch   {fires:[[eid,kind]]} a geometric watch fired
  mind.focus    {agent, eid, phase}  a watch loop took / let go of an entity
  mind.touch    {agent, eid, tool}   a tool call named an entity
  mind.thinker  {kind, about}        the thinker chewed something
  mind.dreamer  {goal}               the dreamer steers by a goal

NO MESSAGE TEXT rides any frame (ids, kinds, tool names only): the HA relay
shows them to admins whatever session they came from.
"""
from __future__ import annotations

import threading
import time

PULSE_S = 1.0
MAX_EIDS = 300
MAX_GROUPS = 80               # events per beat that keep their own entity group (edge pulses)
GROUP_EIDS = 12
MAX_MIND = 40
ENTITY_ARGS = ("spec", "eid", "entity", "key", "name", "id")


def of(srv) -> "FieldPulse | None":
    return getattr(srv, "_field_pulse", None) if srv is not None else None


def touched_eid(tool: str, args: dict) -> str:
    """The entity a tool call names (the MCP tools' alias set), or ''."""
    if not isinstance(args, dict):
        return ""
    for k in ENTITY_ARGS:
        v = args.get(k)
        if isinstance(v, str) and v.strip() and len(v) <= 200 and "\n" not in v:
            return v.strip()
    return ""


class FieldPulse:
    def __init__(self, srv, hub=None, every_s: float = PULSE_S):
        self.srv, self._hub, self.every_s = srv, hub, every_s
        self._lock = threading.Lock()
        self._applied: dict[str, set] = {}
        self._born: set = set()
        self._watch: list = []
        self._moved: dict[str, list] = {}
        self._mind: list = []
        self._slots: dict[str, str] = {}       # loop agent -> loop-N
        self._alive = False
        self.beats = 0
        self.published = 0
        self.last_beat = 0.0

    # ---- the hub (the serve's, wired after the runner exists)
    def hub(self):
        if self._hub is not None:
            return self._hub
        runner = getattr(self.srv, "_loop_runner", None)
        return getattr(runner, "hub", None)

    # ---- intake (cheap, any thread)
    def applied(self, eids, stream: str = "") -> None:
        with self._lock:
            # THE EVENT'S GROUP (2026-10-10, the operator: "show all the events on
            # the visible edges of the graph as pulses"): which entities one event
            # touched together -- the viewer pulses along the edges between them
            grp = [e for e in dict.fromkeys(eids)][:GROUP_EIDS]
            groups = getattr(self, "_groups", None)
            if groups is None:
                groups = self._groups = []
            if len(grp) >= 2 and len(groups) < MAX_GROUPS:
                groups.append({"s": stream or "", "e": grp})
            for e in eids:
                if len(self._applied) >= MAX_EIDS and e not in self._applied:
                    break
                self._applied.setdefault(e, set()).add(stream or "")

    def moved(self, eid: str, vec) -> None:
        with self._lock:
            if len(self._moved) < MAX_EIDS:
                self._moved[eid] = vec

    def born(self, eid: str) -> None:
        with self._lock:
            if len(self._born) < MAX_EIDS:
                self._born.add(eid)

    def watch(self, eid: str, kind: str) -> None:
        with self._lock:
            if len(self._watch) < MAX_EIDS:
                self._watch.append([eid, kind])

    def mind(self, what: str, **fields) -> None:
        with self._lock:
            if len(self._mind) < MAX_MIND:
                self._mind.append({**fields, "type": f"mind.{what}"})

    # ---- the loops' avatars: a stable loop-N per running loop
    def loop_slot(self, agent: str, take: bool = True) -> str:
        with self._lock:
            if agent in self._slots:
                slot = self._slots[agent]
                if not take:
                    self._slots.pop(agent, None)
                return slot
            if not take:
                return ""
            used = set(self._slots.values())
            n = 1
            while f"loop-{n}" in used:
                n += 1
            self._slots[agent] = f"loop-{n}"
            return self._slots[agent]

    def focus(self, agent: str, eid: str, phase: str) -> None:
        slot = self.loop_slot(agent, take=(phase == "start"))
        if slot:
            self.mind("focus", agent=slot, eid=eid, phase=phase)

    def touch(self, session_agent: str, tool: str, args: dict) -> None:
        eid = touched_eid(tool, args)
        if not eid:
            return
        a = str(session_agent or "").strip()
        agent = (self._slots.get(a) or "loop") if a.startswith("watch") else "chat"
        if a == "phone":
            agent = "phone"
        self.mind("touch", agent=agent, eid=eid, tool=str(tool or ""))

    # ---- the beat
    def frames(self) -> list[dict]:
        with self._lock:
            applied, self._applied = self._applied, {}
            groups, self._groups = getattr(self, "_groups", None) or [], []
            born, self._born = self._born, set()
            watch, self._watch = self._watch, []
            moved, self._moved = self._moved, {}
            mind, self._mind = self._mind, []
        out = []
        if applied:
            out.append({"type": "field.applied", "eids": sorted(applied),
                        "streams": sorted({s for v in applied.values() for s in v if s}),
                        "groups": groups})
        if born:
            out.append({"type": "field.born", "eids": sorted(born)})
        if watch:
            out.append({"type": "field.watch", "fires": watch})
        if moved:
            for pid, proj in list((getattr(self.srv, "_field3d", None) or {}).items()):
                moves = []
                for eid, vec in moved.items():
                    if eid in proj.index:
                        try:
                            moves.append([eid] + proj.place(vec))
                        except Exception:
                            pass
                if moves:
                    out.append({"type": "field.moved", "pid": pid, "moves": moves})
        out += mind
        return out

    def beat(self) -> int:
        hub = self.hub()
        frames = self.frames()
        self.beats += 1
        self.last_beat = time.time()
        if hub is None:
            return 0
        for f in frames:
            try:
                hub.publish(f)
                self.published += 1
            except Exception:
                pass
        return len(frames)

    def stats(self) -> dict:
        """The /membrane/state line: is it beating, bound, publishing."""
        return {"alive": self._alive, "beats": self.beats, "published": self.published,
                "hub": self.hub() is not None, "last_beat_ago_s":
                round(time.time() - self.last_beat, 1) if self.last_beat else None,
                "loops": dict(self._slots)}

    def start(self) -> "FieldPulse":
        if self._alive:
            return self
        self._alive = True

        def _run():
            while self._alive:
                time.sleep(self.every_s)
                try:
                    self.beat()
                except Exception:
                    pass
        threading.Thread(target=_run, daemon=True, name="membrane-field-pulse").start()
        return self

    def stop(self) -> None:
        self._alive = False


def efield_listener(srv):
    """The field's applied-event listener (EntityField.listeners): the
    mentioned entities, words and phrases left out (they're the field's
    most numerous, least visual entities)."""
    def fn(key, ev, x_msg=None):
        p = of(srv)
        if p is None or not isinstance(ev, dict):
            return
        eids = [m[0] for m in (ev.get("mentions") or [])
                if isinstance(m, (list, tuple)) and m and str(m[0])[:2] not in ("w:", "p:")]
        if eids:
            p.applied(eids, str(ev.get("stream") or ""))
    return fn
