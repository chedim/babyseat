"""FACTS ON THE SPINE (#932, the operator 2026-11-05: the whole graph as one multiverse).

Every message the structure reader reads becomes facts: one Clause entity per clause it reads, holding its
words with their blocks, who said it and when, whether it is possible (a modal, a verb the rule marks, or
verbs leaning past the happened-vs-possible axis's line) and its MOMENT -- its own time words' span start
(dateparser, from when it was said), else when it was said, a time dateparser could not place just before or
after that day's facts by the past-vs-future axis. A real fact is -on- the world spine (what is believed
real); a possible one -on- its speaker's branch, anchored on the spine at the moment it was first held.

The spine is not a chain of edges: a timeline's facts, or a thing's (its worldline: the facts that name it),
are read in moment order (thread). "first / now / between" walk those threads.
"""
from __future__ import annotations

import re
import time

from .entities import MEMBRANE_NS, QuantumEntity

CLAUSE = MEMBRANE_NS + "clause:"
CLASS_CLAUSE = "Clause"
TIMELINE = MEMBRANE_NS + "timeline:"
WORLD = TIMELINE + "world"
CLASS_TIMELINE = "Timeline"
_PERIOD = (("year", "year"), ("month", "month"), ("week", "week"))
SIDE = {"past": -1, None: 0, "future": 1}


def span_of(phrase: str, base: float):
    """(start, end, period) a time phrase points to, from when it was said (dateparser); None when it can't."""
    try:
        import datetime as dt
        import dateparser
        got = dateparser.parse(phrase, settings={"RELATIVE_BASE": dt.datetime.fromtimestamp(base),
                                                 "PREFER_DATES_FROM": "past"})
    except Exception:
        return None
    if got is None:
        return None
    low = phrase.lower()
    per = next((p for w, p in _PERIOD if w in low), "day")
    start = got.replace(hour=0, minute=0, second=0, microsecond=0)
    if per == "year":
        start = start.replace(month=1, day=1)
    elif per == "month":
        start = start.replace(day=1)
    days = {"year": 365, "month": 31, "week": 7, "day": 1}[per]
    return (start.timestamp(), start.timestamp() + days * 86400, per)


def _timeline(est, eid: str, name: str, kind: str, holder: str | None = None, anchor: float | None = None) -> str:
    if eid not in (est.docs or {}):
        props = {"name": name, "kind": kind, "silent_birth": True}
        if holder:
            props.update({"holder": holder, "branch_of": WORLD, "anchor": anchor})
        est.upsert(QuantumEntity(eid=eid, cls=CLASS_TIMELINE, properties=props), embedder=None)
    return eid


def write(srv, msg: str, text: str, speaker: str | None, said: float | None, sid: str = "") -> list[str]:
    """A message's facts, read by srv.structures, as Clause entities on the spine or a branch."""
    from . import convgraph as CG
    st, est = getattr(srv, "structures", None), getattr(srv, "entity_store", None)
    if st is None or est is None or not str(text or "").strip():
        return []
    be, origin = CG._rel(srv)
    said = float(said or time.time())
    out, n = [], 0
    for r in st.read(text):
        for c in sorted(set(r["clauses"])):
            idx = [i for i, k in enumerate(r["clauses"]) if k == c]
            words = [[r["said"][i], r["labels"][i]] for i in idx]
            if not any(l in ("VERB", "POSSIBLE") for _w, l in words):
                continue
            tw = " ".join(w for w, l in words if l == "TIME")
            span = span_of(tw, said) if tw else None
            side = None if span else (r["side"].get(c) if tw else None)
            possible = bool(r["possible"].get(c))
            moment = span[0] if span else said
            if possible and speaker:
                tl = _timeline(est, TIMELINE + "possible:" + re.sub(r"\W+", "-", speaker.lower()),
                               f"what {speaker} holds possible", "possible", speaker, moment)
            else:
                tl = _timeline(est, WORLD, "what is believed real", "real")
            cid = CLAUSE + msg.split(":", 2)[-1] + f":{n}"
            n += 1
            est.upsert(QuantumEntity(eid=cid, cls=CLASS_CLAUSE, properties={
                "name": " ".join(w for w, _l in words)[:80], "msg": msg, "sid": sid, "speaker": speaker,
                "said": said, "text": r["text"], "words": words, "possible": possible, "timeline": tl,
                "span": [span[0], span[1]] if span else None, "period": span[2] if span else None,
                "side": side, "moment": moment, "silent_birth": True}), embedder=None)
            if be is not None:
                CG._edge(be, origin, cid, "of", msg)
                CG._edge(be, origin, cid, "on", tl)
            out.append(cid)
    return out


def clauses(est) -> list[dict]:
    return [{**(d.properties or {}), "eid": d.eid} for d in list((est.docs or {}).values()) if d.cls == CLASS_CLAUSE]


def order_key(f: dict):
    return (float(f.get("moment") or 0), SIDE.get(f.get("side"), 0), float(f.get("said") or 0))


def lemmas(word: str) -> set:
    from .structures import lemma
    w = str(word).lower().strip("'-")
    return {w, lemma(w, "NOUN"), lemma(w, "VERB")} - {""}


def names(f: dict, word: str) -> bool:
    """Does the fact say this word (by lemma)?"""
    L = lemmas(word)
    return any(lemmas(w) & L for w, _l in f.get("words") or [])


def thread(est, word: str | None = None, timeline: str | None = None) -> list[dict]:
    """A timeline's facts (or a thing's worldline: the facts that name it), in moment order."""
    fs = clauses(est)
    if timeline:
        fs = [f for f in fs if f.get("timeline") == timeline]
    if word:
        fs = [f for f in fs if names(f, word)]
    return sorted(fs, key=order_key)


def period(f: dict) -> str:
    """When a fact was about: its own span's period, else the day it was said."""
    t = float(f.get("moment") or f.get("said") or 0)
    fmt = {"year": "%Y", "month": "%B %Y"}.get(f.get("period") or "", "%-d %B %Y")
    return ("week of " if f.get("period") == "week" else "") + time.strftime(fmt, time.localtime(t))
