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
import json
import os
import re
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
        # THE NAME CHECK (2026-10-10, the operator: "does the night overwrite
        # the mqtt entity, erasing what Argus made?"): Argus's
        # 'Component:mqtt:bridge' got a night twin 'models:mqtt:bridge' (the
        # key check only knew its own key form) and the twin won every lookup
        # -- a name someone already gave an entity is never minted again
        _names = {str((q.properties or {}).get("name") or "").lower()
                  for q in list(est.docs.values())}
        _names.discard("")
        for _, d in fld.b.scan("entity_states",
                               {"origin": fld.origin, "tier": "live"}):
            eid = d["eid"]
            # THE WORD SKIP (the overlay-birth covenant), CHAIN-TRUE
            # (the 'kind:message as an AIModel' exhibit): words and
            # phrases are FIELD entities, never overlay subjects --
            # AND a chain carrying them inside ('m:w:watcher': the
            # model-tracker of a word) is FIELD-INTERNAL TOO: any
            # head of the eid in (w, p, e) skips the overlay's mint
            # (the overlay exists for the subjects a cell is ABOUT)
            if set(eid.replace(":", " ").split()) & {"w", "p", "e"}:
                continue
            # THE TRACKER-CHAIN SKIP (the m:w:watcher shredding): the
            # field's TRACKER chains (an outer 'm'/'models' head over
            # ANOTHER kind: 'm:f:x', 'm:s:y') are the field's own
            # internal accounting -- never overlay subjects (only
            # BARE subjects mint: f:/s:/c:/m:/tool:/port:/ha:/u:)
            heads = [h for h in eid.replace(":", " ").split()
                     if h in ("m", "models", "f", "files", "s", "symbols",
                              "c", "shell", "tool", "port", "ha", "u",
                              "urls", "w", "p", "e")]
            if len(heads) > 1:
                continue
            # THE ECHO GUARD (2026-10-10, the 'phantom ha:light.status'): the
            # model's OWN words about a name are not evidence it exists --
            # three echoes minted a store entity that then echoed itself
            # to the top of the banner. Evidence = observation/code/people.
            from types import SimpleNamespace
            from .efield import evidence_n
            split = d.get("n_by_role") or d.get("n_by_stream")
            ev = (evidence_n(SimpleNamespace(n_by_role=d.get("n_by_role"),
                                             n_by_stream=d.get("n_by_stream")))
                  if split else int(d.get("n") or 0))      # no split recorded: the count stands
            if ev < 3:
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
            # the stored entity this field id is ABOUT already exists under its
            # own spelling (m:component:decision is Component:decision): no twin
            if est.by_field_id(cid) is not None or est.resolve(cid, exact=True) is not None:
                continue
            if cid in est.junk_field_ids() or eid in est.junk_field_ids():
                continue                     # marked junk: the night never dreams it back
            if key.partition(":")[2].lower() in _names:
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
        if eid is None and key and key not in ("session:now", "global:now") \
                and not key.startswith(("synth:", "junk-anchor:", "boot:")):
            # a class-embedded doc (Component:decision) had no field id: it
            # never drifted, so its proximity watches could never fire
            eid = E.field_id(key)
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


def _sid_vec(fld, sid):
    """a truthy sid only: event rows stamp the PLURAL sids, so the
    singular read here asked the store for the document '' -- which
    cblite's native layer warns about per call (issue #11; the guard
    mirrors _cell_points' `get(...) if sid else None`)."""
    return fld.b.get("sentences", sid) if sid else None


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
            # events carry the PLURAL sids (issue #11): a get here on the
            # singular 'sid' (always None) asked cblite for the document ''
            sv = _sid_vec(fld, d.get("sid"))
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
        sv = _sid_vec(fld, d.get("sid"))
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
    ns: dict = {"__builtins__": dict(_bi.__dict__), "state": state,
                # the seat writes JSON-ish (run=true): those names mean what it meant
                "true": True, "false": False, "null": None}
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
    try:                                   # OFFLINE (a walk): a failure there isn't the script's
        from . import offline as _OFF
        if _OFF.is_offline():
            return
    except Exception:
        pass
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
        subj = E.field_id(eid)             # the one spelling (class entities had none)
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
        # BUILTINS-FIRST (#17): wake/unwake specs arrive in the native
        # `llm::` dialect AND the call-form `llm.` dialect (auto-minted
        # from raw failed calls). Both route to the llm entity -- NEITHER
        # may fall through to /bin/sh, where a call-form spec dies rc=127.
        _p_unwake = next((p for p in ("llm::unwake", "llm.unwake")
                          if cmd.startswith(p)), None)
        _p_wake = None if _p_unwake else next(
            (p for p in ("llm::wake", "llm.wake") if cmd.startswith(p)),
            None)
        if _p_unwake or _p_wake:
            # SELF-SCHEDULED WAKES (llm.py): kept on the llm entity, never a shell
            from . import llm as _LLM
            _pfx = _p_unwake or _p_wake
            _fn = _LLM.unwake_from_command if _p_unwake else _LLM.wake_from_command
            _r = _fn(cmd[len(_pfx):].strip())
            rc, text = _r["rc"], _r["text"]
        elif cmd.startswith("llm::call"):
            # THE MODEL AS AN ENTITY (llm.py): a queued loop turn, never a shell
            from . import llm as _LLM
            _r = _LLM.from_command(cmd[len("llm::call"):].strip())
            rc, text = _r["rc"], _r["text"]
        elif cmd.startswith("seat::restart"):
            # ARGUS RESTARTS THE SEAT (restarts.py): never a shell
            from . import restarts as _RS
            from .jsexec import _unquote
            _r = _RS.do_restart(_RS._seat_srv(), _unquote(cmd[len("seat::restart"):].strip()))
            rc, text = _r["rc"], _r["text"]
        elif cmd.startswith("js::"):
            # THE SEAT'S JS (jsexec.py): never a shell -- an opted-in browser tab runs it
            from . import jsexec as _JS
            _r = _JS.run(cmd[4:], timeout=t)
            rc, text, err = _r["rc"], _r["text"], _r.get("err") or ""
            if err and rc != -1:
                text, err = (text + "\n" if text else "") + err, ""
        else:
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
    # a real exec (process spawn alone) is never truly 0 ms; the int
    # truncation on a fast machine can round a sub-millisecond run
    # down to 0 and the tool:run record would add nothing -- floor at 1
    ms = max(1, int((time.time() - t0) * 1000))
    head, tail = text[:ST.RESULT_HEAD], text[-ST.RESULT_TAIL:]
    oc = "timeout" if err else ST.outcome(canon_e, head, tail, rc=rc)
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


# ---- THE EXECUTOR (the edge-backed exec organ, 2026-10-06) ------------------
# The model LINKS command entities to the seeded `tool:executor`; the edge
# (a relations triple: subj=executor, rel='executes', obj=command entity)
# IS the ledger: its existence marks the link action; the run's residue
# (run_rc/outcome/head/tail/ms) always lands on the COMMAND entity --
# the run-lane covenant, reached through a different door.

EXECUTOR_EID = "tool:executor"
EXEC_REL = "executes"
EXEC_LINK_BATCH_CAP = 3      # commands run inline per link turn
EXEC_TIMEOUT_S = 15.0        # an inline run's budget (MCP headroom 60s)
EXEC_EDGES_CAP = 32          # live edges per executor (the cap IS the error)


def _relations_backend(srv):
    """The relations store's backend + origin: the seat's field when the
    capture runs it; None when it does not (RAM seats keep working --
    the executor's ledger degrades to the run, edges stay unwritten)."""
    fld = getattr(srv, "efield", None)
    if fld is None or getattr(getattr(srv, "capturer", None), "mode", "off") \
            != "on":
        return None, None
    return fld.b, fld.origin


def _edge_runs_pending(est, be, origin, exec_key: str) -> list[str]:
    """The executor's OWED commands (the queue's live edges): linked but
    not yet processed -- `run_rc is None` means never landed; a
    `continued` walk (the node cap) also stays owed (its residue lands
    on a later reaper beat; the edge supersedes only then)."""
    out = []
    if be is not None:
        obj_keys = [str(e.get("obj") or "") for e in
                    _live_edges(be, origin, exec_key)]
    else:
        obj_keys = [k[len(MEMBRANE_NS):] for k in (est.docs or {})
                    if k.startswith(MEMBRANE_NS)
                    and (est.docs[k].properties or {}).get("pending_run_by")
                    == exec_key]
    for obj in obj_keys:
        key = obj if obj.startswith(MEMBRANE_NS) else MEMBRANE_NS + obj
        doc = (est.docs or {}).get(key)
        props = (doc.properties or {}) if doc is not None else {}
        if props.get("command") or props.get("parents"):
            out.append(key)
    return out


def run_pending_links(srv, est, fld, mem, exec_key: str,
                      cap: int = EXEC_LINK_BATCH_CAP) -> list[dict]:
    """The queue's reaper (the thinker tick / a later link's turn): run
    the executor's OWED nodes (never-run + cap-continued), oldest edge
    first, up to cap; each landing supersedes its edge (the same
    unlink-on-done covenant as the link turn). Returns the run
    summaries."""
    be, origin = _relations_backend_of(srv, fld)
    if est is None:
        return []
    results = []
    for key in _edge_runs_pending(est, be, origin, exec_key)[:max(1, cap)]:
        doc = (est.docs or {}).get(key)
        if doc is None:
            continue
        run = _exec_command_doc(srv, est, fld, mem, doc)
        if not run.get("continued"):
            _supersede_edge(be, origin, exec_key, doc.eid,
                            receipt={"op": "done", "rc": run.get("rc"),
                                     "outcome": run.get("outcome"),
                                     "head": str(run.get("head") or "")[:80],
                                     "ms": run.get("ms")})
        results.append(run)
    return results


def _relations_backend_of(srv, fld=None) -> tuple:
    """The backend+origin pair (fld wins when handed; else the seat's)."""
    if fld is not None:
        return getattr(fld, "b", None), getattr(fld, "origin", "")
    be, origin = _relations_backend(srv)
    return be, origin or ""


def _supersede_edge(be, origin: str, subj: str, obj: str,
                    rel: str = EXEC_REL, receipt: dict | None = None) -> bool:
    """THE QUEUE'S DONE-STAMP (mark-don't-delete): live = owed,
    superseded = PROCESSED (the run's residue lives on the command doc;
    the edge keeps its n + evidence for the re-link-requeues). True when
    a live edge was found and stamped.

    THE RECEIPT (the seat's durability plan, 2026-10-06: 'a cancellation
    as durable as the debt'): the stamp carries a RECEIPT -- {op: done|
    expired, ts, rc, outcome, why} written ON the edge row (the
    relations journal persists it); a supersession WITHOUT a receipt is
    the accounting bug the phantom-debt exhibit named."""
    if be is None:
        return False
    from . import relations as R
    key = R.relation_key(origin, subj, rel, obj)
    doc = be.get("relations", key)
    if doc is None or doc.get("superseded"):
        return False
    now = time.time()
    _r = dict(receipt or {})
    _r.setdefault("op", "done")
    _r.setdefault("ts", now)

    def _done(cur):
        if cur is not None:
            cur["superseded"] = True
            cur["last_ts"] = now
            cur["receipt"] = _r
        return cur
    be.cas_update("relations", key, _done)
    return True


EXEC_EDGE_TTL_S = 24 * 3600.0   # an owed edge older than this expires


