"""#994 live switch: the goal graph runs on the field engine (`membrane_vm.Fields`) instead of its 12 triggers.

On by default whenever the native engine is built; `MEMBRANE_FIELDS=0` turns it off, and then (or without the
engine) nothing here runs and `goalgraph.seed` defines the triggers as before.

- **The mirror:** at start, every Goal, Task, Decision and Wait is loaded into the engine. A write to one of them
  (heard from the store's write listener) goes to the engine as an outside write; an entity the engine hasn't
  seen is loaded first.
- **Write back:** what the engine wrote (its getters, its setters, its receipts) is applied to the store with the
  writer `trigger:field:<setter>`. To the rest of the seat it's a machine write, like a trigger's: the adapters
  (worker pool, wait checks, replan) hear it, a replan written by it is not a send-back, Blurp's learner skips it,
  and the journal counts it as the VM's.
- **Effects:** a planner call goes to `membrane::llm.call`, which the LLM adapter runs.
- **Getters** (`packs/goalgraph.c1f`: `finished`, `open_blockers`, `due`) compute once at start, as a rebuild: the
  values are stored, no setter runs. After that they recalculate when what they read changes.
- **The clock:** the engine ticks on the VM's sweeps (the beat the old `every` trigger fired on), so armed waits
  come due (`Wait.due`); a seat without a VM ticks every `goalgraph.WAIT_TICK_S`.
- Writes the engine made are never fed back to it: they carry its writer and are skipped.
- **Packs:** cliffhanglets in `.c1f` files load at runtime: `membrane/packs/*.c1f`, then `Pack` entities in the field
  (`membrane::pack:<name>`, the text in `c1f`; a field pack replaces a file pack of the same name). Editing a Pack
  entity reloads it; a pack that doesn't parse is refused, the last good one stays, and the error is written on the
  entity (`error`); the text that last parsed is kept in `good_c1f`, and a seat that boots on a text that no
  longer parses loads it. Every class a loaded pack declares is mirrored, and new getters compute as a rebuild.
"""
from __future__ import annotations

import json
import re
import os
import queue
import sys
import threading
import time
from pathlib import Path

from . import writes as W
from .entities import MEMBRANE_NS

CLASSES = ("Goal", "Task", "Decision", "Wait")
# cliffhanglet packs (`.c1f`) loaded at runtime: files shipped in membrane/packs/, and Pack entities in the field
# (`membrane::pack:<name>`, the text in `c1f`), which replace a file pack of the same name and reload when edited
PACK_DIR = Path(__file__).resolve().parent / "packs"
PACK_CLASS = "Pack"
PACK_NS = MEMBRANE_NS + "pack:"


def is_pack(eid: str, d) -> bool:
    """A Pack entity, or any entity holding `c1f` text: an agent's entity tool may give it another class and id
    (`Component:pack:inflight`)."""
    return d.cls == PACK_CLASS or "c1f" in (d.properties or {})


def pack_name(eid: str, d) -> str:
    """Its `name`, else what follows the last `pack:` in its id, else the id."""
    p = d.properties or {}
    if p.get("name"):
        return str(p["name"])
    e = str(eid)
    return e.rsplit("pack:", 1)[1] if "pack:" in e else e
WRITER = "trigger:field:"
LLM_EID = MEMBRANE_NS + "llm"


# the running engine (the browser VM's snapshot route reads it: ui_serve /ui/fieldvm)
CURRENT = None


def enabled() -> bool:
    """On by default: the goal graph runs on the field. `MEMBRANE_FIELDS=0` is the kill switch, and a seat whose
    native engine isn't built (or predates `Fields.drain`) keeps the triggers, so the goal graph never goes dark."""
    if os.environ.get("MEMBRANE_FIELDS", "").strip() == "0":
        return False
    try:
        from . import membrane_vm as MV
    except Exception:
        return False
    return hasattr(getattr(MV, "Fields", None), "drain")


def _json(v) -> str:
    return json.dumps(v, default=str)


BANNER_CLASS = "Banner"                         # the default of a map seat: only instructions
RECALL_CLASS = "RecallBanner"                   # the default of the other seats: recall, open goals, decisions, the ledger
DEFAULT_CLASSES = (BANNER_CLASS, RECALL_CLASS)  # a class of the model's own is anything else
BANNER_NS = MEMBRANE_NS + "Banner:"
BANNER_TIMEOUT_S = 3.0                          # a banner script gets this long for its reads (operator 2026-11-11)
DIALS_EID = MEMBRANE_NS + "recall:dials"


def banner_eid(owner: str, chat: str) -> str:
    """The banner instance of one person in one chat (internal chats too)."""
    return f"{BANNER_NS}{owner or 'seat'}:{chat}"


STORE_EID = "Store:field"
SQL_ARRIVED = "\x00sql"                          # the queue item a finished query sends to the worker


