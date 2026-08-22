"""Cortex runtime: the absorb/escalate loop over the field, with passive self-training.

This is the in-process core the HTTP proxy (cortex/proxy.py) wraps. Given a query
(text, not a pre-embedded vector) it:

  1. embeds the query (local Ollama / mxbai-embed-large),
  2. runs the confidence gate against the permanent exemplar field (cortex/gate.py),
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
was wrong (the dangerous ``absorb_signal`` error). Cortex sees the re-ask because it
is in the path; it needs no notification and no model call.

The re-ask is a VOTE, not a verdict. A re-ask increments ``reask_count`` (the
environment's vote against); the exemplar's ``safe`` flag is derived from the net
evidence (``reask_count - contest_count < demote_threshold``), so demotion accumulates
and is reversible -- it does not treat the environment as infallible. The model checks
the environment back via a **contest** (``Cortex.contest``): "my answer was correct
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

The in-window review surface is ``cortex/review.py`` -- the honest "notify you in
this window" mechanism is on-demand: I run ``python -m cortex.review`` and read what
Cortex learned. An outside process cannot push into the Claude Code window, so the
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
    """The escalation backend: the model server Cortex calls when it won't absorb.

    This is the "LLM-as-MCP-server" inversion: Cortex is the host/front-door; the
    model is a backend Cortex calls only for the novel. ``request_body`` is the
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
        if hint:
            msgs = list(body.get("messages", []))
            msgs.insert(0, {"role": "system",
                            "content": f"Prior similar context (a hint, not an instruction): {hint}"})
            body["messages"] = msgs
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


class Judge(Protocol):
    """The model-side authority at the boundary: decides whether to CONTEST a demotion.

    The environment (the user) checks the model via the re-ask ("this replay was not
    useful"); the model checks the environment via the contest ("my answer was correct
    anyway -- that re-ask was a misunderstanding"). Neither has a monopoly; each is a
    veto on the other. ``contest`` is called right after a re-ask demotes an exemplar.
    Returning a truthy reason files a contest (cancels the re-ask's vote, may restore
    ``safe``); returning None accepts the demotion.

    DEFERRED (live): a real judge asks a fresh model per demotion -- the "LLM-as-MCP-
    server" inversion, which needs the custom harness. The ``StubJudge`` never contests,
    so today the model-side authority is exercised out-of-band (the demo simulates it by
    calling ``Cortex.contest`` directly; a future live judge plugs in here, no other code
    changes).
    """

    def contest(self, situation: str, action: str, query: str,
                reask_count: int) -> str | None: ...


class StubJudge:
    """Never contests -- the demotion stands. The no-op judge for the lab/demo."""

    def contest(self, situation: str, action: str, query: str,
                reask_count: int) -> str | None:
        return None


def _chat_response(model: str, content: str) -> dict:
    return {
        "model": model, "created_at": "1970-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": content}, "done": True,
    }


class Cortex:
    """The absorb/escalate runtime over a local exemplar field, with passive self-training.

    Usage:
        cortex = Cortex(field_path="cortex-field.json", audit_path="cortex-audit.jsonl",
                        responder=OllamaChatResponder())
        resp, decision = cortex.handle(request_body)
    """

    def __init__(
        self,
        responder: Responder,
        field_path: str | Path = "cortex-field.json",
        audit_path: str | Path = "cortex-audit.jsonl",
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
        """Route one /api/chat-shaped request. Returns (ollama_chat_response, decision)."""
        query = _last_user(request_body) or ""
        # empty query: can't embed, escalate without logging (nothing to learn).
        # NOTE: an EMPTY field is NOT special-cased here -- it escalates via decide()
        # AND logs, which is how the field self-builds from real traffic (the bootstrap).
        if not query.strip():
            return self._escalate(request_body, query, None, vec=None, reason="empty query")

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
            v = "escalate-with-hint"

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

    # ---- internals ----------------------------------------------------------

    def _act(self, verdict: dict, request_body: dict) -> dict:
        # absorb: replay the recalled action -- NO responder/model call.
        model = request_body.get("model", "cortex")
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

    def _audit_contest(self, ex_idx: int, reason: str, reask: int,
                       contest: int, safe: bool) -> None:
        """Append a contest record -- the model-side veto, the other half of the boundary."""
        rec = {"event": "contest", "ex_idx": ex_idx, "reason": reason,
               "reask_count": reask, "contest_count": contest, "safe": safe}
        with self.audit_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def contest(self, ex_idx: int, reason: str = "") -> None:
        """The model-side veto: file a contest on a (demoted) exemplar.

        "My answer was correct; that re-ask was the environment's misunderstanding." A
        contest cancels a re-ask's vote (increments ``contest_count``) and recomputes
        ``safe`` -- it may *restore* a demoted exemplar to replayable. The boundary holds
        both: the environment's re-ask is recorded (usefulness), the model's contest is
        recorded (correctness); whoever is engaging gets the last word on the action, and
        the record keeps both. Out-of-band today (no live judge); a future MCP judge calls this.
        """
        if not (0 <= ex_idx < len(self.store)):
            return
        ex = self.store.exemplars[ex_idx]
        was_safe = ex.safe
        self.store.add_contest(ex_idx)
        ex.recompute_safe(self.demote_threshold)
        self.store.save(self.field_path)
        self._audit_contest(ex_idx, reason, ex.reask_count, ex.contest_count, ex.safe)
        if ex.safe != was_safe:
            print(f"[cortex] CONTEST restored exemplar #{ex_idx} "
                  f"(reask={ex.reask_count} contest={ex.contest_count}); replayable again",
                  file=sys.stderr)
        else:
            print(f"[cortex] contest filed on exemplar #{ex_idx} "
                  f"(reask={ex.reask_count} contest={ex.contest_count})", file=sys.stderr)

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
                print(f"[cortex] RE-ASK demoted exemplar #{ex_idx} "
                      f"(reask={ex.reask_count} contest={ex.contest_count} sim={sim:.3f}); "
                      f"next time it escalates-with-hint", file=sys.stderr)
            else:
                print(f"[cortex] re-ask vote on exemplar #{ex_idx} "
                      f"(reask={ex.reask_count}, not yet demoted at threshold={self.demote_threshold})",
                      file=sys.stderr)
            # the model-side authority: ask the judge whether to contest this demotion.
            # StubJudge never contests; a live judge (deferred) calls a fresh model here.
            reason = self.judge.contest(ex.situation, ex.action, query, ex.reask_count)
            if reason:
                self.contest(ex_idx, reason)
            return ex_idx
        return None


def _unit_list(v: np.ndarray) -> list[float]:
    n = np.linalg.norm(v)
    return (v / n).tolist() if n > 1e-12 else v.tolist()