def expire_stale_edges(srv, est, fld, mem, exec_key: str = EXECUTOR_EID,
                       ttl_s: float | None = None) -> list[dict]:
    """THE EXPIRY-WITH-RESIDUE (the seat's plan's metabolism, delivered):
    an OWED edge older than EXEC_EDGE_TTL_S (the debt's born_ts is the
    edge's first_ts; a re-link bumps n + last_ts -- the clock is honest)
    EXPIRES: superseded + a receipt {op: 'expired', age_s, never-run
    proof: run_rc still None on its command doc}. Never re-run (the
    covenant: expired debt dies with its receipt, never resurrects).
    The boot runs this + the NightClock's pass carries it. Returns the
    receipts."""
    be, origin = _relations_backend_of(srv, fld)
    if est is None or be is None:
        return []
    exec_key = exec_key if exec_key.startswith(MEMBRANE_NS)         else MEMBRANE_NS + exec_key
    from . import relations as R
    ttl = float(ttl_s if ttl_s is not None else EXEC_EDGE_TTL_S)
    now = time.time()
    out = []
    for e in R.history_of(be, origin, exec_key, EXEC_REL):
        if e.get("subj") != exec_key or e.get("superseded"):
            continue          # live edges only
        born = float(e.get("first_ts") or 0.0)
        if not born or now - born < ttl:
            continue
        obj = str(e.get("obj") or "")
        cdoc = (est.docs or {}).get(obj if obj.startswith(MEMBRANE_NS)
                                    else MEMBRANE_NS + obj)
        cprops = (cdoc.properties or {}) if cdoc is not None else {}
        why = (f"expired at boot/night: the edge was owed {now - born:.0f}s "
               f"(past the {ttl:.0f}s TTL) and never landed "
               f"(run_rc {'is None' if cprops.get('run_rc') is None else 'on '
                         + obj}) -- the debt dies WITH its receipt; "
               f"re-link to re-queue deliberately")
        ok = _supersede_edge(be, origin, exec_key, obj,
                             receipt={"op": "expired", "age_s":
                                      round(now - born), "why": why[:180]})
        if ok:
            out.append({"eid": obj, "receipt": why})
            _audit(mem, {"event": "agent-exec-expired", "origin": "expiry",
                         "eid": obj, "age_s": round(now - born)})
    return out


def _live_edges(be, origin: str, exec_key: str) -> list[dict]:
    """The executor's live (owed) executes-edges, oldest delivery first."""
    if be is None:
        return []
    from . import relations as R
    return sorted((e for e in R.of(be, origin, exec_key, EXEC_REL)
                   if e.get("subj") == exec_key and not e.get("superseded")),
                  key=lambda e: (float(e.get("last_ts") or 0.0),
                                 str(e.get("obj"))))


EXEC_WALK_NODES_CAP = 12   # nodes run per pass (the reaper continues)
EXEC_WALK_DEPTH = MultiParentOps.MAX_CHAIN   # the ancestry's depth (16)


def _exec_command_doc(srv, est, fld, mem, doc, args: dict | None = None,
                      ctx: dict | None = None) -> dict:
    """THE PIPELINE ENTRY (the composition covenant, 2026-10-06: 'parent
    executes first, the child is fed its parent's output' -- the entity
    inheritance IS the pipeline): run ONE linked node's whole ancestry
    _exec_node's post-order walk (parents topologically, memoized per
    eid -- a diamond's shared ancestor runs once; MAX_CHAIN deep), then
    the node itself fed the parents' residues. A composition node (a
    Tool withOUT a command but with parents) inherits the last parent's
    residue as its exit; classification edges (Components/Goals) never
    execute. The run lane mints its canon doc (`c:<head>`/`shell:<verb>`)
    for the throwaway-reuse; when the ASKING doc differs from that canon
    doc, the residue is copied onto the asking doc too (the named
    command carries its own truth). NO EDGES HERE: supersede-after-
    landing lives at the link handlers (the edge is the queue;
    supersede = processed)."""
    props = doc.properties or {}
    if not str(props.get("command") or "").strip() \
            and not MultiParentOps._parents_of(props) and not _is_step(props):
        return {"ok": False, "eid": doc.eid,
                "error": "the linked entity carries no 'command' prop "
                         "(and no parents to compose) -- give it a "
                         "command, an ISA parent, or both"}
    missing = [p for p in command_params(str(props.get("command") or ""))
               if p not in (args or {})]
    if missing:
        return {"ok": False, "eid": doc.eid,
                "error": "needs args: " + ", ".join(missing)
                         + " -- exec(action='link', commands=<name>, args={"
                         + ", ".join(f"'{m}': ..." for m in missing) + "})"}
    budget = [0]
    out = _exec_node(srv, est, fld, mem, doc, {}, budget, args=args, ctx=ctx)
    out["nodes_run"] = budget[0]
    out["eid"] = doc.eid
    return out


def _is_step(props: dict) -> bool:
    """A Cliffhanger node that runs without a shell command: a guard, a call or a fan-out."""
    return bool(props.get("guard") or props.get("call") or props.get("each"))


def _step_name(doc) -> str:
    p = doc.properties or {}
    return str(p.get("step") or p.get("name") or doc.eid).split(":")[-1].split(".")[-1]


def _parsed(head):
    """A step's output as data when it is JSON (the {{steps.x.state.key}} reads)."""
    try:
        v = json.loads(str(head or ""))
        return v if isinstance(v, (dict, list)) else None
    except Exception:
        return None


def _ref(ctx: dict, key: str):
    """{{key}} in a call's context: args/self.* first, then steps.<name>.<path>
    (captured when that step finished), then entities.<spec>.<path> (read
    now). None = unresolved."""
    if key in ctx.get("vars", {}):
        return ctx["vars"][key]

    def walk(cur, path):
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            elif isinstance(cur, list) and k.isdigit() and int(k) < len(cur):
                cur = cur[int(k)]
            else:
                return None
        return cur
    if key.startswith("steps."):
        parts = key.split(".")
        return walk(ctx.get("steps", {}).get(parts[1]) if len(parts) > 1 else None, parts[2:])
    if key.startswith("entities.") and ctx.get("est") is not None:
        parts = key[len("entities."):].split(".")
        est = ctx["est"]
        for i in range(len(parts) - 1, 0, -1):          # the spec may hold dots: longest that resolves
            ent = est._resolve_spec(".".join(parts[:i]))
            if ent is not None:
                p = ent.properties or {}
                data = {**p, **dict(p.get("script_data") or {})}
                data.setdefault("state", p.get("script_data") or {})
                return walk(data, parts[i:])
    return None


def _fill_raw(value: str, ctx: dict) -> tuple:
    """A call step's argument: a lone {{ref}} passes its value as-is (data
    to another method, not a shell word); mixed text gets plain strings."""
    m = _PARAM.fullmatch((value or "").strip())
    if m:
        v = _ref(ctx, m.group(1))
        return (v, []) if v is not None and str(v).strip() != "" else (None, [m.group(1)])
    missing = []

    def rep(mm):
        v = _ref(ctx, mm.group(1))
        if v is None or str(v).strip() == "":
            missing.append(mm.group(1))
            return mm.group(0)
        return json.dumps(v) if isinstance(v, (dict, list)) else str(v)
    return _PARAM.sub(rep, value or ""), missing


def _fill(template: str, ctx: dict) -> tuple[str, list]:
    """Every {{ref}} filled shell-quoted; (text, the refs that did not resolve
    or were empty -- no silent empties)."""
    import shlex
    missing = []

    def rep(m):
        k = m.group(1)
        if k == "parent" or k.startswith("parent"):
            return m.group(0)                          # the pipeline feed fills these
        v = _ref(ctx, k)
        if v is None or str(v).strip() == "":
            missing.append(k)
            return m.group(0)
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        return shlex.quote(str(v))
    return _PARAM.sub(rep, template or ""), missing


