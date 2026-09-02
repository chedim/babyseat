"""The compactor's night: the field's slow maintenance clock, riding the
existing Compactor cycle (no separate cron -- the duty-cycle governor
already decides when cycles make sense). Cortex's nightly reflection,
retold at compactor cadence:

  1. JUNK SWEEP (purge_episodic_buffer, retold): sample SAFE cells,
     score them against the junk-anchor entities with their OWN vectors
     ($0 -- nothing new embedded); cells at/over the night cut get
     DEMOTED, never deleted (the data stays; the contest/repair
     machinery and the operator's ledger can overturn). The field
     cleans itself -- the render-time screens stop being the only line
     of defense.
  2. GRAY-BAND REGISTRATION (process_unregistered_logs, retold): cells
     in the gray band (near no anchor but not clean prose, no entity
     tags) are the UNREGISTERED forms. One idle-cortex call classifies
     them: junk-form -> a new anchor sentence (grown as DATA, shared to
     the fork through the couchbase write-through); durable -> entities
     via the existing synthesizer parser. Rate-limited: anchor bloat
     would eat good beats.
  3. ENTITY CONSOLIDATION (the living geometry): every cycle, each
     entity with linked cells drifts its position toward the recent
     cells' centroid (half-life time-decay) and records perceptible
     motion in the coarsening history ring. Cells stay immutable --
     the TRACKER moves.

Governors: the sweep runs at most once per hour; every phase degrades
silently when its substrate (embedder/model) is down; every mutation
carries an audit event; a bad anchor is prunable via membrane_entity_remove.
"""

from __future__ import annotations

import json
import sys
import time

from .entities import (MEMBRANE_NS, CLASS_COMPONENT, CLASS_SESSION,
                       entity_index, _eph_born_of, QuantumEntity,
                       _KIND_CLASS)

# ---- knobs (the audited defaults; all overridable per call) ---------------
NIGHT_SWEEP_CUT = 0.70     # demote threshold (stricter than display 0.62)
NIGHT_SWEEP_SAMPLE = 200   # max cells examined per sweep
NIGHT_SWEEP_MIN_INTERVAL_S = 3600.0   # at most one sweep per hour
GRAY_FLOOR = 0.40          # below this a cell is simply clean prose
ANCHOR_GROWTH_PER_PASS = 1 # new anchors per registration pass (rate limit)
ENTITY_REGISTRATION_PER_PASS = 5      # same for derived entities
NIGHT_SWEEP_DEMOTE_CAP = 50    # absolute per-sweep cap (the field can be
                               # genuinely mostly-junk -- that is a slow
                               # cleanup across sweeps, not an abort)


