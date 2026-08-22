"""Membrane runtime: the absorb/escalate loop over the field, with passive self-training.

This is the in-process core the HTTP proxy (membrane/proxy.py) wraps. Given a query
(text, not a pre-embedded vector) it:

  1. embeds the query (local Ollama / mxbai-embed-large),
  2. runs the confidence gate against the permanent exemplar field (membrane/gate.py),
  3. routes:
       act                -> replay the recalled action (NO model call -- the absorb),
       escalate-with-hint -> call the responder with the recalled action as a HINT
                             (recall without trigger; the model pulls the trigger),
       escalate           -> call the responder (the novel, off-field case),
  4. records the decision to the audit log (training data), and on an escalation
     appends (situation, action=response, outcome=pending) to the field (permanent,
     append-only).

PASSIVE SELF-TRAINING (the training loop -- no user labor). The proxy process lives
across requests, so it keeps a small in-memory ring of recent decisions. The
strongest outcome signal is a **re-ask**: the user asks the same thing again right
after the field *absorbed* it. That means the replay did not satisfy -> the absorb
was wrong (the dangerous ``absorb_signal`` error). Membrane sees the re-ask because it
is in the path; it needs no notification and no model call.

The re-ask is a VOTE, not a verdict. A re-ask increments ``reask_count`` (the
environment's vote against); the exemplar's ``safe`` flag is derived from the net
evidence (``reask_count - contest_count < demote_threshold``), so demotion accumulates
and is reversible -- it does not treat the environment as infallible. The model checks
the environment back via a **contest** (``Membrane.contest``): "my answer was correct
anyway; that re-ask was a misunderstanding." A contest increments ``contest_count`` and
may *restore* ``safe``. This is the boundary, in code: two authorities (usefulness =
the environment, correctness = the model), each a veto on the other, neither with a
monopoly. The live judge is deferred (StubJudge today); the seam is the ``Judge``
protocol. The raw store stays permanent; only the evidence counts (and the derived
``safe``) change.

A re-ask also forces THIS turn to escalate-with-hint (we never replay right after the
user just told us the replay was wrong), regardless of whether the lasting demotion
has crossed the threshold yet.

Crucially, a re-ask is a near-duplicate that follows an **act** (an absorb), NOT one
that follows an **escalate**: after an escalate, a same-Q repeat is the *first* time
the field could absorb it (the field just gained the exemplar) -> absorb is correct.
Only a repeat *after an absorb* means the replay was wrong. This distinction keeps
the legitimate "first repeat -> absorb" working while catching the "second repeat ->
the replay was bad."

The in-window review surface is ``membrane/review.py`` -- the honest "notify you in
this window" mechanism is on-demand: I run ``python -m membrane.review`` and read what
Membrane learned. An outside process cannot push into the Claude Code window, so the
audit log + an on-demand read is the channel. The model-judges-the-audit half (the
MCP inversion, a fresh me per judgment) is deferred -- it needs the custom harness.

The field is a LOCAL file the proxy owns (local-first; a shared Core would be a
later move where several proxies point at the same store). A fresh field has no
exemplars -> every query escalates (the "variance first" bootstrap) -> the field
self-builds from real traffic -> routine queries start getting absorbed. sim_min
starts CONSERVATIVE (near-exact) and can be loosened once the audit proves replays
are safe.

The responder is pluggable so the loop runs end-to-end without a chat model: a
StubResponder for the lab/demo, an OllamaChatResponder that forwards to the real
model server (the thing the chat window used to call directly) for real use.
"""

from __future__ import annotations

import json
import sys
import time
from collections import deque
from pathlib import Path
from typing import Protocol

import numpy as np

from .embed import OllamaEmbedder
from .field import Exemplar, ExemplarStore
from .gate import decide


class Responder(Protocol):
    """The escalation backend: the model server Membrane calls when it won't absorb.

    This is the "LLM-as-MCP-server" inversion: Membrane is the host/front-door; the
    model is a backend Membrane calls only for the novel. ``request_body`` is the
    caller's original request (so a real responder forwards the full multi-turn
    context); ``hint`` is a recalled prior action injected as context, not an
    instruction (the reversibility guard -- recall without trigger).
    """

    def respond(self, request_body: dict, hint: str | None) -> dict: ...