def _exec_node(srv, est, fld, mem, doc, memo: dict, budget: list,
               depth: int = 0, args: dict | None = None, ctx: dict | None = None) -> dict:
    """One node of the ancestry walk (post-order: parents, then self).
    Returns the node's run/residue dict; memo[eid] holds it (a diamond's
    shared ancestor runs once). `budget` = [n] -- the pass's node count
    (the walk STOPS -- and reports `continued: True` -- past the cap;
    the reaper's next beat continues the queue)."""
    eid = doc.eid
    if eid in memo:
        return memo[eid]
    if depth > EXEC_WALK_DEPTH:
        res = {"ok": False, "eid": eid,
               "error": f"the ancestry walks past {EXEC_WALK_DEPTH} deep"}
        memo[eid] = res
        return res
    props = doc.properties or {}
    cmd = str(props.get("command") or "").strip()
    parents = MultiParentOps._parents_of(props)
    res: dict = {"eid": eid}
    parent_residues: dict[str, dict] = {}
    order: list[str] = []
    if parents and (cmd or not props.get("command")):
        for pp in parents:
            if budget[0] >= EXEC_WALK_NODES_CAP:
                res["continued"] = True
                break
            pdoc = (est.docs or {}).get(pp)
            if pdoc is None:
                res["failed_step"] = pp
                res["failed_reason"] = "parent not found"
                break
            # ISA EXECUTES ONLY COMMAND TOOLS: a parent without a
            # `command` prop that is NOT itself a composition node is
            # classification, never a step (the Goal/Component edges
            # stay classification)
            pprops = pdoc.properties or {}
            if not str(pprops.get("command") or "").strip() \
                    and not MultiParentOps._parents_of(pprops) and not _is_step(pprops):
                continue
            sub = _exec_node(srv, est, fld, mem, pdoc, memo, budget,
                             depth + 1, ctx=ctx)
            memo[pp] = sub
            parent_residues[pp] = sub
            order.append(pp)
            # THE STOP-ON-FAILURE: a failed step (non-zero rc OR an
            # error) halts the walk -- `&&`-semantics for the pipeline.
            # ok=None = the step's own ancestry was cap-CONTINUED (not a
            # failure): the walk also continues (the reaper finishes it)
            if sub.get("continued"):
                res["continued"] = True
                break
            if not sub.get("ok"):
                res["failed_step"] = pp
                res["failed_reason"] = (f"rc={sub.get('rc')} "
                                        f"{sub.get('outcome')}"
                                        if sub.get("error") is None
                                        else str(sub.get("error")))[:160]
                if sub.get("status") == "blocked":
                    res["status"] = "blocked"          # a guard (or an unresolved ref) said no
                elif len(order) > 1:
                    res["status"] = "partial"          # earlier steps ran: say so
                break
        if res.get("failed_step"):
            res["ok"] = False
            res["error"] = (f"step failed: {res['failed_step']} -- "
                            f"{res['failed_reason']}")
            memo[eid] = res
            return res
    if ctx is not None and props.get("guard"):
        # CLIFFHANGER'S GUARD: a check, never a shell; false = blocked
        state = {"self": {k[5:]: v for k, v in ctx.get("vars", {}).items() if k.startswith("self.")},
                 "args": {k: v for k, v in ctx.get("vars", {}).items() if not k.startswith("self.")},
                 "steps": dict(ctx.get("steps", {}))}
        out = run_entity_script(str(props["guard"]), state)
        ok = not out.get("error") and out.get("ok") is not False
        why = str(out.get("error") or out.get("why") or "")
        ctx.setdefault("steps", {})[_step_name(doc)] = {"ok": ok, "why": why}
        res.update({"ok": ok, "rc": 0 if ok else None,
                    "outcome": "pass" if ok else "blocked",
                    "status": None if ok else "blocked",
                    "error": None if ok else f"guard {_step_name(doc)}: {why or 'false'}"})
        memo[eid] = res
        return res
    if ctx is not None and props.get("each"):
        # CLIFFHANGER'S FAN-OUT STEP: a method over a class's members, capped
        over, miss = _fill_raw(str(props["each"]), ctx)
        call_args, missing = {}, list(miss)
        for k, v in dict(props.get("args") or {}).items():
            if isinstance(v, str):
                fv, m2 = _fill_raw(v, ctx)
                missing += m2
                call_args[k] = fv
            else:
                call_args[k] = v
        if missing or not props.get("call"):
            res.update({"ok": False, "status": "blocked",
                        "error": f"step {_step_name(doc)}: " + (f"unresolved {', '.join(missing)}" if missing else "each needs call")})
            memo[eid] = res
            return res
        r = method_each(srv, est, fld, mem, str(over), str(props["call"]), call_args,
                        max_n=props.get("max"), on_fail=str(props.get("on_fail") or "abort"),
                        _depth=int(ctx.get("depth", 0)) + 1)
        ctx.setdefault("steps", {})[_step_name(doc)] = {k: r.get(k) for k in ("status", "n", "ran", "n_ok", "results")}
        res.update({"ok": r.get("status") == "ok", "rc": 0 if r.get("status") == "ok" else None,
                    "outcome": r.get("status"), "head": f"{r.get('n_ok', 0)}/{r.get('n', 0)} ok",
                    "status": "blocked" if r.get("status") == "blocked" else None, "error": r.get("error")})
        memo[eid] = res
        return res
    if ctx is not None and props.get("call"):
        # CLIFFHANGER'S CALL STEP: a method on an object, through dispatch
        if int(ctx.get("depth", 0)) >= COMPOSITE_DEPTH:
            res.update({"ok": False, "status": "blocked",
                        "error": f"composites nest at most {COMPOSITE_DEPTH} deep"})
            memo[eid] = res
            return res
        obj_t, miss = _fill_raw(str(props.get("object") or "self"), ctx)
        obj_spec = ctx.get("self_eid", "") if str(obj_t) == "self" else str(obj_t or "")
        call_args, missing = {}, list(miss)
        for k, v in dict(props.get("args") or {}).items():
            if isinstance(v, str):
                fv, m2 = _fill_raw(v, ctx)
                missing += m2
                call_args[k] = fv
            else:
                call_args[k] = v
        if missing:
            res.update({"ok": False, "status": "blocked",
                        "error": f"step {_step_name(doc)}: unresolved {', '.join(missing)}"})
            memo[eid] = res
            return res
        r = method_call(srv, est, fld, mem, obj_spec, str(props["call"]), call_args,
                        _depth=int(ctx.get("depth", 0)) + 1)
        ctx.setdefault("steps", {})[_step_name(doc)] = {
            "rc": r.get("rc"), "status": r.get("status"), "head": r.get("head"),
            "tail": r.get("tail"), "state": _parsed(r.get("head"))}
        res.update({"ok": r.get("status") == "ok", "rc": r.get("rc"), "outcome": r.get("outcome"),
                    "head": r.get("head"), "tail": r.get("tail"),
                    "status": "blocked" if r.get("status") == "blocked" else None,
                    "error": r.get("error")})
        memo[eid] = res
        return res
    if cmd:
        if budget[0] >= EXEC_WALK_NODES_CAP:
            res["continued"] = True
            res["ok"] = None               # neither ran nor failed
            memo[eid] = res
            return res
        budget[0] += 1
        fed = _substitute_parents(cmd, parent_residues, order)
        if ctx is not None:
            fed, missing = _fill(fed, ctx)
            if missing:
                res.update({"ok": False, "status": "blocked",
                            "error": f"unresolved {', '.join(missing)}"})
                memo[eid] = res
                return res
        else:
            fed = substitute_args(fed, args)
        # THE TEMPLATE STAYS (2026-10-09): the fed line used to overwrite
        # the command -- a {{parent.head}} pipeline worked exactly once,
        # then ran its first output forever; the fed line is the residue
        if fed != cmd:
            doc.properties["last_fed"] = fed
        out = run_command_entity(fld, est, mem, fed, timeout_s=EXEC_TIMEOUT_S)
        out.pop("doc", None)
        # THE RESIDUE lands on the asking doc (the canon doc carries its
        # own; the copy covers a differing key)
        key = str(out.get("key") or "")
        if key and key != doc.eid and est is not None:
            canon_doc = (est.docs or {}).get(key)
            if canon_doc is not None:
                cp, dp = canon_doc.properties or {}, (doc.properties or {})
                for k in ("run_rc", "run_outcome", "run_head", "run_tail",
                          "run_ms"):
                    if cp.get(k) is not None:
                        dp[k] = cp[k]
                doc.properties = dp
                est.upsert(doc, embedder=None)
        res.update({k: out.get(k) for k in ("ok", "rc", "outcome", "head",
                                            "tail", "ms", "error")})
        res["fed"] = fed if fed != cmd else None
        if ctx is not None:
            ctx.setdefault("steps", {})[_step_name(doc)] = {
                "rc": out.get("rc"), "head": out.get("head"), "tail": out.get("tail"),
                "state": _parsed(out.get("head"))}
    elif parent_residues:
        # THE COMPOSITION NODE'S EXIT: the last parent's residue
        last = order[-1] if order else ""
        r = parent_residues.get(last) or {}
        res.update({"ok": bool(r.get("rc") == 0),
                    "rc": r.get("rc"),
                    "outcome": r.get("outcome"),
                    "head": r.get("head"),
                    "tail": r.get("tail"),
                    "composition": {"ran": order, "own_command": False}})
    elif parents:
        res.update({"ok": True, "composition": {"ran": [],
                                                "own_command": False}})
    memo[eid] = res
    return res


def _parent_residue_maps(est, doc) -> dict:
    """The per-parent residue view for the substitution: eid -> {head,
    tail, rc, outcome} from each DIRECT parent's doc (+ the ordered
    list for the nearest-parent form)."""
    out: dict[str, dict] = {}
    props = doc.properties or {}
    for pp in MultiParentOps._parents_of(props):
        p = (est.docs or {}).get(pp)
        if p is None:
            continue
        pp_props = p.properties or {}
        if pp_props.get("run_rc") is None and not pp_props.get("command"):
            continue               # a composition parent: no own residue
        out[pp] = {"head": str(pp_props.get("run_head") or "")[:400],
                   "tail": str(pp_props.get("run_tail") or "")[:400],
                   "rc": pp_props.get("run_rc"),
                   "outcome": str(pp_props.get("run_outcome") or "")}
    return out


def _substitute_parents(cmd: str, residues: dict, order: list[str]) -> str:
    """The output feed: {{parent.head}} / {{parent.tail}} / {{parent.rc}}
    = the NEAREST parent's residue (order[0]); {{parent:<eid>.field}} =
    the named parent's. Unmatched tokens SUBSTRIP (left verbatim, the
    command's own text survives -- a visible '' substitution is never a
    silent shell corruption)."""
    import re as _re
    if "{{" not in cmd or not (residues or order):
        return cmd

    def _field(parents_eid: str, field: str):
        r = residues.get(parents_eid)
        if r is None:
            return None
        return str(r.get(field) if field in ("head", "tail")
                   else (f"{r.get('rc')}" if field == "rc"
                         else r.get(field) or ""))

    def _sub(m):
        tok = m.group(1).strip()
        if not (tok == "parent" or tok.startswith(("parent.", "parent:"))):
            return m.group(0)          # not a feed token ({{self.x}}, {{param}}, {{steps..}}): left for the call
        if tok.startswith("parent:"):
            rest = tok[len("parent:"):]
            peid, _, field = rest.rpartition(".")
            for candidate, _r in residues.items():
                if candidate.endswith(peid) or peid in candidate:
                    v = _field(candidate, field or "head")
                    return v if v is not None else m.group(0)
            return m.group(0)          # no such parent: verbatim
        field = tok.rpartition(".")[-1] if tok == "parent" else tok \
            .replace("parent.", "", 1)
        if not order:
            return m.group(0)
        first = order[0]
        v = _field(first, field if field in ("head", "tail", "rc",
                                             "outcome")
                   else "head")
        return v if v is not None else m.group(0)
    return _re.sub(r"\{\{([^}]+)\}\}", _sub, cmd)


def lint_command(spec: str) -> str | None:
    """THE NAME LINT's command side (2026-10-08): a dict literal is not a
    command (the c:{'cmd': exhibit -- the head became an entity name)."""
    t = str(spec or "").strip()
    if t.startswith("{") and t.endswith("}"):
        return ("a dict literal is not a command -- pass the command line "
                "itself (e.g. 'uname -a')")
    return None


def command_specs(commands) -> list:
    """The link/unlink `commands` arg -> a list of command specs. ONE
    string is ONE command: the comprehension used to iterate it letter by
    letter -- the first restored loop links ran '1', 'a', 'g' (rc 127) from
    commands='ag1' (2026-10-07)."""
    if commands is None:
        return []
    if isinstance(commands, (str, bytes)):
        text = commands.decode() if isinstance(commands, bytes) else commands
        commands = [text]
        t = text.strip()
        if t.startswith("[") and t.endswith("]"):
            # a JSON-encoded list in a string (exhibit: '["uname -a"]' ran
            # literally, rc 127); a shell test like `[ -f x ] && ...` is
            # not valid JSON and stays one command
            try:
                import json as _json
                dec = _json.loads(t)
                if isinstance(dec, list):
                    commands = dec
            except ValueError:
                pass
    elif not isinstance(commands, (list, tuple, set)):
        commands = [commands]
    out = []
    for c in commands:
        if isinstance(c, dict):
            # {name, command}: a labelled command (never its str() repr)
            cmd = str(c.get("command") or c.get("cmd") or "").strip()
            name = str(c.get("name") or c.get("label") or "").strip()
            c = f"{name} :: {cmd}" if (name and cmd) else cmd
        c = str(c or "").strip()
        if c:
            out.append(c)
    return out


# a label may be colon-segmented like the seat's own names ('argus:battery.soc
# :: <command>'): without it the whole line went to the shell and died
# 'argus:battery.soc: command not found' (2026-10-11 -- Argus took it for
# "ANY colon gets mangled" and routed around it with a '~'-for-':' runner)
_LABEL_RE = re.compile(r"^\s*(?:shell:|c:)?([A-Za-z][\w.-]{0,40}(?::[\w.-]{1,40}){0,3})\s*::\s*(\S.*)$", re.DOTALL)


def split_label(spec: str) -> tuple[str, str]:
    """'<label> :: <command>' -> (label, command); no label -> ('', spec).
    A model's 'shell:x:: cmd' label fused into the eid once."""
    m = _LABEL_RE.match(spec or "")
    if not m:
        return "", (spec or "").strip()
    return m.group(1).lower(), m.group(2).strip()


# a command's setup prefixes say nothing about what it does (the '.-01c5d8'
# exhibit, 2026-10-09: every '. ~/.config/x.env; curl ...' was named '.')
_SETUP = re.compile(r"^\s*(?:(?:\.|source)\s+\S+(?:\s+\d*>\S*)*|set\s+-[a-z]+|export\s+[^;&]+|cd\s+\S+|"
                    r"[A-Z_][A-Z0-9_]*=\S+)\s*(?:;|&&|\|\|)\s*")
_PARAM = re.compile(r"\{\{\s*([A-Za-z_][\w.:-]*)\s*\}\}")   # entity specs carry ':' and '-'


def command_params(cmd: str) -> list[str]:
    """A command's parameters: its {{name}} placeholders (never the
    pipeline's {{parent...}} feeds)."""
    return [p for p in dict.fromkeys(_PARAM.findall(cmd or ""))
            if p != "parent" and not p.startswith(("parent", "self.", "steps.", "entities."))]


def substitute_args(cmd: str, args: dict | None) -> str:
    """Fill {{name}} from args, each value shell-quoted (an argument is
    data, never shell)."""
    import shlex
    if not args:
        return cmd

    def rep(m):
        k = m.group(1)
        return shlex.quote(str(args[k])) if k in args else m.group(0)
    return _PARAM.sub(rep, cmd)


def _action_of(cmd: str) -> str:
    c = cmd
    for _ in range(6):
        m = _SETUP.match(c)
        if not m:
            break
        c = c[m.end():]
    return c.strip() or cmd


