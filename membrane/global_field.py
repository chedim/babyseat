"""The global field: one small commons shared by every local membrane.

docs/global-field.md is the design; this module is slice 1 (operator-promotion
+ recall top-up). The principle: local fields stay the actor
("no central router"), the commons is a read-mostly layer that (a) receives
memories the OPERATOR explicitly promotes, and (b) tops up recall so another
project's lived knowledge is available here. The oracle stays the operator's
hand -- nothing enters the commons automatically in slice 1.

A "field-id" is a stable, filesystem-safe name for one project's field: the
hex digest of the resolved field path, first 12 chars -- derived, not
configured, so the same project is the same membrane everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from .field import Exemplar, ExemplarStore
import numpy as np


def field_id_for(field_path: str | Path) -> str:
    """Stable per-project membrane id: hash of the RESOLVED field path."""
    real = Path(field_path).resolve()
    return hashlib.sha256(str(real).encode()).hexdigest()[:12]


def _votes_of(ex: Exemplar) -> list[tuple[str, str]]:
    """The vote list as (fid, ts) pairs, tolerating legacy/malformed rows
    (a JSON round-trip may hand back lists instead of tuples; a hand-edited
    file may hold bare strings). Never raises into the read path."""
    out: list[tuple[str, str]] = []
    for v in ex.votes or []:
        if isinstance(v, (list, tuple)) and len(v) >= 1:
            out.append((str(v[0]), str(v[1]) if len(v) > 1 else ""))
        elif isinstance(v, str) and v:
            out.append((v, ""))
    return out


# ---- slice 2: the ledger (design doc "How global learns", path 2) ----------
# Each field appends safe operator-notes + compact-notes to its OWN disjoint
# shard ~/.membrane/ledger/<fid>.jsonl -- lock-free by construction (no two
# writers ever touch the same file). Consensus promotion = the same note
# (cosine >= LEDGER_NOTE_SIM) produced by >= LEDGER_VOTES_MIN DISTINCT fields
# merges into the commons automatically, with provenance. The operator's hand
# (slice 1) and the review queue (slice 3) stay the other write paths.

LEDGER_NOTE_SIM = 0.95     # 'the same note' for consensus counting
LEDGER_VOTES_MIN = 2       # distinct fields needed to auto-promote
LEDGER_MAX_SHARD = 512     # per-shard entries; the merger forgets the rest
USES_MAX = 64              # capped residency evidence per commons note
HOME_DRIFT_MIN_USES = 3    # uses a rival field needs to pull `home`
SCOPE_SUGGEST_MIN = 0.10   # scope_confidence above which a note is suggested
SCOPE_SUGGEST_MAX = 16     # pending suggestions cap (curated, not a firehose)


def _ledger_dir() -> Path:
    return Path(os.path.expanduser("~/.membrane/ledger"))


def _cos(a, b) -> float:
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return float(va @ vb / (na * nb))


class Ledger:
    """One field's ledger shard: the append-only outbox of notes it vouches for.

    Disjoint append-only files, lock-free by construction: each membrane only
    ever appends to ITS OWN shard, and appends are single small line writes
    (O_APPEND). The commons promotion is a deterministic merge at read/write
    time, whoever gets there first -- no election, no leader.
    """

    def __init__(self, fid: str, path: Path | None = None) -> None:
        self.fid = fid
        self.path = path or (_ledger_dir() / f"{fid}.jsonl")

    def append(self, situation: str, action: str, vec: list[float],
               outcome: str = "operator-note") -> bool:
        """Vouch for one note (a safe operator-note or compact-note). True =
        appended (dedup'd: the identical text is not re-appended)."""
        rec = {"ts": _now(), "situation": situation, "action": action,
               "outcome": outcome, "vec": [float(x) for x in vec]}
        if self.has(situation, action):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.trim()
        return True

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        try:
            for ln in self.path.read_text().splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue          # a torn line from a crash mid-append
        except OSError:
            return []
        return out

    def entries(self) -> list[dict]:
        """All NOTE-carrying entries (capture-junk never reaches the commons;
        the ledger only receives curated notes in the first place, so this is
        a second, defensive filter)."""
        out = []
        for r in self.read():
            sit = (r.get("situation") or "").strip().lower()
            if sit.startswith(("reasoning:", "plan:", "call ", "result ",
                               "tool ")):
                continue
            out.append(r)
        return out

    def has(self, situation: str, action: str) -> bool:
        return any(e.get("situation") == situation and
                   (e.get("action") or "") == action
                   for e in self.entries())

    def trim(self) -> None:
        """Capped shard: keep the newest LEDGER_MAX_SHARD entries. Disposal of
        superseded history is the compactor's concern for LOCAL fields; the
        ledger is a proposal mailbox, not an archive."""
        try:
            lines = [l for l in self.path.read_text().splitlines() if l.strip()]
        except OSError:
            return
        if len(lines) <= LEDGER_MAX_SHARD:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines[-LEDGER_MAX_SHARD:]) + "\n")
        os.replace(tmp, self.path)

    def forget_texts(self, situations: set[str]) -> int:
        """Remove ledger entries whose situation matches (the commons forget
        also retracts the local proposal, so a forgotten note does not
        re-promote from the shard)."""
        try:
            lines = [l for l in self.path.read_text().splitlines() if l.strip()]
        except OSError:
            return 0
        keep = []
        removed = 0
        for ln in lines:
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                keep.append(ln)
                continue
            if (r.get("situation") or "") in situations:
                removed += 1
            else:
                keep.append(ln)
        if removed:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("\n".join(keep) + "\n")
            os.replace(tmp, self.path)
        return removed


