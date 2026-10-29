"""membrane/recall_labels.py: the banner learns from use (#668, step 6).

When the model SEARCHES after the banner, the banner missed (or ranked too low).
At the search we keep the banner's pool (each row's features and fact line, in
the scorer's order) and the search result. Once the model's reply lands, the
pool row that the reply USES and the search FOUND is a positive label; the rows
the scorer ranked above it are negatives (found AND used, not every read). The
night pass refits the scorer's weights from those labels, older ones decayed,
and writes them to the field's config entity, so the seat's own use replaces
the starting weights.

The constants are flagged for the operator's approval on the PR."""

from __future__ import annotations

import json

from . import recall_banner as RB

COLL = "recall_labels"
WAIT_S = 86400.0          # an unanswered search is dropped after this
USED_MIN = 0.6            # share of a row's content words found in BOTH the result and the reply
HALF_LIFE_DAYS = 14.0     # a label's weight halves every this many days
KEEP_DAYS = 90.0          # labels older than this are deleted
MIN_POS = 20              # positives needed before the first refit
L2 = 1.0                  # pull toward the starting weights, in pseudo-labels
FOUND_CHARS = 4000        # the search result kept per record
REPLY_EVENTS = 3          # model events after the search read as "the reply"
DAY = 86400.0


def is_search(name: str, args) -> bool:
    """A memory search: the query tool, or find(action='memory')."""
    from . import toolset as TS
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            args = {}
    fam, a = TS.canonical(name or "", args if isinstance(args, dict) else {})
    return fam == "query" or (fam == "find" and (a.get("action") or "memory") == "memory")


def _cov(fact_words: set, text_words: set) -> float:
    return len(fact_words & text_words) / len(fact_words) if fact_words else 0.0


def label(pool: list[dict], found: str, reply: str) -> list[dict] | None:
    """[{feat, y}] for one search, or None when no pool row was both found by
    the search and used in the reply. The positive is the best such row; the
    negatives are the rows ranked above it (not its near copies)."""
    fw, rw = set(RB.content_words(found)), set(RB.content_words(reply))
    best, best_s = None, 0.0
    for i, r in enumerate(pool):
        words = set(RB.content_words(r.get("fact") or ""))
        s = min(_cov(words, fw), _cov(words, rw))
        if s >= USED_MIN and s > best_s:
            best, best_s = i, s
    if best is None:
        return None
    pos_sig = RB._sig(pool[best]["fact"])
    out = [{"feat": r["feat"], "y": 0} for r in pool[:best] if not RB._alike(RB._sig(r["fact"]), pos_sig)]
    return out + [{"feat": pool[best]["feat"], "y": 1}]


def note_search(field, state: dict | None, call_id: str, conv: str, found: str, ts: float) -> str | None:
    """Keep the banner's pool next to this search. None when the banner had no pool."""
    if not state or not state.get("pool") or not call_id:
        return None
    key = f"{conv}:{call_id}"
    if field.b.get(COLL, key) is None:
        field.b.upsert(COLL, key, {"status": "open", "ts": float(ts), "conv": conv,
                                   "session": state.get("session") or "", "text": state.get("text") or "",
                                   "pool": state["pool"], "found": (found or "")[:FOUND_CHARS]})
    return key


def _reply(field, session: str, after: float) -> str | None:
    rows = [d for _k, d in field.b.scan("events", {"origin": field.origin, "session": session})
            if d.get("stream") == "discourse" and d.get("role") == "model"
            and float(d.get("ts") or 0) > after and d.get("text")]
    rows.sort(key=lambda d: (float(d.get("ts") or 0), d.get("seq") or 0))
    return " ".join(d["text"] for d in rows[:REPLY_EVENTS]) or None


def used_outside(found: str, reply: str) -> list[str]:
    """The search result's sentences the reply uses (the USED_MIN rule a pool
    row faces; sentences under 5 words skipped, as past_answers skips them)."""
    rw = set(RB.content_words(reply))
    out = []
    for x in RB._SENT.split(found or ""):
        x = x.strip()
        if len(x.split()) >= 5 and _cov(set(RB.content_words(x)), rw) >= USED_MIN:
            out.append(x)
    return out


