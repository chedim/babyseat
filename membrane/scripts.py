"""SCRIPTS ARE GRAPHS (#576 G): a stored command is a graph, compiled to bash
only to run.

THE GRAPH. A script is a list of ITEMS, each a pipeline and the separator after
it (`&&`, `||`, `;`, `&`, a newline, or none). A pipeline is STAGES joined by `|`
(optionally negated with `!`). A stage is a simple command -- env prefixes
(`A=1`), WORDS, REDIRECTS -- or a GROUP `( ... )` holding a script, with its own
redirects. A redirect keeps its raw text (`2>&1`, `> log`, `<<'EOF'`); a heredoc
keeps its body. A word is an argument node with its KIND:

  lit     a plain word (flags, names, paths)
  param   holds {{x}} -- filled at call time, shell-quoted
  glob    an unquoted * ? [ pattern, expanded by the shell
  var     a $VAR / ${VAR} reference (also inside double quotes)
  subst   a $( ... ) or ` ... ` command substitution
  file    a lit that names a file entity on the field (bind())

Every node keeps its RAW shell text, so compile(parse(x)) means exactly what x
meant: quoting and expansion are never rewritten. The program of a stage is its
first word; bind() links programs and files to the entities already on the field
(`shell:<prog>`, `f:<path>`) -- no entity is made.

What is not a script (an unclosed quote, substitution or group, a heredoc with no
end) is refused: NotAScript."""
from __future__ import annotations

import re

OPS = ("&&", "||", ";;", ";", "&", "|", "(", ")", "\n")


class NotAScript(ValueError):
    pass


# ---- the scanner -----------------------------------------------------------------------------

def _skip_quoted(s: str, i: int) -> int:
    """i at a quote, $( , ` or ${ : the index just past its end."""
    c = s[i]
    if c == "'":
        j = s.find("'", i + 1)
        if j < 0:
            raise NotAScript("an unclosed ' quote")
        return j + 1
    if c == '"':
        j = i + 1
        while j < len(s):
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == '"':
                return j + 1
            if s.startswith("$(", j) or s[j] == "`" or s.startswith("${", j):
                j = _skip_quoted(s, j)
                continue
            j += 1
        raise NotAScript('an unclosed " quote')
    if c == "`":
        j = i + 1
        while j < len(s):
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == "`":
                return j + 1
            j += 1
        raise NotAScript("an unclosed ` substitution")
    if s.startswith("${", i):
        j = s.find("}", i + 2)
        if j < 0:
            raise NotAScript("an unclosed ${")
        return j + 1
    if s.startswith("$((", i):
        depth, j = 0, i + 1
        while j < len(s):
            if s[j] == "(":
                depth += 1
            elif s[j] == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise NotAScript("an unclosed $((")
    if s.startswith("$(", i):
        depth, j = 1, i + 2
        while j < len(s):
            ch = s[j]
            if ch in "'\"`" or s.startswith("$(", j) or s.startswith("${", j):
                j = _skip_quoted(s, j)
                continue
            if ch == "\\":
                j += 2
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise NotAScript("an unclosed $( substitution")
    return i + 1


_REDIR = re.compile(r"(\d*)(&>>|&>|>>|>&|<&|<<-|<<<|<<|<>|>\||>|<)")


