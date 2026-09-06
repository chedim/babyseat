"""E7 (hierarchical generation probe): which realization layer clears the
serve bar WITHOUT the LLM -- and how does each piece work?

Four arms over the same uncertain-band turns (the E2/E3 contested set),
graded at the E1 bar (token overlap >= 0.7 OR action-cosine >= 0.85
against the model's actual answer):

  arm A  vanilla Markov       -- 2-gram transitions over the live actions
                                 corpus, last-tokens conditioning (control:
                                 expect topic drift)
  arm B  situation-conditioned -- transitions re-weighted by cosine to the
                                 momentum direction (session:now's signal;
                                 expect topic-locked, still no plan)
  arm C  plan + extractive    -- Level 0/1 WITHOUT Level 2: a typed plan
                                 (intent/topic/evidence/relations) realized
                                 by quoting the evidence cells (the floor)
  arm D  plan + Markov real.  -- the full hierarchy: the plan holds the
                                 structure, the Markov table realizes each
                                 planned sentence's phrasing (the test)

THE PLAN VOCAB (richer, per the operator's call -- instrumented to teach
the mechanics): intent = {explain-why, report-state, how-to, discuss,
directive-ack}, typed evidence slots (ground > user > model, the provenance
order picks the lead), discourse relations from the field's own metadata
(supersedes = contrast, why-cell = cause, position-neighbors = elaboration).
Every stage logs its intermediate: the plan, the chosen evidence, the
relation edges, the realized sentences -- so the run teaches HOW it works,
not just the score.

Run:  ~/.membrane/venv/bin/python experiments/e7_hierarchical_gen.py
      [--limit N] [--arms ABCD] [--show N]
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.entities import (cooccurrence_neighbors,  # noqa: E402
                                entity_index, query_entities)
from membrane.field import ExemplarStore  # noqa: E402

AUDIT = ".membrane/audit.jsonl"
FIELD = ".membrane/field.json"
CAPTURE_PREFIX = ("tool ", "tool call", "reasoning:", "plan:", "delegated:",
                  "call ", "result ", "system-reminder")

# the plan vocabulary: intents -> template shape
INTENTS = {
    "explain-why": ("cause-first", "evidence", "tie"),
    "report-state": ("summary", "numbers", "status"),
    "how-to": ("steps", "evidence", "note"),
    "discuss": ("stance", "evidence", "counter", "close"),
    "directive-ack": ("ack",),
}


def classify_intent(q: str) -> str:
    ql = q.lower().lstrip()
    if any(k in ql for k in ("why", "how come", "rationale", "because",
                             "reason")):
        return "explain-why"
    if ql.startswith(("what's the state", "what is the state", "field state",
                      "state", "how big", "how many", "membrane:")):
        return "report-state"
    if ql.startswith(("how do i", "how can i", "how to", "can you",
                      "could you", "please")):
        return "how-to"
    if len(ql.split()) <= 3:
        return "directive-ack"
    return "discuss"


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) > 2]


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", (text or "").lower())


def jaccard(a: str, b: str) -> float:
    ta = {w for w in tokens(a)}
    tb = {w for w in tokens(b)}
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


# ---- the Markov tables (Level 2) -------------------------------------------
class Markov:
    """2-gram transition counts over the token stream of one corpus."""

    def __init__(self, order: int = 1):
        self.order = order                 # context tokens before the next
        self.nexts: dict[tuple, dict[str, int]] = collections.defaultdict(
            collections.Counter)

    def learn(self, text: str) -> None:
        toks = tokens(text)
        for i in range(self.order, len(toks)):
            ctx = tuple(toks[i - self.order:i])
            self.nexts[ctx][toks[i]] += 1

    def sample(self, seed_tokens: list[str], n_tokens: int = 40,
               situ_vec=None, pool_index=None, rng=None) -> str:
        """Walk the table; with a situation vector, weight each candidate
        transition by its evidence cell's alignment to the situation
        (arm B/C: the topic anchor)."""
        rng = rng or random.Random(7)
        toks = [t for t in seed_tokens if t][:self.order]
        if not toks:
            return ""
        out = list(toks)
        for _ in range(n_tokens):
            ctx = tuple(out[-self.order:])
            cands = self.nexts.get(ctx)
            if not cands:
                break
            if situ_vec is not None and pool_index:
                # situation re-weighting: each candidate token's weight
                # scales by the alignment of its LIVED evidence cells to
                # the situation direction
                weights = []
                for tok, c in cands.items():
                    align = pool_index.token_situ_align(tok, situ_vec)
                    weights.append(c * (0.1 + align))
                total = sum(weights)
                if total <= 0:
                    tok = rng.choices(list(cands), weights=list(
                        cands.values()))[0]
                else:
                    tok = rng.choices(list(cands), weights=weights)[0]
            else:
                tok = rng.choices(list(cands), weights=list(
                    cands.values()))[0]
            out.append(tok)
        return " ".join(out[len(toks):])


class TokenSituIndex:
    """token -> lived cells that used it -> their alignment to a situation
    direction (lazy, cached per direction)."""

    def __init__(self, rows, sitters):
        self.tok_cells: dict[str, set[int]] = collections.defaultdict(set)
        for ri, r in enumerate(rows):
            for t in set(tokens(r.action)):
                self.tok_cells[t].add(ri)
        self.rows = rows
        self._cache: dict[bytes, dict[str, float]] = {}

    def token_situ_align(self, tok: str, situ) -> float:
        key = situ.tobytes()
        cache = self._cache.setdefault(key, {})
        if tok in cache:
            return cache[tok]
        idxs = self.tok_cells.get(tok)
        if not idxs:
            cache[tok] = 0.0
            return 0.0
        import numpy as _np
        best = 0.0
        for ri in list(idxs)[:8]:          # bounded
            v = getattr(self.rows[ri], "vec", None)
            if v is None:
                continue
            c = float(_np.asarray(v, dtype=float) @ situ)
            if c > best:
                best = c
        cache[tok] = best
        return best


# ---- the plan builder (Level 0/1) ------------------------------------------
def build_plan(q: str, qv, evidence: list, provenance_rank) -> dict:
    """Level 0: intent + typed evidence; Level 1: discourse order from the
    field's own relation metadata (provenance rank = cause-first;
    supersede links = contrast; position neighbors = elaboration)."""
    intent = classify_intent(q)
    slots = INTENTS[intent]
    ev = sorted(evidence, key=lambda r: -provenance_rank(r))
    # the counterpoint: a superseded/demoted sibling of the lead evidence,
    # when the field kept both (the contrast relation)
    counter = next((r for r in evidence
                    if not r.safe or (getattr(r, "outcome", "") or "")
                    .startswith("compacted")), None)
    return {"intent": intent, "slots": slots,
            "evidence": [r for r in ev if r is not counter],
            "counter": counter}


def realize(plan: dict, mode: str, markov: Markov, situ_vec,
            tok_index) -> str:
    """Level 2: realize the plan's slots. mode='extractive' quotes the
    evidence cells' sentences; mode='markov' realizes each slot's phrasing
    from the transition table seeded by the evidence text."""
    parts = []
    for slot in plan["slots"]:
        if slot == "counter":
            if plan.get("counter") is None:
                continue
            src = plan["counter"]
        else:
            evs = plan["evidence"]
            if not evs:
                continue
            src = evs[min(len(evs) - 1, {"cause-first": 0, "summary": 0,
                                         "steps": 0, "stance": 0,
                                         "evidence": 1, "numbers": 1,
                                         "status": 0, "ack": 0,
                                         "counter": 0, "tie": 1,
                                         "note": 1}.get(slot, 0))]
        sents = sentences(src.action or "")
        if not sents:
            continue
        if mode == "extractive":
            parts.append(sents[0])          # the lead sentence, quoted
        else:
            seed = tokens(sents[0])[:2]
            out = markov.sample(seed, n_tokens=30, situ_vec=situ_vec,
                                pool_index=tok_index)
            parts.append((sents[0][:40] + " " + out).strip())
    return " ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sim-act", type=float, default=0.90)
    ap.add_argument("--sim-null", type=float, default=0.50)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--arms", default="ABCD")
    ap.add_argument("--show", type=int, default=3)
    ap.add_argument("--sample", type=int, default=0,
                    help="spread-subsample to N turns (fast verdicts)")
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
    if args.sample:
        step = max(1, len(dec) // args.sample)
        dec = dec[::step][:args.sample]

    store = ExemplarStore.load(FIELD)
    all_rows = store.exemplars
    fs = [int(d.get("field_size") or 0) for d in dec]

    # the final corpus (all actions) trains ONE Markov table; the replay
    # prefix guard keeps the time order honest at the gather stage
    servable = [r for r in all_rows if r.action and not r.ephemeral
                and not r.situation.startswith(CAPTURE_PREFIX)]
    mk = Markov(order=1)
    for r in servable:
        mk.learn(r.action)
    print(f"markov table: {len(mk.nexts)} contexts over "
          f"{len(servable)} actions")

    emb = OllamaEmbedder()
    seen: dict[str, np.ndarray] = {}

    def em(texts: list[str]) -> None:
        todo = sorted({t for t in texts if t not in seen})
        for k in range(0, len(todo), args.batch):
            chunk = todo[k:k + args.batch]
            try:
                for t, v in zip(chunk, emb.embed(chunk)):
                    seen[t] = np.asarray(v, dtype=float)
            except Exception:
                for t in chunk:
                    seen.setdefault(t, np.zeros(1024))

    em([d["query"] for d in dec])
    em([r.action for r in servable])

    def prov_rank(r):
        src = (getattr(r, "extra", None) or {}).get("src") or "user"
        return {"ground": 3, "user": 2, "model": 1, "ingest": 1}.get(src, 2)

    rng = random.Random(11)
    inv = entity_index(all_rows)
    grades: dict[str, collections.Counter] = {
        a: collections.Counter() for a in args.arms}
    samples: dict[str, list] = {a: [] for a in args.arms}
    stage_log: list = []
    n = 0
    for i, d in enumerate(dec):
        q = d["query"]
        fs_i = max(0, fs[i] - 1)
        pool = [r for r in all_rows[:fs_i]
                if not r.ephemeral and r.safe and r.action
                and not r.situation.startswith(CAPTURE_PREFIX)
                and r.situation != q]
        if len(pool) < 20:
            continue
        V = np.array([r.vec for r in pool], dtype=float)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        qv = unit(seen[q])
        sims = Vn @ qv
        s = float(np.max(sims))
        if not (args.sim_null <= s < args.sim_act):
            continue
        n += 1
        resp = d["response"].strip()
        situ = qv                        # the momentum direction at t-1
        tok_index = TokenSituIndex(servable, None)

        # ---- Level 0/1: the plan (evidence from the prefix field) ----
        seed_ents = [k for k in query_entities(q) if k in inv][:4]
        ev_idx: set[int] = set()
        for k in seed_ents:
            for ix in inv.get(k) or []:
                if ix < fs_i:
                    ev_idx.add(ix)
            for nk, _c in cooccurrence_neighbors(all_rows, k, inv=inv,
                                                 min_links=1):
                cnt = 0
                for ix in (inv.get(nk) or []):
                    if ix < fs_i and cnt < 3:
                        ev_idx.add(ix)
                        cnt += 1
        if not ev_idx:
            # fall back to the direct-recall top-3 as evidence
            for ix in np.argsort(-sims)[:3]:
                ev_idx.add(int(ix))
        evidence = [all_rows[ix] for ix in sorted(ev_idx)
                    if ix < len(all_rows) and all_rows[ix].action]
        plan = build_plan(q, qv, evidence, prov_rank)

        # ---- Level 2: the four arms ----
        drafts = {}
        if "A" in args.arms:
            drafts["A"] = mk.sample(tokens(q)[:1], n_tokens=40, rng=rng)
        if "B" in args.arms:
            drafts["B"] = mk.sample(tokens(q)[:1], n_tokens=40,
                                    situ_vec=situ, pool_index=tok_index,
                                    rng=rng)
        if "C" in args.arms:
            drafts["C"] = realize(plan, "extractive", mk, situ, tok_index)
        if "D" in args.arms:
            drafts["D"] = realize(plan, "markov", mk, situ, tok_index)

        if n <= args.show:
            stage_log.append({
                "turn": i, "query": q[:90], "intent": plan["intent"],
                "slots": plan["slots"],
                "evidence_sits": [e.situation[:60] for e in plan["evidence"][:3]],
                "counter": (plan["counter"].situation[:60]
                            if plan.get("counter") else None),
                "drafts": {a: t[:110] for a, t in drafts.items()},
                "model_said": resp[:110],
            })

        for arm, draft in drafts.items():
            if not draft.strip():
                grades[arm]["empty"] += 1
                continue
            # the fast path: token-F1 grading (the jaccard bar catches the
            # graded-good class); the embed fallback only for near-misses
            # (jaccard in the 0.5-0.7 gray zone -- bounded, not every turn)
            ok = (jaccard(draft, resp) >= 0.7)
            if not ok:
                j = jaccard(draft, resp)
                if 0.5 <= j < 0.7:
                    em([draft, resp])
                    dv, rv = seen.get(draft), seen.get(resp)
                    if dv is not None and rv is not None \
                            and float(unit(dv) @ unit(rv)) >= 0.85:
                        ok = True
            grades[arm]["good" if ok else "bad"] += 1
            if ok and len(samples[arm]) < args.show:
                samples[arm].append((q, draft[:160], resp[:160]))

    print(f"\n=== E7 hierarchical generation ({n} uncertain-band turns, "
          f"arms {args.arms}) ===")
    for arm in args.arms:
        g = grades[arm]
        tot = g["good"] + g["bad"]
        print(f"  arm {arm}: good {g['good']} / {tot} "
              f"({100 * g['good'] / max(tot, 1):.0f}%)  "
              f"empty {g['empty']}")
    print("\n--- stage log (the mechanics, first turns) ---")
    for s in stage_log:
        print(json.dumps(s, indent=1)[:1200])
        print()
    for arm in args.arms:
        if samples[arm]:
            print(f"--- arm {arm} good-draft samples:")
            for q, draft, resp in samples[arm][:2]:
                print(f"  Q: {q[:70]!r}")
                print(f"  DRAFT: {draft!r}")
                print(f"  MODEL: {resp!r}")


if __name__ == "__main__":
    main()
