"""THE FIELD MAP (2026-10-08): the field as a 2D map's data -- the field's
live entities as nodes, the `relations` triples between them + the
co-mentions (shown entities sharing sentences in their `recent` rings --
the triples only come from file tools) as edges, and EVERY node's value on EVERY axis, so a client re-plots on any X/Y pair
without a refetch:

  pc1..pck        principal components of the entity states (signs fixed:
                  each component's largest loading is positive -> the same
                  field gives the same map, whatever the row order)
  kind:<k>        the direction from the field's mean to kind k's centroid
                  (where "file-like" or "tool-like" lies)
  mass, recency, age, activity, coherence, degree
                  the scalar readings (log mass; degree = live edges)

Pure (numpy only): the seat's /membrane/map/field handler feeds it the
top-m live states by mass + the live relations.
"""
from __future__ import annotations

import math

import numpy as np

LABEL_MAX = 80


def _fix_signs(vt: np.ndarray) -> np.ndarray:
    idx = np.argmax(np.abs(vt), axis=1)
    signs = np.sign(vt[np.arange(vt.shape[0]), idx])
    signs[signs == 0] = 1.0
    return vt * signs[:, None]


CO_MENTION = "co-mentioned"


def _co_mentions(rows, clique_max: int) -> dict[tuple, int]:
    """Pairs of shown entities sharing sentences in their `recent` rings
    (the last sentence ids each one appeared in) -> shared count. A
    sentence touching more than clique_max shown entities is a stop-word
    sentence: it wires nothing (no hairball)."""
    by_sid: dict[str, list[str]] = {}
    for r in rows:
        for sid in set(r.get("recent") or []):
            by_sid.setdefault(str(sid), []).append(r["eid"])
    pairs: dict[tuple, int] = {}
    for eids in by_sid.values():
        if not 2 <= len(eids) <= clique_max:
            continue
        eids = sorted(set(eids))
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                key = (eids[i], CO_MENTION, eids[j])
                pairs[key] = pairs.get(key, 0) + 1
    return pairs


def _edges(rels, ids: set, max_edges: int, extra=None) -> list[dict]:
    agg: dict[tuple, int] = dict(extra or {})
    for r in rels or []:
        if not isinstance(r, dict) or r.get("superseded"):
            continue
        s, t, rel = r.get("subj"), r.get("obj"), str(r.get("rel") or "")
        if s == t or s not in ids or t not in ids:
            continue
        key = (s, rel, t)
        agg[key] = agg.get(key, 0) + int(r.get("n") or 1)
    out = [{"s": s, "rel": rel, "t": t, "n": n} for (s, rel, t), n in agg.items()]
    out.sort(key=lambda e: (-e["n"], e["s"], e["rel"], e["t"]))
    return out[:max_edges]


def field_map(rows, rels, k: int = 8, max_kinds: int = 8,
              max_edges: int = 6000, clique_max: int = 12) -> dict:
    rows = [r for r in rows or [] if r.get("eid") and r.get("state") is not None]
    if not rows:
        return {"axes": [], "nodes": [], "edges": [], "kinds": [],
                "kind_counts": {}, "explained": []}
    # a stable order (the input order must not move the map)
    rows = sorted(rows, key=lambda r: str(r["eid"]))
    X = np.asarray([np.asarray(r["state"], dtype=np.float64) for r in rows])
    mu = X.mean(0)
    Xc = X - mu
    k = max(1, min(int(k), len(rows) - 1 if len(rows) > 1 else 1, X.shape[1]))
    try:
        _, sv, vt = np.linalg.svd(Xc, full_matrices=False)
        vt = _fix_signs(vt[:k])
        tot = float((sv ** 2).sum()) or 1.0
        explained = [round(float(s * s) / tot, 4) for s in sv[:k]]
    except np.linalg.LinAlgError:
        vt = np.zeros((k, X.shape[1]))
        explained = [0.0] * k
    pcs = Xc @ vt.T

    counts: dict[str, int] = {}
    for r in rows:
        kd = str(r.get("kind") or "?")
        counts[kd] = counts.get(kd, 0) + 1
    kinds = sorted(counts, key=lambda kd: (-counts[kd], kd))
    kind_dirs = []
    for kd in kinds[:max_kinds]:
        if counts[kd] < 2 or counts[kd] == len(rows):
            continue
        sel = np.array([str(r.get("kind") or "?") == kd for r in rows])
        d = X[sel].mean(0) - mu
        nrm = float(np.linalg.norm(d))
        if nrm > 1e-9:
            kind_dirs.append((kd, d / nrm))

    ids = {r["eid"] for r in rows}
    edges = _edges(rels, ids, max_edges, _co_mentions(rows, clique_max))
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["s"]] = deg.get(e["s"], 0) + 1
        deg[e["t"]] = deg.get(e["t"], 0) + 1

    axes = [{"id": f"pc{i + 1}", "label": f"PC{i + 1} ({explained[i] * 100:.1f}%)",
             "group": "components"} for i in range(k)]
    axes += [{"id": f"kind:{kd}", "label": f"toward {kd}", "group": "kinds"}
             for kd, _ in kind_dirs]
    axes += [{"id": a, "label": a, "group": "readings"}
             for a in ("mass", "recency", "age", "activity", "coherence", "degree")]
    kind_vals = [Xc @ d for _, d in kind_dirs]
    newest_first = max((float(r.get("first_ts") or 0) for r in rows), default=0.0)

    nodes = []
    for i, r in enumerate(rows):
        v = [round(float(x), 3) for x in pcs[i]]
        v += [round(float(kv[i]), 3) for kv in kind_vals]
        v += [round(math.log1p(max(0.0, float(r.get("mass_eff") or r.get("accr")
                                    or 0))), 3),
              float(r.get("last_ts") or 0),
              round(newest_first - float(r.get("first_ts") or newest_first), 3),
              round(float(r.get("activity") or 0), 3),
              round(float(r.get("coherence") or 0), 3),
              float(deg.get(r["eid"], 0))]
        label = str(r.get("surface") or r["eid"]).replace("\n", " ").strip()
        nodes.append({"id": r["eid"], "kind": str(r.get("kind") or "?"),
                      "label": label[:LABEL_MAX],
                      "mass": int(round(float(r.get("mass_eff") or r.get("accr")
                                         or 0))),
                      "contest": len(r.get("conflicts") or []),
                      "deg": deg.get(r["eid"], 0), "v": v})
    return {"axes": axes, "nodes": nodes, "edges": edges, "kinds": kinds,
            "kind_counts": counts, "explained": explained}


