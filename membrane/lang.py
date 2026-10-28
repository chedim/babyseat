"""CODE IS A TREE IN THE FIELD (#614, the operator, 2026-10-27: "executors work as
blurp's chat works").

THE NODES. Code is minted as entities, one per node of its syntax tree, each
CONTENT-ADDRESSED: `node:<lang>:<hash>` over (lang, kind, text, the ordered child
ids). The same subtree is one entity wherever it appears (`..`, `git status`), so
the field sees what code shares. A node's children are ordered edges
`-arg:1->`, `-arg:2->` ... (relations rows; the order is in the label, so a
repeated child keeps both places); the node's `args` property mirrors them, so a
seat without a relations store still prints its code.

THE RUN IS AN EDGE (the chat adapter's shape: a write, then the effect):

  root -execute->   <executor>        owed (anyone may write it: the tool, a loop, the reaper)
  root -executing-> <executor>        in progress (replaces execute)
  root -result->    result:<root>:<n> done (replaces executing; one entity per run)

Re-running is re-creating the execute edge; each run keeps its own result, the
newest RESULTS_KEEP per root. Taking an owed edge is a compare-and-set (the
execute row's supersede), so a run happens once however many hands reach it.

THE LANGUAGES. A registry: a parser into nodes (the language's tree-sitter
grammar, #622; one generic conversion for all), a printer out of them, and the
runner its executors use -- `sh` on tool:executor, `js` on the seat's presence
(an opted-in browser tab). An executor entity says its language in `lang` (else
it is the default executor of one); a root of another language is refused.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field as dfield
from typing import Callable

from .entities import CLASS_COMPONENT, MEMBRANE_NS, QuantumEntity

RESULTS_KEEP = 20            # results kept per root (the operator, 2026-10-27)
EXECUTE, EXECUTING, RESULT = "execute", "executing", "result"
SOURCE = "code"


@dataclass
class Node:
    kind: str
    text: str = ""
    args: list = dfield(default_factory=list)


@dataclass
class Lang:
    name: str
    parse: Callable          # text -> Node (raises on what is not code)
    print: Callable          # Node -> text
    executor: str            # its default executor's eid
    runner: Callable         # (srv, text, timeout_s) -> {ok, rc, outcome, head, tail, ms, output, ...}


LANGS: dict[str, Lang] = {}


# ---- parsing: tree-sitter grammars (#622) ---------------------------------------------------

HOLE = "\x00"          # where a child goes in its parent's template
GRAMMARS = {"sh": "tree_sitter_bash", "js": "tree_sitter_javascript"}
_TS: dict = {}
INSTALL = ("pip install tree-sitter tree-sitter-bash tree-sitter-javascript "
           "(where no wheel exists, e.g. Termux or HA on a Raspberry Pi, a C compiler first)")


class NoGrammar(RuntimeError):
    """The language's grammar is not installed: the run surface says what to install."""


def _language(lang: str):
    if lang not in _TS:
        import importlib
        try:
            import tree_sitter as ts
            _TS[lang] = ts.Language(importlib.import_module(GRAMMARS[lang]).language())
        except ImportError as e:
            raise NoGrammar(f"the code tree needs tree-sitter ({e}): {INSTALL}") from None
    return _TS[lang]


def _first_error(n):
    if n.type == "ERROR" or n.is_missing:
        return n
    for c in n.children:
        if c.has_error:
            return _first_error(c)
    return n


def _ts_node(n, src: bytes, a: int, b: int) -> Node:
    """A syntax node -> a Node: a leaf keeps its text; a node keeps its source span
    with a HOLE per named child, so printing is filling the holes."""
    kids = [c for c in n.named_children if a <= c.start_byte and c.end_byte <= b]
    if not kids:
        return Node(n.type, src[a:b].decode())
    tpl, out, last = [], [], a
    for c in kids:
        if c.start_byte < last:                  # overlapping spans: keep the text whole
            return Node(n.type, src[a:b].decode())
        tpl += [src[last:c.start_byte].decode(), HOLE]
        out.append(_ts_node(c, src, c.start_byte, c.end_byte))
        last = c.end_byte
    tpl.append(src[last:b].decode())
    return Node(n.type, "".join(tpl), out)


