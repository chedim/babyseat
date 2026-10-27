"""JS AS A TREE (#614 part B): a statement-level parser, no new dependency.

A program is its statements; a statement that holds blocks (`if (x) {...}`,
`function f() {...}`, `class C {...}`, `() => {...}`) holds them as children,
and each block is its statements again. Every node keeps a TEMPLATE: its raw
text with a HOLE where each child goes, so printing is filling the holes and a
program prints back exactly as written (spacing, comments, semicolons or none).
Deeper nodes (expressions, calls) can come later on the same shape.

What it skips while it looks for statement ends and blocks: strings, template
literals (with their `${...}`), comments and regex literals -- the same care
`scripts._skip_quoted` takes for the shell. Unbalanced brackets, an unclosed
string and a NUL byte (the hole) are not code.
"""
from __future__ import annotations

import re

HOLE = "\x00"

_OPEN, _CLOSE = "([{", ")]}"
# a `/` after one of these (or at the start) opens a regex, else it divides
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}
_REGEX_KEYWORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void",
                   "throw", "instanceof", "yield", "await"}
# a `{` after these (or after `)` / `=>`) opens a block, else an object literal
_BLOCK_KEYWORDS = {"else", "try", "finally", "do", "static"}
# a line ending in one of these goes on; a next line starting with one continues it
_GOES_ON = set("([{,.=+-*/%&|^!~?:<>")
_CONTINUES = set(".?)]},+-*/%&|^=<>:") | {"`"}
_HEADERS = re.compile(r"^(?:if|for|while|with)\b")


class NotJS(ValueError):
    """What the parser cannot read as JS."""


def _string_end(s: str, i: int) -> int:
    q = s[i]
    i += 1
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == q:
            return i + 1
        if c == "\n" and q != "`":
            break
        if q == "`" and c == "$" and s.startswith("${", i):
            i = _balanced_end(s, i + 1)
            continue
        i += 1
    raise NotJS(f"an unclosed string at {i}")


def _regex_end(s: str, i: int) -> int:
    i += 1
    in_class = False
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            break
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            i += 1
            while i < len(s) and (s[i].isalnum() or s[i] == "_"):
                i += 1                         # its flags
            return i
        i += 1
    raise NotJS(f"an unclosed regex at {i}")


def _comment_end(s: str, i: int) -> int:
    if s.startswith("//", i):
        j = s.find("\n", i)
        return len(s) if j < 0 else j
    j = s.find("*/", i + 2)
    if j < 0:
        raise NotJS(f"an unclosed comment at {i}")
    return j + 2


def _last_token(s: str, i: int) -> str:
    """The significant text just before i: a word, or one punctuation char ('' at the start)."""
    j = i - 1
    while j >= 0 and s[j].isspace():
        j -= 1
    if j < 0:
        return ""
    if s[j].isalnum() or s[j] in "_$":
        k = j
        while k >= 0 and (s[k].isalnum() or s[k] in "_$"):
            k -= 1
        return s[k + 1:j + 1]
    return s[j]


def _skip(s: str, i: int) -> int | None:
    """The end of a string/comment/regex starting at i, or None when s[i] starts none."""
    c = s[i]
    if c in "'\"`":
        return _string_end(s, i)
    if s.startswith("//", i) or s.startswith("/*", i):
        return _comment_end(s, i)
    if c == "/":
        t = _last_token(s, i)
        if t in _REGEX_AFTER or t in _REGEX_KEYWORDS:
            return _regex_end(s, i)
    return None


def _balanced_end(s: str, i: int) -> int:
    """s[i] opens a bracket: the index just past its match."""
    want = [_CLOSE[_OPEN.index(s[i])]]
    i += 1
    while i < len(s):
        end = _skip(s, i)
        if end is not None:
            i = end
            continue
        c = s[i]
        if c in _OPEN:
            want.append(_CLOSE[_OPEN.index(c)])
        elif c in _CLOSE:
            if c != want.pop():
                raise NotJS(f"a {c!r} closes nothing open at {i}")
            if not want:
                return i + 1
        i += 1
    raise NotJS("an unclosed bracket")


