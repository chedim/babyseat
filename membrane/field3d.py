"""THE WHOLE FIELD IN 3D (2026-10-08, the operator's viewer v3; a sphere
since #362).

The 2D map projected the top-3000 by mass; the live field is ~14k entities
and only grows. This projects EVERY live entity into the ball inscribed in
the [0,1]^3 cube and serves it as octree tiles, so the viewer loads what the
camera can see and drops the rest (Android's recycled lists, in space):

  * TWO STREAMING PASSES over the store (never an N x 1024 matrix): pass 1
    sums the mean + per-kind centroids and keeps a bounded sample (the top
    SAMPLE_TOP by mass + a SAMPLE_RAND reservoir) to fit the PCA; pass 2
    projects every row in chunks onto the three chosen axes.
  * AXES: pcN (principal components of the sample, signs fixed), kind:<k>
    (mean -> kind centroid), e:<eid> or e:<eid>:toward (both: the signed
    projection on mean -> entity) -- the 2D view's "Make X-axis", now three
    of them. All three are LINEAR in the centred vector (v - mean); e:<eid>
    used to be 1 - cos to the entity, which a sphere cannot use as a
    direction (#362), so plain e:<eid> now means the same as :toward.
  * THE RUSSIAN-DOLL SPHERE (#362): p = the centred vector's projection on
    the three axes. DIRECTION from the centre = p / |p| (an entity with no
    projection at all gets a direction from its id's hash, stable). RADIUS
    = how much of the vector the axes capture, |p| / |v - mean| -- the
    centred length, because the axes are directions of variation ABOUT the
    mean (the PCA is fitted on centred vectors, kind/entity axes start at
    the mean): for orthonormal axes it is in [0, 1], and the offset every
    entity shares (the mean) does not crowd everyone toward the centre.
    Real captured fractions bunch up, so the radius is spread by THE
    SPREAD below; position = 0.5 + 0.5 x radius x direction, so the octree
    keeps working inside [0,1]^3 unchanged.
  * THE SPREAD: 0.8 x rank + 0.2 x robust z (median/MAD, clipped) -- the
    2D layout's recipe (the cube's per-axis rule before #362), done once
    server-side over the whole field, now on the radius; the rank counts
    from 1/n so the radius is in (0, 1] and nobody sits AT the centre. The
    radius is the CUBE ROOT of that spread: a ball's volume grows as r^3, so
    a spread that is even in r would crowd the core (density ~ 1/r^2); the
    cube root fills the ball evenly by volume and keeps the order.
  * THE OCTREE LOD: entities in mass order claim a slot at the coarsest
    level whose cell still has room (K per cell); each entity lives in
    exactly ONE tile, so the client shows levels 0..L of the cells in view
    and more of the field appears as you zoom in.
  * place(vec): a moved entity's new position on the sphere under the SAME
    fit (the sorted captured fractions + median/MAD are kept) -- the live
    layer glides nodes without a refit.

Pure numpy; the seat's lanes feed it the store's scan.
"""
from __future__ import annotations

import hashlib
import heapq
import random
import time

import numpy as np

SAMPLE_TOP = 4000
SAMPLE_RAND = 1000
CHUNK = 2048
TILE_K = 150
MAX_LEVEL = 8
MAX_EDGES = 40000
CLIQUE_MAX = 12
LABEL_MAX = 80
CO_MENTION = "co-mentioned"


def parse_axes(spec: str) -> list[str]:
    """'pc1,pc2,e:<eid>:toward' -> three axis ids (missing ones fill with
    the next unused PC)."""
    axes = [a.strip() for a in (spec or "").split(",") if a.strip()][:3]
    pcs = iter(f"pc{i}" for i in range(1, 10) if f"pc{i}" not in axes)
    while len(axes) < 3:
        axes.append(next(pcs))
    return axes


def _fix_signs(vt: np.ndarray) -> np.ndarray:
    idx = np.argmax(np.abs(vt), axis=1)
    signs = np.sign(vt[np.arange(vt.shape[0]), idx])
    signs[signs == 0] = 1.0
    return vt * signs[:, None]