def command_name(cmd: str) -> str:
    """An ad-hoc command line's entity name: one plain word keeps its name;
    anything longer is '<head>-<hash6>' -- distinct per command line,
    STABLE for the same line (a re-link is the same entity). Every
    `set -a; ...; curl` was ONE entity, shell:set, each run overwriting
    the last (158 mentions, 2026-10-08)."""
    cmd = " ".join((cmd or "").split())
    act = _action_of(cmd)
    head = re.match(r"[A-Za-z0-9_-]+", act)
    head_s = (head.group(0).lower() if head else "cmd")[:24] or "cmd"
    if re.fullmatch(r"[A-Za-z0-9._-]+", cmd):
        return head_s
    # the salient word after the verb: a url's path tail, else the first
    # plain argument (flags, quotes and variables skipped)
    sal = ""
    url = re.search(r"https?://\S+|\$\{?[A-Z_]+\}?(/[\w./-]+)", act)
    if url:
        path = (url.group(1) or url.group(0)).split("?")[0].strip("\"'")
        parts = [p for p in re.split(r"[/.]", path) if p and not p.startswith("$") and p not in ("http:", "https:")]
        sal = "-".join(parts[-2:])
    else:
        for tok in act.split()[1:]:
            t = tok.strip("\"'")
            if t and not t.startswith(("-", "$", "{", "<", ">", "|", "&")) and re.match(r"[A-Za-z]", t):
                sal = t
                break
    sal = re.sub(r"[^a-z0-9_]+", "-", sal.lower()).strip("-")[:24]
    h = hashlib.sha1(cmd.encode()).hexdigest()[:4]
    return f"{head_s}-{sal}-{h}" if sal else f"{head_s}-{h}"


def _norm_cmd(c: str) -> str:
    return " ".join((c or "").split())


LIBRARY_MAX = 12
_JUNK_NAME = re.compile(r"^(?:[.\-]|cmd-|set-|export-)")


def command_library(est, k: int = LIBRARY_MAX) -> str | None:
    """'your commands: ha-states x14, light-on{entity} x3, ...' -- the seat's
    own reusable commands, most used first (it cannot reuse what it cannot
    see). Hash-junk names (the '.-01c5d8' era) stay out."""
    if est is None:
        return None
    rows = []
    for d in list((getattr(est, "docs", None) or {}).values()):
        p = d.properties or {}
        cmd = str(p.get("command") or "")
        n = int(p.get("runs") or 0)
        name = str(p.get("name") or "")
        if not cmd or n < 1 or not name or _JUNK_NAME.match(name):
            continue
        if p.get("method_name"):
            name = str(p.get("name") or name)                 # 'ha light.turn_on'
        rows.append((n, float(p.get("last_run_ts") or 0), name,
                     command_params(cmd), bool(p.get("method_name"))))
    if not rows:
        return None
    rows.sort(key=lambda r: (-r[0], -r[1]))
    items = []
    for n, _, nm, ps, is_m in rows[:k]:
        body = f"{nm}{('{' + ','.join(ps) + '}') if ps else ''} x{n}"
        if is_m:
            # issue #21: a method re-runs through ITS door -- 'exec link
            # commands=<name>' used to run the raw template with no call
            # context and lie 'FAILED -- not-found'
            body += f" [a method: exec action=call object='{nm.rsplit('.', 1)[0]}']"
        items.append(body)
    return ("your commands (re-run by name: exec link commands=<name>, args for {params}): "
            + ", ".join(items))


def entity_link(srv, est, fld, mem, executor_spec: str,
                commands, create: bool = True, args: dict | None = None) -> dict:
    """THE LINK HAND (the model's exec door, 2026-10-06): link command
    entities to the executor and RUN them inline -- the edge (a
    relations triple, rel='executes') IS THE QUEUE: live = owed,
    SUPERSEDED = processed (the unlink-on-done covenant: every run --
    success OR error -- stamps its edge done after landing the residue;
    re-link = the deliberate re-queue; no auto-retry). Residue lands on
    each command entity; the RETURN carries every command's post-run
    state (the sync rule: the tool answers AFTER the triggers processed
    the entities).

    On-the-fly creation: a spec that resolves to nothing becomes a new
    Tool entity whose `command` prop is the spec itself (one-call
    authoring; `create=False` refuses instead). The batch cap and the
    live-edge (queue-depth) cap are hard errors -- never a silent
    partial link."""
    if est is None:
        return {"ok": False, "error": "entity layer unavailable"}
    specs = command_specs(commands)
    if not specs:
        return {"ok": False, "error": "commands required"}
    for _sp in specs:
        _bad = lint_command(_sp)
        if _bad:
            return {"ok": False, "error": f"refused {_sp[:60]!r}: {_bad}"}
        # a bare word that names a defined METHOD is not a shell command (it ran
        # as 'sh: status: not found' and minted a colliding stub, 2026-10-10)
        if est is not None and re.fullmatch(r"[A-Za-z_][\w.-]*", _sp.strip()):
            _res = est._resolve_spec(_sp.strip())
            # issue #21 (THE BANNER LISTED IT): a spec resolving BY NAME to a
            # method doc must refuse here too -- the old guard only fired on
            # `_resolve_spec(...) is None`, while a banner-listed method
            # resolves (properties['name'] == the word) and then ran through
            # the ctx-less link lane: {{self.*}} literal, step nodes skipped,
            # shell rc=127 relabeled 'not-found'
            _mn = (((est.docs or {}).get(_res.eid, _res).properties or {})
                   .get("method_name")) if _res is not None else None
            if _mn:
                _own = str(_res.eid).rsplit(".", 1)[0].split(":")[-1]
                return {"ok": False,
                        "error": f"{_sp!r} is a method, not a command line -- "
                                 f"exec(action='call', object='{_own}', method='{_mn}')"}
            if _res is None:
                _nm = _sp.strip().split(".")[-1]
                if any((d.properties or {}).get("method_name") == _nm for d in list((est.docs or {}).values())):
                    return {"ok": False, "error": f"{_sp!r} is a method, not a command line -- "
                                                  f"exec(action='call', object=<entity>, method='{_nm}')"}
    if len(specs) > EXEC_LINK_BATCH_CAP:
        return {"ok": False,
                "error": f"failed to link: batch cap {EXEC_LINK_BATCH_CAP} "
                         f"(got {len(specs)}) -- link the rest next turn"}
    ex_ent = est._resolve_spec(executor_spec or EXECUTOR_EID)
    if ex_ent is None:
        return {"ok": False, "error": f"no entity matches executor "
                                      f"{executor_spec!r}"}
    ex_key = ex_ent.eid
    be, origin = _relations_backend_of(srv, fld)
    if be is not None:
        from . import relations as R
        live = [e for e in R.of(be, origin, ex_key, EXEC_REL)
                if e.get("subj") == ex_key and not e.get("superseded")]
        if len(live) + len(specs) > EXEC_EDGES_CAP:
            return {"ok": False,
                    "error": f"failed to link: the executor's edge cap "
                             f"{EXEC_EDGES_CAP} is reached ({len(live)} "
                             f"live) -- unlink first"}
    linked, runs = [], []
    for spec in specs:
        label, cmdline = split_label(spec)
        if label:
            # a LABELLED command: the label names it, the line is its
            # command (re-linking the label with a new line updates it)
            ent = est._resolve_spec(MEMBRANE_NS + overlay_key(E.canon("c", label)))
            if ent is not None:
                (ent.properties or {})["command"] = cmdline
        else:
            ent = est._resolve_spec(spec)
        reused = False
        if ent is None and not label:
            # RE-USE (2026-10-09): the same command line is the SAME entity
            # (its runs and history kept) -- the fly-mint below used to
            # rebuild it from scratch on every link; and a line identical to
            # a labelled command re-runs that one
            ent = (est.docs or {}).get(MEMBRANE_NS + overlay_key(E.canon("c", command_name(cmdline))))
            if ent is None:
                want = _norm_cmd(cmdline)
                ent = next((d for d in (est.docs or {}).values()
                            if _norm_cmd(str((d.properties or {}).get("command") or "")) == want),
                           None)
            reused = ent is not None
        created = False
        if ent is None:
            if not create:
                runs.append({"ok": False, "spec": spec,
                             "error": f"no entity matches {spec!r}"})
                continue
            # ON-THE-FLY: the spec itself is the command line.
            name = label or command_name(cmdline)
            ent = QuantumEntity(eid=MEMBRANE_NS + overlay_key(
                                    E.canon("c", name)),
                                cls=CLASS_TOOL,
                                properties={"name": name,
                                            "command": cmdline,
                                            "silent_birth": True})
            est.docs[ent.eid] = ent
            created = True
        if created:
            # THE FLY-MINTED DOC's command IS its spec (already set).
            pass
        # THE STALE GUARD BELT (issue #21): any resolution path (label, reuse)
        # that lands on a method doc stops here -- honest door, no run, no
        # runs bump on a receipt that lied.
        _mn = (ent.properties or {}).get("method_name")
        if _mn:
            _own = str(ent.eid).rsplit(".", 1)[0].split(":")[-1]
            return {"ok": False,
                    "error": f"'{label or spec}' is a method, not a command line -- "
                             f"exec(action='call', object='{_own}', method='{_mn}')"}
        # THE EDGE: existence = the link action (re-link bumps n, the
        # dedupe is the store's own cas merge).
        if be is not None:
            from . import relations as R
            R.upsert(be, origin, ex_key, EXEC_REL, ent.eid, "model-link",
                     ts=time.time())
        linked.append({"eid": ent.eid, "created": created})
        run = _exec_command_doc(srv, est, fld, mem, ent, args=args)
        _p = ent.properties or {}
        _p["runs"] = int(_p.get("runs") or 0) + 1
        _p["last_run_ts"] = time.time()
        ent.properties = _p
        run.update({"name": _p.get("name") or ent.eid, "reused": reused or (not created and not label),
                    "created": created, "labelled": bool(label), "run_n": _p["runs"],
                    "params": command_params(str(_p.get("command") or ""))})
        # THE UNLINK-ON-DONE (the queue's honest close): processed --
        # success OR failure -- stamps the edge superseded WITH ITS
        # RECEIPT (the seat's symmetry rule: a cancellation as durable
        # as the debt). A pending-degrade (run continued by the reaper)
        # keeps the edge live until the reaper's landing.
        if not run.get("continued"):
            _supersede_edge(be, origin, ex_key, ent.eid,
                            receipt={"op": "done", "rc": run.get("rc"),
                                     "outcome": run.get("outcome"),
                                     "head": str(run.get("head") or "")[:80],
                                     "ms": run.get("ms")})
        runs.append(run)
    _audit(mem, {"event": "agent-entity-link", "origin": "mcp",
                 "eid": ex_ent.eid, "ok": True,
                 "commands": [l["eid"] for l in linked]})
    return {"ok": True, "executor": ex_ent.eid,
            "linked": linked, "runs": runs}


# ---- CLIFFHANGER: membrane as an OOP language (2026-10-10, the operator:
# "compose commands kinda like proteins and use membrane as an OOP-language"
# -- named after his graph-operation language, github.com/chedim/cliffhanger).
# A METHOD is a command entity attached to a class or an object
# (method_of + method_name); a CALL is an event on an object, never a new
# entity. Dispatch: the object's own methods, then its classes in the ISA
# resolution order, then the global ones -- the same name on two classes is
# two implementations (polymorphism). {{self.<prop>}} fills from the
# object's properties merged over its classes'; other {{params}} are the
# call's args (shell-quoted). The receipt lands ON the object.
CALLS_RING = 12
COMPOSITE_DEPTH = 3            # Argus: "composite depth <= 3" -- feed-forward, not Turing-complete