# A METHOD'S PARAMETERS (#614 part C): {{name}}, or a placeholder already wrapped in
# quotes ('{{x}}' / "{{x}}") -- the value replaces the pair, quoted once (#128)
PARAM = re.compile(r"\{\{\s*([A-Za-z_][\w.:-]*)\s*\}\}")   # entity specs carry ':' and '-'
QUOTED_PARAM = re.compile(r"(['\"]?)\{\{\s*([A-Za-z_][\w.:-]*)\s*\}\}\1")
QUOTE = {"sh": lambda v: __import__("shlex").quote(v), "js": json.dumps}


def _feed(name: str) -> bool:
    """A pipeline feed ({{parent.head}}): its value goes in as the parent printed it."""
    return name == "parent" or name.startswith(("parent.", "parent:"))


def _params_in(n: Node, tok: re.Pattern, raws: list) -> Node:
    """Placeholder words back to `param` nodes (their text: the placeholder as written)."""
    def split(text):
        kids = [Node("param", raws[int(m.group(1))]) for m in tok.finditer(text)]
        return tok.sub(HOLE, text), kids
    if not n.args:
        m = tok.fullmatch(n.text)
        if m:
            return Node("param", raws[int(m.group(1))])
        t, kids = split(n.text)
        return Node(n.kind, t, kids) if kids else n
    parts, tpl, kids = n.text.split(HOLE), [], []
    for i, part in enumerate(parts):
        t, ks = split(part)
        tpl.append(t)
        kids += ks
        if i < len(n.args):
            tpl.append(HOLE)
            kids.append(_params_in(n.args[i], tok, raws))
    return Node(n.kind, "".join(tpl), kids)


def parse(lang: str, text: str, params: bool = False) -> Node:
    """Code -> its Node tree; ValueError for what the grammar cannot read.
    `params` (a method's code): each {{name}} becomes a `param` node."""
    if params and PARAM.search(text or ""):
        tag = secrets.token_hex(3)
        raws: list = []

        def word(m):
            raws.append(m.group(0))
            return f"__mp{tag}_{len(raws) - 1}__"
        n = parse(lang, QUOTED_PARAM.sub(word, text))
        return _params_in(n, re.compile(rf"__mp{tag}_(\d+)__"), raws)
    if HOLE in text:
        raise ValueError("a NUL byte is not code")
    if not text.strip():
        raise ValueError("no code")
    language = _language(lang)
    import tree_sitter as ts
    src = text.encode()
    root = ts.Parser(language).parse(src).root_node
    if root.has_error:
        e = _first_error(root)
        row, col = e.start_point
        raise ValueError(f"a syntax error at line {row + 1}, column {col + 1}: "
                         f"{src[e.start_byte:e.end_byte][:40].decode(errors='replace')!r}")
    try:
        return _ts_node(root, src, 0, len(src))     # the root spans all of it (edge space too)
    except RecursionError:
        raise ValueError("the code nests too deep to keep as a tree")


def bind(n: Node, lookup: Callable, quote: Callable) -> tuple[str, list]:
    """A method's tree printed with its params filled: lookup(name) -> value or
    None; each value quoted once (a feed goes in as printed). -> (code, the
    params with no value -- no silent empties)."""
    missing: list = []

    def walk(n):
        if n.kind == "param":
            m = PARAM.search(n.text)
            name = m.group(1)
            v = lookup(name)
            if _feed(name):
                return n.text if v is None else n.text.replace(m.group(0), str(v))
            if v is None or str(v).strip() == "":
                missing.append(name)
                return n.text
            return quote(json.dumps(v) if isinstance(v, (dict, list)) else str(v))
        if not n.args:
            return n.text
        parts = n.text.split(HOLE)
        out = [parts[0]]
        for kid, rest in zip(n.args, parts[1:]):
            out += [walk(kid), rest]
        return "".join(out)
    return walk(n), missing


def print_(n: Node) -> str:
    if not n.args:
        return n.text
    parts = n.text.split(HOLE)
    out = [parts[0]]
    for kid, rest in zip(n.args, parts[1:]):
        out += [print_(kid), rest]
    return "".join(out)


# ---- sh: tool:executor -----------------------------------------------------------------------

