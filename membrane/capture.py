"""THE CAPTURER: the proxy hooks' one door into the entity field.

The chat path hands raw turn material here (user text, model prose, tool
calls with their FULL arguments, tool results before any clipping,
reasoning) and returns at once: a single worker thread splits it into
events (membrane/streams.py), writes them (idempotent), and -- in mode
'on' -- embeds and applies them. Nothing here ever raises into the chat
path or blocks it; a full queue drops the oldest DISCOURSE, never an
observation.

Mode (MEMBRANE_EFIELD): on (write + embed + apply; the default) | shadow
(write events only) | off -- the switch survives as a lab/ablation
override only."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time

from . import streams as ST

SESSION_GAP_S = 30 * 60


def mode_from_env() -> str:
    m = os.environ.get("MEMBRANE_EFIELD", "on").strip().lower()
    return m if m in ("off", "shadow", "on") else "off"


class LazyEmbedder:
    """The seat's embedder, looked up at call time (the seat resolves its
    embedder after boot through the ladder)."""

    def __init__(self, mem):
        self.mem = mem

    @property
    def model(self) -> str:
        return getattr(getattr(self.mem, "embedder", None), "model", "?") or "?"

    def embed(self, texts):
        emb = getattr(self.mem, "embedder", None)
        if emb is None:
            raise RuntimeError("no embedder resolved yet")
        return emb.embed(texts)


def _served(evs: list, query: str) -> list:
    """Tool events carry the user message they served."""
    q = ST.clean(query)
    for e in evs:
        e.served_text = q
    return evs


class Sessions:
    """'s:<boot>:<n>' -- n advances after an idle gap (a heuristic until
    the client sends a session id)."""

    def __init__(self, boot: float | None = None, gap_s: float = SESSION_GAP_S):
        self.boot = int(boot or time.time())
        self.gap = gap_s
        self.n = 0
        self.last = None

    def current(self, now: float | None = None) -> str:
        now = now or time.time()
        if self.last is not None and now - self.last > self.gap:
            self.n += 1
        self.last = now
        return f"s:{self.boot}:{self.n}"


class Capturer:
    def __init__(self, field, mode: str | None = None, sessions: Sessions | None = None,
                 maxsize: int = 2048, start: bool = True):
        self.field = field
        self.mode = mode or mode_from_env()
        self.sessions = sessions or Sessions()
        self.q: queue.Queue = queue.Queue(maxsize=maxsize)
        self.errors = 0
        self.idle_hooks: list = []
        self._lock = threading.Lock()
        self._busy = 0
        self._t = None
        if start and self.mode != "off":
            self._t = threading.Thread(target=self._run, name="efield-capture", daemon=True)
            self._t.start()

    # ---- the hooks (all non-blocking, all swallow) ----------------------------------
    def on_turn(self, query: str, prose: str = "", thought: str = "",
                ts: float | None = None) -> None:
        """The user's message + the model's prose + its reasoning. Tool calls
        ride on_calls (their full arguments), results on_tool_result."""
        self._put(lambda: ST.split_turn(query, prose, None, thought, ts=ts), discourse=True)

    def on_calls(self, query: str, calls: dict, ts: float | None = None) -> None:
        self._put(lambda: _served(ST.split_turn("", "", calls, ts=ts), query))

    def on_tool_result(self, call_id: str, name: str, args, result: str,
                       query: str = "", ts: float | None = None) -> None:
        self._put(lambda: _served(ST.split_turn(
            "", results=[(call_id, name, args, result)], ts=ts), query))

    def on_code(self, graph: dict, rels) -> None:
        """Definitions in the re-scanned files -> CODE events; older code
        events of those files are superseded once the new ones land. The
        events are built HERE (the graph is only read on this thread)."""
        from . import code_evidence as CE
        rels = list(rels or [])
        try:
            evs = CE.code_events(graph, rels)
        except Exception:
            self.errors += 1
            return

        def after(keys):
            CE.supersede(self.field, rels, keys)
            CE.store_relations(self.field, graph, rels)
        self._put(lambda: (evs, after))

    def on_events(self, evs: list) -> None:
        """Already-built events (code evidence, notes, ingest)."""
        self._put(lambda: list(evs))

    # ---- the worker ------------------------------------------------------------------
    def _put(self, build, discourse: bool = False) -> None:
        if self.mode == "off":
            return
        try:
            item = (build, self.sessions.current())
            try:
                self.q.put_nowait(item)
            except queue.Full:
                if discourse:
                    return                      # drop discourse, never observations
                try:
                    self.q.get_nowait()
                    self.q.task_done()
                except queue.Empty:
                    pass
                self.q.put_nowait(item)
        except Exception:
            self.errors += 1

    def _step(self, build, session: str) -> None:
        evs = build()
        after = None
        if isinstance(evs, tuple):
            evs, after = evs
        if not evs and after is None:
            return
        keys = self.field.write_events(evs, session=session) if evs else []
        if after is not None:
            after(keys)
        if self.mode == "on":
            self.field.process()

    IDLE_S = 30.0

    def _run(self) -> None:
        while True:
            try:
                build, session = self.q.get(timeout=self.IDLE_S)
            except queue.Empty:
                self._idle()
                continue
            try:
                self._step(build, session)
            except Exception as e:                 # never into the chat path
                self.errors += 1
                if self.errors <= 3 or self.errors % 100 == 0:
                    print(f"[efield] capture error #{self.errors}: {e}", file=sys.stderr)
            finally:
                self.q.task_done()

    def _idle(self) -> None:
        """The queue stayed empty for IDLE_S: the budgeted background work
        (the claim judge, the A/B judge). Each hook swallows its own errors."""
        for fn in list(self.idle_hooks):
            try:
                fn()
            except Exception as e:
                self.errors += 1
                if self.errors <= 3 or self.errors % 100 == 0:
                    print(f"[efield] idle hook error: {e}", file=sys.stderr)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until the queue drains (tests, shutdown)."""
        if self._t is None:
            while not self.q.empty():
                build, session = self.q.get()
                try:
                    self._step(build, session)
                except Exception:
                    self.errors += 1
                finally:
                    self.q.task_done()
            return True
        end = time.time() + timeout
        while self.q.unfinished_tasks and time.time() < end:
            time.sleep(0.01)
        return not self.q.unfinished_tasks
