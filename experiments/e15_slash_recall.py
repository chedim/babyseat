"""E15 -- THE BOARD'S DIALECT: does slash-expanding a query recover
recall lost to a lexical mismatch, without inflating false hits?

Corpus: a real field's situations (default .membrane/field.json.migrated,
read-only), re-embedded fresh with the live embedder so every vector
comes from the same pipeline. Targets: N clean user prompts from it.
Queries: each target PARAPHRASED by a local model told to swap the
vocabulary (the mismatch the lane targets). Distractors: off-domain
questions that should find nothing (the false-hit side of the ledger).

Lanes (see membrane/slash.py):
  plain           the paraphrase as-is (today's recall)
  slash           foreign words -> field-dialect slash-groups
  slash-every     (--every) every content word slash-grouped (full Board)
  multi           the same synonyms, one swap per query, embedded APART
                  and max-merged (the RAG-Fusion-style control)
  merge           max(plain, slash) per exemplar (the advisory merge)
  gated           merge only when plain's top-1 < sim_min (else plain)
  llm-slash       (--llm) the model rewrites Board-style itself

    PYTHONPATH=. .venv/bin/python experiments/e15_slash_recall.py [--n 120] [--llm]

Caches (paraphrases, corpus embeddings) live under .membrane/ (gitignored).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import urllib.request
from pathlib import Path

import numpy as np

from membrane.embed import OllamaEmbedder
from membrane.slash import SlashVocab, merge_max

OLLAMA = "http://localhost:11434"
CACHE = Path(".membrane")

DISTRACTORS = [
    "what's a good recipe for sourdough bread", "how tall is mount everest",
    "recommend a sci-fi novel for the weekend", "how do I tie a bowline knot",
    "what is the capital of australia", "best way to clean a cast iron pan",
    "how many moons does jupiter have", "tips for training a puppy to sit",
    "explain the offside rule in football", "how to grow tomatoes on a balcony",
    "what causes the northern lights", "how long should I boil an egg",
    "translate good morning into japanese", "who painted the mona lisa",
    "how do I change a flat bicycle tire", "what's the plot of hamlet",
    "suggest a birthday gift for my mom", "how do vaccines work",
    "what is the speed of sound", "how to make cold brew coffee at home",
    "why is the sky blue", "how to fold a fitted sheet",
    "what's the difference between a crocodile and an alligator",
    "how to start running as a beginner", "what wine goes with salmon",
    "how do I remove a red wine stain", "what is a haiku",
    "how to meditate for ten minutes", "how do bees make honey",
    "plan a three day trip to lisbon",
]

PARA_PROMPT = (
    "Rewrite the message below so it means exactly the same thing but uses "
    "DIFFERENT words: replace the nouns and verbs with synonyms or other "
    "phrasings wherever you can. Keep code identifiers and file names "
    "unchanged. Output ONLY the rewritten message, nothing else.\n\n"
    "Message:\n{t}")

BOARD_PROMPT = (
    "Rewrite the message below in the style of the Board from the game "
    "Control: wherever a single word is ambiguous or could be phrased "
    "several ways, write it as 2-3 slash-joined synonyms (e.g. "
    "'fix/repair/patch'). Keep everything else the same. Output ONLY the "
    "rewritten message.\n\nMessage:\n{t}")


def _chat(model: str, prompt: str) -> str:
    body = json.dumps({"model": model, "stream": False,
                       "options": {"temperature": 0.2},
                       "messages": [{"role": "user", "content": prompt}]})
    rq = urllib.request.Request(OLLAMA + "/api/chat", data=body.encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=180) as r:
        return json.load(r)["message"]["content"].strip().strip('"')


def _cached_llm(kind: str, model: str, prompt: str, texts: list[str]):
    path = CACHE / f"e15-{kind}-{model.replace(':', '_')}.json"
    have = json.loads(path.read_text()) if path.exists() else {}
    for i, t in enumerate(texts):
        if t not in have:
            have[t] = _chat(model, prompt.format(t=t))
            if i % 10 == 0:
                print(f"  [{kind}] {i}/{len(texts)}", file=sys.stderr)
                path.write_text(json.dumps(have))
    path.write_text(json.dumps(have))
    return [_strip_preamble(have[t]) for t in texts]


def _strip_preamble(out: str) -> str:
    """Small models open with "Here is the rewritten message:" -- noise
    every lane would embed; drop that first line."""
    first, _, rest = out.partition("\n")
    if rest.strip() and first.lower().startswith(("here is", "here's")):
        return rest.strip().strip('"')
    return out


def _unit(M) -> np.ndarray:
    M = np.asarray(M, dtype=float)
    return M / (np.linalg.norm(M, axis=-1, keepdims=True) + 1e-12)


def _corpus_matrix(emb, texts: list[str]) -> np.ndarray:
    h = hashlib.sha256(("\x00".join(texts) + emb.model).encode()).hexdigest()[:12]
    path = CACHE / f"e15-corpus-{h}.npy"
    if path.exists():
        return np.load(path)
    out = []
    for lo in range(0, len(texts), 64):
        out.extend(emb.embed(texts[lo:lo + 64]))
        print(f"  [corpus] {lo}/{len(texts)}", file=sys.stderr)
    M = _unit(out)
    np.save(path, M)
    return M


def _clean_prompt(s: str) -> bool:
    low = s.lower()
    return (40 <= len(s) <= 300 and not low.startswith("tool ")
            and "<system-reminder>" not in low and "```" not in s
            and len(s.split()) >= 6)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default=".membrane/field.json.migrated")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=15)
    ap.add_argument("--embed-model", default="mxbai-embed-large")
    ap.add_argument("--para-model", default="llama3.1:8b")
    ap.add_argument("--sim-min", type=float, default=0.70)
    ap.add_argument("--tau", type=float, default=0.72,
                    help="min token cosine for a synonym to join a group")
    ap.add_argument("--k", type=int, default=2, help="synonyms per word")
    ap.add_argument("--ctx-min", type=float, default=None,
                    help="the antonym guard: a synonym joins only if the "
                         "swapped sentence keeps cos >= this (e.g. 0.94)")
    ap.add_argument("--llm", action="store_true",
                    help="also run the llm-slash lane (one model call/query)")
    ap.add_argument("--every", action="store_true",
                    help="also run slash-every (every content word grouped)")
    ap.add_argument("--show", type=int, default=8,
                    help="print this many rescued/lost examples")
    args = ap.parse_args()

    rows = json.loads(Path(args.field).read_text())
    corpus = list(dict.fromkeys(r["situation"] for r in rows
                                if (r.get("situation") or "").strip()))
    rng = random.Random(args.seed)
    targets = rng.sample([i for i, s in enumerate(corpus) if _clean_prompt(s)],
                         args.n)
    emb = OllamaEmbedder(model=args.embed_model)
    print(f"corpus {len(corpus)} situations, {len(targets)} targets",
          file=sys.stderr)

    M = _corpus_matrix(emb, corpus)
    vocab = SlashVocab(emb, corpus)
    print(f"vocab {len(vocab.words)} field words", file=sys.stderr)

    paras = _cached_llm("para", args.para_model, PARA_PROMPT,
                        [corpus[i] for i in targets])

    def scores(texts):
        return _unit(emb.embed(texts)) @ M.T          # (q, corpus)

    kw = dict(k=args.k, tau=args.tau, ctx_min=args.ctx_min)

    def build(texts: list[str], llm_kind: str | None = None) -> dict:
        """Every lane for a list of queries -> {lane: (q, corpus) scores}.
        The same builder serves targets and distractors (one ledger)."""
        L = {"plain": scores(texts)}
        L["slash"] = scores([vocab.expand(t, **kw) for t in texts])
        if args.every:
            L["slash-every"] = scores([vocab.expand(t, every=True, **kw)
                                       for t in texts])
        # multi-query: plain + each single-swap variant, embedded apart
        multi = []
        for t, row in zip(texts, L["plain"]):
            var = vocab.variants(t, **kw)
            multi.append(merge_max(row, *scores(var)) if var else row)
        L["multi"] = np.vstack(multi)
        L["merge"] = np.maximum(L["plain"], L["slash"])
        g = L["plain"].max(axis=1) < args.sim_min
        L["gated"] = np.where(g[:, None], L["merge"], L["plain"])
        if llm_kind:
            L["llm-slash"] = scores(_cached_llm(llm_kind, args.para_model,
                                                BOARD_PROMPT, texts))
        return L

    lanes = build(paras, "board" if args.llm else None)
    dlanes = build(DISTRACTORS, "board" if args.llm else None)
    slash_q = [vocab.expand(p, **kw) for p in paras]

    tgt = np.arange(len(targets)), np.array(targets)

    def ranks(S):
        return (S > S[tgt][:, None]).sum(axis=1)     # 0 = top-1

    touched = sum(1 for p, s in zip(paras, slash_q) if p != s)
    print(f"\n{len(paras)} targets | tau={args.tau} k={args.k} "
          f"ctx_min={args.ctx_min} | slash rewrote {touched} "
          f"({touched / len(paras):.0%})")
    print(f"{'lane':<12} {'hit@1':>6} {'hit@5':>6} {'MRR':>6} {'served':>7}"
          f" {'+@1':>4} {'-@1':>4} {'+@5':>4} {'-@5':>4} | {'dstr>=min':>9}"
          f" {'dstr-max':>8}")
    rp = ranks(lanes["plain"])
    for name, S in lanes.items():
        r, tsim = ranks(S), S[tgt]
        served = ((r == 0) & (tsim >= args.sim_min)).mean()
        D = dlanes[name].max(axis=1)
        print(f"{name:<12} {(r == 0).mean():>6.1%} {(r < 5).mean():>6.1%} "
              f"{(1 / (r + 1)).mean():>6.3f} {served:>7.1%}"
              f" {((rp > 0) & (r == 0)).sum():>4} {((rp == 0) & (r > 0)).sum():>4}"
              f" {((rp >= 5) & (r < 5)).sum():>4} {((rp < 5) & (r >= 5)).sum():>4}"
              f" | {(D >= args.sim_min).mean():>9.1%} {D.mean():>8.3f}")

    # the per-query story: what the slash lane rescued and what it lost
    rs = ranks(lanes["slash"])
    for label, idx in (("RESCUED @1 (plain missed, slash top-1)",
                        np.where((rp > 0) & (rs == 0))[0]),
                       ("LOST @1 (plain top-1, slash missed)",
                        np.where((rp == 0) & (rs > 0))[0])):
        print(f"\n{label}: {len(idx)}")
        for i in idx[:args.show]:
            print(f"  target: {corpus[targets[i]][:110]!r}\n"
                  f"  para:   {paras[i][:110]!r}\n"
                  f"  slash:  {slash_q[i][:110]!r}  (rank {rp[i]} -> {rs[i]})")

if __name__ == "__main__":
    main()