def _sh_run(srv, text: str, timeout_s=None) -> dict:
    from . import night as N
    from .opencode import _efield_on
    t = N.EXEC_TIMEOUT_S if timeout_s is None else min(float(timeout_s), N.EXEC_TIMEOUT_S)
    return N.run_command_entity(_efield_on(srv), srv.entity_store, getattr(srv, "membrane", None),
                                text, timeout_s=t)


LANGS["sh"] = Lang("sh", lambda t: parse("sh", t), print_, MEMBRANE_NS + "tool:executor", _sh_run)


# ---- js: presence, an opted-in browser tab (jsexec.py) ---------------------------------------

def _js_run(srv, text: str, timeout_s=None) -> dict:
    from . import jsexec as JS
    from . import streams as ST
    t = JS.TIMEOUT_S if timeout_s is None else min(float(timeout_s), JS.TIMEOUT_S)
    t0 = time.time()
    r = JS.run(text, timeout=t)
    out, err, rc = str(r.get("text") or ""), str(r.get("err") or ""), r.get("rc")
    shown = out or err
    return {"ok": rc == 0, "rc": rc, "head": shown[:ST.RESULT_HEAD], "tail": shown[-ST.RESULT_TAIL:],
            "outcome": "ok" if rc == 0 else "timeout" if rc == -1 else "refused" if rc == 2 else "error",
            "ms": max(1, int((time.time() - t0) * 1000)), "output": shown, "error": err}


LANGS["js"] = Lang("js", lambda t: parse("js", t), print_, MEMBRANE_NS + "presence", _js_run)


# ---- the store -----------------------------------------------------------------------------

def _rel(srv):
    from .night import _relations_backend_of
    be, origin = _relations_backend_of(srv)
    return be, origin


def _hash(lang: str, n: Node, kids: list[str]) -> str:
    return hashlib.sha1(json.dumps([lang, n.kind, n.text, kids]).encode()).hexdigest()[:16]


def _mint(srv, lang: str, n: Node, be, origin) -> str:
    kids = [_mint(srv, lang, k, be, origin) for k in n.args]
    eid = f"{MEMBRANE_NS}node:{lang}:{_hash(lang, n, kids)}"
    est = srv.entity_store
    if eid not in (est.docs or {}):
        shown = (n.text if not n.args else print_(n) if n.kind == "program"
                 else n.text.replace(HOLE, "…"))
        est.upsert(QuantumEntity(eid=eid, cls=CLASS_COMPONENT, properties={
            "name": " ".join(str(shown or n.text or n.kind).split())[:80], "lang": lang,
            "kind": n.kind, "text": n.text, "args": kids, "silent_birth": True}), embedder=None)
    if be is not None and kids:
        from . import relations as R
        # a node minted while the relations store was off gets its edges once it is on
        if not be.get("relations", R.relation_key(origin, eid, f"arg:{len(kids)}", kids[-1])):
            for i, k in enumerate(kids, 1):
                R.upsert(be, origin, eid, f"arg:{i}", k, SOURCE, ts=time.time())
    return eid


def mint(srv, lang: str, text: str, params: bool = False) -> str:
    """Code -> its root node's eid (the nodes and their arg edges, made once).
    `params`: a method's code, its {{name}}s as `param` nodes."""
    lg = LANGS.get(lang)
    if lg is None:
        raise ValueError(f"no language {lang!r} (known: {', '.join(sorted(LANGS))})")
    be, origin = _rel(srv)
    return _mint(srv, lang, parse(lang, text, params=True) if params else lg.parse(text), be, origin)


def load(est, eid: str) -> Node:
    p = (est.docs[eid].properties or {})
    return Node(str(p.get("kind") or ""), str(p.get("text") or ""), [load(est, k) for k in p.get("args") or []])


def text(est, eid: str) -> str:
    """A root's code, printed from its tree."""
    lang = str((est.docs[eid].properties or {}).get("lang") or "")
    return LANGS[lang].print(load(est, eid))


def _props(est, eid: str) -> dict:
    doc = (est.docs or {}).get(eid)
    return (doc.properties or {}) if doc is not None else {}


def _lang_of_executor(est, ex: str) -> str:
    p = _props(est, ex)
    if p.get("lang"):
        return str(p["lang"])
    return next((n for n, lg in LANGS.items() if lg.executor == ex), "")


# ---- the run -------------------------------------------------------------------------------

_INLINE: set = set()           # (root, executor) a caller is running itself: the listener stays out
_LK = threading.Lock()