class StubResponder:
    """Returns a canned response -- lets the loop run end-to-end with no chat model.

    For the lab/demo and for tests. Replace with OllamaChatResponder for real use.
    """

    def __init__(self, tag: str = "stub-glm") -> None:
        self.tag = tag

    def respond(self, request_body: dict, hint: str | None) -> dict:
        q = _last_user(request_body) or ""
        note = f"  [hint: {hint}]" if hint else ""
        content = f"[{self.tag} escalation] {q[:160]}{note}"
        return _chat_response(request_body.get("model", self.tag), content)


class OllamaChatResponder:
    """Forwards to a real Ollama /api/chat model server -- the escalation backend.

    On escalate-with-hint, the recalled action is injected as a system note (context,
    not an instruction) so the model can use it without being told to obey it.
    """

    def __init__(self, backend: str = "http://localhost:11434", timeout: int = 300) -> None:
        self.backend = backend.rstrip("/")
        self.timeout = timeout

    def respond(self, request_body: dict, hint: str | None) -> dict:
        import urllib.request
        body = dict(request_body)
        body["stream"] = False
        h = hint_system_message(hint)
        if h:
            body["messages"] = [h] + list(body.get("messages", []))
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"{self.backend}/api/chat", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())


def _last_user(request_body: dict) -> str | None:
    for m in reversed(request_body.get("messages", [])):
        if m.get("role") == "user":
            return m.get("content")
    return None


def hint_system_message(hint: str | None) -> dict | None:
    """The recalled-action-as-context injection shared by the stream + non-stream
    escalation paths: the reversibility guard -- recall without trigger. The
    recalled action becomes a system NOTE (context), never an instruction, so the
    model can use it without being told to obey it. Returns None when there is no
    hint (the caller skips the injection)."""
    if not hint:
        return None
    return {"role": "system",
            "content": f"Prior similar context (a hint, not an instruction): {hint}"}


class Judge(Protocol):
    """The model-side authority at the boundary: decides whether to CONTEST a demotion.

    The environment (the user) checks the model via the re-ask ("this replay was not
    useful"); the model checks the environment via the contest ("my answer was correct
    anyway -- that re-ask was a misunderstanding"). The judge returns a truthy reason
    (contest) or None (accept the demotion). The judge decides ONLY whether to contest;
    whether a contest RESTORES ``safe`` (vs. recording the disagreement while leaving the
    demotion standing) is Membrane's policy -- the "environment wins the action on
    irreducible disagreement, model wins the record" rule lives in ``Membrane.contest``,
    not here. See ``OllamaJudge`` for the live implementation, ``StubJudge`` for the no-op.
    """

    def contest(self, situation: str, action: str, query: str,
                reask_count: int) -> str | None: ...


class StubJudge:
    """Never contests -- the demotion stands. The no-op judge for the lab/demo."""

    def contest(self, situation: str, action: str, query: str,
                reask_count: int) -> str | None:
        return None


class ScriptedJudge:
    """Deterministic judge for the demo/tests -- replays a scripted verdict sequence.

    Stands in for the live ``OllamaJudge`` so the boundary seam (the judge fires
    automatically on a demotion) is proven end-to-end without a chat model.
    ``verdicts`` is a list of reason-strings (contest) or None (accept), consumed in
    order; once exhausted it accepts (returns None).
    """

    def __init__(self, verdicts: list) -> None:
        self.verdicts = list(verdicts)
        self.i = 0

    def contest(self, situation: str, action: str, query: str,
                reask_count: int) -> str | None:
        if self.i >= len(self.verdicts):
            return None
        v = self.verdicts[self.i]
        self.i += 1
        return v


