"""#980 Q3 rows 1-8: the plain entity seeds as one release seed pack ("plain").

tool:clock, tool:executor, tool:filetree, fs:dir/fs:file/fs:code, the language adjectives and the
en executor (tool:language). Same entities and properties the per-module seeders made; the pack
only installs what is missing on a seat's first adoption (an existing -- possibly edited -- entity
is left alone, as before), and later releases go through seedpack's overlay ledger.
"""
from __future__ import annotations

from .entities import CLASS_COMPONENT, CLASS_TOOL, MEMBRANE_NS

PACK_VERSION = 1
NAME = "plain"


def pack() -> dict:
    from . import autonode, fstree, language, night
    ents = [
        {"eid": MEMBRANE_NS + "tool:clock", "cls": CLASS_TOOL, "properties": {
            "name": "clock", "tick_script": night.DEFAULT_CLOCK_TICK_SCRIPT, "tick_every": 1,
            "silent_birth": True}},
        {"eid": MEMBRANE_NS + night.EXECUTOR_EID, "cls": CLASS_TOOL, "properties": {
            "name": "executor",
            "summary": "the sh executor: code linked to it (root "
                       "-execute-> executor) runs here, -executing-> while "
                       "it runs, then -result-> a result entity per run; "
                       "run code with exec(action='run', code=...)",
            "lang": "sh", "silent_birth": True}},
        {"eid": MEMBRANE_NS + "tool:filetree", "cls": CLASS_TOOL, "properties": {
            "name": "filetree", "watch_script": autonode.DEFAULT_FILETREE_WATCH_SCRIPT,
            "watch": {"kind": "file"}, "silent_birth": True}},
        {"eid": language.EXECUTOR, "cls": CLASS_TOOL, "properties": {
            "name": "language", "lang": "en", "silent_birth": True,
            "summary": "the en executor: a sentence linked to it runs as the calls it compiles to"}},
    ]
    for eid, summary in fstree._CLASSES.items():
        ents.append({"eid": eid, "cls": CLASS_COMPONENT, "properties": {
            "name": eid[len(MEMBRANE_NS):], "summary": summary, "silent_birth": True}})
    for name, expr in language.SEED_ADJECTIVES.items():
        ents.append({"eid": language._adj_eid(name), "cls": language.CLASS_ADJECTIVE, "properties": {
            "name": name.lower(), "filter": expr, "silent_birth": True, "seeded": expr}})
    return {"name": NAME, "version": PACK_VERSION, "entities": ents}


def boot(est) -> dict:
    """Load the pack. On a seat's first adoption the legacy per-seeder rules for entities that
    already exist stay (an edited clock survives; an old filetree script / adjective seed migrates)."""
    from . import autonode, language, seedpack
    if est is None:
        return {"installed": 0, "updated": 0, "overlaid": 0}
    p = pack()
    if seedpack.installed_version(est, NAME) == 0:
        docs = est.docs or {}
        if MEMBRANE_NS + "tool:filetree" in docs:
            autonode.seed_filetree_entity(est)          # only migrates the old script
        if any(language._adj_eid(n) in docs for n in language.SEED_ADJECTIVES):
            language.seed(est)                          # refreshes a stale older seed
        p["entities"] = [e for e in p["entities"] if e["eid"] not in docs]
    out = seedpack.load(est, p, release=True)
    if out["installed"]:
        try:
            est.flush()
        except Exception:
            pass
    return out