def _tool_names() -> list:
    """The seat's own tool and method names (never a chat's focus): the banner's dial."""
    try:
        from . import entity_block as EB
        return sorted(str(t) for t in EB._tool_names())
    except Exception:                                      # noqa: BLE001
        return []


def mark_delivered(est, conv: str, seq) -> None:
    """This chat's turn reached the model: its banner instances' SINCE counts from here (`mark_seq`)."""
    if est is None or not conv or seq is None:
        return
    chat = conv[4:] if conv.startswith("sid:") else conv
    for eid, d in list((est.docs or {}).items()):
        if eid.startswith(BANNER_NS) and eid.endswith(":" + chat) and (d.properties or {}).get("mark_seq") != seq:
            d.properties = {**(d.properties or {}), "mark_seq": seq}
            est.upsert(d, embedder=None)


def _store_file(srv) -> str:
    """The seat's SQLite store file (the SQLite backend's database), or '' (another store: no sql())."""
    be = getattr(getattr(srv, "efield", None), "b", None)
    return str(getattr(getattr(be, "db", None), "file", "") or "")


class _Host:
    """The engine's host callbacks: the goal graph's setters bind by class, so none of these lookups is used.
    `call`: a Banner getter's built-ins (recall_banner.c1f_call), during a banner build only."""
    def __init__(self, live=None):
        self.live = live

    # the slow built-ins run on the drivers' threads, off the engine's lock (the getter reads None until they land)
    later = frozenset({"recall", "rerank"})

    def call(self, name, args_json, asker=None):
        if name == "pronoun":                         # a bare it / that / they in a c1f method (#1101): the context stack's
            from . import callctx                     # innermost binding, live at the moment the method runs
            try:
                return json.dumps(callctx.pronoun(self.live.srv, str(json.loads(args_json)[0])))
            except Exception as e:                    # noqa: BLE001
                return json.dumps(None)
        ctxs = getattr(self.live, "ctxs", None) or {}
        ctx = ctxs.get(str(asker).split("\0", 1)[0]) if asker else None
        if ctx is None and len(ctxs) == 1 and not asker:
            ctx = next(iter(ctxs.values()))           # a call in the round: the one build running
        if ctx is None:
            return None
        from . import recall_banner as RB
        out = RB.c1f_call(ctx, name, json.loads(args_json))
        return None if out is RB.NOT_MINE else json.dumps(out, default=float)

    def resolve(self, s): return None
    def resolve_exact(self, s): return None
    def dispatch(self, e, m): return None
    def vel(self, e, n, t): return False
    def align(self, e, t): return False
    def live(self, e, p): return None


