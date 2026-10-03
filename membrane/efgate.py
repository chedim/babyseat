"""THE GATE ON ANSWER EVENTS + the replay A/B.

Every real turn the gate looks for an earlier user message q' that asked
the same thing, and the answer a the model gave it (the last model message
before the next user message of that session). A candidate is REPLAYABLE
only when all of these hold:

  sim       cos(query, q') >= sim_min (message vectors: the sum of their
            sentence vectors, the field's own units)
  entities  the specific entities of the two asks agree (Jaccard >= 0.8;
            'restart the router' never replays 'restart the gateway')
  consensus the other near q's (if any) got an answer like a
  trusted   a is not annotated unsafe and q' was never re-asked
  binding   the ask's pronoun resolved (an unresolved one never replays)
  fresh     THE STALENESS GUARD: no observation, code change or conflict
            on the ask's entities is newer than a -- the world moved, the
            old answer may be wrong

Replay is not served by default. Every eligible turn is logged as an A/B
pair instead: the replay answer next to the answer the model actually gave
(their similarity, and later an idle local judge's verdict), under
annotations 'ab:*'. --replay-ab P serves the replay on a fraction P of the
eligible turns (those pairs have no model answer; they measure nothing but
are counted)."""

from __future__ import annotations

import random
import time

import numpy as np

from . import eids as E
from . import streams as ST
from .efield import unit, vec_list
from .efstore import h16, o8, pack_f16, unpack_f16

SIM_MIN = 0.92
AGREE_MIN = 0.8
CONSENSUS_MIN = 0.8
TOP_K = 6


def message_vec(field, text: str, embed_missing: bool = True):
    """unit(sum of the message's sentence vectors); sentences the field has
    not seen are embedded and cached (the capture would embed them anyway)."""
    sents = ST.sentences(ST.discourse_clean(text or ""))
    if not sents:
        return None
    sids = [field.sentence_id(s) for s in sents]
    got = field.b.get_multi("sentences", sids)
    miss = [(sid, s) for sid, s in zip(sids, sents) if sid not in got]
    if miss and embed_missing:
        vecs = field.emb.embed([s for _, s in miss])
        for (sid, s), v in zip(miss, vecs):
            # packed f16 (the sentences lane's shape; decimal lists
            # were the 11KB-row exhibit)
            doc = {"_k": "sent", "text": s[:400], "model": field.model,
                   "vec": pack_f16(unit(v)), "first_ts": time.time()}
            field.b.insert_if_absent("sentences", sid, doc)
            got[sid] = doc
    vs = [unpack_f16(got[s]["vec"]) for s in sids if s in got]
    return unit(np.sum(vs, axis=0)) if vs else None


def specifics(field, text: str) -> set:
    return {e for e, _ in E.mentions_of(text or "", field.graph_fn(), field.phrases)
            if E.is_specific(e)}


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def worked_out(field, q_ev: dict, limit: int = 400) -> list[dict]:
    """What was said in answer to q', model prose AND reasoning, up to the
    next user message of its session (historical captures often hold the
    reasoning and no final prose)."""
    sess, seq = q_ev.get("session"), int(q_ev.get("seq") or 0)
    out = []
    for _, d in field.b.scan("events", {"origin": field.origin, "session": sess,
                                        "stream": "discourse", "seq": {"gt": seq}},
                             order=("seq",), limit=limit):
        if d.get("role") == "user":
            break
        if d.get("role") in ("model", "reasoning") and d.get("status") != "forgotten":
            out.append(d)
    return out


def answer_of(field, q_ev: dict) -> tuple[str, dict] | None:
    """The last model message after q' and before the next user message of
    its session."""
    sess, seq = q_ev.get("session"), int(q_ev.get("seq") or 0)
    rows = [(d.get("seq") or 0, k, d) for k, d in field.b.scan(
        "events", {"origin": field.origin, "session": sess, "stream": "discourse",
                   "seq": {"gt": seq}}, order=("seq",), limit=400)]
    ans = None
    for _, k, d in rows:
        if d.get("role") == "user":
            break
        if d.get("role") == "model" and d.get("status") != "forgotten":
            ans = (k, d)
    return ans


