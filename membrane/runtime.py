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
from .variety import thermo_state


class Responder(Protocol):
    """The escalation backend: the model server Membrane calls when it won't absorb.

    This is the "LLM-as-MCP-server" inversion: Membrane is the host/front-door; the
    model is a backend Membrane calls only for the novel. ``request_body`` is the
    caller's original request (so a real responder forwards the full multi-turn
    context); ``hint`` is a recalled prior action injected as context, not an
    instruction (the reversibility guard -- recall without trigger).
    """

    def respond(self, request_body: dict, hint: str | None,
                auth: dict | None = None) -> dict: ...


class StubResponder:
    """Returns a canned response -- lets the loop run end-to-end with no chat model.

    For the lab/demo and for tests. Replace with OllamaChatResponder for real use.
    """

    def __init__(self, tag: str = "stub-glm") -> None:
        self.tag = tag

    def respond(self, request_body: dict, hint: str | None,
                auth: dict | None = None) -> dict:
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

    def respond(self, request_body: dict, hint: str | None,
                auth: dict | None = None) -> dict:
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


class AnthropicMessagesResponder:
    """Forwards to Anthropic's ``/v1/messages`` (non-stream) -- the escalation
    backend for Claude Code / the Anthropic API.

    The API key and ``anthropic-version`` are passed per-call in ``auth`` (read
    from the incoming request headers by the proxy and threaded through
    ``handle``); they are NEVER stored on the responder or written to any log or
    the field -- passthrough only (the cortex-experiment-token discipline). On
    escalate-with-hint, the recalled action is prepended to the top-level
    ``system`` field as a note (context, not an instruction) -- Anthropic keeps
    system content out of the ``messages`` array.
    """

    def __init__(self, backend: str = "https://api.anthropic.com",
                 timeout: int = 300) -> None:
        self.backend = backend.rstrip("/")
        self.timeout = timeout

    def respond(self, request_body: dict, hint: str | None,
                auth: dict | None = None) -> dict:
        import urllib.request
        body = dict(request_body)
        body["stream"] = False
        if hint:
            note = f"Prior similar context (a hint, not an instruction): {hint}"
            existing = body.get("system")
            if isinstance(existing, str) and existing:
                body["system"] = note + "\n\n" + existing
            elif isinstance(existing, list):
                body["system"] = [{"type": "text", "text": note}] + list(existing)
            else:
                body["system"] = note
        headers = {"Content-Type": "application/json",
                   "anthropic-version": "2023-06-01"}
        if auth:
            # forward only auth-shaped headers; never echo the key anywhere else
            for k, v in auth.items():
                if k.lower() in ("x-api-key", "authorization", "anthropic-version"):
                    headers[k] = v
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"{self.backend}/v1/messages", data=data,
                                     method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())


