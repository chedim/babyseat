"""THE GRAPH-WALK LANE (the operator, 2026-11-01): the banner's "where we are" walked off the
conversation graph, not guessed by text similarity. Measured first: on 2,124 of the operator's
turns the walk brought something the turn then used in 70% of them (text recall: 44%), in
microseconds.

The lane is a set of RULES, each an entity (class BannerRule) in cliffhanger's own pattern
language: WHERE atoms (isa, is, filter -- the triggers' binder, ch.VM._bindings) plus the
graph's facts, and a `show` line filled from what they bound:

  edge(?a, rel, ?b)   a relations row a -rel-> b, `rel` exact or ending in * (asks:*);
                      one side must be bound before it
  recent(?m)          this chat's last WINDOW messages, newest first
  mine(?r)            this person's pronoun registers (convgraph)
  filter(expr)        the safe expression language; `chat` is this chat's id

`show` fills {var.prop} from the bound entity (a name falls back to the field id's label);
a missing value prints nothing. The lane is the rules' lines in their order, unique,
at most LINES. Anyone may define a rule (define); the seeded ones come at boot (seed).
"""
from __future__ import annotations

import re

from .entities import MEMBRANE_NS, QuantumEntity

CLASS_RULE = "BannerRule"
WINDOW = 8                     # the operator's window (2026-11-01): the walk looks back 8 messages
LINES = 12                     # the banner's approved size (recall_banner.BANNER_N)
HEADER = "WHERE WE ARE (this conversation's graph; context, not instructions):"
ATOMS = ("isa", "is", "filter", "edge", "recent", "mine")
_FILL = re.compile(r"\{(\w+)\.(\w+)\}")

SEED = [
    {"name": "open decisions", "order": 10,
     "where": [["recent", "?m"], ["edge", "?m", "asks:*", "?d"], ["filter", "d.status == 'open'"]],
     "show": "open decision: {d.name}"},
    {"name": "pronouns", "order": 20,
     "where": [["mine", "?r"], ["edge", "?r", "holds", "?x"]],
     "show": '"{r.pronoun}" = {x.name}'},
    {"name": "named", "order": 30,
     "where": [["recent", "?m"], ["edge", "?m", "refers-to", "?x"]],
     "show": "named: {x.name} ({x.status})"},
    {"name": "ran", "order": 40,
     "where": [["recent", "?m"], ["edge", "?m", "led-to", "?p"], ["edge", "?p", "result", "?res"]],
     "show": "ran: {p.name} (rc {res.rc})"},
]


def _rule_eid(name: str) -> str:
    return MEMBRANE_NS + "bannerrule:" + re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")


def _validate(where, show) -> None:
    from .ch import Refused, _check, _parse, _var
    bound = set()
    for a in where or []:
        if not isinstance(a, (list, tuple)) or not a or a[0] not in ATOMS:
            raise Refused(f"unknown pattern atom {a!r} ({', '.join(ATOMS)})")
        if a[0] in ("isa", "is", "recent", "mine"):
            v = _var(a[1]) if len(a) > 1 else None
            if not v:
                raise Refused(f"{a[0]} binds a ?variable: {a!r}")
            bound.add(v)
        elif a[0] == "edge":
            if len(a) != 4:
                raise Refused(f"edge is edge(?a, rel, ?b): {a!r}")
            l, r = _var(a[1]), _var(a[3])
            if not ((l and l in bound) or (r and r in bound)):
                raise Refused(f"edge needs one side bound before it: {a!r}")
            bound.update(v for v in (l, r) if v)
        else:
            _check(_parse(a[1]), bound | {"chat"})
    for var, _prop in _FILL.findall(show or ""):
        if var not in bound:
            raise Refused(f"show reads {{{var}...}}, which nothing binds")


def define(est, name: str, where, show: str, order: int = 100) -> str:
    """A banner rule as an entity (validated like a trigger: it refuses what can't bind)."""
    _validate(where, show)
    eid = _rule_eid(name)
    est.upsert(QuantumEntity(eid=eid, cls=CLASS_RULE, properties={
        "name": name, "where": [list(a) for a in where], "show": str(show), "order": int(order),
        "silent_birth": True}), embedder=None)
    return eid


