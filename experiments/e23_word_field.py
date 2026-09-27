"""E23 -- EVERY WORD IS AN ENTITY: the word-level field and composition.

The model under test: every word (and recurring phrase) is an entity. Its
starting state is its EVERYDAY meaning (the embedding of the bare word);
this project's sentences move it. A word is 'worth tracking' when the
project gives it a meaning of its own -- a measured quantity, not a label.

TEST 1 (word level), per term, from the sentences it occurs in:
  residual     sentence embedding minus the stream's mean (almost every
               sentence here is about membrane -- without this, every word
               would 'mean' membrane)
  coherence    mean pairwise cos of its residuals: do its sentences agree
               on a direction? (significance = coherence * sqrt(n))
  specialized  1 - cos(mean residual, prior - stream mean): does the
               project direction point AWAY from the everyday meaning?
  activity     1 - cos(first-half mean residual, second-half): did its
               meaning keep moving?
TEST 2 (composition): rebuild a sentence's embedding from its content
words -- a plain SUM or a ROLE-BOUND sum (MAP binding: word * a random
+-1 role vector for its position among the content words), from EVERYDAY
meanings or PROJECT meanings (word states from the first 40% of the
stream) -- decoded by a ridge map fitted on 40-60%, tested on 60-100%.
The order check: swap the first two content words of a sentence; how
much does mxbai notice (1 - cos), and does each model notice the same?

    PYTHONPATH=. .venv/bin/python experiments/e23_word_field.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from e18_entity_field import _SENT, embed_cached, load_stream, unit  # noqa: E402
from e20_entity_select import clean_stream, phrase_vocab  # noqa: E402
from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.slash import STOP  # noqa: E402

_WORD = re.compile(r"[A-Za-z][A-Za-z_]+")
PROBES = ["receipt lane", "autoselect", "announcer seat", "trust policy",
          "couchbase", "python", "git pull", "membrane", "field",
          "operational mode", "welcome", "much", "latest", "going", "what"]
N_ROLES = 8


def content_words(s: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(s)
            if len(w) >= 3 and w.lower() not in STOP]


def sentences_of(stream, split):
    out, seen = [], set()
    for t, m in enumerate(stream):
        for s in _SENT.split(m["text"]):
            s = " ".join(s.split())
            if 25 <= len(s) <= 400 and s.lower() not in seen and len(content_words(s)) >= 2:
                seen.add(s.lower())
                out.append({"t": t, "s": s, "head": t < split})
    return out


def ridge(Z, Y, lam):
    return np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ Y)


def cos_rows(A, B):
    return np.sum(unit(A) * unit(B), axis=1)


def word_field(stream, split, S, X, emb, label: str = ""):
    """TEST 1 on any sentence stream; returns (rows, prior, c)."""
    if label:
        print(f"\n######## {label}")
    # ---- terms: content words + recurring phrases ------------------------
    phrases = phrase_vocab(stream, split)
    occ = defaultdict(list)
    for i, x in enumerate(S):
        low = " ".join(content_words(x["s"]))
        ws = set(low.split())
        for w in ws:
            occ[w].append(i)
        for p in phrases:
            if p in low:
                occ[p].append(i)
    terms = [e for e, ix in occ.items() if len(ix) >= 4]
    P = embed_cached(emb, terms, "prior")
    prior = {e: P[i] for i, e in enumerate(terms)}
    c = unit(X.mean(axis=0))

    # ---- TEST 1 -------------------------------------------------------------
    rows = []
    for e in terms:
        ix = occ[e]
        R = X[ix] - c
        Rn = unit(R)
        n = len(ix)
        G = Rn @ Rn.T
        coh = float((G.sum() - n) / (n * (n - 1)))
        m = R.mean(axis=0)
        spec = 1.0 - float(unit(m) @ unit(prior[e] - c))
        act = None
        if n >= 6:
            h = n // 2
            act = 1.0 - float(unit(R[:h].mean(axis=0)) @ unit(R[h:].mean(axis=0)))
        rows.append({"term": e, "n": n, "coherence": coh,
                     "significance": coh * np.sqrt(n), "specialized": spec,
                     "activity": act})
    # null for coherence: random sentence sets of the same size
    rng = np.random.default_rng(23)
    null = []
    for n in (4, 8, 16, 32):
        for _ in range(200):
            ix = rng.choice(len(S), n, replace=False)
            Rn = unit(X[ix] - c)
            G = Rn @ Rn.T
            null.append((n, float((G.sum() - n) / (n * (n - 1)))))
    null_sd = float(np.std([v for _, v in null]))
    null_mu = float(np.mean([v for _, v in null]))
    print(f"\nTEST 1: {len(rows)} terms with >= 4 sentences; coherence of RANDOM "
          f"sentence sets: mean {null_mu:+.3f}, sd {null_sd:.3f}")
    coherent = [r for r in rows if r["coherence"] > null_mu + 3 * null_sd]
    print(f"   coherent (> random + 3 sd): {len(coherent)} of {len(rows)}")
    med = float(np.median([r["specialized"] for r in coherent])) if coherent else 0.5

    def show(title, rs, k=18):
        print(f"\n   {title}:")
        print("     " + ", ".join(f"{r['term']} (n{r['n']} coh {r['coherence']:.2f} "
                                   f"spec {r['specialized']:.2f})" for r in rs[:k]))

    by_sig = sorted(coherent, key=lambda r: -r["significance"])
    show("COHERENT and SPECIALIZED (the project gave it its own meaning)",
         [r for r in by_sig if r["specialized"] >= med])
    show("COHERENT, ORDINARY meaning (used as in everyday language)",
         [r for r in by_sig if r["specialized"] < med])
    show("least coherent (filler: its sentences share nothing)",
         sorted(rows, key=lambda r: r["coherence"]))
    act = sorted([r for r in coherent if r["activity"] is not None],
                 key=lambda r: -r["activity"])
    show("coherent, meaning STILL MOVING (highest activity)", act)
    show("coherent, meaning SETTLED (lowest activity)", act[::-1])
    print(f"\n   the probes (specialized median among coherent terms: {med:.2f}):")
    rix = {r["term"]: r for r in rows}
    for p in PROBES:
        r = rix.get(p)
        if r is None:
            print(f"     {p:<17} (too rare: < 4 sentences)")
            continue
        tag = "coherent" if r in coherent else "not coherent"
        a = f"{r['activity']:.2f}" if r["activity"] is not None else "-"
        print(f"     {p:<17} n {r['n']:>3}  coherence {r['coherence']:+.3f}  "
              f"specialized {r['specialized']:.2f}  activity {a:>4}  [{tag}]")

    return rows, prior, c, (null_mu, null_sd)

def main() -> None:
    emb = OllamaEmbedder(model="mxbai-embed-large")
    stream = load_stream(Path(".membrane/audit.jsonl"), 4000)
    split = int(0.6 * len(stream))
    stream, _, _ = clean_stream(stream, min_df=max(5, len(stream) // 100))
    S = sentences_of(stream, split)
    X = embed_cached(emb, [x["s"] for x in S], "sent")
    print(f"{len(S)} sentences from {len(stream)} messages", file=sys.stderr)

    rows, prior, c, (null_mu, null_sd) = word_field(stream, split, S, X, emb)
    # ---- TEST 2 -------------------------------------------------------------
    t40, t60 = int(0.4 * len(stream)), split
    words = sorted({w for x in S for w in content_words(x["s"])})
    missing = [w for w in words if w not in prior]
    if missing:
        PM = embed_cached(emb, missing, "prior-w")
        prior.update({w: PM[i] for i, w in enumerate(missing)})
    state = {}
    early = defaultdict(list)
    for i, x in enumerate(S):
        if x["t"] < t40:
            for w in set(content_words(x["s"])):
                early[w].append(i)
    for w in words:
        ix = early.get(w, [])
        # project meaning: everyday meaning moved by its early sentences
        # (the residual mean, so the stream topic does not leak in)
        state[w] = (unit(prior[w] + (X[ix] - c).mean(axis=0)) if len(ix) >= 2
                    else prior[w])
    roles = np.random.default_rng(7).choice([-1.0, 1.0], size=(N_ROLES, X.shape[1]))

    def compose(ws, table, bound):
        vs = [table[w] * (roles[min(k, N_ROLES - 1)] if bound else 1.0)
              for k, w in enumerate(ws)]
        return np.sum(vs, axis=0)

    train = [i for i, x in enumerate(S) if t40 <= x["t"] < t60]
    test = [i for i, x in enumerate(S) if x["t"] >= t60]
    print(f"\nTEST 2: composition; train {len(train)} sentences, test {len(test)}")
    models = {"sum / everyday": (prior, False), "sum / project": (state, False),
              "bound / everyday": (prior, True), "bound / project": (state, True)}
    # the order check: swap the first two content words
    swap_ix, swapped = [], []
    for i in test:
        s = S[i]["s"]
        ws = _WORD.findall(s)
        cw = [w for w in ws if len(w) >= 3 and w.lower() not in STOP]
        if len(cw) >= 3 and cw[0].lower() != cw[1].lower():
            a, b = cw[0], cw[1]
            t = re.sub(rf"\b{re.escape(a)}\b", "\x00", s, count=1)
            t = re.sub(rf"\b{re.escape(b)}\b", a, t, count=1).replace("\x00", b)
            if t != s:
                swap_ix.append(i)
                swapped.append(t)
        if len(swap_ix) >= 300:
            break
    XS = embed_cached(emb, swapped, "swap")
    mx_sens = 1.0 - cos_rows(X[swap_ix], XS)
    print(f"   mxbai itself: swapping two words moves the sentence by "
          f"{mx_sens.mean():.3f} on average (1 - cos; n={len(swap_ix)})")
    raw_sum = np.array([compose(content_words(S[i]["s"]), prior, False) for i in test])
    print(f"   plain sum of everyday meanings vs the sentence, no decoder: "
          f"cos {cos_rows(raw_sum, X[test]).mean():.3f}")
    print(f"   {'model':<18} {'rebuild cos':>11} {'order sensitivity':>18} "
          f"{'agrees with mxbai (r)':>22}")
    out = {}
    for name, (table, bound) in models.items():
        Ztr = np.array([compose(content_words(S[i]["s"]), table, bound) for i in train])
        Zte = np.array([compose(content_words(S[i]["s"]), table, bound) for i in test])
        best = None
        for lam in (1.0, 10.0, 100.0):
            W = ridge(Ztr, X[train], lam)
            sc = float(cos_rows(Zte @ W, X[test]).mean())
            if best is None or sc > best[0]:
                best = (sc, lam, W)
        sc, lam, W = best
        Zo = np.array([compose(content_words(S[i]["s"]), table, bound) for i in swap_ix])
        Zs = np.array([compose(content_words(t), table, bound) for t in swapped])
        sens = 1.0 - cos_rows(Zo @ W, Zs @ W)
        r = (float(np.corrcoef(sens, mx_sens)[0, 1])
             if sens.std() > 1e-9 else float("nan"))
        print(f"   {name:<18} {sc:>11.3f} {sens.mean():>18.3f} {r:>22.2f}"
              f"   (ridge lambda {lam:g})")
        out[name] = {"rebuild": sc, "order_sens": float(sens.mean()), "r": r}
    Path(".membrane/e23-results.json").write_text(json.dumps(
        {"test1": rows, "null": [null_mu, null_sd], "test2": out,
         "mxbai_order_sens": float(mx_sens.mean())}, indent=1))


if __name__ == "__main__":
    main()
