"""THE SEAT RUNS JS -- IN THE BROWSER (2026-10-11, the operator: "seats should be
remote_executor implementations and allow running js code ... in the browser,
it should run in the browser. And yes, make it an opt-in ... do the js for
seats; for running shell/python there could be a different agent").

The seat's presence entity implements remote_executor: its `exec(code,
timeout)` method's command is `js::{{code}}`, and a `js::` command never
reaches a shell -- it goes to a browser tab that opted in ("let the seat act as
you here", per tab, off by default). The tab runs it IN ITS WINDOW (the
operator's choice: "why a sandbox? why not let it access the window?") --
the page, the HA connection, the chat panel in reach, i.e. as that person;
the switch is the consent. Value and console come back. No opted-in tab =
an honest error.

Tabs register over the relay (the person is the HA user, stamped by the
relay) and heartbeat; a job goes out as a membrane.jsexec event on the tab's
own session, so only its owner's tab receives it.
"""
from __future__ import annotations

import json
import re
import secrets
import shlex
import threading
import time

TAB_TTL_S = 45.0
RESULT_MAX = 4000
METHOD = "exec"
COMMAND = "js::{{code}}"

_seat = None                                   # the running seat (bind() at boot)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def bind(srv) -> None:
    """Boot: the seat entity implements remote_executor with a JS exec."""
    global _seat
    _seat = srv
    srv.__dict__.setdefault("_js_tabs", {})
    try:
        _implement(srv)
    except Exception as e:                      # noqa: BLE001
        import sys
        print(f"[jsexec] remote_executor not wired ({e!r})", file=sys.stderr)


def _implement(srv) -> None:
    from . import seat_panel as SP
    from .entities import MEMBRANE_NS
    est = getattr(srv, "entity_store", None)
    ent = SP._entity(srv)
    if est is None or ent is None:
        return
    iface = est._resolve_spec("remote_executor")
    pp = ent.properties or {}
    if iface is not None:
        parents = list(pp.get("parents") or [])
        if iface.eid not in parents:
            pp["parents"] = parents + [iface.eid]
    pp["executes"] = "js (in an opted-in browser tab)"
    ent.properties = pp
    def _of(d):
        mo = (d.properties or {}).get("method_of")
        return ent.eid in (mo if isinstance(mo, list) else [mo])
    mine = [d for d in list((est.docs or {}).values())
            if _of(d) and (d.properties or {}).get("method_name") == METHOD]
    # THE DOOR IS KEPT, NOT JUST CREATED (2026-10-15, the live seat): a model
    # call had REDEFINED presence.exec with a literal shell line, and the JS door
    # stayed dead across every restart -- the boot only defined it when missing
    broken = [d for d in mine if str((d.properties or {}).get("command") or "") != COMMAND]
    if not mine or broken:
        from .night import method_define
        method_define(srv, est, None, None, on=ent.eid, method=METHOD, command=COMMAND)
        if broken:
            import sys
            print(f"[jsexec] the seat's {METHOD} door was redefined "
                  f"({str(broken[0].properties.get('command'))[:60]!r}); restored to {COMMAND}", file=sys.stderr)
    try:
        est.upsert(ent, embedder=None)
    except Exception:
        pass


def tabs(srv=None) -> list[dict]:
    srv = srv or _seat
    now = time.time()
    live = {k: v for k, v in ((getattr(srv, "_js_tabs", None) or {}).items()) if now - v.get("seen", 0) < TAB_TTL_S}
    if srv is not None:
        srv._js_tabs = live
    return sorted(({"tab": k, **v} for k, v in live.items()), key=lambda t: -t["seen"])


def register(srv, tab: str, sid: str, person: str, device: str = "", on: bool = True) -> dict:
    reg = srv.__dict__.setdefault("_js_tabs", {})
    if not on:
        reg.pop(tab, None)
        _note(srv, "js-tab-off", tab=tab, person=person)
        return {"ok": True, "tabs": len(tabs(srv))}
    fresh = tab not in reg
    reg[tab] = {"sid": sid, "person": person, "device": str(device)[:80], "seen": time.time()}
    if fresh:
        _note(srv, "js-tab-on", tab=tab, person=person, device=device)
    return {"ok": True, "tabs": len(tabs(srv))}


def _note(srv, kind, **kw):
    try:
        from . import seat_panel as SP
        SP.note(srv, kind, **kw)
    except Exception:
        pass


def _unquote(code: str) -> str:
    """{{code}} arrives shell-quoted (a method's args are): one quoted word -> the code."""
    try:
        parts = shlex.split(code)
        if len(parts) == 1:
            return parts[0]
    except ValueError:
        pass
    return code