def _cluster_consensus(entries: list[dict]) -> list[list[dict]]:
    """Group ledger entries across shards into 'the same note' clusters --
    greedy by cosine at LEDGER_NOTE_SIM. Deterministic given the same file
    contents, so concurrent merges converge (whoever gets there first
    computes the same groups)."""
    groups: list[list[dict]] = []
    for e in entries:
        v = e.get("vec") or []
        best, best_sim = None, LEDGER_NOTE_SIM
        for g in groups:
            gv = g[0].get("vec") or []
            if len(gv) != len(v):
                continue
            s = _cos(v, gv)
            if s >= best_sim:
                best, best_sim = g, s
        if best is not None:
            best.append(e)
        else:
            groups.append([e])
    return groups


class GlobalField:
    """The curated commons: an ExemplarStore plus its JSON path.

    Owned by the operator (promote/forget are operator turns only; slice 2
    adds the consensus write path from ledger shards, which is still notes
    the operator/teacher signed locally); read by every local membrane as a
    recall top-up. Not thread-shared with the gate: the local field is the
    actor, this is read-mostly scaffolding.
    """

    def __init__(self, path: str | Path, fid: str) -> None:
        self.path = Path(path)
        self.fid = fid               # the LOCAL field's id (who reads/writes)
        self.store = ExemplarStore()
        self._mtime = 0.0            # last seen mtime (cross-instance freshness)
        self.ledger = Ledger(self.fid, self.path.parent / "ledger" / f"{self.fid}.jsonl")
        self._load()

    # ---- persistence (same atomic-save discipline as ExemplarStore) ---------
    def _load(self) -> None:
        p = self.path
        try:
            self._mtime = p.stat().st_mtime if p.exists() else 0.0
        except OSError:
            self._mtime = 0.0
        if not p.exists():
            return
        try:
            recs = json.loads(p.read_text() or "[]")
            for rec in recs:
                # strip keys this build does not know (a newer writer)
                known = {f for f in Exemplar.__dataclass_fields__}
                self.store.append(
                    Exemplar(**{k: v for k, v in rec.items() if k in known}))
        except (json.JSONDecodeError, TypeError) as e:
            import sys
            print(f"[global-field] warning: {p} unreadable ({e}); "
                  f"starting empty (file left untouched)", file=sys.stderr)
            self.store = ExemplarStore()

    def _refresh(self) -> None:
        """Re-read the commons when ANOTHER membrane changed it on disk.

        The commons is shared state with no lock: this membrane holds an
        in-memory copy for the read path, and a sibling field may promote/
        forget at any time. One stat per read (cheap) keeps the copy fresh;
        the file is capped-by-design (curated notes), so a changed-file
        re-read is rare and small. Our own save() bumps _mtime itself, so
        self-writes do not force a redundant reload. Also refreshes the
        LEDGER view of THIS field's shard (a _load()-built instance must see
        appends made before instantiation)."""
        try:
            m = self.path.stat().st_mtime
        except OSError:
            m = None
        if m is not None and m != self._mtime:
            self.store = ExemplarStore()
            self._load()

    def save(self) -> None:
        from .field import asdict  # local import keeps the module surface small
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".",
                                   suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump([asdict(e) for e in self.store.exemplars], f, indent=2,
                          ensure_ascii=False)
            os.replace(tmp, self.path)
            try:
                self._mtime = self.path.stat().st_mtime
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- the operator's hand ------------------------------------------------
    def promote(self, situation: str, action: str, vec: list[float],
                origin: str = "operator") -> Exemplar:
        """Operator promotes one note into the commons. Dedup-guarded (the
        same note is not appended twice); returns the exemplar. The promote is
        also VOUCHED for in this field's ledger shard -- the slice-2 consensus
        evidence trail (idempotent).

        Refreshes from disk FIRST: a sibling membrane may have promoted the
        same note since we loaded our copy (promote is operator-frequency, so
        one stat + rare reload is free -- without it our stale in-memory copy
        would dedup-miss and last-writer-wins would DESTROY the sibling's
        note instead of merging)."""
        self._refresh()
        v = list(vec)
        for ex in self.store.exemplars:
            if ex.situation == situation and ex.action == action:
                self._vouch(ex)
                return ex                      # already here (dedup, no grow)
        ex = Exemplar(situation=situation, action=action, outcome="pending",
                      vec=v, safe=True, scope="global", owner=self.fid,
                      home=self.fid, votes=[(self.fid, _now())])
        ex.outcome = origin
        self.store.append(ex)
        self.save()
        try:
            self.ledger.append(situation, action, v,
                               outcome=origin or "operator-note")
        except Exception:
            pass
        return ex

    def _vouch(self, ex: Exemplar) -> None:
        """Add this field's vote to an existing commons note (idempotent per
        fid); saves when the vote list grew. Also appends the voucher to this
        field's ledger shard (the evidence trail -- an idempotent dedup'd
        append). Malformed vote rows are tolerated (legacy files)."""
        fids = [f for f, _ in _votes_of(ex)]
        grew = self.fid not in fids
        if grew:
            ex.votes = [list(v) if isinstance(v, (list, tuple)) else [v, ""]
                        for v in (ex.votes or [])]
            ex.votes.append([self.fid, _now()])
            self.save()
        try:
            self.ledger.append(ex.situation, ex.action, list(ex.vec),
                               outcome="vouch")
        except Exception:
            pass

    def promote_existing(self, ex_ref: Exemplar) -> Exemplar:
        """Promote a live local exemplar (its situation/action/vec) into the
        commons, crediting the local field as origin. Idempotent on text."""
        ex = self.promote(ex_ref.situation, ex_ref.action, list(ex_ref.vec),
                          origin="promoted")
        self._vouch(ex)          # a second field promoting the same text = a vote
        return ex

    def forget(self, indexes) -> int:
        """Operator-given forget, scoped to the commons (never crosses back
        into a project's field). Also retracts the matching proposals from
        this field's ledger shard, so a forgotten note cannot silently
        re-promote from consensus evidence we still hold."""
        exs = self.store.exemplars
        situations = {exs[i].situation for i in indexes
                      if 0 <= int(i) < len(exs)}
        n = self.store.forget(indexes)
        if n:
            self.save()
            try:
                self.ledger.forget_texts(situations)
            except Exception:
                pass
        return n

    # ---- the read path ------------------------------------------------------
    def top_up(self, q, k: int = 2, sim_min: float = 0.55
               ) -> list[tuple[Exemplar, float]]:
        """Recall top-up: the k nearest COMMONS memories above sim_min for a
        local query. Read-only; never learns, never demotes. Refreshes from
        disk first (a sibling membrane may have promoted since we booted)."""
        self._refresh()
        return [(ex, s) for ex, s in self.store.top_k(q, k=k)
                if s >= sim_min and ex.safe]

    # ---- slice 2: consensus promotion (the merge) ---------------------------
    def merge_ledgers(self) -> dict:
        """The deterministic merge: group ALL shards' entries into same-note
        clusters; a cluster with >= LEDGER_VOTES_MIN DISTINCT field-ids
        auto-promotes into the commons (the exemplar already there gets the
        votes; a new one is appended with provenance). Idempotent + cheap to
        run on every promote/forget; returns stats for the audit/report.

        Voter-independence weighting (the design doc's slice-2 note): two
        near-clone fields voting twice is one opinion twice. The sharding
        already guarantees one vote per field per note (distinct fids are
        counted, not raw entries); full centroid-similarity weighting needs
        the peers layer (slice 4) to be more than ceremony, so votes here are
        counted per distinct field-id -- the honest cheap form.
        """
        self._refresh()
        led_dir = self.ledger.path.parent
        entries: list[tuple[str, dict]] = []      # (fid, entry)
        if led_dir.is_dir():
            for shard in sorted(led_dir.glob("*.jsonl")):
                fid = shard.stem
                l = Ledger(fid, shard)
                for e in l.entries():
                    entries.append((fid, e))
        if not entries:
            return {"shards": 0, "entries": 0, "clusters": 0,
                    "promoted": 0, "vouched": 0}
        # cluster with each entry tagged by its fid
        clusters: list[list[tuple[str, dict]]] = []
        for fid, e in entries:
            v = e.get("vec") or []
            best, best_sim = None, LEDGER_NOTE_SIM
            for g in clusters:
                gv = g[0][1].get("vec") or []
                if len(gv) != len(v):
                    continue
                s = _cos(v, gv)
                if s >= best_sim:
                    best, best_sim = g, s
            if best is not None:
                best.append((fid, e))
            else:
                clusters.append([(fid, e)])
        promoted = vouched = 0
        for g in clusters:
            fids = []
            for fid, _ in g:
                if fid not in fids:
                    fids.append(fid)
            if len(fids) < LEDGER_VOTES_MIN:
                continue
            fid0, e0 = g[0]
            situation = e0.get("situation") or ""
            action = e0.get("action") or ""
            vec = e0.get("vec") or []
            if not situation or not action:
                continue
            # already in the commons? vouch the cluster's votes onto it
            existing = None
            for ex in self.store.exemplars:
                if ex.situation == situation or _cos(ex.vec, vec) >= LEDGER_NOTE_SIM:
                    existing = ex
                    break
            if existing is not None:
                known = [f for f, _ in _votes_of(existing)]
                grew = False
                for fid in fids:
                    if fid not in known:
                        existing.votes = [list(v) if isinstance(v, (list, tuple))
                                          else [v, ""] for v in (existing.votes or [])]
                        existing.votes.append([fid, _now()])
                        grew = True
                if grew:
                    self.save()
                    vouched += 1
                continue
            ex = Exemplar(situation=situation, action=action,
                          outcome=e0.get("outcome") or "consensus",
                          vec=[float(x) for x in vec], safe=True,
                          scope="global", owner=fid0, home=fid0,
                          votes=[[fid, _now()] for fid in fids])
            self.store.append(ex)
            promoted += 1
        if promoted or vouched:
            self.save()
        return {"shards": len({fid for fid, _ in entries}),
                "entries": len(entries), "clusters": len(clusters),
                "promoted": promoted, "vouched": vouched}

    # ---- slice 3: the scope axis + residency drift --------------------------
    def _refresh_uses(self) -> None:
        """Record that THIS field just USED the commons (a top-up grounded a
        reply or surfaced in recall). uses is an append LOG (a fid may appear
        many times -- a track record, not a set), capped at USES_MAX. Cheap:
        mutates in memory; the save rides the rebalance that reads it."""
        changed = False
        for ex, _sim in getattr(self, "_last_top", []) or []:
            uses = [list(u) if isinstance(u, (list, tuple)) else [u, ""]
                    for u in (ex.uses or [])]
            uses.append([self.fid, _now()])
            ex.uses = uses[-USES_MAX:]
            changed = True
        if changed:
            self.save()
        self._last_top = []

    def note_use(self, used) -> None:
        """The opencode layer calls this after a top-up served notes: ``used``
        is the list of (Exemplar, sim) that reached a reply context. Buffered;
        flushed (with its save) by the next rebalance."""
        self._last_top = list(used or [])

    def rebalance(self) -> dict:
        """Residency drift: a commons note that keeps earning uses in field B
        but sits unused at birthplace A drifts ``home`` to B (homes follow use,
        not birth). Returns stats; called on the slow clock from opencode."""
        self._refresh()
        self._refresh_uses()
        moved = 0
        for ex in self.store.exemplars:
            if getattr(ex, "scope", "global") != "global":
                continue
            ucounts: dict[str, int] = {}
            for f, _ in _votes_of_list(getattr(ex, "uses", None)):
                ucounts[f] = ucounts.get(f, 0) + 1
            if not ucounts:
                continue
            use_rival = max(ucounts, key=lambda f: ucounts[f])
            n_uses = ucounts[use_rival]
            if use_rival == ex.home or ucounts.get(ex.home, 0) >= n_uses:
                continue                      # home leads (or ties), or no rival
            if n_uses - ucounts.get(ex.home, 0) < HOME_DRIFT_MIN_USES:
                continue                      # not enough evidence to move
            ex.home = use_rival
            moved += 1
        if moved:
            self.save()
        return {"moved": moved, "commons": len(self.store)}

    # ---- slice 3: scope suggestions (the review queue) ----------------------
    def suggest_scope(self, local_exemplars: list, suggestions_path: Path
                      ) -> list[dict]:
        """Scope-axis suggestions: two centroids (c_global from the commons,
        c_project from this field's clearly-local traffic) define an axis
        c_global - c_project; local exemplars near the global end get
        suggested for promotion. Appends PENDING lines to suggestions_path
        (a .scope-suggestions.jsonl beside the commons); the human ratifies
        with 'membrane: promote #' after listing them, or dismisses a slot
        with 'membrane: suggest drop <text-prefix>'. Never writes the commons
        itself -- suggestions park, the hand promotes. Returns the suggestions
        appended THIS call (empty when the field is too thin, the axis
        degenerates, or nothing clears the floor)."""
        exs = list(local_exemplars)
        if len(exs) < 6 or not self.store.exemplars:
            return []
        gV = np.array([np.asarray(e.vec, dtype=float)
                       for e in self.store.exemplars])
        gV = gV / (np.linalg.norm(gV, axis=1, keepdims=True) + 1e-12)
        c_global = gV.mean(axis=0)
        # the local end EXCLUDES capture-junk: machinery echoes are hyper-local
        real_idx = [(i, e) for i, e in enumerate(exs)
                    if not " ".join(e.situation.split()).lower().startswith(
                        ("reasoning:", "plan:", "call ", "result ", "tool "))]
        if len(real_idx) < 3:
            return []
        lV = np.array([np.asarray(e.vec, dtype=float) for _, e in real_idx])
        lV = lV / (np.linalg.norm(lV, axis=1, keepdims=True) + 1e-12)
        c_project = lV.mean(axis=0)
        axis = c_global - c_project
        na = float(np.linalg.norm(axis))
        if na <= 1e-12:
            return []
        axis = axis / na
        # already-resolved suggestion texts are never re-suggested
        done_texts = set()
        try:
            for ln_txt in suggestions_path.read_text().splitlines():
                try:
                    r = json.loads(ln_txt)
                except json.JSONDecodeError:
                    continue
                if r.get("status") in ("promoted", "dropped"):
                    done_texts.add((r.get("text") or "")[:80])
        except OSError:
            pass
        out = []
        try:
            suggestions_path.parent.mkdir(parents=True, exist_ok=True)
            with suggestions_path.open("a") as f:
                for i, ex in real_idx[-40:]:   # only recent traffic is interesting
                    v = np.asarray(ex.vec, dtype=float)
                    nv = float(np.linalg.norm(v))
                    if nv <= 1e-12:
                        continue
                    conf = float(v @ axis / nv)
                    if conf < SCOPE_SUGGEST_MIN:
                        continue
                    text = (ex.situation + " || " + (ex.action or ""))[:80]
                    if text in done_texts:
                        continue
                    rec = {"slot": int(time.time() * 1000), "status": "pending",
                           "ex_idx": i, "text": text,
                           "scope_confidence": round(conf, 3)}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out.append(rec)
        except OSError:
            return out
        return out