def method_define(srv, est, fld, mem, on: str, method: str, command: str,
                  read_only: bool = False, guard: str = "", steps=None,
                  abstract: bool = False, params=None) -> dict:
    """A method: a command, or a COMPOSITE (steps: calls / commands, in
    order, as entities -- its parents), optionally behind a GUARD entity.

    abstract=True: an INTERFACE's required method (2026-10-10, the operator:
    "we should have interfaces"): a signature (params), no command. Its owner
    becomes an interface; every entity inheriting it must bring its own
    implementation (own or from a class in between) -- until then a call is
    blocked with what is missing."""
    if est is None:
        return {"ok": False, "error": "entity layer unavailable"}
    method = re.sub(r"[^A-Za-z0-9_]+", "_", (method or "").strip()).strip("_")
    steps = list(steps or [])
    if abstract:
        if (command or "").strip() or steps or (guard or "").strip():
            return {"ok": False, "error": "an abstract method is a signature only: no command, steps or guard"}
        if not method or not on:
            return {"ok": False, "error": "an abstract method needs its interface (on) and a name"}
    elif not method or not ((command or "").strip() or steps):
        return {"ok": False, "error": "on, method and a command or steps required"}
    if (command or "").strip():
        bad = lint_command(command)
        if bad:
            return {"ok": False, "error": f"refused {command[:60]!r}: {bad}"}
    for st in steps:
        if not isinstance(st, dict) or not (st.get("call") or st.get("command")):
            return {"ok": False, "error": "each step is {name, call, object?, args?}, {name, each, call, args?, max?} or {name, command}"}
        if st.get("command") and lint_command(str(st["command"])):
            return {"ok": False, "error": f"step refused: {lint_command(str(st['command']))}"}
    owner = est._resolve_spec(on) if on else None
    if on and owner is None:
        # a missing owner is minted as a class (the probe suite: 'ha light' existed,
        # 'ha sensor' did not -- define must not depend on which came first)
        r = est.add(cls=CLASS_COMPONENT, name=on, summary=f"class (methods defined on it): {on}")
        owner = (est.docs or {}).get(r.get("eid", "")) if isinstance(r, dict) else None
        if owner is None:
            return {"ok": False, "error": f"no entity matches {on!r} and it could not be created"}
    from .classes import short_name
    label = f"{short_name(owner.eid) if owner else 'any'}.{method}"
    eid = MEMBRANE_NS + overlay_key(E.canon("c", re.sub(r"[^a-z0-9_.:-]+", "-", label.lower())))
    doc = est.docs.get(eid)
    created = doc is None
    if created:
        doc = QuantumEntity(eid=eid, cls=CLASS_TOOL, properties={"name": label, "silent_birth": True})
        est.docs[eid] = doc
    p = doc.properties or {}
    p.update({"command": (command or "").strip(), "method_name": method,
              "method_of": [owner.eid] if owner else [], "read_only": bool(read_only)})
    if abstract:
        names = [re.sub(r"[^A-Za-z0-9_]+", "_", str(x)).strip("_") for x in (params or [])]
        p.update({"abstract": True, "params": [x for x in names if x]})
        op = owner.properties or {}
        op["interface"] = True
        owner.properties = op
        est.upsert(owner, embedder=None)
    else:
        p.pop("abstract", None)
    # THE NODES AS ENTITIES: the guard first, then the steps in order --
    # the method's parents (the executor runs parents first, stop-on-failure)
    nodes = []
    if (guard or "").strip():
        nodes.append(_node(est, eid, "guard", {"guard": guard.strip(), "step": "guard"}))
    for i, st in enumerate(steps):
        name = re.sub(r"[^A-Za-z0-9_]+", "_", str(st.get("name") or f"step{i + 1}")).strip("_")
        props = {"step": name}
        if st.get("each"):
            props.update({"each": str(st["each"]), "call": str(st["call"]),
                          "args": dict(st.get("args") or {}), "max": st.get("max"),
                          "on_fail": str(st.get("on_fail") or "abort")})
        elif st.get("call"):
            props.update({"call": str(st["call"]), "object": str(st.get("object") or "self"),
                          "args": dict(st.get("args") or {})})
        else:
            props["command"] = str(st["command"])
        nodes.append(_node(est, eid, name, props))
    old = list(p.get("parents") or [])
    old_nodes = [x for x in old if str(x).startswith(eid + ":")]
    if nodes or old_nodes:
        # its own nodes are replaced; any other parent (a hand-made pipeline) stays
        p["parents"] = nodes + [x for x in old if x not in old_nodes and x not in nodes]
    doc.properties = p
    est.upsert(doc, embedder=None)
    _audit(mem, {"event": "agent-method-define", "origin": "mcp", "eid": eid,
                 "on": owner.eid if owner else "", "method": method, "ok": True})
    return {"ok": True, "eid": eid, "method": label, "created": created, "abstract": bool(abstract),
            "params": p.get("params") if abstract else command_params(command), "read_only": bool(read_only)}


def _node(est, method_eid: str, name: str, props: dict) -> str:
    """One Cliffhanger node entity (a guard or a step) owned by its method."""
    eid = f"{method_eid}:{name}"
    d = est.docs.get(eid)
    if d is None:
        d = QuantumEntity(eid=eid, cls=CLASS_TOOL, properties={"name": f"{name}", "silent_birth": True})
        est.docs[eid] = d
    dp = d.properties or {}
    for k in ("guard", "call", "object", "args", "command", "each", "max", "on_fail"):
        dp.pop(k, None)
    dp.update(props)
    dp["node_of"] = method_eid
    d.properties = dp
    est.upsert(d, embedder=None)
    return eid


def resolve_method(est, obj, method: str):
    """The method's entity for this object (own -> classes -> global)."""
    docs = list((est.docs or {}).values())
    named = [d for d in docs if (d.properties or {}).get("method_name") == method
             and (str((d.properties or {}).get("command") or "").strip()
                  or (d.properties or {}).get("parents"))]
    if not named:
        return None, None
    order = [obj] + ancestors(est, obj)
    for owner in order:
        for d in named:
            if owner.eid in ((d.properties or {}).get("method_of") or []):
                return d, owner.eid
    for d in named:
        if not (d.properties or {}).get("method_of"):
            return d, None                                   # a global method
    return None, None


def methods_of(est, obj) -> list[str]:
    """The method names an object answers to (for the refusal's hint) --
    implemented ones only: an interface's abstract method is not an answer."""
    owners = {obj.eid} | {a.eid for a in ancestors(est, obj)}
    out = []
    for d in list((est.docs or {}).values()):
        pp = d.properties or {}
        mo = pp.get("method_of")
        if pp.get("method_name") and not pp.get("abstract") and (not mo or owners & set(mo)):
            out.append(pp["method_name"])
    return sorted(set(out))


# ---- INTERFACES (2026-10-10) -------------------------------------------------
def interfaces_of(est, obj) -> list:
    """The interfaces an entity implements: itself or any ancestor marked
    interface (an abstract method was defined on it)."""
    if est is None or obj is None:
        return []
    return [a for a in [obj] + ancestors(est, obj) if (a.properties or {}).get("interface")]


def abstract_methods(est, obj) -> list[dict]:
    """{name, interface, params} for every abstract method the entity's
    interfaces require."""
    ifaces = {i.eid for i in interfaces_of(est, obj)}
    out = []
    for d in list((est.docs or {}).values()):
        pp = d.properties or {}
        if pp.get("abstract") and pp.get("method_name") and ifaces & set(pp.get("method_of") or []):
            out.append({"name": pp["method_name"], "interface": (pp.get("method_of") or [""])[0],
                        "params": list(pp.get("params") or [])})
    return out


def missing_methods(est, obj) -> list[dict]:
    """The required methods the entity does not implement yet (an interface
    itself implements nothing: it is abstract)."""
    # a GLOBAL method of the same name does not fulfil a contract: only one
    # owned by the entity or a class on its chain does
    return [m for m in abstract_methods(est, obj) if resolve_method(est, obj, m["name"])[1] is None]


def implements(est, obj, iface_spec: str) -> bool:
    """Does `obj` fully implement the interface `iface_spec`?"""
    iface = est._resolve_spec(iface_spec or "") if est is not None else None
    if iface is None or obj is None or not (iface.properties or {}).get("interface"):
        return False
    if iface.eid not in {i.eid for i in interfaces_of(est, obj)}:
        return False
    return not [m for m in missing_methods(est, obj) if m["interface"] == iface.eid]


_IFACE_CACHE_S = 300.0


def interfaces_line(srv) -> str | None:
    """'interfaces: remote_executor exec(code, timeout) -- implemented by: a, b'
    (the banner's line; cached 5 min, the member scan is not free)."""
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    hit = getattr(srv, "_iface_line_cache", None)
    now = time.time()
    if hit and now - hit[0] < _IFACE_CACHE_S:
        return hit[1]
    from .classes import short_name
    parts = []
    for d in list((est.docs or {}).values()):
        if not (d.properties or {}).get("interface"):
            continue
        sigs = [f"{m['name']}({', '.join(m['params'])})" for m in abstract_methods(est, d)]
        done = [short_name(m.eid) for m in members_of(est, d.eid) if implements(est, m, d.eid)][:8]
        parts.append(f"{short_name(d.eid)} {'; '.join(sigs)} -- implemented by: "
                     + (", ".join(done) if done else "nobody yet"))
    line = ("interfaces (implement: inherit it, then define each method on yourself or a class): "
            + " | ".join(parts)) if parts else None
    srv._iface_line_cache = (now, line)
    return line


def contract_note(est, obj) -> str:
    """'implements remote_executor; missing: exec(code, timeout)' -- or ''."""
    from .classes import short_name
    ifaces = interfaces_of(est, obj)
    if not ifaces:
        return ""
    miss = missing_methods(est, obj)
    names = ", ".join(short_name(i.eid) for i in ifaces)
    if not miss:
        return f"implements {names}"
    sig = ", ".join(f"{m['name']}({', '.join(m['params'])})" for m in miss)
    return f"implements {names}; missing: {sig}"


# THE OUTCOMES (Argus's review, 2026-10-10: "partial must not silently count
# as success"): ok | blocked (refused before running: a missing method, a
# missing or empty value) | error (ran, failed) | timeout | partial (a
# composite that got part-way -- step 2)
OUTCOMES = ("ok", "blocked", "error", "timeout", "partial")


def _blocked(obj, method, why, **extra) -> dict:
    return {"ok": False, "status": "blocked", "object": getattr(obj, "eid", None),
            "method": method, "error": why, **extra}