def inline(root: str, ex: str) -> bool:
    with _LK:
        return (root, ex) in _INLINE


def request(srv, root: str, ex: str) -> None:
    """Owe a run: root -execute-> executor (the fallback: a pending_run_by mark)."""
    be, origin = _rel(srv)
    if be is not None:
        from . import relations as R
        R.upsert(be, origin, root, EXECUTE, ex, SOURCE, ts=time.time())
        return
    doc = srv.entity_store.docs[root]
    doc.properties["pending_run_by"] = ex
    srv.entity_store.upsert(doc, embedder=None)


def _take(srv, root: str, ex: str) -> bool:
    """Owed -> in progress, once: the execute edge's compare-and-set."""
    be, origin = _rel(srv)
    if be is not None:
        from .night import _supersede_edge
        from . import relations as R
        if not _supersede_edge(be, origin, root, ex, rel=EXECUTE, receipt={"op": "taken"}):
            return False
        R.upsert(be, origin, root, EXECUTING, ex, SOURCE, ts=time.time())
        return True
    est = srv.entity_store
    with _LK:
        doc = (est.docs or {}).get(root)
        if doc is None or (doc.properties or {}).get("pending_run_by") != ex:
            return False
        doc.properties.pop("pending_run_by", None)
        doc.properties["executing_on"] = ex
    est.upsert(doc, embedder=None)
    return True


def _finish(srv, root: str, ex: str, receipt: dict, result: str | None) -> None:
    be, origin = _rel(srv)
    est = srv.entity_store
    doc = est.docs[root]
    if be is not None:
        from .night import _supersede_edge
        from . import relations as R
        _supersede_edge(be, origin, root, ex, rel=EXECUTING, receipt=receipt)
        if result:
            R.upsert(be, origin, root, RESULT, result, SOURCE, ts=time.time())
            live = sorted((r for r in R.of(be, origin, root, RESULT) if r.get("subj") == root),
                          key=lambda r: float(r.get("first_ts") or 0))
            for old in live[:max(0, len(live) - RESULTS_KEEP)]:
                _supersede_edge(be, origin, root, old["obj"], rel=RESULT, receipt={"op": "trimmed"})
                est.remove(old["obj"])
        return
    doc.properties.pop("executing_on", None)
    if result:
        rs = list(doc.properties.get("results") or []) + [result]
        for old in rs[:max(0, len(rs) - RESULTS_KEEP)]:
            est.remove(old)
        doc.properties["results"] = rs[-RESULTS_KEEP:]
    est.upsert(doc, embedder=None)


