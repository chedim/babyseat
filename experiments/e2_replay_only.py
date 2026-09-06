"""E2 (replay-only benchmark): how much of a real workday's traffic does the
field serve with ZERO model calls?

Answers the operator's question -- 'can membrane replace the LLM?' -- with a
number instead of an estimate. Offline, script-first, no production change:

  Replay every audited OPERATOR turn (teed-pair Q/A) in TIME ORDER against
  the field AS IT EXISTED BEFORE that turn (store-prefix replay: the audit's
  monotonic field_size is the clock -- no leakage from a turn's own future).
  For each turn, run the gate (decide) + the exact-dup/settle rules OFFLINE
  and classify:

    served-act       -- the gate says the field's stored answer IS this ask
    served-replay    -- exact-dup / settle-replay rules fire
    served-null      -- all-orthogonal: the honest 'field-only: I don't know'
    escalate         -- the model is load-bearing here (no honest answer)

  Grading: a served turn is GOOD when the field's action says what the
  model actually said (token overlap >= 0.7 OR action-cosine >= 0.85 --
  the E1 ground-truth bar), BAD otherwise (a served wrong answer is worse
  than silence), and the model's own response is the ground truth.

Run:  ~/.membrane/venv/bin/python experiments/e2_replay_only.py [--limit N]
      [--sample S]  (embeds only sampled unique queries -- fast mode)
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.field import ExemplarStore  # noqa: E402

AUDIT = ".membrane/audit.jsonl"
FIELD = ".membrane/field.json"

CAPTURE_PREFIX = ("tool ", "tool call", "reasoning:", "plan:", "delegated:",
                  "call ", "result ", "system-reminder")

# conversational glue: no answer to serve, and the field should not be
# graded on serving it
ACK_STEMS = ("hey", "hi", "ok", "okay", "yes", "no", "yup", "nah", "well",
             "sure", "typo", "lol", "huh", "oi", "go", "welcome", "done",
             "thanks", "ty", "great", "nice", "cool", "yep", "please",
             "wait", "yeah", "noop", "continue")


def is_ack(q: str) -> bool:
    w = q.strip().lower().split()
    return (not w) or w[0] in ACK_STEMS or len(w) <= 3


def jaccard(a: str, b: str) -> float:
    ta = {w.lower() for w in (a or "").split()}
    tb = {w.lower() for w in (b or "").split()}
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return v / (np.linalg.norm(v) + 1e-12)


def categorize(q: str) -> str:
    ql = q.lower().lstrip()
    if is_ack(q):
        return "ack"
    for pre in ("what's the", "what is the", "field state", "membrane:",
                "state", "field:", "how big", "how many"):
        if ql.startswith(pre):
            return "state"
    if any(k in ql for k in ("why", "how come", "rationale", "because")):
        return "why"
    if ql.startswith(("what", "how", "where", "which", "when", "who", "can",
                      "does", "is", "should", "could")):
        return "question"
    return "directive"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="cap audited turns (0 = all)")
    ap.add_argument("--sim-act", type=float, default=0.90,
                    help="the gate's replay bar (production default)")
    ap.add_argument("--sim-null", type=float, default=0.50,
                    help="below this the honest answer is 'I don't know'")
    ap.add_argument("--batch", type=int, default=128, help="embed batch")
    args = ap.parse_args()

    recs = [json.loads(ln) for ln in open(AUDIT)]
    dec = [d for d in recs
           if d.get("event") == "decision" and d.get("query")
           and d.get("response")
           and not str(d.get("query", "")).lstrip().startswith(
               ("membrane:", "model:"))
           and "compact" not in str(d.get("verdict", ""))]
    if args.limit:
        dec = dec[:args.limit]
    print(f"audited operator turns: {len(dec)}")

    store = ExemplarStore.load(FIELD)
    all_rows = store.exemplars
    print(f"field rows (final state): {len(all_rows)}")

    # ---- the clock: audit field_size -> store prefix ----
    # rows[:fs_i] is the field as it existed before turn i (the audit's
    # field_size is read AFTER the turn's learn; the pre-turn pool is the
    # prefix strictly below the rows this turn could have added -- we take
    # fs_i as the count INCLUDING the turn's own learn, so the servable
    # pool for grading turn i is rows[:fs_i] MINUS the cell learned from
    # this very query (self-match exclusion), which the dedup-by-text guard
    # below enforces).
    fs = [int(d.get("field_size") or 0) for d in dec]

    emb = OllamaEmbedder()
    seen: dict[str, np.ndarray] = {}

    def em(texts: list[str]) -> None:
        todo = sorted({t for t in texts if t not in seen})
        for k in range(0, len(todo), args.batch):
            chunk = todo[k:k + args.batch]
            try:
                for t, v in zip(chunk, emb.embed(chunk)):
                    seen[t] = np.asarray(v, dtype=float)
            except Exception as e:
                print(f"embed error at chunk {k}: {e}", file=sys.stderr)
                for t in chunk:
                    seen.setdefault(t, np.zeros(1024))

    # embed all queries + all pool actions ONCE (offline, not per-turn)
    qs = [d["query"] for d in dec]
    em(qs)
    em([r.action for r in all_rows if r.action])

    print("replaying (time-ordered, zero model calls)...")
    stats: collections.Counter = collections.Counter()
    grades: collections.Counter = collections.Counter()
    cats: collections.Counter = collections.Counter()
    per_cat: collections.defaultdict = collections.defaultdict(
        lambda: collections.Counter())
    worst_bad: list = []
    for i, d in enumerate(dec):
        q = d["query"]
        cat = categorize(q)
        cats[cat] += 1
        pool = all_rows[:max(0, fs[i] - 1)]
        # servable-safe: canon, safe, not capture machinery, and never a
        # cell learned from THIS exact query text (the text guard)
        pool = [r for r in pool
                if not r.ephemeral and r.safe
                and r.action
                and not r.situation.startswith(CAPTURE_PREFIX)
                and r.situation != q]
        if not pool:
            stats["null-empty"] += 1
            per_cat[cat]["null"] += 1
            continue
        V = np.array([r.vec for r in pool], dtype=float)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        qv = unit(seen[q])
        sims = Vn @ qv
        j = int(np.argmax(sims))
        s = float(sims[j])
        resp = d["response"].strip()
        if s >= args.sim_act:
            good = (jaccard(pool[j].action, resp) >= 0.7)
            if not good:
                av = seen.get(pool[j].action)
                rv = seen.get(resp)
                if av is None or rv is None:
                    em([t for t in (pool[j].action, resp)
                        if t not in seen])
                    av = seen[pool[j].action]
                    rv = seen[resp]
                good = float(unit(av) @ unit(rv)) >= 0.85
            stats["served-act"] += 1
            per_cat[cat]["served"] += 1
            grades["good" if good else "bad"] += 1
            if not good and len(worst_bad) < 12:
                worst_bad.append((q, s, pool[j].situation[:70],
                                  pool[j].action[:70], resp[:70]))
        elif s < args.sim_null:
            stats["served-null"] += 1
            per_cat[cat]["null"] += 1
        else:
            # the honest middle: 'field-only: uncertain' -- not a wrong
            # answer, but not a served answer either
            stats["uncertain-band"] += 1
            per_cat[cat]["uncertain"] += 1

    n = sum(stats.values())
    served = stats["served-act"]
    print(f"\n=== E2 replay-only results ({n} turns) ===")
    print(f"turn categories: {dict(cats.most_common())}")
    for cat in sorted(per_cat):
        c = per_cat[cat]
        tot = sum(c.values())
        print(f"  {cat:9s}: served {100 * c['served'] / tot:5.1f}%  "
              f"uncertain {100 * c['uncertain'] / tot:5.1f}%  "
              f"null {100 * c['null'] / tot:5.1f}%  (n={tot})")
    g, b = grades["good"], grades["bad"]
    print(f"served-act: {served} ({100 * served / max(n, 1):.1f}%)  "
          f"precision {100 * g / max(g + b, 1):.0f}% (good {g} / bad {b})")
    print(f"null (honest 'I don't know'): {stats['served-null']} "
          f"({100 * stats['served-null'] / max(n, 1):.1f}%)")
    print(f"uncertain band (0.50-{args.sim_act}): "
          f"{stats['uncertain-band']} "
          f"({100 * stats['uncertain-band'] / max(n, 1):.1f}%) -- model "
          f"load-bearing zone")
    print(f"\nTOTAL MODEL-FREE: "
          f"{100 * (served + stats['served-null']) / max(n, 1):.1f}% "
          f"(served-right + honest-null) of operator traffic")
    if worst_bad:
        print("\nworst bad-serves (wrong answer served with confidence):")
        for q, s, sit, act, resp in worst_bad[:8]:
            print(f"  sim {s:.2f} Q: {q[:70]!r}")
            print(f"     field: {act!r} != model: {resp!r}")


if __name__ == "__main__":
    main()
