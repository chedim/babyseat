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
from .efstore import unpack_f16
from .entities import (MEMBRANE_NS, CLASS_COMPONENT, CLASS_SESSION,
                       CLASS_TOOL, QuantumEntity, _KIND_CLASS,
                       MultiParentOps, kind_class)


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
    overlay docs). IDEMPOTENT (the mirror re-mint exhibit): applying
    it to its own output returns the same key (a doubled legacy head
    collapses first, never doubles again)."""
    kind, _, val = eid.partition(":")
    if not val:
        return eid
    back = {v: k for k, v in E._LEGACY_KIND.items()}
    key = back.get(kind, kind) + ":" + val
    # idempotency guard: a doubled head collapses instead of doubling
    if key.count(":") and key.partition(":")[2].partition(":")[2] and \
            key.partition(":")[0] in E._LEGACY_KIND.values():
        k2 = _collapse_mirror(key)
        key = back.get(k2.partition(":")[0],
                       k2.partition(":")[0]) + ":" + k2.partition(":")[2]
    return key


def canon_eid(key: str, fld=None) -> str | None:
    """The overlay key (long form) OR a canonical eid (short form) ->
    the field's canonical eid; None when the key is not a field
    entity ('session:now', 'global:now', junk anchors).
    THE MIRROR COLLAPSE (the seven-boot re-mint exhibit): a DOUBLED
    legacy prefix ('m:m:tool:x', 'files:files:x') collapses to the
    canonical single form -- the read always names the one true
    entity, never its shadows."""
    k = key.strip()
    if k.startswith(MEMBRANE_NS):
        k = k[len(MEMBRANE_NS):]
    k = _collapse_mirror(k)
    head, _, rest = k.partition(":")
    if not rest:
        return None
    if head in E._LEGACY_KIND:
        # the long overlay form -> the canonical short form
        return E._LEGACY_KIND[head] + ":" + rest
    # the SHORT canonical forms pass through verbatim (a canonical
    # eid is its own canon -- 'm:tool:sysmon', 'f:membrane/x.py',
    # 'tool:sysmon' -- canon()'s kind set: letters + port/tool/ctx)
    _short = set(E._LEGACY_KIND.values()) | {"port", "tool", "ctx", "w",
                                             "p", "files", "models",
                                             "symbols", "shell", "urls"}
    if head in _short:
        return k
    # 'session:now' / 'global:now' / 'synth:x' forms: the now-trackers
    # and junk anchors are overlay-only
    return None


def _collapse_mirror(k: str) -> str:
    """The doubled-head stripping: while the head AND the rest's head
    are BOTH kind heads (long or short), peel one -- the canonical
    head wins ('m:m:tool:x' -> 'm:tool:x'; 'files:files:x' -> 'f:x';
    'tool:tool:x' -> 'tool:x')."""
    _canonical = set(E._LEGACY_KIND.values()) | {"port", "tool", "ctx",
                                                 "w", "p", "files",
                                                 "models", "symbols",
                                                 "shell", "urls"}

    def _is_head(h: str) -> bool:
        return h in E._LEGACY_KIND or h in _canonical

    for _ in range(16):                    # the family's nesting cap
        head, sep, rest = k.partition(":")
        if not sep or not rest:
            return k
        head_c = E._LEGACY_KIND.get(head, head)
        rest_head, rest_sep, rest_rest = rest.partition(":")
        if not rest_sep:
            return head_c + ":" + rest
        if _is_head(rest_head):
            # THE PEEL, two shapes:
            # same family doubles -> one head ('m:m:x' -> 'm:x');
            # a CHAIN ('m:c:git': models-outer over command-inner --
            # the minter's stale wrapper) -> the INNER kind's doc
            # is the real one: peel the OUTER head, keep the inner
            # kind's canonical form ('m:c:git' -> 'c:git').
            if head_c == E._LEGACY_KIND.get(rest_head, rest_head):
                k = head_c + ":" + rest_rest
                continue
            # THE CHAIN PEEL only for a LONG outer head (the stale
            # wrapper's shape: 'm' SHORT-outer is an honest pair in
            # the field's spelling -- 'm:tool:sysmon' stays)
            if head in E._LEGACY_KIND and rest_head in _canonical:
                k = E._LEGACY_KIND.get(rest_head, rest_head) + ":" + rest_rest
                continue
            return head_c + ":" + rest
        return head_c + ":" + rest
    return k


def _cell_points(fld, eid: str, limit: int = 64) -> list:
    """The field cells that moved this entity: its mention deltas' text
    sentences as (vec, ts), newest first (consolidate_position weights
    by recency)."""
    out = []
    for d in fld.deltas_of(eid)[:limit]:
        sid = d.get("sid")
        sv = fld.b.get("sentences", sid) if sid else None
        if sv and sv.get("vec"):
            out.append((unpack_f16(sv["vec"]),
                        float(d.get("ts") or 0.0)))
    return out


def _audit(mem, rec: dict) -> None:
    try:
        from .opencode import _write_audit
        _write_audit(mem, rec)
    except Exception:
        pass


def materialized_class_for(eid: str) -> str:
    """The materialized doc's class: the SOURCE kind's class carries
    (a shell command is a Tool, never a Model -- the chain-mint
    exhibit); the overlay key's kind names it."""
    kind, _, _ = overlay_key(canon_eid(eid) or eid).partition(":")
    return _KIND_CLASS.get(kind, kind_class(kind))


