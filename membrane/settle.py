"""SETTLING RECALL (2026-11-06, the operator: "fix the gaps between membrane and hoomans"): recall the way cues
settle in a brain, over the field's move log -- no word matching beyond naming the question's things.

  episodes   every event is one episode: the things it moved, and how far (field.moves), in its chat's order
             (field.episodes);
  cues       the things the question names (eids.mentions_of), each weighed by its rarity, log(episodes / episodes
             that moved it); seeds: a question word the field never tracked cues the one thing whose seed (the
             embedder's everyday meaning) sits nearest its own, weighed by its rarity × that nearness ("pastime"
             -> pottery: the embedder's world knowledge, no word match);
  move       how far an episode moved a thing: the sum of its sentences' moves (move="sum"), or its largest
             single sentence move (move="max", from field.worldlines): a long reply repeating a thing in many
             sentences outweighs the short turn that said it once under the sum, not under the max;
  neighbors  an episode also holds what the turn before it in its chat moved (each thing at the larger of the two
             moves): a reply carries the turn it answers, as a listener does;
  settling   round 1: each episode by its cued things' moves × their rarity. Round r+1: the things of the episodes
             round r lit (excluding the cues), each lit by those episodes' scores × its move there × its rarity,
             cue the episodes again. An episode sharing no word with the question is reached through one that does.
  the score  the rounds' scores, each scaled to its round's best, summed.

Returns [(event key, score)], best first; episodes no round lit are left out.
"""
from __future__ import annotations

import math
import re

from . import eids as E

ROUNDS = 1      # round 2 (completion) floods on LoCoMo; the operator 2026-11-06: 1
SEED_NEAREST = 1   # the things a never-tracked word cues: the one nearest (approved 2026-11-06)


def cues(field, text: str) -> set:
    graph = field.graph_fn()
    return {eid for s in re.findall(r"[^.!?]+[.!?]*", text or "") for eid, _ in E.mentions_of(s, graph, field.phrases)}


def _seed_cues(field, text: str, rare: dict) -> dict:
    """{thing: weight} for the question's words the field never tracked: the nearest thing by seed."""
    import numpy as np
    graph = field.graph_fn()
    miss = [w for w in dict.fromkeys(re.findall(r"[A-Za-z][A-Za-z'-]+", text or ""))
            if not any(e in rare for e, _ in E.mentions_of(w, graph, field.phrases))]
    if not miss:
        return {}
    got = getattr(field, "_settle_seeds", None)
    if got is None or got[0] != len(rare):              # the things' seeds, once per field size
        eids = [e for e in rare if getattr(field.get(e), "seed", None) is not None]
        M = np.stack([np.asarray(field.get(e).seed, dtype=np.float32) for e in eids]) if eids else None
        if M is not None:
            M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-12
        got = field._settle_seeds = (len(rare), eids, M)
    _n, eids, M = got
    out: dict = {}
    if M is None:
        return out
    for w in miss:
        v = np.asarray(field._seed(w), dtype=np.float32)
        sims = M @ (v / (np.linalg.norm(v) + 1e-12))
        for i in np.argsort(-sims)[:SEED_NEAREST]:
            e = eids[int(i)]
            out[e] = max(out.get(e, 0.0), rare[e] * max(0.0, float(sims[i])))
    return out


def _moves(field, move: str) -> dict:
    """episode -> {thing: how far it moved it}: the summed log, or each one's largest sentence move."""
    if move != "max":
        return field.moves
    size = sum(len(v) for v in field.worldlines.values())
    got = getattr(field, "_settle_max", None)
    if got is not None and got[0] == size:
        return got[1]
    out: dict = {}
    for e, rows in field.worldlines.items():
        for _ts, k, st in rows:
            book = out.setdefault(k, {})
            book[e] = max(book.get(e, 0.0), st)
    field._settle_max = (size, out)
    return out


def _held(field, neighbors: bool, move: str = "sum") -> dict:
    """episode -> {thing: move}: its own moves, and (neighbors) the turn before it's, the larger of each."""
    moves = _moves(field, move)
    if not neighbors:
        return moves
    place = {v: k for k, v in field.episodes.items()}
    out = {}
    for k, m in moves.items():
        sid, i = field.episodes.get(k, (None, None))
        prev = moves.get(place.get((sid, i - 1)), {}) if i else {}
        held = dict(prev)
        for e, s in m.items():
            held[e] = max(held.get(e, 0.0), s)
        out[k] = held
    return out