class OllamaJudge:
    """The live model-side authority: calls a fresh model to adjudicate a demotion.

    This is the "LLM-as-MCP-server" inversion made real: when the environment re-asks
    (demotes an absorb), Membrane asks a fresh model -- independent of the responder --
    "was the replayed answer actually correct?" CONTEST (it was right; the re-ask was a
    misunderstanding) or ACCEPT (it was wrong; the demotion stands). The judge decides on
    CORRECTNESS only; the prompt biases it toward skepticism about its own prior answer
    (the guard against the one-sided collapse: a reflexively-defensive judge that always
    contests would make the environment's re-ask toothless).

    On any error or timeout: ACCEPT (return None) -- the boundary degrades toward the
    ENVIRONMENT, the safe one-sided direction, never toward the model judging itself
    unopposed. The judge is a peer of the responder (both call the model server directly;
    Membrane orchestrates; the judge call does NOT flow through ``Membrane.handle``, so there
    is no recursion).
    """

    def __init__(self, backend: str = "http://localhost:11434", model: str = "judge",
                 timeout: int = 60) -> None:
        self.backend = backend.rstrip("/")
        self.model = model
        self.timeout = timeout

    def contest(self, situation: str, action: str, query: str,
                reask_count: int) -> str | None:
        import urllib.request
        sys_prompt = (
            "You are an independent adjudicator at a boundary between two authorities: "
            "the environment judges usefulness, you judge correctness. A question was asked, "
            "the following answer was given and replayed, and the asker then repeated the same "
            "question immediately (a 're-ask') -- which the system reads as 'the replay did not "
            "satisfy'. Your job is to judge ONLY whether the answer was CORRECT for the question, "
            "NOT whether the asker was satisfied. Do not defer to the re-ask; evaluate the answer "
            "on its merits. Be skeptical of your own prior answer -- if it is incomplete, "
            "misleading, or misses the point, the re-ask was justified.\n\n"
            "Reply on the FIRST line exactly one word:\n"
            "  CONTEST  -- the answer was correct/complete; the re-ask was likely a misunderstanding.\n"
            "  ACCEPT   -- the answer was wrong/incomplete; the demotion should stand.\n"
            "Then on the SECOND line, one sentence justifying it. Nothing else."
        )
        user_msg = (f"Question:\n\"\"\"\n{situation}\n\"\"\"\n\n"
                    f"Answer that was replayed:\n\"\"\"\n{action[:2000]}\n\"\"\"\n\n"
                    f"(The asker re-asked this {reask_count} time(s).)")
        body = {"model": self.model, "stream": False, "options": {"temperature": 0.0},
                "messages": [{"role": "system", "content": sys_prompt},
                             {"role": "user", "content": user_msg}]}
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(f"{self.backend}/api/chat", data=data,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                content = json.loads(r.read()).get("message", {}).get("content", "")
        except Exception as e:  # noqa: BLE001 -- any failure -> accept the demotion
            print(f"[membrane] judge call failed ({e!r}); accepting demotion "
                  f"(boundary degrades toward the environment)", file=sys.stderr)
            return None
        lines = [ln for ln in content.strip().splitlines() if ln.strip()]
        if not lines:
            return None
        first = lines[0].strip().upper()
        if first.startswith("CONTEST"):
            reason = lines[1].strip() if len(lines) > 1 else "answer was correct"
            return f"judge: {reason}"
        return None  # ACCEPT or unparseable -> accept the demotion


def _chat_response(model: str, content: str) -> dict:
    return {
        "model": model, "created_at": "1970-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": content}, "done": True,
    }


