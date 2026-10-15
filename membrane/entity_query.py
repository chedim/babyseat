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

import re

from membrane.entities import MEMBRANE_NS
from membrane.eids import legacy_to_canon

# ---- THE KIND ALIAS FAMILY (the 'kind:file:* is empty' exhibit) ----
# The graph's kind spellings come in three shapes per family: the eid's
# legacy head ('files:', 'symbols:'), the canonical one-letter kind
# ('f:', 's:'), and the operator's word ('file', 'symbol'). A kind
# query matches THE FAMILY -- both directions collapse at the read.
_KIND_FAMILY = {
    "file": ("file", "files", "f"),
    "symbol": ("symbol", "symbols", "s"),
    "model": ("model", "models", "m"),
    "command": ("command", "commands", "shell", "c"),
    "url": ("url", "urls", "u"),
    "port": ("port",),
    "tool": ("tool",),
    "word": ("word", "words", "w"),
    "phrase": ("phrase", "phrases", "p"),
    "session": ("session", "sessions", "s2"),
    "claim": ("claim", "claims", "s3"),
    "ctx": ("ctx",),
    "ha": ("ha",),
    "component": ("component",),
    "cron": ("cron",),
}
_KIND_ALIASES: dict[str, frozenset[str]] = {}
for _canon, _fam in _KIND_FAMILY.items():
    for _w in _fam:
        _KIND_ALIASES.setdefault(_w, frozenset(_fam))


def _kind_family(want: str) -> frozenset[str]:
    """The want-word's family (the whole set when the word is unknown:
    the honest miss = only itself)."""
    return _KIND_ALIASES.get(want.lower().strip(), frozenset({want.lower()}))


def _is_guest(eid: str, props: dict | None = None) -> bool:
    """THE GUEST FLAVOR (the two-universes datum): an eid that names a
    FOREIGN namespace (entity::<x>::, episodic::) is a guest -- the
    pull lane's imported items; the seat's locals are membrane::-*.
    The props' explicit flags (guest/imported_by) second it."""
    if eid.startswith(MEMBRANE_NS):
        if props and (props.get("guest") or props.get("imported_by")):
            return True
        return False
    if eid.startswith("entity::") or eid.startswith("episodic::"):
        return True
    return bool(props and (props.get("guest") or props.get("imported_by")))


# ---- the grammar ------------------------------------------------------------
GRAMMAR = ("kind:<kind> | name~:<text> | parent:<spec> | children:<spec> | "
           "near:<free text> | status:<s> | guest:true, joined with & (and) "
           "or | (or); e.g. 'kind:component & name~:battery'")
_VERBS = {"kind", "name~", "name_prefix", "parent", "children", "near",
          "status", "guest", "match-all"}
_VERB_ALIAS = {"class": "kind", "cls": "kind", "type": "kind",
               "name": "name~", "eid": "name~", "id": "name~"}
_TERM = re.compile(r"^\s*([a-z_]+)\s*(~:|~=|~|:|=)\s*(.*)$", re.IGNORECASE | re.DOTALL)


def _term(t: str) -> tuple[str, str]:
    if t.strip().lower() in _VERBS:
        return t.strip().lower(), ""        # a bare verb ('match-all')
    if t.strip() == "*":
        return "match-all", ""
    m = _TERM.match(t)
    if not m:
        raise ValueError(f"cannot read the query term {t!r}; the grammar: {GRAMMAR}")
    verb, op, val = m.group(1).lower(), m.group(2), m.group(3).strip()
    if op.startswith("~"):
        verb = "name~" if verb in ("name", "eid", "id") else verb + "~"
        if verb.endswith("~") and verb != "name~":
            verb = "name~"
    verb = _VERB_ALIAS.get(verb, verb)
    if verb not in _VERBS:
        raise ValueError(f"unknown query verb: {verb!r}; the grammar: {GRAMMAR}")
    return verb, val


def parse(q: str) -> list[list[tuple[str, str]]]:
    """'a & b | c' -> [[('a','a'),('b','b')], [('c','c')]]: an OR of
    AND-blocks (the disjunctive normal form, one line, no nesting). The
    loose forms parse too ('name~x', 'name=x', 'kind=c', 'class:x', AND/OR
    words); prose answers with the grammar."""
    q = (q or "").strip()
    if not q:
        return [[("match-all", "")]]
    q = re.sub(r"\s+(?:AND|and)\s+", " & ", q)
    q = re.sub(r"\s+(?:OR|or)\s+", " | ", q)
    out: list[list[tuple[str, str]]] = []
    for block in q.split("|"):
        terms = []
        for t in block.split("&"):
            t = t.strip()
            if not t:
                continue
            terms.append(_term(t))
        if not terms:
            raise ValueError("an empty OR block in the query")
        out.append(terms)
    if not out:
        raise ValueError("nothing parsed")
    return out


# ---- the evaluators ---------------------------------------------------------
def _kind_of(eid: str) -> str:
    """The eid's kind segment: 'membrane::f:x' -> 'f'; a cls-named form
    ('membrane::Component:x') -> 'component' (the class IS the kind).

    A class-embedded pool entity ('membrane::Component:files:x') folds to
    its canonical field id first ('f:x'), so 'kind:file' sees the files:
    pool inside the class (issue #99) instead of reading 'component'."""
    canon = legacy_to_canon(eid)
    if canon:
        return canon.split(":", 1)[0].lower()
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
        fam = _kind_family(want)
        k = _kind_of(eid).lower()
        cls = str(ent.cls or "").lower()
        return k in fam or cls in fam
    if verb == "guest":
        want = val.strip().lower() in ("true", "yes", "1", "only")
        return _is_guest(eid, props) == want
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
    raise ValueError(f"unknown query verb: {verb!r}; the grammar: {GRAMMAR}")


# ---- the engine ---------------------------------------------------------------
def run(est, q: str, k: int = 20, count: bool = False,
        near_fn=None) -> list[dict] | str:
    """Compile + run; rows (the render) or the bare count string.
    THE UNIVERSE PARTITION (the two-universes datum): a guest (the
    pull lane's foreign-namespace eids) is INVISIBLE unless the query
    asks for it (-- guest:true / any explicit guest term) -- the
    surfaces stop disagreeing because each query's universe is named."""
    blocks = parse(q)
    asks_guests = any(v == "guest" for terms in blocks for v, _ in terms)
    rows: list[dict] = []
    total = 0
    for ent in list(est.docs.values()):
        props = ent.properties or {}
        if not asks_guests and _is_guest(ent.eid, props):
            continue
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