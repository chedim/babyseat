"""E1 (parked, evidence-first): the near-miss replay experiment.

Replays the audited near-miss escalations (gate sim 0.70-0.85, 92 decisions /
86 distinct queries in the live audit) against the live field and asks: would
(a) a lower sim_min or (b) context-prepended query text have flipped them to a
replay, and would that replay have served the answer the model eventually gave?

Run with the membrane venv:  ~/.membrane/venv/bin/python experiments/e1_nearmiss.py
Writes findings to stdout (script-first: no production change).
"""

from __future__ import annotations

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

# captures are context/grounding, never servable replay text; the opencode
# replay layer likewise excludes tool-heavy turns
CAPTURE_PREFIX = ("tool ", "tool call", "reasoning:", "plan:", "delegated:")

# ack/greeting stems: conversational glue, no answer to bank
ACK_STEMS = ("hey", "hi", "ok", "okay", "yes", "no", "yup", "nah", "well",
             "sure", "typo", "lol", "huh", "oi", "go", "welcome", "done",
             "thanks", "ty", "great", "nice", "cool", "yep")


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


def main() -> None:
    recs = [json.loads(ln) for ln in open(AUDIT)]
    dec = [(i, d) for i, d in enumerate(recs) if d.get("event") == "decision"]
    near = [(i, d) for i, d in dec
            if str(d.get("verdict", "")).startswith("escalate")
            and d.get("sim") is not None and 0.70 <= d["sim"] < 0.85]
    esc_total = sum(1 for _, d in dec
                    if str(d.get("verdict", "")).startswith("escalate"))
    print(f"audit: {len(dec)} decisions, {esc_total} escalations, "
          f"near-miss band (0.70-0.85): {len(near)} "
          f"({100 * len(near) / max(esc_total, 1):.0f}%)")

    store = ExemplarStore.load(FIELD)
    pool = [e for e in store.exemplars
            if not e.ephemeral and e.safe
            and not e.situation.startswith(CAPTURE_PREFIX)]
    V = np.array([e.vec for e in pool], dtype=float)
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
    print(f"servable-safe canon pool: {len(pool)} of {len(store)} rows")

    emb = OllamaEmbedder()
    seen: dict[str, np.ndarray] = {}

    def em(texts: list[str]) -> list[np.ndarray]:
        todo = sorted({t for t in texts if t not in seen})
        if todo:
            for t, v in zip(todo, emb.embed(todo)):
                seen[t] = np.asarray(v, dtype=float)
        return [seen[t] for t in texts]

    em([d["query"] for _, d in near])
    # context variant: prepend the previous text-bearing decision's query
    prev, ctx, spos = None, {}, {i for i, _ in near}
    for i, d in enumerate(recs):
        q = (d.get("query") or "").strip()
        if q:
            if i in spos:
                ctx[i] = (prev + "\n" + q) if prev else q
            prev = q
    em([ctx[i] for i, _ in near])

    excl = {d["query"] for _, d in near}   # rows learned FROM these very queries

    def best(v: np.ndarray) -> tuple[float, int]:
        vv = unit(v)
        order = np.argsort(-(Vn @ vv))
        j = next(int(x) for x in order if pool[int(x)].situation not in excl)
        return float(Vn[j] @ vv), j

    # ground truth = the model's eventual answer; "good flip" = the replayed
    # action says the same thing (token overlap >= 0.7 OR action-cosine >= 0.85)
    acache: dict[tuple, float] = {}

    def act_cos(j: int, resp: str) -> float:
        key = (j, resp[:40])
        if key not in acache:
            av = unit(em([pool[j].action])[0])
            rv = unit(em([resp])[0])
            acache[key] = float(av @ rv)
        return acache[key]

    # complaint ground truth from the outcome stream
    outs: dict[str, list] = collections.defaultdict(list)
    for o in recs:
        if o.get("event") == "outcome":
            outs[o.get("query")].append(o.get("outcome"))

    stats: collections.Counter = collections.Counter()
    for i, d in near:
        resp = (d.get("response") or "").strip()
        s_raw, j_raw = best(seen[d["query"]])
        s_ctx, j_ctx = best(seen[ctx[i]])
        for name, s, j in (("raw", s_raw, j_raw), ("ctx", s_ctx, j_ctx)):
            for thr in (0.80, 0.84):
                if s >= thr:
                    good = (jaccard(pool[j].action, resp) >= 0.7
                            or act_cos(j, resp) >= 0.85)
                    stats[(name, thr, "good" if good else "bad")] += 1
                else:
                    stats[(name, thr, "noflip")] += 1

    print("\nvariant results (all 92 near-misses):")
    for name in ("raw", "ctx"):
        for thr in (0.80, 0.84):
            g, b = stats[(name, thr, "good")], stats[(name, thr, "bad")]
            nf = stats[(name, thr, "noflip")]
            flips = g + b
            prec = f"{100 * g / flips:.0f}%" if flips else "-"
            print(f"  {name} thr={thr:.2f}: flips={flips} (good={g}, bad={b}, "
                  f"precision={prec}), no-flip={nf}")

    sub = [(i, d) for i, d in near if not is_ack(d["query"])]
    print(f"\nsubstantive subset (acks dropped): {len(sub)} of {len(near)}")
    stats2: collections.Counter = collections.Counter()
    for i, d in sub:
        resp = (d.get("response") or "").strip()
        s_raw, j_raw = best(seen[d["query"]])
        s_ctx, j_ctx = best(seen[ctx[i]])
        for name, s, j in (("raw", s_raw, j_raw), ("ctx", s_ctx, j_ctx)):
            if s >= 0.80:
                good = (jaccard(pool[j].action, resp) >= 0.7
                        or act_cos(j, resp) >= 0.85)
                stats2[(name, "good" if good else "bad")] += 1
            else:
                stats2[(name, "noflip")] += 1
    for name in ("raw", "ctx"):
        g, b = stats2[(name, "good")], stats2[(name, "bad")]
        nf = stats2[(name, "noflip")]
        flips = g + b
        prec = f"{100 * g / flips:.0f}%" if flips else "-"
        print(f"  {name}: flips={flips} (good={g}, bad={b}, "
              f"precision={prec}), no-flip={nf}")

    n_out = sum(1 for _, d in near if outs.get(d["query"]))
    print(f"\ncomplaints (outcome records) on near-miss queries: {n_out}/{len(near)}")
    print("\nfinding: replay flips in the near-miss band are almost all WRONG "
          "(different conversation beat, context-bound answers); the gate's "
          "0.85 floor and escalate-default are load-bearing. The lever is not "
          "the threshold: it is capture hygiene (machinery captures flooding "
          "the near-miss matches) -- already shipped (W2/postponed batch).")


if __name__ == "__main__":
    main()