class _Axis:
    """One axis: a unit direction the centred vector is projected on
    (None: a dead axis, every projection 0)."""

    def __init__(self, aid: str, vec=None):
        self.id, self.vec = aid, vec


def _hash_dir(eid: str) -> np.ndarray:
    """A stable unit direction from an id (for an entity the axes see none of)."""
    h = hashlib.md5(str(eid).encode()).digest()
    d = np.asarray([int.from_bytes(h[4 * k:4 * k + 4], "big") / 2 ** 32 - 0.5 for k in range(3)])
    n = float(np.linalg.norm(d))
    return d / n if n > 1e-12 else np.asarray([1.0, 0.0, 0.0])


def _project(X: np.ndarray, mu: np.ndarray, A: np.ndarray):
    """(the 3D projections, the captured fractions |p| / |x - mu|)."""
    C = X - mu
    P = C @ A.T
    pn = np.linalg.norm(P, axis=1)
    cn = np.linalg.norm(C, axis=1)
    frac = np.where(cn > 1e-12, pn / np.where(cn > 1e-12, cn, 1.0), 0.0)
    return P, frac


def _directions(P: np.ndarray, frac: np.ndarray, eids) -> np.ndarray:
    pn = np.linalg.norm(P, axis=1)
    out = np.zeros_like(P)
    for i in range(P.shape[0]):
        # no projection at all (or rounding noise): the id's own direction
        if pn[i] > 1e-12 and frac[i] > 1e-9:
            out[i] = P[i] / pn[i]
        else:
            out[i] = _hash_dir(eids[i] if eids is not None else "")
    return out


def _spread(r: float, srt, med: float, mad: float) -> float:
    n = len(srt)
    rank = min(1.0, (float(np.searchsorted(srt, r)) + 1.0) / max(1, n))
    z = 0.0 if mad < 1e-12 else max(-3.0, min(3.0, (r - med) / mad))
    return (0.8 * rank + 0.2 * (z + 3.0) / 6.0) ** (1.0 / 3.0)     # the cube root: even by volume