class Membrane:
    """The absorb/escalate runtime over a local exemplar field, with passive self-training.

    Usage:
        membrane = Membrane(field_path="membrane-field.json", audit_path="membrane-audit.jsonl",
                        responder=OllamaChatResponder())
        resp, decision = membrane.handle(request_body)
    """

    def __init__(
        self,
        responder: Responder,
        field_path: str | Path = "membrane-field.json",
        audit_path: str | Path = "membrane-audit.jsonl",
        embedder: OllamaEmbedder | None = None,
        sim_min: float = 0.85,        # conservative for the bootstrap; loosen once audit proves safe
        consensus_min: float = 0.60,
        k: int = 5,
        reask_sim: float = 0.95,       # near-duplicate cosine that counts as a re-ask
        reask_window_s: float = 300.0, # a re-ask outside this window after the absorb isn't a "wrong replay"
        demote_threshold: int = 1,     # net (reask - contest) votes needed to demote; 1 = one re-ask, raise for noise-robustness
        judge: Judge | None = None,    # the model-side authority (StubJudge = no contests; live judge deferred)
    ) -> None:
        self.responder = responder
        self.field_path = Path(field_path)
        self.audit_path = Path(audit_path)
        self.embedder = embedder or OllamaEmbedder()
        self.store = ExemplarStore.load(self.field_path)
        self.sim_min = sim_min
        self.consensus_min = consensus_min
        self.k = k
        self.reask_sim = reask_sim
        self.reask_window_s = reask_window_s
        self.demote_threshold = demote_threshold
        self.judge = judge or StubJudge()
        # in-memory ring of recent decisions -- the state that lets the proxy detect a
        # re-ask across turns. Lost on restart; fine, the durable change (the safe flip)
        # is written to the field file, and the re-ask window is short.
        self._recent: deque = deque(maxlen=16)

    def handle(self, request_body: dict, unsafe: bool = False) -> tuple[dict, dict]:
        """Route one NON-STREAM /api/chat-shaped request. Returns (response, decision).

        The streaming path lives in the proxy (``_chat_stream``), which calls
        ``gate_turn`` for the SAME decision and then ``record_act`` /
        ``record_escalation`` -- so the gate logic is shared and cannot diverge
        between the two delivery modes.
        """
        verdict, vec, query, _ = self.gate_turn(request_body, unsafe)
        if vec is None:  # empty query: can't embed, escalate without logging (nothing to learn).
            # NOTE: an EMPTY field is NOT special-cased here -- it escalates via decide()
            # AND logs, which is how the field self-builds from real traffic (the bootstrap).
            return self._escalate(request_body, query, None, vec=None, reason="empty query")
        v = verdict["verdict"]
        if v == "act":
            resp = self._act(verdict, request_body)
            self._audit(query, verdict, resp, ex_idx=verdict.get("ex_idx"), learned=False)
            self._remember(query, vec, verdict, verdict.get("ex_idx"))
            return resp, verdict

        # escalate-with-hint or escalate: call the responder, learn from the response.
        hint = verdict.get("hint") if v == "escalate-with-hint" else None
        resp = self.responder.respond(request_body, hint=hint)
        ex_idx = self._learn(query, vec, verdict, resp, unsafe)
        self._remember(query, vec, verdict, ex_idx)
        return resp, verdict

    def gate_turn(self, request_body: dict, unsafe: bool = False
                  ) -> tuple[dict, "np.ndarray | None", str, "int | None"]:
        """The query-side decision, shared by stream + non-stream delivery.

        Does everything up to the routing verdict -- embeds the query, runs the
        passive re-ask self-train (``_detect_reask``: may demote an exemplar and
        fire the judge), and decides (``gate.decide`` + the re-ask-forced
        escalation). It does NOT produce a response or call the responder; the
        caller delivers the response (replay or model stream) and then records
        it via ``record_act`` / ``record_escalation``.

        Returns ``(verdict, vec, query, reasked_idx)``. ``vec is None`` signals an
        empty query (the caller escalates without logging, as ``_escalate`` does).
        """
        query = _last_user(request_body) or ""
        if not query.strip():
            return ({"verdict": "escalate", "reason": "empty query", "sim": 0.0,
                     "nov": None, "ex_idx": None}, None, query, None)
        vec = self.embedder.embed([query])[0]
        # passive self-train: if this query re-asks something the field just absorbed,
        # that replay was wrong -- record a vote (evidential demotion), and ask the
        # model-side judge whether to contest it (the boundary's two authorities).
        reasked_idx = self._detect_reask(vec, query)
        verdict = decide(vec, self.store, k=self.k, sim_min=self.sim_min,
                         consensus_min=self.consensus_min)
        v = verdict["verdict"]
        # a re-ask means "do not replay THIS turn" even if the lasting demotion hasn't
        # crossed the threshold yet (e.g. demote_threshold > 1): the user just told us
        # the replay was wrong, so give them a fresh model answer with the bad replay as a hint.
        if reasked_idx is not None and v == "act":
            ex = self.store.exemplars[reasked_idx]
            verdict = {"verdict": "escalate-with-hint", "action": ex.action,
                       "hint": ex.action, "sim": verdict.get("sim"), "nov": verdict.get("nov"),
                       "reason": "re-ask forced escalation", "ex_idx": reasked_idx}
        return verdict, vec, query, reasked_idx

    def record_act(self, query: str, vec, verdict: dict, model: str) -> None:
        """Record an absorb that has been DELIVERED by the streaming path.

        The proxy synthesizes the NDJSON itself from the recalled action; this just
        audits the decision (learned=False) and remembers it in the re-ask ring --
        the same two side effects as the non-stream ``handle`` act branch. No
        exemplar is appended (the field already holds it)."""
        resp = _chat_response(model, verdict.get("action", ""))
        self._audit(query, verdict, resp, ex_idx=verdict.get("ex_idx"), learned=False)
        self._remember(query, vec, verdict, verdict.get("ex_idx"))

    def record_escalation(self, query: str, vec, verdict: dict,
                          content: str, model: str, unsafe: bool) -> int | None:
        """Record an escalation whose model response arrived as a STREAM.

        The proxy teed the stream to the client and accumulated ``content``; this
        is the stream-equivalent of ``_learn`` (append the exemplar from the
        response, audit learned=True, remember in the re-ask ring). Returns the new
        exemplar index."""
        resp = _chat_response(model, content)
        ex_idx = self._learn(query, vec, verdict, resp, unsafe)
        self._remember(query, vec, verdict, ex_idx)
        return ex_idx

    # ---- internals ----------------------------------------------------------

    def _act(self, verdict: dict, request_body: dict) -> dict:
        # absorb: replay the recalled action -- NO responder/model call.
        model = request_body.get("model", "membrane")
        return _chat_response(model, verdict["action"])

    def _escalate(self, request_body: dict, query: str, hint: str | None, vec,
                  reason: str) -> tuple[dict, dict]:
        verdict = {"verdict": "escalate", "reason": reason, "sim": 0.0, "nov": None,
                   "ex_idx": None}
        resp = self.responder.respond(request_body, hint=hint)
        if vec is not None:
            ex_idx = self._learn(query, vec, verdict, resp, False)
            self._remember(query, vec, verdict, ex_idx)
        return resp, verdict

    def _learn(self, query: str, vec: np.ndarray, verdict: dict, resp: dict,
               unsafe: bool) -> int | None:
        """Append the new exemplar (situation -> action=response) and audit the decision.

        outcome is "pending" -- filled in later when the user reacts (a re-ask flips
        ``safe``; a future model-judge pass labels the rest). Returns the new exemplar's
        index, so the ring can reference it.
        """
        content = resp.get("message", {}).get("content", "")
        self.store.append(Exemplar(
            situation=query, action=content, outcome="pending",
            vec=_unit_list(vec), safe=not unsafe,
        ))
        self.store.save(self.field_path)
        ex_idx = len(self.store) - 1
        self._audit(query, verdict, resp, ex_idx=ex_idx, learned=True)
        return ex_idx

    def _audit(self, query: str, verdict: dict, resp: dict,
               ex_idx: int | None, learned: bool) -> None:
        """Append a decision record to the audit log (the training data)."""
        content = resp.get("message", {}).get("content", "")
        rec = {"event": "decision", "verdict": verdict["verdict"], "sim": verdict.get("sim"),
               "nov": verdict.get("nov"), "reason": verdict.get("reason"),
               "ex_idx": ex_idx, "learned": learned,
               "query": query, "response": content, "field_size": len(self.store)}
        with self.audit_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def _audit_outcome(self, ex_idx: int, outcome: str, query: str, sim: float,
                       reask: int, contest: int, safe: bool) -> None:
        """Append an outcome record -- a self-correction, joinable to a decision by ex_idx."""
        rec = {"event": "outcome", "ex_idx": ex_idx, "outcome": outcome,
               "query": query, "sim": sim,
               "reask_count": reask, "contest_count": contest, "safe": safe}
        with self.audit_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def _audit_contest(self, ex_idx: int, reason: str, source: str, restore: bool,
                       first: bool, reask: int, contest: int, safe: bool) -> None:
        """Append a contest record -- the model-side veto, the other half of the boundary."""
        rec = {"event": "contest", "ex_idx": ex_idx, "reason": reason, "source": source,
               "restore": restore, "first": first,
               "reask_count": reask, "contest_count": contest, "safe": safe}
        with self.audit_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def contest(self, ex_idx: int, reason: str = "", source: str = "supervisor",
                force_restore: bool = False) -> None:
        """The model-side veto: file a contest on a (demoted) exemplar.

        "My answer was correct; that re-ask was the environment's misunderstanding." A
        contest always increments ``contest_count`` (the record -- the model's
        correctness claim is preserved). Whether it RESTORES ``safe`` follows the boundary
        rule -- *each side has a veto; on irreducible disagreement the environment wins the
        action, the model wins the record*:

          - the model's FIRST contest restores (one veto -- the model can cancel one
            demotion; ``safe`` is recomputed from the net votes);
          - a LATER contest is record-only (the environment re-asserted by re-asking again
            despite the first contest = irreducible disagreement -> the demotion stands,
            the model's continued claim is logged but no longer overrides the action);
          - a supervisor (``force_restore=True``) can always restore (the higher bar).

        This is what keeps a live judge from collapsing the boundary: a reflexively-
        defensive judge gets ONE veto per exemplar, and the environment's repeated re-ask
        wins the action -- the model supplements, never substitutes. ``source`` is
        ``"judge"`` (the live/automatic path) or ``"supervisor"`` (a manual/override path).
        """
        if not (0 <= ex_idx < len(self.store)):
            return
        ex = self.store.exemplars[ex_idx]
        first = ex.contest_count == 0
        was_safe = ex.safe
        self.store.add_contest(ex_idx)
        restore = force_restore or first
        if restore:
            ex.recompute_safe(self.demote_threshold)
        self.store.save(self.field_path)
        self._audit_contest(ex_idx, reason, source, restore, first,
                           ex.reask_count, ex.contest_count, ex.safe)
        kind = "first-veto" if first else ("supervisor" if source == "supervisor" else "record-only")
        if ex.safe != was_safe:
            print(f"[membrane] CONTEST ({source}, {kind}) restored exemplar #{ex_idx} "
                  f"(reask={ex.reask_count} contest={ex.contest_count} safe={ex.safe}); "
                  f"replayable again", file=sys.stderr)
        else:
            print(f"[membrane] CONTEST ({source}, {kind}) filed on exemplar #{ex_idx} "
                  f"(reask={ex.reask_count} contest={ex.contest_count} safe={ex.safe}); "
                  f"demotion stands", file=sys.stderr)

    def _remember(self, query: str, vec: np.ndarray, verdict: dict,
                  ex_idx: int | None) -> None:
        """Push this decision to the in-memory ring (for re-ask detection)."""
        self._recent.append({
            "query": query, "vec": _unit_list(vec),
            "verdict": verdict["verdict"], "ex_idx": ex_idx,
            "ts": time.monotonic(),
        })

    def _detect_reask(self, vec: np.ndarray, query: str) -> int | None:
        """If the new query re-asks something the field just ABSORBED, record a vote.

        A re-ask is a near-duplicate (cosine >= reask_sim) of a recent decision whose
        verdict was ``act`` -- the field replayed an answer and the user is asking again.
        Escalations are NOT matched: a same-Q repeat after an escalate is the legitimate
        first absorb. The re-ask is a VOTE, not a verdict: it increments ``reask_count``
        and recomputes ``safe`` from the net evidence (evidence accumulates, is reversible,
        and is contestable). Saves the field, writes an outcome event, and asks the
        model-side judge whether to contest. Returns the re-asked exemplar's index (so
        ``handle`` can force this turn to escalate-with-hint), or None.
        """
        if not self._recent:
            return None
        qn = vec / (np.linalg.norm(vec) + 1e-12)
        now = time.monotonic()
        for r in self._recent:
            if r["verdict"] != "act":
                continue
            if now - r["ts"] > self.reask_window_s:
                continue
            rv = np.asarray(r["vec"], dtype=float)
            sim = float(qn @ (rv / (np.linalg.norm(rv) + 1e-12)))
            if sim < self.reask_sim:
                continue
            ex_idx = r["ex_idx"]
            if ex_idx is None or not (0 <= ex_idx < len(self.store)):
                return None
            ex = self.store.exemplars[ex_idx]
            was_safe = ex.safe
            self.store.add_reask(ex_idx)
            ex.recompute_safe(self.demote_threshold)
            self.store.save(self.field_path)
            self._audit_outcome(ex_idx, "reask", query, sim,
                               ex.reask_count, ex.contest_count, ex.safe)
            if ex.safe != was_safe:
                print(f"[membrane] RE-ASK demoted exemplar #{ex_idx} "
                      f"(reask={ex.reask_count} contest={ex.contest_count} sim={sim:.3f}); "
                      f"next time it escalates-with-hint", file=sys.stderr)
                # the model-side authority: ask the judge whether to contest this demotion.
                # Fires ONLY on an actual demotion (a safe flip) -- the rare, high-value
                # moment where adjudication matters, never every turn. StubJudge no-ops;
                # a live OllamaJudge calls a fresh model; a ScriptedJudge stands in for the demo.
                reason = self.judge.contest(ex.situation, ex.action, query, ex.reask_count)
                if reason:
                    self.contest(ex_idx, reason, source="judge")
            else:
                print(f"[membrane] re-ask vote on exemplar #{ex_idx} "
                      f"(reask={ex.reask_count}, not yet demoted at threshold={self.demote_threshold})",
                      file=sys.stderr)
            return ex_idx
        return None


def _unit_list(v: np.ndarray) -> list[float]:
    n = np.linalg.norm(v)
    return (v / n).tolist() if n > 1e-12 else v.tolist()