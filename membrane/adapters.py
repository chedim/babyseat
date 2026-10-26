"""EFFECTS ARE WRITES (#576 part E): the adapter branches.

An effect happens because something WROTE a property of a field entity, not
because a command string was dispatched. One write listener maps these writes
to the effects that already exist:

  chat:<person>.say          chat_io.on_edit: the line is posted in their chat
  <toy>.poke  "<act> [arg]"  toybox.poke: a toy entity (properties.toybox);
                             a tower block's poke names its block itself
  membrane::llm.call         llm.request: {about, prompt} (a string: the prompt)
  membrane::llm.wake         llm.set_wake: {about, at, prompt}
  membrane::llm.unwake       llm.unwake: the wake's id
  membrane::presence.restart restarts.do_restart: the reason
  <method>.invoke            night.method_call: {object, args} (#576 C: a learned call acts)

The listener only queues (a listener must not write); the effect runs on the
adapters' one worker thread, in the chain of the write that caused it. The
worker CONSUMES the property (back to '') and leaves the effect's receipt in
`<prop>_result` (the chat thing keeps its own: `said` / `say_refused`); those
writes are the adapter's own (writer `adapter:<branch>`) and fire nothing. A
write that empties a branch, and a birth that carries one, are no effect.

The writer is provenance: it labels who poked a toy (a person's write pokes as
that person, anything else as the model) and is the llm's caller tag (its
per-caller caps, read-only for a `script:` writer -- as a script's own call is);
otherwise nothing gates on it but the adapter's own writes, against a loop.
"""
from __future__ import annotations

import queue
import threading

from . import writes as W

WRITER = "adapter:"
_q: queue.Queue = queue.Queue()
_SEAT: dict = {"srv": None, "thread": None}
_busy = threading.Event()             # a job is running (drain() waits it out)
_DONE = threading.Condition()         # a job finished (write_and_wait)


def _branch(path: str) -> tuple[str, str, str] | None:
    """(branch, eid, prop) when this path is an adapter branch's property."""
    eid, _, prop = path.rpartition(".")
    if not eid:
        return None
    from .entities import MEMBRANE_NS
    from . import toybox as _TBX
    if eid == _TBX._eid() and prop in ("list", "add", "remove", "reset", "poke"):
        return "toybox", eid, prop             # #576 G2: the toybox object's verbs
    if prop == "js" and eid == MEMBRANE_NS + "presence":
        return "js", eid, prop                 # #576 G2: the seat's JS (an opted-in browser tab)
    if prop == "say":
        from . import chat_io as CIO
        return ("chat", eid, prop) if CIO.person_of(eid) is not None else None
    if prop == "poke":
        return "toys", eid, prop               # a toy or not: the worker reads properties.toybox
    if prop in ("call", "wake", "unwake") and eid == MEMBRANE_NS + "llm":
        return "llm", eid, prop
    if prop == "restart" and eid == MEMBRANE_NS + "presence":
        return "seat", eid, prop
    if prop == "invoke":
        return "methods", eid, prop             # a method entity or not: the worker reads method_name
    return None


def listen(w: dict) -> None:
    """The write listener: cheap, never writes -- it queues the effect."""
    if _SEAT["srv"] is None or w.get("new") in (None, "", {}):
        return
    b = _branch(str(w.get("path") or ""))
    if b is None or w.get("writer") == WRITER + b[0]:
        return
    _q.put((b, w))


# ---- the effects (the existing functions) ------------------------------------------

def _rc(r, default: int = 0):
    """The effect's return code: its rc, or 0 / 1 from an ok / status."""
    if not isinstance(r, dict):
        return default
    if r.get("rc") is not None:
        return r["rc"]
    if "ok" in r:
        return 0 if r["ok"] else 2
    if r.get("status"):
        return 0 if r["status"] == "ok" else 1
    return default


def _consume(est, ent, prop: str, new, receipt: str, rc=0) -> None:
    pp = ent.properties or {}
    if pp.get(prop) == new:                  # a newer write stays for its own turn
        pp[prop] = ""
    pp[prop + "_result"] = str(receipt)[:4000]
    pp[prop + "_rc"] = rc                    # #576 G2: a write step reports the effect's outcome
    ent.properties = pp
    est.upsert(ent, embedder=None)


