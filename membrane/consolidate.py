"""CONSOLIDATION: membrane generalizes (2026-10-10, the plan in
docs/plans/2026-10-10-generalization.md -- sleep's missing half: replay
the day, induce what is common across many experiences).

STEP 0 -- readings are experience: a getter's reading becomes a field event
about ITS entity (the member's own values only -- a fan-out class getter's
whole map is cut to the member's entry), so readings move entities; a small
readings ring keeps the history the axes induction will need.

STEP 1 -- classes by geometry, SHADOW: clusters of entities that SIT alike
(their positions in the older half of the window) are tested on whether they
MOVE alike in the newer half -- against random groups of the same size and
against the name-stem families (the baseline). Logged only; nothing reaches
the seat until the operator has seen the numbers.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import numpy as np

READING_MIN_S = 60.0          # at most one reading event per entity a minute
READINGS_RING = 96
INDUCE_EVERY_S = 6 * 3600.0
WINDOW_DAYS = 14
MIN_HALF = 2                  # deltas needed in each half
PER_HALF_CAP = 16            # x2 halves x MAX_ENTITIES = the sentence vectors held at once (~50 MB peak)
MAX_ENTITIES = 800
SIM_LINK = 0.85               # 'sits alike': cosine of the older positions
CLUSTER_MIN, CLUSTER_MAX = 3, 60
RANDOM_DRAWS = 24


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


# ---- step 0: readings ------------------------------------------------------
def own_reading(eid: str, state: dict) -> dict:
    """The member's own values: a fan-out map keyed by entity ids is cut to
    this entity's entry."""
    from .classes import short_name
    if not isinstance(state, dict):
        return {}
    me = short_name(eid)
    for k in (eid, me, me.split(":", 1)[-1]):
        v = state.get(k)
        if isinstance(v, dict):
            return v
    if len(state) > 12 and all(isinstance(v, dict) for v in list(state.values())[:12]):
        return {}                                   # someone else's map: nothing of its own
    return state


def keyed_reading(eid: str, state: dict) -> dict | None:
    """This entity's entry in a fan-out map keyed by entity ids, or None."""
    from .classes import short_name
    if not isinstance(state, dict):
        return None
    me = short_name(eid)
    for k in (eid, me, me.split(":", 1)[-1]):
        v = state.get(k)
        if isinstance(v, dict):
            return v
    return None


CLASS_SPREAD_MAX = 150
_class_spread_ts: dict[str, float] = {}


def record_class_readings(srv, est, cls_eid: str, state: dict, skip: str = "",
                          now: float | None = None) -> int:
    """A CLASS getter's one fetch holds every member's state: each member
    found in the map gets its own reading, not only the one that was read
    (the seat's fan-out probe, 2026-10-10: 27 real lights stayed
    'unconfirmed' because nobody had read them one by one). At most once a
    minute per class."""
    now = now or time.time()
    if est is None or not isinstance(state, dict) or len(state) < 2:
        return 0
    if now - _class_spread_ts.get(cls_eid, 0.0) < READING_MIN_S:
        return 0
    _class_spread_ts[cls_eid] = now
    from .night import _raw_members
    n = 0
    for m in _raw_members(est, cls_eid)[:CLASS_SPREAD_MAX * 2]:
        if m.eid == skip:
            continue
        vals = keyed_reading(m.eid, state)
        if vals is None:
            continue
        if record_reading(srv, m, {m.eid: vals}, now):
            n += 1
            try:
                est.upsert(m, embedder=None)
            except Exception:
                pass
        if n >= CLASS_SPREAD_MAX:
            break
    return n


def _render(values: dict) -> str:
    parts = []
    for k, v in list(values.items())[:8]:
        if isinstance(v, (dict, list)):
            continue
        s = str(v)
        if len(s) > 40:
            continue
        parts.append(f"{k} {s}")
    return ", ".join(parts)