class Live:
    def __init__(self, srv):
        from . import membrane_vm as MV
        self.srv, self.est = srv, srv.entity_store
        self.f = MV.Fields(_Host(self))
        self.ctxs: dict = {}                       # banner eid -> its build's context while its getters run
        self._banner_loaded: dict = {}
        self._engine_props: dict = {}               # banner eid -> the props its class's getters wrote (not the seat's, not the model's)              # banner eid -> (class, parents) the engine has it under
        self.cls_of: dict = {}                     # mirrored eid -> its class (a removal's event names it, #1054)
        self.store_path = _store_file(srv)
        if self.store_path:                        # the store driver: `sql(...)` in packs reads it, read-only
            try:
                self.f.set_store(self.store_path)
            except Exception as e:                 # noqa: BLE001 -- the field runs; sql() then says no store
                print(f"[fields] the store driver is off ({e})", file=sys.stderr)
                self.store_path = ""
        if not self.store_path:                    # the stdlib drivers (fs, proc) still run, in the seat's folder
            try:
                self.f.set_io(os.getcwd())
            except Exception:                      # noqa: BLE001
                pass
        self.q: queue.Queue = queue.Queue()
        self.busy = threading.Lock()
        self.stopped = threading.Event()
        self.errors = 0
        self.swept = False
        self.pack_eids: dict = {}
        self.pack_texts: dict = {}
        self.classes: set = set(CLASSES)
        self.file_mtimes: dict = {}
        self._reported: set = set()
        self._scan_files()
        for eid, d in list((self.est.docs or {}).items()):
            if is_pack(eid, d):
                self._pack_entity(eid, d)
        self._seed_store()
        self.loaded = self._mirror()
        self.f.rebuild(time.time())          # the getters compute once; their values are stored, no setter runs
        self._apply()

    def mirrored(self, d) -> bool:
        """An entity the engine sees: its class is a pack's, or a pack's class is one of its ISA kinds
        (`parents: ["cls:chatthing"]`: the kind's getters and setters apply to it, its own class unchanged)."""
        if d.cls in self.classes:
            return True
        ps = (d.properties or {}).get("parents")
        if not ps:
            return False
        kinds = {c.lower() for c in self.classes}
        return any(str(x).lower().split(":", 1)[-1] in kinds for x in (ps if isinstance(ps, list) else [ps]))

    def _seed_store(self) -> None:
        """The store pack's one entity (Store:field), so its getters (tables, counts) run on the seat's store."""
        if "Store" not in self.classes or not self.store_path or STORE_EID in (self.est.docs or {}):
            return
        from .entities import QuantumEntity
        with W.as_writer(WRITER + "store"):
            self.est.upsert(QuantumEntity(eid=STORE_EID, cls="Store", properties={
                "name": "the seat's store", "path": self.store_path, "counted": True}), embedder=None)

    # ---- packs ---------------------------------------------------------------------------------------
    def _pack(self, name: str, text: str, eid) -> bool:
        """Load or replace a pack. A broken one is refused (the last good one stays) and the error is written on
        its entity (`error`), or printed for a file pack."""
        # every pack's text goes to pages, active or waiting: a view pack waits here for `dom`, which pages have
        if self.pack_texts.get(name) != text:
            self.pack_texts[name] = text
            hub = getattr(getattr(self.srv, "_loop_runner", None), "hub", None)
            if hub is not None:
                try:
                    hub.publish({"type": "field.pack", "name": name, "c1f": text})
                except Exception:                  # noqa: BLE001
                    pass
        err = ""
        try:
            self.f.set_pack(name, text)
        except Exception as e:                    # noqa: BLE001
            err = str(e)
            if "isn't loaded" not in err and "isn't active" not in err:         # not a pack waiting for one that loads next
                print(f"[fields] {err}"[:300], file=sys.stderr)
        if eid:
            self.pack_eids[name] = eid
            d = (self.est.docs or {}).get(eid)
            props = (d.properties or {}) if d is not None else {}
            if not err and d is not None and props.get("good_c1f") != text:       # the text that parsed is kept: a seat that boots
                with W.as_writer(WRITER + "pack"):                                   # on a text that no longer does loads this
                    d.properties = {**props, "good_c1f": text}
                    self.est.upsert(d, embedder=None)
            elif err and "isn't loaded" not in err and "isn't active" not in err and props.get("good_c1f"):
                try:
                    self.f.set_pack(name, str(props["good_c1f"]))
                except Exception:                  # noqa: BLE001
                    pass
        # every Pack entity shows its pack's state: an import that arrived later clears a waiting pack's error
        waiting = json.loads(self.f.pack_errors())
        for n, e in list(self.pack_eids.items()):
            want = err if n == name and err else waiting.get(n, "")
            d = (self.est.docs or {}).get(e)
            if d is not None and (d.properties or {}).get("error", "") != want:
                with W.as_writer(WRITER + "pack"):
                    d.properties = {**(d.properties or {}), "error": want}
                    self.est.upsert(d, embedder=None)
        self.classes = set(CLASSES) | set(self.f.classes()) | self._view_classes()
        return not err

    def _view_classes(self) -> set:
        """The classes the view packs declare (packs importing `dom` wait on the seat and run in pages): their entities
        are mirrored too, so pages get them (the about pack's About:seat)."""
        out = set()
        for text in self.pack_texts.values():
            if not re.search(r"(?m)^IMPORT dom\b", text or ""):
                continue
            out |= {m for m in re.findall(r"(?m)^([A-Z][A-Za-z0-9_]*)\.[A-Za-z_]\w* (?:getter|on-setter)\b", text)
                    if m != "DomNode"}
        return out

    def pack_op(self, action: str, name: str = "", c1f: str = "") -> dict:
        """The `pack` tool (#1043): list | show | load a pack (read / write are the same), its load error in the reply. A written pack is the
        entity membrane::pack:<name> (class Pack), loaded now; a broken one is refused and the last good one stays.
        A shipped pack (membrane/packs) is the release's: a seat adds to its classes from its own pack instead."""
        shipped = {fp.stem for fp in PACK_DIR.glob("*.c1f")} if PACK_DIR.is_dir() else set()
        action = {"show": "read", "load": "write"}.get(action, action)
        with self.busy:
            waiting = json.loads(self.f.pack_errors())
            if action == "list":
                rows = []
                for n in sorted(set(self.pack_texts) | shipped):
                    e = self.pack_eids.get(n)
                    err = waiting.get(n) or (((self.est.docs or {}).get(e).properties or {}).get("error", "")
                                             if e and (self.est.docs or {}).get(e) is not None else "")
                    rows.append({"name": n, "source": "shipped" if n in shipped else "entity", "eid": e,
                                 "error": err or ""})
                return {"ok": True, "packs": rows}
            name = str(name or "").strip()
            if not name:
                return {"ok": False, "error": "a pack needs its name"}
            if action == "read":
                if name not in self.pack_texts:
                    return {"ok": False, "error": f"no pack {name!r} (pack action=list names them)"}
                e = self.pack_eids.get(name)
                d = (self.est.docs or {}).get(e) if e else None
                err = waiting.get(name) or (((d.properties or {}).get("error") or "") if d is not None else "")
                return {"ok": True, "name": name, "c1f": self.pack_texts[name], "error": err}
            if action != "write":
                return {"ok": False, "error": "action is list, show or load"}
            if name in shipped:
                return {"ok": False, "error": f"{name} is a shipped pack (the release's): write your own pack with "
                                              f"`IMPORT {name} 1` and your own class, e.g. My{name.capitalize()}"}
            from .entities import QuantumEntity
            eid = PACK_NS + name
            d = (self.est.docs or {}).get(eid)
            with W.as_writer(WRITER + "pack"):
                if d is None:
                    self.est.upsert(QuantumEntity(eid=eid, cls=PACK_CLASS, properties={"name": name, "c1f": c1f}),
                                    embedder=None)
                else:
                    d.properties = {**(d.properties or {}), "name": name, "c1f": c1f}
                    self.est.upsert(d, embedder=None)
            ok = self._pack(name, c1f, eid)
            if ok:
                self._mirror()
                self._apply()
            err = "" if ok else ((self.est.docs[eid].properties or {}).get("error") or "refused")
            if ok:
                err = json.loads(self.f.pack_errors()).get(name, "")      # loaded, but waiting on an IMPORT
            return {"ok": not err, "name": name, "eid": eid, "error": err, "classes": sorted(self.f.classes())}

    def _pack_entity(self, eid: str, d) -> bool:
        p = d.properties or {}
        name = pack_name(eid, d)
        return self._pack(name, str(p.get("c1f") or ""), eid)

    def _mirror(self) -> int:
        """Load every entity of a mirrored class the engine hasn't seen (after a pack adds a class too)."""
        n = 0
        for eid, d in list((self.est.docs or {}).items()):
            if self.mirrored(d):
                if not self.f.has(eid):
                    self.cls_of[eid] = d.cls
                    self.f.load(eid, d.cls, _json(dict(d.properties or {})))
                n += 1
        return n

    # ---- the browser VM's feed ------------------------------------------------------------------------
    def snapshot(self) -> dict:
        """What a page starts from: the loaded packs' text and every entity of a mirrored class."""
        ents = [{"eid": eid, "cls": d.cls, "props": dict(d.properties or {})}
                for eid, d in list((self.est.docs or {}).items()) if self.mirrored(d) and not is_pack(eid, d)]
        return {"packs": dict(self.pack_texts), "entities": ents}

    def publish(self, w: dict) -> None:
        """The store's write listener for pages: every write to a mirrored entity (the engine's own too) goes to
        the seat's /event hub as `field.write`; a birth carries the entity's fields. Cheap, never writes."""
        hub = getattr(getattr(self.srv, "_loop_runner", None), "hub", None)
        eid = w.get("eid")
        if hub is None or not eid or str(w.get("path") or "").startswith("rel:"):
            return
        d = (self.est.docs or {}).get(eid)
        prop = str(w.get("prop") or "")
        if d is None:
            # #1054: a removal (prop "", new None): the pages drop it too, when the engine held it
            if not prop and w.get("new") is None and eid in self.cls_of:
                cls = self.cls_of.get(eid) or ""
                try:
                    hub.publish({"type": "field.remove", "eid": eid, "cls": cls})
                except Exception:                  # noqa: BLE001
                    pass
            return
        if not self.mirrored(d) or is_pack(eid, d):
            return
        ev = ({"type": "field.write", "eid": eid, "cls": d.cls, "prop": prop, "new": w.get("new")} if prop
              else {"type": "field.write", "eid": eid, "cls": d.cls, "props": dict(d.properties or {})})
        try:
            hub.publish(ev)
        except Exception:                          # noqa: BLE001 -- a page's feed never stops a write
            pass

    # ---- hearing the store ---------------------------------------------------------------------------
    def hear(self, w: dict) -> None:
        """The store's write listener: cheap, never writes, only queues."""
        writer = str(w.get("writer") or "")
        if writer.startswith(WRITER):
            return                                   # the engine's own write coming back
        eid = w.get("eid")
        if not eid or str(w.get("path") or "").startswith("rel:"):
            return
        d = (self.est.docs or {}).get(eid)
        if d is None:
            if not w.get("prop") and w.get("new") is None and eid in self.cls_of:
                self.q.put((eid, "", None, time.time(), writer))       # #1054: removed -> it leaves the engine
            return
        if not self.mirrored(d) and not is_pack(eid, d):
            return
        if not is_pack(eid, d):
            self.cls_of[eid] = d.cls                 # its class outlives it (a removal's event names it)
        self.q.put((eid, str(w.get("prop") or ""), w.get("new"), time.time(), writer))

    def _one(self, eid: str, prop: str, new, now: float, writer: str = "py") -> None:
        if eid == SQL_ARRIVED:                       # a getter's query finished: the getters waiting for it recompute
            self.f.settle(now)
            self._apply()
            return
        d = (self.est.docs or {}).get(eid)
        if d is None:
            if not prop and self.f.has(eid):         # #1054: removed from the store -> it leaves the engine
                self.f.remove(eid, now)
                self._apply()
            self.cls_of.pop(eid, None)
            return
        if is_pack(eid, d):                          # a pack written: reload it, mirror any new class, rebuild
            if prop in ("", "c1f", "name") and self._pack_entity(eid, d):
                self._mirror()
                self.f.rebuild(now)                  # a new getter on an old world: computed, no setter runs
                self._apply()
            return
        if not prop or not self.f.has(eid):          # a birth, or one the mirror hasn't seen: load its state
            self.f.load(eid, d.cls, _json(dict(d.properties or {})))
            if not prop:
                self.f.settle(now)                   # its getters compute; what references it recalculates
                self._apply()
                return
        if prop == "position":
            return
        self.f.write(eid, prop, _json(new), now, writer or "py")     # who wrote it: entity scripts never run effects
        self._apply()

    def _apply(self) -> dict:
        out = json.loads(self.f.drain())
        for w in out.get("writes") or []:
            if "born" in w:                          # a setter created it (`<Class>:<key>` of its own or imported class)
                if w["eid"] not in (self.est.docs or {}):
                    from .entities import QuantumEntity
                    with W.as_writer(WRITER + "create"):
                        self.est.upsert(QuantumEntity(eid=w["eid"], cls=w["born"], properties={"silent_birth": True}), embedder=None)
                continue
            d = (self.est.docs or {}).get(w["eid"])
            if d is None or (d.properties or {}).get(w["prop"]) == w["new"]:
                continue
            if str(w.get("writer") or "").startswith("field:") and w["eid"].startswith(BANNER_NS):
                self._engine_props.setdefault(w["eid"], set()).add(w["prop"])   # what a banner's class computed
            with W.as_writer(WRITER + str(w.get("writer") or "").removeprefix("field:")):
                props = {**(d.properties or {}), w["prop"]: w["new"]}
                if w["new"] is None:                 # a write of none removes the property (as a getter's none leaves it absent)
                    props.pop(w["prop"], None)
                d.properties = props
                self.est.upsert(d, embedder=None)
        for c in out.get("calls") or []:
            llm = (self.est.docs or {}).get(LLM_EID)
            if llm is None:
                continue
            with W.as_writer(WRITER + "plan"):
                llm.properties = {**(llm.properties or {}), "call": c}
                self.est.upsert(llm, embedder=None)
        return out

    # ---- the banner (#994): a Banner pack's `lines` for one person in one chat -----------------------
    def has_banner(self) -> bool:
        return BANNER_CLASS in self.classes

    def _banner_instance(self, eid: str, owner: str, chat: str, facts: dict | None = None):
        d = (self.est.docs or {}).get(eid)
        want = BANNER_CLASS if (facts or {}).get("map") else RECALL_CLASS
        if d is not None:
            if d.cls in DEFAULT_CLASSES and d.cls != want:     # a default made for the other kind of seat (before the split)
                with W.as_writer(WRITER + "banner"):
                    d.cls = want
                    self.est.upsert(d, embedder=None)
            return d
        from . import recall_banner as RB
        from .entities import QuantumEntity
        dials = (self.est.docs or {}).get(DIALS_EID)
        props = {k: v for k, v in ((dials.properties if dials is not None else None) or {}).items()
                 if k not in ("name", "summary", "parents", "born", "silent_birth", "refused", "error")}
        props.update({"name": f"banner: {owner} in {chat}", "owner": owner, "chat": chat,
                      "pool": props.get("pool", RB.POOL), "n": props.get("n", RB.BANNER_N), "silent_birth": True})
        props.update(facts or {})                      # the seat's facts are there from the first compute (a map seat's heavy reads never run)
        with W.as_writer(WRITER + "banner"):
            self.est.upsert(QuantumEntity(eid=eid, cls=want, properties=props), embedder=None)
        return (self.est.docs or {}).get(eid)

    def _banner_dials(self, d) -> dict:
        """The layout's dials an instance lacks (today's values, conv_banner's); an instance keeps what it has."""
        from . import conv_banner as CB
        from . import recall_banner as RB
        led = str(getattr(getattr(self.srv, "user_ledger", None), "path", "") or "")
        want = {"budget": CB.BANNER_CHAR_BUDGET, "open_max": CB.OPEN_MAX, "decided_max": CB.DECIDED_MAX,
                "news_chars": CB.NEWS_CHARS, "conv_header": CB.HEADER, "paused_line": CB.PAUSED_LINE,
                "suggest_head": CB.SUGGEST_HEAD, "more_line": CB.MORE_LINE, "recall_header": RB.HEADER,
                "ledger_file": os.path.basename(led) if led.endswith(".jsonl") else None,
                "window": CB.WINDOW_MESSAGES, "focus_k": CB.FOCUS_K, "focus_min_events": CB.FOCUS_MIN_EVENTS,
                "working_chars": CB.WORKING_ON_CHARS, "news_max": CB.NEWS_MAX, "stable_min_convs": CB.STABLE_MIN_CONVS,
                "stable_min_days": CB.STABLE_MIN_DAYS, "stable_max": CB.STABLE_MAX, "tool_names": _tool_names()}
        have = d.properties or {}
        return {k: v for k, v in want.items() if k not in have}

    def banner(self, field, owner: str, chat: str, session: str, text: str, now: float | None = None,
               facts: dict | None = None, default: bool = False) -> dict:
        """Run this chat's banner for the message: {eid, lines, text, error, state}. The pack decides what a message
        recalls and lays out the block (`text`); `facts` are the seat's facts for this turn (paused, alerts, tools, and
        Blurp's blurp / suggest when it is on), written as plain fields like the ask. A
        getter's error is the banner's error (shown, never replaced). The reads the pack asked for (sql, jsonl) are
        waited for here, outside the engine's lock and the GIL. `state` is None when nothing was recalled."""
        now = time.time() if now is None else now
        eid = banner_eid(owner, chat) + (":default" if default else "")   # the default's own instance: the fallback
        writes: list = []
        ctx = {"field": field, "session": session, "now": now, "rows": {}, "timing": {}}
        with self.busy:
            d = self._banner_instance(eid, owner, chat, facts)
            dials = self._banner_dials(d)
            if dials:
                with W.as_writer(WRITER + "banner"):
                    d.properties = {**(d.properties or {}), **dials}
                    self.est.upsert(d, embedder=None)
                if self.f.has(eid):
                    for k, v in dials.items():
                        self.f.write(eid, k, _json(v), now)
            key = (d.cls, tuple((d.properties or {}).get("parents") or []))
            switched = self.f.has(eid) and self._banner_loaded.get(eid) != key   # or loaded before this process knew
            if switched:
                self.f.remove(eid, now)                # the model switched the class: reload it under the new one
                self.f.settle(now)
            if switched:                               # the old class's computed values are not the new class's answer
                old = self._engine_props.pop(eid, set())
                if old & set(d.properties or {}):
                    with W.as_writer(WRITER + "banner"):
                        d.properties = {k: v for k, v in (d.properties or {}).items() if k not in old}
                        self.est.upsert(d, embedder=None)
            if not self.f.has(eid):
                self.f.load(eid, d.cls, _json(dict(d.properties or {})))
                if switched:
                    self.f.rebuild(now)                # the new class's getters compute afresh
                self.f.settle(now)
                self._apply()
            self._banner_loaded[eid] = key
            self.ctxs[eid] = ctx
            self.f.sql_forget(eid)                     # this build's recall starts fresh (the same words recall again)
            if d.cls not in DEFAULT_CLASSES:
                self.f.refresh(eid, now)               # a model's own class: every getter reads the store again (one over constants is never dirty)
        try:
            with self.busy:
                cur0 = json.loads(self.f.get(eid)) or {}
                if cur0.get("ask") == text:
                    self.f.write(eid, "ask", "null", now)      # the same words again: recall again
                # only what changed is written, the ask last: a write re-runs what read it, with the ask it sees
                for p, v in (("session", session), *sorted((facts or {}).items()), ("ask", text)):
                    if p != "ask" and cur0.get(p) == v:
                        continue
                    with W.as_writer(WRITER + "banner"):
                        d.properties = {**(d.properties or {}), p: v}
                        self.est.upsert(d, embedder=None)
                    self.f.write(eid, p, _json(v), now)
                writes += self._apply().get("writes") or []
            # the pack's reads (sql, jsonl, recall, rerank): wait for each WITHOUT the field's lock (a slow one never
            # stalls the thinker or another chat), and settle after the last one too (its answer lands on the round
            # after it finishes); a round may ask for more (a read that needs another's answer)
            custom = d.cls not in DEFAULT_CLASSES         # the limit is the model's own script's; the default has none
            deadline, late = time.monotonic() + BANNER_TIMEOUT_S, False
            while self.f.sql_pending():               # running, or finished and not landed yet
                left = deadline - time.monotonic()
                if custom and left <= 0:
                    late = True                       # the script is over its time: what it asked for lands later, unread
                    break
                self.f.sql_wait(left if custom else None)
                with self.busy:
                    self.f.settle(now)
                    writes += self._apply().get("writes") or []
            with self.busy:
                refused = next((w.get("new") for w in reversed(writes)
                                if w.get("eid") == eid and w.get("prop") == "refused"), None)
                if late and not refused:
                    refused = f"the banner script took longer than {BANNER_TIMEOUT_S:g} s"
                cur = json.loads(self.f.get(eid)) or {}
        finally:
            self.ctxs.pop(eid, None)
        lines = [str(x) for x in (cur.get("lines") or []) if x is not None] if not refused else []
        from . import recall_banner as RB
        return {"eid": eid, "lines": lines, "text": None if refused else cur.get("text"), "error": refused,
                "state": None if refused else RB.c1f_state(ctx, lines)}

    # ---- running -------------------------------------------------------------------------------------
    def settle(self) -> None:
        """Process everything queued now (tests; the worker does the same)."""
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            self._run_one(item)

    def _run_one(self, item) -> None:
        with self.busy:
            try:
                self._one(*item)
            except Exception as e:                   # noqa: BLE001 -- one bad write never stops the goal graph
                self.errors += 1
                print(f"[fields] write {item[0]}.{item[1]} failed: {e!r}"[:300], file=sys.stderr)

    def tick(self, now: float | None = None) -> None:
        with self.busy:
            try:
                self.f.tick(now or time.time())
                self._apply()
            except Exception as e:                   # noqa: BLE001
                self.errors += 1
                print(f"[fields] tick failed: {e!r}"[:300], file=sys.stderr)

    def _scan_files(self) -> bool:
        """Load the pack files that are new or changed since the last scan (their mtime). -> any loaded."""
        changed = False
        for fp in sorted(PACK_DIR.glob("*.c1f")) if PACK_DIR.is_dir() else []:
            try:
                m = fp.stat().st_mtime
            except OSError:
                continue
            if self.file_mtimes.get(fp.name) == m:
                continue
            self.file_mtimes[fp.name] = m
            if self._pack(fp.stem, fp.read_text(), None):
                changed = True
        for n, why in json.loads(self.f.pack_errors() or "{}").items():      # what is still waiting after the files loaded
            if re.search(r"(?m)^IMPORT dom\b", self.pack_texts.get(n) or ""):
                continue                          # a view pack waits for `dom`, which only pages have: not an error here
            if why and (n, why) not in self._reported:
                self._reported.add((n, why))
                print(f"[fields] {why}"[:300], file=sys.stderr)
        return changed

    def on_sweep(self, now: float) -> None:
        """The VM's sweep is the clock (the beat the old `every` trigger fired on): the queue first, then a tick.
        Each beat also reloads pack files edited since the last one."""
        self.settle()
        with self.busy:
            if self._scan_files():
                self._mirror()
                self.f.rebuild(now)
                self._apply()
        self.tick(now)

    def _sql_waiter(self) -> None:
        """The store driver's results: wait (no engine lock, no GIL) until a getter's query finishes, then let the
        worker settle the getters waiting for it. A query never runs under the engine's lock, so it can't hang it."""
        while not self.stopped.is_set():
            if self.f.sql_wait(1.0):
                self.q.put((SQL_ARRIVED, "", None, time.time()))

    def _worker(self) -> None:
        from . import goalgraph as GG
        next_tick = time.time() + GG.WAIT_TICK_S
        while not self.stopped.is_set():
            try:
                item = self.q.get(timeout=max(0.05, next_tick - time.time()))
                self._run_one(item)
            except queue.Empty:
                pass
            if time.time() >= next_tick:
                if not self.swept:                   # no VM sweeping: keep our own beat
                    self.tick()
                with self.busy:                      # pack files reload on this timer too (sweeps stop while the thinker is paused)
                    if self._scan_files():
                        self._mirror()
                        self.f.rebuild(time.time())
                        self._apply()
                next_tick = time.time() + GG.WAIT_TICK_S