def settle(field, text: str, rounds: int = ROUNDS, neighbors: bool = True, seeds: bool = False,
           move: str = "sum") -> list:
    held = _held(field, neighbors, move)
    by_thing: dict = {}
    for k, m in held.items():
        for e in m:
            by_thing.setdefault(e, []).append(k)
    n = max(1, len(held))
    rare = {e: math.log(n / len(ks)) for e, ks in by_thing.items()}
    lit = {e: rare[e] for e in cues(field, text) if e in rare}
    if seeds:
        for e, a in _seed_cues(field, text, rare).items():
            lit[e] = max(lit.get(e, 0.0), a)
    seen = set(lit)
    total: dict = {}
    for r in range(max(1, rounds)):
        score: dict = {}
        for e, a in lit.items():
            for k in by_thing.get(e, ()):
                score[k] = score.get(k, 0.0) + a * held[k][e]
        top = max(score.values(), default=0.0)
        if top <= 0:
            break
        for k, s in score.items():
            total[k] = total.get(k, 0.0) + s / top
        if r + 1 < rounds:                               # the lit episodes' other things cue next
            nxt: dict = {}
            for k, s in score.items():
                for e, mv in held[k].items():
                    if e not in seen:
                        nxt[e] = nxt.get(e, 0.0) + (s / top) * mv * rare[e]
            seen |= set(nxt)
            lit = nxt
    return sorted(total.items(), key=lambda kv: -kv[1])


def explain(field, text: str, keys, fair: bool = False) -> dict:
    """THE QUESTION AS AN EVENT THE FIELD HEARS (2026-11-06, the operator: a natural field reranker): for each event,
    how much of the question's vector the states of the things that event moved leave unexplained -- 1 - cos(the
    least-squares fit of the question from those states, the question). Lower: the event explains the question better
    (as the field's own surprise measures an event against its things). {key: residual}; 1.0 when nothing to fit."""
    import numpy as np
    from . import efgate as G
    qv = G.message_vec(field, text)
    m = len(cues(field, text)) if fair else None       # fair: every event fits with as many things as the question names
    out = {}
    for k in keys:
        if qv is None:
            out[k] = 1.0
            continue
        moved = field.moves.get(k) or {}
        things = sorted(moved, key=lambda e: -moved[e])[:m] if m else list(moved)
        S = [np.asarray(st.state, dtype=np.float32) for e in things
             if (st := field.get(e)) is not None and st.state is not None]
        if not S:
            out[k] = 1.0
            continue
        A = np.stack(S, axis=1)
        c, *_ = np.linalg.lstsq(A, np.asarray(qv, dtype=np.float32), rcond=None)
        fit = A @ c
        n = float(np.linalg.norm(fit))
        out[k] = 1.0 - (float(fit @ qv) / (n * float(np.linalg.norm(qv)) + 1e-12) if n > 0 else 0.0)
    return out


def explain_texts(field, text: str, items: dict, fair: bool = True) -> dict:
    """SURPRISE ON FACTS (2026-11-06): for each piece of text (a fact's own words), how much of the question's vector
    the states of the things THOSE words name leave unexplained (as explain(), but per text, not per event; fair:
    at most as many things as the question names). {item key: residual}; 1.0 when nothing to fit."""
    import numpy as np
    from . import efgate as G
    qv = G.message_vec(field, text)
    graph = field.graph_fn()
    m = len(cues(field, text)) if fair else None
    out = {}
    for key, t in items.items():
        if qv is None:
            out[key] = 1.0
            continue
        things = list(dict.fromkeys(e for e, _ in E.mentions_of(t or "", graph, field.phrases)))
        S = [np.asarray(st.state, dtype=np.float32) for e in things if (st := field.get(e)) is not None and st.state is not None]
        if m:
            S = S[:max(1, m)]
        if not S:
            out[key] = 1.0
            continue
        A = np.stack(S, axis=1)
        c, *_ = np.linalg.lstsq(A, np.asarray(qv, dtype=np.float32), rcond=None)
        fit = A @ c
        nf = float(np.linalg.norm(fit))
        out[key] = 1.0 - (float(fit @ qv) / (nf * float(np.linalg.norm(qv)) + 1e-12) if nf > 0 else 0.0)
    return out