def method_call(srv, est, fld, mem, obj_spec: str, method: str, args: dict | None = None,
                _depth: int = 0) -> dict:
    if est is None:
        return {"ok": False, "status": "blocked", "error": "entity layer unavailable"}
    obj = est._resolve_spec(obj_spec or "")
    if obj is None:
        return {"ok": False, "status": "blocked",
                "error": f"no object matches {obj_spec!r} -- a call names its object"}
    m, via = resolve_method(est, obj, (method or "").strip())
    mro = [short_name_of(a) for a in [obj] + ancestors(est, obj)][:8]
    if m is None:
        req = next((x for x in abstract_methods(est, obj) if x["name"] == (method or "").strip()), None)
        if req is not None:
            from .classes import short_name
            return _blocked(obj, method, f"{short_name_of(obj)} implements {short_name(req['interface'])}, "
                            f"which requires {req['name']}({', '.join(req['params'])}), but it has no "
                            f"{req['name']} of its own yet -- implement it: exec(action='define', "
                            f"on=<it, or a class between it and the interface>, method='{req['name']}', command=...)",
                            mro=mro, missing=req)
        have = methods_of(est, obj)
        return _blocked(obj, method, f"{short_name_of(obj)} has no method {method!r}"
                        + (f"; it answers to: {', '.join(have)}" if have else "")
                        + " -- define one: exec(action='define', on=<class or object>, method=..., command=...)",
                        mro=mro)
    props = effective_properties(est, obj)
    full = {f"self.{k}": v for k, v in props.items() if isinstance(v, (str, int, float, bool))}
    sid = short_name_of(obj)
    bare = sid.split(":", 1)[1] if ":" in sid else sid
    full.update({"self.eid": obj.eid, "self.name": str(props.get("name") or sid),
                 "self.id": bare})                        # 'ha:light.x' -> 'light.x' (the probe suite's sed)
    if sid.startswith("ha:") and not str(full.get("self.entity_id") or "").strip():
        full["self.entity_id"] = bare                     # an HA entity's id is its name
    full.update(dict(args or {}))
    # NO SILENT EMPTIES (Argus: "silent empties cause real-world effects"): every
    # placeholder must have a non-empty value before anything runs
    template = str((m.properties or {}).get("command") or "")
    missing = [p for p in dict.fromkeys(_PARAM.findall(template))
               if not p.startswith(("parent", "steps.", "entities."))
               and (p not in full or str(full[p]).strip() == "")]
    if missing:
        selfs = [p for p in missing if p.startswith("self.")]
        given = [p for p in missing if not p.startswith("self.")]
        why = []
        if selfs:
            why.append(f"{short_name_of(obj)} has no value for {', '.join(selfs)} (set it on the object or its class)")
        if given:
            why.append("needs args: " + ", ".join(given))
        return _blocked(obj, method, "; ".join(why), via=via or "global", mro=mro,
                        params=command_params(template))
    # a call is a synchronous EVENT, not a queued link: the method entity
    # runs directly (its id would read as a 'label :: command' line to the
    # link parser); the run count and success rate land on the method
    ctx = {"vars": full, "steps": {}, "depth": _depth, "est": est, "self_eid": obj.eid}
    run = _exec_command_doc(srv, est, fld, mem, m, args=full, ctx=ctx)
    fed = run.get("fed") or (_fill(template, ctx)[0] if template else "")
    rc = run.get("rc")
    if run.get("status") in ("blocked", "partial"):
        status = run["status"]
    elif run.get("ok") or rc == 0:
        status = "ok"
    elif "timeout" in str(run.get("outcome") or run.get("error") or "").lower():
        status = "timeout"
    elif rc is None and str(run.get("error") or "").startswith("needs args"):
        status = "blocked"
    else:
        status = "error"
    read_only = bool((m.properties or {}).get("read_only"))
    mp = m.properties or {}
    mp["runs"] = int(mp.get("runs") or 0) + 1
    mp["last_run_ts"] = time.time()
    if run.get("rc") == 0:
        mp["ok_runs"] = int(mp.get("ok_runs") or 0) + 1
    m.properties = mp
    _audit(mem, {"event": "agent-method-call", "origin": "mcp", "eid": m.eid,
                 "object": obj.eid, "rc": run.get("rc"), "ok": run.get("rc") == 0})
    # THE RECEIPT ON THE OBJECT: its call ring + a field event about it (a
    # read_only method's calls stay out of the ring -- status polls must not
    # drown the state changes, nor feed the habits induction)
    shown = {k: v for k, v in (args or {}).items()}
    if not read_only:
        op = obj.properties or {}
        ring = list(op.get("calls") or [])
        ring.append({"ts": round(time.time(), 1), "method": method, "args": shown, "via": via or "global",
                     "status": status, "rc": rc, "fed": fed[:300]})
        op["calls"] = ring[-CALLS_RING:]
        obj.properties = op
        est.upsert(obj, embedder=None)
    if fld is not None:
        try:
            subj = short_name_of(obj)
            argtxt = ", ".join(f"{k}={v}" for k, v in shown.items())
            fld.write_events([ST.Event(stream="observation", tool="read" if read_only else "call",
                                       args={"object": subj, "method": method},
                                       result_head=(f"{subj}.{method}({argtxt}) -> rc {run.get('rc')} "
                                                    f"{run.get('outcome') or ''} {str(run.get('head') or '')[:120]}"),
                                       outcome="ok" if run.get("rc") == 0 else "failed",
                                       ts=time.time(), subject=E.field_id(obj.eid))], session="calls")
        except Exception:
            pass
    return {"ok": status == "ok", "status": status, "object": obj.eid, "method": mp.get("name") or method,
            "via": via or "global", "mro": mro, "fed": fed, "rc": rc, "outcome": run.get("outcome"),
            "head": run.get("head"), "tail": run.get("tail"), "error": run.get("error"),
            "read_only": read_only, "params": command_params(template),
            "steps": {k: {kk: vv for kk, vv in v.items() if kk != "tail"} for k, v in ctx["steps"].items()}}


# ---- FAN-OUT (Argus's review: "a named fan-out verb, not cardinality smuggled
# into ordinary calls"; "dry_run is the guard I'd use most -- show me who's
# about to be hit"): one method over every member of a class (or a pattern),
# capped, one summary receipt; each member's own call still lands on it
EACH_MAX_DEFAULT = 20
EACH_MAX_CEILING = 150


def members_of(est, over: str) -> list:
    """The objects a fan-out reaches: entities inheriting (transitively) from
    the class `over`, or matching it as a pattern ('ha:light.*')."""
    return member_split(est, over)[0]


def _weight(d) -> tuple:
    pp = d.properties or {}
    return (len(pp.get("readings") or []), len(pp.get("calls") or []), len(pp))


def member_split(est, over: str) -> tuple[list, list]:
    """(members, unconfirmed). Two cleanups the seat's first fan-out probe
    found (2026-10-10): ONE member per name (the field held two eids for
    'ha:light.aft_cabin_light' and it ran twice -- the one with more lived
    history wins); and a member served by a class GETTER that never gave it
    a reading of its own is unconfirmed ('ha:light.turn_on', '.x', '.head':
    tokens the night minted from text) -- reported, not called."""
    from .classes import short_name
    raw = _raw_members(est, over)
    best: dict[str, object] = {}
    for d in raw:
        k = short_name(d.eid)
        if k not in best or _weight(d) > _weight(best[k]):
            best[k] = d
    members, unconfirmed = [], []
    for d in best.values():
        pp = d.properties or {}
        if not pp.get("readings") and not pp.get("calls") and effective_script(est, d, "getter")[0]:
            unconfirmed.append(d)
        else:
            members.append(d)
    return members, unconfirmed


def _raw_members(est, over: str) -> list:
    if not over:
        return []
    if "*" in over:
        from .classes import matches
        return [d for d in matches(est, over) if not (d.properties or {}).get("method_name")]
    cls = est._resolve_spec(over)
    if cls is None:
        return []
    out = []
    for d in list((est.docs or {}).values()):
        if d.eid == cls.eid or (d.properties or {}).get("method_name") or (d.properties or {}).get("node_of"):
            continue
        if not (d.properties or {}).get("parents"):
            continue
        if any(a.eid == cls.eid for a in ancestors(est, d)):
            out.append(d)
    return out


def method_each(srv, est, fld, mem, over: str, method: str, args: dict | None = None,
                max_n=None, dry_run: bool = False, on_fail: str = "abort", _depth: int = 0) -> dict:
    if est is None:
        return {"ok": False, "status": "blocked", "error": "entity layer unavailable"}
    members, unconf = member_split(est, over)
    names = [short_name_of(m) for m in members]
    skipped = [short_name_of(m) for m in unconf]
    try:
        cap = int(max_n) if max_n not in (None, "") else EACH_MAX_DEFAULT
    except (TypeError, ValueError):
        cap = EACH_MAX_DEFAULT
    cap = max(1, min(cap, EACH_MAX_CEILING))
    base = {"over": over, "method": method, "n": len(members), "max": cap}
    if skipped:
        base["unconfirmed"] = skipped[:30]
        base["n_unconfirmed"] = len(skipped)
    if not members:
        return {**base, "ok": False, "status": "blocked",
                "error": f"no members of {over!r} (a class others inherit from, or a pattern)"}
    if dry_run:
        return {**base, "ok": True, "status": "ok", "dry_run": True, "members": names}
    if len(members) > cap:
        return {**base, "ok": False, "status": "blocked", "members": names[:30],
                "error": f"{len(members)} members exceed max={cap} -- raise max explicitly "
                         f"(ceiling {EACH_MAX_CEILING}) or narrow it; dry_run=true lists them"}
    results, n_ok = [], 0
    for m in members:
        r = method_call(srv, est, fld, mem, m.eid, method, args, _depth=_depth)
        st = r.get("status")
        results.append({"object": short_name_of(m), "status": st, "rc": r.get("rc"),
                        "error": (str(r.get("error") or "")[:120] or None)})
        if st == "ok":
            n_ok += 1
        elif on_fail != "continue":
            break
    ran = len(results)
    if n_ok == len(members):
        status = "ok"
    elif n_ok:
        status = "partial"
    else:
        status = "blocked" if all(x["status"] == "blocked" for x in results) else "error"
    _audit(mem, {"event": "agent-method-each", "origin": "mcp", "over": over, "method": method,
                 "n": len(members), "ran": ran, "ok": n_ok, "status": status})
    return {**base, "ok": status == "ok", "status": status, "ran": ran, "n_ok": n_ok,
            "stopped": ran < len(members), "results": results}


def removal_refusal(est, spec: str) -> str | None:
    """A live METHOD is not a stub (the seat's cleanup removed
    'shell:ha-light.status' as one, 2026-10-10, and 33 lights lost status):
    removing a method that has an owner or has run takes force=true."""
    from .classes import short_name
    d = est._resolve_spec(spec) if est is not None else None
    pp = (getattr(d, "properties", None) or {}) if d is not None else {}
    if not pp.get("method_name") or not (pp.get("method_of") or pp.get("runs")):
        return None
    owners = ", ".join(short_name(o) for o in (pp.get("method_of") or [])) or "global"
    return (f"{d.eid} is the method {pp['method_name']!r} of {owners} ({int(pp.get('runs') or 0)} runs) -- "
            f"every member of {owners} loses it; force=true removes it anyway")


def short_name_of(obj) -> str:
    from .classes import short_name
    return short_name(obj.eid)


def entity_unlink(srv, est, fld, mem, executor_spec: str, commands) -> dict:
    """THE UNLINK HAND: supersede the executor's `executes` edges to the
    named commands (mark-don't-delete, the relation store's own idiom).
    The residue props stay on the command docs; only the edge goes. The
    unlinked-but-never-run command (a pending degrade) loses also its
    pending_run_by marker."""
    if est is None:
        return {"ok": False, "error": "entity layer unavailable"}
    specs = command_specs(commands)
    ex_ent = est._resolve_spec(executor_spec or EXECUTOR_EID)
    if ex_ent is None:
        return {"ok": False, "error": f"no entity matches executor "
                                      f"{executor_spec!r}"}
    ex_key = ex_ent.eid
    be, origin = _relations_backend_of(srv, fld)
    removed, misses = [], []
    for spec in specs:
        ent = est._resolve_spec(spec)
        if ent is None:
            misses.append(spec)
            continue
        hit = False
        if be is not None:
            from . import relations as R
            for e in R.of(be, origin, ex_key, EXEC_REL):
                if e.get("subj") == ex_key and e.get("obj") == ent.eid \
                        and not e.get("superseded"):
                    key = R.relation_key(origin, ex_key, EXEC_REL, ent.eid)
                    doc = be.get("relations", key)

                    def _super(cur, k=key):
                        if cur is not None:
                            cur["superseded"] = True
                            cur["last_ts"] = time.time()
                            # THE WITHDRAW'S RECEIPT (the symmetry rule):
                            cur["receipt"] = {"op": "withdrawn",
                                              "ts": cur["last_ts"],
                                              "why": "entity_unlink"}
                        return cur
                    be.cas_update("relations", key, _super)
                    hit = True
        elif (ent.properties or {}).get("pending_run_by") == ex_key:
            props = ent.properties or {}
            props.pop("pending_run_by", None)
            ent.properties = props
            est.upsert(ent, embedder=None)
            hit = True
        (removed if hit else misses).append(ent.eid if hit else spec)
    if removed or specs:
        _audit(mem, {"event": "agent-entity-unlink", "origin": "mcp",
                     "eid": ex_ent.eid, "ok": True,
                     "removed": removed, "misses": misses})
    return {"ok": True, "executor": ex_ent.eid,
            "removed": removed, "misses": misses}


