"""VERBS ARE ENTITIES (the operator, 2026-11-01: "map cliffhanger methods onto verb
entities"; "different linked nouns might have different verb meanings/implementations";
"some words can be both verbs and nouns").

A method names a verb: its name read as words ("close", "run_tests" -> "run tests") is
`verb:<lemma>` (class Verb), and the verb -implemented-by-> every method of that name, one
per class or object that has it. The verb entity mirrors its implementations
(`implemented_by`) and each method points back (`verb`), so dispatch needs no relations
store; the edges put it on the graph.

ONE VERB, MANY MEANINGS: "close the PR" and "close the goal" are the same verb with two
implementations. Dispatch picks by the object, in cliffhanger's order: the object's own
method, then its classes' (ISA), then a global one. A noun nothing implements the verb for
gets none: another class's meaning is never borrowed.

THE WORD AND THE ROLE: verb:run -word-> w:run, the field's word entity. The word is one
entity; being a verb is a role it plays in a sentence (the command position now; the
parse's part of speech once language is parsed). "The last run" is a noun and refers to
entities, never to verb:run.

Method names keep working: night.resolve_method still dispatches by name.
"""
from __future__ import annotations

import re
import time

from .entities import MEMBRANE_NS, QuantumEntity

VERB = MEMBRANE_NS + "verb:"
CLASS_VERB = "Verb"
SOURCE = "verbs"


def lemma(name: str) -> str:
    return " ".join(re.split(r"[\s_\-]+", str(name or "").strip().lower())).strip()


def verb_eid(name: str) -> str:
    return VERB + lemma(name).replace(" ", "_")


def _rel(est):
    from .night import _relations_backend_of
    srv = getattr(est, "_srv", None)
    return _relations_backend_of(srv) if srv is not None else (None, None)


def _implements(d) -> bool:
    p = d.properties or {}
    return bool(p.get("method_name")) and not p.get("retired") and not p.get("abstract")


def note(est, method) -> str | None:
    """A method into its verb (made on first use). -> the verb's eid."""
    p = method.properties or {}
    if not p.get("method_name"):
        return None
    lem = lemma(p["method_name"])
    eid = verb_eid(lem)
    v = (est.docs or {}).get(eid)
    if v is None:
        v = QuantumEntity(eid=eid, cls=CLASS_VERB, properties={
            "name": lem, "word": "w:" + lem.split(" ", 1)[0], "implemented_by": [],
            "silent_birth": True, "born_ts": time.time()})
    vp = dict(v.properties or {})
    impl = [m for m in (vp.get("implemented_by") or []) if m != method.eid]
    if _implements(method):
        impl.append(method.eid)
    vp["implemented_by"] = impl
    v.properties = vp
    est.upsert(v, embedder=None)
    if p.get("verb") != eid:
        method.properties = {**p, "verb": eid}
        est.upsert(method, embedder=None)
    _alias(est, method, lem)
    be, origin = _rel(est)
    if be is not None:
        from . import relations as R
        R.upsert(be, origin, eid, "word", vp["word"], SOURCE, ts=time.time())
        if _implements(method):
            R.upsert(be, origin, eid, "implemented-by", method.eid, SOURCE, ts=time.time())
        else:
            from .night import _supersede_edge
            _supersede_edge(be, origin, eid, method.eid, rel="implemented-by",
                            receipt={"op": "retired"})
    return eid


def _command_verb(word: str) -> str | None:
    """`word` read as a command ("check it"): its verb lemma, or None (living language's parser)."""
    try:
        from . import language as LL
        if not LL.available():
            return None
        if len(word) < 3 or not word.isalpha():
            return None
        lem = LL._doc(word)[0].lemma_.lower() or word
        t = LL._doc(f"{lem} the thing")[0]       # a command on an object: "check the thing"
    except Exception:
        return None
    return t.lemma_.lower() if t.pos_ == "VERB" and t.dep_ == "ROOT" else None


