"""THE CLIFFHANGER CORE (#576 part B): triggers on the field, no hatches.

A TRIGGER is an entity (class `Trigger`): a reified edge from what it watches to
what it writes --

    WHEN near(?x, op, 0.2) WHERE isa(?x, toy): ?x.close = 1 + (x.close or 0)

- WHEN: watchers, edge-triggered per binding. Level watchers (near, away, above,
  below, vel, align, quiet) fire when the conjunction turns true and re-arm when
  it turns false; `pos` (moved further than d since its stamp) and `around` (the
  neighbours within z changed) are one-call pulses, true only on the evaluation
  that sees the change. The event watchers -- changed (a write on a path), every
  (a period) -- fire on the event while the level ones hold; at most one per
  trigger. The first evaluation of a binding only SEEDS its state: a trigger
  fires on a crossing it saw, never on what was already true (a restart or a
  redefine re-fires nothing).
- WHERE: pattern atoms binding the variables -- isa(?x, Class) (its class or an
  ISA ancestor), is(?x, eid), filter(expr). No XPath.
- WRITE: [target, expr]: `?x.prop` or `<eid>.prop` (`.position` included) gets the
  value. Expressions: a small safe language over scalars and first-class vectors
  (+ - * /, comparisons, and/or/not, x.prop reads; dot, norm, distance (1 - cos,
  the watches' metric), unit, abs, min, max). A written value is a scalar, a
  string, None or a list of finite numbers -- never an entity, never inf/nan; a
  position keeps its dimension. Some properties are never a trigger's to write:
  code (`command`, scripts) and triggers' own definitions.

THE VM hears every field write (writes.on_write, part A) and only QUEUES it; its
one worker thread drains the queue and runs the sweeps the thinker asks for
(request_sweep), so all evaluation is on one thread, one trigger failing never
stops the others (`errors`), and nothing runs while the seat is paused.

THE LOOP RULE: a firing whose trigger is already in its chain's `via` is refused
unless `self` came after it. A trigger owned by self appends `self` -- Blurp is
the one place a loop may run, and only with a learner attached (part C: its
energy bounds it); without one, self's triggers are inert. Self's ownership is
SIGNED by define (an HMAC under a key file beside the store, never an entity): a
trigger that merely says owner=self is not self's. The entity API never edits a
trigger, only this seat's triggers run (an imported one never does), and a chain
arriving over the relay cannot claim self (writes.from_headers)."""
from __future__ import annotations

import ast
import collections
import hashlib
import hmac
import itertools
import os
import secrets
import json
import math
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
RECENT_FIRINGS = 200            # firings remembered for the field map's Blurp overlay (a buffer bound)
EXPR_MAX = 400                  # an expression's length (characters)
NODES_MAX = 64                  # and its size (AST nodes)
VEC_MAX = 8192                  # the longest vector an expression handles

EVENT_VERBS = {"changed", "every"}
LEVEL_VERBS = {"near", "away", "above", "below", "pos", "vel", "align", "around", "quiet"}
VERBS = EVENT_VERBS | LEVEL_VERBS
_DENY = {"command", "owner", "owner_sig", "when", "where", "write", "shadow", "predicted"}
KEY_FILE = ".ch-key"            # beside the store: signs self's ownership (never an entity)


class Refused(ValueError):
    pass


def _denied(prop: str) -> bool:
    return prop in _DENY or prop.startswith("script_") or prop.endswith("_script")


# ---- expressions -----------------------------------------------------------------------------

def _vec(v):
    a = np.asarray(v, dtype=float)
    if a.ndim != 1 or len(a) > VEC_MAX:
        raise Refused("a vector is a flat list of numbers")
    return a


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
_PY = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
       ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}
_NP = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide}
_CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}   # #738: an eid in a list (a join)
_ALLOWED = (ast.Constant, ast.Name, ast.List, ast.Tuple, ast.Dict, ast.Attribute, ast.BinOp, ast.UnaryOp,
            ast.BoolOp, ast.Compare, ast.Call, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div,
            ast.Not, ast.USub, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
            ast.In, ast.NotIn)


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
            return _out(np.asarray(ent.position, dtype=float)) if ent.position is not None else None
        return (ent.properties or {}).get(attr)