def _user_events_near(field, qvec, k: int):
    """Earlier user events whose sentences are nearest the ask (sentence ANN
    -> the deltas that name those sentences -> their events)."""
    hits = field.b.vector_top_k("sentences", "vec", qvec, k * 12)
    sids = [sid for sid, _ in hits]
    if not sids:
        return []
    evkeys = list(dict.fromkeys(d["event"] for _, d in field.b.scan(
        "entity_deltas", {"origin": field.origin, "sid": sids})))
    evs = field.b.get_multi("events", evkeys)
    return [(key, d) for key, d in evs.items()
            if d.get("role") == "user" and d.get("status") == "applied"]


def near_asks(field, qv, k: int = TOP_K) -> list[tuple[float, str, dict]]:
    """Earlier user asks by message similarity to qv, best first."""
    out = []
    for key, d in _user_events_near(field, qv, k):
        v = message_vec(field, d.get("text") or "", embed_missing=False)
        if v is not None:
            out.append((float(v @ qv), key, d))
    out.sort(key=lambda t: -t[0])
    return out


def _stale(field, eids: set, since_ts: float) -> str | None:
    for eid in sorted(eids):
        for d in field.deltas_of(eid):
            if d["stream"] in ("observation", "code") and (d.get("ts") or 0) > since_ts:
                return f"{d['stream']} on {eid} after the answer"
        st = field.get(eid)
        for ck in (st.conflicts if st else []):
            c = field.b.get("claims", ck) or {}
            if c.get("status") == "contradicted" and float(c.get("ts") or since_ts + 1) > since_ts:
                return f"conflict on {eid} after the answer"
    return None


def _annot(field, ev_key: str) -> dict:
    return field.b.get("annotations", f"an:{ev_key}") or {}


def candidate(field, query: str, qvec=None, binding_failed: bool = False,
              sim_min: float = SIM_MIN, exclude_session: str | None = None) -> dict:
    """The gate's verdict on the entity field. Always returns a dict:
    {eligible, reason, sim?, agree?, consensus?, q_event?, a_event?, answer?}."""
    out = {"eligible": False}
    qv = message_vec(field, query) if qvec is None else unit(qvec)
    if qv is None:
        return {**out, "reason": "empty ask"}
    qe = specifics(field, query)
    scored = [(s, key, d) for s, key, d in near_asks(field, qv)
              if not (exclude_session and d.get("session") == exclude_session
                      and ST.clean(d.get("text") or "") == ST.clean(query))]
    if not scored:
        return {**out, "reason": "no earlier ask"}
    sim, qkey, qd = scored[0]
    out.update({"sim": round(sim, 4), "q_event": qd.get("n"), "q_text": (qd.get("text") or "")[:300]})
    if sim < sim_min:
        return {**out, "reason": f"sim {sim:.2f} < {sim_min:.2f}"}
    agree = jaccard(qe, specifics(field, qd.get("text") or ""))
    out["agree"] = round(agree, 3)
    if agree < AGREE_MIN:
        return {**out, "reason": f"entities differ ({agree:.2f})"}
    ans = answer_of(field, qd)
    if ans is None:
        return {**out, "reason": "the earlier ask has no answer"}
    akey, ad = ans
    out.update({"a_event": ad.get("n"), "answer": ad.get("text") or ""})
    av = message_vec(field, ad.get("text") or "", embed_missing=False)
    others = []
    for s2, k2, d2 in scored[1:]:
        if s2 < sim_min:
            break
        a2 = answer_of(field, d2)
        v2 = message_vec(field, a2[1].get("text") or "", embed_missing=False) if a2 else None
        if v2 is not None and av is not None:
            others.append(float(v2 @ av))
    cons = (sum(1 for c in others if c >= CONSENSUS_MIN) / len(others)) if others else 1.0
    out["consensus"] = round(cons, 3)
    if cons < 0.5:
        return {**out, "reason": f"near asks disagree ({cons:.2f})"}
    if _annot(field, akey).get("safe") is False:
        return {**out, "reason": "the answer is marked unsafe"}
    if int(_annot(field, qkey).get("reask_count") or 0) > 0:
        return {**out, "reason": "the earlier ask was re-asked"}
    if binding_failed:
        return {**out, "reason": "unresolved pronoun"}
    why = _stale(field, qe | specifics(field, qd.get("text") or ""), float(ad.get("ts") or 0))
    if why:
        return {**out, "reason": f"stale: {why}"}
    out.update({"eligible": True, "reason": "replayable", "a_key": akey})
    return out


