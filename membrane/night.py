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

import hashlib
import os
import subprocess
import sys
import threading
import time

import numpy as np

from . import eids as E
from . import streams as ST
from .entities import (MEMBRANE_NS, CLASS_COMPONENT, CLASS_SESSION,
                       CLASS_TOOL, QuantumEntity, _KIND_CLASS,
                       MultiParentOps)


# ---- programmable entities (the script constants live in entities.py --
# entities.py owns the entity contract; this module imports them) ----
# THE TICK-PERSIST GATE (the silent applies): at most one store write per
# silent entity per window -- the clock's per-second perspective stays RAM
# (the last tick re-renders within a second of any restart anyway).
_TICK_PERSIST_S = 60.0
_TICK_LAST_PERSIST: dict[str, float] = {}

# THE WATCH WINDOW (the autonode's churn guard)
_WATCH_TELL_MIN_S = 10.0
_WATCH_TELL_TS: dict[str, float] = {}


def _watch_tell_ok(eid: str) -> bool:
    last = _WATCH_TELL_TS.get(eid)
    return last is None or (time.time() - float(last)) >= _WATCH_TELL_MIN_S

SCRIPT_TIMEOUT_S = 2.0         # wall clock per run
SCRIPT_MAX_FAILURES = 3        # consecutive failures -> auto-disable
SCRIPT_MAX_PER_PASS = 20       # hard ceiling per consolidation tick
SCRIPT_DEFAULT_EVERY_N = 4     # each entity's script samples every Nth
                               # pass (hot entities set 1)
# the SANDBOX IS LIFTED (the operator's 2026-10-01 call): scripts may
# import anything -- os/subprocess included -- and shell out; the engine's
# own budgets (per-run timeouts, the per-pass cap, the output caps) are
# the bounds. No import policy remains.