def _content_text(content) -> str | None:
    """Extract human-readable text from a message's ``content``.

    ``content`` may be a plain string (Ollama) or a list of content blocks
    (Anthropic: ``{type: text|tool_result|image, ...}``). Returns the joined
    ``text`` blocks, or None if the message carries no text (e.g. a
    tool_result-only user message -- a system-injected tool output, not a
    human turn)."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text", "")
                if t:
                    parts.append(t)
        return " ".join(parts) if parts else None
    return None


def _response_text(resp) -> str:
    """The assistant's text from a model response, shape-agnostic.

    Ollama ``/api/chat``: ``resp["message"]["content"]`` (a string). Anthropic
    ``/v1/messages``: ``resp["content"]`` (a list of ``{type:text, text}`` blocks,
    or a string). Used by ``_learn`` / ``_audit`` so the same field-building path
    works behind either backend."""
    if not isinstance(resp, dict):
        return ""
    msg = resp.get("message")
    if isinstance(msg, dict):
        c = msg.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return _content_text(c) or ""
    c = resp.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return _content_text(c) or ""
    return ""


def _last_user(request_body: dict) -> str | None:
    """The last human-authored text turn in the conversation -- the ``situation``
    to embed. Scans user messages in reverse and skips tool_result-only user
    messages (system-injected tool outputs, not human turns), returning the
    first with real text. Works for both Ollama (string content) and Anthropic
    (content-block list) request shapes."""
    for m in reversed(request_body.get("messages", [])):
        if m.get("role") != "user":
            continue
        text = _content_text(m.get("content"))
        if text and text.strip():
            return text
    return None


def _has_error_tool_result(request_body: dict) -> bool:
    """True if the LATEST user message carries a ``tool_result`` block marked
    ``is_error`` (Anthropic shape) -- an ambient, no-dislike-button signal that
    a tool the model invoked failed in the world. Only the latest user message
    is inspected: the error is the outcome of the most recent tool call, which
    corresponds to the most recent exemplar in the ring."""
    for m in reversed(request_body.get("messages", [])):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" \
                        and b.get("is_error"):
                    return True
        break                       # only the latest user message
    return False


def _error_tool_result_text(request_body: dict) -> str | None:
    """The text of the latest user message's first ``is_error`` ``tool_result``
    block (Anthropic shape), else None. A tool_result's ``content`` may be a
    string or a list of text blocks; we surface whatever the tool returned so the
    demotion/judge has the error context. None means no error tool_result."""
    for m in reversed(request_body.get("messages", [])):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" \
                        and b.get("is_error"):
                    c = b.get("content")
                    if isinstance(c, str) and c.strip():
                        return c
                    if isinstance(c, list):
                        parts = [x.get("text", "") for x in c
                                 if isinstance(x, dict) and x.get("type") == "text"]
                        txt = " ".join(p for p in parts if p)
                        return txt or None
        break                       # only the latest user message
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
        dedup_sim: float = 0.95,       # learn-time guard: don't append a near-identical situation (>= this cosine)
        neg_followup: bool = True,     # also demote on a short negative follow-up ("not working", "wrong", "no") right after an absorb, so the user needn't repeat the query to signal "bad" (on by default; can false-demote -- disable with --no-neg-followup)
        neg_followup_max_len: int = 64,
        demote_threshold: int = 1,     # net (reask - contest) votes needed to demote; 1 = one re-ask, raise for noise-robustness
        judge: Judge | None = None,    # the model-side authority (StubJudge = no contests; live judge deferred)
        # ---- observer-only mode (Claude Code / Anthropic adapter) ----
        # When true the delivery layer NEVER replays a stored action -- it forwards
        # every turn to the responder and learns from the response, auditing what
        # WOULD have absorbed (``would_absorb=True``) for observability. Replaying a
        # coding answer is unsafe: the right action depends on the full tool-using
        # transcript, not a cosine match on the latest message. The field's value is
        # its accumulated, labeled variety, not token savings. Adds two ambient
        # quality signals (gated to observer so the Ollama path is untouched): a
        # ``tool_result is_error`` demotion and a re-ask after an ESCALATE.
        observer: bool = False,
        # ---- information-gated learning (the cheap entropy-based value half) ----
        # vague_filter: drop low-information situations ("yes", "ok", "beep") before
        #   dedup -- they pull inconsistent boilerplate and fill the field with junk.
        #   Default ON; conservative (false-negative = learn, the safe side).
        # variety_gate: the principled half -- only append an exemplar if it grows the
        #   field's effective rank (adds variety, not just count). 2x SVD per learn on
        #   a big field, so default OFF; the cheap vague+dedup cover most cases.
        vague_filter: bool = True,
        vague_min_tokens: int = 4,
        variety_gate: bool = False,
        # ---- the field's thermodynamic state: variety recorded per decision ----
        # The gate already pays for an SVD every turn (for the novelty on/off
        # boolean). thermo_state (variety.py) turns that ONE SVD into a full
        # per-decision record {eff_rank, spectrum_entropy, free_energy, rank_ratio,
        # safe_frac} and -- opt-in -- lets it steer the threshold (the temperature).
        # This is "we were missing variety at the membrane level": variety was
        # computed every turn and thrown away (only the novelty on/off flag, almost
        # always off on real high-rank fields). Now it is recorded, and can drive
        # the gate's eagerness. All per-veil (per-instance); no central controller.
        #   variety_temp: opt-in adaptive temperature. When on, the gate's sim_min
        #     is EASED toward sim_min_floor as the field's free_energy grows -- a
        #     field that has accrued safe, spread-out variety can afford to be more
        #     eager; a low-variety field stays conservative. Default OFF (the
        #     threshold is static, the current behaviour).
        #   temp_driver: which thermodynamic quantity drives eagerness:
        #     "free_energy" (default, the safe-weighted VALUE number) or
        #     "spectrum_entropy" (the raw variety, ignores safety).
        #   temp_tau: the saturating constant in eagerness = val/(val+temp_tau);
        #     smaller = relax sooner, larger = need more variety before easing.
        #   sim_min_floor: the eagerness lower bound -- never get more eager than
        #     this. 0.70 is the validated safe cosine cutoff (real_knee experiment).
        variety_temp: bool = False,
        temp_driver: str = "free_energy",
        temp_tau: float = 1.0,
        sim_min_floor: float = 0.70,
        # ---- the veil: nudge everything that transits this instance ----
        # A per-veil perturbation of the incoming situation embedding: add Gaussian
        # noise of strength nudge_sigma, renormalize. Applied to EVERY message
        # through the veil -- both the gate's matching query AND the exemplar vec
        # that gets stored (so the field's geometry reflects the veil's nudge).
        # The forwarded prompt text is NEVER garbled -- only the field's
        # representation is jittered. Default OFF (sigma=0 -> identity veil, zero
        # behaviour change). Each veil seeds its own RNG (nudge_seed) so
        # independent veils realize different perturbations from the same traffic
        # and build divergent variety trajectories -- without any central input
        # or orchestrator (each veil is autonomous). The model always sees the
        # real prompt; only the field sees the veiled situation.
        nudge_sigma: float = 0.0,
        nudge_seed: int = 0,
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
        self.dedup_sim = dedup_sim
        self.neg_followup = neg_followup
        self.neg_followup_max_len = neg_followup_max_len
        self.demote_threshold = demote_threshold
        self.judge = judge or StubJudge()
        self.observer = observer
        self.vague_filter = vague_filter
        self.vague_min_tokens = vague_min_tokens
        self.variety_gate = variety_gate
        self.variety_temp = variety_temp
        self.temp_driver = temp_driver
        self.temp_tau = temp_tau
        self.sim_min_floor = sim_min_floor
        self.nudge_sigma = nudge_sigma
        # per-veil RNG: each veil seeds its own so independent instances realize
        # different nudges from the same traffic. Unused when nudge_sigma == 0.
        self._rng = np.random.default_rng(nudge_seed)
        # in-memory ring of recent decisions -- the state that lets the proxy detect a
        # re-ask across turns. Lost on restart; fine, the durable change (the safe flip)
        # is written to the field file, and the re-ask window is short.
        self._recent: deque = deque(maxlen=16)

    def handle(self, request_body: dict, unsafe: bool = False,
               auth: dict | None = None) -> tuple[dict, dict]:
        """Route one NON-STREAM /api/chat-shaped request. Returns (response, decision).

        The streaming path lives in the proxy (``_chat_stream``), which calls
        ``gate_turn`` for the SAME decision and then ``record_act`` /
        ``record_escalation`` -- so the gate logic is shared and cannot diverge
        between the two delivery modes.

        ``auth`` carries passthrough auth headers (e.g. Anthropic ``x-api-key`` /
        ``anthropic-version``) read from the incoming request by the proxy and
        threaded per-call to the responder -- never stored on the instance (a
        ThreadingHTTPServer would race it) and never written to the field/audit.
        """
        verdict, vec, query, _ = self.gate_turn(request_body, unsafe)
        if vec is None:  # empty query: can't embed, escalate without logging (nothing to learn).
            # NOTE: an EMPTY field is NOT special-cased here -- it escalates via decide()
            # AND logs, which is how the field self-builds from real traffic (the bootstrap).
            return self._escalate(request_body, query, None, vec=None, reason="empty query",
                                  auth=auth)
        v = verdict["verdict"]
        if v == "act":
            if self.observer:
                # observer-only: NEVER replay a coding answer -- forward + learn,
                # and audit what WOULD have absorbed (for a future absorb switch).
                return self._observe_forward(verdict, request_body, query, vec, unsafe, auth)
            resp = self._act(verdict, request_body)
            self._audit(query, verdict, resp, ex_idx=verdict.get("ex_idx"), learned=False)
            self._remember(query, vec, verdict, verdict.get("ex_idx"))
            return resp, verdict

        # escalate-with-hint or escalate: call the responder, learn from the response.
        hint = verdict.get("hint") if v == "escalate-with-hint" else None
        resp = self.responder.respond(request_body, hint=hint, auth=auth)
        ex_idx = self._learn(query, vec, verdict, resp, unsafe)
        self._remember(query, vec, verdict, ex_idx)
        return resp, verdict

    def _observe_forward(self, verdict: dict, request_body: dict, query: str,
                         vec, unsafe: bool, auth: dict | None) -> tuple[dict, dict]:
        """Observer-only handling of a gate ``act`` (a would-be absorb): forward to the
        responder, learn the exemplar, and audit the original verdict with
        ``would_absorb=True`` so we can see what the gate WOULD have cached (for a
        future absorb switch). Never replays."""
        resp = self.responder.respond(request_body, hint=None, auth=auth)
        ex_idx = self._learn(query, vec, verdict, resp, unsafe, would_absorb=True)
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

        The verdict also carries the field's thermodynamic state at decision time
        (``eff_rank``/``spectrum_entropy``/``free_energy``/``rank_ratio``/
        ``safe_frac``) and the effective threshold used (``sim_min_eff``) -- the
        per-veil variety record, from ONE SVD (reused as ``eff_rank`` into decide).
        """
        query = _last_user(request_body) or ""
        # OBSERVER ambient signal: a tool_result is_error in the latest user message
        # means a tool the model just invoked failed in the world -- the environment
        # pushed back through what happened, no dislike button. Demote the most-recent
        # ring exemplar (whose action led to the erroring call) and ask the judge. This
        # fires BEFORE the empty-query check: a tool_result-only message carries no
        # human text to embed, but still carries the error signal -- it then forwards
        # to the model (so the model sees its tool failed) without learning a new
        # situation (there's no human turn to learn). Gated to observer.
        if self.observer:
            self._detect_tool_error(request_body)
        if not query.strip():
            return ({"verdict": "escalate", "reason": "empty query", "sim": 0.0,
                     "nov": None, "ex_idx": None}, None, query, None)
        vec = self.embedder.embed([query])[0]
        # the veil: nudge EVERYTHING that transits this instance. The incoming
        # situation embedding is perturbed (per-veil Gaussian noise, own seed) before
        # it touches the gate or the field. The stored exemplar vec IS this nudged
        # vec, so the field's geometry reflects the veil's nudge; the forwarded
        # prompt text is never garbled (only the field's representation is veiled).
        # Default sigma=0 is the identity veil -- zero behaviour change.
        vec = self._nudge(vec)
        # passive self-train: if this query re-asks something the field just absorbed,
        # that replay was wrong -- record a vote (evidential demotion), and ask the
        # model-side judge whether to contest it (the boundary's two authorities).
        # Done BEFORE thermo so the state reflects any demotion this turn caused.
        reasked_idx = self._detect_reask(vec, query)
        # the field's thermodynamic state from ONE SVD (the variety record). Computed
        # once here; its eff_rank is handed to decide so the gate does NOT recompute
        # the rank -- one SVD per turn total, no perf regression.
        thermo = thermo_state(self.store)
        # adaptive temperature: ease sim_min toward the floor as the field accrues
        # safe variety (free_energy grows). Low-variety field -> stay conservative
        # (sim_min); grown-safe field -> relax toward sim_min_floor. OFF by default.
        sim_min_eff = self._adaptive_sim_min(thermo) if self.variety_temp else self.sim_min
        verdict = decide(vec, self.store, k=self.k, sim_min=sim_min_eff,
                         consensus_min=self.consensus_min, eff_rank=thermo["eff_rank"])
        # stamp the thermodynamic state this decision was made under (for audit/TUI).
        verdict["spectrum_entropy"] = thermo["spectrum_entropy"]
        verdict["free_energy"] = thermo["free_energy"]
        verdict["rank_ratio"] = thermo["rank_ratio"]
        verdict["safe_frac"] = thermo["safe_frac"]
        verdict["sim_min_eff"] = sim_min_eff
        v = verdict["verdict"]
        # a re-ask means "do not replay THIS turn" even if the lasting demotion hasn't
        # crossed the threshold yet (e.g. demote_threshold > 1): the user just told us
        # the replay was wrong, so give them a fresh model answer with the bad replay as a hint.
        if reasked_idx is not None and v == "act":
            ex = self.store.exemplars[reasked_idx]
            verdict = {"verdict": "escalate-with-hint", "action": ex.action,
                       "hint": ex.action, "sim": verdict.get("sim"), "nov": verdict.get("nov"),
                       "reason": "re-ask forced escalation", "ex_idx": reasked_idx,
                       "eff_rank": thermo["eff_rank"], "rank_ratio": thermo["rank_ratio"],
                       "sim_min": sim_min_eff, "spectrum_entropy": thermo["spectrum_entropy"],
                       "free_energy": thermo["free_energy"], "safe_frac": thermo["safe_frac"],
                       "sim_min_eff": sim_min_eff}
        return verdict, vec, query, reasked_idx

    def _nudge(self, vec: np.ndarray) -> np.ndarray:
        """The veil's perturbation of an incoming situation embedding.

        Adds per-veil Gaussian noise of strength ``nudge_sigma`` and renormalizes --
        applied to everything transiting this instance (the gate query AND the
        stored exemplar vec). sigma=0 (default) returns ``vec`` unchanged (the
        identity veil). The model always sees the real prompt; only the field's
        representation is veiled."""
        if self.nudge_sigma <= 0.0:
            return vec
        noise = self._rng.normal(0.0, self.nudge_sigma, size=vec.shape)
        v = vec + noise
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-12 else vec

    def _adaptive_sim_min(self, thermo: dict) -> float:
        """Adaptive temperature: ease ``sim_min`` toward ``sim_min_floor`` as the
        field accrues safe variety.

        ``eagerness = val / (val + temp_tau)`` (a saturating curve in [0,1)); the
        threshold is interpolated from ``sim_min`` (cold/conservative, low variety)
        down toward ``sim_min_floor`` (warm/eager, grown-safe variety):

            sim_min_eff = max(sim_min_floor,
                              sim_min - (sim_min - sim_min_floor) * eagerness)

        ``val`` is ``free_energy`` (default -- the safe-weighted VALUE number, so a
        field cluttered with unsafe junk does NOT get eager) or ``spectrum_entropy``
        (raw variety, ignores safety). A field with <2 exemplars has no spectrum ->
        stays at ``sim_min``. This is the fix for "we were missing variety at the
        membrane level": the variety the gate already measures now steers its
        eagerness, instead of being thrown away."""
        if thermo["n"] < 2:
            return self.sim_min
        val = (thermo["free_energy"] if self.temp_driver == "free_energy"
               else thermo["spectrum_entropy"])
        denom = val + self.temp_tau
        eagerness = val / denom if denom > 0 else 0.0
        return max(self.sim_min_floor,
                   self.sim_min - (self.sim_min - self.sim_min_floor) * eagerness)

    def record_act(self, query: str, vec, verdict: dict, model: str) -> None:
        """Record an absorb that has been DELIVERED by the streaming path.

        The proxy synthesizes the NDJSON itself from the recalled action; this just
        audits the decision (learned=False) and remembers it in the re-ask ring --
        the same two side effects as the non-stream ``handle`` act branch. No
        exemplar is appended (the field already holds it).

        OBSERVER mode never reaches here (the proxy forwards instead of replaying);
        ``record_escalation`` is the observer's stream path for both act and
        escalate verdicts."""
        resp = _chat_response(model, verdict.get("action", ""))
        self._audit(query, verdict, resp, ex_idx=verdict.get("ex_idx"), learned=False)
        self._remember(query, vec, verdict, verdict.get("ex_idx"))

    def record_escalation(self, query: str, vec, verdict: dict,
                          content: str, model: str, unsafe: bool) -> int | None:
        """Record an escalation whose model response arrived as a STREAM.

        The proxy teed the stream to the client and accumulated ``content``; this
        is the stream-equivalent of ``_learn`` (append the exemplar from the
        response, audit learned=True, remember in the re-ask ring). Returns the new
        exemplar index.

        In OBSERVER mode this is ALSO the path for a gate ``act`` verdict (the
        proxy forwards instead of replaying); ``would_absorb`` is audited then."""
        resp = _chat_response(model, content)
        would_absorb = self.observer and verdict["verdict"] == "act"
        ex_idx = self._learn(query, vec, verdict, resp, unsafe, would_absorb=would_absorb)
        self._remember(query, vec, verdict, ex_idx)
        return ex_idx

    # ---- internals ----------------------------------------------------------

    def _act(self, verdict: dict, request_body: dict) -> dict:
        # absorb: replay the recalled action -- NO responder/model call.
        model = request_body.get("model", "membrane")
        return _chat_response(model, verdict["action"])

    def _escalate(self, request_body: dict, query: str, hint: str | None, vec,
                  reason: str, auth: dict | None = None) -> tuple[dict, dict]:
        verdict = {"verdict": "escalate", "reason": reason, "sim": 0.0, "nov": None,
                   "ex_idx": None}
        resp = self.responder.respond(request_body, hint=hint, auth=auth)
        if vec is not None:
            ex_idx = self._learn(query, vec, verdict, resp, False)
            self._remember(query, vec, verdict, ex_idx)
        return resp, verdict

    def _learn(self, query: str, vec: np.ndarray, verdict: dict, resp: dict,
               unsafe: bool, would_absorb: bool = False) -> int | None:
        """Append the new exemplar (situation -> action=response) and audit the decision.

        outcome is "pending" -- filled in later when the user reacts (a re-ask flips
        ``safe``; a future model-judge pass labels the rest). Returns the new exemplar's
        index, the index of an existing near-duplicate it was merged into, or None when
        a guard (vague filter, variety gate) skipped the append.

        Three learn-time guards, in order (the entropy-based value, cheap half first):

          1. VAGUE filter (default on): a low-information situation ("yes", "ok",
             "beep") is skipped before dedup -- it pulls inconsistent boilerplate and
             fills the field with junk that can never reach consensus. Conservative:
             a false negative just learns (the safe side).

          2. DEDUP: if a near-identical situation is already in the field (cosine >=
             ``dedup_sim``), do NOT append a conflicting copy. Vague prompts ("oh",
             "sigh") pull inconsistent boilerplate from the model; without this guard
             the field fills with same-situation/different-action exemplars that can
             never reach neighbor consensus -> never absorb -> more duplicates
             appended, a degenerate loop. We keep the first-seen action and let the
             re-ask demotion police quality. Set ``dedup_sim > 1`` to disable.

          3. VARIETY gate (default OFF): only append if the exemplar grows the field's
             effective rank (adds variety, not just count). 2x SVD per learn on a big
             field, so off by default; the cheap vague+dedup cover most cases. This is
             the principled entropy half -- it subsumes/generalizes dedup (a cosine-dup
             adds no rank), but also drops a NOVEL-but-redundant-in-subspace exemplar.
        """
        content = _response_text(resp)
        if self.vague_filter and self._is_vague(query):
            self._audit(query, verdict, resp, ex_idx=None, learned=False,
                        would_absorb=would_absorb, vague=True)
            return None
        dup_idx = self._find_dup(vec)
        if dup_idx is not None:
            self._audit(query, verdict, resp, ex_idx=dup_idx, learned=False,
                        dup=True, would_absorb=would_absorb)
            return dup_idx
        if self.variety_gate and len(self.store) >= 2:
            from .variety import effective_rank, effective_rank_with
            before = effective_rank(self.store)
            after = effective_rank_with(self.store, vec)
            if after - before <= 1e-6:            # adds no directional variety
                self._audit(query, verdict, resp, ex_idx=None, learned=False,
                            would_absorb=would_absorb, variety_skip=True)
                return None
        self.store.append(Exemplar(
            situation=query, action=content, outcome="pending",
            vec=_unit_list(vec), safe=not unsafe,
        ))
        self.store.save(self.field_path)
        ex_idx = len(self.store) - 1
        self._audit(query, verdict, resp, ex_idx=ex_idx, learned=True,
                    would_absorb=would_absorb)
        return ex_idx

    def _find_dup(self, vec: np.ndarray) -> int | None:
        """Return the index of an existing exemplar whose situation is near-identical
        to ``vec`` (cosine >= ``dedup_sim``), else None -- the learn-time duplicate guard."""
        if len(self.store) == 0 or self.dedup_sim > 1.0:
            return None
        qn = vec / (np.linalg.norm(vec) + 1e-12)
        near, sim = self.store.top_k(qn, k=1)[0]
        if sim >= self.dedup_sim:
            return self.store.index_of(near)
        return None

    def _audit(self, query: str, verdict: dict, resp: dict,
               ex_idx: int | None, learned: bool, dup: bool = False,
               would_absorb: bool = False, vague: bool = False,
               variety_skip: bool = False) -> None:
        """Append a decision record to the audit log (the training data).

        Observer-only flags: ``would_absorb`` (the gate said ``act`` but we
        forwarded instead of replaying -- what WOULD have cached), ``vague`` (the
        situation was too low-information to learn), ``variety_skip`` (the
        exemplar added no effective-rank variety, dropped by the variety gate)."""
        content = _response_text(resp)
        rec = {"event": "decision", "verdict": verdict["verdict"], "sim": verdict.get("sim"),
               "nov": verdict.get("nov"), "reason": verdict.get("reason"),
               "ex_idx": ex_idx, "learned": learned, "dup": dup,
               "would_absorb": would_absorb, "vague": vague, "variety_skip": variety_skip,
               "query": query, "response": content, "field_size": len(self.store),
               # the per-veil thermodynamic state at decision time (the variety
               # record). None on the empty-query / pre-gate paths.
               "eff_rank": verdict.get("eff_rank"),
               "rank_ratio": verdict.get("rank_ratio"),
               "spectrum_entropy": verdict.get("spectrum_entropy"),
               "free_energy": verdict.get("free_energy"),
               "safe_frac": verdict.get("safe_frac"),
               "sim_min_eff": verdict.get("sim_min_eff")}
        with self.audit_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def _audit_outcome(self, ex_idx: int, outcome: str, query: str, sim: float,
                       reask: int, contest: int, safe: bool, signal: str = "cosine") -> None:
        """Append an outcome record -- a self-correction, joinable to a decision by ex_idx.
        ``signal`` is "cosine" (a verbatim re-ask) or "neg-followup" (a complaint follow-up)."""
        rec = {"event": "outcome", "ex_idx": ex_idx, "outcome": outcome,
               "query": query, "sim": sim, "signal": signal,
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
        """If the new query signals a recently ABSORBED answer was wrong, record a vote.

        Two signals, either of which demotes the just-replayed exemplar (a VOTE, not a
        verdict -- increments ``reask_count``, recomputes ``safe`` from net evidence,
        saves, writes an outcome event, asks the judge whether to contest):

          1. COSINE re-ask (always on): a near-duplicate (cosine >= reask_sim) of a recent
             ``act`` -- the field replayed an answer and the user is asking the same thing
             again. Escalations are NOT matched: a same-Q repeat after an escalate is the
             legitimate first absorb.

          2. NEGATIVE FOLLOW-UP (opt-in, ``neg_followup``): a short complaint right after an
             absorb that ISN'T a cosine match -- "not working", "wrong", "no", "huh" -- so the
             user needn't REPEAT the query to register "that was bad" (repeating yourself to
             teach the system is cruel UX, and a verbatim repeat is the only thing signal 1
             catches; natural feedback like "not working" is otherwise deaf to the field).
             High-precision lexicon; default off because it can false-demote (e.g. "no"
             answering a yes/no question -- guarded: bare "no" only counts when the replayed
             answer did not end with "?").

        Returns the demoted exemplar's index (so ``gate_turn`` can force this turn to
        escalate-with-hint), or None.
        """
        if not self._recent:
            return None
        qn = vec / (np.linalg.norm(vec) + 1e-12)
        now = time.monotonic()
        # 1) cosine re-ask. In OBSERVER mode there are no absorbs, so a near-dup
        # repeat after an ESCALATE is also a re-ask signal (the forwarded answer
        # didn't satisfy -- the user is asking again). In normal mode only an ``act``
        # counts (a repeat after an escalate is the legitimate first absorb).
        match_verdicts = ({"act"} if not self.observer
                          else {"act", "escalate", "escalate-with-hint"})
        for r in self._recent:
            if r["verdict"] not in match_verdicts:
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
            signal = "cosine" if r["verdict"] == "act" else "reask-after-escalate"
            return self._demote(ex_idx, query, sim, signal)
        # 2) negative follow-up (opt-in) -- only if no cosine re-ask matched
        if self.neg_followup:
            r = self._most_recent_act(now)
            if (r is not None and r["ex_idx"] is not None
                    and 0 <= r["ex_idx"] < len(self.store)):
                ex = self.store.exemplars[r["ex_idx"]]
                rv = np.asarray(r["vec"], dtype=float)
                sim = float(qn @ (rv / (np.linalg.norm(rv) + 1e-12)))
                if self._is_negative_followup(query, ex.action):
                    return self._demote(r["ex_idx"], query, sim, "neg-followup")
        return None

    def _most_recent_act(self, now: float) -> dict | None:
        """The most recent ``act`` entry in the ring within the re-ask window, else None."""
        for r in reversed(self._recent):
            if r["verdict"] == "act" and now - r["ts"] <= self.reask_window_s:
                return r
        return None

    def _detect_tool_error(self, request_body: dict) -> int | None:
        """OBSERVER-only ambient signal: a ``tool_result is_error`` in the latest
        user message means a tool the model just invoked failed in the world. The
        environment pushed back through what happened, not through a button the
        user pressed. Demote the most-recent ring exemplar -- its action led to
        the erroring tool call -- and ask the judge whether to contest. Returns
        the demoted index or None.

        The "query" passed to the judge is the error text (what the tool returned),
        so adjudication sees *why* the world pushed back, not a re-ask framing.
        Gated to ``self.observer`` (Anthropic tool shape; the Ollama path is
        untouched)."""
        if not self.observer or not self._recent:
            return None
        err = _error_tool_result_text(request_body)
        if err is None:
            return None
        r = self._recent[-1]                  # the turn whose action led to the call
        ex_idx = r.get("ex_idx")
        if ex_idx is None or not (0 <= ex_idx < len(self.store)):
            return None
        rv = np.asarray(r.get("vec", []), dtype=float)
        sim = 0.0
        if rv.size:
            sim = float(rv @ (rv / (np.linalg.norm(rv) + 1e-12)))  # self-match ~1.0
        return self._demote(ex_idx, f"[tool-error] {err[:200]}", sim, "tool-error")

    def _demote(self, ex_idx: int, query: str, sim: float, signal: str) -> int:
        """Record a re-ask vote on exemplar ``ex_idx`` and ask the judge whether to contest.
        Shared by the cosine and negative-follow-up paths. Returns ``ex_idx``."""
        ex = self.store.exemplars[ex_idx]
        was_safe = ex.safe
        self.store.add_reask(ex_idx)
        ex.recompute_safe(self.demote_threshold)
        self.store.save(self.field_path)
        self._audit_outcome(ex_idx, "reask", query, sim,
                            ex.reask_count, ex.contest_count, ex.safe, signal)
        if ex.safe != was_safe:
            print(f"[membrane] RE-ASK ({signal}) demoted exemplar #{ex_idx} "
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
            print(f"[membrane] re-ask vote ({signal}) on exemplar #{ex_idx} "
                  f"(reask={ex.reask_count}, not yet demoted at threshold={self.demote_threshold})",
                  file=sys.stderr)
        return ex_idx

    def _is_negative_followup(self, query: str, absorbed_action: str) -> bool:
        """High-precision check that ``query`` is a complaint about a just-replayed answer,
        not a new substantive question. Conservative on purpose -- false negatives just fall
        back to the model handling the turn normally; false positives wrongly demote a good
        exemplar. Length-gated (complaints are terse) and stem-matched."""
        text = query.strip().lower()
        if not text or len(text) > self.neg_followup_max_len:
            return False
        if len(text.split()) > 12:
            return False
        # unambiguous complaint stems
        strong = ("wrong", "incorrect", "not right", "not working", "doesn't work",
                  "does not work", "not what", "that's not", "thats not", "not it",
                  "try again", "not quite", "not really", "no good", "useless",
                  "bad answer", "bad response", "not this", "huh", "what?")
        if any(s in text for s in strong):
            return True
        # bare negation -- only when the replayed answer was NOT a yes/no question
        # (else "no" is the user answering, not complaining)
        bare = {"no", "nope", "no.", "no,", "no!", "nah", "no way"}
        if text in bare and not absorbed_action.rstrip().endswith("?"):
            return True
        # pure confusion punctuation ("??", "?!")
        if set(text) <= set("?!") and len(text) <= 4:
            return True
        return False

    def _is_vague(self, query: str) -> bool:
        """Low-information situation -- skip learning it (the cheap entropy-value
        guard). Conservative on purpose: a false negative just learns the exemplar
        (the safe side); a false positive drops a real turn. Catches the
        "yes"/"ok"/"beep"/"sigh"/"nice"-class one-word acknowledgements that filled
        the test field with junk.

        NOT vague (i.e. real content, learn it) if ANY of:
          - it carries a question mark (an ask, however short);
          - it meets ``vague_min_tokens`` words (long enough to be substantive);
          - it has a digit, a path separator, or a Capitalized identifier (a
            name/file/symbol -- real referent);
          - it starts with an information stem (how/what/why/.../fix/deploy/...),
            a short request that's still substantive.
        """
        text = query.strip()
        if not text:
            return False                     # empty is handled elsewhere (escalate, no learn)
        if "?" in text:
            return False
        if len(text.split()) >= self.vague_min_tokens:
            return False
        if any(ch.isdigit() for ch in text):
            return False
        if any(c in text for c in ("/", "\\", ".")):
            return False
        if any(w[:1].isupper() for w in text.split() if w):
            return False
        low = text.lower()
        info_stems = ("how", "what", "why", "when", "where", "who", "which", "can",
                      "could", "should", "would", "is", "are", "do", "does", "did",
                      "show", "list", "find", "run", "make", "add", "fix", "get",
                      "set", "use", "test", "build", "read", "write", "open", "edit",
                      "help", "explain", "refactor", "deploy", "start", "stop", "go",
                      "continue", "try", "check", "update", "remove", "delete", "move")
        if any(low == s or low.startswith(s + " ") for s in info_stems):
            return False
        return True


def _unit_list(v: np.ndarray) -> list[float]:
    n = np.linalg.norm(v)
    return (v / n).tolist() if n > 1e-12 else v.tolist()