def executor_value(est, exec_spec: str = EXECUTOR_EID, be=None,
                   origin: str = "") -> dict:
    """The executor's (or any entity's) VALUE -- the model-facing post-
    processing shape: eid/class/properties (script_ internals hidden)
    + THE QUEUE: pending (the live executes-edges = owed work, oldest
    first) and done (the superseded edges' commands with their
    residues). The edge IS the state -- the drop-oldest cap bounds the
    pending depth."""
    if est is None:
        return {"ok": False, "error": "entity layer unavailable"}
    ent = est._resolve_spec(exec_spec)
    if ent is None:
        return {"ok": False, "error": f"no entity matches {exec_spec!r}"}

    def _node(key: str) -> dict | None:
        key = key if key.startswith(MEMBRANE_NS) else MEMBRANE_NS + key
        doc = (est.docs or {}).get(key)
        if doc is None:
            return None
        props = doc.properties or {}
        return {"eid": key,
                "command": str(props.get("command"))[:160] or None,
                "run_rc": props.get("run_rc"),
                "run_outcome": props.get("run_outcome"),
                "run_head": props.get("run_head"),
                "run_ms": props.get("run_ms")}
    pending, done = [], []
    be, origin = (be, origin) if be is not None else _relations_backend_of(
        getattr(est, "_srv", None) or type("S", (), {})())
    if be is not None:
        from . import relations as R
        for e in R.history_of(be, origin, ent.eid, EXEC_REL):
            if e.get("subj") != ent.eid:
                continue
            n = _node(str(e.get("obj") or ""))
            if n is None:
                continue
            (done if e.get("superseded") else pending).append({**n,
                "n_times": int(e.get("n") or 1)})
    else:
        for key, doc in (est.docs or {}).items():
            props = doc.properties or {}
            if props.get("pending_run_by") == ent.eid:
                n = _node(key)
                if n is not None:
                    pending.append(n)
    pending.sort(key=lambda e: str(e.get("eid")))
    done.sort(key=lambda e: str(e.get("eid")))
    return {"ok": True, "eid": ent.eid, "cls": ent.cls,
            "properties": {k: v for k, v in (ent.properties or {}).items()
                           if not str(k).startswith("script_")},
            "pending": pending[:EXEC_EDGES_CAP],
            "done": done[-EXEC_EDGES_CAP:]}


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
    # THE GENERALIZATION PHASE (docs/plans/2026-10-10-generalization.md):
    # induce, test on held-out experience -- shadow, logged only
    try:
        from .consolidate import maybe_induce
        rep = maybe_induce(srv, fld, est, now)
        if rep is not None:
            out["induce"] = {k: rep.get(k) for k in ("clusters", "geometry_gain", "name_gain", "entities", "ms")}
    except Exception as e:
        print(f"[night] induction skipped ({e})", file=sys.stderr)
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
                    # a getter reads a REAL source (an HTTP API, a
                    # sensor file): 1.0s couldn't fit one curl
                    "getter": 10.0}
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
        row = {"verb": verb, "arg": arg, "ts": float(f.get("ts") or time.time())}
        if f.get("owner"):
            row["owner"] = str(f.get("owner"))[:80]
        clean.append(row)
    props = ent.properties or {}
    # THE ATTENTION BUDGET (2026-10-08): the filters count against the one
    # limit across the watch senses; a replace costs only its difference
    from .attention import over as _over
    refused = _over(est, getattr(getattr(est, "_srv", None), "_watch_board", None),
                    len(clean) - len(props.get("watch_fields") or []))
    if refused:
        return refused
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
            # the entity's FIELD state carries coherence (2026-10-11 sweep: the
            # verb read entity properties nothing writes -- always 0, never crossed)
            v = float(props.get("coherence") or props.get("net_align") or 0.0)
            try:
                _srv = getattr(getattr(ent, "_store", None), "_srv", None) or _EST_SRV.get("srv")
                _st = _srv.efield.get(E.field_id(ent.eid)) if _srv is not None else None
                if _st is not None:
                    v = float(_st.coherence or 0.0)
            except Exception:
                pass
            stamp = props.get("wf_stamp_align")
            if stamp is None:
                props[stamp_k] = v
                continue
            if float(stamp) >= arg > v:     # crossed DOWN through the bar
                hits.append({"verb": "align", "arg": arg, "now": v})
            props[stamp_k] = v
        elif verb == "vel":
            # the drift ring's velocity is a METHOD (the prop was never written)
            if props.get("ring_velocity") is not None:
                v = float(props.get("ring_velocity") or 0.0)
            else:
                try:
                    v = float(ent.ring_velocity(time.time()) or 0.0)
                except Exception:
                    v = 0.0
            stamp = props.get("wf_stamp_vel")
            if stamp is None:
                props[stamp_k] = v
                continue
            if float(stamp) <= arg < v:     # crossed UP through the bar
                hits.append({"verb": "vel", "arg": arg, "now": v})
            props[stamp_k] = v
    return hits


_EST_SRV: dict = {"srv": None}        # the running seat, for the align verb's field read


def note_field_change(srv, est, ent, change_ts: float | None = None) -> None:
    """THE FLUSH'S INTAKE (called by the field's flush per dirty
    entity): the filters check, the gravity/freeze gate decides, the
    delivery is ONE 'field:changed' event (crossed filters riding it)
    to the ordinary pool. Hot: accumulate the burst; frozen: pause;
    the burst delivers on subsidence/melt/max-latency."""
    if est is None or ent is None:
        return
    if srv is not None:
        _EST_SRV["srv"] = srv
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
    pool = getattr(srv, "events_pool", None) or getattr(srv, "_events_pool", None)
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
    "result = {'render': 'clock ' + now, "
    "'patch': {'script_data': {'last_tick': now}}}"
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


def seed_executor_entity(est) -> bool:
    """The boot seeding: plant `tool:executor` ONCE (idempotent -- the
    model's edits survive every restart). The exec organ: commands link
    to it (`entity_link` / the 'executes' edges) and the engine runs
    them inline; FALSE when there is no store or it already lives."""
    if est is None:
        return False
    eid = MEMBRANE_NS + EXECUTOR_EID
    if eid in (est.docs or {}):
        return False
    from .entities import CLASS_TOOL, QuantumEntity
    est.upsert(QuantumEntity(eid=eid, cls=CLASS_TOOL, properties={
        "name": "executor",
        "summary": "the exec organ: link command entities to it "
                   "(entity_link) -- the edge runs them, the result "
                   "lands on the command",
        "silent_birth": True}))
    try:
        est.flush()
    except Exception:
        pass
    return True


REMOTE_EXECUTOR = "remote_executor"
REMOTE_EXECUTOR_SUMMARY = (
    "INTERFACE: an environment that runs code (the operator, 2026-10-10). An entity that "
    "implements it is ISA remote_executor and brings its own exec(code, timeout): the command "
    "that carries {{code}} into THAT environment. Describe the environment on the entity: "
    "lang (sh, jinja, js...), env (what it is and what it can reach), trust (who must approve a "
    "run). members_of('remote_executor') = every place code can run.")


def seed_interfaces(est) -> bool:
    """The boot seeding of the first interface (idempotent; the seat's edits
    survive): remote_executor with its one abstract method exec(code, timeout)."""
    if est is None:
        return False
    d = est._resolve_spec(REMOTE_EXECUTOR)
    if d is not None and (d.properties or {}).get("interface"):
        return False
    if d is None:
        r = est.add(cls=CLASS_COMPONENT, name=REMOTE_EXECUTOR, summary=REMOTE_EXECUTOR_SUMMARY)
        if not (isinstance(r, dict) and r.get("eid")):
            return False
    res = method_define(None, est, None, None, REMOTE_EXECUTOR, "exec", "", abstract=True,
                        params=["code", "timeout"])
    return bool(res.get("ok"))


def _cooldown_ok(props: dict, trigger: str, now: float) -> bool:
    """Per-entity per-trigger cooldown: a hot entity (session:now gets
    mentioned constantly) cannot exec every tick. properties
    ['script_cooldown_s'] overrides downward-only (the operator's knob)."""
    if trigger == "getter":
        return True       # a getter is paced by the read LRU (5s), never 60s
    last = ((props.get("script_cooldowns") or {}).get(trigger) or 0.0)
    cd = (_SCRIPT_COOLDOWN_WATCH_S if trigger == "watch"
          else _SCRIPT_COOLDOWN_DEFAULT_S)
    try:
        cd = min(cd, float((props.get("script_cooldown_s") or cd)))
    except (TypeError, ValueError):
        pass
    return (now - float(last or 0.0)) >= cd


def cooldown_message(props: dict, trigger: str, now: float) -> str:
    """The cooldown refusal that says when (the seat guessed for minutes)."""
    last = float(((props.get("script_cooldowns") or {}).get(trigger)) or 0.0)
    cd = (_SCRIPT_COOLDOWN_WATCH_S if trigger == "watch"
          else _SCRIPT_COOLDOWN_DEFAULT_S)
    try:
        cd = min(cd, float((props.get("script_cooldown_s") or cd)))
    except (TypeError, ValueError):
        pass
    wait = max(1, int(round(cd - (now - last))))
    return (f"cooldown: the {trigger} script ran {int(now - last)}s ago; "
            f"retry in {wait}s")


def _stamp_cooldown(props: dict, trigger: str, now: float) -> None:
    cds = props.get("script_cooldowns")
    if not isinstance(cds, dict):
        cds = {}
    cds[trigger] = now
    props["script_cooldowns"] = cds


# ---- SCRIPT INHERITANCE (2026-10-09, the operator: "it also has entity
# inheritance to do generalizations, why is it not doing that?"): an entity
# without its own script runs its nearest ancestor's (ISA, breadth-first) --
# ON ITSELF: the script sees the child's own properties. Write a sense once
# on a class; every member has it. Ticks inherit only where the class opts
# in (tick_inherits: true) -- a class of 15 would tick 15x unasked; watch
# scripts stay per entity (each carries its own subscription).
_SCRIPT_KEY = {"mention": "mention_script", "thought": "thought_script",
               "dreamed": "dreamed_script", "consolidation": "nudge_script",
               "tick": "tick_script", "watch": "watch_script", "getter": "getter_script"}
INHERITED_TRIGGERS = frozenset(("getter", "mention", "thought", "dreamed", "tick"))
# THE RESOLUTION RULE (the seat's review, 2026-10-09: "'nearest ancestor' is
# undefined when an entity has two parents -- fix the rule before anything
# depends on it"): breadth-first by generation, parents in their DECLARED
# order -- all parents before any grandparent; the first ancestor holding
# the script wins. The same order resolves inherited properties.
# THE KILL SWITCH: a class with inherit_disabled (or script_disabled) stops
# inheritance through it for every member at once.


def ancestors(est, ent) -> list:
    """The ISA ancestors in resolution order (cycle-safe, bounded)."""
    from .entities import MultiParentOps
    props = (getattr(ent, "properties", None) or {})
    seen, queue, out = {ent.eid}, list(MultiParentOps._parents_of(props)), []
    hops = 0
    while queue and hops < MultiParentOps.MAX_CHAIN * 4:
        hops += 1
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        anc = (est.docs or {}).get(pid) or est._resolve_spec(pid)
        if anc is None:
            continue                       # an unlinked/deleted class degrades to nothing
        out.append(anc)
        queue.extend(MultiParentOps._parents_of(anc.properties or {}))
    return out


def effective_script(est, ent, trigger: str) -> tuple[str | None, str | None]:
    """(code, the ancestor it came from or None for the entity's own)."""
    key = _SCRIPT_KEY.get(trigger)
    props = (getattr(ent, "properties", None) or {})
    own = props.get(key) if key else None
    if own or trigger not in INHERITED_TRIGGERS or est is None:
        return own, None
    for anc in ancestors(est, ent):
        ap = anc.properties or {}
        if ap.get("inherit_disabled") or ap.get("script_disabled"):
            return None, None              # the class's kill switch: none of its members inherit
        code = ap.get(key)
        if code and (trigger != "tick" or ap.get("tick_inherits")):
            return code, anc.eid
    return None, None