_AST: dict = {}


def _parse(expr: str):
    """Parsed once, checked for length and size; cached."""
    expr = str(expr)
    hit = _AST.get(expr)
    if hit is not None:
        return hit
    if len(expr) > EXPR_MAX:
        raise Refused(f"an expression is at most {EXPR_MAX} characters")
    try:
        node = ast.parse(expr, mode="eval").body
    except (SyntaxError, RecursionError, ValueError) as e:
        raise Refused(f"not an expression: {expr[:60]!r} ({e})") from None
    if sum(1 for _ in ast.walk(node)) > NODES_MAX:
        raise Refused(f"an expression is at most {NODES_MAX} nodes")
    if len(_AST) > 4096:
        _AST.clear()
    _AST[expr] = node
    return node


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Refused(f"not a number: {v!r}")
    return v


def _arith(op, a, b):
    if isinstance(a, list) or isinstance(b, list):
        x = _vec(a) if isinstance(a, list) else _num(a)
        y = _vec(b) if isinstance(b, list) else _num(b)
        with np.errstate(all="raise"):
            try:
                return _out(_NP[op](x, y))
            except (FloatingPointError, ValueError) as e:
                raise Refused(f"vector arithmetic: {e}") from None
    if isinstance(a, str) and isinstance(b, str) and op is ast.Add:
        return a + b
    a, b = _num(a), _num(b)
    try:
        r = _PY[op](a, b)
    except (ZeroDivisionError, OverflowError) as e:
        raise Refused(f"arithmetic: {e}") from None
    if isinstance(r, float) and not math.isfinite(r):
        raise Refused("not a finite number")
    return r


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
    if isinstance(node, ast.Dict):                      # a call's arguments (#576 C)
        if not all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys):
            raise Refused("a dict's keys are literal strings")
        return {k.value: _ev(v, env) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Attribute):
        base = _ev(node.value, env)
        if not isinstance(base, EntityView):
            raise Refused("attributes are read on entities only")
        return base.get(node.attr)
    if isinstance(node, ast.BinOp) and type(node.op) in _PY:
        return _arith(type(node.op), _ev(node.left, env), _ev(node.right, env))
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not v
        if isinstance(node.op, ast.USub):
            return _out(-_vec(v)) if isinstance(v, list) else -_num(v)
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
            try:
                if not _CMP[type(op)](left, right):
                    return False
            except TypeError:
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BUILTINS \
            and not node.keywords:
        try:
            return BUILTINS[node.func.id](*[_ev(a, env) for a in node.args])
        except Refused:
            raise
        except (TypeError, ValueError) as e:
            raise Refused(f"{node.func.id}: {e}") from None
    raise Refused(f"not allowed in an expression: {type(node).__name__}")


def _check(node, names: set) -> None:
    """The definition-time check: the same whitelist, no evaluation (iterative)."""
    for n in ast.walk(node):
        if not isinstance(n, _ALLOWED):
            raise Refused(f"not allowed in an expression: {type(n).__name__}")
        if isinstance(n, ast.Dict) and not all(isinstance(k, ast.Constant) and isinstance(k.value, str)
                                               for k in n.keys):
            raise Refused("a dict's keys are literal strings")
        if isinstance(n, ast.Name) and n.id not in names and n.id not in _NAMES and n.id not in BUILTINS:
            raise Refused(f"unbound name {n.id!r}")
        if isinstance(n, ast.Attribute) and (n.attr.startswith("_") or not isinstance(n.value, ast.Name)):
            raise Refused("attributes are read on entities only")
        if isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id in BUILTINS
                                            and not n.keywords):
            raise Refused("only the built-ins may be called")


def evaluate(expr: str, env: dict):
    return _ev(_parse(expr), env)


def _writable(v, _depth: int = 3):
    out = _writable_inner(v, _depth)
    if isinstance(out, (dict, list)) and len(json.dumps(out)) > W.ARG_MAX:
        raise Refused(f"a written value is at most {W.ARG_MAX} characters")
    return out