RUN_TIMEOUT_DEFAULT_S = 20.0   # one run's budget (the MCP call
RUN_TIMEOUT_MAX_S = 600.0      # transport allows ~30s; longer work goes
# ---- the file hands (read/write/edit/grep/glob -- the owner's 2026-10-02
# ask: opencode-native hands that LAND on entities like run does) ----
FILE_READ_LINES_DEFAULT = 200  # read's line bound
FILE_READ_LINES_MAX = 600
FILE_LINE_TRUNC = 400          # per-line cap in read output
FILE_GREP_MAX_FILES = 5000     # walk ceiling per search
FILE_LAND_MAX = 12             # matched file docs touched per search
FILE_RESULT_FILES = 60         # matches reported back per search
FILE_SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", ".venv312",
    ".membrane", ".opencode", ".mypy_cache", ".pytest_cache",
    ".tox", "dist", "build"})
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
            # THE WORD SKIP (the overlay-birth covenant): words and
            # phrases are FIELD entities, never overlay subjects (the
            # 'night born: w:showing' flood; the overlay exists for
            # the subjects a cell is ABOUT)
            if eid.split(":", 1)[0] in ("w", "p", "e"):
                continue
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
    for ent in list(est.docs.values()):
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
        # THE MALSIZED REPAIR (the esphome seat): a hydrate residue or
        # foreign-embedder vector (8-dim position vs 1024-dim cells) made
        # consolidate_position's broadcast fail -- and ONE bad entity used
        # to abort the WHOLE drift+now pass, every pass (15x '[night]
        # consolidation skipped' per boot). Drop the stale position; it
        # re-seeds from this entity's own field cells below (the next
        # drift step) -- and the repair persists.
        repaired = False
        if ent.position and len(ent.position) != len(cell_pts[0][0]):
            ent.properties["position_repaired"] = now
            ent.position = None
            repaired = True
        try:
            res = ent.consolidate_position(cell_pts, now)
        except Exception:
            continue                 # one entity's failure is its own
        if repaired:
            est.upsert(ent, embedder=getattr(fld, "emb", None))
            continue
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
                               f"entity_edit)")
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
    """THE MODEL'S HANDS (run, the owner's 2026-10-01 design):
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
    props = doc.properties
    props["run_rc"], props["run_outcome"] = rc, oc
    props["run_head"], props["run_tail"], props["run_ms"] = head, tail, ms
    est.upsert(doc)
    # THE TOOL'S OWN ENTITY (the owner's design: run is itself
    # ONE entity, auto-updated by every run it executes): the
    # `tool:run` singleton keeps the rolling record (run/fail
    # counts, last command + rc/outcome, cumulative ms) while each
    # command keeps its own doc; scripts and the night read the
    # singleton to watch the tool lived.
    tk = MEMBRANE_NS + "tool:run"
    tdoc = (est.docs or {}).get(tk)
    if tdoc is None:
        tdoc = QuantumEntity(eid=tk, cls=CLASS_TOOL,
                             properties={"name": "run",
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
        _audit(mem, {"event": "agent-shell-run", "origin": "run",
                     "eid": canon_e, "rc": rc, "ok": ok, "ms": ms,
                     "command": cmd[:200]})
    return {"ok": ok, "eid": canon_e, "key": key, "rc": rc, "outcome": oc,
            "head": head, "tail": tail, "ms": ms, "doc": doc,
            "created": created}



# ---- THE FILE HANDS (read/write/edit/grep/glob, the owner's 2026-10-02
# design): one call -> one `f:<path>` file entity via the standard canon
# (born cls Component or reused), the action's counters on the doc, an
# observation event subject-pinned to the file, errors down the
# script-error lanes, an audit per op. Same trust as the run lane;
# MEMBRANE_RUN=0 gates both hand families (the check lives in the proxy
# handlers).

def _file_resolve(path) -> str:
    """~ expands, seat-relative joins the cwd -> one absolute path."""
    fp = os.path.expanduser(str(path or "").strip())
    if not fp:
        return ""
    if not os.path.isabs(fp):
        fp = os.path.join(os.getcwd(), fp)
    return os.path.abspath(fp)


def _file_land(fld, est, mem, fp, action, ok, head, detail, err_msg=""):
    """One file call -> its entity: born or reused, counters + detail
    props on the doc, an observation event, errors in the script-error
    lanes, the audit. Returns (eid, key, created)."""
    eid = E.canon("f", fp)
    key = MEMBRANE_NS + overlay_key(eid)
    created = False
    if est is not None:
        doc = (est.docs or {}).get(key)
        if doc is None:
            doc = QuantumEntity(eid=key, cls=CLASS_COMPONENT,
                                properties={"name": os.path.basename(fp) or fp,
                                            "silent_birth": True})
            created = True
        props = doc.properties
        props[f"{action}_count"] = int(props.get(f"{action}_count") or 0) + 1
        props[f"last_{action}_ts"] = props["last_ts"] = time.time()
        for k, v in detail.items():
            props[k] = v
        if not ok:
            props["script_last_error"] = err_msg[:300]
        est.upsert(doc)
    oc = "ok" if ok else "error"
    if fld is not None:
        try:
            fld.write_events([ST.Event(
                stream="observation", tool=action, args={"path": fp},
                result_head=head, result_tail="", result_len=len(head),
                outcome=oc, ts=time.time(), subject=eid)], session="")
            if getattr(fld.emb, "model", "?") != "?":
                fld.process(limit=8)
        except Exception as e:
            print(f"[night] file {action} event skipped ({e})", file=sys.stderr)
    if est is not None and not ok:
        _script_error_announce(est, key, action, err_msg)
    if mem is not None:
        _audit(mem, {"event": "agent-file-op", "origin": action,
                     "eid": eid, "ok": ok, "path": fp[:200]})
    return eid, key, created


def file_read_entity(fld, est, mem, path, offset=None, limit=None) -> dict:
    """THE READ HAND: bounded content (default 200 lines, max 600, long
    lines trimmed), the file's f: entity born or touched, the read
    observed. line strings are 'N: text' like the code readers print."""
    fp = _file_resolve(path)
    if not fp:
        return {"ok": False, "error": "path required"}
    if not os.path.isfile(fp):
        eid = E.canon("f", fp)
        key = MEMBRANE_NS + overlay_key(eid)
        _file_land(fld, est, mem, fp, "read", False, "", {}, f"no such file: {fp}")
        return {"ok": False, "eid": eid, "key": key, "path": fp,
                "error": f"no such file: {fp}"[:200]}
    try:
        off = max(0, int(offset or 0))
        lim = max(1, min(int(limit or FILE_READ_LINES_DEFAULT),
                         FILE_READ_LINES_MAX))
    except (TypeError, ValueError):
        return {"ok": False, "error": "offset/limit must be integers"}
    t0 = time.time()
    try:
        lines, total = [], 0
        with open(fp, "r", encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                total = n
                if n > off:
                    lines.append(f"{n}: {line.rstrip(chr(10))[:FILE_LINE_TRUNC]}")
                    if len(lines) >= lim:
                        break
        text = "\n".join(lines)
        head = text[:ST.RESULT_HEAD]
    except OSError as e:
        err = f"{type(e).__name__}: {e}"
        eid, key, _c = _file_land(fld, est, mem, fp, "read", False, "", {}, err)
        return {"ok": False, "eid": eid, "key": key, "path": fp, "error": err[:200]}
    ms = int((time.time() - t0) * 1000)
    eid, key, created = _file_land(fld, est, mem, fp, "read", True, head,
                                   {"read_lines": len(lines),
                                    "last_read_offset": off,
                                    "total_lines_hint": total})
    return {"ok": True, "eid": eid, "key": key, "path": fp, "created": created,
            "lines": lines, "scanned_lines": total, "ms": ms}


def file_write_entity(fld, est, mem, path, content, create_dirs=True) -> dict:
    """THE WRITE HAND: full-content overwrite (parents made), a no-op
    when the content already matches; the f: entity carries write_count,
    last_write_bytes, last_sha1."""
    fp = _file_resolve(path)
    if not fp:
        return {"ok": False, "error": "path required"}
    body = "" if content is None else str(content)
    t0 = time.time()
    try:
        parent = os.path.dirname(fp)
        if create_dirs and parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        file_created = not os.path.exists(fp)
        if not file_created:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                if fh.read() == body:
                    ms = int((time.time() - t0) * 1000)
                    eid, key, _c = _file_land(
                        fld, est, mem, fp, "write", True,
                        f"write {fp}: unchanged ({len(body)} bytes)",
                        {"write_unchanged": True,
                         "last_sha1": hashlib.sha1(body.encode()).hexdigest()[:10]})
                    return {"ok": True, "eid": eid, "key": key, "path": fp,
                            "bytes": len(body), "unchanged": True, "ms": ms}
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError as e:
        err = f"{type(e).__name__}: {e}"
        eid, key, _c = _file_land(fld, est, mem, fp, "write", False, "", {}, err)
        return {"ok": False, "eid": eid, "key": key, "path": fp, "error": err[:200]}
    ms = int((time.time() - t0) * 1000)
    head = f"write {fp}: {len(body)} bytes (" + ("created" if file_created else "overwrote") + ")"
    eid, key, ent_created = _file_land(
        fld, est, mem, fp, "write", True, head,
        {"last_write_bytes": len(body),
         "last_sha1": hashlib.sha1(body.encode()).hexdigest()[:10],
         "last_write_created": file_created})
    return {"ok": True, "eid": eid, "key": key, "path": fp,
            "created": file_created, "entity_created": ent_created,
            "bytes": len(body), "ms": ms}


def file_edit_entity(fld, est, mem, path, old_string, new_string,
                     replace_all=False) -> dict:
    """THE EDIT HAND: exact literal replace -- one match (or
    replace_all); zero matches and ambiguous matches are REFUSED and
    announced with the refusal on the entity."""
    fp = _file_resolve(path)
    if not fp:
        return {"ok": False, "error": "path required"}
    old = "" if old_string is None else str(old_string)
    new = "" if new_string is None else str(new_string)
    if not old:
        return {"ok": False, "error": "old_string required"}
    t0 = time.time()
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        err = f"{type(e).__name__}: {e}"
        eid, key, _c = _file_land(fld, est, mem, fp, "edit", False, "", {}, err)
        return {"ok": False, "eid": eid, "key": key, "path": fp, "error": err[:200]}
    n = text.count(old)
    if n == 0:
        err = "old_string not found"
        eid, key, _c = _file_land(fld, est, mem, fp, "edit", False, "", {}, err)
        return {"ok": False, "eid": eid, "key": key, "path": fp, "matches": 0,
                "error": err}
    if n > 1 and not replace_all:
        err = (f"old_string matches {n} times (ambiguous); pass "
               "replace_all=true or a longer literal")
        eid, key, _c = _file_land(fld, est, mem, fp, "edit", False, "", {}, err)
        return {"ok": False, "eid": eid, "key": key, "path": fp, "matches": n,
                "error": err}
    subs = n if replace_all else 1
    new_text = text.replace(old, new, subs)
    try:
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    except OSError as e:
        err = f"{type(e).__name__}: {e}"
        eid, key, _c = _file_land(fld, est, mem, fp, "edit", False, "", {}, err)
        return {"ok": False, "eid": eid, "key": key, "path": fp, "error": err[:200]}
    ms = int((time.time() - t0) * 1000)
    head = (f"edit {fp}: {subs} substitution" + ("s" if subs != 1 else "") +
            f" ({len(text)} -> {len(new_text)} chars)")
    eid, key, ent_created = _file_land(
        fld, est, mem, fp, "edit", True, head,
        {"last_edit_subs": subs,
         "last_sha1": hashlib.sha1(new_text.encode()).hexdigest()[:10]})
    return {"ok": True, "eid": eid, "key": key, "path": fp, "subs": subs,
            "created": ent_created, "ms": ms}


def _walk_files(root: str, cap: int = FILE_GREP_MAX_FILES):
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in FILE_SKIP_DIRS)
        for fn in sorted(filenames):
            out.append(os.path.join(dirpath, fn))
            if len(out) >= cap:
                return out
    return out


def file_grep_entity(fld, est, mem, pattern, path=None, glob=None,
                     max_results=None) -> dict:
    """THE GREP HAND: a regex over the seat's files (machinery dirs
    skipped), matches grouped per file with sample lines; the top matched
    files' entities get the pattern pinned (last_grep, grep_hits)."""
    import fnmatch
    import re
    pat = str(pattern or "").strip()
    if not pat:
        return {"ok": False, "error": "pattern required"}
    root = _file_resolve(path) or os.getcwd()
    if not os.path.isdir(root):
        return {"ok": False, "error": f"not a directory: {root}"}
    gfn = str(glob or "").strip()
    try:
        mr = max(1, int(max_results or FILE_RESULT_FILES))
    except (TypeError, ValueError):
        mr = FILE_RESULT_FILES
    mr = min(mr, 240)
    try:
        rx = re.compile(pat)
    except re.error as e:
        return {"ok": False, "error": f"bad pattern: {e}"}
    t0 = time.time()
    files, scanned = [], 0
    for full in _walk_files(root):
        scanned += 1
        rel = os.path.relpath(full, root)
        if gfn and not fnmatch.fnmatch(rel, gfn):
            continue
        hits, samples = 0, []
        try:
            with open(full, "r", errors="ignore") as fh:
                for line in fh:
                    if rx.search(line):
                        hits += 1
                        if len(samples) < 2:
                            samples.append(line.strip()[:160])
                        if hits >= 200:
                            break
        except OSError:
            continue
        if hits:
            files.append((rel, hits, samples))
            if len(files) >= mr:
                break
    files.sort(key=lambda t: (-t[1], t[0]))
    ms = int((time.time() - t0) * 1000)
    for rel, hits, _samples in files[:FILE_LAND_MAX]:
        _file_land(fld, est, mem, os.path.join(root, rel), "grep", True,
                   f"grep /{pat[:80]}/: {hits} hits",
                   {"last_grep": pat[:120], "grep_hits": hits})
    return {"ok": True, "pattern": pat, "root": root, "scanned": scanned,
            "match_files": len(files), "ms": ms,
            "files": [{"path": rel, "hits": h, "samples": s}
                      for rel, h, s in files]}