def resolve(field, now: float) -> dict:
    """Label the open records whose reply has landed; drop the unanswered ones
    past WAIT_S. A reply that used no pool row but used the search's result is
    a recall MISS (operator, 2026-10-29): kept with the ask and the used
    sentences, for a learned query map later, never a scorer label. A reply
    that used neither is dropped."""
    out = {"done": 0, "dropped": 0, "open": 0, "miss": 0}
    for key, rec in list(field.b.scan(COLL, {"status": "open"})):
        reply = _reply(field, rec.get("session") or "", float(rec.get("ts") or 0)) if rec.get("session") else None
        if reply is None:
            if now - float(rec.get("ts") or 0) > WAIT_S:
                field.b.upsert(COLL, key, {"status": "dropped", "ts": rec.get("ts"), "done_ts": now})
                out["dropped"] += 1
            else:
                out["open"] += 1
            continue
        ex = label(rec.get("pool") or [], rec.get("found") or "", reply)
        if ex is None:
            used = used_outside(rec.get("found") or "", reply)
            if used:
                field.b.upsert(COLL, key, {"status": "miss", "ts": rec.get("ts"), "done_ts": now,
                                           "conv": rec.get("conv"), "session": rec.get("session"),
                                           "text": rec.get("text") or "", "used": " ".join(used)[:FOUND_CHARS]})
                out["miss"] += 1
            else:
                field.b.upsert(COLL, key, {"status": "dropped", "ts": rec.get("ts"), "done_ts": now})
                out["dropped"] += 1
        else:
            field.b.upsert(COLL, key, {"status": "done", "ts": rec.get("ts"), "done_ts": now, "examples": ex})
            out["done"] += 1
    return out


def labelled(field) -> tuple[list[dict], list[float]]:
    """(examples, the time each was labelled) over the done records, oldest first."""
    recs = sorted((d for _k, d in field.b.scan(COLL, {"status": "done"})), key=lambda d: float(d.get("done_ts") or 0))
    ex, stamp = [], []
    for d in recs:
        for e in d.get("examples") or []:
            ex.append(e)
            stamp.append(float(d.get("done_ts") or d.get("ts") or 0))
    return ex, stamp


def misses(field) -> list[dict]:
    """The kept recall misses (the ask, the result sentences the reply used), oldest first."""
    return sorted((d for _k, d in field.b.scan(COLL, {"status": "miss"})), key=lambda d: float(d.get("done_ts") or 0))


def examples(field, now: float | None = None) -> list[dict]:
    return labelled(field)[0]


def fit(ex: list[dict], w0: dict, now: float, stamped: list[float]) -> dict:
    """Weighted logistic fit (Newton), pulled toward w0 by L2 pseudo-labels;
    a label's weight halves every HALF_LIFE_DAYS."""
    import numpy as np
    keys = [k for k in w0 if k != "bias"]
    X = np.array([[1.0] + [float(e["feat"].get(k, 0.0)) for k in keys] for e in ex])
    y = np.array([float(e["y"]) for e in ex])
    s = np.array([0.5 ** (max(0.0, now - t) / (HALF_LIFE_DAYS * DAY)) for t in stamped])
    t0 = np.array([float(w0.get("bias", 0.0))] + [float(w0[k]) for k in keys])
    th = t0.copy()

    def loss(v):
        z = X @ v
        ll = np.logaddexp(0.0, z) - y * z
        return float((s * ll).sum() + 0.5 * L2 * ((v - t0) ** 2).sum())
    for _ in range(50):
        p = 1.0 / (1.0 + np.exp(-np.clip(X @ th, -30, 30)))
        g = X.T @ (s * (p - y)) + L2 * (th - t0)
        H = (X * (s * p * (1 - p))[:, None]).T @ X + L2 * np.eye(len(th))
        step = np.linalg.solve(H, g)
        cur, scale = loss(th), 1.0
        while scale > 1e-4 and loss(th - scale * step) > cur:      # damped: a Newton step may overshoot
            scale /= 2
        th = th - scale * step
        if float(np.abs(scale * step).max()) < 1e-6:
            break
    return {"bias": float(th[0]), **{k: float(th[i + 1]) for i, k in enumerate(keys)}}


def prune(field, now: float) -> int:
    n = 0
    for key, rec in list(field.b.scan(COLL, {})):
        if now - float(rec.get("ts") or 0) > KEEP_DAYS * DAY:
            field.b.delete(COLL, key)
            n += 1
    return n


def night(field, now: float) -> dict:
    """One night pass: label what can be labelled, forget the old, refit when
    there are enough positives and labels the last fit had not seen."""
    out = {"labels": resolve(field, now), "pruned": prune(field, now), "refit": False}
    ex, stamp = labelled(field)
    pos = sum(1 for e in ex if e["y"] == 1)
    out["pos"] = pos
    doc = field.b.get(RB.CONFIG_COLL, RB.CONFIG_KEY) or {}
    if pos >= MIN_POS and len(ex) != int(doc.get("fit_n") or -1):
        w = fit(ex, RB.load_weights(field), now, stamp)
        RB.save_weights(field, w, {"fit_n": len(ex), "fit_ts": now, "fit_pos": pos})
        out["refit"] = True
    return out
