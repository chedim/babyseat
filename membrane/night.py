"""THE NIGHT on the entity field: the QuantumEntity overlay's slow clock,
plus the programmable-entities engine that all clocks dispatch to.

The entity field's night lives in efmind.py (births, junk, commons,
activity, the coherence null). THIS module is what the curated overlay
(QuantumEntity tracks = the model's class/summary/script documents, the
banner's track stories) still needs on top of the field:

  consolidate_entities  drift every curated entity's position toward the
                        cells that MOVED ITS FIELD ENTITY lately (mention
                        sentence vectors, time-decayed) and record the
                        motion; hot entities earn overlay docs
  consolidate_now       session:now (EPHEMERAL, momentum-pulsed) +
                        global:now (SHARED, the newest field cells)
  scripts               the lifecycle scripts (delete/nudge/mention/
                        thought/dreamed): exec in a stripped namespace
                        and return {render?, patch?, veto?, delete?} --
                        the CALLER applies (the authorship covenant)

Governors: every phase degrades silently when its substrate is down;
every mutation carries an audit event"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import numpy as np

from . import eids as E
from . import streams as ST
from .entities import (MEMBRANE_NS, CLASS_COMPONENT, CLASS_SESSION,
                       CLASS_TOOL, QuantumEntity, _KIND_CLASS,
                       MultiParentOps)


# ---- programmable entities (the script constants live in entities.py --
# entities.py owns the entity contract; this module imports them) ----

SCRIPT_TIMEOUT_S = 2.0         # wall clock per run
SCRIPT_MAX_FAILURES = 3        # consecutive failures -> auto-disable
SCRIPT_MAX_PER_PASS = 20       # hard ceiling per consolidation tick
SCRIPT_DEFAULT_EVERY_N = 4     # each entity's script samples every Nth
                               # pass (hot entities set 1)
# the SANDBOX IS LIFTED (the operator's 2026-10-01 call): scripts may
# import anything -- os/subprocess included -- and shell out; the engine's
# own budgets (per-run timeouts, the per-pass cap, the output caps) are
# the bounds. No import policy remains.

RUN_TIMEOUT_DEFAULT_S = 20.0   # one membrane_run's budget (the MCP call
RUN_TIMEOUT_MAX_S = 600.0      # transport allows ~30s; longer work goes
                               # through a script on a duty cycle)


def overlay_key(eid: str) -> str:
    """The canonical entity-field id -> the overlay doc's legacy key
    ('f:router.py' -> 'files:router.py'; words/phrases/ctx never have
    overlay docs)."""
    kind, _, val = eid.partition(":")
    back = {v: k for k, v in E._LEGACY_KIND.items()}
    return back.get(kind, kind) + ":" + val if val else eid


def canon_eid(key: str, fld=None) -> str | None:
    """The overlay key -> the field's canonical eid (or None when the
    key is not a field entity: 'session:now', 'global:now', junk
    anchors)."""
    k = key.strip()
    if k.startswith(MEMBRANE_NS):
        k = k[len(MEMBRANE_NS):]
    head, _, rest = k.partition(":")
    if not rest:
        return None
    if head in E._LEGACY_KIND:
        # the overlay key IS an entity-field eid through overlay_key:
        # the round-trip is verbatim -- ''~' bare-file prefixes and
        # paths survive as written (a graph re-resolve would mangle
        # the '~' form into '~~').
        return E._LEGACY_KIND[head] + ":" + rest
    # 'session:now' / 'global:now' / 'synth:x' forms: the now-trackers
    # and junk anchors are overlay-only
    return None


def _cell_points(fld, eid: str, limit: int = 64) -> list:
    """The field cells that moved this entity: its mention deltas' text
    sentences as (vec, ts), newest first (consolidate_position weights
    by recency)."""
    out = []
    for d in fld.deltas_of(eid)[:limit]:
        sid = d.get("sid")
        sv = fld.b.get("sentences", sid) if sid else None
        if sv and sv.get("vec"):
            out.append((np.asarray(sv["vec"], dtype=float),
                        float(d.get("ts") or 0.0)))
    return out


def _audit(mem, rec: dict) -> None:
    try:
        from .opencode import _write_audit
        _write_audit(mem, rec)
    except Exception:
        pass


def consolidate_entities(fld, est, mem=None, materialize: int = 8,
                         only_keys=None) -> dict:
    """Drift every overlay entity's position toward the field cells that
    moved its field entity (mention deltas, time-weighted) and record
    the motion. The field's STATE never moves here (it updates online,
    in order, at mention time) -- only the TRACKER does.

    ``only_keys``: consolidate ONLY these overlay keys (the plant-time
    seeding path -- a fresh track's ring should not wait for a night).
    Returns the drift stats for the gate note."""
    now = time.time()
    mat = 0
    # -- materialization: hot field entities earn overlay docs -- hot =
    #    enough mass; only kinds that make good subjects (files/models,
    #    the operator's real objects) ANNOUNCE their birth; symbols/
    #    configs graduate silently (the doc exists; the banner isn't
    #    told)
    _ANNOUNCE_KINDS = ("files", "models", "f", "m")
    if materialize > 0 and not only_keys:
        hot = []
        for _, d in fld.b.scan("entity_states",
                               {"origin": fld.origin, "tier": "live"}):
            eid = d["eid"]
            if int(d.get("n") or 0) < 3:
                continue
            key = overlay_key(eid)
            if f"{MEMBRANE_NS}{key}" in est.docs or est.find(key) is not None:
                continue
            hot.append((int(d.get("n") or 0), eid, key))
        hot.sort(key=lambda t: -t[0])
        for n, eid, key in hot[:materialize]:
            kind, _, val = key.partition(":")
            ent = QuantumEntity(
                eid=f"{MEMBRANE_NS}{key}",
                cls=_KIND_CLASS.get(kind, CLASS_COMPONENT),
                properties={"name": val, "materialized_by": "night",
                            **({} if kind in _ANNOUNCE_KINDS
                               else {"silent_birth": True})},
                derived_by=["membrane-night"])
            if est.upsert(ent, embedder=getattr(fld, "emb", None)):
                mat += 1
                _audit(mem, {"event": "night-entity-birth",
                             "origin": "night-materialize",
                             "eid": ent.eid, "cls": ent.cls, "cells": n})
    # -- the drift pass --
    drifted = 0
    total_moved = 0.0
    for ent in est.docs.values():
        key = ent.eid[len(MEMBRANE_NS):] \
            if ent.eid.startswith(MEMBRANE_NS) else ""
        if key == "session:now" or key == "global:now":
            continue                 # consolidate_now owns the now-trackers
        if only_keys:
            matched = key in only_keys
            if not matched and ":" in key:
                stripped = key.split(":", 1)[1]
                if stripped in only_keys:
                    matched = True
                    key = stripped   # the class-embedded dual-eid form
            if not matched:
                continue
        eid = canon_eid(key, fld)
        if eid is None:
            continue                 # overlay-only doc (junk anchors, synth)
        cell_pts = _cell_points(fld, eid)
        if not cell_pts:
            continue
        res = ent.consolidate_position(cell_pts, now)
        if res["moved"] > 0:
            drifted += 1
            total_moved += res["moved"]
        emb = getattr(fld, "emb", None)
        if res["moved"] > ent.grain():
            est.upsert(ent, embedder=emb)     # persist the motion
        elif len(ent.position_history) == 1 and not (
                ent.properties or {}).get("_ring_persisted", False):
            # THE SEED PERSISTENCE: a fresh ring's seed moves 0.0 (below
            # grain) -- without persisting, every boot re-seeds into RAM
            # and the geometry never accumulates across boots.
            ent.properties["_ring_persisted"] = True
            est.upsert(ent, embedder=emb)
    return {"drifted": drifted, "materialized": mat,
            "avg_moved": round(total_moved / drifted, 4) if drifted else 0.0}


def consolidate_now(fld, est, mem=None, momentum_vec=None) -> dict:
    """The 'now' trackers (the operator's design: the default entity is the
    session's living present, not the model):

    - session:now -- EPHEMERAL (RAM-only, never persisted, never shared):
      a session's present is nobody else's business; it dies with the
      process. Its cells are the fresh turns' vectors -- the momentum
      EMA is the newest point; the fresh discourse deltas carry the rest.
    - global:now -- SHARED (couchbase write-through): the COLLECTIVE
      drift, heavy half-life (7d), nudged only by the night's slow clock
      from the newest field cells across all seats."""
    now = time.time()
    res = {"moved": 0.0, "velocity": 0.0, "direction": ""}
    # ---- session:now (ephemeral) ----
    eid = f"{MEMBRANE_NS}session:now"
    ent = est.docs.get(eid)
    if ent is None:
        ent = QuantumEntity(
            eid=eid, cls=CLASS_SESSION,
            properties={"name": "now", "grain": 0.003, "ephemeral": True},
            derived_by=["membrane-night"])
        est.docs[eid] = ent            # RAM-only: upsert would persist
    pts = []
    if momentum_vec is not None:
        pts.append((np.asarray(momentum_vec, dtype=float), now))
    # the now-tracker's fresh-cell pull: the newest discourse mentions'
    # sentences (their own subject junk is kept out by the capture, not
    # a blocklist -- the field's boilerplate filter already did the
    # honest thing)
    try:
        last = int(fld._ostats.get("seq_last") or 0)
        for _, d in fld.b.scan("events",
                               {"origin": fld.origin,
                                "stream": "discourse",
                                "seq": {"gt": max(0, last - 12)}},
                               order=("seq",), limit=12):
            sv = fld.b.get("sentences", d.get("sid"))
            if sv and sv.get("vec"):
                pts.append((np.asarray(sv["vec"], dtype=float),
                            float(d.get("ts") or now)))
    except Exception:
        pass
    res = ent.consolidate_position(pts, now, drift_gain=0.9,
                                   record_always=True)
    # ---- global:now (shared) ----
    geid = f"{MEMBRANE_NS}global:now"
    gent = est.docs.get(geid)
    if gent is None:
        gent = QuantumEntity(
            eid=geid, cls=CLASS_SESSION,
            properties={"name": "the global now",
                        "grain": 0.05, "half_life_h": 168.0},
            derived_by=["membrane-night"])
        est.upsert(gent, embedder=getattr(fld, "emb", None))
    gres = {"moved": 0.0}
    gpts = []
    for _, d in fld.b.scan("events",
                           {"origin": fld.origin,
                            "seq": {"gt": max(0, int(
                                fld._ostats.get("seq_last") or 0) - 64)}},
                           order=("seq",), limit=64):
        sv = fld.b.get("sentences", d.get("sid"))
        if sv and sv.get("vec") and (d.get("ts") or 0):
            gpts.append((np.asarray(sv["vec"], dtype=float),
                         float(d.get("ts") or 0)))
    if gpts:
        gres = gent.consolidate_position(gpts, now)
        if gres["moved"] > gent.grain():
            est.upsert(gent, embedder=getattr(fld, "emb", None))
    return {"moved": round(res["moved"], 4),
            "velocity": round(res["velocity"], 4),
            "direction": res["direction"],
            "global_moved": round(gres["moved"], 4) if gpts else 0.0}


# ---- the script engine ----------------------------------------------------------------


def run_entity_script(code: str, state: dict,
                      membrane: "dict | None" = None) -> dict:
    """ONE script execution: exec with the real builtins available (the
    SANDBOX IS LIFTED -- the operator's 2026-10-01 call: scripts may
    import/shell anything; the ENGINE's own budgets -- this call is
    wrapped by the run site's timeout, the pass cap, the output caps --
    are the bounds now), the entity state injected as ``state``, and --
    when a membrane api object is handed -- the read-only navigation
    interface as the ``membrane`` variable.
    Returns the script's ``result`` dict: {render?, data?, delete?,
    patch?, veto?}. Any exception becomes {'error': <msg>} (the caller
    counters failures and turns them into banner events)."""
    import builtins as _bi
    ns: dict = {"__builtins__": dict(_bi.__dict__), "state": state}
    if membrane is not None:
        ns["membrane"] = membrane
    try:
        exec(compile(code, "<entity-script>", "exec"), ns)
        res = ns.get("result")
        return res if isinstance(res, dict) else {}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def run_scripted_entities(fld, est, mem=None, now: float | None = None,
                          force: bool = False) -> dict:
    """Consolidation-time script pass: for every entity carrying scripts,
    run delete_script FIRST (retire wins over nudge), then nudge_script
    (its output {render?, data?} patches the entity's banner fragment
    and script_data). Errors/timeouts count consecutive failures
    (SCRIPT_MAX_FAILURES -> auto-disable) and announce to the banner.

    THE DUTY CYCLE (the operator's 'too many scripts' concern): sensors
    sample, they don't fire continuously -- each entity's scripts run
    every ``script_every_n`` passes (default 4: once/hour at the 15-min
    tick; hot entities set 1), the whole pass caps at
    SCRIPT_MAX_PER_PASS executions (oldest-run first).
    ``force``: the BOOT pass bypasses the duty cycle -- a script edit
    between boots must echo immediately, not wait for the next tick."""
    now = now or time.time()
    stats = {"deleted": 0, "nudged": 0, "errors": 0, "deferred": 0}
    scripted = [(k, e) for k, e in est.docs.items()
                if ((e.properties or {}).get("nudge_script")
                    or (e.properties or {}).get("delete_script"))]
    # THE DUTY CYCLE: pass number mod N -- the eid's hash spreads the
    # slots so entities don't bunch
    pass_no = int(now // 900)          # a 15-minute tick identity
    budget = SCRIPT_MAX_PER_PASS
    for eid, ent in scripted:
        if budget <= 0:
            stats["deferred"] = stats.get("deferred", 0) + 1
            continue
        props = ent.properties or {}
        if props.get("script_disabled"):
            continue
        every = props.get("script_every_n") or SCRIPT_DEFAULT_EVERY_N
        try:
            every = max(1, int(every))
        except (TypeError, ValueError):
            every = SCRIPT_DEFAULT_EVERY_N
        if not force:
            slot = (hash(eid) % max(every, 1))
            if pass_no % max(every, 1) != slot % max(every, 1):
                stats["deferred"] = stats.get("deferred", 0) + 1
                continue
        budget -= 1
        props = ent.properties or {}
        state = _script_state(ent, now,
                              extra={"trigger": "consolidation"},
                              trigger="consolidation",
                              timeout=SCRIPT_TIMEOUT_S)
        # 1. the DELETE script first: retirement precedes any nudge
        dcode = props.get("delete_script")
        if dcode and not props.get("script_disabled"):
            out = run_entity_script(dcode, state)
            if out.get("error"):
                stats["errors"] += 1
                _script_record_fail(fld, est, ent, eid, "delete",
                                    out["error"], mem=mem)
                continue
            ent.properties.pop("script_failures", None)
            if out.get("delete"):
                ent.properties["retired"] = True
                ent.properties["retired_ts"] = now
                est.upsert(ent)
                est._announce("retired", ent)
                _audit(mem, {"event": "agent-entity-retired",
                             "origin": "delete-script", "eid": eid})
                est.unwatch_track(eid[len(MEMBRANE_NS):]
                                  if eid.startswith(MEMBRANE_NS) else eid)
                stats["deleted"] += 1
                continue
        # 2. the NUDGE script: render fragment + data
        ncode = props.get("nudge_script")
        if ncode:
            out = run_entity_script(ncode, state)
            if out.get("error"):
                stats["errors"] += 1
                _script_record_fail(fld, est, ent, eid, "nudge",
                                    out["error"], mem=mem)
                continue
            ent.properties.pop("script_failures", None)
            ent.properties.pop("script_last_error", None)
            changed = False
            if out.get("render"):
                ent.properties["script_render"] = str(out["render"])[:300]
                changed = True
            if out.get("data") is not None and \
                    isinstance(out.get("data"), dict):
                ent.properties["script_data"] = out["data"]
                changed = True
            if changed:
                est.upsert(ent, embedder=getattr(fld, "emb", None))
                stats["nudged"] += 1
                _script_tell(fld, eid, "nudge", True,
                             f"its nudge script rendered: "
                             f"{str(out.get('render') or '')[:160]}")
    return stats


def _script_fail(est, ent, fld=None) -> None:
    """One more strike; SCRIPT_MAX_FAILURES consecutive -> auto-disable
    (a broken script cannot spam stderr or wedge the render)."""
    n = int((ent.properties or {}).get("script_failures") or 0) + 1
    ent.properties["script_failures"] = n
    if n >= SCRIPT_MAX_FAILURES:
        ent.properties["script_disabled"] = True
        _script_error_announce(est, ent.eid, "disabled",
                               f"{n} consecutive failures -- script "
                               f"auto-disabled (clear via "
                               f"membrane_entity_edit)")
    est.save()


def _script_error_announce(est, eid: str, kind: str, error: str) -> None:
    """A script error is a BANNER EVENT (the model sees its own broken
    script and can fix or clear it) -- not a silent stderr line."""
    est._announce("script-error", type("E", (), {
        "eid": eid, "cls": "Component",
        "properties": {"name": kind, "summary": error[:140]}})())


def _script_tell(fld, eid: str, kind: str, ok: bool, text: str) -> None:
    """A script run SPEAKS TO THE FIELD (2026-10-01, the owner's ask):
    a discourse note tagged src='script:<kind>' whose subject is the
    entity itself (prose mentions can't resolve a synthetic eid --
    streams.Event.subject carries it, efield.embed_pending pins it as
    the first own-mention like the code stream's def). The consequence:
    the entity's state drifts toward the event sentence -- errored
    scripts become findable BY SIMILARITY (an ask near the error text
    surfaces the broken entity); and the state diff quotes the
    event (the dormant src-lane in entity_block.state_diff finally has
    a writer). session='' keeps it off the conversation's own lane.
    Degrades everywhere (the field's embedder may still be unresolved;
    the run must never break the script engine)."""
    if fld is None:
        return
    try:
        subj = canon_eid(eid) or ""
        fld.write_events([ST.Event(stream="discourse", role="note",
                                   text=text, ts=time.time(),
                                   src=f"script:{kind}", subject=subj)],
                         session="")
        if subj and getattr(fld.emb, "model", "?") != "?":
            fld.process(limit=8)
    except Exception as e:
        print(f"[night] script event skipped ({e})", file=sys.stderr)


def _script_record_fail(fld, est, ent, eid: str, kind: str,
                        msg: str, mem=None) -> None:
    """ONE failure, all its lanes: the error lands on the entity doc
    (script_last_error -- the error TEXT was never persisted; 3-strikes
    needs it survived), announced (the banner render), spoken into the
    field (the similarity hook) and audited. _script_fail stays the
    strike counter; this is the full record around it."""
    ent.properties["script_last_error"] = msg[:300]
    _script_error_announce(est, eid, kind, msg)
    _script_fail(est, ent, fld=fld)
    _script_tell(fld, eid, kind, False,
                 f"the {kind} script on {eid} errored: {msg}")
    if mem is not None:
        _audit(mem, {"event": "agent-script-error", "origin": kind,
                     "eid": eid, "error": msg[:200]})


def run_command_entity(fld, est, mem, command: str,
                       timeout_s: float | None = None) -> dict:
    """THE MODEL'S HANDS (membrane_run, the owner's 2026-10-01 design):
    a shell command is executed THROUGH its entity. One command -> one
    command entity -- the field's own canon id (`c:<head>`; throwaway
    repeats REUSE the same doc, real commands get their own), class
    Tool, the command line on it. The ENGINE execs it (not the
    user-script sandbox: shell line, subprocess.run, the seat's working
    dir, streams' result head/tail caps), then every lane speaks: the
    doc's run_* result props, an observation event into the field
    (subject-pinned -- the state drifts toward the outcome, so similar
    asks/surfaces find the command), errors in the script-error lanes
    (announce + script_last_error + audit). Returns
    {ok, eid, key, rc, outcome, head, tail, ms, doc, created}."""
    cmd = str(command or "").strip()
    if not cmd:
        return {"ok": False, "error": "empty command"}
    try:
        t = float(timeout_s or RUN_TIMEOUT_DEFAULT_S)
    except (TypeError, ValueError):
        t = RUN_TIMEOUT_DEFAULT_S
    t = max(0.5, min(t, RUN_TIMEOUT_MAX_S))
    heads = ST.shell_heads(cmd)[:1]
    canon_e = E.canon("c", heads[0] if heads else
                      (cmd.split() or ["cmd"])[0][:40])
    key = MEMBRANE_NS + overlay_key(canon_e)
    t0 = time.time()
    err = ""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=t, cwd=os.getcwd())
        rc = p.returncode
        text = "\n".join(x for x in ((p.stdout or "").rstrip("\n"),
                                     (p.stderr or "").rstrip("\n")) if x)
    except subprocess.TimeoutExpired as e:
        rc = -1
        err = f"timeout after {t:g}s"
        text = "\n".join(x.decode(errors="replace") if isinstance(x, bytes)
                         else (x or "") for x in (e.stdout, e.stderr))
    ms = int((time.time() - t0) * 1000)
    head, tail = text[:ST.RESULT_HEAD], text[-ST.RESULT_TAIL:]
    oc = "timeout" if err else ST.outcome(canon_e, head, tail)
    ok = rc == 0 and not err
    if est is None:
        # no entity store mounted: exec, report, degrade (never wedge)
        return {"ok": ok, "eid": canon_e, "key": key, "rc": rc,
                "outcome": oc, "head": head, "tail": tail, "ms": ms,
                "doc": None, "created": False}
    doc = (est.docs or {}).get(key)
    created = doc is None
    if created:
        doc = QuantumEntity(eid=key, cls=CLASS_TOOL,
                            properties={"command": cmd,
                                        "name": canon_e.split(":", 1)[-1],
                                        "silent_birth": True})
    else:
        doc.properties["command"] = cmd
        doc.properties.pop("silent_birth", None)
    t0 = time.time()
    err = ""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=t, cwd=os.getcwd())
        rc = p.returncode
        text = "\n".join(x for x in ((p.stdout or "").rstrip("\n"),
                                     (p.stderr or "").rstrip("\n")) if x)
    except subprocess.TimeoutExpired as e:
        rc = -1
        err = f"timeout after {t:g}s"
        text = "\n".join(x.decode(errors="replace") if isinstance(x, bytes)
                         else (x or "") for x in (e.stdout, e.stderr))
    ms = int((time.time() - t0) * 1000)
    head, tail = text[:ST.RESULT_HEAD], text[-ST.RESULT_TAIL:]
    oc = "timeout" if err else ST.outcome(canon_e, head, tail)
    ok = rc == 0 and not err
    props = doc.properties
    props["run_rc"], props["run_outcome"] = rc, oc
    props["run_head"], props["run_tail"], props["run_ms"] = head, tail, ms
    est.upsert(doc)
    # THE TOOL'S OWN ENTITY (the owner's design: membrane_run is itself
    # ONE entity, auto-updated by every run it executes): the
    # `tool:membrane_run` singleton keeps the rolling record (run/fail
    # counts, last command + rc/outcome, cumulative ms) while each
    # command keeps its own doc; scripts and the night read the
    # singleton to watch the tool lived.
    tk = MEMBRANE_NS + "tool:membrane_run"
    tdoc = (est.docs or {}).get(tk)
    if tdoc is None:
        tdoc = QuantumEntity(eid=tk, cls=CLASS_TOOL,
                             properties={"name": "membrane_run",
                                         "silent_birth": True})
    tp = tdoc.properties
    tp["run_count"] = int(tp.get("run_count") or 0) + 1
    tp["fail_count"] = int(tp.get("fail_count") or 0) + (0 if ok else 1)
    tp["total_ms"] = int(tp.get("total_ms") or 0) + ms
    tp["last_command"], tp["last_rc"] = cmd, rc
    tp["last_outcome"], tp["last_ts"] = oc, time.time()
    est.upsert(tdoc)
    if fld is not None:
        try:
            fld.write_events([ST.Event(
                stream="observation", tool="shell",
                args={"command": cmd, "eid": canon_e},
                result_head=head, result_tail=tail, result_len=len(text),
                outcome=oc, ts=time.time(), subject=canon_e)], session="")
            if getattr(fld.emb, "model", "?") != "?":
                fld.process(limit=8)
        except Exception as e:
            print(f"[night] run event skipped ({e})", file=sys.stderr)
    if not ok:
        msg = err or f"rc={rc}; " + (tail[:140] or "(no output)")
        _script_error_announce(est, key, "run", msg)
        props["script_last_error"] = msg[:300]
        est.upsert(doc)
    if mem is not None:
        _audit(mem, {"event": "agent-shell-run", "origin": "membrane_run",
                     "eid": canon_e, "rc": rc, "ok": ok, "ms": ms,
                     "command": cmd[:200]})
    return {"ok": ok, "eid": canon_e, "key": key, "rc": rc, "outcome": oc,
            "head": head, "tail": tail, "ms": ms, "doc": doc,
            "created": created}


def run_night(srv, fld, est, now: float | None = None) -> dict:
    """The whole night, callable from the NightClock (efmind.night calls
    it, then adds births/junk/commons/activity/null of its own). Every
    failure degrades -- the night must never break the day."""
    out: dict = {}
    now = now or time.time()
    mem = getattr(srv, "membrane", None)
    try:
        # THE SCRIPT PASS first: delete_scripts retire, then nudge_scripts
        # patch render/data -- a retired entity skips its drift work.
        out["scripts"] = run_scripted_entities(fld, est, mem=mem, now=now)
        out["consolidation"] = consolidate_entities(fld, est, mem=mem)
        out["now"] = consolidate_now(fld, est, mem=mem,
                                     momentum_vec=getattr(
                                         srv, "momentum_vec", None))
    except Exception as e:
        print(f"[night] consolidation skipped ({e})", file=sys.stderr)
    return out


# ---- the getter/setter engine (the entity's own policy scripts) ----------
# THE RESOURCE NARROWING: budgets tighten as the trigger approaches the
# operator's live chat -- dreamed (60s idle) 2s, thought (1s hum tick) 1s,
# mention (10s digest) 0.25s. The 15-min consolidation keeps its 2s.
_SCRIPT_TIMEOUTS = {"consolidation": SCRIPT_TIMEOUT_S,
                    "dreamed": 2.0,
                    "thought": 1.0,
                    "mention": 0.25}
_SCRIPT_COOLDOWN_DEFAULT_S = 60.0


def _cooldown_ok(props: dict, trigger: str, now: float) -> bool:
    """Per-entity per-trigger cooldown: a hot entity (session:now gets
    mentioned constantly) cannot exec every tick. properties
    ['script_cooldown_s'] overrides downward-only (the operator's knob)."""
    last = ((props.get("script_cooldowns") or {}).get(trigger) or 0.0)
    cd = _SCRIPT_COOLDOWN_DEFAULT_S
    try:
        cd = min(cd, float((props.get("script_cooldown_s") or cd)))
    except (TypeError, ValueError):
        pass
    return (now - float(last or 0.0)) >= cd


def _stamp_cooldown(props: dict, trigger: str, now: float) -> None:
    cds = props.get("script_cooldowns")
    if not isinstance(cds, dict):
        cds = {}
    cds[trigger] = now
    props["script_cooldowns"] = cds


def run_entity_update_script(srv, ent, trigger: str,
                             extra_state: dict | None = None) -> dict:
    """The getter/setter dispatch (the thinker's + the dreamer's hooks):
    run ONE entity's update_script for this trigger, under the narrowed
    budget (_SCRIPT_TIMEOUTS) + the cooldown, with the membrane
    navigation object injected (membrane.field reads the entity field).

    THE AUTHORSHIP COVENANT: the script NEVER mutates directly -- it
    returns {patch, veto, render, delete} and the CALLER applies them
    inside the store's mut_lock (the engine applies, the entity
    decides)."""
    props = ent.properties or {}
    code = None
    if trigger == "mention":
        code = props.get("mention_script")
    elif trigger == "thought":
        code = props.get("thought_script")
    elif trigger == "dreamed":
        code = props.get("dreamed_script")
    elif trigger == "consolidation":
        code = props.get("nudge_script")
    if not code:
        return {"error": "no script"}
    now = time.time()
    if props.get("script_disabled"):
        return {"error": "disabled (3 strikes)"}
    if not _cooldown_ok(props, trigger, now):
        return {"error": "cooldown"}
    timeout = _SCRIPT_TIMEOUTS.get(trigger, SCRIPT_TIMEOUT_S)
    _stamp_cooldown(props, trigger, now)
    extra = dict(extra_state or {})
    extra["trigger"] = trigger
    extra["timeout_s"] = timeout
    api = _script_membrane_api(ent, srv,
                               getattr(srv, "entity_store", None),
                               trigger, timeout, extra)
    state = _script_state(ent, now, extra=extra,
                          trigger=trigger, timeout=timeout)
    out = run_entity_script(code, state, membrane=api)
    est = getattr(srv, "entity_store", None)
    if out.get("error"):
        if est is not None:
            fld = getattr(srv, "efield", None)
            _script_record_fail(fld, est, ent, ent.eid, trigger,
                                out["error"],
                                mem=getattr(srv, "membrane", None))
    else:
        if ent.properties.get("script_failures"):
            ent.properties.pop("script_failures", None)
        if ent.properties.get("script_last_error"):
            ent.properties.pop("script_last_error", None)
    return out


def _script_membrane_api(ent, srv, est, trigger: str, timeout: float,
                         extra: dict) -> dict:
    """THE MEMBRANE OBJECT (read-only, the operator's design): the
    navigation interface injected next to ``state`` -- the entity
    field's reads, the entity layer's reads, the intent steering.
    NO mutators (the authorship covenant: scripts analyze + return
    results; the engine applies)."""

    class _FieldAPI:
        """Reads over the live entity field (degrade to [] / {} on any
        failure -- a script can never wedge recall)."""
        def __init__(self):
            self._fld = getattr(srv, "efield", None)
        def recall(self, vec, k: int = 5):
            if self._fld is None:
                return []
            return [(eid, round(float(s), 4)) for eid, s in
                    self._fld.recall(vec, k=min(k, 20))]
        def vitals(self):
            try:
                return self._fld.vitals() if self._fld else {}
            except Exception:
                return {}

    class _Me:
        """THIS entity: reads + the derived-geometry methods."""
        def __init__(self, e):
            self.eid = e.eid
            self.cls = e.cls
            self.properties = {k: v for k, v in
                               (e.properties or {}).items()
                               if not str(k).startswith("script_")}
            self.position = list(e.position or [])
            self._ent = e
        def describe(self):
            return self._ent.describe()
        def velocity(self):
            return (self._ent.ring_velocity(time.time())
                    if getattr(self._ent, "position", None) else 0.0)
        def direction(self):
            return self._ent.ring_direction()
        def offsets(self):
            try:
                return MultiParentOps.parent_offsets(est, self.eid)
            except Exception:
                return {}
        def parents(self):
            return list(MultiParentOps._parents_of(
                self._ent.properties or {}))
        def children(self):
            try:
                return [c.eid for c in est.children_of(self.eid)]
            except Exception:
                return []

    class _Entities:
        """The symbolic layer, read-only."""
        def get(self, spec):
            e = est._resolve_spec(spec) if est else None
            if e is None:
                return None
            return {"eid": e.eid, "cls": e.cls,
                    "position": list(e.position or []),
                    "properties": {k: v for k, v in
                                   (e.properties or {}).items()
                                   if not str(k).startswith("script_")}}
        def timelines_of(self, spec, open_only: bool = True):
            try:
                return [e.eid for e in est.timelines_of(
                    spec, open_only=open_only)]
            except Exception:
                return []
        def related_by_class(self, spec, cls, limit: int = 8):
            try:
                return [e.eid for e in
                        est.related_by_class(spec, cls, limit)]
            except Exception:
                return []
        def count(self, cls: str | None = None):
            if cls is None:
                return len(est.docs)
            return sum(1 for e in est.docs.values() if e.cls == cls)

    class _Intents:
        """The steering wheel (open intentions, read-only)."""
        def __init__(self, ib):
            self._ib = ib
        def open(self):
            try:
                return [i.text for i in self._ib.open_intents()]
            except Exception:
                return []
        def nearest(self, vec):
            try:
                need, sim = self._ib.nearest(vec)
                return {"text": need.text, "sim": round(float(sim), 3)}
            except Exception:
                return None

    class _Membrane:
        """The assembled read-only interface (field/me/entities/
        intents)."""
        def __init__(self):
            self.field = _FieldAPI()
            self.me = _Me(ent)
            self.entities = _Entities()
            ib = getattr(srv, "_intent_board", None)
            self.intents = _Intents(ib)
            self.trigger = trigger
            self.timeout_s = timeout
            self.extra = dict(extra)

    return _Membrane()


def _script_state(ent, now: float, extra: dict, trigger: str,
                  timeout: float) -> dict:
    """The script's state: geometry + properties (script_ internal keys
    hidden) + own data + THE TRIGGER SHAPE (what fired, with what) +
    the trigger's timeout (the script can see its own budget)."""
    return {"position": ent.position,
            "ring": [p for _, p in (getattr(ent, "position_history",
                                            []) or [])],
            "velocity": (ent.ring_velocity(now)
                         if getattr(ent, "position", None) else 0.0),
            "cells": len(ent.links or []),
            "properties": {k: v for k, v in (ent.properties or {}).items()
                           if not k.startswith("script_")},
            "data": dict((ent.properties or {}).get("script_data") or {}),
            "now": now,
            "trigger": trigger,
            "timeout_s": timeout,
            "extra": dict(extra)}


def apply_entity_update(srv, ent, out: dict, trigger: str) -> dict:
    """The engine applies the script's RESULT (the authorship covenant:
    scripts analyze + return; the engine writes). The keys:
      render -> properties['script_render'] (the banner fragment)
      patch  -> {prop: value} merges into the entity (bounded: values
                clipped to 2000 chars, the entity's own keys only --
                a script may move ITS OWN doc, never another's)
      delete -> retire the entity (audited)
    Returns the applied summary for the caller's audit."""
    est = getattr(srv, "entity_store", None)
    if est is None or not out:
        return {"applied": 0}
    changed = 0
    props = ent.properties or {}
    if out.get("render"):
        props["script_render"] = str(out["render"])[:300]
        changed += 1
    if isinstance(out.get("patch"), dict):
        for k, v in list(out["patch"].items())[:16]:
            if str(k).startswith("script_"):
                continue               # the script cannot forge engine keys
            props[str(k)[:80]] = (v if not isinstance(v, str)
                                  else v[:2000])
            changed += 1
    if out.get("delete"):
        ent.properties["retired"] = True
        ent.properties["retired_ts"] = time.time()
        changed += 1
    if changed:
        try:
            est.upsert(ent)
        except Exception:
            pass
        # the applied render/patch is field news too (what the entity's
        # own organ decided about itself)
        what = (('render: ' + (props.get("script_render") or '')[:140])
                if out.get("render") else
                'patch: ' + ", ".join(sorted(out.get("patch") or {}))[:140])
        _script_tell(getattr(srv, "efield", None), ent.eid, trigger,
                     True, f"its {trigger} script applied {what}")
    from .opencode import _write_audit
    _write_audit(getattr(srv, "membrane", None),
                 {"event": "agent-entity-scripted", "origin": "update-script",
                  "eid": ent.eid, "trigger": trigger,
                  "applied": changed})
    return {"applied": changed}