def file_glob_entity(fld, est, mem, pattern, path=None) -> dict:
    """THE GLOB HAND: fnmatch on seat-relative paths over the walk, the
    matched files listed (machinery dirs skipped) and their entities
    touched (last_glob)."""
    import fnmatch
    pat = str(pattern or "").strip()
    if not pat:
        return {"ok": False, "error": "pattern required"}
    root = _file_resolve(path) or os.getcwd()
    if not os.path.isdir(root):
        return {"ok": False, "error": f"not a directory: {root}"}
    t0 = time.time()
    matches = []
    for full in _walk_files(root):
        rel = os.path.relpath(full, root)
        if fnmatch.fnmatch(rel, pat):
            matches.append(rel)
            if len(matches) >= FILE_RESULT_FILES * 3:
                break
    matches.sort()
    ms = int((time.time() - t0) * 1000)
    for rel in matches[:FILE_LAND_MAX]:
        _file_land(fld, est, mem, os.path.join(root, rel), "glob", True,
                   f"glob {pat[:80]}: hit", {"last_glob": pat[:120]})
    return {"ok": True, "pattern": pat, "root": root, "count": len(matches),
            "files": matches, "ms": ms}

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
                    "mention": 0.25,
                    # the clock organ's pace: the hum's 1s heartbeat is
                    # the trigger, the budget stays sub-perception
                    "tick": 0.25,
                    # the autonode's trigger (membrane/autonode.py): the
                    # BOUNDED POOL (4 threads) is the real limit -- this
                    # budget only strikes the 3-strikes lane on overrun,
                    # the thread cannot be killed
                    "watch": 10.0,
                    # the READ-BACK trigger (the operator's 2026-10-02
                    # ask): a dynamic entity's getter runs on read; the
                    # LRU (watch_agents 5s) paces, this budget bounds
                    "getter": 1.0}