def seat_facts(env, settings, srv, known=None) -> dict:
    """The facts packs read from Boot:seat, assembled in the launcher (the one place that may): `env` (the environment), `settings`
    (the person's settings file), `srv` (the seat: backend, router, the minds) and `known` (what the launcher resolved: ui_dir,
    build_id, seat_version, seat_cwd, seat_home, seat_project). A source that is missing leaves its fact out (never None); the
    JWT secret is only ever a PATH."""
    out = {k: str(v) for k, v in (known or {}).items() if v}
    port = str(env.get("MEMBRANE_DOOR_PORT") or "").strip()
    if port.isdigit() and int(port) > 0:
        out["door_port"] = int(port)                   # absent unless asked: no door starts by default
    if env.get("MEMBRANE_JWT", "").strip() != "0" and "MEMBRANE_JWT_SECRET" not in env:
        sf = env.get("MEMBRANE_JWT_SECRET_FILE") or (os.path.join(out["seat_cwd"], ".membrane", "jwt.secret") if out.get("seat_cwd") else "")
        if sf:
            out["door_secret_file"] = sf
    out["seat_name"] = str(settings.get("name") or "") or "Bob"
    out["seat_named"] = bool(settings.get("name"))
    out["seat_aliases"] = list(settings.get("aliases") or [])
    if str(settings.get("stt") or "").startswith("http"):
        out["seat_stt"] = str(settings["stt"])
    out["seat_voice_out"] = bool(settings.get("voice_out"))
    out["seat_language"] = settings.get("language") or "en"
    out["seat_blurp"] = str(env.get("MEMBRANE_BLURP", "0")).strip() not in ("0", "", "off", "false", "no")
    if getattr(srv, "backend", None):
        out["seat_backend"] = str(srv.backend).rstrip("/")
    router = getattr(srv, "router", None)
    out["router_on"] = router is not None
    rows = [{"name": r.name, "backend": r.backend} for r in (getattr(router, "records", None) or {}).values()
            if getattr(r, "backend", "") and r.kind == "cloud"]
    if rows:
        out["cloud_models"] = rows
    from . import minds_models as MM
    out["minds"] = MM.read(srv)
    return out


