"""SELF'S NAME, PER SEAT (#283, the zoo: several seats in one Home Assistant,
a card per seat -- the cards differ only if each seat's self has its own name).

The operator named self Blurp (#277); that is now the DEFAULT. A seat names its
own in its settings (`self_name` in <data dir>/settings.json, "" = the
default), MEMBRANE_SELF_NAME overrides it, and every place the seat says self's
name to a person reads current(): the sheet's header and prose and the dock
(self_sheet), the seat card (lanes).

Not the seat's own name: settings' `name` is the assistant's (the name
ceremony, naming.py -- "Hey Bob"); this one is self's (membrane::self).

Set it with `membrane self name <NAME>` (here, cli_main) or the seat's
control door, POST /membrane/seat/control {"self_name": ...} (seat_panel.control).

#299: `membrane self accept <ID>` (here too) writes the operator's accept
line through the running seat's control door ({"accept": ID}): the seat
writes the record line, so the CLI never touches the record itself (#576:
it grants nothing until the new learner reads it).
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


SERVE_URL_ENV = "MEMBRANE_SERVE_URL"      # #299: where `self accept` finds the running seat
SERVE_URL = "http://127.0.0.1:4096"


def _post_json(url: str, body: dict, headers: dict) -> tuple[int, dict]:
    """POST a JSON body -> (status, the JSON answer or {})."""
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def _auth_headers() -> dict:
    """JWT mode: the client token; else the seat token when this machine has
    one (an off-box --url needs it; localhost needs nothing). Never minted here."""
    from . import jwtauth as _JWT
    h = _JWT.client_headers()
    if h:
        return h
    try:
        from . import seatauth as _SA
        tok = _SA._read(_SA.token_file())
    except Exception:
        tok = None
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def accept_main(ident: str, url: str | None = None) -> int:
    """`membrane self accept <ID>`: the running seat accepts it (#299)."""
    base = (url or os.environ.get(SERVE_URL_ENV) or SERVE_URL).rstrip("/")
    try:
        code, data = _post_json(base + "/membrane/seat/control", {"accept": ident}, _auth_headers())
    except OSError as e:
        print(f"membrane self accept: the seat at {base} is not reachable ({e})", file=sys.stderr)
        return 1
    out = ((data or {}).get("applied") or {}).get("accept") if isinstance(data, dict) else None
    if code != 200 or not isinstance(out, dict):
        print(f"membrane self accept: the seat answered {code} (its control door: localhost, or the seat token)",
              file=sys.stderr)
        return 1
    if not out.get("ok"):
        print(f"membrane self accept: {out.get('error') or 'refused'}", file=sys.stderr)
        return 2
    nm = out.get("name") or out.get("id")
    print(f"already accepted: {nm}" if out.get("already") else f"accepted: {nm} -- what it leads to opens now")
    return 0


def cli_main(argv: list[str]) -> int:
    """`membrane self name` (show it) | `membrane self name <NAME>` (set it)
    | `membrane self name --reset` (back to the default) | `membrane self
    accept <ID> [--url URL]` (#299: the operator's accept line)."""
    import argparse
    ap = argparse.ArgumentParser(prog="membrane self", description="self (#161) on this seat")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("name", help=f"show or set self's name on this seat (default {DEFAULT}; "
                                    f"{ENV} overrides it)")
    n.add_argument("name", nargs="*", help=f"the new name, 1-{MAX_LEN} printable characters")
    n.add_argument("--reset", action="store_true", help=f"back to the default ({DEFAULT})")
    a = sub.add_parser("accept", help="the operator's accept line on self's record (#299; #576: it grants nothing yet)")
    a.add_argument("id", help="what is accepted (an id)")
    a.add_argument("--url", default=None, help=f"the seat's serve URL (default ${SERVE_URL_ENV} or {SERVE_URL})")
    args = ap.parse_args(argv)
    if args.cmd == "accept":
        return accept_main(args.id, args.url)
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
