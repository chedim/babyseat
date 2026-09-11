"""THE THINKER: the fast clock -- background deliberation, separate from
the dreamer (the operator's design: 'thinking is different from dreaming').

THE DISTINCTION (the organizing principle):
  - the DREAMER generates: new timeline branches out of silence, steered by
    open intentions, judged later by reality's vote. Sleep.
  - the THINKER digests: the last few stimuli chewed until understood or
    answered, and -- when traffic is gone -- the quarantined branches
    EXTENDED (what could happen next). Rumination.

THE WORKING-MEMORY COVENANT: the think queue is a bounded deque (5 items,
drop-oldest) -- ATTENTION, not a todo list. What is 'on the mind' becomes
inspectable (/membrane/state renders it) instead of implicit.

THE TWO BEHAVIORS, ONE LOOP:
  - messages flowing -> DIGEST: each recent stimulus (a capture, a vote
    result, a night finding) gets one deliberation pass: entities touched,
    fn stamps, sim surprises -> maybe speak (planner-gated: the ActTable
    decides; stay-silent is the learned default) or an entity-track note.
  - silence -> THINK AHEAD: pull an open intention or a quarantined branch
    and EXTEND it (the token walk -- Phase T's typed table, fed by the fn
    bridge) -> commit as steps[] on the branch, or a new child branch;
    optionally speak a draft (act: propose-draft, review-gated).

THE AUTHORSHIP COVENANT (the operator's ruling): the thinker may SPEAK on
its own (both delivery channels, planner-gated, reinforcement-trained) but
never initiates a model turn or a tool call. Membrane proposes; the
operator acts. The closest honest 'initiating a conversation' is the
ask-operator act -- an explicit question the operator answers on the next
turn (the review-ruling resolver matches it).

THE COST COVENANT: $0 by default (numpy, entity ops, the token walk). At
most ONE cheap model call per item, only when the item clears the
novelty bar, on the idle-cortex model (_model_chat(srv)) -- hard-capped
per minute (THINK_MODEL_CAP). Never on the turn path.
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque

# THE CONSTANT HUM (the operator's call): the thinker is never off -- a
# 1s heartbeat tick, continuously chewing what it can, throttled by the
# DUTY-CYCLE CONTROLLER (the compactor's, retargeted): measured think work
# over the trailing window is held to THINK_CPU_TARGET (~3-5% of one core
# by design, not by clock). Work earns sleep: a heavy chew (a model call,
# a long walk) stretches the next tick until the window's duty is back
# under the target; an idle mind hums cheap no-ops (checks only).
THINK_TICK_S = 1.0
THINK_CPU_TARGET = 0.04      # the duty ceiling: think work / (think + window)
THINK_IDLE_S = 60.0          # the max stretch when the duty is way over
QUEUE_MAX = 5                # the 3-7 working memory (the covenant's 5)
THINK_MODEL_CAP_PER_MIN = 4  # the cost dial: model calls per minute, max
NOVELTY_SPEAK_BAR = 0.85     # a digest speaks only above this sim surprise
WALK_MAX_STEPS = 14          # one extension's token budget (cheap, typed)


class Thinker:
    """One seat's rumination thread."""

    def __init__(self, srv) -> None:
        self.srv = srv
        self.queue: deque = deque(maxlen=QUEUE_MAX)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run,
                                        name="membrane-thinker", daemon=True)
        self._model_ts: list[float] = []   # the cost dial's window
        self._ema_work: float = 0.0        # the duty controller's measure
        self.stats = {"processed": 0, "spoken": 0, "extended": 0,
                      "skipped": 0, "model_calls": 0}

    # ---- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout=join_timeout)

    def enqueue_chat(self, text: str) -> None:
        """The operator's chat turn, on the mind (the receipt lane's
        retirement voice): the thinker digests what the operator is
        saying -- questions, ok's, confusion -- and its speech goes
        through the planner gate. $0: enqueue only."""
        self.enqueue("chat", {"text": str(text)[:400]})

    def enqueue(self, kind: str, payload: dict) -> None:
        """One stimulus on the mind. Drop-oldest when full (the 3-7
        covenant: attention, not a backlog)."""
        try:
            self.queue.append({"kind": str(kind)[:40],
                               "payload": dict(payload or {}),
                               "ts": time.time()})
        except Exception:
            pass

    # ---- the loop ------------------------------------------------------------
    def _run(self) -> None:
        """The constant hum: a 1s heartbeat, every tick one SMALL unit of
        thought under the duty controller -- measured work over the
        trailing window held to THINK_CPU_TARGET (~3-5% of one core)."""
        while not self._stop.is_set():
            try:
                slept = self._hum()
            except Exception:
                traceback.print_exc()
                slept = THINK_IDLE_S
            if self._stop.wait(slept):
                break

    def _hum(self) -> float:
        """One heartbeat: at most one unit of thought (a digest item OR
        one think-ahead probe), then the duty controller sets the next
        sleep. The idle mind still hums (the queue drains to nothing,
        then think-ahead keeps the organ warm -- cheap no-op when the
        budget says so)."""
        t0 = time.time()
        did_work = False
        if self.queue:
            item = self.queue.popleft()
            try:
                self._process_one(item)
            except Exception:
                traceback.print_exc()
            self.stats["processed"] += 1
            did_work = True
        else:
            # the idle half, throttled by the SAME duty controller: a
            # think-ahead probe only when the recent duty allows it
            if self._duty() < THINK_CPU_TARGET:
                self._idle_think()
                did_work = True
        work = time.time() - t0
        self._ema_work = 0.7 * self._ema_work + 0.3 * work
        # the duty controller: work / (work + window) vs the target -- a
        # heavy chew stretches the next sleeps until the window's duty
        # falls back under ~3-5%
        window = max(1e-6, 60.0)
        duty = self._ema_work / max(self._ema_work + window, 1e-6)
        if duty > THINK_CPU_TARGET:
            over = min(59.0, duty / max(THINK_CPU_TARGET, 1e-6) - 1.0)
            return max(THINK_TICK_S, THINK_IDLE_S * (1.0 + over) / 60.0)
        return THINK_TICK_S

    def _duty(self) -> float:
        """The current think duty: measured work over (work + the 60s
        window) -- the controller's read, ~3-5% at the target."""
        return self._ema_work / max(self._ema_work + 60.0, 1e-6)

    # ---- the model-call cost dial -------------------------------------------
    def _model_budget_ok(self) -> bool:
        now = time.time()
        self._model_ts = [t for t in self._model_ts
                          if now - t < 60.0]
        return len(self._model_ts) < THINK_MODEL_CAP_PER_MIN

    def _model_call(self, messages: list) -> str:
        """One cheap deliberation call, under the per-minute cap. Uses the
        seat's model-aware chat (the idle-cortex rung; the same wire the
        compactor rides). Empty on any failure (degrade, never raise)."""
        if not self._model_budget_ok():
            return ""
        try:
            from .rag import _model_chat
            srv = self.srv
            out = _model_chat(srv)(
                getattr(srv, "backend", ""),
                getattr(srv, "idle_cortex_model", "") or "",
                messages, timeout=45)
            self._model_ts.append(time.time())
            self.stats["model_calls"] += 1
            return out or ""
        except Exception:
            return ""

    # ---- the fed item: DIGEST -------------------------------------------------
    def _process_one(self, item: dict) -> None:
        kind = item.get("kind")
        payload = item.get("payload") or {}
        srv = self.srv
        est = getattr(srv, "entity_store", None)
        if est is None:
            return
        if kind == "capture":
            self._digest_capture(payload, est)
        elif kind == "dream-outcome":
            self._digest_dream(payload, est)
        elif kind == "night":
            self._digest_night(payload, est)
        elif kind == "chat":
            self._digest_chat(payload, est)

    def _digest_capture(self, payload: dict, est) -> None:
        """One real capture, chewed: which entities did it touch, does the
        pattern deserve a note. Speech is planner-gated (the ActTable
        decides; stay-silent holds unless the table has learned this
        context speaks)."""
        label = str(payload.get("label") or "")
        situation = str(payload.get("situation") or "")[:180]
        idx = payload.get("idx")
        exs = getattr(self.srv.membrane.store, "exemplars", [])
        ex = (exs[idx] if isinstance(idx, int) and 0 <= idx < len(exs)
              else None)
        tags = list((getattr(ex, "extra", {}) or {}).get("entities") or [])
        sim = payload.get("sim")
        # the speak bar: only a SURPRISE is worth saying (a capture whose
        # gate sim ran hot -- the field already held this, and it still
        # mattered: that is the note-pattern shape). Planner-gated.
        surprise = (isinstance(sim, (int, float)) and float(sim) >= 0.98)
        if surprise and tags:
            names = ", ".join(str(t) for t in tags[:3])
            self._speak(
                f"pattern: {label} '{situation}' landed on {names} -- "
                f"the field already held it", act="note-pattern")
        # the entity-touch pass: born stamps refresh (the night renders
        # tracks from them; no write here -- the night owns persistence)

    def _digest_chat(self, payload: dict, est) -> None:
        """The operator's turn chewed: is anything worth SAYING? The
        planner gate holds (stay-silent is the learned default) -- only a
        context whose transition table has LEARNED an operator-facing act
        (ask-operator / note-pattern) speaks, and only above the surprise
        bar (a question shape the field already holds an answer-adjacent
        pattern for). Deliberation, not chatter."""
        import numpy as _np
        text = str(payload.get("text") or "")
        if not text:
            return
        mem = self.srv.membrane
        # the surprise bar: the FIELD's own top-1 sim to this ask -- a hot
        # match means membrane may have something to offer (the ask shape)
        try:
            store = mem.store
            _exs = [e for e in store.exemplars if getattr(e, "vec", None)]
            if len(_exs) < 2:
                return
            _v = _np.asarray(mem.embedder.embed([text])[0], dtype=float)
            _vn = float(_np.linalg.norm(_v))
            if _vn < 1e-12:
                return
            _v = _v / _vn
            _V = _np.asarray([e.vec for e in _exs], dtype=float)
            _Vn = _V / (_np.linalg.norm(_V, axis=1, keepdims=True) + 1e-12)
            top = float((_Vn @ _v).max())
        except Exception:
            return
        if top >= NOVELTY_SPEAK_BAR:
            self._speak(f"you are near held ground: '{text[:100]}' -- "
                        f"top field match {top:.2f}; ask me to recall it",
                        act="ask-operator")
        """Reality's vote landed: a branch confirmed or refuted. Worth
        one sentence when it CONFIRMS (a prediction came true); refutes
        stay quiet (the dreamer's GC already handled the doc)."""
        op = str(payload.get("op") or "")
        if op == "elect" and payload.get("confirmed"):
            eid = str(payload.get("eid") or "")
            ent = est.docs.get(eid)
            term = ((ent.properties or {}).get("terminal") if ent
                    else "") or str(payload.get("text") or "")
            self._speak(f"timeline confirmed: {term[:120]}",
                        act="note-pattern")

    def _digest_night(self, payload: dict, est) -> None:
        """The night's findings on the mind: materializations and heavy
        drift are the reportable shapes; the sweep counts stay internal."""
        mat = int(payload.get("materialized") or 0)
        if mat > 0:
            self._speak(f"night materialized {mat} entities from "
                        f"hot cells", act="report-state")

    # ---- the idle half: THINK AHEAD -------------------------------------------
    def _idle_think(self) -> None:
        """Silence: extend a quarantined branch (what could happen next)
        or a new branch under an open intention. The Phase T tie-in: the
        typed walk (TokenTimeline, fed by the fn stamps) generates the
        extension -- $0; the model call only when the budget allows and
        the walk came up empty (the composer finishes what the table
        cannot). THE ATTENTION RULE (curiosity): the HUNGRIEST branch is
        extended first (highest curiosity property), not the oldest --
        the unanswered questions get the mind's time."""
        srv = self.srv
        est = getattr(srv, "entity_store", None)
        if est is None:
            return
        open_tl = [e for e in est.open_timelines(limit=8)
                   if (e.properties or {}).get("status") == "quarantined"]
        if not open_tl:
            return
        # THE HUNGRIEST FIRST (curiosity): the branch with the highest
        # curiosity property gets the mind's time (ties -> newest).
        branch = max(open_tl,
                     key=lambda e: (float((e.properties or {})
                                          .get("curiosity") or 0.0),
                                    float((e.properties or {})
                                          .get("born") or 0.0)))
        props = branch.properties or {}
        terminal = str(props.get("terminal") or "")
        if not terminal:
            return
        tt = getattr(srv, "membrane", None)
        tt = getattr(tt, "timeline", None) if tt else None
        steps: list[str] = []
        if tt is not None:
            try:
                toks = terminal.lower().split()[:3]
                gen = tt.walk(toks, max_steps=WALK_MAX_STEPS)
                if gen:
                    step = " ".join(gen[len(toks):])[:220]
                    if step.strip():
                        steps = [step]
            except Exception:
                pass
        if not steps and self._model_budget_ok():
            out = self._model_call([
                {"role": "system", "content":
                    "Extend one imagined next step (one sentence) for "
                    "the branch. Output only the sentence."},
                {"role": "user", "content":
                    f"Branch: {terminal[:300]}\nOne next step:"}])
            if out.strip():
                steps = [out.strip()[:220]]
        if not steps:
            return
        # commit the extension (union-merge on the doc: steps append,
        # deduped, bounded)
        have = list(props.get("steps") or [])
        if steps[0] in have:
            return
        have.append(steps[0])
        props["steps"] = have[-12:]
        try:
            est.upsert(branch, embedder=getattr(srv.membrane, "embedder",
                                                None))
            self.stats["extended"] += 1
        except Exception:
            pass

    # ---- the voice: always through the planner --------------------------------
    def _speak(self, text: str, act: str) -> None:
        """The thinker's voice goes through speak() -- the ActTable
        decides (stay-silent is the learned default; reinforcement demotes
        chatter). The dreamer never authors turns; neither does this."""
        try:
            from .opencode import speak
            if speak(self.srv, text, kind=act):
                self.stats["spoken"] += 1
        except Exception:
            pass


def feed_capture(srv, label: str, idx, situation: str, sim=None) -> None:
    """The message-path hook: one real capture becomes a digest item
    (drop-oldest; a busy session's early stimuli fall off the mind)."""
    th = getattr(srv, "thinker", None)
    if th is None:
        return
    try:
        th.enqueue("capture", {"label": label, "idx": idx,
                               "situation": situation, "sim": sim})
    except Exception:
        pass