_SCRIPT_COOLDOWN_DEFAULT_S = 60.0
# The watch trigger's own, gentler default: a 60s cooldown would make a
# watcher a per-minute organ -- dead for the thing it exists for. 5s
# coalesces file churn while staying responsive; the real flood guards
# are the 10s tell window, the 4-worker pool and the 64-place backlog.
_SCRIPT_COOLDOWN_WATCH_S = 5.0


# THE CLOCK (the operator's 2026-10-01 ask): ONE seeded entity whose
# tick_script fires on the thinker's heartbeat -- its default script
# prints the current time (the render) and stores it as `last_tick`
# (the last output survives on the doc, across restarts).
DEFAULT_CLOCK_TICK_SCRIPT = (
    "import time as _t\n"
    "now = _t.strftime('%H:%M:%S')\n"
    "result = {'render': 'clock ' + now, 'patch': {'last_tick': now}}"
)


def seed_clock_entity(est) -> bool:
    """The boot seeding: plant `tool:clock` ONCE (idempotent -- a
    REWRITTEN or model-edited clock survives every restart; only a
    missing doc plants the default). False when there is no store or
    the clock already lives."""
    if est is None:
        return False
    eid = MEMBRANE_NS + "tool:clock"
    if eid in (est.docs or {}):
        return False
    doc = (est.docs or {}).get(eid)
    if doc is not None:
        return False
    from .entities import CLASS_TOOL, QuantumEntity
    est.upsert(QuantumEntity(eid=eid, cls=CLASS_TOOL, properties={
        "name": "clock",
        "tick_script": DEFAULT_CLOCK_TICK_SCRIPT,
        "tick_every": 1,
        "silent_birth": True}))
    # THE DURABILITY FIX: flush NOW -- the debounce window (10s) used to
    # outlive a quick restart and re-plant the clock every boot.
    try:
        est.flush()
    except Exception:
        pass
    return True