def _alias(est, method, lem: str) -> None:
    """A compound name ("pr checks") under each of its words that reads as a command verb, with
    the rest of the name as the noun it expects (verb check, noun pr): `aliased` on that verb,
    apart from exact names -- it maps only where the sentence's object agrees."""
    words = lem.split()
    if len(words) < 2:
        return
    for i, w in enumerate(words):
        vl = _command_verb(w)
        if not vl or vl == lem:
            continue
        noun = " ".join(words[:i] + words[i + 1:])
        eid = verb_eid(vl)
        v = (est.docs or {}).get(eid) or QuantumEntity(eid=eid, cls=CLASS_VERB, properties={
            "name": vl, "word": "w:" + vl, "implemented_by": [], "silent_birth": True, "born_ts": time.time()})
        vp = dict(v.properties or {})
        al = [a for a in (vp.get("aliased") or []) if a.get("method") != method.eid]
        if _implements(method):
            al.append({"method": method.eid, "noun": noun})
        vp["aliased"] = al
        v.properties = vp
        est.upsert(v, embedder=None)


def aliased(est, verb: str) -> list:
    """[(method, the noun it expects)] the verb means through compound names."""
    v = (est.docs or {}).get(verb_eid(verb))
    if v is not None and (v.properties or {}).get("same_as") and lemma(v.properties["same_as"]) != lemma(verb):
        return aliased(est, v.properties["same_as"])
    docs = est.docs or {}
    return [(docs[a["method"]], a.get("noun") or "") for a in ((v.properties or {}).get("aliased") or []) if v
            and a.get("method") in docs and _implements(docs[a["method"]])] if v is not None else []


def boot(srv) -> dict:
    """Boot: every method noted on its verb (idempotent; a retired one stops implementing).
    Aliases are rebuilt from scratch, and a verb left meaning nothing is removed (a stale alias
    of an older rule, a method that went)."""
    est = srv.entity_store
    for d in list((est.docs or {}).values()):
        if d.cls == CLASS_VERB and (d.properties or {}).get("aliased"):
            d.properties = {**(d.properties or {}), "aliased": []}
    seen = set()
    for d in list((est.docs or {}).values()):
        if (d.properties or {}).get("method_name"):
            e = note(est, d)
            if e:
                seen.add(e)
    gone = [d.eid for d in list((est.docs or {}).values()) if d.cls == CLASS_VERB
            and not (d.properties or {}).get("implemented_by") and not (d.properties or {}).get("aliased")
            and not (d.properties or {}).get("same_as")]          # a taught synonym means something
    for eid in gone:
        try:
            est.remove(eid)
        except Exception:
            est.docs.pop(eid, None)
    return {"verbs": len(seen), "removed": len(gone)}


def _impls(est, verb: str) -> list:
    v = (est.docs or {}).get(verb_eid(verb))
    if v is None:
        return []
    docs = est.docs or {}
    return [docs[m] for m in ((v.properties or {}).get("implemented_by") or [])
            if m in docs and _implements(docs[m])]


def implementation(est, obj, verb: str):
    """(the method, its owner eid) that `verb` means on `obj`: own, then its classes', then
    a global one. (None, None): nothing implements it for this noun. No verb entity yet:
    the method of that name (night.resolve_method)."""
    from .night import ancestors, resolve_method
    same = ((est.docs or {}).get(verb_eid(verb)) or QuantumEntity(eid="", cls="")).properties or {}
    if same.get("same_as") and lemma(same["same_as"]) != lemma(verb):
        return implementation(est, obj, same["same_as"])          # a taught synonym ("finish" = close)
    if (est.docs or {}).get(verb_eid(verb)) is None:
        return resolve_method(est, obj, lemma(verb).replace(" ", "_"))
    impls = _impls(est, verb)
    for owner in [obj] + ancestors(est, obj):
        for m in impls:
            if owner.eid in ((m.properties or {}).get("method_of") or []):
                return m, owner.eid
    for m in impls:
        if not (m.properties or {}).get("method_of"):
            return m, None
    return None, None


def implementers(est, verb: str) -> list[str]:
    """The classes and objects that give `verb` a meaning."""
    out = []
    for m in _impls(est, verb):
        out += [o for o in ((m.properties or {}).get("method_of") or []) if o not in out]
    return out
