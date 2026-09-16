"""THE UNIVERSE MAP (the entity graph + the field, one 3D scene): the
projection server for the universe viewer -- the map of the field as a
universe (stars = memories, constellations = entities) with
google-map-like controls.

THE SCALE FIX (the old viewer's crampedness, quantified): both layers
spread over ~0.7-0.9 per PCA axis (sigma ~0.15) while the old camera
sat at radius 6-36 -- the whole universe was a sub-unit speck. The
projection here NORMALIZES to a canonical world: the basis is fit once
per process (the stable-basis covenant -- the cloud grows, it does not
spin), then scaled so 3 sigma = the canonical radius (50 units). The
server owns the scale; the client renders in world units.

THE AXES: the raw embedding axes are opaque, but DIRECTIONS are
meaningful and derivable: the class centroids, any named entity's
position, session:now (the living present), the momentum vector. The
axis selector serves these as unit vec3s; the client re-projects with
them (the map as an instrument: rotate the universe toward a word or a
file).
"""
from __future__ import annotations

import numpy as np

CANON_RADIUS = 50.0          # the canonical world radius (3 sigma = 50)
_AXIS_CANDIDATES_MAX = 40    # the axis dropdown's cap


class UniverseProjection:
    """One stable projection per process: the basis + the scale, fit
    once, reused for every layer (the cloud, the entities, the custom
    axes) so the layers stay co-registered."""

    def __init__(self) -> None:
        self._basis: np.ndarray | None = None
        self._mu: np.ndarray | None = None
        self._scale: float = 1.0

    def fit(self, vecs: list) -> None:
        """Fit the basis + the scale from the union of both layers'
        vectors (the cloud + the entities -- one universe, one fit)."""
        import numpy as _np
        if len(vecs) < 4:
            return
        V = _np.array(vecs, dtype=float)
        mu = V.mean(0)
        try:
            _, _, Vt = _np.linalg.svd(V - mu, full_matrices=False)
        except Exception:
            return
        coords = (V - mu) @ Vt[:3].T
        sigma = float(coords.std())
        if sigma < 1e-9:
            sigma = 1.0
        self._basis = Vt[:3]
        self._mu = mu
        # THE SCALE FIX: 3 sigma -> the canonical radius (the world is
        # built for the camera; the client never guesses the scale)
        self._scale = 3.0 * CANON_RADIUS / (3.0 * sigma) \
            if sigma else 1.0
        self._scale = CANON_RADIUS / (3.0 * sigma) if sigma else 1.0

    def ready(self) -> bool:
        return self._basis is not None

    def project(self, vecs: list) -> "np.ndarray | None":
        """Vectors -> (N, 3) world coords in the canonical scale."""
        import numpy as _np
        if not self.ready():
            return None
        try:
            V = _np.asarray(vecs, dtype=float)
            if V.size == 0:
                return None
            if V.ndim == 1:
                V = V.reshape(1, -1)
            coords = (V - self._mu) @ self._basis.T * self._scale
            return coords
        except Exception:
            return None


def _class_centroid(est, cls: str) -> list | None:
    """A class's centroid vec (the semantic direction: 'goal-ness',
    'component-ness' -- the axis candidates)."""
    import numpy as _np
    vs = [e.position if getattr(e, "position", None)
          else e.embedding for e in est.docs.values()
          if e.cls == cls and (getattr(e, "position", None)
                               or getattr(e, "embedding", None))]
    if not vs:
        return None
    V = _np.asarray(vs, dtype=float)
    mu = V.mean(0)
    n = float(_np.linalg.norm(mu))
    return (mu / n).tolist() if n > 1e-12 else None


def axis_candidates(srv, est) -> list[dict]:
    """The axis selector's menu: the PCA basis (the default view), the
    class centroids, the named entities (top by links), session:now, the
    momentum. Each candidate = {id, label, vec} (a unit vec the client
    projects with)."""
    import numpy as _np
    out = [{"id": "pca", "label": "PCA (the default view)",
            "vec": None}]
    if est is not None:
        from .entities import CLASS_DECISION, CLASS_FAILURE, CLASS_GOAL, \
            CLASS_COMPONENT, CLASS_TOOL
        for cls in (CLASS_COMPONENT, CLASS_GOAL, CLASS_FAILURE,
                    CLASS_DECISION, CLASS_TOOL):
            c = _class_centroid(est, cls)
            if c:
                out.append({"id": f"cls:{cls}", "label": cls,
                            "vec": c})
        # the named entities (top by links -- the map's landmarks)
        named = sorted(est.docs.values(),
                       key=lambda e: -len(e.links or []))[:20]
        for e in named:
            pos = getattr(e, "position", None)
            if pos:
                out.append({"id": e.eid,
                            "label": (e.describe() or e.eid)[:60],
                            "vec": [float(x) for x in pos]})
    # session:now (the living present)
    now_ent = est.docs.get("membrane::session:now") if est else None
    if now_ent is not None and getattr(now_ent, "position", None):
        out.append({"id": "session:now", "label": "session:now (the "
                                                   "present)",
                    "vec": [float(x) for x in now_ent.position]})
    # the momentum (where the work is heading)
    mom = getattr(srv, "momentum_vec", None)
    if mom is not None:
        try:
            out.append({"id": "momentum", "label": "momentum (where "
                                                    "the work heads)",
                        "vec": [float(x) for x in mom]})
        except Exception:
            pass
    return out[:_AXIS_CANDIDATES_MAX + 6]