def boot_started(est, ledger: str | None = None, facts: dict | None = None, announce: bool = True) -> None:
    """The seat tells the field it has started: Boot:seat.started = now (data only, as About:seat is), with the facts packs
    need (`ledger`: the operator ledger's file; `facts`: seat_facts). Each fact is a property of Boot:seat and the whole set is the
    dict `facts`. `announce=False` only adds facts (the router's arrive after the loops start) and does not say `started` again.
    Packs answer with setters on Boot.started (the dev pack makes the dev Commands, the decisions pack its nodes)."""
    from .entities import QuantumEntity
    d = (est.docs or {}).get("Boot:seat")
    if d is None:
        est.upsert(QuantumEntity(eid="Boot:seat", cls="Boot", properties={"name": "this seat's start", "silent_birth": True}),
                   embedder=None)
        d = est.docs["Boot:seat"]
    known = dict(facts or {})
    if ledger:
        known["ledger"] = str(Path(ledger).resolve())      # absolute: the file driver resolves a relative path against the data folder
    for k, v in known.items():              # the facts first, the announcement last: a setter on `started` must find them
        d.properties = {**(d.properties or {}), k: v}
        est.upsert(d, embedder=None)
    whole = {**((d.properties or {}).get("facts") or {}), **known}
    d.properties = {**(d.properties or {}), "facts": whole}
    est.upsert(d, embedder=None)
    if announce:
        d.properties = {**(d.properties or {}), "started": time.time()}
        est.upsert(d, embedder=None)


