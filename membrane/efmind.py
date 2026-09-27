"""THE BACKGROUND MIND ON THE ENTITY FIELD: what the thinker, the dreamer and
the night read when the seat's capture runs the field.

  on_applied    every applied LIVE event (discourse or observation) is a
                stimulus: the thinker digests it (its entities as tags, an
                observation as the fn stamp), and it votes on the dreamer's
                quarantined timeline branches (reality's election)
  night         the NightClock's pass: the entity scripts, BIRTHS (entities
                that crossed specialization x mass, announced once), JUNK
                (words that stay incoherent however often they are said),
                the activity of what moved since the last night, the
                coherence null once a day
  DreamSource   the dreamer's corpus and critic on the field: it proposes
                from the model's own recent sentences and vets a candidate
                by its SURPRISE against the entity states (on topic but
                not already said)"""

from __future__ import annotations

import time

import numpy as np

from . import eids as E
from .efield import fit, unit

BIRTH_MASS = 8            # accreting mentions before an entity can be born
BIRTH_SPEC = 0.30         # specialization: its use drifted from the everyday sense
JUNK_MIN_N = 24           # said this often ...
JUNK_MAX_COH = 0.02       # ... and still no shared direction
NULL_EVERY_S = 86400.0


def legacy_tags(ev: dict) -> list[str]:
    """The event's specific entities in the entity layer's key form
    ('files:x', 'symbols:x', ...): the thinker and the scripts resolve
    those."""
    back = {v: k for k, v in E._LEGACY_KIND.items()}
    out = []
    for eid, _i, _s in ev.get("mentions") or []:
        kind, _, val = eid.partition(":")
        if kind in ("w", "p") or not val:
            continue
        out.append(f"{back.get(kind, kind)}:{val}")
    return list(dict.fromkeys(out))


def fn_of(ev: dict) -> dict | None:
    """An observation as the thinker's fn stamp (op / binds / returns)."""
    if ev.get("stream") != "observation" or not ev.get("tool"):
        return None
    a = ev.get("args") or {}
    return {"op": ev["tool"], "binds": a.get("filePath") or a.get("path") or a.get("command") or "",
            "returns": ev.get("outcome") or ""}


def on_applied_fn(srv, own_prefix):
    """The field listener feeding the thinker and the dreamer's election."""
    from .thinker import feed_capture

    def on_applied(key, ev, vec):
        if ev.get("stream") not in ("discourse", "observation"):
            return
        if not str(ev.get("session") or "").startswith(own_prefix()):
            return
        surprise = ev.get("surprise")
        label = ev.get("role") if ev["stream"] == "discourse" else f"tool {ev.get('tool')}"
        feed_capture(srv, label or "?", ev.get("n"),
                     (ev.get("text") or ev.get("served") or "")[:300],
                     sim=(1.0 - surprise) if isinstance(surprise, float) else None,
                     tags=legacy_tags(ev), fn=fn_of(ev))
        if getattr(srv, "dreamer", None) is not None and ev["stream"] == "discourse" \
                and ev.get("role") == "user":
            from .opencode import _EventView
            from .dreamer import vote_on_turn
            vote_on_turn(srv, _EventView(ev, vec))
    return on_applied


# ---- the night -------------------------------------------------------------------------
def births(fld, est, limit: int = 3) -> list[dict]:
    """Entities that became something of this project's own: enough accreting
    mass and a use that drifted from the everyday sense. Announced once."""
    born = fld._ostats.setdefault("born", [])
    bg = fld._bg("discourse")
    found = []
    for _, d in fld.b.scan("entity_states", {"origin": fld.origin, "tier": "live"}):
        if d["eid"] in born or int(d.get("accr") or 0) < BIRTH_MASS:
            continue
        st = fld.get(d["eid"])
        if st is None or (not E.is_specific(st.eid) and not fld.is_coherent(st)):
            continue
        spec = st.specialization(bg)
        if spec is None or spec < BIRTH_SPEC:
            continue
        found.append((spec * np.log1p(st.mass), st))
    found.sort(key=lambda t: -t[0])
    out = []
    for score, st in found[:limit]:
        ev = fld.evidence(st.eid, k=1)
        a = {"kind": "birth", "eid": st.eid, "cls": st.kind, "name": st.surface or st.eid,
             "summary": (ev[0]["text"][:140] if ev else st.eid), "score": round(float(score), 3)}
        born.append(st.eid)
        out.append(a)
        if est is not None:
            est.announcements.append(a)
    del born[:-5000]
    return out


