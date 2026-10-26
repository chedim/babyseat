"""THE CLIFFHANGER CORE (#576 part B): triggers on the field, no hatches.

A TRIGGER is an entity (class `Trigger`): a reified edge from what it watches to
what it writes --

    WHEN near(?x, op, 0.2) WHERE isa(?x, toy): ?x.close = 1 + (x.close or 0)

- WHEN: watchers, edge-triggered per binding. Level watchers (near, away, above,
  below, pos, vel, align, around, quiet) fire when the conjunction turns true and
  re-arm when it turns false; the event watchers (changed: a write on a path,
  every: a period) fire on the event while the level ones hold. At most one event
  watcher per trigger.
- WHERE: pattern atoms that bind the variables -- isa(?x, Class) (its class or an
  ISA ancestor), is(?x, eid), filter(expr) -- no XPath.
- WRITE: [target, expr]: `?x.prop` (or `<eid>.prop`, `.position` included) gets
  the expression's value. Expressions are a small safe language over scalars and
  first-class vectors: + - * /, comparisons, and/or/not, x.prop reads, and the
  built-ins dot, norm, distance (1 - cos, the watches' metric), unit, abs, min, max.

THE VM hears every field write (writes.on_write, part A) and QUEUES it -- a
listener never writes; drain() works the queue outside the writer's call, and
sweep() evaluates the geometric and timed watchers on the thinker's beat. A firing
writes through the store as `trigger:<eid>`, in the chain that caused it (a
sweep starts its own), its trigger appended to the chain's `via`.

THE LOOP RULE: a firing whose trigger is already in its chain's `via` is refused
unless the chain passed through self after it (a trigger owned by self appends
`self` -- Blurp is the one place a loop may run; its energy bounds it, part C)."""
from __future__ import annotations

import ast
import collections
import json
import re
import threading
import time

import numpy as np

from . import writes as W
from .entities import MEMBRANE_NS, QuantumEntity

SELF = MEMBRANE_NS + "Component:self"
CLASS = "Trigger"
_TRIG_PREFIX = MEMBRANE_NS + CLASS + ":"
QUEUE_MAX = 4096                # writes waiting for the VM (oldest dropped and counted)

EVENT_VERBS = {"changed", "every"}
LEVEL_VERBS = {"near", "away", "above", "below", "pos", "vel", "align", "around", "quiet"}
VERBS = EVENT_VERBS | LEVEL_VERBS


class Refused(ValueError):
    pass


# ---- expressions -----------------------------------------------------------------------------

def _vec(v):
    return np.asarray(v, dtype=float)


def _out(v):
    return [float(x) for x in v] if isinstance(v, np.ndarray) else v


def _dist(a, b) -> float:
    a, b = _vec(a), _vec(b)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if len(a) != len(b) or na < 1e-12 or nb < 1e-12:
        return 1.0
    return 1.0 - float(a @ b) / (na * nb)


def _unit(a):
    a = _vec(a)
    n = float(np.linalg.norm(a))
    return _out(a / n) if n > 1e-12 else _out(a)


BUILTINS = {
    "dot": lambda a, b: float(_vec(a) @ _vec(b)),
    "norm": lambda a: float(np.linalg.norm(_vec(a))),
    "distance": _dist,
    "unit": _unit,
    "abs": abs, "min": min, "max": max,
}
_NAMES = {"True": True, "False": False, "None": None}
_BIN = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide}
_CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b}


class EntityView:
    """An entity read inside an expression: x.prop, x.position, x.eid, x.cls."""

    def __init__(self, est, eid: str):
        self._est, self._eid = est, eid

    def get(self, attr: str):
        if attr.startswith("_"):
            raise Refused(f"no attribute {attr!r}")
        ent = (self._est.docs or {}).get(self._eid)
        if ent is None:
            return None
        if attr == "eid":
            return ent.eid
        if attr == "cls":
            return ent.cls
        if attr == "position":
            return _out(_vec(ent.position)) if ent.position is not None else None
        return (ent.properties or {}).get(attr)


def _parse(expr: str):
    try:
        return ast.parse(str(expr), mode="eval").body
    except SyntaxError as e:
        raise Refused(f"not an expression: {expr!r} ({e.msg})") from None


