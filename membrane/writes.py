"""EVERY WRITE IS AN EVENT (#576 part A): the field's writes with provenance.

A write is {path, old, new, writer, chain, ts}: `path` is `<eid>` (an entity
born or removed), `<eid>.<prop>` (a property, `parents` included) or
`<eid>.position`; `rel:<subj>|<rel>|<obj>` is a field relation. Every write is
an overwrite -- nothing -> something is a birth, something -> nothing a removal.

The WRITER is who wrote it (`field` unless a caller says: `person:<name>`,
`llm:<model>`, `script:<eid>`, `self`). The CHAIN is the causal chain the write
belongs to: a source starts one (a person's turn, a sensor, the field, Blurp)
and everything it causes carries it -- the pool's events, jobs, LLM calls and
loop turns hand it on (stamp / resume), so a reply minutes later still belongs
to the chain that asked for it. `via` lists the triggers the chain has passed
through, for the loop rule (#576 part B).

Listeners are called synchronously and must be cheap; a listener that raises is
dropped from that call only. No listeners: a write costs one context read."""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import secrets
import time

_WRITER: contextvars.ContextVar[str] = contextvars.ContextVar("membrane_writer", default="field")
_CHAIN: contextvars.ContextVar[dict | None] = contextvars.ContextVar("membrane_chain", default=None)

listeners: list = []

SMALL = 2048          # a value this long (serialized) keeps its old value; longer ones keep a hash only


def on_write(fn) -> None:
    if fn not in listeners:
        listeners.append(fn)


def off_write(fn) -> None:
    if fn in listeners:
        listeners.remove(fn)


def writer() -> str:
    return _WRITER.get()


def current() -> dict | None:
    return _CHAIN.get()


@contextlib.contextmanager
def as_writer(name: str):
    tok = _WRITER.set(str(name or "field"))
    try:
        yield
    finally:
        _WRITER.reset(tok)


@contextlib.contextmanager
def source(kind: str, ident: str = ""):
    """A new chain, rooted at a source (a person's turn, a sensor, self)."""
    tok = _CHAIN.set({"id": f"{kind}-{ident or secrets.token_hex(6)}", "root": str(kind), "via": []})
    try:
        yield _CHAIN.get()
    finally:
        _CHAIN.reset(tok)


@contextlib.contextmanager
def resume(ev_or_chain):
    """Continue the chain an event (or a stored chain) carries; none: as is."""
    ch = ev_or_chain.get("chain") if isinstance(ev_or_chain, dict) and "id" not in ev_or_chain \
        else ev_or_chain
    if not isinstance(ch, dict) or not ch.get("id"):
        yield _CHAIN.get()
        return
    tok = _CHAIN.set({"id": ch["id"], "root": ch.get("root", ""), "via": list(ch.get("via") or [])})
    try:
        yield _CHAIN.get()
    finally:
        _CHAIN.reset(tok)


def stamp(ev: dict) -> dict:
    """An event put on a queue carries the chain it was caused in (unless it has one)."""
    ch = _CHAIN.get()
    if isinstance(ev, dict) and ch is not None and "chain" not in ev:
        ev["chain"] = {"id": ch["id"], "root": ch.get("root", ""), "via": list(ch.get("via") or [])}
    return ev


def emit(path: str, old, new) -> None:
    if not listeners:
        return
    ch = _CHAIN.get()
    w = {"path": path, "old": old, "new": new, "writer": _WRITER.get(),
         "chain": dict(ch, via=list(ch.get("via") or [])) if ch else None, "ts": time.time()}
    for fn in list(listeners):
        try:
            fn(w)
        except Exception:
            pass


# ---- the store's snapshot: what each entity last held, to diff an in-place edit --------

def _fp(v):
    """(fingerprint, the value when small enough to keep)."""
    try:
        s = json.dumps(v, sort_keys=True, default=str)
    except Exception:
        s = repr(v)
    return hashlib.blake2b(s.encode(), digest_size=8).hexdigest(), (v if len(s) <= SMALL else None)


def snapshot(ent) -> dict:
    snap = {k: _fp(v) for k, v in (getattr(ent, "properties", None) or {}).items()}
    pos = getattr(ent, "position", None)
    if pos is not None:
        try:
            snap["\0position"] = (_fp([round(float(x), 6) for x in pos])[0], None)
        except Exception:
            pass
    return snap


def diff(eid: str, before: dict | None, ent) -> tuple[dict, list]:
    """(the new snapshot, [(path, old, new)]) for one entity's write."""
    after = snapshot(ent)
    if before is None:
        return after, [(eid, None, eid)]
    out = []
    props = getattr(ent, "properties", None) or {}
    for k in set(before) | set(after):
        b, a = before.get(k), after.get(k)
        if (b[0] if b else None) == (a[0] if a else None):
            continue
        if k == "\0position":
            out.append((f"{eid}.position", None, getattr(ent, "position", None)))
        else:
            out.append((f"{eid}.{k}", b[1] if b else None, props.get(k)))
    return after, out


# ---- carrying the chain across threads and the MCP relay --------------------------------

def carry(fn):
    """fn, run later (a thread's target) in the context it was handed over in."""
    ctx = contextvars.copy_context()
    return lambda *a, **k: ctx.run(fn, *a, **k)


HDR_CHAIN, HDR_WRITER = "X-Membrane-Chain", "X-Membrane-Writer"


def headers(default_writer: str = "") -> dict:
    """What a relay call to the seat carries: the chain and the writer it was made in."""
    out = {}
    ch = _CHAIN.get()
    if ch is not None:
        out[HDR_CHAIN] = json.dumps(ch)
    w = _WRITER.get()
    if w != "field" or default_writer:
        out[HDR_WRITER] = w if w != "field" else default_writer
    return out


@contextlib.contextmanager
def from_headers(hdrs):
    """A seat handler resumes the chain and writer its caller sent (none: as is)."""
    try:
        ch = json.loads(hdrs.get(HDR_CHAIN) or "null") if hdrs is not None else None
    except Exception:
        ch = None
    w = hdrs.get(HDR_WRITER) if hdrs is not None else None
    with resume(ch if isinstance(ch, dict) else None), as_writer(w or _WRITER.get()):
        yield


@contextlib.contextmanager
def turn(sid: str, writer_name: str = "llm"):
    """A model turn: it continues the chain that woke it (a loop's event), else a
    person's message starts one; what the turn writes, the model wrote."""
    if _CHAIN.get() is not None:
        with as_writer(writer_name):
            yield
        return
    with source("person", str(sid)), as_writer(writer_name):
        yield
