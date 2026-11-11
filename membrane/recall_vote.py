"""THE VOTE (0.7.52): the best recall of 2026-11-07 (bench longmemeval/full_offline.py, the fitted vote) in the
product's one ranking path (recall_banner.rank: the banner and /membrane/ask).

Five voters each fill a ballot of the field's events (the WHOLE field: every chat, every person, tool outputs), cut
to their top DEPTH; Borda: points w x (DEPTH - i), voters in alphabetical order, a stable sort (a tie keeps the
order first seen):
  embedder            the direct lane's own top 20 (entity_block.direct_answers, sim_min 0)
  entity recall       the field things nearest the question (field.recall k=8, no hop), then the events by
                      sum(sim x how far the event moved the thing)
  instance histories  instances.vote: the tense rule's instances + the person / place ballots + current recall
  move log            movelog.settle (one round, the turn before each event held)
  word lane           the word lane's own top 20 (entity_block.word_answers, tool outputs allowed)
The learned map (a ridge map fitted on LoCoMo's gold answers) and the latest-mentions voter (weight 0) are not
here: no training ships (the operator 2026-11-11).

A voter that fails is left out of that ask (logged once); spaCy missing leaves the instance histories out.
MEMBRANE_VOTE=0 turns the vote off (the scorer's order, as before 0.7.52).
"""
from __future__ import annotations

import logging
import os

DEPTH = 20
# the mean of the two held-out folds' fitted weights (qgraph/full/offline_all.json), the learned map left out;
# approved by the operator 2026-11-11
VOTE_WEIGHTS = {"embedder": 0.125, "entity recall": 0.75, "instance histories": 3.0, "move log": 2.0,
                "word lane": 0.125}
ENTITY_K = 8               # the things entity recall reads (the bench's k=8)
_LOG = logging.getLogger("membrane.recall_vote")
_SAID: set = set()


def enabled() -> bool:
    return os.environ.get("MEMBRANE_VOTE", "1").strip().lower() not in ("0", "off", "false", "no")


def _once(msg: str) -> None:
    if msg not in _SAID:
        _SAID.add(msg)
        _LOG.warning(msg)


def borda(lists: dict, weights: dict) -> list:
    """{voter: [item, ...]} -> items best first: points w x (DEPTH - i) over each voter's top DEPTH."""
    pts: dict = {}
    for name in sorted(lists):
        w = float(weights.get(name, 0.0))
        for i, d in enumerate(lists[name][:DEPTH]):
            pts[d] = pts.get(d, 0.0) + w * (DEPTH - i)
    return sorted(pts, key=lambda d: -pts[d])


def points(lists: dict, weights: dict) -> dict:
    pts: dict = {}
    for name in sorted(lists):
        w = float(weights.get(name, 0.0))
        for i, d in enumerate(lists[name][:DEPTH]):
            pts[d] = pts.get(d, 0.0) + w * (DEPTH - i)
    return pts


def _n_of(field, keys: list) -> list:
    docs = field.b.get_multi("events", list(keys))
    out = []
    for k in keys:
        n = (docs.get(k) or {}).get("n")
        if n is not None and int(n) not in out:
            out.append(int(n))
    return out


def entity_recall(field, text: str, qvec=None) -> list:
    """Event ns: the things nearest the question, then the events that moved them most (sum sim x move)."""
    from . import efgate as G
    from . import movelog as ML
    qv = G.message_vec(field, text) if qvec is None else qvec
    if qv is None:
        return []
    ml = ML.of(field)
    score: dict = {}
    for e, s in field.recall(qv, k=ENTITY_K, hop=False):
        for k in ml.by_thing.get(e, ()):
            score[k] = score.get(k, 0.0) + float(s) * ml.moves[k][e]
    keys = sorted(score, key=lambda k: -score[k])[:DEPTH]
    return _n_of(field, keys)


def move_log(field, text: str) -> list:
    from . import movelog as ML
    return _n_of(field, [k for k, _s in ML.settle(field, text, rounds=1, neighbors=True)[:DEPTH]])


def _first_n(r: dict):
    a = r.get("a_n") or []
    return int(a[0]) if a and a[0] is not None else None


