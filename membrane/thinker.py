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


def _branch_convergence(props: dict, lane) -> float:
    """THE CONVERGENCE PRIOR (the VSA lane's #9 consumer): how close the
    branch's terminal text is to the lane's recent REAL turns (best
    Hamming-sim over the lane's history; 0.0 with no lane/history --
    the vec era's behavior). The idle thinker uses it as the SECOND
    ranking key (curiosity stays primary): a branch the world's
    language is drifting TOWARD outranks an equally-hungry stale one.
    $0 (the states are already encoded; the terminal encodes once and
    caches on the props)."""
    if lane is None or not getattr(lane, "_history", None):
        return 0.0
    try:
        terminal = str((props or {}).get("terminal") or "")
        if not terminal.strip():
            return 0.0
        cached = (props.get("vsa_converge") or {})
        if cached.get("cb") == lane.codebook.id:
            from .vsa import unpack_state as _unpack
            from .vsa import hamming as _ham
            st = _unpack(cached["s"])
        else:
            st = lane.encode(terminal)
            if st is None:
                return 0.0
            from .vsa import pack_state as _pack
            props["vsa_converge"] = {"cb": lane.codebook.id,
                                     "s": _pack(st)}
        return max(1.0 - vsa_hamming(st, h)
                   for h in lane._history[:-1]) \
            if len(lane._history) > 1 else 0.0
    except Exception:
        return 0.0