def process(srv, root: str, ex: str, timeout_s=None) -> dict | None:
    """The executor's reaction to an owed edge: take it, print the tree, run it,
    land a result entity. None when the edge was not owed (someone else took it)."""
    est = srv.entity_store
    if not _take(srv, root, ex):
        return None
    lang = str((est.docs[root].properties or {}).get("lang") or "")
    ex_lang = _lang_of_executor(est, ex)
    if lang != ex_lang:
        why = f"{ex} runs {ex_lang or 'no language'}, this code is {lang}: link it to an executor of its language"
        _finish(srv, root, ex, {"op": "refused", "why": why[:180]}, None)
        return {"ok": False, "root": root, "executor": ex, "error": why}
    code = text(est, root)
    try:
        run = {k: v for k, v in (LANGS[lang].runner(srv, code, timeout_s) or {}).items() if k != "doc"}
    except Exception as e:                  # a runner that raises still ends the run on a result
        run = {"ok": False, "rc": None, "outcome": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
    doc = est.docs[root]
    with _LK:                               # one number per run: a result never overwrites another
        n = int(doc.properties.get("runs") or 0) + 1
        while f"{MEMBRANE_NS}result:{root.rsplit(':', 1)[-1]}:{n}" in (est.docs or {}):
            n += 1
        doc.properties["runs"] = n
    est.upsert(doc, embedder=None)
    res = f"{MEMBRANE_NS}result:{root.rsplit(':', 1)[-1]}:{n}"
    keep = {k: run.get(k) for k in ("rc", "outcome", "head", "tail", "ms", "error") if run.get(k) is not None}
    est.upsert(QuantumEntity(eid=res, cls=CLASS_COMPONENT, properties={
        "name": f"run {n} of {doc.properties.get('name') or root}"[:80], "root": root, "executor": ex,
        "text": code, "ts": time.time(), "silent_birth": True, **keep}), embedder=None)
    _finish(srv, root, ex, {"op": "done", "rc": run.get("rc"), "outcome": run.get("outcome"),
                            "head": str(run.get("head") or "")[:80], "ms": run.get("ms"),
                            "why": str(run.get("error") or "")[:160]}, res)
    return {**run, "ok": bool(run.get("ok", run.get("rc") == 0)), "root": root, "executor": ex,
            "result": res, "text": code}


def run(srv, code: str | None = None, lang: str = "sh", node: str | None = None,
        on: str | None = None, timeout_s=None) -> dict:
    """The model's one door (exec(action='run')): mint the code (or take a root),
    owe its run to the executor, and run it here -- the same edges anyone else's
    request makes."""
    est = srv.entity_store
    if node:
        doc = est._resolve_spec(node)
        if doc is None or not str(doc.eid).startswith(MEMBRANE_NS + "node:"):
            return {"ok": False, "error": f"no code node matches {node!r}"}
        root = doc.eid
        lang = str((doc.properties or {}).get("lang") or lang)
    else:
        if not str(code or "").strip():
            return {"ok": False, "error": "nothing to run: give code, or node=<a root> to re-run"}
        try:
            root = mint(srv, lang, str(code))
        except NoGrammar as e:
            return {"ok": False, "error": str(e)}
        except ValueError as e:              # NotAScript is a ValueError
            return {"ok": False, "error": f"not {lang} code: {e}"}
    if on:
        ex_doc = est._resolve_spec(on)
        if ex_doc is None:
            return {"ok": False, "root": root, "error": f"no executor matches {on!r}"}
        ex = ex_doc.eid
    else:
        ex = LANGS[lang].executor
    with _LK:
        _INLINE.add((root, ex))
    try:
        request(srv, root, ex)
        out = process(srv, root, ex, timeout_s)
    finally:
        with _LK:
            _INLINE.discard((root, ex))
    return out if out is not None else {"ok": False, "root": root, "executor": ex,
                                        "error": "already running: read the root for its result"}


def reap(srv, cap: int = 3, ex: str | None = None) -> list[dict]:
    """The owed runs anyone wrote (a loop, a re-created edge), oldest first, up to
    cap; `ex` keeps one executor's."""
    be, origin = _rel(srv)
    est = srv.entity_store
    if be is not None:
        owed = sorted((float(d.get("last_ts") or 0), d["subj"], d["obj"]) for _, d in
                      be.scan("relations", {"origin": origin, "rel": EXECUTE, "superseded": False}))
    else:
        owed = sorted((0.0, k, str((d.properties or {}).get("pending_run_by")))
                      for k, d in list((est.docs or {}).items())
                      if (d.properties or {}).get("pending_run_by"))
    if ex:
        owed = [o for o in owed if o[2] == ex]
    out = []
    for _, root, on in owed[:max(1, cap)]:
        if (est.docs or {}).get(root) is None:
            continue
        r = process(srv, root, on)
        if r is not None:
            out.append(r)
    return out


def state(srv, root: str) -> dict:
    """A root as the model reads it: its code, language, state and runs."""
    est = srv.entity_store
    doc = est.docs[root]
    p = doc.properties or {}
    be, origin = _rel(srv)
    st = "idle"
    if be is not None:
        from . import relations as R
        rows = [r for r in R.of(be, origin, root) if r.get("subj") == root]
        if any(r["rel"] == EXECUTING for r in rows):
            st = "executing"
        elif any(r["rel"] == EXECUTE for r in rows):
            st = "owed"
        res = [r["obj"] for r in sorted((r for r in rows if r["rel"] == RESULT),
                                        key=lambda r: float(r.get("first_ts") or 0))]
    else:
        st = "executing" if p.get("executing_on") else "owed" if p.get("pending_run_by") else "idle"
        res = list(p.get("results") or [])
    results = []
    for r in res:
        rp = _props(est, r)
        results.append({"eid": r, **{k: rp.get(k) for k in ("rc", "outcome", "head", "ms", "ts")}})
    return {"eid": root, "lang": p.get("lang"), "code": text(est, root), "state": st, "results": results}
