"""LIVE FILE TREES, OPT-IN (#715 part A, the operator 2026-10-30: "generic, not
membrane-specific, and opt-in through entities").

Three classes, seeded once like tool:clock / tool:filetree:

  fs:dir   an entity that extends it, with a `path`, gets that directory's file
           tree on the graph: one entity per file and one per subdirectory, each
           `within` its container (the asker itself for the top level). The
           autonode's fingerprint scan (FileTreeScanner, its cadence) keeps it:
           a new file appears, a gone one is RETIRED (mark-don't-delete) and
           revives when it comes back. Dot-dirs and codegraph's skip dirs
           (.venv, node_modules, ...) are never walked.
  fs:file  what those entities are. They ARE the field's file entities
           (`files:<path>`, field id f:<path>): repo-relative under the seat's
           root, absolute elsewhere -- an existing one is reused, never doubled.
  fs:code  a file entity that also extends it gets its symbol tree (srctree:
           module, class, function, method; the grammar by extension).

NOTHING IS SCANNED UNLESS AN ENTITY ASKS -- this repo included. `within` is a
plain property, not `parent`: `parent` is the legacy ISA key, and a file must
not inherit its directory's fs:dir (it would scan itself).

THE FIRST BUILD IS PACED: about BUILD_CHUNK entity writes per scan pass (file
entities, then symbols, a whole file at a time), so a big tree lands over
several passes instead of one burst of writes."""
from __future__ import annotations

import time
from pathlib import Path

from . import srctree as ST
from .entities import CLASS_COMPONENT, MEMBRANE_NS, MultiParentOps, QuantumEntity

DIR, FILE, CODE = MEMBRANE_NS + "fs:dir", MEMBRANE_NS + "fs:file", MEMBRANE_NS + "fs:code"
FILES = MEMBRANE_NS + "files:"
LIVE, RETIRED = ST.LIVE, ST.RETIRED
BUILD_CHUNK = 500          # entity writes per scan pass (operator approval: #735)

_CLASSES = {
    DIR: "a directory whose file tree is on the graph, kept live (extend it, set `path`)",
    FILE: "a file on the graph (the files:<path> entities)",
    CODE: "a file whose symbol tree is on the graph, kept live (extend it on a file entity)",
}


def seed_classes(est) -> int:
    """Plant fs:dir / fs:file / fs:code once (idempotent). -> how many were new."""
    n = 0
    for eid, summary in _CLASSES.items():
        if (est.docs or {}).get(eid) is None:
            est.upsert(QuantumEntity(eid=eid, cls=CLASS_COMPONENT,
                                     properties={"name": eid[len(MEMBRANE_NS):], "summary": summary,
                                                 "silent_birth": True}),
                       embedder=None)
            n += 1
    return n


class Trees:
    """The asked-for trees, one scanner per fs:dir; pump() is one pass (the
    autonode calls it on its scan cadence; tests call it directly)."""

    def __init__(self, srv, est, seat_root):
        self.srv, self.est = srv, est
        self.seat_root = Path(seat_root).resolve()
        self.dirs: dict = {}            # asker eid -> {root, scan, queue}

    # ---- identity -----------------------------------------------------------------
    def key(self, p: Path) -> str:
        p = Path(p).resolve()
        try:
            return p.relative_to(self.seat_root).as_posix()
        except ValueError:
            return p.as_posix()

    def eid(self, p: Path) -> str:
        return FILES + self.key(p)

    def _path(self, props: dict) -> Path | None:
        s = str(props.get("path") or "").strip()
        if not s:
            return None
        p = Path(s).expanduser()
        return (p if p.is_absolute() else self.seat_root / p).resolve()

    # ---- the census: who asks -----------------------------------------------------
    def census(self) -> tuple[dict, dict]:
        """(fs:dir askers, fs:code files): eid -> path, by the ISA chain."""
        docs = dict(self.est.docs or {})
        memo: dict = {}

        def under(eid: str, cls: str, depth=0) -> bool:
            k = (eid, cls)
            if k not in memo:
                memo[k] = False
                d = docs.get(eid)
                ps = MultiParentOps._parents_of(d.properties or {}) if d is not None else []
                memo[k] = depth < 32 and any(p == cls or under(p, cls, depth + 1) for p in ps)
            return memo[k]
        dirs, codes = {}, {}
        for eid, d in docs.items():
            p = d.properties or {}
            if eid in _CLASSES or p.get("status") == RETIRED or not MultiParentOps._parents_of(p):
                continue
            path = self._path(p)
            if path is None:
                continue
            if under(eid, DIR):
                dirs[eid] = path
            if under(eid, CODE):
                codes[eid] = path
        return dirs, codes

    # ---- one pass -----------------------------------------------------------------
    def pump(self) -> dict:
        from .autonode import FileTreeScanner
        out = {"files": 0, "retired": 0, "symbols": 0}
        dirs, codes = self.census()
        for eid in [e for e in self.dirs if e not in dirs or self.dirs[e]["root"] != dirs[e]]:
            self.dirs.pop(eid)
        for eid, root in dirs.items():
            t = self.dirs.get(eid)
            if t is None:
                if not root.is_dir():
                    continue
                sc = FileTreeScanner(root)
                sc.scan()                               # the baseline: every file, queued
                self.dirs[eid] = {"root": root, "scan": sc, "queue": sorted(sc._fps)}
                continue
            for trig in t["scan"].scan():
                ev, rel = trig.payload.get("event"), trig.payload.get("path")
                if ev == "created":
                    t["queue"].append(rel)
                elif ev == "deleted":
                    if rel in t["queue"]:
                        t["queue"].remove(rel)
                    out["retired"] += self._retire(self.eid(root / rel))
        budget = BUILD_CHUNK
        for eid, t in self.dirs.items():
            while t["queue"] and budget > 0:
                rel = t["queue"].pop(0)
                if (t["root"] / rel).is_file():
                    self._put_file(eid, t["root"], rel)
                    out["files"] += 1
                    budget -= 1
        for eid, path in codes.items():                 # the same budget: one file at a time
            if budget <= 0:
                break
            r = ST.sync_if_stale(self.srv, self.key(path), path, within=eid)
            out["symbols"] += r["upserted"]
            out["retired"] += r["retired"]
            budget -= r["upserted"] + r["retired"]
        return out

    # ---- the writes ---------------------------------------------------------------
    def _put_file(self, asker: str, root: Path, rel: str) -> None:
        parts = Path(rel).parts
        within = asker
        for i in range(len(parts) - 1):
            sub = root.joinpath(*parts[:i + 1])
            self._ensure(sub, "dir", within)
            within = self.eid(sub)
        self._ensure(root / rel, "file", within)

    def _ensure(self, path: Path, kind: str, within: str) -> None:
        eid = self.eid(path)
        want = {"path": str(Path(path).resolve()), "kind": kind, "within": within, "status": LIVE}
        d = (self.est.docs or {}).get(eid)
        if d is None:
            self.est.upsert(QuantumEntity(eid=eid, cls=CLASS_COMPONENT, properties={
                "name": self.key(path), **want, "parents": [FILE],
                "silent_birth": True, "born_ts": time.time()}), embedder=None)
            return
        p = dict(d.properties or {})
        ps = MultiParentOps._parents_of(p)
        if all(p.get(k) == v for k, v in want.items()) and FILE in ps:
            return
        p.update(want)
        p.pop("retired_ts", None)
        if FILE not in ps:
            p["parents"] = ps + [FILE]
        d.properties = p
        self.est.upsert(d, embedder=None)

    def _retire(self, eid: str) -> bool:
        return ST._retire(self.est, eid)