# THE SHELL-LOOKING PAYLOAD (issue #2, 2026-10-14): the door runs ANYTHING
# as JavaScript -- 'echo hi' rode the full round trip, died in the tab as
# SyntaxError or sat till the timeout. Plain words with a space and no JS
# syntax are shell, refused at the door with the honest receipt.
_SHELL_LOOKS = re.compile(r"^[a-zA-Z][\w./:@-]*(?:[ \t]+[\w./:@-]+)+$")
_JS_HEADS = {"await", "async", "return", "function", "let", "const", "var",
             "if", "while", "for", "typeof", "new", "delete", "null", "true",
             "false", "undefined", "this", "throw", "do", "else", "switch",
             "case", "break", "continue", "yield", "import", "of", "void",
             "in", "instanceof"}


def run(code: str, timeout: float = 15.0, tab: str | None = None) -> dict:
    """Run JS in an opted-in tab: {rc, text, err}. rc 0 = it returned."""
    srv = _seat
    if srv is None:
        return {"rc": 127, "text": "", "err": "no seat bound"}
    code = _unquote(str(code or ""))
    _first = code.split()[:1]
    if _SHELL_LOOKS.match(code) and not (_first and _first[0].lower() in _JS_HEADS):
        # shell-looked: rejected loudly, nothing rides (the receipt carries
        # the why and the door to the shell path)
        return {"rc": 2, "text": "", "err":
                (f"js:: runs JavaScript in the browser tab, not shell -- {code[:120]!r} "
                 "reads shell (plain words, no JS syntax). Make it a method's command instead "
                 "(exec(action='define', ...), then exec(action='call', ...)); if JavaScript is meant, give it JS "
                 "shape -- a call, a keyword, an operator (e.g. 'await new Promise(...)').")}
    live = tabs(srv)
    if tab:
        live = [t for t in live if t["tab"] == tab]
    if not live:
        return {"rc": 127, "text": "", "err": "no browser tab runs JS for this seat -- someone has to opt in "
                                            "(membrane home: 'allow JS scripting in this window')"}
    t = live[0]
    job = {"id": "js_" + secrets.token_hex(5), "code": _unquote(str(code or "")), "timeout": float(timeout),
           "tab": t["tab"], "done": threading.Event(), "result": None}
    with _lock:
        _jobs[job["id"]] = job
    r = getattr(srv, "_loop_runner", None)
    try:
        r.hub.publish({"type": "membrane.jsexec", "sessionID": t["sid"],
                       "job": {k: job[k] for k in ("id", "code", "timeout", "tab")}})
    except Exception as e:                      # noqa: BLE001
        with _lock:
            _jobs.pop(job["id"], None)
        return {"rc": 127, "text": "", "err": f"could not reach the tab: {e!r}"}
    got = job["done"].wait(float(timeout) + 3.0)
    with _lock:
        _jobs.pop(job["id"], None)
    if not got:
        return {"rc": -1, "text": "", "err": f"the tab did not answer in {timeout:g}s"}
    res = job["result"] or {}
    text = ""
    # THE FAKE VALUE (issue #2: '=> None'): the server's job dict always
    # CARRIES the value key (json null -> Python None), so `"value" in res`
    # was true for a value the tab never returned -- the receipt said
    # "=> None" for an undefined return. A failed run says why, no value.
    if res.get("ok"):
        if res.get("value") is not None:
            text += "=> " + str(res.get("value"))[:RESULT_MAX]
        else:
            text += "=> undefined (the job returned no value)"
    if res.get("logs"):
        text += ("\n" if text else "") + "\n".join(str(x) for x in res["logs"])[:RESULT_MAX]
    if res.get("error"):
        text += ("\n" if text else "") + "error: " + str(res["error"])[:RESULT_MAX]
    return {"rc": 0 if res.get("ok") else 1, "text": text.strip(), "err": ""}


def result(srv, body: dict) -> dict:
    """A tab's answer to a job."""
    with _lock:
        job = _jobs.get(str(body.get("id") or ""))
    if job is None:
        return {"ok": False, "error": "no such job (late, or not yours)"}
    reg = (getattr(srv, "_js_tabs", None) or {}).get(job["tab"]) or {}
    if body.get("person") and reg.get("person") and body["person"] != reg["person"]:
        return {"ok": False, "error": "not your tab's job"}
    job["result"] = {k: body.get(k) for k in ("ok", "value", "logs", "error")}
    job["done"].set()
    return {"ok": True}
