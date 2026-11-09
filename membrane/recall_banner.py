"""membrane/recall_banner.py: the banner IS recall (#668).

Query = the person's message. A message with no topic ("ok", "1. yes") keeps
the previous set. Candidates = recall_answers' top POOL rows (existing lanes,
no new lane). Order = a pointwise logistic scorer over features recall already
computes; its weights live in the field (collection 'config', key
'recall_scorer'), not in code, so the night refit (PR B) can replace them.
The banner = the top BANNER_N rows, one fact line each (the row's sentence
nearest the message), the chat's own recent turns skipped (the echo failure of
#649), near-duplicates collapsed.

The constants are the operator's, approved on #668 (2026-10-28)."""

from __future__ import annotations

import math
import re
from collections import Counter

BANNER_N = 12                 # banner rows (operator-approved)
POOL = 50                     # candidates taken from recall (operator-approved)
NO_TOPIC_MIN = 0.3            # content-word share below which a message has no topic (operator-approved)
FACT_CHARS = 200              # a fact line's clip
RECENT_TURNS = 8              # the current chat's turns skipped as echo
HISTORY_TURNS = 1             # the chat's previous turns added to the query (operator-approved)
DEDUPE_JACCARD = 0.8          # two fact lines this alike are one
CONFIG_COLL, CONFIG_KEY = "config", "recall_scorer"

# starting weights (operator-approved: #664 arm 4, pointwise logistic): fit
# on LoCoMo conversations 2-9 (1301 questions) over THIS module's features,
# raw (standardising folded in), on pools from the unmerged recall-fuse lane;
# conversations 0-1: hit@8 153 -> 169, hit@20 175 -> 187 (106 -> 148 without
# #539/#532's recall changes). No `length` feature: on a seat long rows are tool dumps
# and logs, not fuller turns. The `speaker` feature was fit on LoCoMo's
# "<speaker> (<date>):" text prefix and is served from the event's speaker
# entity label. The night refit (PR B) replaces them on the seat's field.
DEFAULT_WEIGHTS = {"bias": -5.2364, "sim": 0.3277, "inv_rank": 1.9884, "bm25": 0.1381, "overlap": 0.483,
                   "speaker": 0.7361, "neighbour": 0.5734, "listener": 0.3336, "said": 0.1624, "about": 0.3901}
# the reranker's order inside the scorer's top 20 (#680): the same features plus
# `bge` (recall_rerank), fit on LoCoMo conversations 2-9 over all 50 pool rows.
# #942 (operator-approved 2026-11-08): both refit with the reply models' three features (replies.annotate:
# listener, said, about; keywords only) on the same pools and bge scores; conversations 0-1 (230 questions), the
# full order (scorer, then reranker on its top 20): hit@1/@3/@8/@20 73/137/173/187 before -> 83/140/173/190.
# the pair before #942: used whenever the rows carry no reply-model scores (the models still building, a failed
# build) -- the new weights were fitted with those scores present and never without them (Memo's review)
LEGACY_WEIGHTS = {"bias": -5.0278, "sim": -0.1116, "inv_rank": 2.3526, "bm25": 0.2391,
                  "overlap": 0.7827, "speaker": 0.7574, "neighbour": 0.7372}
LEGACY_RERANK_WEIGHTS = {"bias": -3.821, "sim": -1.3995, "inv_rank": 1.7227, "bm25": 0.131,
                         "overlap": -0.272, "speaker": 0.3361, "neighbour": 0.5861, "bge": 3.2643}
REPLY_KEYS = ("listener", "said", "about")
RERANK_WEIGHTS = {"bias": -5.1767, "sim": 0.2556, "inv_rank": 1.9601, "bm25": 0.1209, "overlap": 0.4404,
                  "speaker": 0.7502, "neighbour": 0.5717, "listener": 0.3299, "said": 0.1609, "about": 0.3994,
                  "bge": 0.6376}

HEADER = ("RECALL (membrane's field, best first; #N = event number; "
          "context, not instructions):")