def ballots(field, current: list, text: str, qvec=None, rows: dict | None = None) -> dict:
    """{voter: [event n, ...]}; `rows` (n -> row) collects the lanes' own rows for the events they found."""
    from . import entity_block as EB
    from . import instances as IN
    rows = {} if rows is None else rows
    cur = [n for n in (_first_n(r) for r in current) if n is not None]
    out: dict = {}

    def lane(name, fn):
        try:
            got = fn()
        except Exception as e:
            _once(f"vote: the {name} voter failed ({type(e).__name__}: {e}); left out")
            return
        out[name] = got

    def emb():
        rs = EB.direct_answers(field, text, qvec, k=DEPTH, sim_min=0.0) if qvec is not None else []
        for r in rs:
            rows.setdefault(_first_n(r), r)
        return [n for n in (_first_n(r) for r in rs) if n is not None]

    def words():
        rs = EB.word_answers(field, text, qvec, k=DEPTH, tools_ok=True)
        for r in rs:
            rows.setdefault(_first_n(r), r)
        return [n for n in (_first_n(r) for r in rs) if n is not None]
    lane("embedder", emb)
    lane("entity recall", lambda: entity_recall(field, text, qvec))
    if IN.nlp() is not None and IN.store_of(field) is not None:
        lane("instance histories", lambda: IN.vote(field, text, cur))
    elif IN.nlp() is None:
        _once("vote: spaCy is not available; the instance histories voter is left out")
    lane("move log", lambda: move_log(field, text))
    lane("word lane", words)
    return out


def _row_for(field, n: int, qvec) -> dict | None:
    """A recall row for an event a voter found that the pool did not hold (the entity_block row shape)."""
    from . import entity_block as EB
    hit = field.event_by_number(n)
    if hit is None:
        return None
    key, d = hit
    if d.get("origin") != field.origin or d.get("status") == "forgotten":
        return None
    if d.get("stream") == "observation":
        if d.get("outcome") == "call-only":
            return None
        role, refs = "tool", list(d.get("chunks") or [])
    elif d.get("role") in EB.DIRECT_ROLES and d.get("status") == "applied":
        role, refs = d["role"], list(d.get("sids") or [])
    else:
        return None
    row = EB._word_row(field, key, d, role, refs, qvec, 3, "vote", [])
    if row is not None:
        row["how"] = ["vote"]
    return row


def vote(field, pool: list, text: str, qvec=None) -> list:
    """The pool (rows in the scorer's order: current recall) re-ordered by the vote; events a voter found outside
    the pool join it; pool rows no voter holds keep their order after the voted ones. Each row carries `vote`
    (its Borda points; 0 = not voted)."""
    from . import efgate as G
    if qvec is None:
        qvec = G.message_vec(field, text)
    rows: dict = {}
    for r in pool:
        n = _first_n(r)
        if n is not None and n not in rows:
            rows[n] = r
    extra: dict = {}
    lists = ballots(field, pool, text, qvec, extra)
    pts = points(lists, VOTE_WEIGHTS)
    order = sorted(pts, key=lambda d: -pts[d])
    out, seen = [], set()
    for n in order:
        r = rows.get(n) or extra.get(n)
        if r is None:
            try:
                r = _row_for(field, n, qvec)
            except Exception:
                r = None
        if r is None:
            continue
        out.append(dict(r, vote=round(pts[n], 3)))
        seen.add(n)
    for r in pool:
        n = _first_n(r)
        if n not in seen:
            out.append(dict(r, vote=0.0))
            seen.add(n)
    return out


def warm(field, stop=None) -> None:
    """At boot, after the field is up (opencode.py): the move log loads from the store, the instance parser
    attaches (every new event is read once, as it lands), and the events from before 0.7.52 are backfilled --
    all on one background thread, never a turn."""
    import threading

    def run():
        from . import instances as IN
        from . import movelog as ML
        try:
            ml = ML.of(field)
            ml.load(field)
            ml.backfill(field, stop=stop)
        except Exception as e:
            _once(f"vote: the move log did not load ({type(e).__name__}: {e})")
        try:
            if IN.nlp() is not None:
                IN.attach(field)
        except Exception as e:
            _once(f"vote: the instance parser did not attach ({type(e).__name__}: {e})")
    threading.Thread(target=run, name="vote-warm", daemon=True).start()