def _tokens(s: str) -> list[tuple[str, str]]:
    """[(kind, raw)]: kind is 'w' (word), 'op', 'redir' or 'heredoc' (raw = its body)."""
    out: list[tuple[str, str]] = []
    pending_heredocs: list[tuple[str, bool]] = []         # (delimiter, strip tabs)
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t":
            i += 1
            continue
        if c == "\\" and s.startswith("\\\n", i):
            i += 2
            continue
        if c == "#" and (not out or out[-1][0] == "op"):
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "\n":
            out.append(("op", "\n"))
            i += 1
            for delim, strip in pending_heredocs:          # their bodies follow the line
                body_lines = []
                while True:
                    j = s.find("\n", i)
                    line = s[i:] if j < 0 else s[i:j]
                    if i >= n:
                        raise NotAScript(f"a heredoc with no end ({delim})")
                    i = n if j < 0 else j + 1
                    body_lines.append(line)
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
                out.append(("heredoc", "\n".join(body_lines)))
            pending_heredocs = []
            continue
        m = _REDIR.match(s, i)
        if m and (m.group(1) == "" or i == 0 or s[i - 1] in " \t;|&(\n"):
            op = m.group(2)
            j = m.end()
            raw = s[i:j]
            if op in (">&", "<&") and j < n and (s[j].isdigit() or s[j] == "-"):
                k = j
                while k < n and (s[k].isdigit() or s[k] == "-"):
                    k += 1
                out.append(("redir", s[i:k]))
                i = k
                continue
            while j < n and s[j] in " \t":
                j += 1
            k = _word_end(s, j)
            if k == j:
                raise NotAScript(f"a redirect with no target: {raw}")
            target = s[j:k]
            if op in ("<<", "<<-"):
                delim = target.strip("'\"").replace("\\", "")
                pending_heredocs.append((delim, op == "<<-"))
            out.append(("redir", raw + target if op == ">&" or op == "<&" else f"{raw}{target}" if op in ("<<", "<<-") else f"{raw} {target}"))
            i = k
            continue
        for op in OPS:
            if s.startswith(op, i):
                if op == ";;":
                    raise NotAScript("case ... esac is not a script here")
                out.append(("op", op))
                i += len(op)
                break
        else:
            k = _word_end(s, i)
            out.append(("w", s[i:k]))
            i = k
    if pending_heredocs:
        raise NotAScript("a heredoc with no end")
    return out


def _word_end(s: str, i: int) -> int:
    n = len(s)
    j = i
    while j < n:
        c = s[j]
        if c in " \t\n" or c in ";&|()<>":
            break
        if c == "\\":
            j += 2
            continue
        if c in "'\"`" or s.startswith("$(", j) or s.startswith("${", j):
            j = _skip_quoted(s, j)
            continue
        j += 1
    return j


# ---- the parser ------------------------------------------------------------------------------

def _kind(raw: str) -> str:
    if "{{" in raw and "}}" in raw:
        return "param"
    unquoted = re.sub(r"'[^']*'", "", raw)
    if "$(" in unquoted or "`" in unquoted:
        return "subst"
    if re.search(r"\$[A-Za-z_{?#@!$0-9]", unquoted):
        return "var"
    bare = re.sub(r'"[^"]*"', "", unquoted)
    if re.search(r"[*?\[]", bare):
        return "glob"
    return "lit"


class _P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def script(self, until=None) -> dict:
        items = []
        while True:
            tok = self.peek()
            while tok is not None and tok == ("op", "\n") and not items:
                self.take()
                tok = self.peek()
            if tok is None or (until and tok == ("op", until)):
                break
            pipe = self.pipeline()
            sep = None
            tok = self.peek()
            if tok is not None and tok[0] == "op" and tok[1] in ("&&", "||", ";", "&", "\n"):
                sep = self.take()[1]
                while sep in (";", "\n") and self.peek() == ("op", "\n"):
                    self.take()
            items.append({"sep": sep, "pipe": pipe})
            if sep is None:
                break
        if items and items[-1]["sep"] in ("\n",):
            items[-1]["sep"] = None
        return {"v": 1, "items": items}

    def pipeline(self) -> dict:
        neg = False
        if self.peek() == ("w", "!"):
            self.take()
            neg = True
        stages = [self.command()]
        while self.peek() == ("op", "|"):
            self.take()
            while self.peek() == ("op", "\n"):
                self.take()
            stages.append(self.command())
        return {"neg": neg, "stages": stages}

    def command(self) -> dict:
        st = {"env": [], "words": [], "redirs": [], "group": None}
        tok = self.peek()
        if tok == ("op", "("):
            self.take()
            st["group"] = self.script(until=")")
            if self.peek() != ("op", ")"):
                raise NotAScript("an unclosed ( group")
            self.take()
        while True:
            tok = self.peek()
            if tok is None or tok[0] == "op":
                break
            self.take()
            if tok[0] == "redir":
                st["redirs"].append({"raw": tok[1]})
            elif tok[0] == "heredoc":
                for r in reversed(st["redirs"]):
                    if r["raw"].startswith(("<<", "0<<")) and "body" not in r:
                        r["body"] = tok[1]
                        break
            elif st["group"] is None and not st["words"] and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok[1]):
                st["env"].append({"raw": tok[1]})
            else:
                st["words"].append({"raw": tok[1], "kind": _kind(tok[1])})
        if st["group"] is None and not st["words"] and not st["env"]:
            raise NotAScript("an empty command")
        return st