def _cooldown_ok(props: dict, trigger: str, now: float) -> bool:
    """Per-entity per-trigger cooldown: a hot entity (session:now gets
    mentioned constantly) cannot exec every tick. properties
    ['script_cooldown_s'] overrides downward-only (the operator's knob)."""
    last = ((props.get("script_cooldowns") or {}).get(trigger) or 0.0)
    cd = (_SCRIPT_COOLDOWN_WATCH_S if trigger == "watch"
          else _SCRIPT_COOLDOWN_DEFAULT_S)
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
    decides).

    THE TWO KINDS (the operator's 2026-10-02 ask):
    - CRON (tick / nudge / consolidation -- periodic): BLOCKING PER-
      ENTITY (one run at a time per entity, a wait, never a skip) and
      the OVERRUN covenant: a run whose duration exceeded the entity's
      tick_every_s (or the cooldown) BLOCKS the next run -- the engine
      notifies the model's event pool (kind 'script-cron-overrun'):
      a stuck cron is a decision, not a silent drip.
    - WATCHER (watch / mention / thought / dreamed -- event-triggered):
      the event's arrival is the cadence; no overrun notify; the same
      per-entity lock (one script per entity at a time across kinds).
    ALWAYS: any script error notifies the model's event pool (kind
    'script-error') -- errors are events, never stderr dust."""
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
    elif trigger == "tick":
        code = props.get("tick_script")
    elif trigger == "watch":
        code = props.get("watch_script")
    elif trigger == "getter":
        code = props.get("getter_script")
    if not code:
        return {"error": "no script"}
    now = time.time()
    if props.get("script_disabled"):
        return {"error": "disabled (3 strikes)"}
    # THE TWO KINDS (the operator's 2026-10-02 ask): cron = periodic
    # (blocking per-entity + the overrun covenant); watcher =
    # event-triggered (the arrival is the cadence; the same lock).
    is_cron = trigger in ("tick", "consolidation")
    pool = getattr(srv, "events_pool", None)
    # THE PER-ENTITY LOCK (blocking per-entity): one script per entity
    # at a time ACROSS kinds; the per-entity key = the eid.
    with _entity_script_lock(ent.eid):
        return _run_script_locked(srv, ent, trigger, code, props, now, is_cron,
                                  pool, extra_state)


def _entity_script_lock(eid: str):
    """One lock per entity (a dict of locks under the module lock --
    created on first use; blocking per-entity, concurrent across
    entities)."""
    with _script_locks_guard:
        return _script_locks.setdefault(eid, threading.RLock())


_script_locks: dict = {}
_script_locks_guard = threading.Lock()


def _run_script_locked(srv, ent, trigger, code, props, now, is_cron,
                       pool, extra_state) -> dict:
    if is_cron:
        # THE OVERRUN CHECK (cron only): the LAST run outlasted the
        # interval -> the next run is BLOCKED + the pool notified (a
        # stuck cron is a decision, not a silent drip). Watchers have
        # no interval -- the event's arrival is the cadence.
        last_started = float(props.get("script_last_started") or 0.0)
        last_ms = float(props.get("script_last_ms") or 0.0) / 1000.0
        try:
            _te_s = float(props.get("tick_every_s") or 0.0)
        except (TypeError, ValueError):
            _te_s = 0.0
        try:
            _te_hb = float(props.get("tick_every") or 0.0)
        except (TypeError, ValueError):
            _te_hb = 0.0
        # THE WIPE-PROOF READ (the boredom-poke wedge, earned 2026-10):
        # a proxy re-seed wipes runtime props, so tick_every_s falls
        # back to the 5.0s built-in and a ~7s cron not only overruns,
        # it WEDGES: script_last_ms stays >= the interval until a run
        # succeeds and no run ever succeeds (every fire stays blocked).
        # tick_every (beats, ~1s each) survives the re-seed -- cross-read
        # it as the seconds floor and write the normalized seconds back
        # so this gate and every later reader converge on one value.
        interval = max(_te_s, _te_hb if _te_hb > 1.0 else 0.0)
        if interval < 1.0:
            interval = DEFAULT_TICK_EVERY_S   # no authored cadence
        if not _te_s:
            props["tick_every_s"] = interval
        if last_started and last_ms >= interval:
            ev = {"kind": "script-cron-overrun", "entity": ent.eid,
                  "trigger": trigger,
                  "ran_s": round(last_ms, 2),
                  "interval_s": round(interval, 2), "ts": now}
            if pool is not None:
                pool.put(ev)
            try:
                est = getattr(srv, "entity_store", None)
                if est is not None:
                    _script_error_announce(est, ent.eid, "cron-overrun",
                                           f"{trigger} script ran "
                                           f"{last_ms:.0f}s > interval "
                                           f"{interval:.0f}s; next run "
                                           "blocked")
            except Exception:
                pass
            return {"error": "cron-overrun: next run blocked",
                    "blocked": True}
    if trigger != "tick" and not _cooldown_ok(props, trigger, now):
        return {"error": "cooldown"}   # the tick is paced by tick_every
    timeout = _SCRIPT_TIMEOUTS.get(trigger, SCRIPT_TIMEOUT_S)
    if trigger != "tick":              # tick needs no stamp (never gated)
        _stamp_cooldown(props, trigger, now)
    props["script_last_started"] = now
    est0 = getattr(srv, "entity_store", None)
    _t0 = time.time()
    extra = dict(extra_state or {})
    extra["trigger"] = trigger
    extra["timeout_s"] = timeout
    api = _script_membrane_api(ent, srv,
                               getattr(srv, "entity_store", None),
                               trigger, timeout, extra)
    state = _script_state(ent, now, extra=extra,
                          trigger=trigger, timeout=timeout)
    out = run_entity_script(code, state, membrane=api)
    ms = (time.time() - _t0) * 1000.0
    props["script_last_ms"] = ms
    est = getattr(srv, "entity_store", None)
    if out.get("error"):
        if est is not None:
            fld = getattr(srv, "efield", None)
            _script_record_fail(fld, est, ent, ent.eid, trigger,
                                out["error"],
                                mem=getattr(srv, "membrane", None))
        # THE POOL NOTIFY (errors are events, never stderr dust)
        if pool is not None:
            pool.put({"kind": "script-error", "entity": ent.eid,
                      "trigger": trigger,
                      "error": str(out.get("error"))[:200],
                      "ts": time.time()})
    else:
        if ent.properties.get("script_failures"):
            ent.properties.pop("script_failures", None)
        if ent.properties.get("script_last_error"):
            ent.properties.pop("script_last_error", None)
    return out


DEFAULT_TICK_EVERY_S = 5.0


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
        intents) + speak (the planner-gated voice)."""
        def __init__(self):
            self.field = _FieldAPI()
            self.me = _Me(ent)
            self.entities = _Entities()
            ib = getattr(srv, "_intent_board", None)
            self.intents = _Intents(ib)
            self.trigger = trigger
            self.timeout_s = timeout
            self.extra = dict(extra)
            # THE VOICE (the operator's 2026-10-01 'the word'): an
            # entity script CAN speak into the room -- routed through
            # the SAME planner gate as every other voice (opencode.speak:
            # the ActTable consults, stay-silent drops) and capped at
            # ONE line per fire (the second call within this script
            # returns False -- a chatty script never spams the room).
            said = [False]
            def _speak(text, kind: str = "report-state") -> bool:
                if said[0]:
                    return False
                said[0] = True
                from . import opencode as _oc
                return bool(_oc.speak(srv, str(text)[:500], kind=kind))
            self.speak = _speak

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


