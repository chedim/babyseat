"""CLASS INDUCTION (2026-10-09, the operator: "it also has entity inheritance
to do generalizations, why is it not doing that?").

The live seat held 2081 entities and not one ISA link: inheritance existed
and nobody built a hierarchy. This proposes the classes the field already
shows -- siblings sharing a name stem ('ha:light.port_strip',
'ha:light.aft_cabin' -> 'ha:light'; 'seat:organs:vessel' ... ->
'seat:organs') -- and the seat accepts one with ONE call: inherit with a
pattern links every member and mints the class when it is missing. The
engine proposes; the seat decides (its hierarchy, its call).
"""
from __future__ import annotations

import fnmatch
import re
import time

MIN_MEMBERS = 3
MAX_LINK = 150                 # the live seat's 'ha switch' family alone is 87
PROPOSALS_SHOWN = 3
_CACHE_S = 300.0
# the bookkeeping kinds: not things in the world, never a class
_SKIP_HEADS = {"shell", "symbols", "models", "error", "boot", "goal", "junk-anchor",
               "m", "w", "urls", "files", "tool", "timeline", "now", "seat", "e"}
_CLASS_OK = {"Component", "Sensor", "Endpoint", "Config"}
_CLASS_PREFIX = re.compile(r"^[A-Z][A-Za-z]+:")


def short_name(eid: str) -> str:
    """'membrane::Component:ha:light.port_strip' -> 'ha:light.port_strip'."""
    s = eid.split("::", 1)[-1]
    return _CLASS_PREFIX.sub("", s, count=1)


def family_key(eid: str) -> str | None:
    s = short_name(eid)
    head = re.split(r"[:.]", s, maxsplit=1)[0].lower()
    if head in _SKIP_HEADS or not re.search(r"[:.]", s):
        return None
    i = max(s.rfind(":"), s.rfind("."))
    stem = s[:i]
    return stem if stem and len(stem) >= 3 and len(s) - i > 2 else None


def label_of(stem: str) -> str:
    return re.sub(r"[:._]+", " ", stem).strip()


def _cls(doc) -> str:
    return str(getattr(doc, "cls", None) or getattr(doc, "class_", None)
               or (doc.properties or {}).get("class") or "")


def proposals(est) -> list[dict]:
    """The classes the names show, biggest first; families already linked
    (any member with a parent) are not proposed again."""
    if est is None:
        return []
    fams: dict[str, list] = {}
    for eid, d in list((est.docs or {}).items()):
        c = _cls(d)
        if c and c not in _CLASS_OK:
            continue
        k = family_key(eid)
        if k:
            fams.setdefault(k, []).append(d)
    out = []
    for stem, members in fams.items():
        if len(members) < MIN_MEMBERS:
            continue
        if any((m.properties or {}).get("parents") for m in members):
            continue                      # already classified
        out.append({"stem": stem, "label": label_of(stem), "n": len(members),
                    "pattern": stem + "*",
                    "examples": list(dict.fromkeys(short_name(m.eid) for m in members))[:3]})
    out.sort(key=lambda p: -p["n"])
    return out


def proposals_line(srv) -> str | None:
    cache = getattr(srv, "_class_props_cache", None)
    now = time.time()
    if cache is None or now - cache[0] > _CACHE_S:
        cache = (now, proposals(getattr(srv, "entity_store", None)))
        srv._class_props_cache = cache
    ps = cache[1][:PROPOSALS_SHOWN]
    if not ps:
        return None
    items = [f"'{p['label']}' ({p['n']}: {', '.join(p['examples'])}...)" for p in ps]
    return ("class proposals -- members would share one class's scripts (a getter on the "
            "class runs on each member); accept with entity(action='inherit', "
            f"spec='{ps[0]['pattern']}', parents=['{ps[0]['label']}']): " + "; ".join(items))


def matches(est, pattern: str) -> list:
    pat = pattern.strip()
    out = []
    for eid, d in list((est.docs or {}).items()):
        if fnmatch.fnmatchcase(short_name(eid), pat) or fnmatch.fnmatchcase(eid, pat):
            out.append(d)
    return out


def inherit_many(est, pattern: str, parents: list[str], create: bool = True) -> dict:
    """ONE call classifies a family: every entity matching the pattern gets
    the parents; a parent that does not exist yet is minted as a class."""
    from .entities import CLASS_COMPONENT
    members = matches(est, pattern)
    if not members:
        return {"ok": False, "error": f"no entity matches {pattern!r}"}
    if len(members) > MAX_LINK:
        return {"ok": False, "error": f"{len(members)} entities match {pattern!r} "
                                      f"(cap {MAX_LINK}) -- narrow the pattern"}
    pids, minted = [], []
    for p in parents:
        doc = est._resolve_spec(p)
        if doc is None and create:
            r = est.add(cls=CLASS_COMPONENT, name=p,
                        summary=f"class of {len(members)} ({pattern}): their shared scripts live here")
            doc = (est.docs or {}).get(r.get("eid", "")) if isinstance(r, dict) else None
            if doc is not None:
                minted.append(doc.eid)
        if doc is None:
            return {"ok": False, "error": f"no parent matches {p!r}"}
        pids.append(doc.eid)
    linked = []
    for m in members:
        if m.eid in pids:
            continue
        from .entities import MultiParentOps
        r = MultiParentOps.set_parents(est, m.eid, pids)
        if r.get("ok"):
            linked.append(m.eid)
    return {"ok": True, "pattern": pattern, "parents": pids, "minted": minted,
            "linked": linked, "child": f"{len(linked)} entities ({pattern})",
            "added": pids}