def parse(cmd: str) -> dict:
    """A command line -> its graph (NotAScript when it is not one)."""
    toks = _tokens(str(cmd or ""))
    # heredoc bodies arrive after the newline that ends their line: move each next to its stage
    fixed, held = [], []
    for tok in toks:
        if tok[0] == "heredoc":
            held.append(tok)
            continue
        fixed.append(tok)
    if held:
        out, hi = [], 0
        for tok in fixed:
            out.append(tok)
            if tok[0] == "redir" and tok[1].lstrip("0123456789").startswith("<<") and not tok[1].lstrip("0123456789").startswith("<<<"):
                if hi < len(held):
                    out.append(held[hi])
                    hi += 1
        fixed = out
    p = _P(fixed)
    g = p.script()
    if p.peek() is not None:
        raise NotAScript(f"unexpected {p.peek()[1]!r}")
    if not g["items"]:
        raise NotAScript("an empty script")
    return g


# ---- the compiler ----------------------------------------------------------------------------

def _stage(st: dict) -> tuple[str, list[str]]:
    parts, bodies = [], []
    if st.get("group") is not None:
        parts.append("( " + compile(st["group"]) + " )")
    parts += [e["raw"] for e in st.get("env") or []]
    parts += [w["raw"] for w in st.get("words") or []]
    for r in st.get("redirs") or []:
        parts.append(r["raw"])
        if "body" in r:
            bodies.append(r["body"])
    return " ".join(parts), bodies


def compile(g: dict) -> str:
    """A graph -> the bash it means (heredoc bodies after their line)."""
    out = []
    for it in g.get("items") or []:
        bodies = []
        texts = []
        for st in it["pipe"]["stages"]:
            t, b = _stage(st)
            texts.append(t)
            bodies += b
        line = ("! " if it["pipe"].get("neg") else "") + " | ".join(texts)
        sep = it.get("sep")
        if bodies:
            line += (" " + sep if sep and sep not in ("\n",) else "") + "\n" + "\n".join(bodies)
            out.append(line + ("\n" if sep else ""))
        else:
            out.append(line + ({None: "", "\n": "\n"}.get(sep, f" {sep} ")))
    return "".join(out).rstrip(" ")


# ---- reading the graph -----------------------------------------------------------------------

def program(st: dict) -> str:
    w = (st.get("words") or [{}])[0].get("raw", "") if st.get("words") else ""
    return w.rsplit("/", 1)[-1] if w else ""


def stages(g: dict):
    for it in g.get("items") or []:
        for st in it["pipe"]["stages"]:
            yield st
            if st.get("group") is not None:
                yield from stages(st["group"])


def bind(g: dict, est) -> dict:
    """Link programs and file words to the entities already on the field (no new
    entity): a stage gets `prog_eid`, a lit word naming a known file gets kind
    `file` and its `eid`."""
    docs = getattr(est, "docs", None) or {}
    from .entities import MEMBRANE_NS
    for st in stages(g):
        prog = program(st)
        if prog and MEMBRANE_NS + f"shell:{prog}" in docs:
            st["prog_eid"] = MEMBRANE_NS + f"shell:{prog}"
        for w in (st.get("words") or [])[1:]:
            if w.get("kind") != "lit":
                continue
            raw = w["raw"].strip("'\"")
            for cand in (MEMBRANE_NS + f"files:{raw}", MEMBRANE_NS + f"f:{raw}"):
                if cand in docs:
                    w["kind"], w["eid"] = "file", cand
                    break
    return g


