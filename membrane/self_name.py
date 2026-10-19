"""SELF'S NAME, PER SEAT (#283, the zoo: several seats in one Home Assistant,
a card per seat -- the cards differ only if each seat's self has its own name).

The operator named self Blurp (#277); that is now the DEFAULT. A seat names its
own in its settings (`self_name` in <data dir>/settings.json, "" = the
default), MEMBRANE_SELF_NAME overrides it, and every place the seat says self's
name to a person reads current(): the sheet's header and prose and the dock
(self_sheet), a locked toy's refusal (toy_unlocks), the seat card (lanes).

Not the seat's own name: settings' `name` is the assistant's (the name
ceremony, naming.py -- "Hey Bob"); this one is self's (membrane::self).

Set it with `membrane self name <NAME>` (here, cli_main) or the 🌱 chooser's
door, POST /membrane/seat/control {"self_name": ...} (seat_panel.control).
A name is trimmed and 1..MAX_LEN printable characters. Display only: the ids
(membrane::self, seat:self), the record's `by: "self"` and the code's
vocabulary stay.
"""
from __future__ import annotations

import os
import re
import sys

from . import settings as S

DEFAULT = "Blurp"             # the operator named it (2026-10-19, #277): every seat's until it is renamed
KEY = "self_name"             # its settings.json key
ENV = "MEMBRANE_SELF_NAME"    # an override over the setting (a container, a test seat)
MAX_LEN = 24


def valid(name) -> str | None:
    """The name trimmed, or None: not a string, empty, over MAX_LEN, or with
    a character that does not print (a newline, a tab, a control)."""
    if not isinstance(name, str):
        return None
    n = name.strip()
    if not n or len(n) > MAX_LEN or not n.isprintable():
        return None
    return n


def current() -> str:
    """Self's name on this seat: the env override, the setting, the default."""
    env = valid(os.environ.get(ENV) or "")
    if env:
        return env
    return valid(str(S.load().get(KEY) or "")) or DEFAULT


def set_name(name) -> str:
    """Store the seat's name for self; ValueError on an invalid one."""
    n = valid(name)
    if n is None:
        raise ValueError(f"a name is 1-{MAX_LEN} printable characters")
    S.save({KEY: n})
    return n


def reset() -> str:
    """Back to the default."""
    S.save({KEY: ""})
    return current()


_WORD = re.compile(r"\b" + re.escape(DEFAULT) + r"\b")


def say(text: str, name: str | None = None) -> str:
    """Prose written with the default name, said with this seat's (whole
    words: "Blurp's" becomes "Zed's", "Blurpy" stays)."""
    name = current() if name is None else name
    if name == DEFAULT or not isinstance(text, str):
        return text
    return _WORD.sub(lambda _m: name, text)


def say_all(obj, name: str | None = None):
    """say() over every string in a JSON-shaped value (a new value; the
    original is not changed)."""
    name = current() if name is None else name
    if name == DEFAULT:
        return obj
    if isinstance(obj, str):
        return say(obj, name)
    if isinstance(obj, dict):
        return {k: say_all(v, name) for k, v in obj.items()}
    if isinstance(obj, list):
        return [say_all(v, name) for v in obj]
    if isinstance(obj, tuple):
        return tuple(say_all(v, name) for v in obj)
    return obj


def cli_main(argv: list[str]) -> int:
    """`membrane self name` (show it) | `membrane self name <NAME>` (set it)
    | `membrane self name --reset` (back to the default)."""
    import argparse
    ap = argparse.ArgumentParser(prog="membrane self", description="self (#161) on this seat")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("name", help=f"show or set self's name on this seat (default {DEFAULT}; "
                                    f"{ENV} overrides it)")
    n.add_argument("name", nargs="*", help=f"the new name, 1-{MAX_LEN} printable characters")
    n.add_argument("--reset", action="store_true", help=f"back to the default ({DEFAULT})")
    args = ap.parse_args(argv)
    if args.reset:
        print(reset())
        return 0
    if not args.name:
        print(current())
        return 0
    try:
        got = set_name(" ".join(args.name))
    except ValueError as e:
        print(f"membrane self name: {e}", file=sys.stderr)
        return 2
    print(got)
    if valid(os.environ.get(ENV) or "") and current() != got:
        print(f"(note: {ENV}={current()} overrides it)", file=sys.stderr)
    return 0