def materialized_eid(eid: str) -> str:
    """The materialized doc's eid: the collapsed canonical -> its
    overlay key under the membrane namespace (one TRUE id, never a
    chain, never a mirror). A CHAIN's outer head is a stale wrapper
    when it's a LONG legacy spelling over a DIFFERENT inner kind
    ('models:c:git': a command minted under models) -- the INNER
    kind's doc is the true one; peel to it."""
    cid = canon_eid(eid) or eid
    if cid.startswith(MEMBRANE_NS):
        cid = cid[len(MEMBRANE_NS):]
    # THE CHAIN PEEL (the mint surface's honest id): ANY kind-head
    # chain whose heads live in DIFFERENT families is a stale
    # wrapper -- the INNER kind names the true doc ('m:c:git' ->
    # 'c:git'; 'm:tool:sysmon' -> 'tool:sysmon'). The same-family
    # doubles canon_eid already peeled.
    for _ in range(8):
        head, _, rest = cid.partition(":")
        rest_head = rest.partition(":")[0]
        if not rest.partition(":")[1] or not (head or rest_head):
            break
        fam_head = _kind_family_head(head)
        fam_rest = _kind_family_head(rest_head)
        if fam_head and fam_rest and fam_head != fam_rest:
            cid = E._LEGACY_KIND.get(rest_head, rest_head) + ":" + \
                rest.partition(":")[2]
        else:
            break
    return MEMBRANE_NS + overlay_key(cid)


def _kind_family_head(head: str) -> str | None:
    """A kind head's FAMILY name (both spellings) or None when the
    head is not a kind."""
    canon_head = E._LEGACY_KIND.get(head, head)
    for fam, members in _KIND_HEAD_FAMILIES.items():
        if canon_head in members:
            return fam
    return None