CARD_FIELDS = ("eid", "kind", "surface", "tier", "n", "n_by_stream", "accr",
               "mass_eff", "coherence", "net_align", "first_ts", "last_ts",
               "activity", "conflicts")


def entity_card(doc, evidence, near, relations, self_eid: str | None = None) -> dict | None:
    """The map drawer's read of ONE field entity (GET /membrane/field/entity):
    its readings, the evidence sentences, the nearest field neighbors, the
    relations. Vectors (state/seed/mean/rsum/vsa) never leave the seat."""
    if not isinstance(doc, dict):
        return None
    eid = doc.get("eid") or self_eid
    card = {k: doc.get(k) for k in CARD_FIELDS if k in doc}
    card["eid"] = eid
    card["evidence"] = [{"text": str(e.get("text") or "")[:400],
                         "stream": e.get("stream"), "role": e.get("role"),
                         "ts": e.get("ts"), "score": e.get("score")}
                        for e in evidence or [] if isinstance(e, dict)]
    card["near"] = [{"eid": x, "sim": round(float(s), 3)}
                    for x, s in near or [] if x != eid]
    card["relations"] = [{"subj": r.get("subj"), "rel": r.get("rel"),
                          "obj": r.get("obj"), "n": r.get("n")}
                         for r in relations or [] if isinstance(r, dict)]
    return card


def nearest(vec, rows, k: int = 12, exclude: str | None = None) -> list[tuple[str, float]]:
    """Cosine neighbors of vec among rows ({eid, state}) -- the card's
    fallback when the backend's vector index answers nothing."""
    rows = [r for r in rows or [] if r.get("state") is not None and r.get("eid") != exclude]
    if vec is None or not rows:
        return []
    q = np.asarray(vec, dtype=np.float64)
    qn = float(np.linalg.norm(q))
    if qn < 1e-12:
        return []
    X = np.asarray([np.asarray(r["state"], dtype=np.float64) for r in rows])
    norms = np.linalg.norm(X, axis=1)
    ok = norms > 1e-12
    sims = np.full(len(rows), -2.0)
    sims[ok] = (X[ok] @ q) / (norms[ok] * qn)
    order = np.argsort(-sims)[:k]
    return [(rows[i]["eid"], round(float(sims[i]), 4)) for i in order if sims[i] > -2.0]


def distances(vec, rows) -> dict[str, float]:
    """THE ENTITY AXIS: 1 - cosine of each row's state to vec (the
    reference entity = 0); rows without a direction are left out."""
    if vec is None:
        return {}
    q = np.asarray(vec, dtype=np.float64)
    qn = float(np.linalg.norm(q))
    if qn < 1e-12:
        return {}
    out = {}
    for r in rows or []:
        if r.get("state") is None:
            continue
        x = np.asarray(r["state"], dtype=np.float64)
        n = float(np.linalg.norm(x))
        if n < 1e-12:
            continue
        out[r["eid"]] = round(1.0 - float(x @ q) / (n * qn), 5)
    return out


def toward(vec, rows) -> dict[str, float]:
    """THE SIGNED ENTITY AXIS: each row's projection onto the unit
    direction from the field's mean to vec -- 0 = the typical memory,
    + = shares what makes the reference distinctive, - = its opposite
    (the shared background the cosine distance is dominated by is gone)."""
    rows = [r for r in rows or [] if r.get("state") is not None]
    if vec is None or not rows:
        return {}
    X = np.asarray([np.asarray(r["state"], dtype=np.float64) for r in rows])
    mu = X.mean(0)
    d = np.asarray(vec, dtype=np.float64) - mu
    dn = float(np.linalg.norm(d))
    if dn < 1e-12:
        return {r["eid"]: 0.0 for r in rows}
    p = (X - mu) @ (d / dn)
    return {r["eid"]: round(float(v), 5) for r, v in zip(rows, p)}