def _opens_block(s: str, i: int, start: int) -> bool:
    t = _last_token(s, i)
    if t == ")" or t in _BLOCK_KEYWORDS:
        return True
    if t == ">" and s[max(0, i - 3):i].rstrip().endswith("=>"):
        return True
    return bool(re.search(r"\bclass(?:\s+[\w$]+)?(?:\s+extends\s+[^{]+)?\s*$", s[start:i]))


def _next_significant(s: str, i: int, end: int) -> int:
    while i < end:
        if s[i].isspace():
            i += 1
        elif s.startswith("//", i) or s.startswith("/*", i):
            i = _comment_end(s, i)
        else:
            return i
    return end


def _ends_at_newline(s: str, start: int, i: int, end: int) -> bool:
    """A statement running from start reaches a newline at i: does it end here?"""
    so_far = s[start:i].rstrip()
    if so_far.endswith(("++", "--")):
        return True
    if not so_far or so_far[-1] in _GOES_ON or re.search(r"\b(?:else|do)$", so_far):
        return False
    if so_far[-1] == ")" and _HEADERS.match(so_far):
        return False                          # `if (x)` with its body on the next line
    j = _next_significant(s, i, end)
    if j >= end:
        return True
    nxt = s[j]
    return not (nxt in _CONTINUES or re.match(r"(?:else|catch|finally)\b", s[j:j + 8]))


def _seq(s: str, a: int, b: int, kind: str):
    """The statements in s[a:b] -> (kind, template, children)."""
    from .lang import Node
    tpl, kids, last = [], [], a
    i, start, blocks = a, None, []

    def close(stop: int):
        nonlocal start, last, blocks
        assert start is not None
        tpl.append(s[last:start])
        tpl.append(HOLE)
        kids.append(_stmt(s, start, stop, blocks))
        last, start, blocks = stop, None, []

    while i < b:
        c = s[i]
        if c.isspace():
            if c == "\n" and start is not None and _ends_at_newline(s, start, i, b):
                close(len(s[:i].rstrip()) if s[start:i].strip() else i)
            i += 1
            continue
        if start is None and (s.startswith("//", i) or s.startswith("/*", i)):
            i = _comment_end(s, i)            # a comment between statements stays in the template
            continue
        if start is None:
            start = i
        end = _skip(s, i)
        if end is not None:
            i = end
            continue
        if c in _OPEN:
            j = _balanced_end(s, i)
            if c == "{" and _opens_block(s, i, start):
                blocks.append((i + 1, j - 1))
            i = j
            continue
        if c in _CLOSE:
            raise NotJS(f"a {c!r} closes nothing open at {i}")
        if c == ";":
            close(i + 1)
        i += 1
    if start is not None:
        stop = len(s[:b].rstrip())
        close(max(stop, start + 1))
    tpl.append(s[last:b])
    return Node(kind, "".join(tpl), kids)


def _stmt(s: str, a: int, b: int, blocks: list):
    from .lang import Node
    if not blocks:
        return Node("stmt", s[a:b])
    tpl, kids, last = [], [], a
    for x, y in blocks:
        tpl += [s[last:x], HOLE]
        kids.append(_seq(s, x, y, "block"))
        last = y
    tpl.append(s[last:b])
    return Node("stmt", "".join(tpl), kids)


def parse(text: str):
    if HOLE in text:
        raise NotJS("a NUL byte is not JS")
    if not text.strip():
        raise NotJS("no code")
    return _seq(text, 0, len(text), "program")


def print_(n) -> str:
    if not n.args:
        return n.text
    parts = n.text.split(HOLE)
    if len(parts) != len(n.args) + 1:
        raise NotJS(f"a {n.kind} template holds {len(parts) - 1} holes for {len(n.args)} children")
    out = [parts[0]]
    for kid, rest in zip(n.args, parts[1:]):
        out += [print_(kid), rest]
    return "".join(out)