def _votes_of_list(rows) -> list[tuple[str, str]]:
    """The uses-rows tolerance twin of _votes_of: JSON round-trips hand lists
    back where tuples were; hand-edited files may hold bare strings."""
    out: list[tuple[str, str]] = []
    for v in rows or []:
        if isinstance(v, (list, tuple)) and len(v) >= 1:
            out.append((str(v[0]), str(v[1]) if len(v) > 1 else ""))
        elif isinstance(v, str) and v:
            out.append((v, ""))
    return out


# ---- slice 4: peers + the live ask ------------------------------------------
# ~/.membrane/peers.jsonl: one JSON line per LIVE membrane proxy
# {"fid", "port", "field", "ts"} -- self-registered at boot (O_APPEND), stale
# entries (heartbeat older than PEER_STALE_S) self-expire on every read. A
# failed ask to a peer degrades to "no help" -- escalation falls through to
# the model (the same covenant as judge-error -> ACCEPT; degrade toward the
# safe direction).

PEERS_MAX = 128            # capped registry (stale rows are garbage-collected)
PEER_STALE_S = 15 * 60     # a heartbeat older than this = a ghost
PEER_ASK_TIMEOUT = 20      # seconds; a slow peer is a dead peer
PEER_ASK_SIM_MIN = 0.60    # a peer answer must clear its own bar to count