# what a member never inherits: identity, its own scripts and run residue,
# the class's own controls
_NOT_INHERITED = re.compile(r"^(?:name|summary|parents|class|eid|links|command|runs|last_run_ts|"
                            r"last_fed|voice_rule|tick_inherits|inherit_disabled|script_.*|.*_script|"
                            r"run_.*|silent_birth|merged_into|published|junk_anchor|derived_by)$")


def effective_properties(est, ent) -> dict:
    """The member's properties with its classes' defaults under them
    (nearest class wins among the classes; the member's own always win)."""
    own = dict(getattr(ent, "properties", None) or {})
    if est is None or not own.get("parents"):
        return own
    merged: dict = {}
    for anc in reversed(ancestors(est, ent)):           # farthest first, nearer overwrite
        for k, v in (anc.properties or {}).items():
            if not _NOT_INHERITED.match(k):
                merged[k] = v
    merged.update(own)
    return merged


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
    _est = getattr(srv, "entity_store", None)
    _EST_FOR_PROPS["est"] = _est
    code, _from = effective_script(_est, ent, trigger)
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
        # the tick is paced by tick_every
        return {"error": cooldown_message(props, trigger, now)}
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


_FIELD_READS: set = set()                # entities whose field-fired getter is running now


def field_read(srv, ent) -> bool:
    """The field read `ent`: its getter fires in the background (one at a
    time per entity; the getter's own LRU paces it). False when it had
    no getter or one is already running."""
    est = getattr(srv, "entity_store", None)
    if est is None or not effective_script(est, ent, "getter")[0]:
        return False
    with _FR_LOCK:
        if ent.eid in _FIELD_READS:
            return False
        _FIELD_READS.add(ent.eid)

    def go():
        try:
            from .watch_agents import entity_read_back
            entity_read_back(srv, ent, by="field")
        except Exception:
            pass
        finally:
            with _FR_LOCK:
                _FIELD_READS.discard(ent.eid)
    threading.Thread(target=go, daemon=True, name="field-read").start()
    return True


_FR_LOCK = threading.Lock()


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
                               if not (str(k).endswith("_script")
                                       or (str(k).startswith("script_")
                                           and k not in ("script_data", "script_render")))}
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
        def get(self, spec, run=False, **_ignored):
            """The entity as it stands; its getter fires in the background.
            run=True: the getter runs NOW and the fresh values come back (it
            spends this script's budget -- a test entity's read)."""
            e = est._resolve_spec(spec) if est else None
            if e is None:
                return None
            if run and e.eid != getattr(ent, "eid", None) and trigger != "getter":   # a getter's reads: one level
                try:
                    from .watch_agents import entity_read_back
                    entity_read_back(srv, e, force=True, by="field")
                except Exception:
                    pass
                run = "done"
            # A SCRIPT'S READ IS A READ (2026-10-11, the operator: "scripts read
            # should fire a getter too"): the target's getter runs, as on a
            # model read -- paced by its 5s LRU, ONE level deep (a getter
            # fired from a script's read fires no further getters), never
            # the reading script itself. The field schedules its own reads.
            # IN THE BACKGROUND: a real getter (a shell, an HTTP read) takes
            # seconds and the reading script's budget is a fraction of one --
            # the read returns the values as they stand; the fresh ones land
            # within the getter's run (a tick's periodic read keeps it live).
            if run != "done" and e.eid != getattr(ent, "eid", None) and trigger != "getter":
                field_read(srv, e)          # a GETTER's own reads fire nothing: one level deep
            # the script CODE and its bookkeeping stay hidden -- its VALUES
            # (script_data: what the getter read, script_render) were hidden
            # too: no script could read another entity's live values (2026-10-11)
            return {"eid": e.eid, "cls": e.cls,
                    "position": list(e.position or []),
                    "properties": {k: v for k, v in
                                   (e.properties or {}).items()
                                   if not (str(k).endswith("_script")
                                           or (str(k).startswith("script_")
                                               and k not in ("script_data", "script_render")))}}
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

    class _Timeline:
        """membrane.timeline.walk (advertised since 10-02, never built -- the
        scripts calling it errored every fire): walk(n) = the open dreamed
        branches, newest first [{eid, terminal, steps, status}]; walk("text",
        n) = the field's token walk continuing that text (a list of words)."""
        def walk(self, *args, limit=None, depth=None, steps=None, start=None, text=None, **_ignored):
            n = next((a for a in args if isinstance(a, int)), None) or limit or depth or steps or 4
            n = max(1, min(int(n), 12))
            seed = next((a for a in args if isinstance(a, str)), None) or start or text
            if seed:
                tt = getattr(getattr(srv, "membrane", None), "timeline", None)
                if tt is not None:
                    try:
                        toks = str(seed).lower().split()[:3]
                        return list(tt.walk(toks, max_steps=n) or [])
                    except Exception:
                        return []
                return []
            out = []
            try:
                for b in est.open_timelines(limit=n):
                    p = b.properties or {}
                    out.append({"eid": b.eid, "terminal": p.get("terminal"), "steps": list(p.get("steps") or [])[-n:],
                                "status": p.get("status")})
            except Exception:
                pass
            return out

    class _Membrane:
        """The assembled read-only interface (field/me/entities/
        intents) + speak (the planner-gated voice)."""
        def __init__(self):
            self.field = _FieldAPI()
            self.me = _Me(ent)
            self.entities = _Entities()
            ib = getattr(srv, "_intent_board", None)
            self.intents = _Intents(ib)
            self.timeline = _Timeline()
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
            # THE MODEL, FROM A SCRIPT (llm.py): one call per fire, about
            # this entity; capped per caller and per hour like every call
            called = [False]
            def _call_llm(prompt, about: str = "") -> dict:
                if called[0]:
                    return {"rc": 1, "text": "one model call per fire"}
                called[0] = True
                from . import llm as _LLM
                return _LLM.request(srv, str(prompt), about=about or ent.eid,
                                    caller="script:" + str(getattr(ent, "eid", "?")).replace("membrane::", ""))
            self.call_llm = _call_llm
            # A WAKE, FROM A SCRIPT (llm.py): one per fire; it fires read-only (a field-call)
            woke = [False]
            def _wake(at, prompt, about: str = "") -> dict:
                if woke[0]:
                    return {"rc": 1, "text": "one wake per fire"}
                woke[0] = True
                from . import llm as _LLM
                return _LLM.set_wake(srv, about or ent.eid, str(at), str(prompt),
                                     caller="script:" + str(getattr(ent, "eid", "?")).replace("membrane::", ""))
            self.wake = _wake

    return _Membrane()


_EST_FOR_PROPS = {"est": None}       # the running seat's store (set at run time)


def _props_for_script(ent) -> dict:
    try:
        return effective_properties(_EST_FOR_PROPS["est"], ent)
    except Exception:
        return dict(ent.properties or {})


def _script_state(ent, now: float, extra: dict, trigger: str,
                  timeout: float) -> dict:
    """The script's state: geometry + properties (script_ internal keys
    hidden) + own data + THE TRIGGER SHAPE (what fired, with what) +
    the trigger's timeout (the script can see its own budget)."""
    return {"eid": ent.eid,             # whose script this run is (a class's script runs on its member)
            "position": ent.position,
            "ring": [p for _, p in (getattr(ent, "position_history",
                                            []) or [])],
            "velocity": (ent.ring_velocity(now)
                         if getattr(ent, "position", None) else 0.0),
            "cells": len(ent.links or []),
            "properties": {k: v for k, v in (_props_for_script(ent)).items()
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
    # THE REFUSAL ON THE ENTITY (2026-10-11 sweep: the docs promised patch of
    # any property and delete; the engine refuses both -- by covenant -- but
    # only a getter's caller ever saw it): every trigger's refusal is noted
    # where an entity read shows it
    _ref = list(out.get("patch_refused") or [])
    if out.get("delete") and trigger not in ("consolidation", "delete"):
        _ref.append("delete (only a delete_script retires; else entity remove)")
    if _ref:
        props["patch_refused"] = {"keys": _ref[:12], "trigger": trigger, "ts": round(time.time(), 1),
                                  "allowed": "render, patch.script_data, patch.position, run, intent"}
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
    # THE INTENT VERDICT (C2, 2026-10-06): a script planting ITS OWN
    # commitment -- {intent: "<goal text>"} routes through the SAME
    # create-or-pin lane the model's tools use (a shared Goal entity +
    # the seat's active-set pin + the announcement). The steering wheel
    # belongs to the model's scripts too: a watch/tick script that saw
    # the shape of ongoing work may propose the direction, the ENGINE
    # plants. Honest refusal when the goal layer is unmounted.
    if out.get("intent"):
        intent_text = str(out["intent"])[:400].strip()
        board = getattr(srv, "_intent_board", None)
        if not intent_text or board is None:
            out["intent_result"] = {"ok": False,
                                    "error": ("empty intent" if not intent_text
                                              else "the goal layer is "
                                                   "unavailable")}
        else:
            from .opencode import _membrane_intent_create_or_pin
            ires = _membrane_intent_create_or_pin(
                srv, intent_text,
                planted_by="script:" + str(getattr(ent, "eid", "?")).replace("membrane::", "")[:60])
            out["intent_result"] = ires
            if ires.get("ok"):
                try:
                    from .opencode import speak
                    speak(srv, f"goal active: {intent_text[:120]}",
                          kind="note-pattern")
                except Exception:
                    pass
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

# ---- the thought / dreamed triggers (2026-10-11: settable since 10-02, never fired) ----
TRIGGER_COOLDOWN_S = {"thought": 10.0, "dreamed": 30.0}
_trigger_last: dict = {}


def fire_trigger(srv, ent, trigger: str, extra_state: dict | None = None) -> dict | None:
    """Run `ent`'s armed thought_/dreamed_script once (the engine's budgets,
    3 strikes, the per-entity cooldown). None when it has none, it's dormant
    (set before the trigger was wired: setting it again arms it) or cooling."""
    est = getattr(srv, "entity_store", None)
    if est is None or ent is None:
        return None
    code, _from = effective_script(est, ent, trigger)
    if not code:
        return None
    holder = ent
    if _from:
        holder = (est.docs or {}).get(_from) or ent
    if not (holder.properties or {}).get(f"{trigger}_armed_ts"):
        return None
    now = time.time()
    key = (ent.eid, trigger)
    if now - _trigger_last.get(key, 0.0) < TRIGGER_COOLDOWN_S.get(trigger, 10.0):
        return None
    _trigger_last[key] = now
    out = run_entity_update_script(srv, ent, trigger, extra_state=extra_state or {})
    apply_entity_update(srv, ent, out, trigger)
    return out


def entities_named(srv, text: str, k: int = 4) -> list:
    """The store entities a text names (the field's specific mentions,
    resolved), for the triggers that fire on what a thought or dream is about."""
    est = getattr(srv, "entity_store", None)
    fld = getattr(srv, "efield", None)
    if est is None or not text:
        return []
    names = []
    try:
        from .efgate import specifics
        names = [x if isinstance(x, str) else x[0] for x in (specifics(fld, text) or [])] if fld is not None else []
    except Exception:
        names = []
    if not names:                       # no field (or nothing it knows): the id-shaped tokens
        import re as _re
        names = [t.strip(".,;:'\"()") for t in _re.findall(r"[\w.-]+(?::[\w.-]+)+", text)]
    out, seen = [], set()
    for n in names:
        e = est._resolve_spec(str(n).split(":", 1)[-1] if str(n).startswith("m:") else str(n))
        if e is not None and e.eid not in seen:
            seen.add(e.eid)
            out.append(e)
        if len(out) >= k:
            break
    return out