# ---- on the field ----------------------------------------------------------------------------

def _whole(g) -> bool:
    """A kept graph survived the disk: the store's json-safe screen turns values
    past its depth into strings, and a stage's words sit past it."""
    try:
        return all(isinstance(x, dict) for st in stages(g)
                   for x in (st.get("env") or []) + (st.get("words") or []) + (st.get("redirs") or []))
    except Exception:
        return False


def graph_of(doc, est=None) -> dict:
    """The graph a command entity runs: kept on it as `script` (with the `src` it
    was parsed from); parsed again when the command was edited or the kept graph
    came back from the disk screened. NotAScript when the command is not one --
    then it never runs."""
    props = doc.properties if doc.properties is not None else {}
    src = str(props.get("command") or "").strip()
    sc = props.get("script")
    if isinstance(sc, dict) and sc.get("src") == src and _whole(sc):
        return sc
    g = parse(src)
    if est is not None:
        bind(g, est)
    g["src"] = src
    props["script"] = g
    doc.properties = props
    return g


MIGRATED_FILE = ".ch-commands-migrated"     # beside the store: the migration ran (it runs once)
VARIANT = re.compile(r"-[0-9a-f]{4}$")      # a generated link-command name: shell:<prog>-<label>-<4 hex>


def migrate(est, backup_dir=None) -> dict:
    """#576 G, the operator's call, ONCE per store (MIGRATED_FILE beside it): the
    retired link commands go. Only the `shell:` namespace is touched: a generated
    variant (`shell:<prog>-<label>-<4 hex>`) is deleted; every other `shell:` entity
    with a stale command (a program, a hand-named command) keeps its place without
    it. Methods, their steps and every other entity are left alone. A backup is
    written beside the store FIRST; with no backup, nothing changes. To restore:
    put each `deleted` entry back as an entity with that command, and each
    `stripped` command back on its entity. -> {stripped, deleted, backup}."""
    import json
    import time
    from pathlib import Path
    from .entities import MEMBRANE_NS
    out = {"stripped": 0, "deleted": 0, "backup": None}
    home = Path(backup_dir) if backup_dir else (Path(est.path).parent if getattr(est, "path", None) else None)
    if home is None:
        return out                                     # no backup possible: nothing changes
    marker = home / MIGRATED_FILE
    if marker.exists():
        return out
    docs = getattr(est, "docs", None) or {}
    prefix = MEMBRANE_NS + "shell:"
    strip, delete = {}, {}
    for eid, d in list(docs.items()):
        p = d.properties or {}
        cmd = p.get("command")
        if not eid.startswith(prefix) or not (isinstance(cmd, str) and cmd.strip()) \
                or p.get("method_name") or p.get("node_of") or p.get("step"):
            continue
        (delete if VARIANT.search(eid[len(prefix):]) else strip)[eid] = cmd
    if strip or delete:
        f = home / f"backup-576-commands-{int(time.time())}.json"
        try:
            f.write_text(json.dumps({"stripped": strip, "deleted": delete}, indent=1))
        except OSError:
            return out                                 # no backup written: nothing changes
        out["backup"] = str(f)
        for eid in strip:
            d = docs.get(eid)
            if d is None:
                continue
            for k in ("command", "script", "last_fed", "pending_run_by"):
                d.properties.pop(k, None)
            est.upsert(d)
            out["stripped"] += 1
        out["deleted"] = est.remove_many(list(delete)) if hasattr(est, "remove_many") else \
            sum(1 for eid in delete if est.remove(eid))
    try:
        marker.write_text(json.dumps({"ts": time.time(), **{k: out[k] for k in ("stripped", "deleted")}}))
    except OSError:
        pass
    return out