def junk(fld) -> list[str]:
    """Words said often that never took a direction: the field's filler."""
    mu, sd = fld.null_coherence()
    out = []
    for _, d in fld.b.scan("entity_states", {"origin": fld.origin, "tier": "live",
                                             "kind": ["w", "p"]}):
        if int(d.get("n") or 0) >= JUNK_MIN_N and float(d.get("coherence") or 0) < \
                min(JUNK_MAX_COH, mu - sd):
            out.append(d["eid"])
    fld._ostats["junk"] = sorted(out)[:2000]
    return out


def night(srv, fld, est, now: float | None = None) -> dict:
    """One NightClock pass. Every step degrades on its own."""
    now = now or time.time()
    out: dict = {"ts": now}
    mem = srv.membrane
    if est is not None:
        try:
            from .night import run_scripted_entities
            out["scripts"] = run_scripted_entities(mem, est, now=now)
        except Exception as e:
            out["scripts"] = {"error": str(e)[:120]}
    from . import claims as CL

    def commons():
        n = 0
        gf = getattr(srv, "global_field", None)
        if gf is not None and not fld._ostats.get("global_converted"):
            n += CL.convert_global(fld.b, gf.store.exemplars, fld.graph_fn())
            fld._ostats["global_converted"] = True
        return n + CL.promote_commons(fld.b)
    for name, step in (("births", lambda: [a["eid"] for a in births(fld, est)]),
                       ("junk", lambda: len(junk(fld))),
                       ("commons", commons)):
        try:
            out[name] = step()
        except Exception as e:
            out[name] = {"error": str(e)[:120]}
    try:
        mark = int(fld._ostats.get("night_seq") or 0)
        moved = list(fld.changes_since(mark))[:500]
        for eid in moved:
            fld.refresh_activity(eid)
        fld._ostats["night_seq"] = int(fld._ostats.get("cursor") or 0)
        out["activity"] = len(moved)
    except Exception as e:
        out["activity"] = {"error": str(e)[:120]}
    if now - float(fld._ostats.get("null_ts") or 0) > NULL_EVERY_S:
        try:
            out["null"] = fld.refresh_null()
            fld._ostats["null_ts"] = now
        except Exception as e:
            out["null"] = {"error": str(e)[:120]}
    fld.flush()
    return out


# ---- the dreamer's source ----------------------------------------------------------------
class DreamSource:
    """The dreamer's corpus and critic on the entity field."""

    TOPIC_FLOOR = 0.40
    DUP_SIM = 0.95
    SURPRISE_BAND = (0.08, 0.60)       # novel, yet about what the field knows

    def __init__(self, fld, max_texts: int = 2000):
        self.fld = fld
        self.max_texts = max_texts

    def trusted_texts(self) -> list[str]:
        rows = [d for _, d in self.fld.b.scan("events", {"origin": self.fld.origin,
                                                         "stream": "discourse", "role": "model",
                                                         "status": "applied"})]
        rows.sort(key=lambda d: -(d.get("seq") or 0))
        return [d["text"] for d in rows[:self.max_texts] if d.get("text")]

    def embed(self, text: str) -> np.ndarray:
        return unit(self.fld.emb.embed([text])[0])

    def vet(self, vec) -> bool:
        near = self.fld.recall(vec, k=6)
        if not near or near[0][1] < self.TOPIC_FLOOR:
            return False
        dup = self.fld.b.vector_top_k("sentences", "vec", vec, 1)
        if dup and dup[0][1] >= self.DUP_SIM:
            return False
        A = [self.fld.get(e).state for e, _ in near if self.fld.get(e) is not None]
        surprise = float(1.0 - unit(fit(vec, A)) @ vec) if A else 1.0
        lo, hi = self.SURPRISE_BAND
        return lo <= surprise <= hi
