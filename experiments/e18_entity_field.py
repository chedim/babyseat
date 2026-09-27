"""E18 -- THE ENTITY FIELD, first measurement: can a message's embedding
be rebuilt from the states of the entities it mentions -- and which way
of tracking those states (and which correction) rebuilds it best?

The design under test: each entity has a state vector; a message's
embedding is modelled as a weighted sum of its entities' states; what
the sum cannot explain (the residual) is the message's NEW information,
split among the entities as their deltas. Before building that, this
measures how far mxbai's geometry allows it.

Stream: this project's audit log (.membrane/audit.jsonl), turns in time
order, capture wrappers unwrapped ("compact note: tool edit for: <msg>
args: {...}" -> <msg>), deduped by message. Update text = message + the
head of its response.

Two questions, never mixed:
  FIT       least-squares weights chosen KNOWING the message (what the
            delta rule does at update time: decompose an observed
            message). Always paired with a NULL: the same fit using as
            many RANDOM entities -- only the gap over it is evidence.
  FORECAST  equal weights, the message unseen (a real prediction).

Tracking methods (how an entity's state moves after each update):
  static    the seed (first mention sentence), never moved
  message   state <- blend toward the WHOLE message (~ today's drift)
  delta     state <- state + its share of the fit residual
  span      state <- blend toward the SENTENCES that mention it
Adjustments: +ctx (a 'conversation so far' vector joins the fit), and
a learned correction map (ridge, fitted on the first 60% of the stream)
from the entity mix to the message -- mxbai's non-linearity, learned.

Ceilings: pairwise additivity (embed("A B") vs embed(A)+embed(B)) and
the own-spans fit (a message from its OWN mention sentences).

Detectors: specific (extract_entities: files/symbols/models/...), known
(names from membrane's entity layer), concept (frequent content words).

    PYTHONPATH=. .venv/bin/python experiments/e18_entity_field.py [--n 4000]

Caches under .membrane/ (gitignored).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, deque
from pathlib import Path

import numpy as np

from membrane.embed import OllamaEmbedder
from membrane.entities import extract_entities
from membrane.rag import strip_machinery_text
from membrane.slash import STOP
from membrane.token_timeline import tokenize

CACHE = Path(".membrane")
_WRAP = re.compile(r"^(?:compact note:\s*)?(?:tool \w+ for:|tool call for:|"
                   r"call \w+ for:|reasoning:|plan:)\s*", re.I)
_ARGS = re.compile(r"\s+(?:args:\s*\{.*|->\s*\w+\(args\).*)$", re.S)
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def unit(M) -> np.ndarray:
    M = np.asarray(M, dtype=float)
    return M / (np.linalg.norm(M, axis=-1, keepdims=True) + 1e-12)


def clean_query(q: str) -> str:
    q = " ".join(strip_machinery_text(q or "").split())
    q = _WRAP.sub("", q)
    q = _ARGS.sub("", q)
    return q.strip(" .")


def load_stream(path: Path, n: int) -> list[dict]:
    rows = []
    for line in path.open():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "decision" and r.get("query") and r.get("ts"):
            rows.append(r)
    rows.sort(key=lambda r: r["ts"])
    seen, out = set(), []
    for r in rows:
        q = clean_query(r["query"])
        if len(q) < 12 or q.lower() in seen:
            continue
        seen.add(q.lower())
        resp = " ".join(strip_machinery_text(r.get("response") or "").split())
        out.append({"ts": r["ts"], "q": q, "text": (q + "\n" + resp[:600]).strip()})
    return out[-n:]


def embed_cached(emb, texts: list[str], tag: str) -> np.ndarray:
    h = hashlib.sha256(("\x00".join(texts) + emb.model).encode()).hexdigest()[:12]
    p = CACHE / f"e18-{tag}-{h}.npy"
    if p.exists():
        return np.load(p)
    out = []
    for lo in range(0, len(texts), 64):
        out.extend(emb.embed(texts[lo:lo + 64]))
        if lo % 640 == 0:
            print(f"  [embed {tag}] {lo}/{len(texts)}", file=sys.stderr)
    M = unit(out)
    np.save(p, M)
    return M


# ---- detectors: message -> {entity_id: surface string} ----------------------

def detect_specific(msg) -> dict:
    ents = extract_entities(msg["q"], msg["text"])
    return {f"{k}:{v.lower()}": v for k, vs in ents.items() for v in vs}


def known_names(path: Path) -> dict[str, str]:
    """Phrase -> entity id, from membrane's own entity layer (the synth /
    component names), 1-4 words, no junk anchors / one-letter names."""
    docs = json.loads(path.read_text()).get("docs", {})
    out = {}
    for eid, d in docs.items():
        if not eid.startswith("membrane::") or "junk-anchor" in eid:
            continue
        name = eid.split(":")[-1].replace("-", " ").replace("_", " ").strip().lower()
        words = name.split()
        if 1 <= len(words) <= 4 and len(name) >= 5 and all(len(w) >= 3 for w in words):
            out[name] = "known:" + name
    return out


def detect_known(msg, names: dict, pat) -> dict:
    low = msg["text"].lower()
    return {names[m]: m for m in set(pat.findall(low))}


def concept_vocab(stream, lo: int = 8, hi_frac: float = 0.05) -> set[str]:
    df = Counter()
    for m in stream:
        df.update({w for w in tokenize(m["text"]) if len(w) >= 4
                   and w.isalpha() and w not in STOP})
    hi = hi_frac * len(stream)
    return {w for w, c in df.items() if lo <= c <= hi}


def detect_concept(msg, vocab: set) -> dict:
    return {f"concept:{w}": w for w in set(tokenize(msg["text"])) if w in vocab}


def span_of(text: str, surface: str) -> str:
    s = surface.lower()
    hits = [x for x in _SENT.split(text) if s in x.lower()]
    return (" ".join(hits[:2]) or text)[:300]


# ---- the fit -----------------------------------------------------------------

def fit(x, cols: np.ndarray):
    """Non-negative-clipped least squares of x on the columns; returns
    (x_hat, weights)."""
    w, *_ = np.linalg.lstsq(cols.T, x, rcond=None)
    w = np.clip(w, 0.0, None)
    if w.sum() <= 1e-12:
        w = np.ones(len(cols)) / len(cols)
    return cols.T @ w, w


def cos(a, b) -> float:
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12))


def simulate(stream, X, mentions, span_vec, split: int, seed: int = 18):
    """One chronological pass. mentions[t] = {eid: span_key}; span_vec
    maps span_key -> unit vector. Returns per-message score rows."""
    rng = random.Random(seed)
    methods = ("static", "message", "delta", "span")
    S = {m: {} for m in methods}
    ctx = deque(maxlen=3)
    centroid = np.zeros(X.shape[1])
    rows = []
    DIST.clear()
    for t, x in enumerate(X):
        ents = mentions[t]
        known = [e for e in ents if e in S["span"]]
        if known and t > 0:
            r = {"t": t, "k": len(known), "tail": t >= split,
                 "prev": cos(X[t - 1], x), "centroid": cos(centroid, x)}
            for m in methods:
                A = np.array([S[m][e] for e in known])
                xh, w = fit(x, A)
                r[f"fit:{m}"] = cos(xh, x)
                r[f"fc:{m}"] = cos(A.mean(axis=0), x)
                if m == "span":
                    pool = [e for e in S["span"] if e not in ents]
                    if len(pool) >= len(known):
                        R = np.array([S["span"][e] for e in rng.sample(pool, len(known))])
                        r["fit:null"] = cos(fit(x, R)[0], x)
                    if ctx:
                        c = unit(np.mean(ctx, axis=0))
                        r["fit:span+ctx"] = cos(fit(x, np.vstack([A, c]))[0], x)
                        r["fc:span+ctx"] = cos(A.mean(axis=0) + c, x)
                        r["z_ctx"] = (A.mean(axis=0) + c).tolist()
                    r["z"] = A.mean(axis=0).tolist()
                if m == "delta":
                    r["_delta_fit"] = (xh, w, known)
            # own-spans ceiling: the message from its OWN mention sentences
            own = np.array([span_vec[ents[e]] for e in ents])
            r["fit:own-spans"] = cos(fit(x, own)[0], x)
            rows.append(r)
        # distinguishability: each mention of an already-known entity --
        # does its sentence rank that entity first among ALL current states?
        if t >= split and len(S["span"]) > 20:
            for m in methods:
                ids = list(S[m])
                M = np.array([S[m][e] for e in ids])
                pos = {e: i for i, e in enumerate(ids)}
                for e in known:
                    sims = M @ span_vec[ents[e]]
                    rank = int((sims > sims[pos[e]]).sum())
                    DIST.setdefault(m, []).append(rank)
        # ---- updates (after scoring: predict, then learn) ----
        for e, sk in ents.items():
            sv = span_vec[sk]
            if e not in S["span"]:
                for m in methods:
                    S[m][e] = sv.copy() if m != "message" else x.copy()
                continue
            S["message"][e] = unit(0.5 * S["message"][e] + 0.5 * x)
            S["span"][e] = unit(0.5 * S["span"][e] + 0.5 * sv)
        if rows and rows[-1]["t"] == t and "_delta_fit" in rows[-1]:
            xh, w, known = rows[-1].pop("_delta_fit")
            resid = x - xh
            share = w / (w.sum() + 1e-12)     # each entity's SHARE of the
            for e, we in zip(known, share):     # residual (raw LS weights blow
                S["delta"][e] = unit(S["delta"][e] + 0.5 * we * resid)  # up)
        ctx.append(x)
        centroid = centroid + (x - centroid) / (t + 1)
    return rows


DIST: dict[str, list[int]] = {}


def ridge(Z, Y, lam: float = 1.0):
    d = Z.shape[1]
    return np.linalg.solve(Z.T @ Z + lam * np.eye(d), Z.T @ Y)


def report(rows, label: str, coverage: float, n_ents: int) -> dict:
    tail = [r for r in rows if r["tail"]]
    keys = sorted({k for r in tail for k in r
                   if ":" in k or k in ("prev", "centroid")})
    summary = {"coverage": coverage, "entities": n_ents, "scored_tail": len(tail)}
    for k in keys:
        v = [r[k] for r in tail if k in r]
        summary[k] = float(np.mean(v)) if v else None
    # the learned correction map: fit on the head's entity mixes -> messages
    head = [r for r in rows if not r["tail"] and "z" in r]
    if len(head) > 200 and tail:
        for zk, name in (("z", "fc:span+map"), ("z_ctx", "fc:span+ctx+map")):
            H = [r for r in head if zk in r]
            T = [r for r in tail if zk in r]
            if len(H) < 200 or not T:
                continue
            W = ridge(np.array([r[zk] for r in H]), np.array([r["_x"] for r in H]))
            P = np.array([r[zk] for r in T]) @ W
            summary[name] = float(np.mean([cos(p, r["_x"]) for p, r in zip(P, T)]))
    by_k = {}
    for r in tail:
        by_k.setdefault(min(r["k"], 3), []).append(r)
    summary["by_k"] = {f"k={k}{'+' if k == 3 else ''}": {
        "n": len(v),
        "fit:span": float(np.mean([r["fit:span"] for r in v])),
        "fit:null": float(np.mean([r["fit:null"] for r in v if "fit:null" in r] or [np.nan]))}
        for k, v in sorted(by_k.items())}
    print(f"\n== {label}: coverage {coverage:.0%} of messages, {n_ents} entities, "
          f"{len(tail)} scored (tail)")
    for k in ("prev", "centroid"):
        print(f"   baseline {k:<9} {summary[k]:.3f}")
    print(f"   {'method':<12} {'FIT':>6} {'FORECAST':>9}")
    for m in ("static", "message", "delta", "span", "span+ctx"):
        f, c = summary.get(f"fit:{m}"), summary.get(f"fc:{m}")
        print(f"   {m:<12} {f if f is not None else float('nan'):>6.3f} "
              f"{c if c is not None else float('nan'):>9.3f}")
    print(f"   null (random entities, same k)  FIT {summary.get('fit:null', float('nan')):.3f}")
    print(f"   ceiling: own mention sentences FIT {summary.get('fit:own-spans', float('nan')):.3f}")
    for k in ("fc:span+map", "fc:span+ctx+map"):
        if k in summary:
            print(f"   learned map  {k[3:]:<14} FORECAST {summary[k]:.3f}")
    for k, v in summary["by_k"].items():
        print(f"   {k:<5} n={v['n']:<5} fit:span {v['fit:span']:.3f}  null {v['fit:null']:.3f}")
    summary["distinct"] = {}
    for m, ranks in DIST.items():
        r = np.array(ranks)
        summary["distinct"][m] = {"n": len(r), "top1": float((r == 0).mean()),
                                  "top5": float((r < 5).mean())}
    if DIST:
        n_states = None
        print("   DISTINGUISHABLE? a new mention's sentence picks its own entity:")
        for m, v in summary["distinct"].items():
            print(f"     {m:<8} top-1 {v['top1']:.1%}  top-5 {v['top5']:.1%}  (n={v['n']})")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default=".membrane/audit.jsonl")
    ap.add_argument("--entities", default=".membrane/entities.json")
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--embed-model", default="mxbai-embed-large")
    args = ap.parse_args()

    emb = OllamaEmbedder(model=args.embed_model)
    stream = load_stream(Path(args.audit), args.n)
    print(f"stream: {len(stream)} messages", file=sys.stderr)
    X = embed_cached(emb, [m["text"] for m in stream], "msg")
    split = int(0.6 * len(stream))

    # ---- ceiling 1: pairwise additivity of mxbai -----------------------------
    rng = random.Random(18)
    pairs = [(rng.randrange(len(stream)), rng.randrange(len(stream))) for _ in range(400)]
    A_txt = [stream[i]["q"][:200] for i, _ in pairs]
    B_txt = [stream[j]["q"][:200] for _, j in pairs]
    EA = embed_cached(emb, A_txt, "pa")
    EB = embed_cached(emb, B_txt, "pb")
    EAB = embed_cached(emb, [a + " " + b for a, b in zip(A_txt, B_txt)], "pab")
    add = [cos(EA[i] + EB[i], EAB[i]) for i in range(len(pairs))]
    alone = [max(cos(EA[i], EAB[i]), cos(EB[i], EAB[i])) for i in range(len(pairs))]
    print(f"\nADDITIVITY: embed('A B') vs embed(A)+embed(B): mean cos {np.mean(add):.3f} "
          f"(vs the better single part alone {np.mean(alone):.3f})")

    names = known_names(Path(args.entities))
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len,
                                               reverse=True)) + r")\b")
    vocab = concept_vocab(stream)
    dets = {"specific": lambda m: detect_specific(m),
            "known": lambda m: detect_known(m, names, pat),
            "concept": lambda m: detect_concept(m, vocab)}
    per = {k: [f(m) for m in stream] for k, f in dets.items()}
    per["all"] = [{**a, **b, **c} for a, b, c in
                  zip(per["specific"], per["known"], per["concept"])]

    # every mention sentence, embedded once
    keys, key_of = [], {}
    for t, m in enumerate(stream):
        for e, surf in per["all"][t].items():
            sp = span_of(m["text"], surf)
            if sp not in key_of:
                key_of[sp] = len(keys)
                keys.append(sp)
    SV = embed_cached(emb, keys, "span")
    span_vec = {i: SV[i] for i in range(len(keys))}

    results = {"additivity": float(np.mean(add)), "single_part": float(np.mean(alone)),
               "n_messages": len(stream)}
    for label in ("specific", "known", "concept", "all"):
        mentions = [{e: key_of[span_of(stream[t]["text"], s)] for e, s in ents.items()}
                    for t, ents in enumerate(per[label])]
        cov = sum(1 for m in mentions if m) / len(mentions)
        n_ents = len({e for m in mentions for e in m})
        rows = simulate(stream, X, mentions, span_vec, split)
        for r in rows:
            r["_x"] = X[r["t"]]
        results[label] = report(rows, label, cov, n_ents)
        for r in rows:
            r.pop("_x", None)
            r.pop("z", None)
            r.pop("z_ctx", None)
    (CACHE / "e18-results.json").write_text(json.dumps(results, indent=1))
    print("\nwrote .membrane/e18-results.json", file=sys.stderr)


if __name__ == "__main__":
    main()