class Projection:
    def __init__(self, axes, ids, coords, mass, kinds, kind_names, labels,
                 contest, level, cell, edges, scale, explained, built_s,
                 mu=None, A=None):
        self.axes, self.ids, self.coords = axes, ids, coords
        self.mass, self.kinds, self.kind_names = mass, kinds, kind_names
        self.labels, self.contest = labels, contest
        self.level, self.cell, self.edges = level, cell, edges
        self.scale, self.explained, self.built_s = scale, explained, built_s
        self.mu, self.A = mu, A
        self.index = {e: i for i, e in enumerate(ids)}
        self.ts = time.time()
        self.pid = hashlib.sha1((",".join(a.id for a in axes) + repr(self.ts)
                                 ).encode()).hexdigest()[:12]
        self.tiles: dict[tuple, list[int]] = {}
        for i in range(len(ids)):
            self.tiles.setdefault((int(level[i]),) + tuple(int(c) for c in cell[i]), []).append(i)
        self.max_level = int(level.max()) if len(ids) else 0
        # the cells with something FINER inside (an entity at a deeper
        # level under them): only those refine -- empty octants stop the walk
        self.has_below: set = set()
        for (lv, x, y, z) in self.tiles:
            for up in range(lv - 1, -1, -1):
                sh = lv - up
                key = (up, x >> sh, y >> sh, z >> sh)
                if key in self.has_below:
                    break
                self.has_below.add(key)
        self._edges_of: dict[int, list[int]] = {}
        for j, e in enumerate(edges):
            self._edges_of.setdefault(e[0], []).append(j)
            self._edges_of.setdefault(e[1], []).append(j)

    # -- the routes' shapes
    def meta(self) -> dict:
        counts: dict[str, int] = {}
        for k in self.kinds:
            nm = self.kind_names[k]
            counts[nm] = counts.get(nm, 0) + 1
        per_level = [0] * (self.max_level + 1)
        for lv in self.level:
            per_level[int(lv)] += 1
        return {"pid": self.pid, "axes": [a.id for a in self.axes],
                "n_total": len(self.ids), "kinds": self.kind_names,
                "kind_counts": counts, "explained": self.explained,
                "max_level": self.max_level, "per_level": per_level,
                "tile_k": TILE_K, "edges": len(self.edges), "shape": "sphere",
                "built_s": round(self.built_s, 2)}

    def _node(self, i: int) -> dict:
        return {"id": self.ids[i], "k": int(self.kinds[i]), "l": self.labels[i],
                "m": int(self.mass[i]), "c": int(self.contest[i]),
                "p": [round(float(v), 4) for v in self.coords[i]],
                "lv": int(self.level[i])}

    def tile(self, lv: int, x: int, y: int, z: int) -> dict:
        idx = self.tiles.get((int(lv), int(x), int(y), int(z))) or []
        mine = set(idx)
        edges = []
        for i in idx:
            for j in self._edges_of.get(i, ()):
                s, t, rel, n = self.edges[j]
                other = t if s == i else s
                # an edge rides the tile of its FINER endpoint (the coarser
                # one is on screen whenever this tile is): no duplicates
                if other in mine and s != i:
                    continue
                if other not in mine and int(self.level[other]) > lv:
                    continue
                edges.append({"s": self.ids[s], "t": self.ids[t], "rel": rel, "n": n})
        has_children = (int(lv), int(x), int(y), int(z)) in self.has_below
        return {"pid": self.pid, "l": lv, "x": x, "y": y, "z": z,
                "nodes": [self._node(i) for i in idx], "edges": edges,
                "more": has_children}

    def locate(self, eid: str) -> dict | None:
        i = self.index.get(eid)
        if i is None:
            return None
        return {"pid": self.pid, **self._node(i),
                "tile": [int(self.level[i])] + [int(c) for c in self.cell[i]]}

    def neighbours(self, eid: str, k: int = 80) -> list[dict]:
        """An entity's whole neighbourhood under this fit: every kept edge
        with the other end's node (loaded on the client or not), strongest
        first -- the selection's highlight and related list."""
        i = self.index.get(eid)
        if i is None:
            return []
        out = []
        for j in self._edges_of.get(i, ()):
            s, t, rel, n = self.edges[j]
            other = t if s == i else s
            out.append({**self._node(other), "rel": rel, "n": n,
                        "dir": "out" if s == i else "in"})
        out.sort(key=lambda d: (-d["n"], -d["m"]))
        return out[:max(1, int(k))]

    def place(self, vec, eid: str = "") -> list[float]:
        """A (moved) state's position on the sphere under this fit (eid:
        the stable direction when the axes see none of it)."""
        if self.mu is None or self.A is None:
            return [0.5, 0.5, 0.5]
        x = np.asarray(vec, dtype=np.float64)[None, :]
        P, frac = _project(x, self.mu, self.A)
        d = _directions(P, frac, [eid])[0]
        srt, med, mad = self.scale
        rad = _spread(float(frac[0]), srt, med, mad)
        return [round(min(1.0, max(0.0, 0.5 + 0.5 * rad * float(d[k]))), 4) for k in range(3)]


