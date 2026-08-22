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
is in the path; it needs no notification and no model call. It flips that exemplar's
``safe`` flag to False, so the *next* time the field recalls it as a **hint**
(``escalate-with-hint``) instead of replaying the bad answer. The raw store stays
permanent; only the ``safe`` bit flips.

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
        # that replay was wrong -- flip the exemplar's safe flag BEFORE running the gate,
        # so this turn escalates-with-hint instead of replaying the bad answer again.
        self._detect_reask(vec, query)

        verdict = decide(vec, self.store, k=self.k, sim_min=self.sim_min,
                        consensus_min=self.consensus_min)
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

    def _audit_outcome(self, ex_idx: int, outcome: str, query: str, sim: float) -> None:
        """Append an outcome record -- a self-correction, joinable to a decision by ex_idx."""
        rec = {"event": "outcome", "ex_idx": ex_idx, "outcome": outcome,
               "query": query, "sim": sim}
        with self.audit_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def _remember(self, query: str, vec: np.ndarray, verdict: dict,
                  ex_idx: int | None) -> None:
        """Push this decision to the in-memory ring (for re-ask detection)."""
        self._recent.append({
            "query": query, "vec": _unit_list(vec),
            "verdict": verdict["verdict"], "ex_idx": ex_idx,
            "ts": time.monotonic(),
        })

    def _detect_reask(self, vec: np.ndarray, query: str) -> None:
        """If the new query re-asks something the field just ABSORBED, the replay was wrong.

        A re-ask is a near-duplicate (cosine >= reask_sim) of a recent decision whose
        verdict was ``act`` -- i.e. the field replayed an answer and the user is asking
        again. Escalations are NOT matched: a same-Q repeat after an escalate is the
        legitimate first absorb. Flips that exemplar's ``safe`` flag (-> next time it
        escalates-with-hint), saves the field, and writes an outcome event to the audit.
        """
        if not self._recent:
            return
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
                return
            ex = self.store.exemplars[ex_idx]
            if not ex.safe:
                # already demoted by an earlier re-ask; don't double-log.
                return
            self.store.set_safe(ex_idx, False)
            self.store.save(self.field_path)
            self._audit_outcome(ex_idx, "wrong-absorb", query, sim)
            print(f"[cortex] RE-ASK after absorb: exemplar #{ex_idx} flipped safe=False "
                  f"(sim={sim:.3f}); next time it escalates-with-hint", file=sys.stderr)
            return


def _unit_list(v: np.ndarray) -> list[float]:
    n = np.linalg.norm(v)
    return (v / n).tolist() if n > 1e-12 else v.tolist()