# ---- the A/B log -----------------------------------------------------------------------
def ab_key(field, query: str, ts: float) -> str:
    return f"ab:{o8(field.origin)}:{h16(query + '|' + str(round(ts, 3)))}"


def serve_replay(p: float, rng=random.random) -> bool:
    return p > 0 and rng() < p


def ab_open(field, verdict: dict, query: str, served: bool, ts: float | None = None) -> str:
    ts = ts or time.time()
    key = ab_key(field, query, ts)
    field.b.upsert("annotations", key, {
        "_k": "ab", "origin": field.origin, "ts": ts, "query": query[:600],
        "q_event": verdict.get("q_event"), "a_event": verdict.get("a_event"),
        "sim": verdict.get("sim"), "replay": (verdict.get("answer") or "")[:4000],
        "served": "replay" if served else "model", "model_answer": None,
        "answer_sim": None, "judge": None, "status": "served-replay" if served else "open"})
    return key


def ab_close(field, key: str, model_answer: str) -> dict | None:
    """The model's actual answer lands: similarity to the replay now, the
    judge later (idle, budgeted)."""
    d = field.b.get("annotations", key)
    if d is None or d.get("status") != "open":
        return None
    d["model_answer"] = (model_answer or "")[:4000]
    a = message_vec(field, d["replay"])
    b = message_vec(field, d["model_answer"])
    d["answer_sim"] = round(float(a @ b), 4) if a is not None and b is not None else None
    d["status"] = "pending_judge"
    field.b.upsert("annotations", key, d)
    return d


AB_JUDGE_PROMPT = (
    "Two answers to the same question. Do they agree on the substance (facts, "
    "decisions, steps)? Reply with exactly one word -- agree, partial or disagree "
    "-- then a colon and one short reason.\n\nQUESTION: {q}\n\nANSWER A: {a}\n\n"
    "ANSWER B: {b}")


def ab_judge_pending(field, judge_fn, limit: int = 2) -> int:
    """judge_fn(prompt) -> text. Idle-only callers; bounded per call."""
    n = 0
    for key, d in list(field.b.scan("annotations", {"origin": field.origin, "_k": "ab",
                                                    "status": "pending_judge"}, limit=limit)):
        try:
            out = judge_fn(AB_JUDGE_PROMPT.format(q=d["query"][:600], a=d["replay"][:1500],
                                                  b=d["model_answer"][:1500]))
        except Exception:
            continue
        word, _, why = (out or "").strip().partition(":")
        word = word.strip().lower()
        d["judge"] = {"verdict": word if word in ("agree", "partial", "disagree") else "unclear",
                      "reason": why.strip()[:200], "ts": time.time()}
        d["status"] = "judged"
        field.b.upsert("annotations", key, d)
        n += 1
    return n


def ab_report(field) -> dict:
    rows = [d for _, d in field.b.scan("annotations", {"origin": field.origin, "_k": "ab"})]
    judged = [d for d in rows if d.get("judge")]
    sims = [d["answer_sim"] for d in rows if d.get("answer_sim") is not None]
    verdicts: dict = {}
    for d in judged:
        v = d["judge"]["verdict"]
        verdicts[v] = verdicts.get(v, 0) + 1
    return {"eligible": len(rows),
            "served_replay": sum(1 for d in rows if d.get("served") == "replay"),
            "paired": len(sims),
            "mean_answer_sim": round(float(np.mean(sims)), 4) if sims else None,
            "judged": len(judged), "verdicts": verdicts}