def answer_card(srv, card: str, request: dict, timeout: float = 15.0) -> dict:
    """The seat's door for a person's own answer to a Decision card (the board, the chat's "N. value"): write
    `Decision.answer` and wait for the decisions pack's receipt (`answer_result`). A stopgap until the serve routes are
    entities: the decision itself is the pack's. -> {ok, value | error[, conflict], card}"""
    est = getattr(srv, "entity_store", None)
    ent = est.resolve(str(card or ""), exact=True) if est is not None else None
    if ent is None or ent.cls != "Decision":
        return {"ok": False, "error": f"no Decision card {card!r}"}
    if getattr(srv, "fields_live", None) is None:
        return {"ok": False, "error": "the field engine is off on this seat: cards are answered by the decisions pack"}
    live = srv.fields_live
    rid = f"a{time.time_ns()}-{threading.get_ident()}"
    with live.busy:                       # the engine's own lock: its write-backs are read-modify-write on the same properties
        ent = est.docs.get(ent.eid) or ent
        with W.as_writer("field:answer"):
            ent.properties = {**(ent.properties or {}), "answer": {**dict(request), "id": rid}}
            est.upsert(ent, embedder=None)
    t = time.time() + timeout
    while time.time() < t:
        for r in (est.docs.get(ent.eid).properties or {}).get("answer_log") or []:
            if isinstance(r, dict) and r.get("id") == rid:
                from . import decision_map as DM
                return {**r, "card": DM._card_of(est.docs[ent.eid])}
        time.sleep(0.02)
    return {"ok": False, "error": "the decisions pack did not answer in time (is the field engine running?)"}


