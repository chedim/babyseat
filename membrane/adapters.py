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


def _branch(path: str) -> tuple[str, str, str] | None:
    """(branch, eid, prop) when this path is an adapter branch's property."""
    eid, _, prop = path.rpartition(".")
    if not eid:
        return None
    from .entities import MEMBRANE_NS
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

def _consume(est, ent, prop: str, new, receipt: str) -> None:
    pp = ent.properties or {}
    if pp.get(prop) == new:                  # a newer write stays for its own turn
        pp[prop] = ""
    pp[prop + "_result"] = str(receipt)[:400]
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
             if out.get("ok") else out.get("error"))


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
    _consume(est, ent, prop, new, r.get("text") if isinstance(r, dict) else r)


def _seat(srv, est, ent, prop, new, by) -> None:
    from . import restarts as RS
    why = str(new.get("why") or "") if isinstance(new, dict) else str(new)
    r = RS.do_restart(srv, why)                    # it signals the stop a moment later, on its own thread
    _consume(est, ent, prop, new, r.get("text") if isinstance(r, dict) else r)


def _methods(srv, est, ent, prop, new, by) -> None:
    """#576 C: `<method>.invoke = {object, args}` runs that method on the object -- how a
    learned call acts (the call itself records `last_call`, as any call does). The
    executor itself is refused here exactly as on the model's route (#581)."""
    from . import night as N
    name = (ent.properties or {}).get("method_name")
    if not name or not isinstance(new, dict) or not new.get("object"):
        return
    args = new.get("args") if isinstance(new.get("args"), dict) else {}
    r = N.method_call(srv, est, getattr(srv, "efield", None), getattr(srv, "membrane", None),
                      str(new["object"]), str(name), args)
    _consume(est, ent, prop, new, (r.get("head") or r.get("status") or "") if isinstance(r, dict) else r)


EFFECTS = {"chat": _chat, "toys": _toys, "llm": _llm, "seat": _seat, "methods": _methods}


def _run(b, w) -> None:
    srv = _SEAT["srv"]
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