def _peers_path(default_dir: Path | None = None) -> Path:
    env = os.environ.get("MEMBRANE_PEERS_PATH")
    if env:
        return Path(env)
    if default_dir is not None:
        return default_dir / "peers.jsonl"
    return Path(os.path.expanduser("~/.membrane/peers.jsonl"))


class Peers:
    """Self-registration + peer discovery for the live-ask layer.

    No election, no leader: every membrane appends its own heartbeat line and
    reads everyone else's. A peer's /membrane/ask endpoint answers from ITS
    field's local kNN (its lived knowledge, never the commons) -- this is the
    slice-4 routing layer "around ghosts": a live ask to a crashed peer fails
    and the caller falls through to the model.
    """

    def __init__(self, fid: str, path: Path | None = None) -> None:
        self.fid = fid
        self.path = path or _peers_path()
        self.info: dict = {}

    def register(self, port: int, field: str, peers_path: Path | None = None
                 ) -> Path:
        """Append this membrane's heartbeat (idempotent per append; reads
        garbage-collect stale rows of everyone)."""
        p = peers_path or self.path
        rec = {"fid": self.fid, "port": int(port),
               "field": str(getattr(self, "field_path", field) or field),
               "ts": time.time()}
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        return p

    def livelist(self, exclude_self: bool = True, peers_path: Path | None = None
                 ) -> list[dict]:
        """Live peers (heartbeat fresh), newest per fid. Stale rows are
        dropped in memory; the file is rewritten (compacted) lazily."""
        p = peers_path or self.path
        now = time.time()
        by_fid: dict[str, dict] = {}
        raw: list[str] = []
        try:
            raw = [l for l in p.read_text().splitlines() if l.strip()]
        except OSError:
            return []
        for ln in raw:
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if now - float(r.get("ts", 0)) > PEER_STALE_S:
                continue                      # ghost: expired
            fid = r.get("fid") or ""
            if not fid:
                continue
            prev = by_fid.get(fid)
            if prev is None or float(r.get("ts", 0)) >= float(prev.get("ts", 0)):
                by_fid[fid] = r
        if exclude_self:
            by_fid.pop(self.fid, None)
        live = list(by_fid.values())
        # lazy compaction: rewrite only when garbage piled up
        if len(raw) > PEERS_MAX and len(live) < len(raw) // 2:
            try:
                tmp = p.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(r) + "\n" for r in
                                       sorted(by_fid.values(),
                                              key=lambda r: r.get("fid", ""))))
                os.replace(tmp, p)
            except OSError:
                pass
        return live

    @staticmethod
    def ask(peer: dict, situation: str, sim_min: float = PEER_ASK_SIM_MIN,
            timeout: float = PEER_ASK_TIMEOUT) -> dict | None:
        """One live ask: GET http://127.0.0.1:<port>/membrane/ask?q=...
        Returns {"fid", "situation", "action", "sim", "ts"} or None (no match
        / peer down / any error -- ALL failures degrade to None, the caller
        falls through to the model). Read-only on the peer; timeout-guarded.
        ``timeout`` lets the live chat path use a tighter budget than the
        default (a hung peer must not stall a turn)."""
        import urllib.parse
        import urllib.request
        q = urllib.parse.quote(situation[:400])
        url = f"http://127.0.0.1:{int(peer.get('port', 0))}/membrane/ask?q={q}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read())
        except Exception:
            return None
        try:
            if float(data.get("sim", 0.0)) < sim_min:
                return None
        except (TypeError, ValueError):
            return None
        if not (data.get("situation") and data.get("action")):
            return None
        return data


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"