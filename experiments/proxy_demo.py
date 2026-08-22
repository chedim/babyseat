"""End-to-end demo of the Cortex absorb/escalate loop WITH passive self-training.

Drives the in-process Cortex runtime with a StubResponder (so it runs anywhere --
only needs local Ollama/mxbai for embeddings) through a sequence that proves both
the decision loop AND the training loop:

  1. a NOVEL query      -> escalate, responder called, exemplar E0 logged to the field
  2. REPEAT of query 1  -> ABSORB: the gate recalls E0 and replays its action; the
                           responder is NOT called (first repeat, correct)
  3. REPEAT of query 1  -> RE-ASK: the field just absorbed this and the user is asking
                           again -> the replay was wrong -> flip E0 safe=False -> this
                           turn ESCALATES-WITH-HINT (the self-train; no more bad replay)
  4. another NOVEL one  -> escalate, field grows
  5. off-field (Haskell) -> escalate (clearly novel)
  6. REPEAT of query 4  -> ABSORB (first repeat, correct)

The CountingResponder wraps the stub and counts respond() calls, so the absorb is
visible: the count rises on escalations and stays flat on the replay. The re-ask on
turn 3 flips E0's safe flag (visible in the review summary at the end) -- that is the
training loop closing itself with no user labor beyond asking again, which the user
would do anyway.

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
    cortex = Cortex(responder=responder, field_path=field, audit_path=audit,
                    sim_min=0.85, consensus_min=0.0)

    q1 = "how do I upsert a document in Couchbase Java SDK?"
    q2 = "what's the difference between a bucket and a scope?"
    seq = [
        ("novel",     q1),                  # 1 -> escalate, E0 logged
        ("REPEAT",    q1),                  # 2 -> ABSORB (first repeat, correct)
        ("REPEAT",    q1),                  # 3 -> RE-ASK -> flip E0 -> escalate-with-hint
        ("novel",     q2),                  # 4 -> escalate
        ("off-field", "explain quicksort in Haskell"),  # 5 -> escalate
        ("REPEAT",    q2),                  # 6 -> ABSORB (first repeat, correct)
    ]

    print(f"sim_min={cortex.sim_min}  consensus_min={cortex.consensus_min} (chat: sim-primary)  "
          f"field starts at {len(cortex.store)} exemplars\n")
    for tag, q in seq:
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
    print(f"\nresponder was called {responder.calls} times for {len(seq)} turns "
          f"({len(seq) - responder.calls} absorbed with NO model call).")

    print("\n" + summarize(audit, field))


if __name__ == "__main__":
    main()