def _chat(srv, est, ent, prop, new, by) -> None:
    from . import chat_io as CIO
    if CIO.on_edit(srv, ent) is not None:    # None: already posted (the edit route) or empty
        est.upsert(ent, embedder=None)


def _toys(srv, est, ent, prop, new, by) -> None:
    from . import toybox as TB
    tb = (ent.properties or {}).get("toybox") or {}
    if not isinstance(tb, dict) or not tb.get("name"):
        return
    toks = str(new).split()
    if not toks:
        return
    arg = str(tb["block"]) if tb.get("block") else (toks[1] if len(toks) > 1 else None)
    who = by.split(":", 1)[1] if by.startswith("person:") else "model"   # a label (who poked), never a gate
    out = TB.poke(str(tb["name"]), toks[0], arg, by=who)
    _consume(est, ent, prop, new, f"{out.get('action')} {out.get('name')}: now {out.get('reading')}"
             if out.get("ok") else out.get("error"), rc=_rc(out))


def _llm(srv, est, ent, prop, new, by) -> None:
    from . import llm as LLM
    d = new if isinstance(new, dict) else {}
    if prop == "call":
        r = LLM.request(srv, d.get("prompt") if d else str(new), about=str(d.get("about") or ""),
                        caller=by)
    elif prop == "wake":
        r = LLM.set_wake(srv, str(d.get("about") or ""), str(d.get("at") or ""), str(d.get("prompt") or ""),
                         caller=by)
    else:
        r = LLM.unwake(srv, str(d.get("id") or "") if d else str(new))
    _consume(est, ent, prop, new, r.get("text") if isinstance(r, dict) else r, rc=_rc(r))


def _seat(srv, est, ent, prop, new, by) -> None:
    from . import restarts as RS
    why = str(new.get("why") or "") if isinstance(new, dict) else str(new)
    r = RS.do_restart(srv, why)                    # it signals the stop a moment later, on its own thread
    _consume(est, ent, prop, new, r.get("text") if isinstance(r, dict) else r, rc=_rc(r))


def _methods(srv, est, ent, prop, new, by) -> None:
    """#576 C: `<method>.invoke = {object, args}` runs that method on the object -- how a
    learned call acts (the call itself records `last_call`, as any call does). The
    executor itself is refused here exactly as on the model's route (#581)."""
    from . import night as N
    name = (ent.properties or {}).get("method_name")
    if not name or not isinstance(new, dict) or not new.get("object"):
        return
    args = new.get("args") if isinstance(new.get("args"), dict) else {}
    why = N.direct_exec_refusal(est, str(new["object"]), str(name))   # the executor: refused as on the route
    if why:
        _consume(est, ent, prop, new, why, rc=2)
        return
    r = N.method_call(srv, est, getattr(srv, "efield", None), getattr(srv, "membrane", None),
                      str(new["object"]), str(name), args)
    _consume(est, ent, prop, new, (r.get("head") or r.get("status") or "") if isinstance(r, dict) else r, rc=_rc(r))


def _toybox(srv, est, ent, prop, new, by) -> None:
    """#576 G2: the toybox object's verbs as writes -- list / add <spec> / remove <name> /
    reset <name> / poke {toy, action}; its own dispatcher answers."""
    import shlex
    from . import toybox as TB
    if prop == "list":
        line = "list"
    elif prop == "poke":
        d = new if isinstance(new, dict) else {}
        line = f"poke {shlex.quote(str(d.get('toy') or ''))} {shlex.quote(str(d.get('action') or ''))}"
    else:
        line = f"{prop} {shlex.quote(str(new))}"
    r = TB.from_command(line)
    _consume(est, ent, prop, new, r.get("text") if isinstance(r, dict) else r, rc=_rc(r))


def _js(srv, est, ent, prop, new, by) -> None:
    """#576 G2: presence's `js` -- the code runs in an opted-in browser tab (jsexec)."""
    from . import jsexec as JS
    r = JS.run(str(new or ""), timeout=float(getattr(srv, "_js_timeout", 15.0)))
    text = str(r.get("text") or "") if isinstance(r, dict) else str(r)
    err = str(r.get("err") or "") if isinstance(r, dict) else ""
    if err and (r.get("rc") if isinstance(r, dict) else 0) != -1:
        text = (text + "\n" if text else "") + err
    _consume(est, ent, prop, new, text, rc=_rc(r))