def apply_entity_update(srv, ent, out: dict, trigger: str,
                        silent: bool = False) -> dict:
    """The engine applies the script's RESULT (the authorship covenant:
    scripts analyze + return; the engine writes). The keys:
      render -> properties['script_render'] (the banner fragment)
      patch  -> {prop: value} merges into the entity (bounded: values
                clipped to 2000 chars, the entity's own keys only --
                a script may move ITS OWN doc, never another's)
      delete -> retire the entity (audited)
    ``silent=True`` (the tick pace): the apply lands render + props but
    writes NO field tell and NO audit row -- the apply is engine noise
    at that rate, and the mover speaks only via the field-move event.
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
    # THE RUN VERDICT (the exec's only lane, the operator's ruling
    # 2026-10-02): a script returning {run: true} makes the ENGINE
    # exec the entity's OWN 'command' prop (run_command_entity -- the
    # observation event, the result props, the error lanes, the
    # audit); the script's OWN verdict runs first, then the engine
    # runs. The command entity decides THROUGH its script; no tool,
    # no route.
    if out.get("run"):
        cmd = str(props.get("command") or "").strip()
        if cmd:
            run_out = run_command_entity(getattr(srv, "efield", None),
                                         est,
                                         getattr(srv, "membrane", None),
                                         cmd)
            run_out.pop("doc", None)
            out["run_result"] = run_out
            changed += 1
            # THE ENTITY KEEPS ITS OWN RUN'S RESIDUE (the asking
            # entity's props, not the shell-minted doc's): the result
            # (rc/outcome/head/tail) rides the entity that decided
            for k, v in (("run_rc", run_out.get("rc")),
                         ("run_outcome", run_out.get("outcome")),
                         ("run_head", run_out.get("head")),
                         ("run_tail", run_out.get("tail")),
                         ("run_ms", run_out.get("ms"))):
                if v is not None:
                    props[k] = v
            # THE POOL NOTIFY (the exec is an event the loops see)
            pool = getattr(srv, "events_pool", None)
            if pool is not None:
                pool.put({"kind": "entity-run", "entity": ent.eid,
                          "command": cmd[:120],
                          "ok": bool(run_out.get("ok")),
                          "rc": run_out.get("rc"), "ts": time.time()})
        else:
            out["run_result"] = {"ok": False,
                                 "error": "run: true but the entity "
                                          "carries no 'command' prop"}
            changed += 1
    if changed:
        # THE TICK-PERSIST GATE: a silent apply is a per-second RAM
        # perspective -- its properties land on the doc (in place, above)
        # but persisting it would mark the 154MB store dirty EVERY second
        # (a whole-file JSON rewrite every debounce, forever) and
        # re-embed + couchbase-write the doc per tick (86k/day). Silence
        # persists at most once per window, JSON-only; non-silent applies
        # (bounded by their cooldowns) upsert every time as before.
        if not silent:
            try:
                est.upsert(ent)
            except Exception:
                pass
        else:
            _last = _TICK_LAST_PERSIST.get(ent.eid)
            if _last is None or time.time() - _last >= _TICK_PERSIST_S:
                _TICK_LAST_PERSIST[ent.eid] = time.time()
                try:
                    est.upsert(ent, sync=False)
                except Exception:
                    pass
    if changed and not silent:
        # THE WATCH WINDOW (the autonode's churn guard): a hot file
        # tree (or a script_cooldown_s=0 watcher) would flood the field
        # corpus -- ONE apply-tell per eid per 10s window; the event
        # cadence is still bounded by the script cooldown above.
        if trigger == "watch" and not _watch_tell_ok(ent.eid):
            pass
        else:
            # the applied render/patch is field news too (what the entity's
            # own organ decided about itself)
            what = (('render: ' + (props.get("script_render") or '')[:220])
                    if out.get("render") else
                    'patch: ' + ", ".join(sorted(out.get("patch") or {}))[:140])
            _script_tell(getattr(srv, "efield", None), ent.eid, trigger,
                         True, f"its {trigger} script applied {what}")
            if trigger == "watch":
                _WATCH_TELL_TS[ent.eid] = time.time()
    if changed and not silent:
        from .opencode import _write_audit
        _write_audit(getattr(srv, "membrane", None),
                     {"event": "agent-entity-scripted", "origin": "update-script",
                      "eid": ent.eid, "trigger": trigger,
                      "applied": changed})
    return {"applied": changed}