def build(rows_factory, unpack, axes_spec: str = "", ref_vec=None, rels=(),
          tile_k: int = TILE_K, sample_top: int = SAMPLE_TOP,
          sample_rand: int = SAMPLE_RAND, chunk: int = CHUNK,
          seed: int = 0) -> Projection:
    """rows_factory() -> a FRESH iterator of row dicts (eid, kind, surface,
    accr, conflicts, recent, state=packed); unpack(packed) -> vector;
    ref_vec(eid) -> an entity's state (for e: axes)."""
    t0 = time.time()
    rnd = random.Random(seed)
    # ---- pass 1: the mean, kind centroids, the sample, the metadata
    total = None
    n = 0
    ksum: dict[str, np.ndarray] = {}
    kcnt: dict[str, int] = {}
    top: list = []                  # (mass, tie, vec) min-heap of the heaviest
    res: list = []                  # the reservoir
    meta = []
    for r in rows_factory():
        if not r.get("eid") or r.get("state") is None:
            continue
        v = np.asarray(unpack(r["state"]), dtype=np.float64)
        total = v.copy() if total is None else total + v
        kd = str(r.get("kind") or "?")
        ksum[kd] = ksum[kd] + v if kd in ksum else v.copy()
        kcnt[kd] = kcnt.get(kd, 0) + 1
        # the metabolism rows carry mass_eff when on; raw accr stays the fallback
        mass = int(round(float(r.get("mass_eff") or r.get("accr") or 0)))
        item = (mass, n, v)
        if len(top) < sample_top:
            heapq.heappush(top, item)
        else:
            low = heapq.heappushpop(top, item)
            if len(res) < sample_rand:
                res.append(low[2])
            else:
                j = rnd.randint(0, n)
                if j < sample_rand:
                    res[j] = low[2]
        label = str(r.get("surface") or r["eid"]).replace("\n", " ").strip()[:LABEL_MAX]
        meta.append((str(r["eid"]), kd, label, mass, len(r.get("conflicts") or []),
                     list(r.get("recent") or [])))
        n += 1
    if not n:
        return Projection([], [], np.zeros((0, 3)), np.zeros(0), [], [], [], [],
                          np.zeros(0, int), np.zeros((0, 3), int), [], [], [], 0.0)
    mu = total / n
    S = np.asarray([t[2] for t in top] + res, dtype=np.float32) - mu.astype(np.float32)
    explained = []
    try:
        # the covariance's eigenvectors = the sample's principal axes; a
        # D x D eigh is far lighter than an SVD of the sample itself
        C = (S.T @ S).astype(np.float64)
        w, V = np.linalg.eigh(C)
        order = np.argsort(-w)[:9]
        vt = _fix_signs(V[:, order].T)
        tot = float(np.clip(w, 0, None).sum()) or 1.0
        explained = [round(float(max(0.0, w[j])) / tot, 4) for j in order]
    except np.linalg.LinAlgError:
        vt = np.zeros((9, S.shape[1]))
    del S
    def _unit(d):
        nd = float(np.linalg.norm(d))
        return d / nd if nd > 1e-12 else None

    axes = []
    for aid in parse_axes(axes_spec):
        if aid.startswith("pc") and aid[2:].isdigit() and 1 <= int(aid[2:]) <= vt.shape[0]:
            axes.append(_Axis(aid, vt[int(aid[2:]) - 1]))
        elif aid.startswith("kind:") and aid[5:] in ksum:
            axes.append(_Axis(aid, _unit(ksum[aid[5:]] / kcnt[aid[5:]] - mu)))
        elif aid.startswith("e:"):
            # e:<eid> and e:<eid>:toward: the signed projection on mean ->
            # entity (#362: linear, so the sphere's direction is defined)
            body = aid[2:]
            eid = body[:-len(":toward")] if body.endswith(":toward") else body
            q = ref_vec(eid) if ref_vec else None
            axes.append(_Axis(aid, None if q is None else _unit(np.asarray(q, dtype=np.float64) - mu)))
        else:
            axes.append(_Axis(aid, None))
    A = np.asarray([a.vec if a.vec is not None else np.zeros_like(mu) for a in axes], dtype=np.float64)
    # ---- pass 2: every row's projection + captured fraction, in chunks
    P = np.zeros((n, 3), dtype=np.float64)
    frac = np.zeros(n, dtype=np.float64)
    i = 0
    buf = []

    def _flush(i, buf):
        Pb, fb = _project(np.asarray(buf), mu, A)
        P[i:i + len(buf)], frac[i:i + len(buf)] = Pb, fb

    for r in rows_factory():
        if not r.get("eid") or r.get("state") is None:
            continue
        buf.append(np.asarray(unpack(r["state"]), dtype=np.float64))
        if len(buf) >= chunk:
            _flush(i, buf)
            i += len(buf)
            buf = []
        if i + len(buf) >= n:
            break
    if buf:
        _flush(i, buf)
    # ---- the sphere: direction = p / |p|, radius = the captured fraction
    # spread by rank + robust z (the cube's per-axis recipe, on the radius)
    ids = [m_[0] for m_ in meta]
    tie = np.asarray([int(hashlib.md5(e.encode()).hexdigest()[:8], 16) for e in ids])
    order = np.lexsort((tie, frac))
    rank = np.empty(n)
    rank[order] = (np.arange(n) + 1.0) / n
    med = float(np.median(frac))
    mad = float(np.median(np.abs(frac - med))) * 1.4826
    z = np.zeros(n) if mad < 1e-12 else np.clip((frac - med) / mad, -3, 3)
    radius = (0.8 * rank + 0.2 * (z + 3.0) / 6.0) ** (1.0 / 3.0)   # even by VOLUME, not by radius
    dirs = _directions(P, frac, ids)
    coords = np.clip(0.5 + 0.5 * radius[:, None] * dirs, 0.0, 1.0).astype(np.float32)
    scale = (np.sort(frac), med, mad)
    # ---- the octree: heaviest first claim the coarsest cell with room
    mass = np.asarray([m_[3] for m_ in meta], dtype=np.int64)
    level = np.zeros(n, dtype=np.int32)
    cell = np.zeros((n, 3), dtype=np.int32)
    fill: dict[tuple, int] = {}
    for i in np.lexsort((tie, -mass)):
        for lv in range(MAX_LEVEL + 1):
            side = 1 << lv
            c = tuple(int(min(side - 1, coords[i, k] * side)) for k in range(3))
            key = (lv,) + c
            if lv == MAX_LEVEL or fill.get(key, 0) < tile_k:
                fill[key] = fill.get(key, 0) + 1
                level[i], cell[i] = lv, c
                break
    # ---- the edges: relations + co-mentions (stop-word sentences wire nothing)
    index = {e: j for j, e in enumerate(ids)}
    agg: dict[tuple, int] = {}
    by_sid: dict[str, list[int]] = {}
    for j, m_ in enumerate(meta):
        for sid in set(m_[5]):
            by_sid.setdefault(str(sid), []).append(j)
    for js in by_sid.values():
        if not 2 <= len(js) <= CLIQUE_MAX:
            continue
        js = sorted(set(js))
        for a in range(len(js)):
            for b in range(a + 1, len(js)):
                key = (js[a], js[b], CO_MENTION)
                agg[key] = agg.get(key, 0) + 1
    for rr in rels or ():
        if not isinstance(rr, dict) or rr.get("superseded"):
            continue
        s, t = index.get(rr.get("subj")), index.get(rr.get("obj"))
        if s is None or t is None or s == t:
            continue
        key = (s, t, str(rr.get("rel") or ""))
        agg[key] = agg.get(key, 0) + int(rr.get("n") or 1)
    # past the cap the strongest stay, then those of the HEAVIEST endpoints
    # (the ones on screen first) -- ties used to break by scan order and a
    # heavy, visible entity could keep none of its edges
    edges = sorted(((s, t, rel, cnt) for (s, t, rel), cnt in agg.items()),
                   key=lambda e: (-e[3], -(int(meta[e[0]][3]) + int(meta[e[1]][3])),
                                  e[0], e[1]))[:MAX_EDGES]
    kind_names = sorted(kcnt, key=lambda kd: (-kcnt[kd], kd))
    kix = {kd: j for j, kd in enumerate(kind_names)}
    return Projection(axes, ids, coords, mass, [kix[m_[1]] for m_ in meta], kind_names,
                      [m_[2] for m_ in meta], [m_[4] for m_ in meta], level, cell,
                      edges, scale, explained, time.time() - t0, mu=mu, A=A)
