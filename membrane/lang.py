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
newest code_runs_keep per root (a seat option, default 30); collect() deletes
what is older -- results, bound trees, one-off trees -- and the nodes no kept
tree reaches. Taking an owed edge is a compare-and-set (the
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



def _keep(srv) -> int:
    """How many runs are kept per thing collected (options.code_runs_keep)."""
    from . import options as _OP
    return _OP.code_runs_keep(srv)
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
# the kinds a language's parser gives its ROOT (tree-sitter's bash and javascript:
# `program`). collect() marks from these: a language whose root is another kind
# adds it here, or its trees are swept as unreachable.
ROOT_KINDS = {"program"}


# ---- parsing: tree-sitter grammars (#622) ---------------------------------------------------

HOLE = "\x00"          # where a child goes in its parent's template
GRAMMARS = {"sh": "tree_sitter_bash", "js": "tree_sitter_javascript",
            "py": "tree_sitter_python"}          # py: source trees only (#715), no executor
_TS: dict = {}
INSTALL = ("pip install tree-sitter tree-sitter-bash tree-sitter-javascript tree-sitter-python "
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
            "kind": n.kind, "text": n.text, "args": kids, "silent_birth": True,
            "born_ts": time.time()}), embedder=None)
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
    tree = parse(lang, text, params=True) if params else lg.parse(text)
    with _GC:                                 # a tree's nodes land before its root: never mid-collect
        return _mint(srv, lang, tree, be, origin)


# ---- the one-shot shape gate (#725) ------------------------------------------------------------
# A program's SHAPE is its tree with the literal leaves as holes; the shape's own node counts
# the one-shot runs (code_run's door: run(oneshot=True)). The K+1-th one-shot of a shape is
# refused with the fix: define a method from it, its varying literals as {{params}}, and call
# it. A method call never counts (it runs through run() without oneshot).
SHAPE_K = 3                    # the operator's K (2026-10-29)
SHAPE_RUNS = "oneshot_runs"    # on the shape's root node: the one-shot runs of that shape
SHAPE_ROOTS = "oneshot_roots"  # ... and the roots that ran (the refusal names them)
_HOLE_KIND = "literal"
# sh: the DATA-LIKE leaves (the scope card): these kinds whole, and a bare word that looks
# like data (a path, a number, key=value ...); command names, flags and bare words stay
_SH_LITERAL_KINDS = {"number", "string", "raw_string", "ansi_c_string", "translated_string",
                     "concatenation", "expansion", "simple_expansion", "command_substitution",
                     "heredoc_body", "arithmetic_expansion"}
_SH_DATA_WORD = re.compile(r"[/.~\d:=@,]")


def _sh_literal(n: Node) -> bool:
    return n.kind in _SH_LITERAL_KINDS or (n.kind == "word" and bool(_SH_DATA_WORD.search(n.text)))


SHAPE_LITERAL = {"sh": _sh_literal}     # a language with no entry is not gated


def shape_of(n: Node, lang: str = "sh") -> Node:
    """The tree with its literal leaves (whole subtrees) as holes; a command's name stays."""
    lit = SHAPE_LITERAL[lang]

    def walk(n, in_name=False):
        if not in_name and lit(n):
            return Node(_HOLE_KIND, "?")
        return Node(n.kind, n.text, [walk(k, in_name or n.kind == "command_name") for k in n.args])
    return walk(n)


def _eid_of(lang: str, n: Node) -> str:
    return f"{MEMBRANE_NS}node:{lang}:{_hash(lang, n, [_eid_of(lang, k) for k in n.args])}"


def shape_eid_of(n: Node, lang: str = "sh") -> str:
    """The shape's node id (pure: nothing minted)."""
    return _eid_of(lang, shape_of(n, lang))