EFFECTS = {"chat": _chat, "toys": _toys, "llm": _llm, "seat": _seat, "methods": _methods,
           "toybox": _toybox, "js": _js}


def write_and_wait(est, eid: str, prop: str, value, timeout: float = 20.0, srv=None) -> dict:
    """#576 G2, a method's WRITE STEP: write the property and return the adapter's
    receipt (`<prop>_result`; the chat thing's `said`). On the adapters' own worker (a
    learned invoke running a method) the effect runs inline -- one worker never waits
    on itself."""
    import time as _t
    ent = (est.docs or {}).get(eid)
    if ent is None:
        return None
    b = _branch(f"{eid}.{prop}")
    srv = srv or _SEAT["srv"] or getattr(est, "_srv", None)
    if b is None or srv is None:
        ent.properties[prop] = value                 # a plain write: no effect to wait for
        est.upsert(ent, embedder=None)
        return {"text": "written", "rc": 0}
    t = _SEAT.get("thread")
    if threading.current_thread() is t or t is None or not t.is_alive() or _SEAT["srv"] is None:
        # inline: on the worker itself (a learned invoke), or with no worker running
        _run(b, {"path": f"{eid}.{prop}", "eid": eid, "prop": prop, "new": value,
                 "writer": W.writer(), "chain": W.current()}, srv=srv)
        return _receipt(ent, prop)
    ent.properties[prop] = value
    est.upsert(ent, embedder=None)                   # the listener queues the effect
    end = _t.time() + max(0.1, float(timeout))
    with _DONE:
        while (ent.properties or {}).get(prop) == value and _t.time() < end:
            _DONE.wait(min(0.5, max(0.01, end - _t.time())))
    if (ent.properties or {}).get(prop) == value:
        return {"text": f"(no receipt within {timeout:g}s: the effect is still queued)", "rc": None}
    return _receipt(ent, prop)


def _receipt(ent, prop: str) -> dict:
    pp = ent.properties or {}
    text = pp.get(prop + "_result")
    if text is None and prop == "say":
        said = pp.get("said")
        text = said[-1].get("text") if isinstance(said, list) and said and isinstance(said[-1], dict) else said
    return {"text": text, "rc": pp.get(prop + "_rc", 0)}


def _run(b, w, srv=None) -> None:
    srv = srv if srv is not None else _SEAT["srv"]
    est = getattr(srv, "entity_store", None)
    branch, eid, prop = b
    ent = (est.docs or {}).get(eid) if est is not None else None
    if ent is None:
        return
    by = str(w.get("writer") or "field")           # the llm's caller tag (its caps): provenance
    with W.resume(w), W.as_writer(WRITER + branch):
        EFFECTS[branch](srv, est, ent, prop, w.get("new"), by)


def _worker() -> None:
    while True:
        job = _q.get()
        if job is None:
            _q.task_done()
            return
        _busy.set()
        try:
            _run(*job)
        except Exception as e:                  # noqa: BLE001
            import sys
            print(f"[adapters] {job[0][0]} effect failed ({e!r})", file=sys.stderr)
        finally:
            _busy.clear()
            _q.task_done()
            with _DONE:
                _DONE.notify_all()


def start(srv) -> None:
    """Boot: the listener and its one worker."""
    _SEAT["srv"] = srv
    W.on_write(listen)
    t = _SEAT.get("thread")
    if t is None or not t.is_alive():
        t = threading.Thread(target=_worker, daemon=True, name="adapters")
        _SEAT["thread"] = t
        t.start()


def drain(timeout: float = 5.0) -> None:
    """Wait until the queued effects have run (tests)."""
    import time
    end = time.time() + timeout
    while (_q.unfinished_tasks or _busy.is_set()) and time.time() < end:
        time.sleep(0.01)


def stop() -> None:
    """Unhook and end the worker (tests)."""
    W.off_write(listen)
    t = _SEAT.get("thread")
    if t is not None and t.is_alive():
        _q.put(None)
        t.join(timeout=5)
    _SEAT.update(srv=None, thread=None)
