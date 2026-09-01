"""Append-only exemplar store: the agent's analogical memory.

An exemplar is a judgment the agent (or a batch judge) recorded:

    situation : str        normalized summary of the task/context (what was faced)
    action    : str        a REPLAYABLE structured action (tool calls / edit / decision)
    outcome   : str        what happened when that action ran
    vec       : list[float]  unit situation embedding (local Ollama, mxbai-embed-large)
    safe      : bool       derived: (reask_count - contest_count) < demote_threshold.
                          Replaying a safe action is low-risk; an unsafe one is only a hint.
    reask_count  : int     how many times the environment re-asked after this was absorbed
                          (the usefulness authority voting "this replay did not satisfy").
    contest_count: int    how many times the model contested a demotion of this exemplar
                          (the correctness authority voting "my answer was right anyway").

`safe` is a TWO-AUTHORITY boundary, not a single oracle. A re-ask (the environment)
votes against; a contest (the model) votes for. The gate reads `safe` unchanged; the
counts are the evidence, and `safe` is recomputed from them -- so demotion is
evidential and reversible, not a hard one-shot flip that treats the environment as
infallible. Neither authority has a monopoly: each re-ask adds a vote, each contest
cancels one, and a contested-but-reasked exemplar holds the disagreement in the record
rather than resolving it one way silently.

The store is permanent: no decay, no EMA, no averaging. Within-session context is
the conversation; old lessons must not fade. It grows by append at judgment time;
at runtime the agent queries it by cosine to the current task's embedding.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, asdict, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np


@dataclass
class Exemplar:
    situation: str
    action: str
    outcome: str
    vec: list[float]
    safe: bool = True
    reask_count: int = 0
    contest_count: int = 0
    # character ids this scene belongs to (the [[cast]] ledger). Empty for legacy
    # exemplars and chat-learned turns; populated by story-fed growth (the
    # chronicle scenes). Backward-compatible: old field records lack the key ->
    # Exemplar(**rec) falls back to this default.
    tags: list[int] = field(default_factory=list)
    # ---- recall hygiene (model provenance) ----
    # WHICH answering model produced this action (the routing verdict's pick,
    # stamped at learn time). Empty for legacy rows / interceptor notes. The
    # recall surfaces show it ("[via <model>]") and the boot hygiene sweep
    # mass-demotes exemplars whose model the compat screen now rejects
    # (bakllava/whisper/toys from the dumb-model era) -- the ordinary
    # two-authority boundary applies: the model's first contest restores.
    model: str = ""
    # True when this scene was authored by the cell's OWN n-gram voice (the
    # cells-act chronicle / --speak ngram path), NOT by the LLM. Such beats are
    # absorbed into the field (so the world still grows + recall stays rich) but
    # EXCLUDED from the n-gram's training corpus + the cells-act seed -- otherwise
    # the n-gram trains on its own stammer and spirals to gibberish. The LLM is
    # the teacher at seed time; the n-gram speaks only from trusted (LLM-seeded)
    # material. Backward-compatible: old records lack the key -> trusted by default.
    self_authored: bool = False
    # ---- the global-field layer (docs/global-field.md) ----
    # scope: is this memory local knowledge or commons? "global" exemplars live
    # in a curated cross-project field, operator-promoted only (slice 1).
    scope: str = "project"
    # owner: the field-id where this memory was BORN (provenance -- who vouches
    # for the text); home: the field that currently MASTERs it (who to ask).
    # Both default empty = born here, mastered here (single-field normal form).
    owner: str = ""
    home: str = ""
    # consensus evidence: [(field-id, iso-ts), ...] -- the distinct fields that
    # independently produced this note (the slice-2 promotion path).
    votes: list = field(default_factory=list)
    # residency evidence (slice 3): [(field-id, iso-ts), ...] -- the fields that
    # actually USED this note (it grounded a reply / surfaced in a top-up).
    # Capped by the writer; home drifts toward the heaviest user ("homes follow
    # use, not birth"). Unused locally (a local field never sets it).
    uses: list = field(default_factory=list)
    # ---- the shared-substrate layer (origin axis + CAN arbitration) ----
    # origin: WHICH membrane wrote this, as "start-folder@hostname" -- the axis
    # that keeps two membranes' writes from colliding and lets the reader
    # filter to its own patch + the commons. Empty = born here (legacy rows).
    origin: str = ""
    # alternates: NON-DESTRUCTIVE CONTENTION (cortex's CAN bus, retold) -- when
    # two membranes earned DIFFERENT actions for the same situation, the less
    # salient version defers here instead of being erased:
    #   [{"action": str, "origin": str, "salience": float, "ts": iso}, ...]
    # capped (ALTERNATES_MAX); a deferred action is re-promotable if the
    # current master later degrades (demoted/forgotten) and the alternate is
    # more salient to a future query. Losers wait; nothing is lost.
    alternates: list = field(default_factory=list)
    # ---- the schema sliding window (docs: SCHEMA_VERSION below) ----
    # Keys a LOAD encountered that THIS version's Exemplar does not know land
    # here, and the save paths spread them back as TOP-LEVEL record keys (the
    # wire never contains the literal "extra" key). A newer field loaded by
    # this version round-trips losslessly; to an older reader those keys are
    # simply invisible (this load path is the one that IGNORES unknowns).
    extra: dict = field(default_factory=dict)
    # ---- the ephemeral tier (experiments/ephemeral_tier.py, OVERALL GREEN) --
    # True = a CANDIDATE (fresh capture nobody has stood by): recalled only
    # through the merged surface (recency-weighted), never replayed by the
    # gate, retired by the CLOCK at TTL unless evidence promotes it. False =
    # canon (evidence of reality lived here). Kept OUT of the v1 wire shape
    # when False (legacy rows stay byte-identical); the birth timestamp rides
    # extra["eph_born"].
    ephemeral: bool = False

    def recompute_safe(self, demote_threshold: int) -> None:
        """Re-derive `safe` from the evidence: net votes against must reach the threshold.

        safe = (reask_count - contest_count) < demote_threshold. Default threshold 1 ->
        one un-contested re-ask demotes (the environment's single strong signal), but a
        contest cancels it (the model's veto). Raise the threshold for noise-robustness
        (require accumulated re-asks before demoting); the boundary itself is the counts.
        """
        self.safe = (self.reask_count - self.contest_count) < demote_threshold


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


# ---- persistence backends --------------------------------------------------
# JSON file (default, zero-setup) or Couchbase (durable incremental writes,
# shared across processes). The store object stays the SAME: exemplars live in
# RAM for the gate/kNN; the backend only decides how mutations reach disk.
# Couchbase keys are `membrane:ex:<n>` -- but indexes are volatile (the
# compactor forgets/renumbers), so each document carries the full record and
# mutations are expressed against CONTENT, not position: upsert a deterministic
# content key (sha1 of situation+action) under scope `membrane`, and deletions
# are a query-and-remove sweep. The in-RAM list remains positional; save() is
# a full reconcile (upsert every current exemplar, remove stale keys), which
# turns the 30MB full-JSON rewrite into per-doc upserts (unchanged docs are
# cheap idempotent writes) plus one lookup sweep.

_CB_SCOPE = "membrane"
_CB_COLL = "exemplars"

# ---- the schema sliding window ----------------------------------------------
# SCHEMA_VERSION is bumped ONLY by a registered migration (below). The window
# rule: this version READS records of version v in [V-1, V] (older writers:
# migrated up on load) and WRITES its own version V, PRESERVING unknown keys
# (Exemplar.extra -> top-level wire keys) so a record written by V+1 survives
# a round-trip through V untouched -- invisible to this reader, intact for the
# newer reader. Two-window safety: never skip a version (V-2 records must be
# migrated by V-1 first; the registry chains them).
#
# COUCHBASE DOCS: each exemplar doc carries `_scm` = the writer's version; a
# reader inside its window decodes normally and PRESERVES unknown keys; a doc
# stamped OLDER than its window (an ancient shared bucket, pre-stamp docs
# count as v1) is migrated by the same registry. This is what lets membranes
# of different versions share one bucket without erasing each other's fields.
#
# JSON field files: raw exemplar LISTS (v1 format). A future envelope version
# bumps WIRE_VERSION to {"version": N, "exemplars": [...]} -- load accepts both
# shapes forever, so one version can always read its predecessor's file.
SCHEMA_VERSION = 1
WIRE_VERSION = 1          # the JSON file envelope version (raw list == 1)
SCHEMA_STAMP = f"s{SCHEMA_VERSION}"

# Registry key: (from_version,). Value: a function record-dict -> record-dict
# applied IN ORDER (1 -> 2 -> ... -> V). Add exactly one entry per version
# bump; older entries stay forever (the sliding window's spine).
MIGRATIONS: dict[int, Any] = {}


def migrate_record(rec: dict, from_version: int,
                   to_version: int = SCHEMA_VERSION) -> dict:
    """Lift a raw record dict from ``from_version`` to ``to_version`` by
    applying the registry's migrations in order. Unknown target versions and
    gaps simply leave the record as-is except the version key -- decode never
    destroys data (an out-of-window record keeps its unknown keys in ``extra``)."""
    rec = dict(rec)
    for v in range(from_version, to_version):
        step = MIGRATIONS.get(v)
        if step is not None:
            rec = step(rec)
    return rec

DATA_DIRNAME = ".membrane"   # the per-project data dir (cli.DATA_DIR)

# ---- the origin axis + CAN arbitration constants ----------------------------
ALTERNATES_MAX = 4          # deferred contenders kept per exemplar
ARBITRATION_ROUNDS = 3      # CAS mismatch -> re-read, re-arbitrate, retry
VEIL_POKE_SIM = 0.90        # foreign-scope exemplars poke through the veil at
                            # this cosine (they shadow, they never master)

# ---- the ephemeral tier (experiments/ephemeral_tier.py, OVERALL GREEN) ------
# The invariant: EVIDENCE OF REALITY -> DURABLE; SILENCE -> EPHEMERAL;
# CONFIRMATION IS THE PROMOTION. Candidates (fresh turns nobody has stood by,
# quarantined dreams, watch pendings) live in the ephemeral tier with a TTL;
# the CLOCK does the GC (Couchbase doc TTL in production, eph_born compare in
# the JSON mirror) -- no janitor sweep, ONE horizon shared with the dreamer.
EPH_TTL_S = float(os.environ.get("MEMBRANE_EPHEMERAL_TTL_S", 3 * 86400))
EPH_COLL = "exemplars_ephemeral"   # sibling collection, same scope
# The ephemeral BUCKET: candidates live in a dedicated ephemeral-type bucket
# (server-side TTL, the clock's GC) alongside the canon bucket -- the
# cortex-core / cortex-ephemeral convention. Resolution order in
# `_resolve_eph_bucket`: explicit COUCHBASE_EPHEMERAL_BUCKET, then
# "<canon>-ephemeral" if it exists, then any existing *ephemeral* bucket,
# then CREATE an ephemeral-type bucket (SDK can't: management REST), else
# None -> the sibling-collection fallback (ephemeral tier rides the canon
# bucket's exemplars_ephemeral collection).
EPH_BUCKET_SUFFIX = "-ephemeral"
_JL_SEED = 5                # picked for low basis crosstalk at toy scale (lab E4)
_SKETCH_D = 96              # production sketch dim (D=1024 -> d=96)


def _is_lab_field_path(field_path: str | Path) -> bool:
    """EXPERIMENTS-ISOLATION predicate: does this field path smell like a LAB
    field (the experiments/ dir, a tempdir, or a pytest tmp fixture)? Lab
    membranes build Membrane(field_path=tempfile...) with the default store
    autodetect; without this guard they silently join the LIVE shared
    substrate -- the observed failure was a dream-loop field reusing the
    cortex-core bucket. False for everything that looks like a real membrane
    (a .membrane/ data dir, an explicit user path) -- those keep auto-detect."""
    p = Path(field_path).resolve()
    parts = p.parts
    if any(seg.startswith("dream-loop-") or seg == "pytest-" or
           seg.startswith("pytest-of-") for seg in parts):
        return True
    if "experiments" in parts and p.suffix == ".json" and \
            parts[-1].startswith("field-"):
        return True
    if "site-packages" in parts:      # never expected; kept out of paranoia
        return False
    try:
        tmp_roots = {Path(tempfile.gettempdir()).resolve()}
        have_common = any(p.is_relative_to(t) for t in tmp_roots)
    except (OSError, ValueError):
        have_common = False
    return have_common


def _origin_tag(field_path: str | Path) -> str:
    """The origin axis: "<start-folder>@<hostname>", computed from the RESOLVED
    field path (so two membranes started from the same folder agree on the tag
    regardless of symlinks/relative paths). This is the wire identity every
    write stamps and every read filters by. The START FOLDER is the project
    directory, not the data dir: for the per-project layout (<proj>/.membrane/
    field.json) that is the PARENT of the field's directory.

    PERSISTED IDENTITY: the derived tag is only a DEFAULT. On first boot the
    tag is written to <data-dir>/origin (mode 600, gitignored like the rest of
    .membrane/) and every later boot reuses it -- so renaming the repo (or
    moving it) keeps the same origin: your couchbase patch stays YOURS, your
    memories keep their provenance, forgets keep matching them. Rename-
    migration is zero-step for the common case; delete <data-dir>/origin to
    deliberately fork a new identity. Hand-edit it to ADOPT an old identity
    (e.g. after restoring a clone from a backup path)."""
    import socket
    p = Path(field_path).resolve()
    data_dir = (p.parent if p.parent.name == DATA_DIRNAME
                else p.parent / DATA_DIRNAME)
    tag_file = data_dir / "origin"
    if tag_file.is_file():
        stored = tag_file.read_text().strip()
        if stored:
            return stored
    tag = (f"{p.parent.name if p.parent.name != DATA_DIRNAME
            else p.parent.parent.name}@{socket.gethostname()}")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        tag_file.write_text(tag + "\n")
        tag_file.chmod(0o600)
    except OSError:
        pass                    # read-only cwd: fall back to the derived tag
    return tag


# ---- wire decode/encode (the schema window's moving parts) ------------------
_EX_KNOWN = frozenset(Exemplar.__dataclass_fields__) - {"extra"}


def _rec_from_wire(rec: dict) -> "Exemplar":
    """Decode one wire record -> Exemplar, schema-window aware.

    v1 wire shape: flat exemplar keys. Later versions may add top-level keys
    or an envelope; this function is the ONE place that decides. Unknown keys
    are preserved in ``extra`` (lossless round-trip through an older reader);
    unknown VERSIONS are tolerated the same way -- decode never destroys."""
    rec = dict(rec or {})
    v = int(rec.get("_scm") or rec.get("_schema") or 1)
    if v < SCHEMA_VERSION:
        rec = migrate_record(rec, v)
    known = {k: rec[k] for k in _EX_KNOWN if k in rec}
    extra = {k: rec[k] for k in rec
             if k not in _EX_KNOWN and not k.startswith("_")
             and k != "extra"}
    ex = Exemplar(**known, extra=extra)
    stamp = rec.get("_scm", rec.get("_schema"))
    if isinstance(stamp, int):
        # same-or-newer stamp rides along; save paths re-spread it
        ex.extra["_scm"] = stamp
    return ex


def _ex_to_wire(ex: "Exemplar", stamp: bool = True) -> dict:
    """Exemplar -> wire record: dataclass fields as top-level keys, ``extra``
    spread back into top level. ``stamp`` adds the ``_scm`` version stamp --
    for COUCHBASE docs (underscore keys are invisible to older readers, the
    shared-bucket window). JSON files stay unstamped: byte-identical to the
    v1 raw-list shape every released version already reads."""
    rec = {k: v for k, v in asdict(ex).items() if k != "extra"}
    # empty provenance is the legacy shape: only rows WITH a model carry the
    # key, so pre-provenance JSON stays byte-identical to v1
    if not rec.get("model"):
        rec.pop("model", None)
    # the ephemeral flag: legacy shape too -- only CANDIDATES carry it
    if not rec.get("ephemeral"):
        rec.pop("ephemeral", None)
    extra = getattr(ex, "extra", None) or {}
    for k, v in extra.items():
        if not k.startswith("_"):
            rec[k] = v
    if stamp:
        scm = extra.get("_scm")
        rec["_scm"] = scm if isinstance(scm, int) and scm > SCHEMA_VERSION \
            else SCHEMA_VERSION
    return rec


def _ex_key(ex: "Exemplar") -> str:
    """Deterministic content key: same situation+action+origin -> same doc.

    Keys are NAMESPACED by origin: two membranes that earned the same Q/A
    keep separate documents (each patch's evidence stays its own); they merge
    only through arbitration. Blank origin (legacy rows) hashes exactly as
    the pre-shared-substrate scheme did, so old keys stay deterministic."""
    import hashlib
    prefix = f"{ex.origin}\x00" if ex.origin else ""
    h = hashlib.sha1(f"{prefix}{ex.situation}\x00{ex.action}".encode("utf-8"))
    return h.hexdigest()[:20]


def _situ_key(situation: str) -> str:
    """The CONTEST key: hash of the situation text alone. Two origins earning
    answers for the same situation contend for one master slot; this is how
    arbitration finds them."""
    import hashlib
    return hashlib.sha1(" ".join(situation.split()).encode("utf-8")).hexdigest()[:20]


def _eph_key(ex: "Exemplar") -> str:
    """Content key for an EPHEMERAL row: separate namespace from the canon
    keys (a candidate and its later promoted self are different docs in
    different collections; sharing the hash would make promotion invisible
    to the delta sets)."""
    import hashlib
    prefix = f"{ex.origin}\x00" if ex.origin else ""
    h = hashlib.sha1(f"eph\x00{prefix}{ex.situation}\x00{ex.action}".encode("utf-8"))
    return h.hexdigest()[:20]


class ExemplarStore:
    """Append-only store of exemplars; queryable by cosine to a query vector."""

    def __init__(self) -> None:
        self.exemplars: list[Exemplar] = []
        # set by load() when a corrupt field could NOT be quarantined: save()
        # then refuses to write, so it never clobbers the unrecovered file.
        self._quarantine_failed = False
        # couchbase backend state: _cb is the collection handle once connected
        # (None -> JSON-file backend); _cb_keys tracks live doc ids so save()
        # can reconcile deletions (forget/compactor removals). _cb_archive is
        # the cold-tier handle (exemplars_archive) -- a separate collection,
        # NOT the live one (the early-return cache is mode-aware for this).
        self._cb = None
        self._cb_archive = None
        self._cb_bucket: str = ""
        self._cb_keys: set[str] | None = None
        # the origin axis: "<start-folder>@<hostname>", set at load/boot (empty
        # = a store constructed bare, e.g. tests). Stamped onto every write by
        # the save path; the reader-side veil filter keys off it.
        self.origin: str = ""
        # indexes of FOREIGN-patch exemplars hydrated as poke-through
        # candidates (see load_couchbase / veil_poke).
        self.veil_foreign: set[int] = set()
        # situ keys already arbitrated (this session): every save re-writes
        # ALL own exemplars; without this, boot + every learn would replay
        # get/CAS on every situation's channel doc. Seeded at hydrate with
        # the own patch's situ keys; each save only arbitrates NEW situations.
        self._cb_situ: set[str] = set()
        # ---- the ephemeral tier (candidates; evidence -> durable) ----
        # ONE list: exemplars carry ex.ephemeral. The tier never splits the
        # index spaces (re-ask votes, index_of, compactor, veil_foreign all
        # index store.exemplars). Promotion flips the flag; the couchbase
        # doc moves buckets/collections at the next save (delta sets
        # invalidate). _cb_eph_bucket: the DEDICATED ephemeral bucket when
        # one was resolved ("" -> sibling collection in the canon bucket).
        self._cb_eph = None
        self._cb_eph_bucket: str = ""
        self._cb_keys_eph: set[str] = None  # type: ignore[assignment]
        # delta-save bookkeeping: content keys already known current on the
        # backend (written by us, unmutated since). save() upserts only
        # exemplars NOT in this set and removes only vanished keys -- the
        # full-reconcile cost (per-save re-upsert of the whole patch) dies.
        self._written: set[str] = set()
        self._written_eph: set[str] = set()

    # ---- the ephemeral tier (capture / promote / expire / merged recall) ----
    @staticmethod
    def _eph_born(ex: Exemplar) -> float:
        try:
            return float((ex.extra or {}).get("eph_born", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def capture_ephemeral(self, ex: Exemplar, now: float | None = None) -> int:
        """A CANDIDATE enters the field: appended to the ONE list flagged
        ``ephemeral`` (all indexes stay coherent; the replayable set is
        canon-only by definition). Callers must NOT hand a confirmed/refuted
        row here -- that is evidence, promote it."""
        ex.ephemeral = True
        ex.extra = dict(ex.extra or {})
        ex.extra["eph_born"] = float(now if now is not None else time.time())
        ex.extra.setdefault("eph_ttl", EPH_TTL_S)
        self.append(ex)
        return len(self.exemplars) - 1

    def promote_ephemeral(self, index: int, *, safe: bool,
                          outcome: str | None = None,
                          how: str = "") -> int:
        """The ONLY cross-tier move: reality signed (confirm-contest, re-ask
        refute, dream vote, compactor distill). The flag drops; the row
        becomes canon (safe carries the vote's sign: a refute is a durable
        UNSAFE row -- the confirmed negative). ``outcome`` is left untouched
        by default: the tier flips, the existing verdict vocabulary
        (pending/teed/escalate/confirmed) stays the caller's business. The
        couchbase doc moves from the ephemeral collection to canon at the
        next save (both delta keys invalidate). Returns its (unchanged)
        index. Raises when the row is not an ephemeral candidate (the
        evidence paths may then run their normal in-canon route)."""
        ex = self.exemplars[index]
        if not ex.ephemeral:
            raise ValueError(f"promote_ephemeral: #{index} is already canon")
        ex.ephemeral = False
        ex.safe = bool(safe)
        if outcome is not None:
            ex.outcome = outcome
        ex.extra = {k: v for k, v in (ex.extra or {}).items()
                    if k not in ("eph_born", "eph_ttl")}
        # a promotion is a WRITE in both collections' books: the canon key
        # must upsert (new content there) and the ephemeral key must delete
        self._written.discard(_ex_key(ex))
        self._written_eph.discard(_eph_key(ex))
        return index

    def expire_ephemeral(self, now: float | None = None) -> list[int]:
        """The clock's GC: candidates past the horizon (TTL == the dreamer's
        GC horizon). Returns their INDEXES; the caller forget()s + saves
        (expiry is an operator-shaped deletion of CANDIDATES only -- never
        canon, so the append-only promise for earned history holds). On the
        couchbase backend the docs are already TTL-expired server-side;
        this trims the RAM mirror to match."""
        now = float(now if now is not None else time.time())
        return [i for i, e in enumerate(self.exemplars)
                if e.ephemeral and now - self._eph_born(e) >= EPH_TTL_S]

    def promote_on_use(self, q: np.ndarray, *, sim_min: float = 0.85,
                       now: float | None = None, max_promote: int = 2,
                       exclude: set[int] | None = None
                       ) -> list[int]:
        """USE IS THE PROMOTION (lab E1's confirm, generalized): candidates
        near a REAL turn's query vector are the evidence that the turn's
        need matched the candidate's content -- promote them durable-safe.
        ``exclude`` skips indexes captured by THIS turn (a candidate must
        never confirm itself: use is a LATER turn's evidence). Called from
        the confirm path (a gate turn that went to the model and came back
        served). Never touches canon; raises nothing."""
        now = float(now if now is not None else time.time())
        qn = _unit(q)
        out: list[int] = []
        excl = exclude or set()
        for i, e in enumerate(self.exemplars):
            if not e.ephemeral or len(out) >= max_promote or i in excl:
                continue
            ev = np.asarray(e.vec, dtype=float)
            n = float(np.linalg.norm(ev))
            if n < 1e-12:
                continue
            if float(qn @ (ev / n)) >= sim_min:
                self.promote_ephemeral(i, safe=True, how="use")
                out.append(i)
        return out

    def recall_merged(self, q: np.ndarray, k: int = 5, *,
                      now: float | None = None,
                      w_rec: float = 0.15, tau: float = 6 * 3600.0
                      ) -> list[tuple[Exemplar, float]]:
        """DURABLE + EPHEMERAL, recency-weighted (lab E3): a fresh candidate
        outranks stale canon for the same need; canon recall is untaxed when
        nothing is fresh. The returned sim is the EFFECTIVE score (cosine +
        the ephemeral recency bonus); callers treating it as plain cosine
        still work -- the bonus only matters near ties, exactly where the
        fresh answer should win."""
        if not self.exemplars:
            return []
        now = float(now if now is not None else time.time())
        qn = _unit(q)
        V = self.vectors()
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        sims_all = Vn @ qn
        order = np.argsort(-sims_all)[:min(len(self.exemplars), k + 16)]
        rows: list[tuple[Exemplar, float]] = []
        for i in order:
            ex = self.exemplars[i]
            sim = float(sims_all[i])
            if ex.ephemeral and w_rec > 0:
                age = max(now - self._eph_born(ex), 0.0)
                sim += w_rec * float(np.exp(-age / tau))
            rows.append((ex, sim))
        rows.sort(key=lambda t: -t[1])
        return rows[:k]

    def append(self, ex: Exemplar) -> None:
        self.exemplars.append(ex)

    def forget(self, indexes) -> int:
        """Operator-given deletion: drop the exemplars at ``indexes``.

        The store is append-only for EARNED history -- the gate, the compactor and
        the boundary paths only ever add cells or mutate the evidence counts;
        nothing in the learning loop may remove a cell. The OPERATOR stands
        outside that promise: the same authority that injects entropy may withdraw
        what it gave, and an explicitly commanded forget (``membrane: forget ...``,
        the skills in .opencode/skills/, shipped as membrane/skills/) removes cells
        outright. Nothing else may
        call this. Persists nothing here -- the caller saves.

        Returns the number removed (out-of-range indexes are ignored -- the
        compactor may have renumbered between listing and dropping)."""
        idx_set = {int(i) for i in indexes if 0 <= int(i) < len(self.exemplars)}
        if not idx_set:
            return 0
        self.exemplars = [e for i, e in enumerate(self.exemplars)
                          if i not in idx_set]
        return len(idx_set)

    def set_safe(self, index: int, value: bool) -> None:
        """Manually override an exemplar's `safe` (the review surface / a supervisor).

        The store stays append-only: no exemplar is added, removed, or rewritten --
        only this one mutable bit changes. The normal path mutates the *evidence*
        (`add_reask` / `add_contest`) and recomputes `safe`; this is the escape hatch.
        """
        self.exemplars[index].safe = value

    def add_reask(self, index: int) -> None:
        """The environment votes against this exemplar (a re-ask after an absorb)."""
        self.exemplars[index].reask_count += 1

    def add_contest(self, index: int) -> None:
        """The model votes for this exemplar (it contests a demotion -- it stands by its answer)."""
        self.exemplars[index].contest_count += 1

    def repair_action(self, index: int, new_action: str, new_outcome: str = "pending") -> None:
        """Overwrite a DEMOTED exemplar's action with the corrected answer (repair, not abandon).

        The re-ask demotion path makes a wrong exemplar ``unsafe`` but leaves its wrong
        ``action`` in place; the learn-time dedup guard (``_find_dup``) then blocks
        appending the correct answer the model returned on the re-ask, so the slot
        escalates-with-hint on the WRONG action forever (abandonment). ``repair_action``
        overwrites the action with the corrected one -- the hint becomes correct, and
        IF the slot re-promotes it will replay the right answer.

        Evidence handling (the probation model -- NOT a clean reset):
          - ``reask_count`` is KEPT. The re-ask was against the old (wrong) action;
            after overwrite it is no longer a vote against the new action, but it is
            carried as PROBATION: "this slot has been wrong before, so the corrected
            answer must earn its way back." So ``safe`` stays False after repair --
            the slot does NOT replay yet (not the harsh immediate-trust of a cosine
            cache, which is the carelessness this boundary exists to avoid).
          - ``contest_count`` is RESET to 0. Any prior contests were about the old
            action and are moot. (In practice an unsafe dup always has contest_count 0
            anyway -- a prior ``contest()`` would have restored ``safe`` -- but reset
            is the honest invariant.)

        Re-promotion is NOT done here. It happens later, on the CONFIRM path: when the
        model stands by the corrected action on a subsequent escalation, that is a
        ``contest`` (``add_contest`` + ``recompute_safe``): the first contest cancels
        the probation re-ask -> ``safe`` flips to True -> the slot is replayable again.
        The environment keeps the final say: a further re-ask (reask=2) out-votes the
        single contest -> the demotion stands. That is the existing ``contest()``
        boundary rule, unchanged.

        The store stays append-only in COUNT: no exemplar is added or removed -- only
        this one exemplar's mutable fields (``action``/``outcome``/``contest_count``)
        change, the same fields ``add_reask``/``add_contest``/``set_safe`` mutate. Call
        only on an UNSAFE exemplar (a safe one is a proven-good answer -- never overwrite).
        """
        ex = self.exemplars[index]
        ex.action = new_action
        ex.outcome = new_outcome
        ex.contest_count = 0
        # reask_count is intentionally kept (probation); safe is intentionally left
        # as-is (already False on the unsafe dup). recompute_safe is NOT called here --
        # the confirm path (a later contest) does it when the model stands by the fix.

    def index_of(self, ex: Exemplar) -> int | None:
        """Identity lookup of an exemplar the gate recalled (``e is ex``).

        Uses identity, not dataclass equality: a near-duplicate exemplar would compare
        equal by fields, so ``==`` is unsafe here. ``top_k`` returns the actual objects
        from ``self.exemplars``, so identity holds.
        """
        for i, e in enumerate(self.exemplars):
            if e is ex:
                return i
        return None

    def __len__(self) -> int:
        return len(self.exemplars)

    def vectors(self) -> np.ndarray:
        """All exemplar vectors as an (N, D) array (empty -> (0, 0))."""
        if not self.exemplars:
            return np.zeros((0, 0), dtype=float)
        return np.array([e.vec for e in self.exemplars], dtype=float)

    def top_k(self, q: np.ndarray, k: int = 5,
              canon_only: bool = False) -> list[tuple[Exemplar, float]]:
        """The k nearest exemplars to unit query q, by cosine similarity.

        ``canon_only`` excludes ephemeral CANDIDATES: the gate's replay
        decision reads canon alone (a candidate is an unconfirmed claim --
        replaying it would be acting on silence). Recall that should SEE
        candidates uses ``recall_merged`` instead."""
        pool = ([e for e in self.exemplars if not e.ephemeral]
                if canon_only else self.exemplars)
        if not pool:
            return []
        V = np.array([e.vec for e in pool], dtype=float)
        qn = _unit(q)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        sims = Vn @ qn
        k = min(k, len(pool))
        idx = np.argsort(-sims)[:k]
        return [(pool[i], float(sims[i])) for i in idx]

    def save(self, path: str | Path) -> None:  # noqa: F811
        if self._cb is not None:
            self._save_couchbase()
            return
        self._save_json(path)

    def _save_json(self, path: str | Path) -> None:
        """Atomic save: write to a temp file in the same directory, then
        ``os.replace`` it over the target. A crash mid-write leaves the old
        field intact (the rename is the atomic publish step) instead of a
        truncated JSON no future load can read.

        Ephemeral rows ride the same file as plain exemplars carrying their
        ``eph_born`` in extra -- load() routes them back into the ephemeral
        tier, so the JSON mirror has the SAME semantics as the couchbase
        tier split. (The fallback must be a faithful tier mirror, or a
        cluster-loss flip would promote silence by forgetting candidates.)"""
        p = Path(path)
        if self._quarantine_failed:
            raise RuntimeError(
                f"refusing to save over unreadable field {p} "
                f"(quarantine failed at load); move the file aside first")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp",
                                   dir=str(p.parent))
        try:
            with os.fdopen(fd, "w") as f:
                # ONE list, two tiers by flag: ephemeral rows carry their
                # eph_born in extra (the load path routes them back); the
                # flag itself rides the record only when True (legacy rows
                # stay byte-identical)
                recs = []
                for e in self.exemplars:
                    rec = _ex_to_wire(e, stamp=False)
                    if e.ephemeral:
                        rec["ephemeral"] = True
                    recs.append(rec)
                json.dump(recs, f, indent=2)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- the cold tier (archive) -------------------------------------------
    # Cortex's episodic-buffer idea: raw turns that have been distilled into a
    # compact note leave the HOT retrieval surface but are never destroyed --
    # they move to a cold archive. JSON backend: an append-only JSONL next to
    # the field (<field>.archive.jsonl). Couchbase backend: the sibling
    # collection `membrane.archive` (same bucket/scope). The hot field stays
    # small (kNN latency, save() cost, context noise all scale with it); the
    # archive stays queryable (membrane: query the field can search it) and is
    # the source of truth for anything the compactor retired.

    def archive_exemplars(self, exs: list["Exemplar"], field_path: str | Path) -> int:
        """Move exemplars to the cold tier. Idempotent per content key: a
        repeated archive of the same situation+action is a no-op write.

        Returns the number newly written. Never raises into the chat path? It
        CAN -- callers hold mut_lock and treat archive failure like save()
        failure (the exemplars are still in RAM; nothing is lost)."""
        if not exs:
            return 0
        if self._cb is not None:
            return self._archive_couchbase(exs)
        return self._archive_jsonl(exs, field_path)

    def _archive_jsonl(self, exs: list["Exemplar"], field_path: str | Path) -> int:
        p = Path(str(field_path) + ".archive.jsonl")
        if self._quarantine_failed:
            raise RuntimeError(
                f"refusing to archive next to unreadable field {p}")
        p.parent.mkdir(parents=True, exist_ok=True)
        seen = self._archive_seen_jsonl(p)
        n = 0
        with open(p, "a") as f:
            for ex in exs:
                k = _ex_key(ex)
                if k in seen:
                    continue
                rec = _ex_to_wire(ex, stamp=False)
                rec["_archived_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                rec["_key"] = k
                f.write(json.dumps(rec) + "\n")
                seen.add(k)
                n += 1
        return n

    @staticmethod
    def _archive_seen_jsonl(p: Path) -> set[str]:
        """Content keys already in the archive (file missing -> empty set)."""
        if not p.exists():
            return set()
        keys = set()
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    keys.add(json.loads(line).get("_key", ""))
                except json.JSONDecodeError:
                    continue  # a torn tail line from a crash is not fatal
        return keys

    def _archive_couchbase(self, exs: list["Exemplar"]) -> int:
        coll = self._cb_collection(mode="archive")
        n = 0
        for ex in exs:
            k = _ex_key(ex)
            try:
                if coll.get(k):
                    continue  # idempotent: already in the cold tier
            except Exception:
                pass
            rec = _ex_to_wire(ex, stamp=False)
            rec["_archived_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            coll.upsert(k, rec)
            n += 1
        return n

    def archived(self, field_path: str | Path) -> list[dict]:
        """The cold tier, newest last. JSON backend reads the JSONL; couchbase
        backend runs a SQL++ scan. Empty list when nothing was ever archived.
        Degrades to [] on any read failure (the archive is an affordance, not
        a dependency of the hot path)."""
        try:
            if self._cb is not None:
                from couchbase.options import QueryOptions
                qr = self._cb_cluster.query(
                    "SELECT t.* "
                    f"FROM `{self._cb_bucket}`.`{_CB_SCOPE}`.`{_CB_COLL}_archive` t",
                    QueryOptions(timeout=timedelta(seconds=30)))
                return [dict(r) for r in qr]
            p = Path(str(field_path) + ".archive.jsonl")
            if not p.exists():
                return []
            out = []
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            return out
        except Exception:
            return []

    # ---- Couchbase backend -------------------------------------------------
    # Same call sites (mem.store.save(path)) -- when the store was hydrated
    # from Couchbase, `path` is ignored and mutations are per-doc upserts.

    def _cb_collection(self, mode: str = "live") -> Any:
        """The `membrane.exemplars` (mode="live") or `membrane.exemplars_archive`
        (mode="archive") collection, created on first touch.

        Bucket may or may not exist; the couchbase SDK cannot create buckets,
        so a missing bucket falls back to the management REST API (same
        credentials). Connection settings come from the env:
        COUCHBASE_CONN_STRING / COUCHBASE_USER / COUCHBASE_PASSWORD /
        COUCHBASE_BUCKET (cortex-experiment's convention).
        """
        if mode == "live" and self._cb is not None:
            return self._cb
        if mode == "archive" and self._cb_archive is not None:
            return self._cb_archive
        import urllib.request
        import base64
        from couchbase.cluster import Cluster
        from couchbase.auth import PasswordAuthenticator
        from couchbase.options import ClusterOptions
        conn = os.environ.get("COUCHBASE_CONN_STRING", "couchbase://localhost")
        user = os.environ.get("COUCHBASE_USER", "Administrator")
        pw = os.environ.get("COUCHBASE_PASSWORD", "password")
        bucket = os.environ.get("COUCHBASE_BUCKET", "membrane")
        self._cb_bucket = bucket
        self._cb_cluster = Cluster(conn, ClusterOptions(
            PasswordAuthenticator(user, pw)))
        self._cb_cluster.wait_until_ready(timedelta(seconds=10))
        # the bucket must exist; the SDK can't create buckets -> management
        # REST API on :8091 with the same credentials. When creation is
        # refused (cluster RAM quota fully allocated by other buckets, e.g.
        # cortex-core/cortex-ephemeral), REUSE an existing bucket: the
        # membrane scope + exemplars collection namespace everything, so
        # sharing a bucket is collision-free by design. Preference order:
        # an existing cortex bucket (cortex-core first), else the first.
        mgr = self._cb_cluster.buckets()
        try:
            mgr.get_bucket(bucket)
        except Exception:
            import urllib.request
            import base64
            host = conn.replace("couchbase://", "").split("/")[0].split(":")[0]
            req = urllib.request.Request(
                f"http://{host}:8091/pools/default/buckets", method="POST")
            req.add_header("Authorization", "Basic " + base64.b64encode(
                f"{user}:{pw}".encode()).decode())
            req.add_header("Content-Type",
                           "application/x-www-form-urlencoded")
            from urllib.parse import urlencode
            # try a sane quota, then the minimum: a cluster whose RAM quota
            # is already fully allocated (other buckets) rejects big asks.
            created = False
            last_err: Exception | None = None
            for mb in (256, 128, 100):
                try:
                    urllib.request.urlopen(req, data=urlencode({
                        "name": bucket, "ramQuotaMB": mb,
                        "bucketType": "couchbase"}).encode(), timeout=10)
                    created = True
                    break
                except Exception as e:
                    last_err = e
            if not created:
                reuse = self._existing_bucket(host, user, pw)
                if not reuse:
                    raise RuntimeError(
                        f"bucket '{bucket}' missing and cannot be created "
                        f"({last_err}); no existing bucket to reuse either. "
                        f"Free cluster RAM quota in the Couchbase UI, or set "
                        f"COUCHBASE_BUCKET to an existing bucket.") from last_err
                print(f"[membrane] bucket '{bucket}' not creatable "
                      f"({last_err}); sharing existing bucket '{reuse}' "
                      f"(the membrane scope/collection namespaces it "
                      f"-- collision-free)", file=__import__("sys").stderr)
                bucket = reuse
            self._cb_bucket = bucket
            mgr.get_bucket(bucket)  # proves the bucket we will use exists
        colls = self._cb_cluster.bucket(bucket).collections()
        created = self._cb_ensure_infra(bucket, colls)
        coll_name = _CB_COLL if mode == "live" else _CB_COLL + "_archive"
        handle = (self._cb_cluster.bucket(bucket)
                  .scope(_CB_SCOPE).collection(coll_name))
        if created:
            print(f"[membrane] bootstrapped couchbase infra in bucket "
                  f"'{bucket}': {created}", file=__import__("sys").stderr)
        if mode == "live":
            self._cb = handle
        else:
            self._cb_archive = handle
        return handle

    def _list_buckets(self, host: str, user: str, pw: str) -> list[dict]:
        """The cluster's buckets (name + bucketType) via the management REST
        API -- the SDK cannot create buckets, and existence checks need the
        type too (an ephemeral tier wants an EPHEMERAL-type bucket). Empty
        list when the API is unreachable (offline cluster, bad creds)."""
        import base64
        import urllib.request
        try:
            req = urllib.request.Request(
                f"http://{host}:8091/pools/default/buckets")
            req.add_header("Authorization", "Basic " + base64.b64encode(
                f"{user}:{pw}".encode()).decode())
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except Exception:
            return []

    def _resolve_eph_bucket(self) -> str:
        """Which bucket holds the ephemeral tier. Resolution order:
        1. explicit COUCHBASE_EPHEMERAL_BUCKET (must exist on the cluster)
        2. "<canon>-ephemeral" (the cortex-core/cortex-ephemeral convention)
        3. any existing bucket of type 'ephemeral'
        4. CREATE "<canon>-ephemeral" (management REST; the SDK cannot) --
           ephemeral bucketType: the server TTLs docs, nothing persists.
        "" when nothing works -> callers fall back to the sibling collection
        inside the canon bucket (today's semantics, still correct)."""
        conn = os.environ.get("COUCHBASE_CONN_STRING", "couchbase://localhost")
        user = os.environ.get("COUCHBASE_USER", "Administrator")
        pw = os.environ.get("COUCHBASE_PASSWORD", "password")
        host = conn.replace("couchbase://", "").split("/")[0].split(":")[0]
        canon = self._cb_bucket or os.environ.get("COUCHBASE_BUCKET", "membrane")
        buckets = {b.get("name", ""): b.get("bucketType", "membase")
                   for b in self._list_buckets(host, user, pw)}
        explicit = os.environ.get("COUCHBASE_EPHEMERAL_BUCKET", "").strip()
        if explicit:
            if explicit in buckets:
                return explicit
            print(f"[membrane] COUCHBASE_EPHEMERAL_BUCKET={explicit!r} not on "
                  f"the cluster; falling back to discovery", file=sys.stderr)
        want = canon + EPH_BUCKET_SUFFIX
        if buckets.get(want) == "ephemeral":
            return want
        for n, btype in buckets.items():
            if btype == "ephemeral" and n:
                return n
        # create it: same ladder as the canon bucket (quota may be tight);
        # no replicas -- candidates are disposable by design
        import base64
        import urllib.request
        from urllib.parse import urlencode
        last_err: Exception | None = None
        for mb in (256, 128, 100):
            try:
                req = urllib.request.Request(
                    f"http://{host}:8091/pools/default/buckets", method="POST")
                req.add_header("Authorization", "Basic " + base64.b64encode(
                    f"{user}:{pw}".encode()).decode())
                req.add_header("Content-Type",
                               "application/x-www-form-urlencoded")
                urllib.request.urlopen(req, data=urlencode({
                    "name": want, "ramQuotaMB": mb,
                    "bucketType": "ephemeral", "replicaNumber": 0,
                }).encode(), timeout=10)
                print(f"[membrane] created ephemeral bucket '{want}' "
                      f"({mb} MB, bucketType=ephemeral)", file=sys.stderr)
                return want
            except Exception as e:
                last_err = e
        print(f"[membrane] no ephemeral bucket available ({last_err}); "
              f"the ephemeral tier rides the canon bucket's {EPH_COLL} "
              f"collection", file=sys.stderr)
        return ""

    def _ensure_eph_collection(self) -> Any:
        """The ephemeral tier's home: the `membrane.exemplars_ephemeral`
        collection in the DEDICATED ephemeral bucket when one resolves
        (`_resolve_eph_bucket`), else the sibling collection in the canon
        bucket (same fresh-bucket contract: scope + collection + PRIMARY
        INDEX, idempotent). Doc TTL does the GC server-side either way."""
        if self._cb_eph is not None:
            return self._cb_eph
        bucket = self._resolve_eph_bucket()
        if bucket:
            self._cb_eph_bucket = bucket
        else:
            bucket = self._cb_bucket or os.environ.get(
                "COUCHBASE_BUCKET", "membrane")
        colls = self._cb_cluster.bucket(bucket).collections()
        created: list[str] = []
        try:
            colls.create_scope(_CB_SCOPE)
            created.append(f"scope {_CB_SCOPE}")
        except Exception:
            pass                    # scope exists (or refused): use it
        try:
            colls.create_collection(_CB_SCOPE, EPH_COLL)
            created.append(f"collection {EPH_COLL}")
        except Exception:
            pass
        try:
            mgr = self._cb_cluster.query_indexes()
            existing = {i.name for i in mgr.get_all_indexes(
                bucket, scope_name=_CB_SCOPE, collection_name=EPH_COLL)}
            if "def_primary" not in existing and "#primary" not in existing:
                mgr.create_primary_index(
                    bucket, scope_name=_CB_SCOPE, collection_name=EPH_COLL)
                created.append(f"primary index {EPH_COLL}")
        except Exception:
            pass                    # old SDK: KV paths still work
        handle = (self._cb_cluster.bucket(bucket)
                  .scope(_CB_SCOPE).collection(EPH_COLL))
        if created:
            where = (f"bucket '{bucket}' (dedicated ephemeral)"
                     if self._cb_eph_bucket else f"bucket '{bucket}'")
            print(f"[membrane] bootstrapped ephemeral tier in {where}: "
                  f"{created}", file=__import__("sys").stderr)
        self._cb_eph = handle
        return handle

    def _cb_ensure_infra(self, bucket: str, colls: Any) -> list[str]:
        """THE FRESH-BUCKET CONTRACT: installing onto an EMPTY Couchbase
        bucket creates everything membrane needs, idempotently -- the
        `membrane` scope, the `exemplars` (hot) and `exemplars_archive`
        (cold-tier) collections, and a PRIMARY INDEX on each (SQL++ needs
        one: without it the veil queries fail with 'No index available' on
        a bucket that has never had an index). Nothing is deferred/built
        async -- fields here are tiny at boot, and a synchronous CREATE
        keeps first-write safe. Already-existing objects are a no-op.
        Returns a human list of what was actually created (for the boot
        notice), so a warm bucket stays silent."""
        created: list[str] = []
        try:
            colls.create_scope(_CB_SCOPE)
            created.append(f"scope {_CB_SCOPE}")
        except Exception:
            pass                        # scope exists (or refused): use it
        for coll_name in (_CB_COLL, _CB_COLL + "_archive"):
            try:
                colls.create_collection(_CB_SCOPE, coll_name)
                created.append(f"collection {coll_name}")
            except Exception:
                pass
        try:
            mgr = self._cb_cluster.query_indexes()
            for coll_name in (_CB_COLL, _CB_COLL + "_archive"):
                try:
                    existing = {i.name for i in mgr.get_all_indexes(
                        bucket, scope_name=_CB_SCOPE,
                        collection_name=coll_name)}
                    if "def_primary" not in existing \
                            and "#primary" not in existing:
                        mgr.create_primary_index(
                            bucket, scope_name=_CB_SCOPE,
                            collection_name=coll_name,
                            ignore_if_exists=True)
                        created.append(f"primary index {coll_name}")
                except Exception:
                    pass                # exists / verifier lag: not fatal
        except Exception:
            pass                        # no index manager (old SDK): kv paths still work
        return created

    @classmethod
    def load_couchbase(cls, field_path: str | Path = "") -> "ExemplarStore":
        """Hydrate from Couchbase; on any failure the caller falls back to JSON.

        THE VEIL (scope split): a shared bucket holds every membrane's patch.
        This membrane hydrates --
          1. its OWN patch   (origin == my origin tag)
          2. the COMMONS     (scope == "global", any origin)
          3. ...and, poking through the veil: foreign PROJECT exemplars whose
             situation is near-identical to one I already hold (cosine >=
             VEIL_POKE_SIM on the situation TEXT-hash is too weak, so the poke
             is done AFTER hydration, in `veil_poke` -- Couchbase has no
             vectors to cosine here). Foreign memories are scenery: loaded so
             recall/gate can SEE them, but writes never mutate or delete them,
             and arbitration never makes a foreign exemplar master of mine.
        On this pass the raw foreign set is loaded (cheap: no model call); a
        caller with an embedder narrows it via `prune_veil`.
        """
        st = cls()
        st.origin = _origin_tag(field_path) if field_path else st.origin
        coll = st._cb_collection()
        from couchbase.options import QueryOptions
        my = st.origin
        qr = st._cb_cluster.query(
            "SELECT meta().id AS id, t.* "
            f"FROM `{st._cb_bucket}`.`{_CB_SCOPE}`.`{_CB_COLL}` t "
            "WHERE (t.`origin` = $my OR t.`scope` = 'global') "
            "AND meta().id NOT LIKE 'sit:%'",
            # the arbitration index docs (sit:<hash> -> {origin, ex_key})
            # carry an origin but no exemplar fields -- bookkeeping, not
            # memories; the key-prefix filter excludes them.
            QueryOptions(timeout=timedelta(seconds=30),
                         named_parameters={"my": my}))
        keys = []
        for row in qr:
            ex = _rec_from_wire({k: v for k, v in row.items() if k != "id"})
            st.append(ex)
            keys.append(row["id"])
        st._cb_keys = set(keys)
        # delta-save bookkeeping: everything hydrated IS current on the
        # backend -- the first save touches only rows born/mutated since.
        st._written = set(keys)
        # own patch situations are already arbitrated: don't replay the CAS
        # pass for them on the first save after boot
        st._cb_situ = {_situ_key(e.situation) for e in st.exemplars
                       if (e.origin or my) == my}
        # the ephemeral tier: candidates survive across boots server-side
        # (TTL does the GC); hydrate them back flagged into the ONE list.
        try:
            eph_coll = st._ensure_eph_collection()
            eph_bucket = st._cb_eph_bucket or st._cb_bucket
            now = time.time()
            eph_q = st._cb_cluster.query(
                "SELECT meta().id AS id, t.* "
                f"FROM `{eph_bucket}`.`{_CB_SCOPE}`.`{EPH_COLL}` t",
                QueryOptions(timeout=timedelta(seconds=30)))
            n_eph = 0
            eph_keys: list[str] = []
            for row in eph_q:
                # the id is bookkeeping (delta sets + retire-on-sight), not
                # exemplar data: strip it before decode (canon loop's shape)
                eph_id = row.get("id") or ""
                ex = _rec_from_wire(
                    {k: v for k, v in row.items() if k != "id"})
                if not (ex.situation or ex.action):
                    continue
                if "eph_born" not in (ex.extra or {}):
                    ex.extra["eph_born"] = float(now)
                if now - st._eph_born(ex) >= EPH_TTL_S:
                    # server TTL lag: retire on sight (not loaded at all)
                    try:
                        eph_coll.remove(eph_id)
                    except Exception:
                        pass
                    continue
                ex.ephemeral = True
                st.append(ex)
                eph_keys.append(eph_id)
                n_eph += 1
            # delta-save seeding (canon's `_cb_keys`/`_written` contract):
            # everything hydrated IS current on the backend -- without this,
            # a candidate forgotten/expired BEFORE the first save is invisible
            # to the stale reconcile and its doc lingers server-side.
            st._cb_keys_eph = set(eph_keys)
            st._written_eph = set(eph_keys)
            if n_eph:
                print(f"[membrane] ephemeral tier: {n_eph} candidate(s) "
                      f"hydrated (TTL {EPH_TTL_S:.0f}s)",
                      file=__import__("sys").stderr)
        except Exception as e:
            print(f"[membrane] ephemeral tier hydrate skipped ({e})",
                  file=__import__("sys").stderr)
        # foreign project exemplars ride along ONLY through the boot poke
        # (`veil_poke`, bounded) and arbitration adoption -- never wholesale.
        return st

    def veil_poke(self, embedder, limit: int = 256) -> int:
        """The veil's poke-through, run once at boot (embedder is proven by
        then). A BOUNDED sample of foreign PROJECT exemplars is fetched; a
        foreign memory is adopted into RAM only when its situation is
        near-identical to one I already hold (cosine >= VEIL_POKE_SIM): the
        same question, possibly a different earned answer. Adopted rows keep
        their foreign origin (read-only scenery; my save never touches them).
        Returns the number adopted."""
        if self._cb is None or embedder is None or not self.exemplars:
            return 0
        from couchbase.options import QueryOptions
        my = self.origin
        try:
            qr = self._cb_cluster.query(
                "SELECT t.* "
                f"FROM `{self._cb_bucket}`.`{_CB_SCOPE}`.`{_CB_COLL}` t "
                "WHERE t.`origin` IS NOT MISSING AND t.`origin` != '' "
                "AND t.`origin` != $my "
                "AND (t.`scope` IS MISSING OR t.`scope` != 'global') "
                "LIMIT $lim",
                QueryOptions(timeout=timedelta(seconds=30),
                             named_parameters={"my": my, "lim": limit}))
            docs = list(qr)
        except Exception as e:
            print(f"[membrane] veil poke skipped ({e})",
                  file=__import__("sys").stderr)
            return 0
        if not docs:
            return 0
        V = self.vectors()
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12) \
            if len(self.exemplars) else None
        texts = [d.get("situation", "") for d in docs]
        try:
            E = np.asarray(embedder.embed(texts), dtype=float)
        except Exception as e:
            print(f"[membrane] veil poke embed failed ({e})",
                  file=__import__("sys").stderr)
            return 0
        En = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-12)
        n = 0
        for doc, ev in zip(docs, En):
            if not doc.get("situation"):
                continue
            best = float((Vn @ ev).max()) if len(Vn) else 0.0
            if best >= VEIL_POKE_SIM:
                if self._adopt_foreign_doc(doc):
                    n += 1
        if n:
            print(f"[membrane] veil: {n} foreign memories poked through "
                  f"(cosine >= {VEIL_POKE_SIM})", file=__import__("sys").stderr)
        return n

    # ---- the shared substrate: CAS save with CAN-style arbitration ----------
    # Two membranes sharing a bucket used to erase each other (full reconcile
    # deleted every key not in the local list). Now:
    #   - every content key is NAMESPACED by origin (_ex_key hashes origin too)
    #   - deletes only ever touch OWN keys (foreign patches are read-only)
    #   - when two origins earned DIFFERENT actions for the SAME situation,
    #     arbitration runs: the version more salient to THIS membrane (cosine
    #     to its own live field, tie-break evidence then recency) masters the
    #     shared situation; the loser DEFERS into `alternates` on the winner's
    #     doc (CAN non-destructive queuing: losers wait, nothing is destroyed).
    # Contention is detected via Couchbase CAS on the situation-index doc
    # (`sit:<situ_key>` -> {origin, ex_key}); a CasMismatchError means another
    # membrane grabbed the channel -> re-read, re-arbitrate, retry.

    def _salience(self, ex: "Exemplar") -> float:
        """How well an exemplar's situation resonates with THIS membrane's
        lived field (its best cosine against my OTHER exemplars -- the
        contender never scores against itself, or a lone cell would claim
        salience 1.0 and win every arbitration). Salience is the CAN priority
        axis: semantic and LOCAL -- each membrane arbitrates by its own focus,
        so no global arbiter exists."""
        import numpy as _np
        v = _np.asarray(ex.vec, dtype=float)
        n = float(_np.linalg.norm(v))
        if n < 1e-12 or len(self.exemplars) < 2:
            return 0.0
        v = v / n
        best = 0.0
        for mine in self.exemplars:
            if mine is ex:
                continue           # never self-resonance
            mv = _np.asarray(mine.vec, dtype=float)
            mn = float(_np.linalg.norm(mv))
            if mn < 1e-12:
                continue
            s = float(v @ (mv / mn))
            if s > best:
                best = s
        return best

    @staticmethod
    def _evidence(ex: "Exemplar") -> int:
        """The tie-break mass: votes + uses + re-ask/contest traffic."""
        return (len(ex.votes or []) + len(ex.uses or [])
                + (ex.reask_count or 0) + (ex.contest_count or 0))

    @staticmethod
    def _existing_bucket(host: str, user: str, pw: str) -> str | None:
        """An existing bucket to share when 'membrane' can't be created:
        an existing cortex bucket first (cortex-core -- the sibling project's
        home), else alphabetical-first. None when the cluster has none."""
        import base64
        import urllib.request
        try:
            req = urllib.request.Request(
                f"http://{host}:8091/pools/default/buckets")
            req.add_header("Authorization", "Basic " + base64.b64encode(
                f"{user}:{pw}".encode()).decode())
            with urllib.request.urlopen(req, timeout=5) as r:
                names = [b.get("name", "") for b in json.loads(r.read())]
        except Exception:
            return None
        names = [n for n in names if n]
        for pref in ("cortex-core", "cortex"):
            for n in names:
                if pref in n.lower():
                    return n
        return names[0] if names else None

    def _cb_doc(self, ex: Exemplar) -> dict:
        """The wire record for an exemplar (asdict + provenance bookkeeping)."""
        rec = _ex_to_wire(ex, stamp=True)
        rec["_origin"] = ex.origin or self.origin
        rec["_situ"] = _situ_key(ex.situation)
        return rec

    # ---- the mem:stats doc (the sketch that replaces boot hydration) --------
    # Lab E4's finding, carried honestly: a JL projection FLOORS the
    # observable eff_rank near the sketch dim, so the spectrum here is a
    # TREND estimator (does the field get richer or just bigger between
    # saves), not an exact meter. safe_frac / counts are EXACT O(1) tallies
    # -- the part every --status render reads per write.

    def stats_sketch(self) -> dict:
        """The mem:stats payload: exact counts + the sketched spectrum."""
        n = len(self.exemplars)
        n_eph = sum(1 for e in self.exemplars if e.ephemeral)
        safe = sum(1 for e in self.exemplars if e.safe)
        safe_frac = (safe / n) if n else 0.0
        out = {"n": n, "n_durable": n - n_eph, "n_ephemeral": n_eph,
               "safe": safe, "safe_frac": safe_frac}
        # trend: JL-project then SVD (one fixed matrix per dim -- stable
        # across saves so the trend is comparable over time)
        st = self.exemplars
        D = len(st[0].vec) if st else 0
        if n >= 2 and D:
            d = min(_SKETCH_D, D)
            rng = np.random.default_rng(_JL_SEED)
            R = rng.standard_normal((d, D)) / np.sqrt(d)
            V = np.array([e.vec for e in st], dtype=float)
            Y = (V - V.mean(axis=0)) @ R.T
            _, S, _ = np.linalg.svd(Y, full_matrices=False)
            var = (S ** 2) / n
            p = var / (var.sum() + 1e-12)
            er = float(np.exp(-(p * np.log(p + 1e-12)).sum()))
            fe = safe_frac * er - 0.25 * (1.0 - safe_frac) * er
            out.update({"eff_rank_sketch": er, "free_energy_sketch": float(fe),
                        "sketch_d": d, "dim": D})
        return out

    def _save_stats_doc(self) -> None:
        """Persist the sketch as `mem:stats` in the live collection (a KV
        upsert with the delta saves -- cheaper than any recompute-on-boot)."""
        try:
            self._cb.upsert("mem:stats", self.stats_sketch())
        except Exception:
            pass                    # stats are an affordance, never a dependency

    def _save_couchbase(self) -> None:
        """DELTA save (was: full reconcile). Only rows NOT already confirmed
        current on the backend get upserted; only OWN vanished keys get
        removed. Arbitration still runs for NEW situations only (the
        _cb_situ set keeps that bookkeeping). A full reconcile is pay-per-
        exemplar on every learn; with the delta set the steady-state save is
        O(changed), which is what makes the ephemeral write path affordable
        (every capture is a save).

        Origin stamping happens HERE, at the wire (unchanged): in-RAM
        exemplars written by this membrane carry origin="" (born here); the
        first save pins that blank to the store's origin tag."""
        coll = self._cb
        live: set[str] = set()
        pending: dict[str, Exemplar] = {}   # situ_key -> my contender
        dirty: list[tuple[str, Exemplar]] = []
        for ex in self.exemplars:
            if not ex.origin:
                if self.origin:
                    ex.origin = self.origin        # pin at first touch
                else:
                    continue    # origin-less store: JSON-only semantics
            k = _ex_key(ex)
            live.add(k)
            if ex.origin != self.origin:
                continue                    # foreign patch: never written by us
            if ex.ephemeral:
                continue                    # candidates: the ephemeral pass
                # owns them (their key namespace differs from canon's)
            if k not in self._written:
                dirty.append((k, ex))
            situ = _situ_key(ex.situation)
            if situ not in self._cb_situ:   # new situation: contend once
                self._cb_situ.add(situ)
                pending.setdefault(situ, ex)
        for k, ex in dirty:
            coll.upsert(k, self._cb_doc(ex))
        self._written |= {k for k, _ in dirty}
        if pending:
            self._arbitrate(pending)
        ephemeral_keys = {_eph_key(ex) for ex in self.exemplars if ex.ephemeral}
        if getattr(self, "_cb_keys", None):
            stale = (self._cb_keys - live) | ephemeral_keys
            # a promoted candidate's ephemeral doc must GO; canon's stale
            # keys go with it (the forget/compactor paths)
            for k in stale:
                try:
                    coll.remove(k)
                except Exception:
                    pass
        self._cb_keys = live
        # the ephemeral pass + stats doc need the cluster handle (fakes and
        # bare KV stubs in tests set only _cb): skip on anything less
        if getattr(self, "_cb_cluster", None) is not None:
            self._save_couchbase_ephemeral()
            self._save_stats_doc()

    def _save_couchbase_ephemeral(self) -> None:
        """The ephemeral tier on couchbase: same origin namespace, the
        SIBLING `exemplars_ephemeral` collection, server-side doc TTL (the
        clock's GC -- no janitor sweep to run). A PROMOTED candidate's
        ephemeral doc is removed here (its content moved to canon); TTL
        handles the silent rest."""
        coll = self._ensure_eph_collection()
        upsert = getattr(coll, "upsert_with_expiry", None)
        live: set[str] = set()
        for ex in self.exemplars:
            if not ex.ephemeral:
                continue
            if not ex.origin:
                if self.origin:
                    ex.origin = self.origin
                else:
                    continue
            k = _eph_key(ex)
            live.add(k)
            if k not in self._written_eph:
                rec = self._cb_doc(ex)
                rec["eph_born"] = self._eph_born(ex)
                rec["eph_ttl"] = EPH_TTL_S
                try:
                    if upsert is not None:
                        from datetime import timedelta as _td
                        upsert(k, rec, expiry=_td(seconds=int(EPH_TTL_S)))
                    else:  # old SDK: no expiry API -- plain upsert; the JSON
                           # mirror's eph_born still retires it at load
                        coll.upsert(k, rec)
                except Exception:
                    continue
                self._written_eph.add(k)
        stale = getattr(self, "_cb_keys_eph", None) or set()
        stale -= live
        # a promoted row's ephemeral key vanished from `live` because the
        # flag flipped -- its doc is garbage now; remove it
        for k in stale:
            try:
                coll.remove(k)
            except Exception:
                pass
        self._cb_keys_eph = live

    def _arbitrate(self, pending: dict[str, Exemplar]) -> None:
        """One arbitration pass over my newly-written docs.

        Each contested situation has a channel doc `sit:<situ_key>` =
        {ex_key, origin, salience, alternates}. Challenger protocol (CAN,
        retold): read the channel with CAS -> compare salience (mine vs. the
        incumbent's own claim) -> winner masters the channel, loser's action
        is APPENDED to the channel's alternates (non-destructive queuing).
        A CAS mismatch -> re-read, re-arbitrate, retry; the loser is never
        deleted, only deferred."""
        coll = self._cb
        import couchbase.exceptions as _cbe
        CasMismatch = getattr(_cbe, "CasMismatchError", None) \
            or _cbe.CASMismatchException
        DocExists = _cbe.DocumentExistsException
        for situ, mine in pending.items():
            k = f"sit:{situ}"
            sal_m = self._salience(mine)
            for _ in range(ARBITRATION_ROUNDS):
                try:
                    got = coll.get(k)
                    doc, cas = got.content_as[dict], got.cas
                except Exception:
                    # no incumbent: claim the channel with MY salience claim
                    try:
                        coll.insert(k, {"ex_key": _ex_key(mine),
                                        "origin": self.origin,
                                        "salience": sal_m,
                                        "alternates": []})
                        break
                    except DocExists:
                        continue       # someone claimed first -> retry loop
                other = str(doc.get("origin") or "")
                if other == self.origin:
                    doc["salience"] = max(float(doc.get("salience") or 0.0),
                                          sal_m)   # keep our best claim fresh
                    try:
                        coll.upsert(k, doc, cas=cas)
                    except CasMismatch:
                        continue
                    break
                sal_t = float(doc.get("salience") or 0.0)
                # the master's action text: needed to store as my alternate
                try:
                    mdoc = coll.get(doc["ex_key"]).content_as[dict]
                except Exception:
                    mdoc = {}
                mine_wins = (sal_m > sal_t or
                             (sal_m == sal_t and
                              self._evidence(mine) >
                              self._evidence(self._rec_from_doc(mdoc))))
                alt_theirs = {"action": (mdoc.get("action")
                                         or doc.get("action") or ""),
                              "origin": other, "salience": sal_t,
                              "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
                alt_mine = {"action": mine.action, "origin": self.origin,
                            "salience": sal_m,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
                # merge BOTH sides' alternates on the channel (the shared
                # contention record): mine if I lost, theirs if I won --
                # either way every contender survives on the channel doc.
                channel = dict(doc)
                alts = [a for a in (channel.get("alternates") or [])
                        if a.get("action")]
                if mine_wins:
                    if alt_theirs["action"] and alt_theirs["action"] not in \
                            [a["action"] for a in alts]:
                        alts.append(alt_theirs)
                    channel = {"ex_key": _ex_key(mine),
                               "origin": self.origin, "salience": sal_m,
                               "alternates": alts[-ALTERNATES_MAX:]}
                    self._merge_alternates(mine, alts)
                    method = coll.upsert
                else:
                    if alt_mine["action"] and alt_mine["action"] not in \
                            [a["action"] for a in alts]:
                        alts.append(alt_mine)
                    channel["alternates"] = alts[-ALTERNATES_MAX:]
                    self._adopt_foreign_doc(mdoc)
                    method = coll.upsert
                try:
                    method(k, channel, cas=cas)
                except CasMismatch:
                    continue       # lost the channel -> re-read, re-arbitrate
                break

    @staticmethod
    def _rec_from_doc(doc: dict) -> "Exemplar | None":
        """An Exemplar from a raw couchbase doc (None when it isn't one).
        Schema-window aware: unknown keys survive in ``extra`` (a newer
        writer's fields are invisible here but never erased)."""
        if not doc:
            return None
        ex = _rec_from_wire(doc)
        return ex if ex.situation or ex.action else None

    def _merge_alternates(self, mine: Exemplar, alts: list) -> None:
        """Fold the channel's deferred contenders into MY local exemplar's
        alternates (recall can see them; nothing here writes the remote doc)."""
        try:
            seen = {a.get("action") for a in (mine.alternates or [])}
            for a in alts:
                if a.get("action") and a["action"] not in seen:
                    mine.alternates.append(a)
                    seen.add(a["action"])
            mine.alternates = mine.alternates[-ALTERNATES_MAX:]
        except Exception:
            pass

    def _adopt_foreign_doc(self, doc: dict) -> bool:
        """Pull a foreign master doc into my RAM field as read-only scenery
        (they keep ownership; my save never re-serves their key). True when
        adopted, False when it was already known or unusable."""
        ex = self._rec_from_doc(doc)
        if ex is None or not (ex.situation or "").strip() or not ex.vec:
            return False
        ex.origin = doc.get("_origin") or doc.get("origin") or "foreign"
        ex.scope = "project"
        if any(e.situation == ex.situation and e.origin == ex.origin
               for e in self.exemplars):
            return False          # the poke-through already carries it
        self.append(ex)
        return True

    @staticmethod
    def _probe_local_couchbase() -> bool:
        """Cheap one-shot probe: does a local Couchbase answer at the default
        (or env-provided) endpoint with default (or env-provided) creds?

        Plain HTTP GET /pools with Basic auth -- no SDK import, ~1s cap.
        True only when the server responds 200; auth failures, DNS failures,
        timeouts, and every other refusal are all 'no' (the JSON field, as
        always, the safe fallback). Env vars override defaults, matching
        load_couchbase's convention."""
        import base64
        import urllib.request
        conn = os.environ.get("COUCHBASE_CONN_STRING", "couchbase://localhost")
        user = os.environ.get("COUCHBASE_USER", "Administrator")
        pw = os.environ.get("COUCHBASE_PASSWORD", "password")
        host = conn.replace("couchbase://", "").replace("http://", "") \
                   .split("/")[0].split(":")[0]
        try:
            req = urllib.request.Request(f"http://{host}:8091/pools")
            req.add_header("Authorization", "Basic " + base64.b64encode(
                f"{user}:{pw}".encode()).decode())
            with urllib.request.urlopen(req, timeout=2.0) as r:
                return r.status == 200
        except Exception:
            return False

    @classmethod
    def load(cls, path: str | Path) -> "ExemplarStore":
        """Load the field; hydrates from Couchbase when either:

        1. MEMBRANE_STORE=couchbase is set (explicit opt-in, as before), or
        2. a LOCAL Couchbase answers at the default conn string with the
           default (or env-provided) credentials -- auto-detection, so a
           machine with a running cluster shares by default and a plain
           laptop falls back to the JSON file silently.

        Couchbase is OPTIONAL by design: missing SDK or unreachable server
        -> a how-to-fix notice, then a transparent fallback to the JSON
        field -- degradation, not failure (membrane keeps running
        autonomously either way). Set MEMBRANE_STORE=json to disable
        detection and force the file."""
        want_cb = os.environ.get("MEMBRANE_STORE", "").strip().lower()
        if want_cb in ("couchbase", "cb"):
            try:
                return cls.load_couchbase(path)
            except ImportError as e:
                print(f"[membrane] MEMBRANE_STORE=couchbase but the SDK is "
                      f"missing ({e}). Fix: pip install couchbase. Falling "
                      f"back to the JSON field {path} -- everything works, "
                      f"nothing is shared until the SDK is installed.",
                      file=__import__("sys").stderr)
            except Exception as e:
                conn = os.environ.get("COUCHBASE_CONN_STRING",
                                      "couchbase://localhost")
                print(f"[membrane] Couchbase unreachable ({e}). Fix: check the "
                      f"server is up and COUCHBASE_CONN_STRING / COUCHBASE_USER "
                      f"/ COUCHBASE_PASSWORD / COUCHBASE_BUCKET are set "
                      f"(current conn: {conn}). Falling back to the JSON field "
                      f"{path} -- membrane keeps running autonomously and will "
                      f"retry Couchbase at next boot.",
                      file=__import__("sys").stderr)
        elif want_cb not in ("json", "file", "none", "0", "false"):
            # EXPERIMENTS-ISOLATION GUARD: auto-detect is for a membrane's OWN
            # field only. A temp-dir / experiments-field path (the lab: the
            # dream-loop et al. build Membrane(field_path=tempfile...)) must
            # NEVER join the shared substrate -- observed failure: a lab field
            # auto-joined the live cluster and, bucket-creation refused (RAM
            # quota), silently reused a FOREIGN bucket (cortex-core). Lab data
            # contaminating production memories is the worst failure mode this
            # file can have; the explicit opt-in (MEMBRANE_STORE=couchbase)
            # remains available for a lab that truly wants sharing.
            if _is_lab_field_path(path):
                p = Path(path)
                print(f"[membrane] lab field path ({p.parent}) -> JSON backend, "
                      f"shared substrate NOT joined (isolation guard).",
                      file=__import__("sys").stderr)
            elif cls._probe_local_couchbase():
                try:
                    return cls.load_couchbase(path)
                except Exception as e:
                    print(f"[membrane] local Couchbase detected but "
                        f"unusable ({e}); JSON field {path} it is.",
                        file=__import__("sys").stderr)
        store = cls()
        store.origin = _origin_tag(path)
        p = Path(path)
        if p.exists():
            text = p.read_text()
            try:
                parsed = json.loads(text) if text.strip() else []
            except json.JSONDecodeError as e:
                # corrupt field -- QUARANTINE, don't discard. The file is moved
                # aside with the exact bytes preserved so nothing is destroyed;
                # the proxy starts empty and the next save() writes cleanly.
                # (Before: a warning + an empty start, which silently orphaned
                # every memory in the file against the append-only promise.)
                import sys
                import time
                quar = p.with_name(p.name + f".corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
                try:
                    os.replace(p, quar)
                    print(f"[membrane] warning: field {p} unreadable ({e}); "
                          f"quarantined to {quar}; starting empty", file=sys.stderr)
                except OSError as e2:
                    print(f"[membrane] warning: field {p} unreadable ({e}); "
                          f"quarantine failed ({e2}); proceeding WITHOUT saving "
                          f"over it", file=sys.stderr)
                    # mark the store read-only-ish: save() refuses to clobber
                    store._quarantine_failed = True
                recs = []
            else:
                # envelope-aware: a raw list is v1; a dict envelope carries
                # its own WIRE_VERSION + records list (future-proof: one
                # version reads its predecessor's file).
                if isinstance(parsed, dict):
                    env_v = int(parsed.get("version") or 1)
                    recs = parsed.get("exemplars") or []
                    if env_v < WIRE_VERSION:
                        print(f"[membrane] field file v{env_v} envelope -> "
                              f"migrating to v{WIRE_VERSION} on next save",
                              file=__import__("sys").stderr)
                else:
                    recs = parsed
            for rec in recs:
                ex = _rec_from_wire(rec)
                if (ex.extra or {}).get("eph_born") is not None \
                        or rec.get("ephemeral"):
                    # the ephemeral tier rides the JSON mirror (same file):
                    # restore the candidate flag -- the fallback must be a
                    # faithful tier mirror
                    ex.ephemeral = True
                store.append(ex)
        return store