def shape_refusal(srv, lang: str, tree: Node) -> str | None:
    """The one-shot door's refusal: this shape already ran SHAPE_K times. None: it may run."""
    if lang not in SHAPE_LITERAL:
        return None
    doc = (srv.entity_store.docs or {}).get(shape_eid_of(tree, lang))
    p = (doc.properties or {}) if doc is not None else {}
    if int(p.get(SHAPE_RUNS) or 0) < SHAPE_K:
        return None
    roots = ", ".join(str(r) for r in (p.get(SHAPE_ROOTS) or [])[-SHAPE_K:])
    return (f"refused: this program's shape already ran {int(p[SHAPE_RUNS])} times as a one-shot "
            f"(roots: {roots}) -- program the field first: define a method from it on the entity it "
            "belongs to (method_define, its command this code with the varying literals as "
            "{{params}}), then method_call it")


def note_oneshot(srv, lang: str, tree: Node, root: str) -> None:
    """Count a one-shot run on its shape's node (minted on its first run)."""
    if lang not in SHAPE_LITERAL:
        return
    be, origin = _rel(srv)
    with _GC:
        eid = _mint(srv, lang, shape_of(tree, lang), be, origin)
        doc = srv.entity_store.docs[eid]
        p = doc.properties or {}
        p[SHAPE_RUNS] = int(p.get(SHAPE_RUNS) or 0) + 1
        p[SHAPE_ROOTS] = [r for r in (p.get(SHAPE_ROOTS) or []) if r != root] + [root]
        doc.properties = p
        srv.entity_store.upsert(doc, embedder=None)


# ---- one shape logic for every program (#741) ---------------------------------------------------
# A METHOD FIXES ITS PROGRAM; only its data varies. A {{param}} may sit only where the shape gate
# reads a leaf as data: never a command's name, never an interpreter's script. Every method's
# template is noted on its SKELETON's node (the tree with every argument leaf a hole -- coarser
# than #725's shape, so `--json {{f}}` and `--json title` meet); a one-shot whose tree matches a
# method's template (the fixed parts equal, each param binding one plain literal) runs AS that
# method, and a define that an existing method's template already covers is refused.
SHAPE_METHODS = "methods"      # on the skeleton's node: the methods whose template has it
PASSTHROUGH = ("refused: a method fixes its program; only its data can vary (paths, values, "
               "strings) -- put the program in the command and its data in {{params}}")
_SCRIPT_FLAGS = {"bash": "-c", "sh": "-c", "zsh": "-c", "dash": "-c", "ksh": "-c", "fish": "-c",
                 "python": "-c", "python3": "-c", "node": "-e", "perl": "-e", "ruby": "-e",
                 "php": "-r", "osascript": "-e", "deno": "eval", "bun": "-e"}