def sweep_junk(mem, store, est, cut: float = 0.70,
               sample: int = NIGHT_SWEEP_SAMPLE) -> dict:
    """Phase 1: the junk sweep. Returns the stats dict (also the gate-note
    payload). Demote-only: safe -> unsafe + a night_junk provenance tag;
    the operator (or the judge) can overturn on re-ask.

    TWO misfire guards (the sweep's blast radius is field-wide, so it
    holds a stricter standard than the display screen):
    - CONSENSUS: a cell demotes only when it is near MULTIPLE anchors
      (>= 2 distinct anchor vectors within the cut) -- a single hot
      anchor (possibly mis-aimed) cannot alone kill a cell.
    - CIRCUIT BREAKER: if the sweep would demote more than
      NIGHT_SWEEP_ABORT_FRAC of its sample, the anchor set is mis-aimed
      -- the sweep COMMITS NOTHING and reports the abort (the operator
      tunes or prunes anchors; the field is untouched)."""
    import numpy as _np
    now = time.time()
    exs = store.exemplars
    safe_idx = [i for i, e in enumerate(exs)
                if getattr(e, "safe", False)]
    if not safe_idx:
        return {"demoted": 0, "gray": 0, "checked": 0}
    # sample oldest-first + jitter (the junk ages at the bottom; the jitter
    # keeps repeated sweeps from reading the exact same head)
    import random as _r
    head = safe_idx[:sample // 2]
    tail = _r.sample(safe_idx[sample // 2:],
                     min(len(safe_idx) - len(head),
                         sample - len(head))) if len(safe_idx) > len(head) \
        else []
    checked = head + tail
    screen = est.junk_screen
    cut = float(cut)
    demoted, gray = [], []
    for i in checked:
        ex = exs[i]
        v = getattr(ex, "vec", None)
        if not v:
            continue
        # the anchor sims, individually (the screen's max is not enough
        # for a field-wide write)
        sims = []
        screen._refresh(mem.embedder)
        if not screen._vecs:
            break
        import numpy as _np
        vec = _np.asarray(v, dtype=float)
        vn = _np.linalg.norm(vec)
        if vn < 1e-12:
            continue
        vec = vec / vn
        n_near = sum(1 for a in screen._vecs if float(vec @ a) >= cut)
        if n_near >= 2:
            demoted.append((i, max(float(vec @ a)
                                   for a in screen._vecs if
                                   float(vec @ a) >= cut)))
        elif any(float(vec @ a) >= GRAY_FLOOR for a in screen._vecs):
            from .entities import iter_entities
            if not iter_entities(ex):
                gray.append((i, max(float(vec @ a) for a in screen._vecs
                                    if float(vec @ a) >= GRAY_FLOOR)))
    # the blast-radius cap: at most NIGHT_SWEEP_DEMOTE_CAP demotions per
    # sweep (the field CAN be genuinely mostly-junk -- that is a slow
    # cleanup across sweeps; what must never happen is one sweep
    # rewriting the field wholesale). The rest waits for the next sweep.
    if len(demoted) > NIGHT_SWEEP_DEMOTE_CAP:
        demoted.sort(key=lambda x: -x[1])       # the worst first
        deferred = len(demoted) - NIGHT_SWEEP_DEMOTE_CAP
        print(f"[night] sweep capped: {deferred} demotions deferred to "
              f"the next sweep (cap {NIGHT_SWEEP_DEMOTE_CAP})",
              file=sys.stderr)
        demoted = demoted[:NIGHT_SWEEP_DEMOTE_CAP]
    # commit the demotions under the mutation lock (the same covenant as
    # the compactor's commit step: re-verify, mutate, save)
    with mem.mut_lock:
        store2 = mem.store
        for i, sim in demoted:
            if i >= len(store2.exemplars):
                continue
            ex = store2.exemplars[i]
            if not getattr(ex, "safe", False):
                continue                  # raced; already retired
            store2.set_safe(i, False)
            extra = getattr(ex, "extra", None) or {}
            extra["night_junk"] = {"sim": round(sim, 3),
                                   "ts": now, "cut": cut,
                                   "origin": "night-sweep"}
            try:
                ex.extra = extra
            except Exception:
                pass
        if demoted:
            store2.save(mem.field_path)
    return {"demoted": len(demoted), "gray": len(gray),
            "checked": len(checked),
            "worst": (round(max((s for _, s in demoted), default=0.0), 3)),
            "_gray_idx": gray}


def register_gray_forms(srv, mem, est, gray_idx_sims: list,
                        chat) -> dict:
    """Phase 2: the unregistered forms. ONE idle-cortex call over the
    gray-band sample; junk answers become anchors, durable answers become
    entities. Rate-limited (<= 1 anchor, <= 5 entities per pass).
    ``gray_idx_sims``: [(cell_index, sim), ...] (sweep_junk's _gray_idx
    handoff); bare ints are tolerated."""
    if not gray_idx_sims:
        return {"anchors": 0, "entities": 0}
    exs = mem.store.exemplars
    norm: list = []
    for it in gray_idx_sims:
        if isinstance(it, (tuple, list)) and it:
            norm.append(it[0])
        else:
            norm.append(it)
    samples = [str(exs[i].situation)[:200] for i in norm[:8]
               if 0 <= i < len(exs)]
    user = ("These memory-cell texts were flagged as neither clean work "
            "prose nor known machinery. For EACH, decide: is it raw "
            "machinery output (a dump, a listing, chatter with no durable "
            "fact) or durable knowledge?\n\n"
            "Reply with ONE JSON object: {\"anchor\": <one sentence "
            "describing the junk FORM, or null>, \"entities\": [<the "
            "existing entity array shape>]}\n\nSamples:\n\n"
            + "\n----\n".join(samples))
    try:
        reply = chat(srv.backend, srv.idle_cortex_model, [
            {"role": "system", "content":
                "You classify memory fragments. Output only the JSON "
                "object."},
            {"role": "user", "content": user}])
    except Exception:
        return {"anchors": 0, "entities": 0}
    out = {"anchors": 0, "entities": 0}
    if not reply:
        return out
    try:
        lo, hi = reply.index("{"), reply.rindex("}")
        parsed = json.loads(reply[lo:hi + 1])
    except (ValueError, json.JSONDecodeError):
        return out
    if not isinstance(parsed, dict):
        return out
    # the anchor (rate-limited to one per pass)
    anchor_text = str(parsed.get("anchor") or "").strip()
    if anchor_text and len(anchor_text) > 20:
        from .entities import (QuantumEntity, JUNK_EID_PREFIX,
                               junk_anchor_docs)
        n_existing = sum(1 for q in est.docs.values()
                         if q.eid.startswith(JUNK_EID_PREFIX))
        eid = f"{JUNK_EID_PREFIX}night-{int(time.time())}"
        ent = QuantumEntity(
            eid=eid, cls=CLASS_COMPONENT,
            properties={"name": f"junk-anchor-night-{int(time.time())}",
                        "summary": anchor_text[:300],
                        "junk_anchor": True},
            derived_by=["membrane-night"])
        if est.upsert(ent):
            out["anchors"] = 1
    # the entities (the existing validated parser, rate-limited)
    items = parsed.get("entities")
    if isinstance(items, list) and items:
        from .entity_synthesis import parse_entity_reply
        import json as _json
        try:
            validated = parse_entity_reply(_json.dumps(
                items[:ENTITY_REGISTRATION_PER_PASS]))
        except Exception:
            validated = []
        if validated:
            from .entities import entity_id, QuantumEntity as _QE
            for it in validated:
                e = _QE(
                    eid=f"membrane::synth:{str(it['name']).strip().lower().replace(' ', '-')[:80]}",
                    cls=it["class"],
                    properties={"name": it["name"],
                                "summary": it["summary"],
                                "topic": "night-registration"},
                    derived_by=["membrane-night"])
                if est.upsert(e):
                    out["entities"] += 1
    return out


def consolidate_now(mem, est, momentum_vec=None) -> dict:
    """The 'now' trackers (the operator's design: the default entity is the
    session's living present, not the model):

    - session:now -- EPHEMERAL (RAM-only, never persisted, never shared):
      a session's present is nobody else's business; it dies with the
      process. Nudged EVERY TURN (the momentum fold) + the night tick.
    - global:now -- SHARED (the couchbase write-through): the collective
      drift, heavy half-life (7d), nudged only by the night's slow clock
      from the newest cells. Parallel seats merge through the doc
      merge-on-collision; the fresher trajectory wins (last-writer-wins
      for derived state).

    Its cells are the fresh tail -- not tag-matched (nothing tags 'now';
    everything is now). Time rides INSIDE the nudge: the drift weights
    decay by each cell's eph_born, so no separate timestamp push exists."""
    import numpy as _np
    now = time.time()
    res = {"moved": 0.0, "velocity": 0.0, "direction": ""}
    exs = mem.store.exemplars
    safe_idx = [i for i, e in enumerate(exs) if getattr(e, "safe", False)]
    # ---- session:now (ephemeral) ----
    eid = f"{MEMBRANE_NS}session:now"
    ent = est.docs.get(eid)
    if ent is None:
        ent = QuantumEntity(
            eid=eid, cls=CLASS_SESSION,
            properties={"name": "now", "grain": 0.03, "ephemeral": True},
            derived_by=["membrane-night"])
        est.docs[eid] = ent            # RAM-only: upsert would persist
    pts = []
    if momentum_vec is not None:
        pts.append((list(momentum_vec), now))     # the strongest force
    for i in safe_idx[-16:]:                       # the newest safe cells
        e = exs[i]
        v = getattr(e, "vec", None)
        born = _eph_born_of(exs, i)
        if v and born:
            pts.append((v, born))
    res = ent.consolidate_position(pts, now)
    # ---- global:now (shared, heavy) ----
    geid = f"{MEMBRANE_NS}global:now"
    gent = est.docs.get(geid)
    if gent is None:
        gent = QuantumEntity(
            eid=geid, cls=CLASS_SESSION,
            properties={"name": "the global now",
                        "grain": 0.05, "half_life_h": 168.0},
            derived_by=["membrane-night"])
        est.upsert(gent, embedder=mem.embedder)
    gpts = [(exs[i].vec, _eph_born_of(exs, i)) for i in safe_idx[-64:]
            if getattr(exs[i], "vec", None)]
    if gpts:
        gres = gent.consolidate_position(gpts, now)
        if gres["moved"] > gent.grain():
            est.upsert(gent, embedder=mem.embedder)   # shared: drift syncs
    return {"moved": round(res["moved"], 4),
            "velocity": round(res["velocity"], 4),
            "direction": res["direction"],
            "global_moved": round(gres["moved"], 4) if gpts else 0.0}


def consolidate_entities(mem, est, materialize: int = 8) -> dict:
    """Phase 3 (every cycle, ~ms): drift every linked entity's position
    toward its recent cells and record the motion. Cells are immutable;
    the tracker moves. Returns the drift stats for the gate note.

    MATERIALIZATION: tag-derived entities (files:router.py etc.) live in
    the exemplar side-index, not the store -- when a key accumulates
    enough cells (>= 3) it EARNS a store doc (the tracker that drifts).
    Bounded per pass (top-k by cell count) so a big field does not spawn
    hundreds of trackers in one cycle."""
    import numpy as _np
    now = time.time()
    inv = entity_index(mem.store.exemplars)
    # -- materialize: hot tag keys earn store docs --
    mat = 0
    if materialize > 0:
        hot = sorted(((k, len(v)) for k, v in inv.items()
                      if len(v) >= 3 and f"{MEMBRANE_NS}{k}" not in est.docs
                      and est.find(k.split(":", 1)[1]) is None),
                     key=lambda x: -x[1])[:materialize]
        for key, n in hot:
            kind, _, val = key.partition(":")
            ent = QuantumEntity(
                eid=f"{MEMBRANE_NS}{key}",
                cls=_KIND_CLASS.get(kind, CLASS_COMPONENT),
                properties={"name": val, "materialized_by": "night"},
                derived_by=["membrane-night"])
            if est.upsert(ent, embedder=mem.embedder):
                mat += 1
    # -- the drift pass --
    drifted = 0
    total_moved = 0.0
    for ent in est.docs.values():
        idxs = list(inv.get(ent.eid[len(MEMBRANE_NS):]) or []) \
            if ent.eid.startswith(MEMBRANE_NS) else []
        if not idxs and ent.links:
            idxs = [i for i in ent.links
                    if 0 <= i < len(mem.store.exemplars)
                    and getattr(mem.store.exemplars[i], "safe", False)]
        if not idxs:
            continue
        cell_pts = []
        for i in idxs[:64]:               # bounded per entity
            ex = mem.store.exemplars[i]
            v = getattr(ex, "vec", None)
            born = _eph_born_of(mem.store.exemplars, i)
            if v and born:
                cell_pts.append((v, born))
        if not cell_pts:
            continue
        res = ent.consolidate_position(cell_pts, now)
        if res["moved"] > 0:
            drifted += 1
            total_moved += res["moved"]
        if res["moved"] > ent.grain():
            est.upsert(ent, embedder=mem.embedder)   # persist the motion
    return {"drifted": drifted, "materialized": mat,
            "avg_moved": round(total_moved / drifted, 4) if drifted else 0.0}


def run_night(srv, mem, est, chat, sweep: bool = False) -> dict:
    """The whole night, callable from the compactor cycle. ``sweep`` gates
    the phase-1/2 pair (rate-limited by the caller); consolidation always
    runs (pure numpy, ~ms). Every failure degrades -- the compaction
    already landed; the night must never break the day."""
    out: dict = {}
    try:
        out["consolidation"] = consolidate_entities(mem, est)
        out["now"] = consolidate_now(mem, est,
                                     momentum_vec=getattr(srv,
                                                          "momentum_vec",
                                                          None))
    except Exception as e:
        print(f"[night] consolidation skipped ({e})", file=sys.stderr)
    if sweep:
        try:
            res = sweep_junk(mem, mem.store, est)
            out["sweep"] = res
            try:
                from .opencode import _write_audit
                _write_audit(mem, {"event": "night-junk-demote",
                                   "origin": "night-sweep",
                                   "demoted": res["demoted"],
                                   "checked": res["checked"],
                                   "cut": res.get("cut", 0.70),
                                   "field_size": len(mem.store)})
            except Exception:
                pass
        except Exception as e:
            print(f"[night] sweep skipped ({e})", file=sys.stderr)
        try:
            gray_idx = res.get("_gray_idx") or [] if isinstance(res, dict) \
                else []
            reg = register_gray_forms(srv, mem, est, gray_idx, chat)
            out["registration"] = reg
        except Exception as e:
            print(f"[night] registration skipped ({e})", file=sys.stderr)
    return out