from .vsa import hamming as vsa_hamming  # noqa: E402  (module-level user above)


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
        # THE MIND-LANE (the inner speech, visible): what the thinker
        # chewed and what it decided -- bounded, newest last, rendered in
        # the banner's mind line (the operator's window into the loop)
        self.mind_log: deque = deque(maxlen=6)
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
            # THE SHARP-WAVE REPLAY (the membrain upgrade, phase B):
            # just-completed experiences consolidate on the idle cadence
            # (the night's drift pass scoped to the recently-touched
            # entities -- numpy-only, ~ms; the duty controller governs).
            rep = getattr(self, "_touched_replay", None)
            if rep:
                eid = rep.popleft()
                est = getattr(self.srv, "entity_store", None)
                if est is not None and eid in est.docs:
                    try:
                        from .night import consolidate_entities
                        consolidate_entities(self.srv.membrane, est,
                                             materialize=0,
                                             only_keys=[
                                                 eid[len("membrane::"):]
                                                 if eid.startswith(
                                                     "membrane::") else eid])
                        did_work = True
                    except Exception:
                        pass
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
    def _log_mind(self, entry: dict) -> None:
        """One inner-speech record: what was chewed + the decision (the
        bounded mind log; the banner's mind-lane render)."""
        try:
            self.mind_log.append({"ts": time.time(), **entry})
        except Exception:
            pass

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
        # THE MENTION SCRIPTS (model-written policy): every tagged entity
        # carrying a mention_script gets one engine-mediated pass (0.25s
        # budget, cooldown, the membrane API object injected). The engine
        # applies the script's {render, patch, delete}; the render lands
        # on the entity's banner fragment for the next turn.
        est_all = getattr(self.srv, "entity_store", None)
        if est is not None and tags:
            try:
                from .night import run_entity_update_script, \
                    apply_entity_update
                for tag in tags[:4]:
                    ent = est._resolve_spec(str(tag))
                    if ent is None:
                        continue
                    if not (ent.properties or {}).get("mention_script"):
                        continue
                    out = run_entity_update_script(
                        self.srv, ent, "mention",
                        extra_state={"operator_text": situation,
                                     "label": label, "sim": sim})
                    apply_entity_update(self.srv, ent, out, "mention")
            except Exception:
                pass
        # ---- THE FN->ENTITY STATE BRIDGE (phase 3, the operator's B):
        # the fn stamp (op/binds/returns, parsed at learn time) updates the
        # touched entities' live properties -- the entity layer becomes a
        # MODEL OF PROJECT STATE (which files are hot, what the last tool
        # result was, how many edits in the window). $0: the fn rides the
        # exemplar's extra.
        fn = (getattr(ex, "extra", {}) or {}).get("fn") if ex is not None \
            else None
        if est is not None and tags and isinstance(fn, dict):
            try:
                self._bridge_fn_state(fn, tags, est, label)
            except Exception:
                pass
        # phase 4: the first touch of an entity surfaces its why-cells
        if est is not None and tags:
            try:
                self._why_at_touch(tags, est)
            except Exception:
                pass

        # ---- THE GENERALIZED PATTERN DETECTOR (phase 2, the operator's
        # A, generalized): three universal classes over the digest stream.
        # 1. RECURRENCE: the same situation-signature N times in a window.
        rec_sig = " ".join((label + " " + situation).split()).lower()[:120]
        # 2. FAILURE RESONANCE: a live event near a known FailureMode
        #    entity (the proactive recall push -- the field's learned
        #    physics answering the present).
        if est is not None and tags:
            try:
                self._failure_resonance(tags, est, situation)
            except Exception:
                pass
        self._check_recurrence(rec_sig, tags)

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
        self._log_mind({"kind": "capture", "label": label,
                        "about": (tags[0] if tags else label)[:40],
                        "acted": ("pattern" if (surprise and tags)
                                  else "silent")})

    # ---- phase 3: the fn->entity state bridge -------------------------------
    def _bridge_fn_state(self, fn: dict, tags: list, est, label: str) -> None:
        """The fn stamp updates the touched entities' properties: the live
        project-state model (the monitor-getter scripts' feed). op + binds
        -> the property moves; the night's delta-write persists. THE
        REPLAY REGISTRY (the sharp-wave queue): every touched entity's
        eid joins the replay set -- the idle ticks drain it (the
        mini-consolidation, the night's drift pass scoped to these)."""
        import time as _t
        now = _t.time()
        op = str(fn.get("op") or "?")
        touched = getattr(self, "_replay_eids", None)
        if touched is None:
            self._touched_replay = touched = deque(maxlen=16)
        for tag in tags[:4]:
            ent = est._resolve_spec(str(tag))
            if ent is None:
                continue
            touched.append(ent.eid)
            props = ent.properties
            # the edit/build counters: per-entity, windowed (bounded)
            counts = props.get("fn_counts") if isinstance(
                props.get("fn_counts"), dict) else {}
            counts[op] = int(counts.get(op, 0)) + 1
            props["fn_counts"] = counts
            props["last_fn"] = {"op": op, "ts": round(_t.time(), 1)}
            try:
                est.upsert(ent, embedder=getattr(
                    self.srv.membrane, "embedder", None))
            except Exception:
                pass

    # ---- phase 4: why-recall at touch-time ----------------------------------
    _WHY_TOUCH_TOP = 2          # the why-cells surfaced per touch (cheap)
    _WHY_TOUCH_MIN = 0.72       # the why-stamp bar (the same anchor bar)

    def _why_at_touch(self, tags: list, est) -> None:
        """C: the FIRST touch of an entity in this session surfaces its
        why-stamped cells -- 'why this code looks this way' -- once per
        entity per session (the seat-local cooldown; the planner gate
        holds). The why-stamp rides the exemplar's extra['why']."""
        if not tags or est is None:
            return
        touched = getattr(self, "_touched", None)
        if touched is None:
            self._touched = touched = set()
        fresh = [t for t in tags[:4]
                 if str(t) not in touched]
        if not fresh:
            return
        store = getattr(self.srv.membrane, "store", None)
        if store is None:
            return
        # the why-cells nearest any fresh entity's tag cells (the
        # entity-layer links point at cell indexes)
        why_cells: list[tuple[float, str]] = []
        exs = getattr(store, "exemplars", [])
        for tag in fresh:
            ent = est._resolve_spec(str(tag))
            if ent is None:
                continue
            for i in (ent.links or [])[:64]:
                if not (0 <= i < len(exs)):
                    continue
                ex = exs[i]
                w = (getattr(ex, "extra", {}) or {}).get("why")
                if isinstance(w, (int, float)) and float(w) >=                         self._WHY_TOUCH_MIN:
                    why_cells.append((float(w),
                                      (ex.action or ex.situation or "")[:120]))
        if not why_cells:
            for t in fresh:
                self._touched = (self._touched or set()) | {t}
            return
        why_cells.sort(reverse=True)
        top = why_cells[:self._WHY_TOUCH_TOP]
        for t in fresh:
            self._touched = (self._touched or set()) | {t}
        names = "; ".join(c for _s, c in top)
        self._speak(f"design intent for {fresh[0]}: {names}",
                    act="note-pattern")

    # ---- phase 2: the pattern classes ---------------------------------------
    _REC_WINDOW_S = 600.0       # the recurrence window (10 min)
    _REC_THRESHOLD = 3          # N repeats -> a pattern note
    _REC_COOLDOWN_S = 1800.0    # per-signature cooldown (the noise guard)

    def _failure_resonance(self, tags: list, est, situation: str) -> None:
        """A live event's entity tags landing near a known FailureMode ->
        the proactive recall push ('this matches a failure you solved').
        The universal class: any known-bad pattern, not just builds."""
        if not tags:
            return                    # no event, no resonance
        import numpy as _np
        fails = [e for e in est.docs.values()
                 if (e.properties or {}).get("class") == "FailureMode"
                 or e.cls == "FailureMode"]
        if not fails:
            return
        mem = self.srv.membrane
        store = getattr(mem, "store", None)
        if store is None:
            return
        try:
            from .rag import _clip
            _exs = [e for e in store.exemplars
                    if getattr(e, "vec", None)]
            if not _exs:
                return
            _v = _np.asarray(mem.embedder.embed([situation])[0],
                             dtype=float)
            _vn = _np.linalg.norm(_v)
            if _vn < 1e-12:
                return
            _v = _v / _vn
            for f in fails:
                fpos = getattr(f, "position", None)
                if not fpos:
                    continue
                fv = _np.asarray(fpos, dtype=float)
                fn_ = _np.linalg.norm(fv)
                if fn_ > 1e-12 and float((fv / fn_) @ _v) >= 0.85:
                    self._speak(
                        f"this matches a known failure: "
                        f"{f.describe()[:80]} -- ask me to recall the fix",
                        act="note-pattern")
                    return                 # one resonance per digest
        except Exception:
            pass

    def _check_recurrence(self, rec_sig: str, tags: list) -> None:
        """Class 1: the same signature N times in the window -> one
        pattern note (per-signature cooldown; planner-gated). The
        signature window rides here (the digest's only job is the call)."""
        import time as _t
        now = _t.time()
        sigs = getattr(self, "_sigs", None)
        if sigs is None:
            self._sigs = sigs = []
        sigs.append((now, rec_sig))
        if len(sigs) > 256:               # bounded window (drop the oldest)
            del sigs[:-256]
        recent = [ts for ts, s in sigs
                  if now - ts <= self._REC_WINDOW_S and s == rec_sig]
        if len(recent) < self._REC_THRESHOLD:
            return
        cooldowns = getattr(self, "_rec_cooldowns", None)
        if cooldowns is None:
            cooldowns = self._rec_cooldowns = {}
        if now - float(cooldowns.get(rec_sig, 0)) < self._REC_COOLDOWN_S:
            return
        cooldowns[rec_sig] = now
        tag_s = (", ".join(str(t) for t in tags[:2]) if tags
                 else "this direction")
        self._speak(
            f"pattern: {rec_sig[:80]!r} came up {len(recent)}x in "
            f"{int(self._REC_WINDOW_S / 60)} min", act="note-pattern")

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
            # THE CLEAN QUOTE (the operator's 'restarted <system-reminder>'
            # exhibit): the speech carries the OPERATOR's actual words --
            # machinery spans (mode banners, reminders) are stripped
            # before the render, never relayed verbatim.
            try:
                from .rag import strip_machinery_text
                clean = strip_machinery_text(text)[:100] or text[:100]
            except Exception:
                clean = text[:100]
            self._speak(f"you are near held ground: '{clean}' -- "
                        f"top field match {top:.2f}; ask me to recall it",
                        act="ask-operator")
            self._log_mind({"kind": "chat", "about": clean[:40],
                            "acted": "asked to recall"})
        else:
            self._log_mind({"kind": "chat",
                            "about": str(payload.get("text") or "")[:40],
                            "acted": "silent"})
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
        # THE HUNGRIEST FIRST (curiosity), converged-second: the branch
        # with the highest curiosity property gets the mind's time; the
        # VSA lane's convergence (the branch terminal's state vs the
        # recent REAL turns) breaks ties and re-ranks near-ties -- the
        # world's language drifting TOWARD a branch pulls it forward.
        # Curiosity stays primary (the hunger covenant); convergence is
        # a 0.1-scale nudge (the tie-band only), $0, lane-failure safe.
        _lane = getattr(srv.membrane, "vsa_lane", None) \
            if getattr(srv, "membrane", None) is not None else None

        def _rank(e):
            p = e.properties or {}
            cur = float(p.get("curiosity") or 0.0)
            conv = _branch_convergence(p, _lane) if _lane is not None \
                else 0.0
            return (cur, round(cur, 2) * 0.01 + 0.01 * conv,
                    float(p.get("born") or 0.0))
        branch = max(open_tl, key=_rank)
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
            self._log_mind({"kind": "idle", "about": terminal[:40],
                            "acted": "no step (table miss + budget)"})
            return
        self._log_mind({"kind": "idle", "about": terminal[:40],
                        "acted": f"extended: {steps[0][:40]}"})
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