def record_reading(srv, ent, state, now: float | None = None) -> bool:
    """A getter's reading as experience: one field event about the entity
    (only when the reading changed, at most once a minute) + the ring."""
    now = now or time.time()
    vals = own_reading(ent.eid, state)
    text = _render(vals)
    if not text:
        return False
    props = ent.properties or {}
    if props.get("_last_reading") == text or now - float(props.get("_last_reading_ts") or 0) < READING_MIN_S:
        return False
    ring = list(props.get("readings") or [])
    nums = {k: v for k, v in vals.items() if isinstance(v, (int, float, bool)) or
            (isinstance(v, str) and len(v) <= 24)}
    ring.append({"ts": round(now, 1), "v": dict(list(nums.items())[:8])})
    props["readings"] = ring[-READINGS_RING:]
    props["_last_reading"] = text
    props["_last_reading_ts"] = now
    ent.properties = props
    fld = getattr(srv, "efield", None)
    if fld is None:
        return True
    try:
        from . import streams as ST
        from .classes import short_name
        from .eids import field_id
        subj = short_name(ent.eid)
        fld.write_events([ST.Event(stream="observation", tool="read", args={"entity": subj},
                                   result_head=f"{subj}: {text}", outcome="ok", ts=now,
                                   subject=field_id(ent.eid))], session="readings")
    except Exception as e:
        print(f"[consolidate] reading event skipped: {e!r}", file=sys.stderr)
    return True


# ---- step 1: classes by geometry (shadow) -----------------------------------
def _halves(fld, eids, now: float):
    """Per entity: (older mean vector, newer mean vector) over the window,
    split at the window's median delta time."""
    since = now - WINDOW_DAYS * 86400
    rows = {}
    ts_all = []
    for eid in eids:
        ds = [d for d in fld.deltas_of(eid) if float(d.get("ts") or 0) >= since and d.get("sid")]
        if len(ds) >= 2 * MIN_HALF:
            rows[eid] = ds
            ts_all.extend(float(d.get("ts") or 0) for d in ds)
    if not rows:
        return {}, None
    t_mid = float(np.median(ts_all))
    want, plan = set(), {}
    for eid, ds in rows.items():
        a = [d["sid"] for d in ds if float(d.get("ts") or 0) < t_mid][-PER_HALF_CAP:]
        b = [d["sid"] for d in ds if float(d.get("ts") or 0) >= t_mid][-PER_HALF_CAP:]
        if len(a) >= MIN_HALF and len(b) >= MIN_HALF:
            plan[eid] = (a, b)
            want.update(a)
            want.update(b)
    if not plan:
        return {}, t_mid
    from .efstore import unpack_f16
    got = {}
    want = list(want)
    for i in range(0, len(want), 512):
        got.update(fld.b.get_multi("sentences", want[i:i + 512]))

    def vec(s):
        d = got.get(s)
        if not d or d.get("vec") is None:
            return None
        v = d["vec"]
        return _unit(unpack_f16(v) if isinstance(v, (str, bytes)) else v)
    out = {}
    for eid, (a, b) in plan.items():
        va = [x for x in (vec(s) for s in a) if x is not None]
        vb = [x for x in (vec(s) for s in b) if x is not None]
        if len(va) >= MIN_HALF and len(vb) >= MIN_HALF:
            out[eid] = (_unit(np.mean(va, axis=0)), _unit(np.mean(vb, axis=0)))
    return out, t_mid


def _clusters(ids, E):
    """Connected components of the 'sits alike' graph (older positions)."""
    S = E @ E.T
    np.fill_diagonal(S, 0.0)
    parent = list(range(len(ids)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(ids)):
        for j in np.nonzero(S[i] >= SIM_LINK)[0]:
            if j > i:
                parent[find(i)] = find(int(j))
    groups = {}
    for i in range(len(ids)):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if CLUSTER_MIN <= len(g) <= CLUSTER_MAX]


def _coherence(M, idx) -> float:
    """How alike a group moved: mean pairwise cosine of the members'
    movement directions (newer minus older)."""
    if len(idx) < 2:
        return 0.0
    X = np.stack([_unit(M[i]) for i in idx])
    S = X @ X.T
    n = len(idx)
    return float((S.sum() - n) / (n * (n - 1)))


