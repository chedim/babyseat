"""THE WRITE JOURNAL (#971 step 2): every field write, kept, so a VM can be replayed.

writes.py makes every write an event, but only to in-memory listeners: the store keeps the resulting
state and nothing keeps the writes. This listener appends them to a JSONL file, one line per write:

    {"ts", "path", "eid", "prop", "new", "writer", "chain": {"id", "root", "via"}[, "born"]}

A birth (`path == eid`, nothing -> eid) carries what writes.diff() leaves out, the entity's content:
`"born": {"cls", "props", "position"}`, so a fresh store can be rebuilt from the journal alone.
Values are serialized when the write happens (an in-place edit later must not rewrite them).

`replay(path, store)` applies every line to a store. `replay_vm(path, store, make_vm)` is the VM's
contract: only the OUTSIDE writes (everything not written by the VM itself, `trigger:*`) go in, a fresh
VM runs on them, and its writes must equal the recorded trigger writes, in order. A native VM passes
the same test.

Off unless a seat opens one. Listener rules (writes.py) hold: cheap, never writes; the lines go to the
file from a queue on the journal's own thread.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from . import writes as W

VM_WRITER = "trigger:"


def _plain(v):
    """A JSON-safe copy of a value as it is now."""
    try:
        return json.loads(json.dumps(v, default=str))
    except Exception:
        return str(v)


def _position(ent):
    pos = getattr(ent, "position", None)
    if pos is None:
        return None
    try:
        return [float(x) for x in pos]
    except Exception:
        return None


class Journal:
    def __init__(self, path, store=None):
        self.path = Path(path)
        self.store = store
        self._q: queue.Queue = queue.Queue()
        self._fh = self.path.open("a", encoding="utf-8")
        self._t = threading.Thread(target=self._run, daemon=True, name="journal")
        self._t.start()
        self.lines = 0

    def attach(self) -> None:
        W.on_write(self._hear)

    def detach(self) -> None:
        W.off_write(self._hear)

    def _hear(self, w: dict) -> None:
        ch = w.get("chain") or None
        line = {"ts": w.get("ts"), "path": w.get("path"), "eid": w.get("eid"), "prop": w.get("prop") or "",
                "new": _plain(w.get("new")), "writer": w.get("writer") or "",
                "chain": ({"id": ch.get("id"), "root": ch.get("root", ""), "via": list(ch.get("via") or [])}
                          if isinstance(ch, dict) else None)}
        eid = w.get("eid")
        if self.store is not None and eid and w.get("path") == eid and w.get("old") is None and w.get("new") == eid:
            ent = (self.store.docs or {}).get(eid)
            if ent is not None:
                line["born"] = {"cls": ent.cls, "props": _plain(dict(ent.properties or {})),
                                "position": _position(ent)}
        self._q.put(json.dumps(line, ensure_ascii=False))

    def _run(self) -> None:
        while True:
            s = self._q.get()
            if s is None:
                break
            self._fh.write(s + "\n")
            self.lines += 1
            if self._q.empty():
                self._fh.flush()

    def flush(self) -> None:
        while not self._q.empty():
            threading.Event().wait(0.01)
        self._fh.flush()

    def close(self) -> None:
        self.detach()
        self._q.put(None)
        self._t.join()
        self._fh.close()


def read(path):
    with Path(path).open(encoding="utf-8") as fh:
        for s in fh:
            s = s.strip()
            if s:
                yield json.loads(s)


def apply(store, line: dict) -> None:
    """One journal line onto a store, with its writer and chain (so the store's own events carry them)."""
    from .entities import QuantumEntity
    path, eid, prop, new = line["path"], line.get("eid"), line.get("prop") or "", line.get("new")
    if path.startswith("rel:") or not eid:
        return                                   # relations: not in the replay yet (flagged in docs/vm-api.md)
    ch = line.get("chain")
    with W.resume(ch) if ch else _nullctx(), W.as_writer(line.get("writer") or "field"):
        if path == eid:
            if new is None:
                store.remove_many([eid])
            elif "born" in line:
                b = line["born"]
                store.upsert(QuantumEntity(eid=eid, cls=b.get("cls"), properties=dict(b.get("props") or {}),
                                           position=b.get("position")), embedder=None)
            return
        ent = (store.docs or {}).get(eid)
        if ent is None:
            return
        if prop == "position":
            ent.position = new
        elif new is None:
            (ent.properties or {}).pop(prop, None)
        else:
            pp = ent.properties or {}
            pp[prop] = new
            ent.properties = pp
        store.upsert(ent, embedder=None)


class _nullctx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def replay(path, store, keep=lambda line: True) -> int:
    n = 0
    for line in read(path):
        if keep(line):
            apply(store, line)
            n += 1
    return n


def replay_vm(path, store, make_vm, record=None) -> dict:
    """The VM contract: the outside writes in, a fresh VM drained after each, its writes compared with the
    recorded `trigger:*` writes ((path, new), in order)."""
    lines = list(read(path))
    want = [(ln["path"], ln.get("new")) for ln in lines if str(ln.get("writer", "")).startswith(VM_WRITER)]
    got: list = []

    def hear(w):
        if str(w.get("writer", "")).startswith(VM_WRITER):
            got.append((w.get("path"), _plain(w.get("new"))))
    jn = Journal(record, store) if record else None
    if jn:
        jn.attach()
    vm = make_vm(store)
    vm.attach()
    W.on_write(hear)
    try:
        for ln in lines:
            if str(ln.get("writer", "")).startswith(VM_WRITER):
                continue
            apply(store, ln)
            vm.drain()
    finally:
        W.off_write(hear)
        vm.detach()
        if jn:
            jn.close()
    mism = [{"at": i, "want": w, "got": g} for i, (w, g) in enumerate(zip(want, got)) if w != g]
    if len(want) != len(got):
        mism.append({"at": min(len(want), len(got)), "want_n": len(want), "got_n": len(got)})
    return {"outside": len(lines) - len(want), "vm_writes": len(got), "mismatches": mism}
