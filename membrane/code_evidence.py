"""THE CODE AS EVIDENCE: the codebase itself is a standing observation.

E25's port case: a compact note claimed opencode "cannot select another
port"; the conversation and the tool output never showed otherwise -- only
the CODE did (`_pick_listen_port`: "the probe walks up from it"). So every
definition becomes a CODE event -- `qual(sig) in file: <docstring head>` --
mentioning its own symbol entity (module-qualified, collision-free) and its
file, and the call graph becomes relations (calls, defined_in). A changed
definition gets a new event (the text hash differs); the old one is marked
superseded (evidence() skips it; history keeps it)."""

from __future__ import annotations

import re

from . import streams as ST

_SENT_END = re.compile(r"(?<=[.!?])\s+")


def sig_str(sig: dict | None) -> str:
    if not sig:
        return ""
    parts = [a if d is None else f"{a}={d}" for a, d in sig.get("args") or []]
    if sig.get("vararg"):
        parts.append("*" + sig["vararg"])
    parts += [a if d is None else f"{a}={d}" for a, d in sig.get("kwonly") or []]
    if sig.get("kwarg"):
        parts.append("**" + sig["kwarg"])
    return ", ".join(parts)


def doc_head(doc: str, n: int = 2) -> str:
    d = " ".join((doc or "").split())
    return " ".join(_SENT_END.split(d)[:n])[:400]


def code_text(full: str, info: dict) -> str:
    qual, file = info.get("qual") or full, info.get("file", "")
    head = f"{qual}({sig_str(info.get('sig'))})" if info.get("kind") != "class" else f"class {qual}"
    doc = doc_head(info.get("doc", ""))
    return f"{head} in {file}: {doc}" if doc else f"{head} in {file}"


def code_events(graph: dict | None, rels=None, ts: float | None = None) -> list:
    """One CODE event per definition (all, or only those in `rels`)."""
    if not graph:
        return []
    want = set(rels) if rels is not None else None
    out = []
    for full, info in sorted((graph.get("defs") or {}).items()):
        if want is not None and info.get("file") not in want:
            continue
        out.append(ST.Event(
            stream="code", role="code", text=code_text(full, info), ts=ts, src="codegraph",
            code={"full": full, "file": info.get("file"), "qual": info.get("qual"),
                  "mod": info.get("mod"), "line": info.get("line"),
                  "kind": info.get("kind"), "sig": sig_str(info.get("sig")),
                  "doc": (info.get("doc") or "")[:600]}))
    return out


def code_relations(graph: dict | None, rels=None) -> list[tuple[str, str, str]]:
    """(subject, relation, object) triples: s:A calls s:B (only when the
    callee's short name resolves to ONE definition), s:A defined_in f:x."""
    if not graph:
        return []
    defs = graph.get("defs") or {}
    short = graph.get("short_index") or {}
    want = set(rels) if rels is not None else None
    out = []
    for full, info in sorted(defs.items()):
        if want is not None and info.get("file") not in want:
            continue
        out.append((f"s:{full}", "defined_in", f"f:{info.get('file')}"))
        for call in info.get("calls") or []:
            targets = short.get(call.rsplit(".", 1)[-1], [])
            if len(targets) == 1 and targets[0] != full:
                out.append((f"s:{full}", "calls", f"s:{targets[0]}"))
    return list(dict.fromkeys(out))


def supersede(field, rels, keep_keys) -> int:
    """Mark this origin's older CODE events for the given files superseded
    (not in `keep_keys`: the events just written for their current defs)."""
    keep, want, n = set(keep_keys), set(rels), 0
    for key, ev in list(field.b.scan("events", {"origin": field.origin, "stream": "code"})):
        if (ev.get("code") or {}).get("file") in want and key not in keep \
                and ev.get("status") != "superseded":
            ev["status"] = "superseded"
            field.b.upsert("events", key, ev)
            n += 1
    return n


def store_relations(field, graph: dict | None, rels) -> int:
    """Upsert the code relations of the given files; relations those files'
    definitions no longer have are superseded."""
    from . import relations as R
    rels = list(rels or [])
    defs = (graph or {}).get("defs") or {}
    now = set()
    for subj, rel, obj in code_relations(graph, rels):
        file = (defs.get(subj[2:]) or {}).get("file")
        R.upsert(field.b, field.origin, subj, rel, obj, "code", file=file)
        now.add((subj, rel, obj))
    want = set(rels)
    for key, d in list(field.b.scan("relations", {"origin": field.origin, "source": "code",
                                                  "superseded": False})):
        if d.get("file") in want and (d["subj"], d["rel"], d["obj"]) not in now:
            d["superseded"] = True
            field.b.upsert("relations", key, d)
    return len(now)