def induce_classes(fld, est=None, now: float | None = None, rng=None) -> dict:
    """The shadow report: geometry clusters vs random groups vs name
    families, on held-out (newer-half) movement."""
    now = now or time.time()
    rng = rng or random.Random(7)
    from .eids import is_specific
    from .classes import family_key, label_of
    cands = [st.eid for st in list(getattr(fld, "_states", {}).values())
             if getattr(st, "tier", "") == "live" and is_specific(st.eid)
             and not st.eid.startswith(("person:", "seat:", "m:", "s:?"))][:MAX_ENTITIES * 3]
    # m: = the field's mirror of membrane's own renders, s:? = tool-name token
    # echoes -- the first shadow night's junk clusters (2026-10-10)
    halves, t_mid = _halves(fld, cands, now)
    ids = list(halves)[:MAX_ENTITIES]
    rep = {"ts": int(now), "kind": "classes-by-geometry", "shadow": True,
           "entities": len(ids), "split_ts": t_mid}
    if len(ids) < CLUSTER_MIN * 2:
        rep["note"] = "too few entities with history in both halves"
        return rep
    E0 = np.stack([halves[e][0] for e in ids])
    M = np.stack([halves[e][1] - halves[e][0] for e in ids])
    groups = _clusters(ids, E0)

    def rand_coh(size):
        vals = [_coherence(M, rng.sample(range(len(ids)), size)) for _ in range(RANDOM_DRAWS)]
        return float(np.mean(vals))
    clusters = []
    for g in groups:
        coh = _coherence(M, g)
        base = rand_coh(len(g))
        clusters.append({"members": [ids[i] for i in g][:12], "n": len(g),
                         "coherence": round(coh, 4), "random": round(base, 4),
                         "gain": round(coh - base, 4)})
    clusters.sort(key=lambda c: -c["gain"])
    fams = {}
    for i, e in enumerate(ids):
        k = family_key(e)
        if k:
            fams.setdefault(k, []).append(i)
    names = []
    for k, g in fams.items():
        if len(g) >= CLUSTER_MIN:
            coh = _coherence(M, g)
            base = rand_coh(len(g))
            names.append({"family": label_of(k), "n": len(g), "coherence": round(coh, 4),
                          "random": round(base, 4), "gain": round(coh - base, 4)})
    names.sort(key=lambda c: -c["gain"])

    def wmean(xs):
        tot = sum(c["n"] for c in xs)
        return round(sum(c["gain"] * c["n"] for c in xs) / tot, 4) if tot else None
    rep.update({"clusters": len(clusters), "geometry_gain": wmean(clusters),
                "names": len(names), "name_gain": wmean(names),
                "top_clusters": clusters[:8], "top_names": names[:8]})
    return rep


def log_report(srv, rep: dict) -> None:
    mp = getattr(srv, "_models_path", None)
    if not mp:
        return
    p = Path(mp).parent / "consolidation.jsonl"
    try:
        with open(p, "a") as f:
            f.write(json.dumps(rep, default=float) + "\n")
    except Exception:
        pass


def maybe_induce(srv, fld, est, now: float | None = None) -> dict | None:
    """The night phase, at most every INDUCE_EVERY_S (shadow: logged only)."""
    now = now or time.time()
    last = float(getattr(srv, "_induce_last_ts", 0) or 0)
    if now - last < INDUCE_EVERY_S:
        return None
    srv._induce_last_ts = now
    t0 = time.time()
    rep = induce_classes(fld, est, now)
    rep["ms"] = int((time.time() - t0) * 1000)
    log_report(srv, rep)
    print(f"[consolidate] classes-by-geometry (shadow): {rep.get('clusters')} clusters, "
          f"gain {rep.get('geometry_gain')} vs names {rep.get('name_gain')} "
          f"over {rep.get('entities')} entities, {rep['ms']}ms", file=sys.stderr)
    return rep