real_answer_card = answer_card          # (the name a test that swaps the door cannot reach)


def start(srv, thread: bool = True) -> Live | None:
    """Turn the goal graph's field engine on (the default; MEMBRANE_FIELDS=0 is off). Returns None when off."""
    if not enabled():
        return None
    global CURRENT
    live = Live(srv)
    srv.fields_live = live
    CURRENT = live
    W.on_write(live.hear)
    W.on_write(live.publish)
    vm = getattr(srv, "ch_vm", None)
    if vm is not None and hasattr(vm, "on_sweep"):   # the clock ticks on the VM's sweeps, chained after the journal's
        prev = vm.on_sweep
        def _sweep(now, _prev=prev, _live=live):
            if _prev is not None:
                _prev(now)
            _live.on_sweep(now)
        vm.on_sweep = _sweep
        live.swept = True
    if thread:
        threading.Thread(target=live._worker, daemon=True, name="fields-live").start()
        threading.Thread(target=live._sql_waiter, daemon=True, name="fields-io").start()   # reads and effects land
    print(f"[fields] the goal graph runs on the field engine: {live.loaded} entities loaded", file=sys.stderr)
    return live


def stop(srv) -> None:
    global CURRENT
    live = getattr(srv, "fields_live", None)
    if live is not None:
        W.off_write(live.hear)
        W.off_write(live.publish)
        if CURRENT is live:
            CURRENT = None
        live.stopped.set()
        try:
            live.f.sql_interrupt()                   # running queries stop with the seat
        except Exception:                            # noqa: BLE001
            pass
        srv.fields_live = None