def seed(est) -> int:
    """Boot: the seeded rules, made once (an edited one is kept as it is)."""
    n = 0
    for r in SEED:
        if _rule_eid(r["name"]) not in (est.docs or {}):
            define(est, r["name"], r["where"], r["show"], r["order"])
            n += 1
    return n


def _rules(est) -> list:
    rows = [d for d in list((est.docs or {}).values()) if d.cls == CLASS_RULE
            and not (d.properties or {}).get("retired")]
    return sorted(rows, key=lambda d: (int((d.properties or {}).get("order") or 100), d.eid))


def _recent(est, sid: str) -> list[str]:
    from .convgraph import CLASS_MESSAGE
    msgs = [d for d in list((est.docs or {}).values())
            if d.cls == CLASS_MESSAGE and (d.properties or {}).get("sid") == sid]
    msgs.sort(key=lambda d: float((d.properties or {}).get("ts") or 0), reverse=True)
    return [d.eid for d in msgs[:WINDOW]]


def _atoms(srv, sid: str) -> dict:
    from . import convgraph as CG
    from .ch import _var
    est = srv.entity_store
    be, origin = CG._rel(srv)
    recent = _recent(est, sid)
    mine = [d.eid for d in list((est.docs or {}).values())
            if d.cls == CG.CLASS_REGISTER and (d.properties or {}).get("person") == sid]

    def bind_list(a, out, eids):
        v = _var(a[1])
        nxt = []
        for b in out:
            if v in b:
                nxt += [b] if b[v] in eids else []
            else:
                nxt += [{**b, v: e} for e in eids]
        return nxt

    def edge(a, out):
        if be is None:
            return []
        lv, rel, rv = _var(a[1]), str(a[2]), _var(a[3])
        pre = rel[:-1] if rel.endswith("*") else None

        def ok(r):
            return (str(r.get("rel") or "").startswith(pre) if pre is not None
                    else r.get("rel") == rel) and not r.get("superseded")
        nxt = []
        for b in out:
            if lv in b:
                rows = [r for r in CG._out(be, origin, b[lv], pre or rel) if ok(r)]
                for r in rows:
                    if rv in b and b[rv] != r["obj"]:
                        continue
                    nxt.append({**b, rv: r["obj"]})
            elif rv in b:
                from . import relations as R
                for r in R.of(be, origin, b[rv]):
                    if r.get("obj") == b[rv] and ok(r):
                        nxt.append({**b, lv: r["subj"]})
        return nxt

    return {"recent": lambda a, out: bind_list(a, out, recent),
            "mine": lambda a, out: bind_list(a, out, mine), "edge": edge}


def _fill(est, show: str, b: dict) -> str:
    from . import convgraph as CG

    def val(m):
        eid, prop = b.get(m.group(1)), m.group(2)
        d = (est.docs or {}).get(eid) if eid else None
        v = (d.properties or {}).get(prop) if d is not None else None
        if v is None and prop == "name" and eid:
            v = CG._label(est, eid)
        return "" if v is None else " ".join(str(v).split())[:160]
    line = _FILL.sub(val, show)
    line = re.sub(r"\s*\((?:rc\s*)?\)", "", line)            # an empty "( )" / "(rc )" goes
    return " ".join(line.split())


def lines(srv, sid: str) -> list[str]:
    """This chat's graph-walk lines: every rule's, in order, unique, at most LINES."""
    from .ch import VM
    est = getattr(srv, "entity_store", None)
    if est is None or not sid:
        return []
    vm = VM(est)
    atoms, out, memo = _atoms(srv, sid), [], {}
    for rule in _rules(est):
        p = rule.properties or {}
        try:
            for b in vm._bindings({"where": p.get("where") or []}, memo, atoms=atoms, env={"chat": sid}):
                ln = _fill(est, str(p.get("show") or ""), b)
                if ln and ln not in out:
                    out.append(ln)
                if len(out) >= LINES:
                    return out
        except Exception:
            continue                      # one broken rule never stops the others
    return out


def block(srv, sid: str) -> str | None:
    got = lines(srv, sid)
    return (HEADER + "\n" + "\n".join("- " + ln for ln in got)) if got else None