_ALL_SCRIPT = {"eval", "exec", "source", "."}      # what they take IS code: no param at all
# WRAPPERS run the NEXT word as a command: a prefix, skipped with its own flags (and the flags'
# values) and VAR=x assignments; the wrapped command then faces the same two tests (#743).
_WRAPPERS = {"sudo": {"-u", "-g", "-C", "-p", "-U", "-r", "-t", "-D"}, "nohup": set(),
             "time": {"-f", "-o"}, "watch": {"-n"},
             "env": {"-u", "-C"}, "xargs": {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-a"}}
_WRAPPER_SCRIPT_FLAGS = {"env": "-S"}       # a flag whose value is itself a command line
_EXPANSIONS = {"expansion", "simple_expansion", "command_substitution", "arithmetic_expansion",
               "process_substitution"}


def _has_param(n: Node) -> bool:
    return n.kind == "param" or any(_has_param(k) for k in n.args)


def _name_of(cmd: Node) -> str:
    for k in cmd.args:
        if k.kind == "command_name":
            return print_(k).strip().rsplit("/", 1)[-1]
    return ""


def passthrough_refusal(tree: Node) -> str | None:
    """#741: a method's tree (params=True) whose param is the program -- in a command's name,
    or as (part of) an interpreter's script. None: its params are data."""
    def walk(n: Node) -> bool:
        if n.kind == "command_name" and _has_param(n):
            return True
        if n.kind == "command":
            name, rest = _name_of(n), [k for k in n.args if k.kind != "command_name"]
            if command_passes(name, rest):
                return True
        return any(walk(k) for k in n.args)

    def command_passes(name: str, rest: list) -> bool:
        while name in _WRAPPERS:                       # unwrap: the wrapped command is judged
            takes, script, i = _WRAPPERS[name], _WRAPPER_SCRIPT_FLAGS.get(name), 0
            while i < len(rest):
                t = print_(rest[i]).strip()
                if script and t == script:
                    return any(_has_param(k) for k in rest[i + 1:])
                if t in takes:
                    i += 2
                elif t.startswith("-") or ("=" in t and not t.startswith("=")):
                    i += 1
                else:
                    break
            if i >= len(rest):
                return False                           # a bare wrapper: nothing wrapped
            if _has_param(rest[i]):
                return True                            # the param is the wrapped command's name
            name, rest = print_(rest[i]).strip().rsplit("/", 1)[-1], rest[i + 1:]
        if name in _ALL_SCRIPT and any(_has_param(k) for k in rest):
            return True
        flag = _SCRIPT_FLAGS.get(name)
        if flag:
            for i, k in enumerate(rest[:-1]):
                if print_(k).strip() == flag and _has_param(rest[i + 1]):
                    return True
        return False
    return PASSTHROUGH if walk(tree) else None


def skeleton_of(n: Node, lang: str = "sh") -> Node:
    """The index key: every argument leaf (a param, a #725 literal, a bare word) a hole."""
    lit = SHAPE_LITERAL[lang]

    def walk(n, in_name=False):
        if n.kind == "param" or (not in_name and (lit(n) or n.kind == "word")):
            return Node(_HOLE_KIND, "?")
        return Node(n.kind, n.text, [walk(k, in_name or n.kind == "command_name") for k in n.args])
    return walk(n)


def _plain(n: Node) -> str | None:
    """A run's subtree as ONE plain value (a param binds it, quoted once on the way back),
    or None when quoting it would change it (an expansion, a glob, several words)."""
    import shlex

    def bad(x: Node) -> bool:
        return x.kind in _EXPANSIONS or x.kind == "param" or any(bad(k) for k in x.args)
    if bad(n):
        return None
    t = print_(n)
    if any(c in t for c in "*?[") and n.kind == "word":
        return None
    try:
        parts = shlex.split(t)
    except ValueError:
        return None
    return parts[0] if len(parts) == 1 else None


def match_template(tpl: Node, n: Node, binds: list | None = None) -> list | None:
    """The method's template over a tree: the fixed parts equal, each param binding one
    plain value (in order: [(name, value)]). A param over a param (another template) binds
    too. None: no match."""
    binds = [] if binds is None else binds
    if tpl.kind == "param":
        name = PARAM.search(tpl.text).group(1)
        v = "{{}}" if n.kind == "param" else _plain(n)
        if v is None:
            return None
        binds.append((name, v))
        return binds
    if tpl.kind != n.kind or tpl.text != n.text or len(tpl.args) != len(n.args):
        return None
    for a, b in zip(tpl.args, n.args):
        if match_template(a, b, binds) is None:
            return None
    return binds


def _method_tree(doc) -> Node | None:
    p = doc.properties or {}
    cmd = str(p.get("command") or "").strip()
    if not cmd or p.get("retired") or p.get("abstract") or not p.get("method_name"):
        return None
    try:
        tree = parse("sh", cmd, params=True)
    except (ValueError, NoGrammar):
        return None
    names = [PARAM.search(x.text).group(1) for x in _params_of(tree)]
    if any(_feed(x) or "." in x or ":" in x for x in names):
        return None                           # self.* / parent feeds bind from the call, never a run
    return tree


def _params_of(n: Node) -> list:
    return [n] if n.kind == "param" else [x for k in n.args for x in _params_of(k)]


def note_method(srv, tree: Node, method_eid: str) -> None:
    """Note a method on its skeleton's node (minted on its first method)."""
    be, origin = _rel(srv)
    with _GC:
        eid = _mint(srv, "sh", skeleton_of(tree), be, origin)
        doc = srv.entity_store.docs[eid]
        p = doc.properties or {}
        if method_eid not in (p.get(SHAPE_METHODS) or []):
            p[SHAPE_METHODS] = list(p.get(SHAPE_METHODS) or []) + [method_eid]
            doc.properties = p
            srv.entity_store.upsert(doc, embedder=None)


def methods_matching(est, tree: Node, skip: str = "") -> list:
    """[(method doc, binds)] whose template matches `tree`, the most-called first."""
    try:
        sk = (est.docs or {}).get(_eid_of("sh", skeleton_of(tree)))
    except KeyError:
        return []
    out = []
    for me in ((sk.properties or {}).get(SHAPE_METHODS) or []) if sk is not None else []:
        d = (est.docs or {}).get(me)
        if d is None or me == skip:
            continue
        mt = _method_tree(d)
        binds = match_template(mt, tree) if mt is not None else None
        if binds is not None:
            out.append((d, binds))
    out.sort(key=lambda x: -int((x[0].properties or {}).get("runs") or 0))
    return out


def _sig(d) -> str:
    p = d.properties or {}
    names = list(dict.fromkeys(PARAM.search(x.text).group(1) for x in _params_of(_method_tree(d))))
    return f"{p.get('name') or d.eid}({', '.join(names)})"


def duplicate_refusal(est, tree: Node, eid: str) -> str | None:
    """#741: a new template an existing method's template already covers."""
    hits = methods_matching(est, tree, skip=eid)
    if not hits:
        return None
    return (f"refused: you have {_sig(hits[0][0])} for this: call it, or compose it via steps"
            + (f" (also: {', '.join(_sig(d) for d, _ in hits[1:])})" if hits[1:] else ""))


def route_oneshot(srv, tree: Node) -> dict | None:
    """#741: a one-shot that matches a method runs as that method (recorded as its call)."""
    from . import night as N
    for d, binds in methods_matching(srv.entity_store, tree):
        owner = ((d.properties or {}).get("method_of") or [None])[0]
        if not owner or (srv.entity_store.docs or {}).get(owner) is None:
            continue
        args: dict = {}
        if any(args.setdefault(k, v) != v for k, v in binds):
            continue                          # one name, two values: not this template
        others = [o for o, _ in methods_matching(srv.entity_store, tree) if o.eid != d.eid]
        out = N.method_call(srv, srv.entity_store, getattr(srv, "efield", None), getattr(srv, "membrane", None),
                            owner, d.properties["method_name"], args)
        note = (f"you have {_sig(d)} for this: call it next time; this ran it for you"
                + (f" (also matching: {', '.join(_sig(o) for o in others)})" if others else ""))
        out.update(routed={"method": d.eid, "object": owner, "args": args}, note=note,
                   text=note, executor=out.get("via"))
        return out
    return None


def boot_methods(srv) -> dict:
    """Boot (#741): retire the passthrough methods (marked, never deleted) and note every
    other method on its skeleton's node. Idempotent."""
    est = srv.entity_store
    retired, noted = [], 0
    for d in list((est.docs or {}).values()):
        p = d.properties or {}
        cmd = str(p.get("command") or "").strip()
        if not p.get("method_name") or not cmd or p.get("retired") or p.get("abstract"):
            continue
        try:
            tree = parse("sh", cmd, params=True)
        except (ValueError, NoGrammar):
            continue
        if passthrough_refusal(tree):
            p["retired"] = "#741: a passthrough method (its param is the program)"
            d.properties = p
            est.upsert(d, embedder=None)
            retired.append(d.eid)
        elif _method_tree(d) is not None:
            note_method(srv, tree, d.eid)
            noted += 1
    return {"retired": retired, "noted": noted}


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

_GC = threading.RLock()        # mint and collect never interleave
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
            for old in live[:max(0, len(live) - _keep(srv))]:
                _supersede_edge(be, origin, root, old["obj"], rel=RESULT, receipt={"op": "trimmed"})
                est.remove(old["obj"])
        return
    doc.properties.pop("executing_on", None)
    if result:
        rs = list(doc.properties.get("results") or []) + [result]
        keep = _keep(srv)
        for old in rs[:max(0, len(rs) - keep)]:
            est.remove(old)
        doc.properties["results"] = rs[-keep:]
    est.upsert(doc, embedder=None)


def process(srv, root: str, ex: str, timeout_s=None) -> dict | None:
    """The executor's reaction to an owed edge: take it, print the tree, run it,
    land a result entity. None when the edge was not owed (someone else took it)."""
    est = srv.entity_store
    if not _props(est, root).get("lang"):
        return None                         # not code (a goal-graph task's edge to the worker pool): not ours
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
        doc.properties["last_run_ts"], doc.properties["executor"] = time.time(), ex   # collect()'s clock
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
        on: str | None = None, timeout_s=None, oneshot: bool = False) -> dict:
    """The model's one door (exec(action='run')): mint the code (or take a root),
    owe its run to the executor, and run it here -- the same edges anyone else's
    request makes. `oneshot` (code_run's door, not a method's run): the shape
    gate (#725) refuses a shape that already ran SHAPE_K times, and counts it."""
    tree = None
    if oneshot:
        try:
            if node:
                d = srv.entity_store._resolve_spec(node)
                if d is not None and str(d.eid).startswith(MEMBRANE_NS + "node:"):
                    lang = str((d.properties or {}).get("lang") or lang)
                    tree = load(srv.entity_store, d.eid)
            elif str(code or "").strip() and lang in LANGS:
                tree = LANGS[lang].parse(str(code))
        except (ValueError, KeyError, NoGrammar):
            tree = None                       # _owe says what is wrong with it
        if tree is not None and lang == "sh":
            routed = route_oneshot(srv, tree)     # #741: a method has this program: run it as that
            if routed is not None:
                return routed
        why = shape_refusal(srv, lang, tree) if tree is not None else None
        if why:
            return {"ok": False, "error": why}
    with _GC:                                 # minted and owed before a collect can see it
        owed = _owe(srv, code, lang, node, on)
    if "error" in owed:
        return owed
    root, ex = owed["root"], owed["executor"]
    if tree is not None:
        note_oneshot(srv, lang, tree, root)
    try:
        out = process(srv, root, ex, timeout_s)
    finally:
        with _LK:
            _INLINE.discard((root, ex))
    return out if out is not None else {"ok": False, "root": root, "executor": ex,
                                        "error": "already running: read the root for its result"}


def _owe(srv, code, lang: str, node, on) -> dict:
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
    request(srv, root, ex)
    return {"root": root, "executor": ex}


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


def note_bound(est, doc, root: str) -> None:
    """A method (or a step) ran its code bound as `root`: its newest bound trees,
    newest last -- the ones collect() keeps for it."""
    p = doc.properties or {}
    bound = [r for r in (p.get("bound") or []) if r != root] + [root]
    p["bound"] = bound
    doc.properties = p
    est.upsert(doc, embedder=None)


def collect(srv) -> dict:
    """THE CODE TREES' COLLECTION (the operator, 2026-10-27: delete, keep a
    configurable number per thing): each method keeps its newest code_runs_keep
    bound trees, each executor its newest one-off trees; the rest are deleted with
    their results and edges, and then every node no kept tree reaches. A method's
    own code and a run still owed or running are never collected. -> counts."""
    with _GC:
        return _collect(srv)


def _collect(srv) -> dict:
    est = srv.entity_store
    keep = _keep(srv)
    be, origin = _rel(srv)
    docs = dict(est.docs or {})
    node_ns, res_ns = MEMBRANE_NS + "node:", MEMBRANE_NS + "result:"
    codes, kept, doomed = set(), set(), set()
    for d in docs.values():
        p = d.properties or {}
        if str(p.get("code") or "").startswith(node_ns):
            codes.add(p["code"])
        if p.get(SHAPE_RUNS) or p.get(SHAPE_METHODS):
            codes.add(d.eid)                  # #725 / #741: a shape's count or methods are never collected
        bound = [r for r in (p.get("bound") or []) if r in docs]
        if bound:
            kept.update(bound[-keep:])
            doomed.update(bound[:-keep])
            if bound[-keep:] != list(p.get("bound") or []):
                p["bound"] = bound[-keep:]
                est.upsert(d, embedder=None)
    busy = set()
    if be is not None:
        for rel in (EXECUTE, EXECUTING):
            busy.update(r["subj"] for _, r in be.scan("relations", {"origin": origin, "rel": rel, "superseded": False}))
    else:
        busy.update(k for k, d in docs.items()
                    if (d.properties or {}).get("pending_run_by") or (d.properties or {}).get("executing_on"))
    # a root from before collection kept no clocks: its newest result stands in
    last: dict = {}
    for k, d in docs.items():
        if k.startswith(res_ns):
            p = d.properties or {}
            r, ts = str(p.get("root") or ""), float(p.get("ts") or 0)
            if ts >= last.get(r, (0.0, ""))[0]:
                last[r] = (ts, str(p.get("executor") or ""))
    # one-off trees (a code_run's): the newest per executor are kept
    by_ex: dict = {}
    for k, d in docs.items():
        p = d.properties or {}
        if k.startswith(node_ns) and p.get("kind") in ROOT_KINDS and k not in codes and k not in kept and k not in doomed:
            res_ts, res_ex = last.get(k, (0.0, ""))
            seen = max(float(p.get("last_run_ts") or 0), float(p.get("born_ts") or 0), res_ts)
            by_ex.setdefault(str(p.get("executor") or res_ex), []).append((seen, k))
    for rows in by_ex.values():
        rows.sort()
        doomed.update(k for _, k in rows[:max(0, len(rows) - keep)])
    doomed -= codes | kept | busy
    # mark: what a kept tree (or a method's code, or a busy run) reaches stays
    live, stack = set(), [k for k, d in docs.items() if k.startswith(node_ns)
                          and (d.properties or {}).get("kind") in ROOT_KINDS and k not in doomed]
    while stack:
        k = stack.pop()
        if k in live or k not in docs:
            continue
        live.add(k)
        stack.extend((docs[k].properties or {}).get("args") or [])
    from . import relations as R
    gone_nodes = [k for k in docs if k.startswith(node_ns) and k not in live]
    gone_results = [k for k, d in docs.items() if k.startswith(res_ns)
                    and str((d.properties or {}).get("root") or "") in doomed]
    if be is not None:
        for k in gone_nodes:
            p = docs[k].properties or {}
            for i, kid in enumerate(p.get("args") or [], 1):
                be.delete("relations", R.relation_key(origin, k, f"arg:{i}", kid))
            if k in doomed:
                ex = str(p.get("executor") or "")
                for rel in (EXECUTE, EXECUTING):
                    if ex:
                        be.delete("relations", R.relation_key(origin, k, rel, ex))
        for k in gone_results:
            be.delete("relations", R.relation_key(origin, str(docs[k].properties.get("root")), RESULT, k))
    gone = gone_nodes + gone_results
    if gone:
        est.remove_many(gone) if hasattr(est, "remove_many") else [est.remove(k) for k in gone]
    return {"trees": len(doomed), "nodes": len(gone_nodes), "results": len(gone_results), "keep": keep}


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