def _writable_inner(v, _depth: int = 3):
    """What a trigger may write: a scalar, a string, None, a list of finite numbers,
    or (a call's arguments) a dict / list of those, nested at most 3 deep."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            raise Refused("not a finite number")
        return v
    if isinstance(v, list) and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                   and math.isfinite(float(x)) for x in v):
        return [float(x) for x in v]
    if isinstance(v, dict) and all(isinstance(k, str) for k in v) and _depth > 0:
        return {k: _writable_inner(x, _depth - 1) for k, x in v.items()}      # a call's arguments
    if isinstance(v, list) and _depth > 0:
        return [_writable_inner(x, _depth - 1) for x in v]
    raise Refused(f"cannot write a {type(v).__name__}")


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
        if not isinstance(a, (list, tuple)) or not a or a[0] not in ("isa", "is", "filter"):
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
        if not isinstance(a, (list, tuple)) or not a or a[0] not in VERBS:
            raise Refused(f"unknown watcher {a!r} ({', '.join(sorted(VERBS))})")
        n_event += a[0] in EVENT_VERBS
        for x in a[1:]:
            v = _var(str(x).split(".", 1)[0]) if isinstance(x, str) else None
            if v and v not in bound:
                raise Refused(f"?{v} is bound by nothing (WHERE isa/is)")
        try:
            if a[0] in ("above", "below"):
                _check(_parse(a[1]), bound)
                float(a[2])
            elif a[0] != "changed":
                float(a[-1])
        except (TypeError, ValueError, IndexError):
            raise Refused(f"{a[0]}: a number is missing or wrong in {a!r}") from None
    if n_event > 1:
        raise Refused("one event watcher (changed / every) per trigger")
    tgt, expr = write
    head, _, prop = str(tgt).rpartition(".")
    if not head or not prop:
        raise Refused(f"write target is ?x.prop or <eid>.prop: {tgt!r}")
    if _denied(prop):
        raise Refused(f"a trigger cannot write {prop!r}")
    v = _var(head)
    if v and v not in bound:
        raise Refused(f"?{v} is bound by nothing (WHERE isa/is)")
    _check(_parse(expr), bound)
    return {"when": [list(a) for a in when], "where": [list(a) for a in where],
            "write": [str(tgt), str(expr)]}


# ---- self's ownership: signed ------------------------------------------------------------------

def _key(est) -> bytes:
    """The key that signs self's triggers: a file beside the store (mode 0600, never
    an entity, so no tool can read or write it), in RAM for a store without a path."""
    hit = getattr(est, "_ch_key", None)
    if hit:
        return hit
    from pathlib import Path
    path = getattr(est, "path", None)
    key = None
    if path:
        f = Path(path).parent / KEY_FILE
        try:
            key = f.read_bytes()
        except OSError:
            key = secrets.token_bytes(32)
            try:
                fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(key)
            except FileExistsError:
                key = f.read_bytes()
            except OSError:
                pass
    key = key or secrets.token_bytes(32)
    try:
        est._ch_key = key
    except Exception:
        pass
    return key


def _sig(est, eid: str) -> str:
    return hmac.new(_key(est), eid.encode(), hashlib.sha256).hexdigest()


# ---- the VM ----------------------------------------------------------------------------------

class VM:
    def __init__(self, est, clock=time.time, paused=lambda: False):
        self.est = est
        self.clock = clock
        self.paused = paused
        self._q: collections.deque = collections.deque()
        self._qlk = threading.Lock()
        self._run = threading.RLock()                # all evaluation, one at a time
        self._wake = threading.Event()
        self._sweep_wanted = False
        self._stop = False
        self.dropped = 0
        self.refused: dict[str, int] = {}
        self.fired: dict[str, int] = {}
        self.errors: dict[str, str] = {}
        self._seen: dict[str, float] = {}         # eid -> the last write on it (quiet)
        self._state: dict[str, dict] = {}         # trigger -> binding -> {lv, every, stamp, within}
        self._thread = None
        self._trig: tuple | None = None            # (version, eids)
        self._trig_v = 0
        self._spec: dict[str, tuple] = {}          # trigger -> (definition fingerprint, spec or None)
        self._seq = itertools.count()
        self.learner = None                        # #576 C: ch_learn.Learner (Blurp's plasticity)
        self.recent: collections.deque = collections.deque(maxlen=RECENT_FIRINGS)   # the map's overlay
        now = float(self.clock())
        for eid in list((est.docs or {}).keys()):
            self._seen[eid] = now                  # everything was last seen when the VM came up

    # -- hearing ----------------------------------------------------------------------------
    def attach(self) -> None:
        W.on_write(self._hear)

    def detach(self) -> None:
        W.off_write(self._hear)

    @staticmethod
    def _split(w: dict) -> tuple[str, str]:
        if w.get("eid") is not None:
            return str(w["eid"]), str(w.get("prop") or "")
        path = str(w.get("path") or "")
        return path, ""

    def _hear(self, w: dict) -> None:
        """The write listener: note and queue, never write (part A's rule)."""
        if str(w.get("path") or "").startswith("rel:"):
            return
        eid, prop = self._split(w)
        if eid.startswith(_TRIG_PREFIX):
            self._trig_v += 1                       # a trigger born, changed or gone
        if w.get("new") is None and not prop:       # a removal: forget it
            self._seen.pop(eid, None)
            self._state.pop(eid, None)
        elif not str(w.get("writer") or "").startswith("trigger:"):
            self._seen[eid] = float(self.clock())   # the VM's own writes are not the entity's activity
        with self._qlk:
            if len(self._q) >= QUEUE_MAX:
                self._q.popleft()
                self.dropped += 1
            self._q.append(w)
        self._wake.set()

    # -- defining ---------------------------------------------------------------------------
    def define(self, name: str, when, where, write, owner: str | None = None) -> str:
        spec = _validate(when, where, write)
        eid = MEMBRANE_NS + f"{CLASS}:{_slug(name)}"
        props = {"name": str(name), **spec, "owner": owner or "",
                 "from": [str(a[1]) for a in spec["when"] if len(a) > 1], "to": spec["write"][0]}
        props["owner_sig"] = _sig(self.est, eid) if owner == SELF else ""   # only define signs self's
        old = (self.est.docs or {}).get(eid)
        if old is not None:
            for k, v in props.items():
                old.properties[k] = v
            self.est.upsert(old)
        else:
            self.est.upsert(QuantumEntity(eid=eid, cls=CLASS, properties=props))
        self._state.pop(eid, None)
        return eid

    def _is_selfs(self, tr) -> bool:
        """Self's trigger: owner self AND signed by define (a forged owner is not)."""
        p = tr.properties or {}
        sig = str(p.get("owner_sig") or "")
        return p.get("owner") == SELF and bool(sig) and hmac.compare_digest(sig, _sig(self.est, tr.eid))

    def triggers(self) -> list:
        """This seat's trigger entities (an imported one never runs), cached until a
        Trigger entity is written."""
        hit = self._trig
        if hit is None or hit[0] != self._trig_v:
            v = self._trig_v
            hit = (v, [eid for eid, d in list((self.est.docs or {}).items())
                       if d.cls == CLASS and eid.startswith(_TRIG_PREFIX)])
            self._trig = hit
        docs = self.est.docs or {}
        return [docs[e] for e in hit[1] if e in docs]

    def _valid(self, tr) -> dict | None:
        """A trigger's spec, validated when first used or changed -- an entity of class
        Trigger made any other way is checked like a definition."""
        p = tr.properties or {}
        fp = json.dumps([p.get("when"), p.get("where"), p.get("write")], default=str)
        hit = self._spec.get(tr.eid)
        if hit is None or hit[0] != fp:
            try:
                hit = (fp, _validate(p.get("when"), p.get("where"), p.get("write")))
            except (Refused, TypeError, ValueError) as e:
                hit = (fp, None)
                self.errors[tr.eid] = f"invalid: {e}"[:200]
            self._spec[tr.eid] = hit
        return hit[1]

    # -- binding ----------------------------------------------------------------------------
    def _isa(self, ent, cls: str, memo: dict) -> bool:
        key = (ent.eid, str(cls).lower())
        if key in memo:
            return memo[key]
        c = str(cls).lower()
        ok = (ent.cls or "").lower() == c
        if not ok:
            from .night import ancestors
            ok = any(a.eid.lower().endswith(":" + c) or str((a.properties or {}).get("name") or "").lower() == c
                     for a in ancestors(self.est, ent))
        memo[key] = ok
        return ok

    def _lookup(self, ref) -> str | None:
        ref = str(ref)
        docs = self.est.docs or {}
        if ref in docs:
            return ref
        e = self.est.resolve(ref, exact=True) if hasattr(self.est, "resolve") else None
        return e.eid if e is not None else None

    def _candidates(self, atom, memo) -> list[str]:
        if atom[0] == "is":
            e = self._lookup(atom[2])
            return [e] if e else []
        key = ("cands", str(atom[2]).lower())
        if key not in memo:
            memo[key] = [eid for eid, e in list((self.est.docs or {}).items())
                         if e.cls != CLASS and self._isa(e, atom[2], memo)]
        return memo[key]

    def _bindings(self, spec: dict, memo: dict, fixed: dict | None = None,
                  atoms: dict | None = None, env: dict | None = None) -> list[dict]:
        """The WHERE atoms' bindings. `atoms`: more atom kinds, name -> fn(atom, bindings) ->
        bindings (the banner rules' edge / recent / mine, graphbanner); `env`: plain values
        the filters may read besides the bound entities."""
        out = [dict(fixed or {})]
        for a in spec.get("where") or []:
            if atoms and a[0] in atoms:
                out = atoms[a[0]](a, out)
                continue
            if a[0] not in ("isa", "is"):
                continue
            v = _var(a[1])
            nxt = []
            for b in out:
                if v in b:
                    ent = (self.est.docs or {}).get(b[v])
                    if ent is not None and (ent.eid in self._candidates(a, memo) if a[0] == "is"
                                            else self._isa(ent, a[2], memo)):
                        nxt.append(b)
                else:
                    nxt.extend({**b, v: eid} for eid in self._candidates(a, memo))
            out = nxt
        res = []
        for b in out:
            env_b = {**(env or {}), **self._env(b)}
            try:
                if all(evaluate(a[1], env_b) for a in spec.get("where") or [] if a[0] == "filter"):
                    res.append(b)
            except Refused:
                pass
        return res

    def _env(self, binding: dict) -> dict:
        return {v: EntityView(self.est, eid) for v, eid in binding.items()}

    def _ref(self, ref, binding):
        v = _var(ref) if isinstance(ref, str) else None
        return binding.get(v) if v else self._lookup(ref)

    def _pos(self, eid):
        e = (self.est.docs or {}).get(eid) if eid else None
        return e.position if e is not None and e.position is not None else None

    # -- watchers ---------------------------------------------------------------------------
    def _level(self, a, binding, env, st, now) -> bool:
        verb = a[0]
        if verb in ("near", "away"):
            p, q = self._pos(self._ref(a[1], binding)), self._pos(self._ref(a[2], binding))
            if p is None or q is None:
                return False
            d = _dist(p, q)
            return d <= float(a[3]) if verb == "near" else d >= float(a[3])
        if verb in ("above", "below"):
            v = evaluate(a[1], env)
            if v is None or isinstance(v, (bool, str, list)):
                return False
            return float(v) > float(a[2]) if verb == "above" else float(v) < float(a[2])
        if verb == "pos":                                # a pulse: true on the call that sees the move
            p = self._pos(self._ref(a[1], binding))
            if p is None:
                return False
            stamp = st.get("stamp")
            if stamp is None or len(stamp) != len(p):
                st["stamp"] = [float(x) for x in p]
                return False
            if float(np.linalg.norm(np.asarray(p, dtype=float) - np.asarray(stamp, dtype=float))) > float(a[2]):
                st["stamp"] = [float(x) for x in p]
                return True
            return False
        if verb == "vel":
            ent = (self.est.docs or {}).get(self._ref(a[1], binding))
            return ent is not None and float(ent.ring_velocity(now) or 0.0) > float(a[2])
        if verb == "align":
            ent = (self.est.docs or {}).get(self._ref(a[1], binding))
            srv = getattr(self.est, "_srv", None)
            fld = getattr(srv, "efield", None) if srv is not None else None
            if ent is None or fld is None:
                return False
            from .eids import field_id
            s = fld.get(field_id(ent.eid))
            return s is not None and float(s.coherence or 0.0) < float(a[2])
        if verb == "around":                             # a pulse: true when the neighbours changed
            me = self._ref(a[1], binding)
            p = self._pos(me)
            if p is None:
                return False
            within = sorted(e for e, d in list((self.est.docs or {}).items())
                            if e != me and d.position is not None and _dist(p, d.position) <= float(a[2]))
            changed = st.get("within") is not None and st.get("within") != within
            st["within"] = within
            return changed
        if verb == "quiet":
            last = self._seen.get(self._ref(a[1], binding))
            return last is not None and now - last >= float(a[2])
        return False

    def _consider(self, tr, spec, binding: dict, now: float, event: dict | None = None) -> None:
        key = json.dumps(binding, sort_keys=True)
        st = self._state.setdefault(tr.eid, {}).setdefault(key, {})
        env = self._env(binding)
        ev_atoms = [a for a in spec["when"] if a[0] in EVENT_VERBS]
        lv = [self._level(x, binding, env, st, now) for x in spec["when"] if x[0] in LEVEL_VERBS]   # every atom
        level = all(lv)
        if ev_atoms:
            a = ev_atoms[0]
            if a[0] == "changed":
                if event is None:
                    return
                head, _, prop = str(a[1]).rpartition(".")
                eid, wprop = self._split(event)
                if eid != self._ref(head, binding) or wprop != prop:
                    return
            else:                                        # every
                if event is not None:
                    return
                last = st.get("every")
                if last is not None and now - last < float(a[1]):
                    return
                st["every"] = now
            if level:
                self._fire(tr, spec, binding, env, now)
            return
        seeded = "lv" in st
        was = st.get("lv", False)
        st["lv"] = level
        if seeded and level and not was:
            self._fire(tr, spec, binding, env, now)

    # -- firing -----------------------------------------------------------------------------
    def _fire(self, tr, spec, binding, env, now) -> None:
        mine = self._is_selfs(tr)
        if mine and self.learner is None:
            return                                   # self's triggers run only with a learner (its energy)
        ch = W.current()
        via = list((ch or {}).get("via") or [])
        if tr.eid in via:
            last = len(via) - 1 - via[::-1].index(tr.eid)
            if "self" not in via[last + 1:]:
                self.refused[tr.eid] = self.refused.get(tr.eid, 0) + 1
                return
        tgt, expr = spec["write"]
        head, _, prop = tgt.rpartition(".")
        eid = self._ref(head, binding)
        ent = (self.est.docs or {}).get(str(eid)) if eid else None
        if ent is None or ent.cls == CLASS:
            self.errors[tr.eid] = f"no target {tgt!r}"
            return
        try:
            value = _writable(evaluate(expr, env))
            if prop == "position" and (not isinstance(value, list) or
                                       (ent.position is not None and len(value) != len(ent.position))):
                raise Refused("a position is a list of the field's dimension")
        except Refused as e:
            self.errors[tr.eid] = f"{expr!r}: {e}"[:200]
            return
        if mine:
            via = via + [tr.eid, "self"]
            via = via[len(via) - 1:]                 # before self nothing matters to the rule: keep it short
        else:
            via = via + [tr.eid]
        base = ch or {"id": f"field-{tr.eid.rsplit(':', 1)[-1]}-{int(now * 1000)}-{next(self._seq)}",
                      "root": "field"}
        with W.resume(dict(base, via=via)), W.as_writer(f"trigger:{tr.eid}"):
            mode = "live"
            if mine:
                mode = self.learner.on_fire(tr, f"{ent.eid}.{prop}", value)   # #576 C/H: shadow or asleep (it suggests, never acts)
            if mode == "skip":
                return
            was = ent.properties.get(prop)
            if mode == "shadow":                     # the prediction lands on the candidate itself
                ch_now = W.current() or {}
                tr.properties["predicted"] = {"path": self.learner._expected(f"{ent.eid}.{prop}"), "value": value,
                                              "chain": ch_now.get("id"), "ts": now}
                self.est.upsert(tr)
            elif prop == "position":
                ent.position = value
                self.est.upsert(ent)
            else:
                ent.properties[prop] = value          # in place: a concurrent writer's keys survive
                self.est.upsert(ent)
            if mine and mode == "live":
                self.learner.landed(f"{ent.eid}.{prop}", value, was)   # #740: Blurp's own write, landed
        self.fired[tr.eid] = self.fired.get(tr.eid, 0) + 1
        src = binding.get("x") or next(iter(binding.values()), None) or ent.eid
        self.recent.append({"trig": tr.eid, "from": src, "to": ent.eid, "ts": now, "mode": mode})

    # -- running ----------------------------------------------------------------------------
    def _vars_for(self, spec, ent, memo) -> list[str]:
        """The variables the written entity can bind (its isa / is atoms)."""
        out = []
        for a in spec.get("where") or []:
            if a[0] == "is" and self._lookup(a[2]) == ent.eid:
                out.append(_var(a[1]))
            elif a[0] == "isa" and self._isa(ent, a[2], memo):
                out.append(_var(a[1]))
        return [v for v in out if v]

    def _one(self, tr, fn) -> None:
        try:
            fn()
        except Exception as e:                       # noqa: BLE001 -- one trigger never stops the others
            self.errors[tr.eid] = f"{type(e).__name__}: {e}"[:200]

    def drain(self, max_n: int = 1000) -> int:
        """Work the queued writes: the `changed` triggers on the written path, and the
        level triggers re-read for every variable the written entity can bind."""
        n = 0
        while n < max_n and not self.paused():
            with self._qlk:
                if not self._q:
                    break
                w = self._q.popleft()
            n += 1
            eid, _prop = self._split(w)
            with self._run:
                ent = (self.est.docs or {}).get(eid)
                trs = [t for t in self.triggers() if t.eid != eid]
                if trs and ent is not None and ent.cls != CLASS:
                    now = float(self.clock())
                    memo: dict = {}
                    with W.resume(w.get("chain")):
                        for tr in trs:
                            self._one(tr, lambda tr=tr: self._on_write(tr, ent, w, now, memo))
                if self.learner is not None:
                    with W.resume(w.get("chain")):
                        try:
                            self.learner.observe(w)        # #576 C: score the predictions, then guess
                        except Exception as e:             # noqa: BLE001
                            self.errors["learner"] = f"{type(e).__name__}: {e}"[:200]
        return n

    def _on_write(self, tr, ent, w, now, memo) -> None:
        spec = self._valid(tr)
        if spec is None:
            return
        kinds = {a[0] for a in spec["when"]}
        if "every" in kinds:
            return
        if "changed" in kinds:
            a = next(a for a in spec["when"] if a[0] == "changed")
            head = str(a[1]).rpartition(".")[0]
            if not _var(head):                           # a literal eid: every binding hears it
                if self._lookup(head) == ent.eid:
                    for b in self._bindings(spec, memo):
                        self._consider(tr, spec, b, now, event=w)
                return
        for v in self._vars_for(spec, ent, memo):
            for b in self._bindings(spec, memo, {v: ent.eid}):
                if "changed" in kinds:
                    self._consider(tr, spec, b, now, event=w)
                else:
                    self._consider(tr, spec, b, now)

    def sweep(self, now: float | None = None) -> None:
        """The geometric and timed watchers over every binding (the seat runs it on the
        worker: request_sweep)."""
        if self.paused():
            return
        now = float(now if now is not None else self.clock())
        with self._run:
            memo: dict = {}
            for tr in self.triggers():
                def go(tr=tr):
                    spec = self._valid(tr)
                    if spec is None or any(a[0] == "changed" for a in spec["when"]):
                        return
                    for b in self._bindings(spec, memo):
                        self._consider(tr, spec, b, now)
                self._one(tr, go)

    def request_sweep(self) -> None:
        """The thinker's beat: ask the worker for a sweep (its thread, its pace)."""
        self._sweep_wanted = True
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop = False

        def run():
            while not self._stop:
                self._wake.wait(5.0)
                self._wake.clear()
                if self._stop:
                    break
                try:
                    self.drain()
                    if self._sweep_wanted:
                        self._sweep_wanted = False
                        self.sweep()
                except Exception as e:              # noqa: BLE001
                    self.errors["worker"] = repr(e)[:200]
        self._thread = threading.Thread(target=run, daemon=True, name="ch-vm")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=5)

    def report(self) -> dict:
        return {"triggers": len(self.triggers()), "queued": len(self._q), "dropped": self.dropped,
                "fired": dict(self.fired), "refused": dict(self.refused), "errors": dict(self.errors)}