def _ev(node, env):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool, type(None))):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _NAMES:
            return _NAMES[node.id]
        raise Refused(f"unbound name {node.id!r}")
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_ev(e, env) for e in node.elts]
    if isinstance(node, ast.Attribute):
        base = _ev(node.value, env)
        if not isinstance(base, EntityView):
            raise Refused("attributes are read on entities only")
        return base.get(node.attr)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _ev(node.left, env), _ev(node.right, env)
        if isinstance(a, list) or isinstance(b, list):
            return _out(_BIN[type(node.op)](_vec(a) if isinstance(a, list) else a,
                                            _vec(b) if isinstance(b, list) else b))
        return _BIN[type(node.op)](a, b).item() if hasattr(_BIN[type(node.op)](a, b), "item") \
            else _BIN[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not v
        if isinstance(node.op, ast.USub):
            return _out(-_vec(v)) if isinstance(v, list) else -v
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            v = True
            for e in node.values:
                v = _ev(e, env)
                if not v:
                    return v
            return v
        v = False
        for e in node.values:
            v = _ev(e, env)
            if v:
                return v
        return v
    if isinstance(node, ast.Compare) and all(type(o) in _CMP for o in node.ops):
        left = _ev(node.left, env)
        for op, right_node in zip(node.ops, node.comparators):
            right = _ev(right_node, env)
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BUILTINS \
            and not node.keywords:
        return BUILTINS[node.func.id](*[_ev(a, env) for a in node.args])
    raise Refused(f"not allowed in an expression: {type(node).__name__}")


def _check(node, names: set) -> None:
    """The definition-time check: the same whitelist, no evaluation."""
    if isinstance(node, ast.Constant):
        return
    if isinstance(node, ast.Name):
        if node.id not in names and node.id not in _NAMES:
            raise Refused(f"unbound name {node.id!r}")
        return
    if isinstance(node, (ast.List, ast.Tuple)):
        for e in node.elts:
            _check(e, names)
        return
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_") or not isinstance(node.value, ast.Name):
            raise Refused("attributes are read on entities only")
        _check(node.value, names)
        return
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        _check(node.left, names); _check(node.right, names)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
        _check(node.operand, names)
        return
    if isinstance(node, ast.BoolOp):
        for e in node.values:
            _check(e, names)
        return
    if isinstance(node, ast.Compare) and all(type(o) in _CMP for o in node.ops):
        _check(node.left, names)
        for c in node.comparators:
            _check(c, names)
        return
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BUILTINS \
            and not node.keywords:
        for a in node.args:
            _check(a, names)
        return
    raise Refused(f"not allowed in an expression: {type(node).__name__}")


def evaluate(expr: str, env: dict):
    return _ev(_parse(expr), env)


# ---- definitions -----------------------------------------------------------------------------

def _var(s) -> str | None:
    return s[1:] if isinstance(s, str) and s.startswith("?") else None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", str(name).lower()).strip("-")[:60] or "t"


def render(doc) -> str:
    """The trigger as one readable line (the Code tab)."""
    p = doc.properties or {}

    def atom(a):
        return f"{a[0]}({', '.join(str(x) for x in a[1:])})"
    when = " and ".join(atom(a) for a in p.get("when") or [])
    where = ", ".join(atom(a) if a[0] != "filter" else str(a[1]) for a in p.get("where") or [])
    tgt, expr = (p.get("write") or ["?", "?"])[:2]
    return f"WHEN {when}" + (f" WHERE {where}" if where else "") + f": {tgt} = {expr}"


def _validate(when, where, write) -> dict:
    when, where, write = list(when or []), list(where or []), list(write or [])
    if not when:
        raise Refused("a trigger needs a watcher (WHEN)")
    if len(write) != 2:
        raise Refused("write is [target, expression]")
    bound = set()
    for a in where:
        if not a or a[0] not in ("isa", "is", "filter"):
            raise Refused(f"unknown pattern atom {a!r} (isa, is, filter)")
        if a[0] in ("isa", "is"):
            v = _var(a[1]) if len(a) > 2 else None
            if not v:
                raise Refused(f"{a[0]} binds a ?variable: {a!r}")
            bound.add(v)
    for a in where:
        if a[0] == "filter":
            _check(_parse(a[1]), bound)
    n_event = 0
    for a in when:
        if not a or a[0] not in VERBS:
            raise Refused(f"unknown watcher {a[0] if a else a!r} ({', '.join(sorted(VERBS))})")
        n_event += a[0] in EVENT_VERBS
        for x in a[1:]:
            v = _var(str(x).split(".", 1)[0]) if isinstance(x, str) else None
            if v and v not in bound:
                raise Refused(f"?{v} is bound by nothing (WHERE isa/is)")
        if a[0] in ("above", "below"):
            _check(_parse(a[1]), bound)
    if n_event > 1:
        raise Refused("one event watcher (changed / every) per trigger")
    tgt, expr = write
    head, _, prop = str(tgt).partition(".")
    if not prop:
        raise Refused(f"write target is ?x.prop or <eid>.prop: {tgt!r}")
    v = _var(head)
    if v and v not in bound:
        raise Refused(f"?{v} is bound by nothing (WHERE isa/is)")
    _check(_parse(expr), bound)
    return {"when": when, "where": where, "write": [str(tgt), str(expr)]}


# ---- the VM ----------------------------------------------------------------------------------

class VM:
    def __init__(self, est, clock=time.time):
        self.est = est
        self.clock = clock
        self._q: collections.deque = collections.deque()
        self._lk = threading.Lock()
        self._wake = threading.Event()
        self.dropped = 0
        self.refused: dict[str, int] = {}
        self.fired: dict[str, int] = {}
        self.errors: dict[str, str] = {}
        self._seen: dict[str, float] = {}         # eid -> the last write on it (quiet)
        self._state: dict[str, dict] = {}         # trigger -> binding -> {lv, every, stamp}
        self._thread = None
        self._trig: list | None = None             # the trigger eids (None: rescan)

    # -- hearing ----------------------------------------------------------------------------
    def attach(self) -> None:
        W.on_write(self._hear)

    def detach(self) -> None:
        W.off_write(self._hear)

    def _hear(self, w: dict) -> None:
        """The write listener: note and queue, never write (part A's rule)."""
        path = str(w.get("path") or "")
        if path.startswith(_TRIG_PREFIX):
            self._trig = None                       # a trigger born, changed or gone
        if not path.startswith("rel:"):
            self._seen[path.split(".", 1)[0]] = float(self.clock())     # the VM's own clock
        with self._lk:
            if len(self._q) >= QUEUE_MAX:
                self._q.popleft()
                self.dropped += 1
            self._q.append(w)
        self._wake.set()

    def note_seen(self, eid: str, ts: float) -> None:
        self._seen[eid] = float(ts)

    # -- defining ---------------------------------------------------------------------------
    def define(self, name: str, when, where, write, owner: str | None = None) -> str:
        spec = _validate(when, where, write)
        eid = MEMBRANE_NS + f"{CLASS}:{_slug(name)}"
        doc = QuantumEntity(eid=eid, cls=CLASS,
                            properties={"name": str(name), **spec, "owner": owner or "",
                                        "from": [str(a[1]) for a in spec["when"] if len(a) > 1],
                                        "to": spec["write"][0]})
        old = (self.est.docs or {}).get(eid)
        if old is not None:
            old.properties = {**(old.properties or {}), **doc.properties}
            self.est.upsert(old)
        else:
            self.est.upsert(doc)
        self._state.pop(eid, None)
        return eid

    def triggers(self) -> list:
        """The trigger entities, cached until a Trigger entity is written."""
        if self._trig is None:
            self._trig = [eid for eid, d in list((self.est.docs or {}).items()) if d.cls == CLASS]
        docs = self.est.docs or {}
        return [docs[e] for e in self._trig if e in docs]

    # -- binding ----------------------------------------------------------------------------
    def _isa(self, ent, cls: str) -> bool:
        c = str(cls).lower()
        if (ent.cls or "").lower() == c:
            return True
        from .night import ancestors
        for a in ancestors(self.est, ent):
            if a.eid.lower().endswith(":" + c) or str((a.properties or {}).get("name") or "").lower() == c:
                return True
        return False

    def _candidates(self, atom) -> list[str]:
        if atom[0] == "is":
            e = self.est.resolve(str(atom[2]), exact=True) if hasattr(self.est, "resolve") else None
            e = e or (self.est.docs or {}).get(str(atom[2]))
            return [e.eid] if e is not None else []
        return [eid for eid, e in list((self.est.docs or {}).items())
                if e.cls != CLASS and self._isa(e, atom[2])]

    def _bindings(self, spec: dict, fixed: dict | None = None) -> list[dict]:
        out = [dict(fixed or {})]
        for a in spec.get("where") or []:
            if a[0] not in ("isa", "is"):
                continue
            v = _var(a[1])
            nxt = []
            for b in out:
                if v in b:
                    ent = (self.est.docs or {}).get(b[v])
                    ok = ent is not None and (ent.eid in self._candidates(a) if a[0] == "is"
                                              else self._isa(ent, a[2]))
                    if ok:
                        nxt.append(b)
                else:
                    nxt.extend({**b, v: eid} for eid in self._candidates(a))
            out = nxt
        res = []
        for b in out:
            env = self._env(b)
            try:
                if all(evaluate(a[1], env) for a in spec.get("where") or [] if a[0] == "filter"):
                    res.append(b)
            except Refused:
                pass
        return res

    def _env(self, binding: dict) -> dict:
        return {v: EntityView(self.est, eid) for v, eid in binding.items()}

    def _resolve_ref(self, ref, binding):
        v = _var(ref) if isinstance(ref, str) else None
        if v:
            return binding.get(v)
        e = self.est.resolve(str(ref), exact=True) if hasattr(self.est, "resolve") else None
        return (e.eid if e is not None else None) or (str(ref) if str(ref) in (self.est.docs or {}) else None)

    def _pos(self, eid):
        e = (self.est.docs or {}).get(eid) if eid else None
        return e.position if e is not None and e.position is not None else None

    # -- watchers ---------------------------------------------------------------------------
    def _level(self, a, binding, env, st, now) -> bool:
        verb = a[0]
        if verb in ("near", "away"):
            p, q = self._pos(self._resolve_ref(a[1], binding)), self._pos(self._resolve_ref(a[2], binding))
            if p is None or q is None:
                return False
            d = _dist(p, q)
            return d <= float(a[3]) if verb == "near" else d >= float(a[3])
        if verb in ("above", "below"):
            try:
                v = evaluate(a[1], env)
            except Refused:
                return False
            if v is None:
                return False
            return float(v) > float(a[2]) if verb == "above" else float(v) < float(a[2])
        if verb == "pos":
            p = self._pos(self._resolve_ref(a[1], binding))
            if p is None:
                return False
            stamp = st.get("stamp")
            if stamp is None:
                st["stamp"] = [float(x) for x in p]
                return False
            if float(np.linalg.norm(_vec(p) - _vec(stamp))) > float(a[2]):
                st["stamp"] = [float(x) for x in p]
                return True
            return False
        if verb == "vel":
            ent = (self.est.docs or {}).get(self._resolve_ref(a[1], binding))
            try:
                return ent is not None and float(ent.ring_velocity(now) or 0.0) > float(a[2])
            except Exception:
                return False
        if verb == "align":
            ent = (self.est.docs or {}).get(self._resolve_ref(a[1], binding))
            srv = getattr(self.est, "_srv", None)
            fld = getattr(srv, "efield", None) if srv is not None else None
            if ent is None or fld is None:
                return False
            try:
                from .eids import field_id
                s = fld.get(field_id(ent.eid))
                return s is not None and float(s.coherence or 0.0) < float(a[2])
            except Exception:
                return False
        if verb == "around":
            me = self._resolve_ref(a[1], binding)
            p = self._pos(me)
            if p is None:
                return False
            within = sorted(e for e, d in list((self.est.docs or {}).items())
                            if e != me and d.position is not None and _dist(p, d.position) <= float(a[2]))
            changed = st.get("within") is not None and st.get("within") != within
            st["within"] = within
            return changed
        if verb == "quiet":
            eid = self._resolve_ref(a[1], binding)
            last = self._seen.get(eid)
            return last is not None and now - last >= float(a[2])
        return False

    def _consider(self, tr, binding: dict, now: float, event: dict | None = None) -> None:
        p = tr.properties or {}
        key = json.dumps(binding, sort_keys=True)
        st = self._state.setdefault(tr.eid, {}).setdefault(key, {})
        env = self._env(binding)
        ev_atoms = [a for a in p.get("when") or [] if a[0] in EVENT_VERBS]
        lv_atoms = [a for a in p.get("when") or [] if a[0] in LEVEL_VERBS]
        if ev_atoms:
            a = ev_atoms[0]
            if a[0] == "changed":
                if event is None:
                    return
                head, _, prop = str(a[1]).partition(".")
                if event.get("path") != f"{self._resolve_ref(head, binding)}.{prop}":
                    return
            else:                                   # every
                if event is not None:
                    return
                last = st.get("every")
                if last is not None and now - last < float(a[1]):
                    return
                st["every"] = now
            if all(self._level(x, binding, env, st, now) for x in lv_atoms):
                self._fire(tr, binding, env, now)
            return
        level = all(self._level(x, binding, env, st, now) for x in lv_atoms)
        was = st.get("lv", False)
        st["lv"] = level
        if level and not was:
            self._fire(tr, binding, env, now)

    # -- firing -----------------------------------------------------------------------------
    def _fire(self, tr, binding, env, now) -> None:
        p = tr.properties or {}
        ch = W.current()
        via = list((ch or {}).get("via") or [])
        if tr.eid in via:
            last = len(via) - 1 - via[::-1].index(tr.eid)
            if "self" not in via[last + 1:]:
                self.refused[tr.eid] = self.refused.get(tr.eid, 0) + 1
                return
        tgt, expr = p.get("write") or [None, None]
        head, _, prop = str(tgt).partition(".")
        eid = self._resolve_ref(head, binding)
        ent = (self.est.docs or {}).get(str(eid)) if eid else None
        if ent is None or not prop:
            self.errors[tr.eid] = f"no target {tgt!r}"
            return
        try:
            value = evaluate(str(expr), env)
        except Exception as e:                      # noqa: BLE001
            self.errors[tr.eid] = f"{expr!r}: {e}"[:200]
            return
        via = via + [tr.eid] + (["self"] if p.get("owner") == SELF else [])
        base = ch or {"id": f"field-{tr.eid.rsplit(':', 1)[-1]}-{int(now * 1000)}", "root": "field"}
        with W.resume(dict(base, via=via)), W.as_writer(f"trigger:{tr.eid}"):
            if prop == "position":
                ent.position = list(value) if value is not None else None
            else:
                ent.properties = {**(ent.properties or {}), prop: value}
            self.est.upsert(ent)
        self.fired[tr.eid] = self.fired.get(tr.eid, 0) + 1

    # -- running ----------------------------------------------------------------------------
    def drain(self, max_n: int = 1000) -> int:
        """Work the queued writes (outside any writer's call): the `changed` triggers
        on the written path, and the level triggers re-read on the written entity."""
        n = 0
        while n < max_n:
            with self._lk:
                if not self._q:
                    break
                w = self._q.popleft()
            n += 1
            path = str(w.get("path") or "")
            if path.startswith("rel:"):
                continue
            eid = path.split(".", 1)[0]
            trs = [t for t in self.triggers() if t.eid != eid]
            if not trs:
                continue
            now = float(self.clock())
            with W.resume(w.get("chain")):
                for tr in trs:
                    spec = tr.properties or {}
                    for v in {_var(str(a[1]).split(".", 1)[0]) for a in spec.get("when") or []
                              if len(a) > 1 and isinstance(a[1], str)} - {None}:
                        for b in self._bindings(spec, {v: eid}):
                            if any(a[0] == "changed" for a in spec.get("when") or []):
                                self._consider(tr, b, now, event=w)
                            elif not any(a[0] == "every" for a in spec.get("when") or []):
                                self._consider(tr, b, now)
        return n

    def sweep(self, now: float | None = None) -> None:
        """The thinker's beat: the geometric and timed watchers over every binding."""
        now = float(now if now is not None else self.clock())
        for tr in self.triggers():
            spec = tr.properties or {}
            if any(a[0] == "changed" for a in spec.get("when") or []):
                continue
            for b in self._bindings(spec):
                self._consider(tr, b, now)

    def start(self) -> None:
        """The VM's own worker: drains the queue as writes arrive."""
        if self._thread is not None:
            return

        def run():
            while True:
                self._wake.wait(5.0)
                self._wake.clear()
                try:
                    self.drain()
                except Exception as e:              # noqa: BLE001
                    self.errors["drain"] = repr(e)[:200]
        self._thread = threading.Thread(target=run, daemon=True, name="ch-vm")
        self._thread.start()

    def report(self) -> dict:
        return {"triggers": len(self.triggers()), "queued": len(self._q), "dropped": self.dropped,
                "fired": dict(self.fired), "refused": dict(self.refused), "errors": dict(self.errors)}
