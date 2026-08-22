"""Live end-to-end: real embeddings + a real chat responder + a REAL model-judge.

Unlike proxy_demo.py (StubResponder + ScriptedJudge -- no chat model), this wires the
boundary against LIVE Ollama models so the two authorities genuinely act:

  - embeddings: mxbai-embed-large (local)
  - responder:   a real chat model that ANSWERS the question (the model's proposal)
  - judge:       a real, INDEPENDENT chat model that ADJUDICATES a demotion
                 ("was the replayed answer actually correct?") -- the model checking
                 itself, but a fresh instance/family, not the one that answered

Sequence (a real question the model should answer correctly, then a simulated re-ask
that demotes it -- the honest test is whether the judge CONTESTS a correct answer):

  1. NOVEL q        -> escalate: real model answers, E0 logged
  2. REPEAT q       -> ABSORB: replay E0's real answer (no model call)
  3. REPEAT q       -> RE-ASK: demote E0 -> the LIVE JUDGE fires: it reads E0's answer
                      and decides CONTEST (restore -- the answer was right, the re-ask
                      was a misunderstanding) or ACCEPT (the demotion stands -- the
                      answer was wrong). This is the moment -- two real models, one as
                      proposer, one as adjudicator, the boundary live.

Models default to qwen2.5-coder:7b (responder) + granite3.3:8b (judge) -- two
independent families so the judge is not the instance that answered. Override with
RESPONDER_MODEL / JUDGE_MODEL env vars. Needs Ollama running with both + mxbai-embed-large.

Run:  PYTHONPATH=. .venv/bin/python experiments/live_judge_demo.py
"""

from __future__ import annotations

import os
import textwrap

from cortex import Cortex, OllamaChatResponder, OllamaJudge
from cortex.review import summarize

TMP = os.environ.get("CLAUDE_JOB_DIR", "/tmp") + "/tmp"
RESPONDER_MODEL = os.environ.get("RESPONDER_MODEL", "qwen2.5-coder:7b")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "granite3.3:8b")


def chat(user: str, model: str) -> dict:
    return {"model": model, "stream": False,
            "messages": [{"role": "user", "content": user}]}


def show(label: str, text: str, width: int = 76) -> None:
    print(f"  {label}:")
    for ln in textwrap.wrap(text, width=width) or ["(empty)"]:
        print(f"    {ln}")


def main() -> None:
    field = os.path.join(TMP, "cortex-live-field.json")
    audit = os.path.join(TMP, "cortex-live-audit.jsonl")
    for p in (field, audit):
        if os.path.exists(p):
            os.remove(p)
    os.makedirs(TMP, exist_ok=True)

    print(f"responder model: {RESPONDER_MODEL}  (proposes answers)")
    print(f"judge model:     {JUDGE_MODEL}  (adjudicates demotions -- independent family)")
    print(f"embedder:        mxbai-embed-large\n")

    responder = OllamaChatResponder()
    judge = OllamaJudge(model=JUDGE_MODEL, timeout=120)
    cortex = Cortex(responder=responder, field_path=field, audit_path=audit,
                    sim_min=0.85, consensus_min=0.0, demote_threshold=1, judge=judge)

    q = "how do I upsert a document in Couchbase Java SDK?"

    print("=" * 78)
    print("TURN 1 -- novel question: the responder (real model) answers; E0 logged")
    print("=" * 78)
    resp, dec = cortex.handle(chat(q, RESPONDER_MODEL))
    e0 = cortex.store.exemplars[0]
    print(f"verdict={dec['verdict']}  field={len(cortex.store)}  E0 reask={e0.reask_count} "
          f"contest={e0.contest_count} safe={e0.safe}")
    show("E0 answer (the model's proposal)", e0.action)

    print("\n" + "=" * 78)
    print("TURN 2 -- REPEAT: the gate ABSORBS (replays E0's answer, NO model call)")
    print("=" * 78)
    resp, dec = cortex.handle(chat(q, RESPONDER_MODEL))
    print(f"verdict={dec['verdict']}  field={len(cortex.store)}  E0 reask={e0.reask_count} "
          f"contest={e0.contest_count} safe={e0.safe}")
    show("replayed (E0's answer, no model call)", resp["message"]["content"])

    print("\n" + "=" * 78)
    print("TURN 3 -- RE-ASK: E0 demoted -> the LIVE JUDGE adjudicates (real model)")
    print("=" * 78)
    resp, dec = cortex.handle(chat(q, RESPONDER_MODEL))
    print(f"verdict={dec['verdict']}  field={len(cortex.store)}")
    print(f"E0 final state: reask={e0.reask_count} contest={e0.contest_count} safe={e0.safe}")
    if e0.contest_count > 0:
        effect = "RESTORED (judge vetoed the demotion -- answer was correct)" if e0.safe \
            else "DEMOTE STANDS (judge contested but record-only, or accepted)"
        print(f"  -> {effect}")
    else:
        print("  -> judge ACCEPTED the demotion (answer was wrong) -- demotion stands, no contest filed")
    show("turn-3 response (escalated-with-hint)", resp["message"]["content"])

    print("\n" + "=" * 78)
    print("REVIEW -- the two authorities, both live")
    print("=" * 78)
    print(summarize(audit, field))


if __name__ == "__main__":
    main()