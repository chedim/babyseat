"""Validate the rebuilt (cosine-primary) gate on real embeddings.

Routes the same real-code workload as real_knee.py through `gate.decide`:
  - noise (more Couchbase)    -> should ACT (confident + safe)
  - borderline (other Java)   -> mixed (the acquisition gap; cosine's blind spot)
  - novel (Python/Go/SQL)     -> should ESCALATE
With one action on all field exemplars, consensus is always 1.0, so this isolates
the cosine signal -- it shows the cosine-primary decision on real data. It also
prints whether novelty is ON or OFF for this field (it should be OFF: the real
field is high-rank, so the conditional novelty veto is inert by construction).

Run:  PYTHONPATH=. python experiments/real_gate.py   (needs local Ollama + mxbai-embed-large)
"""

from __future__ import annotations

import numpy as np

from experiments.real_knee import FIELD, NOISE, BORDERLINE, NOVEL
from cortex.embed import OllamaEmbedder
from cortex.field import Exemplar, ExemplarStore
from cortex.gate import decide
from cortex.variety import effective_rank


def main() -> None:
    emb = OllamaEmbedder()
    fvecs = emb.embed(FIELD)
    wtexts = NOISE + BORDERLINE + NOVEL
    wclasses = (["noise"] * len(NOISE)) + (["borderline"] * len(BORDERLINE)) + (["novel"] * len(NOVEL))
    wvecs = emb.embed(wtexts)

    # one action on all field exemplars -> consensus always 1.0, isolating the
    # cosine signal. All safe=True -> confident matches ACT (reversibility guard
    # shown separately; this isolates the absorb/escalate decision).
    store = ExemplarStore()
    for v in fvecs:
        store.append(Exemplar("couchbase-java", "actA", "ok", v.tolist(), safe=True))

    rank_ratio = effective_rank(store) / len(store)
    print(f"field: {len(store)} exemplars  effective_rank={effective_rank(store):.2f}  "
          f"rank_ratio={rank_ratio:.2f}  -> novelty "
          f"{'ON (low-rank field)' if rank_ratio < 0.5 else 'OFF (high-rank field; conditional veto inert)'}\n")

    tallies: dict[str, dict[str, int]] = {}
    for v, cls in zip(wvecs, wclasses):
        verdict = decide(v, store)["verdict"]
        tallies.setdefault(cls, {})
        tallies[cls][verdict] = tallies[cls].get(verdict, 0) + 1

    print("== gate verdicts per class (confident+safe -> act; not-sure -> escalate) ==")
    for cls in ("noise", "borderline", "novel"):
        t = tallies.get(cls, {})
        n = sum(t.values())
        act = t.get("act", 0)
        esc = t.get("escalate", 0) + t.get("escalate-with-hint", 0)
        print(f"  {cls:11s} ({n:2d}): act={act:2d}  escalate={esc:2d}   "
              f"absorb_rate={act/max(n,1):.0%}")
    print("\n  (noise should mostly act; novel should mostly escalate; borderline is the\n"
          "   mixed band -- cosine's blind spot, the acquisition gap.)\n")

    # show the reversibility guard: if these were RISKY actions, confident matches
    # would escalate-with-hint instead of act (the field does recall but doesn't
    # pull the trigger). Demonstrate by re-asking one noise query with a risky field.
    print("== reversibility guard demo (same noise query, field marked risky) ==")
    risky_store = ExemplarStore()
    for v in fvecs:
        risky_store.append(Exemplar("couchbase-java", "commit+push", "ok", v.tolist(), safe=False))
    sample = decide(wvecs[0], risky_store)
    print(f"  confident match on a RISKY action -> {sample['verdict']} "
          f"(sim={sample['sim']:.2f}); hint='{sample.get('hint')}'")
    print("  (the field recalls the action and hands it as a HINT; it does not act --\n"
          "   the LLM escalation pulls the trigger.)")


if __name__ == "__main__":
    main()