_KIND_HEAD_FAMILIES = {
    "file": {"f", "files"},
    "symbol": {"s", "symbols"},
    "model": {"m", "models"},
    "command": {"c", "shell"},
    "url": {"u", "urls"},
    "ha": {"ha"},
    "tool": {"tool"},
    "port": {"port"},
    "ctx": {"ctx"},
    "word": {"w"},
    "phrase": {"p"},
}


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
            # THE EXISTENCE CHECK + THE COLLAPSE (the minter's defects:
            # 'no existence check' + 'the chain mint' -- an eid like
            # 'm:c:git' must collapse BEFORE the overlay: overlaying a
            # CHAIN minted 'models:c:git' -> kind 'models' -> AIModels
            # -- a shell command is not a model):
            cid = canon_eid(eid) or eid
            if cid.startswith(MEMBRANE_NS):
                cid = cid[len(MEMBRANE_NS):]
            key = overlay_key(cid)
            if f"{MEMBRANE_NS}{key}" in est.docs or est.find(key) is not None:
                continue
            hot.append((int(d.get("n") or 0), cid, key))
        hot.sort(key=lambda t: -t[0])
        for n, eid, key in hot[:materialize]:
            kind, _, val = key.partition(":")
            ent = QuantumEntity(
                eid=f"{MEMBRANE_NS}{key}",
                cls=_KIND_CLASS.get(kind, kind_class(kind)),
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
                pts.append((unpack_f16(sv["vec"]),
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
            gpts.append((unpack_f16(sv["vec"]),
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
        # THE GRAVITY READ (the heat meter's third consumer): hot
        # entities' nudges defer more passes (the effective every_n).
        try:
            every = effective_tick_every(props, every)
        except Exception:
            pass
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
                # THE SCREEN (the poison exhibit): script data rides
                # the same json-safe gate as the patch lane's values.
                from .entities import sanitize_properties as _san
                ent.properties["script_data"] = _san(out["data"])
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
    """THE ERROR BUBBLE (the entity-scripts covenant: everything is an
    entity; the model sees entities only): an error surfaces as a NEW
    ENTITY dropped near the errored one -- no pool events, no pushed
    banner lines. The bubble:
      eid   membrane::error:<fingerprint>  (the errored eid + trigger +
            the error head -- dedupe is identity)
      pos   the errored entity's position (or the store-free None --
            the bubble drifts in the field when evidence lands)
      props errored (= the origin eid), kind, error, n_hits, first_ts,
            last_ts (refreshed WHILE the error recurs -- active errors
            stay near the top; when fixed, no refreshes happen and the
            bubble drifts back in time on the UPDATED axis by itself).
    ``_script_error_announce`` (the old banner push lane) is RETIRED.
    ``kind`` here is the script trigger (tick/watch/...) for an EXEC
    error, or a lane name ('cron-overrun', 'disabled'...) for the
    engine's own verdicts.
    ``pos``: the errored entity's position — the bubble is dropped NEAR it."""
    if est is None:
        return
    err_head = (error or "")[:80]
    fp_src = f"{eid}|{kind}|{err_head}"
    fp = hashlib.sha1(fp_src.encode()).hexdigest()[:12]
    bub_eid = f"{MEMBRANE_NS}error:{fp}"
    now = time.time()
    old = est.docs.get(bub_eid)
    origin = est.docs.get(eid)
    pos = list(origin.position) if origin is not None and \
        getattr(origin, "position", None) else None
    if old is not None:
        # THE REFRESH: the same error recurred -- one entity, n_hits
        # up, last_ts re-stamped (active errors STAY near the top).
        props = old.properties or {}
        props["n_hits"] = int(props.get("n_hits") or 1) + 1
        props["last_ts"] = now
        props["error"] = err_head          # the latest rendering
        if pos:
            old.position = pos             # re-dropped near its origin
        est.upsert(old, embedder=None)
        return
    props = {"name": f"error: {kind}",
             "errored": eid, "kind": kind, "error": err_head,
             "n_hits": 1, "first_ts": now, "last_ts": now,
             "guest": False}
    bub = QuantumEntity(eid=bub_eid, cls="Component",
                        properties=props)
    if pos:
        bub.position = pos                 # dropped near its origin
    est.docs[bub_eid] = bub
    est.upsert(bub, embedder=None)


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
    lanes, the audit. Returns (eid, key, created).
    THE MIRROR COLLAPSE (the seven-boot re-mint exhibit): any existing
    MIRROR doc under a doubled key (the stale boots' residue) retires
    into the landing doc (merged_into -- mark-don't-delete): the data
    merges, the shadow dies, the canonical grows."""
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
        # THE MIRRORS RETIRE (all the doubled-key shadows of THIS file):
        body = eid.partition(":")[2]
        for bad_k in list((est.docs or {}).keys()):
            if bad_k == key or MEMBRANE_NS not in bad_k:
                continue
            bad_body = bad_k[len(MEMBRANE_NS):]
            if canon_eid(bad_k) == eid and bad_body != body:
                _merge_mirror_into_doc(est, bad_k, doc)
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


def _merge_mirror_into_doc(est, bad_key: str, doc) -> None:
    """THE MIRROR'S RETIRE: mark-don't-delete (the operator's covenant)
    -- the shadow's props merge into the canonical doc (counters max'd,
    script_render kept when the canon has none), the shadow stamped
    merged_into (the graph reader follows the pointer) and retired."""
    bad = (est.docs or {}).get(bad_key)
    if bad is None or doc is None:
        return
    badp = bad.properties or {}
    docp = doc.properties or {}
    for k, v in badp.items():
        if k.startswith("script_") or k in ("merged_into", "retired_ts"):
            continue
        if str(k).endswith("_count") or k == "n":
            docp[k] = max(int(docp.get(k) or 0), int(v or 0))
        elif k not in docp:
            docp[k] = v
    badp["merged_into"] = doc.eid
    badp["retired_ts"] = time.time()
    doc.properties = docp
    bad.properties = badp
    est.upsert(doc, embedder=None)
    est.upsert(bad, embedder=None)


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
                    # THE TICK'S HARD BUDGET (the 146s-poke exhibit):
                    # the tick is the HUM's beat -- a script over
                    # budget is ABANDONED (its result thread lands
                    # out-of-band; the per-entity lock blocks the next
                    # fire until then), and the overrun STRIKES (3
                    # slow runs -> disabled) -- a slow-success is a
                    # strike too.
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

# ---- THE HEAT METER (the gravity limiter's one measure) ---------------
# Two signals, both $0, both already collected:
# - FIELD CHURN: the entity's state-update rate (the field's flush
#   stamps last_ts per change) -- EMA'd, half-life HEAT_HALF_LIFE_S.
# - SCRIPT CPU: the entity's OWN scripts' process_time per run -- the
#   wall lie (a sleeper) never counts (the operator's parenthetical).
# entity_heat = max of the two, each normalized against its own cap
# (churn vs 1 update/s, cpu vs the kind's budget) -- 0..inf with both
# floors comparable. Gravity (1 -> x8) stretches the entity's OWN
# cadences; freeze pauses the field-watches (the lane's covenant).
HEAT_CHURN_CAP_S = 1.0        # 1 change/s = heat churn 1.0
HEAT_CPU_CAP_S = 0.25         # the tick budget = the CPU cap's floor
HEAT_HALF_LIFE_S = 20.0
HEAT_GRAVITY_MAX = 8.0        # the stretch ceiling
HEAT_COLD = float(os.environ.get("MEMBRANE_HEAT_COLD") or 0.3)
HEAT_FREEZE = float(os.environ.get("MEMBRANE_HEAT_FREEZE") or 1.5)
HEAT_MELT_S = float(os.environ.get("MEMBRANE_HEAT_MELT_S") or 60.0)


def _heat_ema(old: float, new: float, dt: float, half_life: float
              = HEAT_HALF_LIFE_S) -> float:
    """The decayed EMA: the new sample lands, the old decays by
    half-life (a fast entity's history fades in ~a minute)."""
    import math as _m
    decay = 0.5 ** (max(0.0, dt) / max(0.001, half_life))
    return old * decay + new * (1.0 - decay)


def note_script_cpu(est, eid: str, cpu_ms: float, wall_ms: float = 0.0,
                    ts: float | None = None) -> None:
    """The CPU sample lands (from _run_script_locked's exec wrap): the
    entity's script_cpu_ema_ms EMA's the honest CPU (a sleeper's wall
    never counts). The store's RAM doc only -- the heat is seat-local
    (never a couchbase write)."""
    if est is None or eid is None:
        return
    ent = (est.docs or {}).get(eid)
    if ent is None:
        return
    ts = ts if ts is not None else time.time()
    props = ent.properties or {}
    old = float(props.get("script_cpu_ema_ms") or 0.0)
    had_prior = "script_cpu_last" in props
    old_ts = float(props.get("script_cpu_last") or ts)
    dt = max(0.0, ts - old_ts)
    # normalize to per-second cpu (the sample's rate, half-life decayed);
    # a FIRST sample (no prior) lands directly (there's no history to
    # decay against -- the meter can't read 0 forever)
    rate = (cpu_ms / 1000.0) / max(0.05, min(dt, 30.0))
    ema = (_heat_ema(old / 1000.0, rate, dt) if had_prior and dt > 0
           else rate) * 1000.0
    props["script_cpu_ema_ms"] = ema
    props["script_cpu_last"] = ts
    props["script_wall_last_ms"] = wall_ms   # the visible wall (the lie)
    ent.properties = props


def entity_heat(ent_or_props) -> float:
    """The entity's heat (0..inf): the max of the chilled churn lane
    and the script-CPU lane (each against its cap).
    THE DECAY ON READ: the ema decays by the time since its last
    sample's STAMP (silence cools the entity without a new sample --
    the read is where the half-life lives)."""
    props = (ent_or_props.properties or {}
             if hasattr(ent_or_props, "properties") else ent_or_props or {})
    import math as _m
    # THE OVERRIDE PROP (the field's own meter may stamp entity_heat:
    # an explicit value wins over the computed one -- the meter's
    # write is the truth when it exists); the override decays too
    # (by its own stamp's age -- the half-life's read).
    ov = props.get("entity_heat")
    last = float(props.get("heat_stamp_ts")
                 or props.get("script_cpu_last") or 0.0)
    dt = max(0.0, time.time() - last) if last else 0.0
    decay = 0.5 ** (dt / max(0.001, HEAT_HALF_LIFE_S))
    cpu = (float(props.get("script_cpu_ema_ms") or 0.0) / 1000.0) * decay
    cpu_h = cpu / max(0.05, HEAT_CPU_CAP_S)
    churn = float(props.get("churn_ema") or 0.0) / max(0.05, HEAT_CHURN_CAP_S)
    computed = max(cpu_h, churn)
    return float(ov) * decay if ov is not None else computed


def tick_gravity(props) -> float:
    """The stretch multiplier (1..HEAT_GRAVITY_MAX): continuous, the
    cold bar floors it at 1."""
    h = entity_heat(props)
    if h <= HEAT_COLD:
        return 1.0
    g = 1.0 + (h - HEAT_COLD) * 3.0
    return float(min(HEAT_GRAVITY_MAX, g))


def effective_tick_every(props, authored: int) -> int:
    """The cadence the tick pass uses: authored x gravity, never below
    the authored (gravity only stretches)."""
    return max(int(authored or 1),
               int(round(int(authored or 1) * tick_gravity(props))))


def entity_frozen(props) -> bool:
    """The freeze bar: over HEAT_FREEZE => the field-watches pause.
    The bar reads the env LIVE (the operator's knob turns mid-flight;
    a module constant would need a restart)."""
    try:
        bar = float(os.environ.get("MEMBRANE_HEAT_FREEZE") or HEAT_FREEZE)
    except (TypeError, ValueError):
        bar = HEAT_FREEZE
    return entity_heat(props) > bar


def entity_melted(props) -> bool:
    """The melt gate: the frozen_ts sat longer than HEAT_MELT_S (the
    cool window) AND the heat reads under the bar now."""
    ts = float(props.get("frozen_ts") or 0.0)
    if not ts:
        return True
    return time.time() - ts >= HEAT_MELT_S and \
        not entity_frozen(props)


# ---- THE FIELD-WATCH LANE (the model's filters over the field) --------
# The model plants up to WATCH_FIELDS_CAP filters per entity
# (entity_edit watch_fields=[{verb, arg}]); the field's flush sweeps
# them as state changes land; a crossing delivers ONE 'field:changed'
# event to the ordinary pool (the drainer admits it like any fire).
# THE GRAVITY'S GROUPING: a hot entity's suppressed changes accumulate
# into one burst event (n_changes, span) -- delivered on the heat's
# subsidence or WF_MAX_LATENCY_S; frozen filters pause entirely.
# VERBS (fixed; the entity's OWN field geometry only):
#   pos(s)  -- chord distance from the LAST STAMPED position > arg
#   align(f)-- coherence/net-align BELOW arg (crossing down)
#   vel(v)  -- ring_velocity OVER arg (crossing up)
WATCH_FIELDS_CAP = 3
WATCH_FIELDS_VERBS = frozenset(("pos", "align", "vel"))
WF_MAX_LATENCY_S = 120.0
WF_BURST_KEY = "wf_burst"          # props[WF_BURST_KEY] = {n, since}


def edit_watch_fields(est, eid: str, filters) -> dict:
    """THE MODEL'S PLANT (entity_edit watch_fields=): a list of
    {verb, arg} filters (cap 3, verbs fixed). Empty list CLEARS."""
    ent = (est.docs or {}).get(eid)
    if ent is None:
        return {"ok": False, "error": f"no entity: {eid}"}
    if filters is None:
        return {"ok": False, "error": "watch_fields required"}
    if not isinstance(filters, list):
        return {"ok": False, "error": "watch_fields must be a list"}
    if len(filters) > WATCH_FIELDS_CAP:
        return {"ok": False,
                "error": f"cap {WATCH_FIELDS_CAP} filters per entity "
                         f"(got {len(filters)}); drop one first"}
    clean = []
    for f in filters[:WATCH_FIELDS_CAP]:
        if not isinstance(f, dict):
            return {"ok": False, "error": "each filter is {verb, arg}"}
        verb = str(f.get("verb") or "").strip().lower()
        if verb not in WATCH_FIELDS_VERBS:
            return {"ok": False,
                    "error": f"unknown verb {verb!r} (the field's "
                             f"verbs: pos, align, vel)"}
        try:
            arg = float(f.get("arg"))
        except (TypeError, ValueError):
            return {"ok": False, "error": f"{verb}'s arg must be a number"}
        clean.append({"verb": verb, "arg": arg})
    props = ent.properties or {}
    props["watch_fields"] = clean
    props.pop("wf_stamp_pos", None)       # fresh stamps (re-arm)
    props.pop("wf_stamp_align", None)
    props.pop("wf_stamp_vel", None)
    props.pop(WF_BURST_KEY, None)
    ent.properties = props
    est.upsert(ent, embedder=None)
    return {"ok": True, "watch_fields": clean}


def _field_filter_hits(ent, props) -> list[dict]:
    """The filters that CROSS on this change (crossing semantics: fire
    on the transition, re-arm on return). Stamps live in the props
    (wf_stamp_<verb>); each verb's hit RE-STAMPS (the re-arm is the
    return crossing)."""
    hits = []
    for f in (props.get("watch_fields") or [])[:WATCH_FIELDS_CAP]:
        verb, arg = f["verb"], float(f["arg"])
        stamp_k = f"wf_stamp_{verb}"
        if verb == "pos":
            import numpy as _np
            pos = getattr(ent, "position", None)
            stamp = props.get("wf_stamp_pos")
            if pos is None or not stamp:
                if pos is not None:
                    props[stamp_k] = list(pos)
                continue
            d = float(_np.linalg.norm(
                _np.asarray(pos, dtype=float) -
                _np.asarray(stamp, dtype=float)))
            if d > arg:
                hits.append({"verb": "pos", "arg": arg, "dist": round(d, 4)})
                props[stamp_k] = list(pos)
        elif verb == "align":
            v = float(props.get("coherence")
                      or props.get("net_align") or 0.0)
            stamp = props.get("wf_stamp_align")
            if stamp is None:
                props[stamp_k] = v
                continue
            if float(stamp) >= arg > v:     # crossed DOWN through the bar
                hits.append({"verb": "align", "arg": arg, "now": v})
            props[stamp_k] = v
        elif verb == "vel":
            v = float(props.get("ring_velocity") or 0.0)
            stamp = props.get("wf_stamp_vel")
            if stamp is None:
                props[stamp_k] = v
                continue
            if float(stamp) <= arg < v:     # crossed UP through the bar
                hits.append({"verb": "vel", "arg": arg, "now": v})
            props[stamp_k] = v
    return hits


def note_field_change(srv, est, ent, change_ts: float | None = None) -> None:
    """THE FLUSH'S INTAKE (called by the field's flush per dirty
    entity): the filters check, the gravity/freeze gate decides, the
    delivery is ONE 'field:changed' event (crossed filters riding it)
    to the ordinary pool. Hot: accumulate the burst; frozen: pause;
    the burst delivers on subsidence/melt/max-latency."""
    if est is None or ent is None:
        return
    props = ent.properties or {}
    if not props.get("watch_fields"):
        return
    now = change_ts if change_ts is not None else time.time()
    heat = entity_heat(props)
    frozen_ts = float(props.get("frozen_ts") or 0.0)
    try:
        _bar = float(os.environ.get("MEMBRANE_HEAT_FREEZE") or HEAT_FREEZE)
    except (TypeError, ValueError):
        _bar = HEAT_FREEZE
    is_frozen = heat > _bar
    if is_frozen:
        if not frozen_ts:
            props["frozen_ts"] = now        # the freeze stamps (visible)
    elif frozen_ts:
        props.pop("frozen_ts", None)        # the melt clears
    # THE FILTERS RUN unless frozen (the stamps stay FRESH through a
    # freeze -- the melt re-arms against its last pre-freeze stamp)
    hits = [] if is_frozen else _field_filter_hits(ent, props)
    burst = dict(props.get(WF_BURST_KEY) or {"n": 0, "since": now})
    if not is_frozen:
        burst["n"] = int(burst.get("n") or 0) + 1
        burst["since"] = burst.get("since") or now
    hot = heat > HEAT_COLD
    latency_ok = (now - float(burst.get("since") or now)) >= WF_MAX_LATENCY_S
    deliver = bool(hits or int(burst["n"]) > 1) and (
        (not hot and not is_frozen)      # cold: deliver immediately
        or latency_ok                    # the forever-hot covenant: the
                                         # burst delivers at max latency
    )
    if is_frozen and not latency_ok:
        props[WF_BURST_KEY] = burst
        ent.properties = props
        return
    if not deliver:
        props[WF_BURST_KEY] = burst
        ent.properties = props
        return
    # THE DELIVERY: one event, the burst's grouping honest
    n = int(burst.get("n") or 1)
    span = round(now - float(burst.get("since") or now), 2)
    pool = getattr(srv, "_events_pool", None)
    ev = {"kind": "field:changed",
          "entity": ent.eid,
          "verb": (hits[0]["verb"] if hits else "pos"),
          "n_changes": max(n, len(hits) or 1),
          "span_s": span,
          "frozen": bool(is_frozen),
          "ts": now}
    if pool is not None:
        pool.put(ev)
    props.pop(WF_BURST_KEY, None)         # the burst resets
    ent.properties = props
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
        # THE GRAVITY READ (the heat meter's consumer): the EFFECTIVE
        # interval = authored x the entity's gravity -- the gate and
        # the thinker's pacing agree on this one value (a stretched
        # cron cannot wedge: the comparison uses the stretched bar).
        est_for_heat = getattr(srv, "entity_store", None)
        if est_for_heat is not None:
            _live = est_for_heat.docs.get(ent.eid)
            interval = effective_tick_every(
                (_live.properties if _live is not None else props),  # noqa
                authored=max(1, int(round(interval))))
        if not _te_s:
            props["tick_every_s"] = interval
        if last_started and last_ms >= interval:
            # THE OVERRUN'S SURFACE (the covenant): the bubble entity
            # (the entity graph), never the pool.
            est = getattr(srv, "entity_store", None)
            if est is not None:
                try:
                    _script_error_announce(
                        est, ent.eid, "cron-overrun",
                        f"{trigger} script ran {last_ms:.0f}s > interval "
                        f"{interval:.0f}s; next run blocked")
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
    # THE HARD BUDGET (the 146s-poke exhibit's kill switch): the exec
    # runs in a WORKER thread; over timeout the fire ABANDONS it (the
    # abandoned thread's result lands out-of-band -- the per-entity
    # lock held by the worker blocks the next fire until it lands)
    # and the overrun STRIKES: a slow-success counts (the tick's 0.25s
    # budget is the hum's heartbeat contract, not advice).
    box: dict = {}
    _done = threading.Event()

    def _runner():
        try:
            # THE CPU METER (the heat lane's honest sample):
            # process_time around the exec -- a sleeper's wall never
            # counts; the churn does.
            _c0 = time.process_time()
            box["out"] = run_entity_script(code, state, membrane=api)
            box["cpu_s"] = time.process_time() - _c0
        except Exception as e:            # pragma: no cover - belt
            box["out"] = {"error": f"{type(e).__name__}: {e}"[:200]}
        finally:
            _done.set()

    _worker = threading.Thread(target=_runner,
                               name=f"script-{ent.eid[:24]}",
                               daemon=True)
    _worker.start()
    if not _done.wait(timeout=max(0.05, timeout)):
        out = {"error": f"over-budget: abandoned after {timeout:g}s"}
        box["cpu_s"] = timeout            # the abandoned slice counts
    else:
        out = box.get("out") or {}
    ms = (time.time() - _t0) * 1000.0
    cpu_ms = max(box.get("cpu_s") or 0.0, 0.0) * 1000.0
    props["script_last_ms"] = ms
    # THE HEAT SAMPLE LANDS (the meter's intake): the entity's CPU EMA
    try:
        note_script_cpu(est0, ent.eid, cpu_ms=cpu_ms, wall_ms=ms, ts=now)
    except Exception:
        pass
    est = getattr(srv, "entity_store", None)
    # THE SLOW-SUCCESS STRIKE: no error but WAY over budget (x4) = a
    # strike (the same 3-strikes lane; a wedging success is a wedge).
    if not out.get("error") and ms > timeout * 4000.0:
        _script_fail(est, ent, fld=getattr(srv, "efield", None))
    if out.get("error"):
        if est is not None:
            fld = getattr(srv, "efield", None)
            _script_record_fail(fld, est, ent, ent.eid, trigger,
                                out["error"],
                                mem=getattr(srv, "membrane", None))
        # THE POOL NOTIFY (errors are events, never stderr dust) +
        # THE WAKE CALL (the animal's bark: a script broke -- the
        # RETIRED (the entity-scripts covenant): the error's surface
        # IS THE BUBBLE ENTITY (_script_error_announce's new form).
        # The model's watchers decide what to watch; the engine's
        # lanes never leak into the model's event pool.
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
    scripts analyze + return; the engine writes). THE MUTATOR
    WHITELIST (the entity-scripts covenant, the operator's ruling:
    everything is an entity, the model sees entities only, a script
    is a PART of its entity -- the script's ways outward are its own
    shape):
      render  -> properties['script_render'] (the banner fragment --
                 the heartbeat's visible line, the entity's voice)
      position-> the patch's 'position' key MOVES the entity in the
                 field (a list vector; normed on the way in)
      links   -> the FIELD-WATCH lane only (trigger == 'change'):
                 link/unlink via the patch's 'links' key
      script_data -> the patch's 'script_data' dict (its own lane)
      run     -> the {run: true} verdict (the engine execs the entity's
                 own 'command')
    NOTHING ELSE: arbitrary prop-writes and delete are REFUSED (the
    model decides retirement via entity_edit, never a script).
    ``silent=True`` (the tick pace): the apply lands render + position
    but writes NO field tell and NO audit row.
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
        # THE WHITELIST AT THE WRITE (the covenant's teeth): patch
        # keys outside the whitelist are REFUSED (counted, visible in
        # the return); 'position' is the geometry move; 'links' is
        # the field-change lane's privilege. The json-safe screen
        # still runs on every admitted value (the 251-failures
        # poison guard).
        from .entities import json_safe as _json_safe
        refused = []
        _WHITELIST = {"position", "script_data"} | \
            ({"links"} if trigger == "change" else set())
        for k, v in list(out["patch"].items())[:16]:
            if str(k).startswith("script_") and str(k) != "script_data":
                continue               # the script cannot forge engine keys
            if str(k) not in _WHITELIST:
                refused.append(str(k)[:40])
                continue
            val = _json_safe(v)
            if str(k) == "position":
                try:
                    import numpy as _np
                    vec = _np.asarray(val, dtype=float)
                    n = float(_np.linalg.norm(vec))
                    ent.position = list(vec / (n + 1e-12))
                    changed += 1
                except Exception:
                    refused.append("position(unparseable)")
                continue
            if str(k) == "links":
                try:
                    clean = [int(i) for i in (val or [])][:16]
                    ent.properties["links"] = clean
                    changed += 1
                except Exception:
                    refused.append("links(unparseable)")
                continue
            props[str(k)[:80]] = val
            changed += 1
        if refused:
            out["patch_refused"] = refused   # the visible refusal
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
            # THE RUN'S SURFACE (the covenant): the result props ride
            # the asking entity (above); the pool hears NOTHING (the
            # model's watchers decide what to watch; the engine's
            # exec isn't an announcement).
        else:
            out["run_result"] = {"ok": False,
                                 "error": "run: true but the entity "
                                          "carries no 'command' prop"}
            changed += 1
    # THE FILE VERDICTS (the entity-scripts covenant, the reads/writes
    # lane): a script's result carries {read: path} / {write: [path,
    # content]} / {edit_file: [path, old, new]}, and the ENGINE's file
    # hands exec it (file_read_entity/file_write_entity -- the
    # observation event + the f:<path> entity as always). The script
    # ASKS; the engine does; the result rides the asking entity's
    # file_read/file_write props (the run-lane's residue shape).
    if out.get("read"):
        from . import file_lane
        out["read_result"] = file_lane.read(srv, ent,
                                            str(out["read"]))
        changed += 1
    if out.get("write"):
        w = out.get("write") or []
        if isinstance(w, (list, tuple)) and len(w) >= 2:
            from . import file_lane
            out["write_result"] = file_lane.write(srv, ent,
                                                  str(w[0]), str(w[1]),
                                                  create_dirs=True)
            changed += 1
        else:
            out["write_result"] = {"ok": False,
                                   "error": "write: [path, content]"
                                            " required"}
            changed += 1
    if out.get("edit_file"):
        e = out.get("edit_file") or []
        if isinstance(e, (list, tuple)) and len(e) >= 3:
            from . import file_lane
            out["edit_result"] = file_lane.edit(srv, ent,
                                                str(e[0]), str(e[1]),
                                                str(e[2]))
            changed += 1
        else:
            out["edit_result"] = {"ok": False,
                                  "error": "edit_file: [path, old, new]"
                                           " required"}
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
    _ret = {"applied": changed}
    if out.get("patch_refused"):
        _ret["patch_refused"] = out["patch_refused"]
    return _ret