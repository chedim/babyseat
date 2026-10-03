"""ENTITY QUERY LANGUAGE (the lightweight graph query, the operator's
2026-10-02 ask): one line grammar enumerating/listing/counting the
entity graph:

    kind:<kind|*>            the eid's kind segment (f/s/m/c/tool/...)
    name~:<prefix>           the eid/name's case-insensitive prefix
    parent:<spec>            rows whose PARENT CHAIN includes spec
    children:<spec>          the inverse (the ISA walk reversed)
    near:<free text>         semantic nearness (the related lane's read)
    status:<s>               the properties.status filter
    <op>&<op>                conjunction; '|' disjunction (OR of filters)
    count: true (the param)  render 'N entity(ies)' instead of rows

$0 (the RAM graph scan; no embedder without 'near:'). Rows carry
{eid, class, name, summary, count, parent}; the tool caps k (default
20). Unknown verbs raise ValueError (the honest refusal).
"""
from __future__ import annotations

from membrane.entities import MEMBRANE_NS


# ---- the grammar ------------------------------------------------------------
def parse(q: str) -> list[list[tuple[str, str]]]:
    """'a & b | c' -> [[('a','a'),('b','b')], [('c','c')]]: an OR of
    AND-blocks (the disjunctive normal form, one line, no nesting)."""
    q = (q or "").strip()
    if not q:
        return [[("match-all", "")]]
    out: list[list[tuple[str, str]]] = []
    for block in q.split("|"):
        terms = []
        for t in block.split("&"):
            t = t.strip()
            if not t:
                continue
            verb, _, val = t.partition(":")
            verb = verb.strip()
            if verb.endswith("~"):
                verb = "name~"            # the prefix-match verb's name
            if not verb:
                continue
            terms.append((verb, val.strip()))
        if not terms:
            raise ValueError("an empty OR block in the query")
        out.append(terms)
    if not out:
        raise ValueError("nothing parsed")
    return out


# ---- the evaluators ---------------------------------------------------------
def _kind_of(eid: str) -> str:
    """The eid's kind segment: 'membrane::f:x' -> 'f'; a cls-named form
    ('membrane::Component:x') -> 'component' (the class IS the kind)."""
    body = eid[len(MEMBRANE_NS):] if eid.startswith(MEMBRANE_NS) else eid
    first = body.split(":", 1)[0]
    return first.lower()


def _ancestor(est, eid: str, target: str, hops: int = 16) -> bool:
    """Is `target` on eid's parent chain? (the ISA walk)."""
    walker = est.docs.get(eid)
    seen = 0
    while walker is not None and seen < hops:
        pp = (walker.properties or {}).get("parent")
        if pp == target:
            return True
        walker = est.docs.get(pp) if pp else None
        seen += 1
    return False


def _matches(est, ent, verb: str, val: str, near_fn=None) -> bool:
    props = ent.properties or {}
    eid = ent.eid
    if verb == "match-all":
        return True
    if verb == "kind":
        want = val.strip().removesuffix("/*").removesuffix(":*").removesuffix("*").rstrip(":")
        if not want:
            return True
        k = _kind_of(eid)
        cls = str(ent.cls or "").lower()
        return k == want.lower() or cls == want.lower()
    if verb in ("name~", "name_prefix"):
        pref = val.strip().lower()
        return pref in eid.lower() or pref in str(
            props.get("name") or "").lower()
    if verb == "parent":
        return _ancestor(est, eid, val.strip())
    if verb == "children":
        kids = {c.eid for c in est.children_of(val.strip())}
        return eid in kids
    if verb == "status":
        return (props.get("status") or "open").lower() == val.strip().lower()
    if verb == "near":
        if near_fn is None:
            return False
        return near_fn(ent)
    raise ValueError(f"unknown query verb: {verb!r}")


# ---- the engine ---------------------------------------------------------------
def run(est, q: str, k: int = 20, count: bool = False,
        near_fn=None) -> list[dict] | str:
    """Compile + run; rows (the render) or the bare count string."""
    blocks = parse(q)
    rows: list[dict] = []
    total = 0
    for ent in list(est.docs.values()):
        props = ent.properties or {}
        hit = False
        for terms in blocks:
            if all(_matches(est, ent, v, val, near_fn) for v, val in terms):
                hit = True
                break
        if not hit:
            continue
        total += 1
        if not count and len(rows) < k:
            rows.append({"eid": ent.eid, "class": ent.cls,
                         "name": str(props.get("name")
                                     or ent.eid[len(MEMBRANE_NS):]),
                         "summary": str(props.get("summary") or "")[:120],
                         "count": int(props.get("cells") or 0),
                         "parent": str(props.get("parent") or ""),
                         "status": str(props.get("status") or "open")})
    if count:
        return f"{total} entity(ies)"
    return rows


def render(rows: list[dict]) -> str:
    """The tool's text form (the model reads rows, not JSON)."""
    if not rows:
        return "no entities match"
    lines = []
    for r in rows:
        st = f"[{r['status']}]" if r["status"] != "open" else ""
        lines.append(f"- {r['eid']} ({r['class']}{st}, {r['count']} cells"
                     f"{', parent ' + r['parent'] if r['parent'] else ''}) "
                     f"{r['name']} -- {r['summary']}")
    return "\n".join(lines)