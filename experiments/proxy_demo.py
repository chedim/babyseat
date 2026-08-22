"""End-to-end demo of the Cortex absorb/escalate loop WITH passive self-training
and the two-authority boundary (re-ask vs contest).

Drives the in-process Cortex runtime with a StubResponder (so it runs anywhere --
only needs local Ollama/mxbai for embeddings) through a sequence that proves the
decision loop, the training loop, AND the boundary that keeps it honest:

  1. a NOVEL query      -> escalate, responder called, exemplar E0 logged
  2. REPEAT of query 1  -> ABSORB: gate recalls E0, replays it; responder NOT called
                           (first repeat, correct)
  3. REPEAT of query 1  -> RE-ASK: the field just absorbed this and the user asks again
                           -> the replay was wrong -> a VOTE (reask_count=1) -> E0 demoted
                           (safe=False) -> this turn ESCALATES-WITH-HINT. The environment
                           has now checked the model.
  4. [model-contest]    -> the model checks the environment back: "my answer was correct;
                           that re-ask was a misunderstanding." A VOTE the other way
                           (contest_count=1) -> E0 RESTORED (safe=True). Out-of-band here
                           (no live judge yet -- the Judge seam is wired, StubJudge no-ops,
                           so the demo simulates the model-side authority by calling
                           cortex.contest directly). This is the deferred half, made visible.
  5. another NOVEL one  -> escalate, field grows
  6. REPEAT of query 2  -> ABSORB (first repeat, correct)

The CountingResponder wraps the stub and counts respond() calls, so the absorb is
visible: the count rises on escalations and stays flat on the replay. The review
summary at the end shows BOTH sides of the boundary: the re-ask vote (turn 3), the
contest (turn 4), and E0's net evidence (reask=1 contest=1 -> safe=true, restored).

Run:  PYTHONPATH=. .venv/bin/python experiments/proxy_demo.py   (needs local Ollama + mxbai-embed-large)
"""

from __future__ import annotations

import os

from cortex import Cortex, StubResponder
from cortex.review import summarize

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
    field = os.path.join(TMP, "cortex-demo-field.json")
    audit = os.path.join(TMP, "cortex-demo-audit.jsonl")
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
    cortex = Cortex(responder=responder, field_path=field, audit_path=audit,
                    sim_min=0.85, consensus_min=0.0, demote_threshold=1)

    q1 = "how do I upsert a document in Couchbase Java SDK?"
    q2 = "what's the difference between a bucket and a scope?"

    def turn(tag, q):
        before = responder.calls
        resp, dec = cortex.handle(chat(q))
        called = responder.calls - before
        content = resp["message"]["content"]
        mark = "ABSORBED (no model call)" if called == 0 else f"escalated ({called} model call)"
        sim = dec.get("sim")
        sim_s = f"{sim:.3f}" if isinstance(sim, (int, float)) else "  -  "
        print(f"[{tag:9s}] verdict={dec['verdict']:18s} sim={sim_s:>5}  "
              f"field={len(cortex.store):>2d}  responder_calls={responder.calls:>2d}  {mark}")
        print(f"            -> {content[:90]}")

    print(f"sim_min={cortex.sim_min}  consensus_min={cortex.consensus_min} (chat: sim-primary)  "
          f"demote_threshold={cortex.demote_threshold}  field starts at {len(cortex.store)} exemplars\n")

    turn("novel",  q1)                 # 1 -> escalate, E0 logged
    turn("REPEAT", q1)                 # 2 -> ABSORB (first repeat, correct)
    turn("REPEAT", q1)                 # 3 -> RE-ASK -> vote -> demote E0 -> escalate-with-hint
    # 4 -- the model-side authority (deferred live judge, simulated out-of-band):
    print("[contest  ] the model stands by exemplar #0: 'my answer was correct; the re-ask was a misunderstanding'")
    cortex.contest(0, "my answer was correct; the re-ask was a misunderstanding")
    turn("novel",  q2)                 # 5 -> escalate, E1 logged
    turn("REPEAT", q2)                 # 6 -> ABSORB (first repeat, correct)

    n_turns = 5  # handle() turns (the contest is out-of-band, not a turn)
    print(f"\nresponder was called {responder.calls} times for {n_turns} turns "
          f"({n_turns - responder.calls} absorbed with NO model call).")

    print("\n" + summarize(audit, field))


if __name__ == "__main__":
    main()