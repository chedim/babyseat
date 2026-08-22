"""End-to-end demo of the Membrane absorb/escalate loop WITH passive self-training
AND the live two-authority boundary (re-ask vs an automatic model-judge).

Drives the in-process Membrane runtime with a StubResponder (so it runs anywhere --
only needs local Ollama/mxbai for embeddings) through a sequence that proves the
decision loop, the training loop, AND the boundary with the model-side authority
engaging AUTOMATICALLY (no manual contest call -- the judge fires on its own):

  1. a NOVEL query      -> escalate, responder called, exemplar E0 logged
  2. REPEAT of query 1  -> ABSORB: gate recalls E0, replays it; responder NOT called
                           (first repeat, correct)
  3. REPEAT of query 1  -> RE-ASK: the field just absorbed this and the user asks again
                           -> the replay was wrong -> a VOTE (reask=1) -> E0 DEMOTED -> this
                           turn ESCALATES-WITH-HINT. THEN the LIVE JUDGE fires automatically:
                           "was my answer correct?" -> CONTESTS (its FIRST veto) -> E0
                           RESTORED (safe=true). The model checked the environment back.
  4. REPEAT of query 1  -> RE-ASK again (reask=2) -> E0 DEMOTED again -> judge fires AGAIN
                           -> CONTESTS, but this is its SECOND contest -> RECORD-ONLY (the
                           environment re-asserted despite the first contest = irreducible
                           disagreement -> environment WINS THE ACTION, demotion stands; the
                           model WINS THE RECORD, its claim is logged). This is the anti-
                           collapse rule: the judge gets ONE veto per exemplar.
  5. another NOVEL one  -> escalate, field grows
  6. REPEAT of query 2  -> ABSORB (first repeat, correct)

The judge here is a ScriptedJudge (deterministic) standing in for the live OllamaJudge
(which calls a real chat model) -- so the seam is proven without a chat model. Swapping
in OllamaJudge() is the only change for real use. The CountingResponder counts respond()
calls, so the absorb is visible: the count rises on escalations, stays flat on replays.

Run:  PYTHONPATH=. .venv/bin/python experiments/proxy_demo.py   (needs local Ollama + mxbai-embed-large)
"""

from __future__ import annotations

import os

from membrane import Membrane, StubResponder, ScriptedJudge
from membrane.review import summarize

# field + audit into the bg-job tmp dir so parallel jobs don't clobber each other
TMP = os.environ.get("CLAUDE_JOB_DIR", "/tmp") + "/tmp"


class CountingResponder:
    """Wraps a responder and counts respond() calls -- proves the absorb skips the model."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def respond(self, request_body: dict, hint):
        self.calls += 1
        return self.inner.respond(request_body, hint)


def chat(user: str, model: str = "demo") -> dict:
    return {"model": model, "stream": False,
            "messages": [{"role": "user", "content": user}]}


def main() -> None:
    field = os.path.join(TMP, "membrane-demo-field.json")
    audit = os.path.join(TMP, "membrane-demo-audit.jsonl")
    # clean slate
    for p in (field, audit):
        if os.path.exists(p):
            os.remove(p)
    os.makedirs(TMP, exist_ok=True)

    responder = CountingResponder(StubResponder())
    # consensus_min=0 for the chat use: every exemplar's action is UNIQUE response text, so
    # "top-k neighbors agree on the action" (the linter's categorical flag/ignore signal) is
    # degenerate here -- neighbors are different responses by construction. The chat gate is
    # effectively sim-primary (cosine), which is exactly the #3 finding (cosine-primary).
    # demote_threshold=1: one un-contested re-ask demotes; a contest cancels it (the boundary).
    # The judge is a ScriptedJudge (contests twice, then accepts) standing in for the live
    # OllamaJudge. Swap for OllamaJudge(model=...) to use a real chat model.
    judge = ScriptedJudge([
        "answer was correct; the re-ask was a misunderstanding",   # 1st contest -> restores
        "answer still holds; the repeated re-ask does not change that",  # 2nd -> record-only
    ])
    membrane = Membrane(responder=responder, field_path=field, audit_path=audit,
                    sim_min=0.85, consensus_min=0.0, demote_threshold=1, judge=judge)

    q1 = "how do I upsert a document in Couchbase Java SDK?"
    q2 = "what's the difference between a bucket and a scope?"

    def turn(tag, q):
        before = responder.calls
        resp, dec = membrane.handle(chat(q))
        called = responder.calls - before
        content = resp["message"]["content"]
        mark = "ABSORBED (no model call)" if called == 0 else f"escalated ({called} model call)"
        sim = dec.get("sim")
        sim_s = f"{sim:.3f}" if isinstance(sim, (int, float)) else "  -  "
        e0 = membrane.store.exemplars[0]
        print(f"[{tag:9s}] verdict={dec['verdict']:18s} sim={sim_s:>5}  "
              f"field={len(membrane.store):>2d}  responder_calls={responder.calls:>2d}  {mark}  "
              f"| E0 reask={e0.reask_count} contest={e0.contest_count} safe={e0.safe}")
        print(f"            -> {content[:80]}")

    print(f"sim_min={membrane.sim_min}  consensus_min={membrane.consensus_min} (chat: sim-primary)  "
          f"demote_threshold={membrane.demote_threshold}  judge=ScriptedJudge (live=OllamaJudge)")
    print(f"field starts at {len(membrane.store)} exemplars\n")

    turn("novel",  q1)   # 1 -> escalate, E0 logged
    turn("REPEAT", q1)   # 2 -> ABSORB (first repeat, correct)
    turn("REPEAT", q1)   # 3 -> RE-ASK demotes E0 -> judge 1st contest RESTORES -> esc-w-hint
    turn("REPEAT", q1)   # 4 -> RE-ASK demotes E0 again -> judge 2nd contest RECORD-ONLY -> esc-w-hint
    turn("novel",  q2)   # 5 -> escalate, E1 logged
    turn("REPEAT", q2)   # 6 -> ABSORB (first repeat, correct)

    n_turns = 6
    print(f"\nresponder was called {responder.calls} times for {n_turns} turns "
          f"({n_turns - responder.calls} absorbed with NO model call).")

    print("\n" + summarize(audit, field))


if __name__ == "__main__":
    main()