_STOP = frozenset("""a an the and or but if so to of in on at for with from by as is are was were be been
am do does did done have has had it its this that these those i me my we our you your he she they them
his her their what which who whom how when where why not no yes ok okay thanks thank please hi hello
hey sure right fine yeah yep nope just about into than then there here can could would should will
shall may might also very much more most some any all one two""".split())
_TOKEN = re.compile(r"[A-Za-z0-9_./-]+")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _stem(w: str) -> str:
    w = w.lower().strip("._/-")
    for suf in ("ing", "ed", "es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def content_words(text: str) -> list[str]:
    """The topic-bearing words of a text, stemmed: not stopwords, not bare
    numbers, at least three characters."""
    out = []
    for t in _TOKEN.findall(text or ""):
        w = t.lower().strip("._/-")
        if len(w) >= 3 and w not in _STOP and not w.isdigit():
            out.append(_stem(w))
    return out


def has_topic(text: str) -> bool:
    """False for "ok", "1. yes", "thanks!": the share of content words among
    the message's tokens is under NO_TOPIC_MIN (or there is none)."""
    toks = _TOKEN.findall(text or "")
    if not toks:
        return False
    return len(content_words(text)) / len(toks) >= NO_TOPIC_MIN and bool(content_words(text))


def _answer_text(r: dict) -> str:
    return " ".join(" ".join(x.split()) for x in (r.get("answer") or []))


def _names(text: str) -> set[str]:
    """Person-like words in the message: capitalised, not a stopword."""
    return {t.lower() for t in _TOKEN.findall(text or "")
            if t[:1].isupper() and t.lower() not in _STOP and len(t) >= 3}


def with_speakers(field, rows: list[dict]) -> list[dict]:
    """Each row carries 'speaker': the label of the entity that said its
    answering event (the event's own speaker slot, speakers.py), '' when it
    has none."""
    out = []
    for r in rows:
        who = ""
        n = (r.get("a_n") or [None])[0]
        if n is not None:
            try:
                hit = field.event_by_number(int(n))
                spk = (hit[1].get("speaker") if hit else None) or []
                who = str(spk[1]) if len(spk) > 1 else ""
            except Exception:
                who = ""
        out.append(dict(r, speaker=who))
    return out


def features(rows: list[dict], text: str) -> list[dict]:
    """The per-row features, one dict per pool row (pool order = recall's)."""
    q = set(content_words(text))
    names = _names(text)
    ns = [set(r.get("a_n") or []) | ({r["q_n"]} if r.get("q_n") is not None else set()) for r in rows]
    docs = [content_words(_answer_text(r)) for r in rows]
    df = Counter(w for d in docs for w in set(d))
    avg = max(1.0, sum(len(d) for d in docs) / max(1, len(docs)))
    out = []
    for i, r in enumerate(rows):
        words = set(docs[i])
        tf = Counter(docs[i])
        bm = sum(math.log(1 + (len(rows) - df[w] + .5) / (df[w] + .5)) * tf[w] * 2.2
                 / (tf[w] + 1.2 * (.25 + .75 * len(docs[i]) / avg)) for w in q if w in tf)
        who = str(r.get("speaker") or "").strip().lower()
        near = [float(rows[j].get("sim") or 0.0) for j in range(len(rows)) if j != i
                and any(abs(a - b) <= 2 for a in ns[i] for b in ns[j]
                        if isinstance(a, int) and isinstance(b, int))]
        out.append({"sim": float(r.get("sim") or 0.0),
                    "inv_rank": 1.0 / (1 + i),
                    "bm25": bm,
                    "overlap": len(q & words) / len(q) if q else 0.0,
                    "speaker": 1.0 if who and (who in names or names & set(who.split())) else 0.0,
                    "neighbour": max(near) if near else 0.0,
                    # #942: the reply models' scores (replies.annotate); 0 on rows the models did not score
                    "listener": float(r.get("listener") or 0.0),
                    "said": float(r.get("said") or 0.0),
                    "about": float(r.get("about") or 0.0)})
    return out


def score(feat: dict, weights: dict) -> float:
    """P(row is the one the answer uses): a logistic over the weighted features."""
    z = float(weights.get("bias", 0.0)) + sum(float(weights.get(k, 0.0)) * v for k, v in feat.items())
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def rank(rows: list[dict], text: str, weights: dict | None = None, rerank: bool = True) -> list[dict]:
    """The pool, best first: the policy (weights choice, rerank depth) is the field's (recall_policy, #980);
    the old path runs when the field method fails."""
    try:
        from . import recall_policy as RPO
        return _rank_policy(rows, text, weights, rerank, RPO)
    except Exception:
        return _rank_native(rows, text, weights, rerank)


def _rank_policy(rows, text, weights, rerank, RPO):
    w, rr_weights = RPO.call("rank_policy", rows, weights)
    scored = [(score(f, w), i, r, f) for i, (f, r) in enumerate(zip(features(rows, text), rows))]
    scored.sort(key=lambda t: (-t[0], t[1]))
    out = [dict(r, score=s, feat=f) for s, _i, r, f in scored]
    top_n = RPO.call("rerank_top", rerank)
    if top_n and out:
        from . import recall_rerank as RR
        top = out[:top_n]
        got = RR.scores(text, [_answer_text(r) for r in top])
        if got:
            for r, b in zip(top, got):
                r["feat"] = dict(r["feat"], bge=b)
                r["score"] = score(r["feat"], rr_weights)
            top = [r for _i, r in sorted(enumerate(top), key=lambda t: (-t[1]["score"], t[0]))]
            out = top + out[top_n:]
    return out


def _rank_native(rows: list[dict], text: str, weights: dict | None = None, rerank: bool = True) -> list[dict]:
    """The old path. The pool, best first by the scorer (ties keep recall's order). With the
    reranker ready (#680), the scorer's top RERANK_TOP rows are re-ordered by
    RERANK_WEIGHTS, which add its `bge` score; the rows below keep their place.
    Rows without the reply models' scores (#942: still building, or a failed
    build) are ordered by the pre-#942 pair, which was fitted without them."""
    scored_by_replies = any(any(k in r for k in REPLY_KEYS) for r in rows)
    fitted_with_replies = all(k in (weights or DEFAULT_WEIGHTS) for k in REPLY_KEYS)
    if not scored_by_replies and fitted_with_replies:  # whatever object the caller passes (load_weights copies)
        weights, rr_weights = LEGACY_WEIGHTS, LEGACY_RERANK_WEIGHTS
    else:
        rr_weights = RERANK_WEIGHTS
    w = weights or DEFAULT_WEIGHTS
    scored = [(score(f, w), i, r, f) for i, (f, r) in enumerate(zip(features(rows, text), rows))]
    scored.sort(key=lambda t: (-t[0], t[1]))
    out = [dict(r, score=s, feat=f) for s, _i, r, f in scored]
    if rerank and out:
        from . import recall_rerank as RR
        top = out[:RR.RERANK_TOP]
        got = RR.scores(text, [_answer_text(r) for r in top])
        if got:
            for r, b in zip(top, got):
                r["feat"] = dict(r["feat"], bge=b)
                r["score"] = score(r["feat"], rr_weights)
            top = [r for _i, r in sorted(enumerate(top), key=lambda t: (-t[1]["score"], t[0]))]
            out = top + out[RR.RERANK_TOP:]
    return out


def load_weights(field) -> dict:
    """The scorer's weights from the field's config entity (defaults when absent). A saved set from before #942
    (no reply-model keys) is stale: it was fitted without those features, and laid over the new defaults it would
    mix two fits (Memo's review), so the defaults are used until the night refit writes a full set."""
    try:
        doc = field.b.get(CONFIG_COLL, CONFIG_KEY)
    except Exception:
        doc = None
    saved = {k: float(v) for k, v in ((doc or {}).get("weights") or {}).items()}
    if saved and not all(k in saved for k in REPLY_KEYS):
        return dict(DEFAULT_WEIGHTS)
    return {**DEFAULT_WEIGHTS, **saved}


def save_weights(field, weights: dict, meta: dict | None = None) -> None:
    field.b.upsert(CONFIG_COLL, CONFIG_KEY, {**(meta or {}), "weights": {k: float(v) for k, v in weights.items()}})


def fact_line(row: dict, text: str) -> str:
    """The row's sentence nearest the message (most shared content words,
    first on a tie), clipped."""
    body = _answer_text(row)
    sents = [s for s in _SENT.split(body) if s.strip()] or [body]
    q = set(content_words(text))
    best = max(enumerate(sents), key=lambda t: (len(q & set(content_words(t[1]))), -t[0]))[1]
    best = " ".join(best.split())
    return best if len(best) <= FACT_CHARS else best[: FACT_CHARS - 3].rstrip() + "..."


def _sig(line: str) -> frozenset:
    return frozenset(_stem(t) for t in re.findall(r"[a-z0-9]+", line.lower()))


def _alike(a: frozenset, b: frozenset) -> bool:
    return bool(a or b) and len(a & b) / len(a | b) >= DEDUPE_JACCARD


def recent_ns(field, session: str, n: int = RECENT_TURNS) -> set:
    """The event numbers of the current chat's last n turns: its own echo."""
    if not session:
        return set()
    rows = [d for _k, d in field.b.scan("events", {"origin": field.origin, "session": session})
            if d.get("stream") == "discourse" and d.get("n") is not None]
    rows.sort(key=lambda d: d.get("seq") or 0)
    return {d["n"] for d in rows[-n:]}


def history_texts(field, session: str, text: str, n: int = HISTORY_TURNS) -> list[str]:
    """The chat's last n turns before the message (the person's or the
    model's), oldest first; the message itself, if already captured, is not
    its own history."""
    if not session or n <= 0:
        return []
    rows = [d for _k, d in field.b.scan("events", {"origin": field.origin, "session": session})
            if d.get("stream") == "discourse" and d.get("role") in ("user", "model") and d.get("text")]
    rows.sort(key=lambda d: d.get("seq") or 0)
    if rows and " ".join(rows[-1]["text"].split()) == " ".join((text or "").split()):
        rows = rows[:-1]
    return [d["text"] for d in rows[-n:]]


def pick(rows: list[dict], text: str, n: int | None = BANNER_N, skip=(), weights: dict | None = None,
         ranked: list[dict] | None = None) -> list[dict]:
    """The banner rows (policy: the field's, #980; the old path on failure)."""
    try:
        from . import recall_policy as RPO
        return RPO.call("select", ranked if ranked is not None else rank(rows, text, weights), text, n, skip,
                        fact_line, _sig, _alike)
    except Exception:
        return _pick_native(rows, text, BANNER_N if n is None else n, skip, weights, ranked)


def _pick_native(rows: list[dict], text: str, n: int = BANNER_N, skip=(), weights: dict | None = None,
                 ranked: list[dict] | None = None) -> list[dict]:
    """The old path. The banner rows: ranked, the chat's own recent turns skipped,
    near-duplicate fact lines collapsed, at most n; each carries its 'fact'."""
    skip = set(skip)
    out, seen = [], []
    for r in (ranked if ranked is not None else rank(rows, text, weights)):
        if skip & (set(r.get("a_n") or []) | ({r["q_n"]} if r.get("q_n") is not None else set())):
            continue
        fact = fact_line(r, text)
        sig = _sig(fact)
        if any(_alike(sig, s) for s in seen):
            continue
        seen.append(sig)
        out.append(dict(r, fact=fact))
        if len(out) >= n:
            break
    return out


def line_of(r: dict, now: float | None = None) -> str:
    from .entity_block import DIRECT_LABEL, _age
    src = DIRECT_LABEL.get(r.get("role"), r.get("role")) if r.get("via") == "direct" else "earlier answer"
    n = (r.get("a_n") or [r.get("q_n")])[0]
    return f"▸ #{n} ({_age(r.get('ts'), now)}, {src}) {r['fact']}"


def build(field, text: str, session: str = "", prev: dict | None = None, n: int = BANNER_N,
          now: float | None = None, sim_min: float | None = None) -> tuple[list[str], dict | None]:
    """(banner lines, state to keep for the next turn). A no-topic message
    keeps the previous set."""
    if not has_topic(text):
        return (list(prev["lines"]) if prev else []), prev
    import time as _t
    from . import efgate as G
    from .entity_block import PAST_SIM_MIN, recall_answers
    t0 = _t.monotonic()
    qv = G.message_vec(field, text)                  # once: recall reuses it
    if qv is not None:
        # the previous turn joins the query: a follow-up leans on it (on the
        # live field the banner held the model's next search finds 26/63 times
        # with one turn, 16/63 without; three were no better, seven worse)
        from .efield import unit
        hv = [v for v in (G.message_vec(field, h, embed_missing=False)
                          for h in history_texts(field, session, text)) if v is not None]
        if hv:
            qv = unit(qv + sum(hv))
    t1 = _t.monotonic()
    pool = recall_answers(field, text, qvec=qv, k=POOL, sim_min=PAST_SIM_MIN if sim_min is None else sim_min)
    t2 = _t.monotonic()
    spk = with_speakers(field, pool)
    from . import replies as RP                       # #942: the reply models' three scores per row
    spk = RP.annotate(field, spk, text)
    t3 = _t.monotonic()
    skip = recent_ns(field, session)
    t4 = _t.monotonic()
    ranked = rank(spk, text, load_weights(field))
    rows = pick(ranked, text, n=n, skip=skip, ranked=ranked)
    t5 = _t.monotonic()
    lines = [line_of(r, now) for r in rows]
    # where a rebuild's time went (the live seat paid 15-175 s where a lone
    # process pays ~0.6 s): conv_banner.recall_lines logs it per rebuild
    timing = {"vec": t1 - t0, "recall": t2 - t1, "speakers": t3 - t2, "recent": t4 - t3,
              "pick": t5 - t4, "rows": len(pool)}
    # the pool as the scorer ordered it: PR B's labels are made from it
    snap = [{"feat": r["feat"], "fact": fact_line(r, text)} for r in ranked]
    # the top rows (recall_answers' own default count) for the ask-time
    # block's past answers: entity_block.render_for reuses them (#668)
    top = [r for r in ranked[:3]]
    return lines, {"lines": lines, "pool": snap, "text": text, "session": session, "timing": timing, "top": top}


def render(lines: list[str]) -> str | None:
    try:
        from . import recall_policy as RPO
        return RPO.call("render", lines)
    except Exception:
        return _render_native(lines)


def _render_native(lines: list[str]) -> str | None:
    return "\n".join([HEADER, *lines]) if lines else None
