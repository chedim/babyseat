"""The held-out recall benchmark -- the entity field scored like a memory system.

The field's recall claim ("who were these never-seen mentions about?") was
measured during the worldlines work (MRR 0.210 -> 0.539) but never shipped as
a re-runnable script. This module is that script's core: replay a seat's
history into a FRESH field, hold out a fraction of the mentions, then ask
``recall()`` who each held-out mention was about and score the ranked hits
against ground truth (the entity the mention was captured under).

Scoring is the standard IR family every memory framework reports -- MRR and
hit@k -- so the number sits next to Mem0/Zep/Letta's without a translation
step. The three read modes the field ships are scored side by side:

  worldline   a young entity by its mean, a mature one by its best episode
  mean        the old plain-mean read
  state       the old raw state read (the pre-worldlines floor)

The core is model-agnostic (the embedder is injected), so it unit-tests with
a deterministic fake embedder and a synthetic field, and runs against a real
field copy + Ollama in ``experiments/bench_recall.py`` (or ``membrane bench``).
"""
from __future__ import annotations

import argparse
import json

MODES = ("worldline", "mean", "state")


def score_rankings(rankings: list[list[str]], gold: list[str]) -> dict:
    """MRR + hit@k over ranked predictions. ``rankings[i]`` is the predicted
    eid list (best first) for query i; ``gold[i]`` is the correct eid.

    Reciprocal rank is 1/rank for a hit (1-based) and 0 when the gold eid is
    absent from the list. hit@k is the fraction of queries whose gold sits at
    rank <= k."""
    n = len(gold)
    if n == 0:
        return {"mrr": 0.0, "hit1": 0.0, "hit5": 0.0, "hit10": 0.0, "n": 0}
    rr_sum = 0.0
    hit1 = hit5 = hit10 = 0
    for ranking, g in zip(rankings, gold):
        rank = 0
        for i, eid in enumerate(ranking, start=1):
            if eid == g:
                rank = i
                break
        if rank:
            rr_sum += 1.0 / rank
            if rank <= 1:
                hit1 += 1
            if rank <= 5:
                hit5 += 1
            if rank <= 10:
                hit10 += 1
    return {"mrr": rr_sum / n, "hit1": hit1 / n, "hit5": hit5 / n,
            "hit10": hit10 / n, "n": n}


def benchmark_recall(field, train_events, holdout, embedder, k: int = 8,
                     modes: tuple[str, ...] = MODES, session: str = "bench") -> dict:
    """Replay ``train_events`` into the (fresh) field, then score recall on
    the held-out ``(text, gold_eid)`` pairs.

    ``field`` must be empty (the benchmark builds it so held-out mentions are
    truly never-seen); ``embedder`` is any object with ``.embed([text]) ->
    [vec]``, the SAME model the field embeds with."""
    if train_events:
        field.write_events(list(train_events), session=session)
        field.process()
    golds = [g for _text, g in holdout]
    per_mode: dict[str, list[list[str]]] = {m: [] for m in modes}
    for text, _gold in holdout:
        vec = embedder.embed([text])[0]
        for m in modes:
            ranking = [eid for eid, _s in field.recall(vec, k=k, by=m)]
            per_mode[m].append(ranking)
    return {m: score_rankings(per_mode[m], golds) for m in modes}


def discourse_pairs(field) -> list[tuple[str, str]]:
    """(text, eid) ground-truth pairs from the field's OWN deltas: every
    discourse mention that moved an entity, with the entity it moved. This is
    the authoritative "who was this mention about" -- prose mentions resolve
    their subject here, not in the event's ``subject`` field (which prose
    leaves empty).

    The noise is skipped: the event echo (``e:``), the speaker (``person:``),
    the word index (``w:``/``word:``) and the seat (``seat:``) -- none of
    these is what a mention is *about*."""
    pairs: list[tuple[str, str]] = []
    for _k, d in field.b.scan("entity_deltas", {"origin": field.origin},
                              order=("seq", "sidx")):
        if d.get("stream") != "discourse":
            continue
        eid = d.get("eid")
        evkey = d.get("event")
        if not eid or not evkey:
            continue
        if eid.startswith(("e:", "w:", "word:", "person:", "seat:")):
            continue
        try:
            ev = field.b.get("events", evkey)
        except Exception:
            ev = None
        text = ((ev or {}).get("text") or "").strip()
        if text:
            pairs.append((text, eid))
    return pairs


def render_benchmark(scores: dict, k: int = 8, pairs: int = 0) -> str:
    lines = [f"recall benchmark ({pairs} held-out mentions, top-k {k}):",
             f"  {'mode':10s} {'MRR':>7s} {'hit@1':>7s} {'hit@5':>7s} {'hit@10':>7s}   n"]
    for m, s in scores.items():
        lines.append(f"  {m:10s} {s['mrr']:7.4f} {s['hit1']:7.4f} "
                     f"{s['hit5']:7.4f} {s['hit10']:7.4f}   {s['n']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="membrane bench",
                                 description="held-out recall benchmark for the entity field")
    ap.add_argument("--field", default=".membrane/field",
                    help="field store path (a COPY of the live field)")
    ap.add_argument("--embed-model", default=None,
                    help="embedding model (default: the embed.py default)")
    ap.add_argument("--hold-out", type=float, default=0.2,
                    help="fraction of mentions held out (0 < x < 1)")
    ap.add_argument("--k", type=int, default=8, help="top-k for recall")
    ap.add_argument("--modes", default=",".join(MODES),
                    help="comma-separated read modes to score")
    ap.add_argument("--pairs", default=None,
                    help="a JSONL of {\"text\",\"eid\"} pairs instead of --field")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args(argv)

    from .efield import EntityField
    from .embed import OllamaEmbedder
    from . import streams as ST

    modes = tuple(m for m in args.modes.split(",") if m)
    embedder = OllamaEmbedder(model=args.embed_model) if args.embed_model \
        else OllamaEmbedder()

    if args.pairs:
        pairs = [tuple(x) for x in (json.loads(l) for l in open(args.pairs))
                 if isinstance(x, dict)]
        pairs = [(x[0], x[1]) for x in pairs]
        field = None
    else:
        from .efstore import open_backend
        backend = open_backend(args.field)
        field = EntityField(backend, embedder, args.field)
        pairs = discourse_pairs(field)

    if not pairs:
        print("no discourse mention pairs found -- run against a populated "
              "field copy, or pass --pairs", file=__import__("sys").stderr)
        return 1

    cut = max(1, int(len(pairs) * args.hold_out))
    train, holdout = pairs[:-cut], pairs[-cut:]

    if field is None:
        from .efstore import MemBackend
        field = EntityField(MemBackend(), embedder, "bench")
        train_events = [ST.Event(stream="discourse", role="user", text=t,
                                 subject=g) for t, g in train]
    else:
        # a fresh field so held-out mentions are never-seen; replay the train
        # pairs as subject-tagged discourse events
        from .efstore import MemBackend
        field = EntityField(MemBackend(), embedder, "bench")
        train_events = [ST.Event(stream="discourse", role="user", text=t,
                                 subject=g) for t, g in train]

    scores = benchmark_recall(field, train_events, holdout, embedder,
                              k=args.k, modes=modes)
    if args.json:
        print(json.dumps(scores, indent=2))
    else:
        print(render_benchmark(scores, k=args.k, pairs=len(holdout)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
