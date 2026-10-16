"""Open intentions in the field: the third channel of the generative-agents
loop (observations -> reflections -> PLANS). Watches hold ATTENTION ("tell me
when X happens"); intentions hold COMMITMENT ("we are doing X until Y or T").
The dream loop steers by them (experiments/dream_loop.py D5); recall surfaces
them so a fresh session inherits not just what happened but what we are DOING.

The two zombie-goal guards (an intention without them poisons recall):
  * EXPIRY -- every intention carries an absolute deadline (default 14 days,
    the parse of a window spec like '2w'/'10d' or an explicit date). Expired
    -> it goes dormant in the listing with 'expired' (never deleted: the
    audit trail keeps the fulfilled/expired history on the model's lane).
  * FULFILLMENT -- closure is EXPLICIT (`membrane: fulfill <spec>`), reality's
    signature, like a contest in the two-authority boundary. An intention is
    never auto-closed by similarity alone -- cos(x, intent) >= 0.9 marks it
    'maybe-done' in the listing (a hint to fulfill), never a conclusion.

CAP -- at most MAX_INTENTS live (a new one evicts the oldest, tie lowest
confirm hit): commitments are scarce by construction; a board that grows
forever is a backlog, not a plan.

State persists in <field>.intents.json next to the field (same convention as
the watches board). No model call anywhere.
"""

from __future__ import annotations

from .entities import CLASS_GOAL

import json
from pathlib import Path
import re
import time
from dataclasses import dataclass, field as dc_field

import numpy as np

MAX_INTENTS = 8           # live intentions at once (new evicts oldest)
DEFAULT_TTL_S = 14 * 86400.0
CONFIRM_SIM = 0.90        # cos at which the listing flags 'maybe-done'
STEER_SIM = 0.60          # the dream-loop steering line (dream_loop INTENT_MIN)

_TTL_RE = re.compile(r"^\s*(\d+)\s*([dwmh])\s*$", re.I)
_TTL_UNITS = {"h": 3600.0, "d": 86400.0, "w": 7 * 86400.0, "m": 30 * 86400.0}


def parse_ttl(spec: str) -> float | None:
    """'2w' / '10d' / '48h' / '1m' -> seconds; None when not a window spec."""
    m = _TTL_RE.match(spec or "")
    if not m:
        return None
    return float(m.group(1)) * _TTL_UNITS[m.group(2).lower()]


@dataclass
class Intention:
    iid: int
    text: str                # the commitment, one sentence
    probe: list              # unit vector (the steering coordinate)
    created: float = 0.0
    expires: float = 0.0     # wall-clock deadline (0 = never expires)
    status: str = "open"     # open | fulfilled | expired
    fulfilled: float = 0.0   # when reality signed
    hits: int = 0            # near turns seen (context, not auto-close)
    eid: str = ""            # the goal-backed view's goal eid ('' = json)
    last_hit: float = 0.0

    def describe(self) -> str:
        """The commitment's text -- what a goal entity's describe() gives (the
        dreamer's vote calls it on either; a fresh seat's board holds these, and
        every vote crashed: AttributeError, 2026-10-12)."""
        return self.text

    def to_json(self) -> dict:
        return {"iid": self.iid, "text": self.text, "probe": self.probe,
                "created": self.created, "expires": self.expires,
                "status": self.status, "fulfilled": self.fulfilled,
                "hits": self.hits, "last_hit": self.last_hit}

    @classmethod
    def from_json(cls, r: dict) -> "Intention":
        return cls(iid=int(r["iid"]), text=str(r.get("text", "")),
                   probe=[float(x) for x in r.get("probe", [])],
                   created=float(r.get("created", 0.0)),
                   expires=float(r.get("expires", 0.0)),
                   status=str(r.get("status", "open")),
                   fulfilled=float(r.get("fulfilled", 0.0)),
                   hits=int(r.get("hits", 0)),
                   last_hit=float(r.get("last_hit", 0.0)))


class IntentBoard:
    """All intentions for one field: intend/fulfill/drop, the steering query
    the dream loop (and recall) uses, and the listing."""

    def __init__(self, path, embedder=None) -> None:
        self.path = path
        self.embedder = embedder
        self.intents: dict[int, Intention] = {}
        self._next_iid = 1
        # THE GOAL-BACKED SELECTION (the operator's design: 'the fact that
        # a goal became active makes it an intent'): when an entity store
        # is attached (the proxy boot does), the board becomes a VIEW over
        # the shared Goal entities -- open_intents/nearest delegate to the
        # seat's active set (top-K by urgency, hysteresis, no TTL). The
        # JSON intents stay the bare-library fallback (no entity layer).
        self.selection: "GoalSelection | None" = None

    def attach_entity_store(self, est) -> None:
        """The proxy boot's wiring: the board's steering reads the shared
        Goal layer through the seat's selection. THE MIGRATION: legacy
        JSON intents import once as Goal entities (shared, long-lived) +
        priority pins (the operator's own plants keep their hand)."""
        sel_path = None
        try:
            sel_path = Path(self.path).parent / "goal-selection.json"
        except Exception:
            pass
        self.selection = GoalSelection(est, path=sel_path)
        try:
            self._migrate_json_goals(est)
        except Exception:
            pass

    def _migrate_json_goals(self, est) -> None:
        """Legacy intents.json entries -> Goal entities + pins. One-shot:
        migrated entries are marked status 'migrated' in the JSON (the
        data stays; the file survives as the record)."""
        import hashlib as _hashlib
        moved = 0
        from .entities import QuantumEntity, CLASS_GOAL, MEMBRANE_NS
        for i in list(self.intents.values()):
            if i.status == "migrated":
                continue
            text = (i.text or "").strip()
            if not text:
                continue
            slug = _hashlib.sha256(text.lower().encode()).hexdigest()[:12]
            eid = f"{MEMBRANE_NS}goal:{slug}"
            props = {"name": text[:120], "summary": text[:600],
                     "status": ("fulfilled" if i.status == "fulfilled"
                                else "open"),
                     "born": i.created or time.time()}
            if i.fulfilled:
                props["fulfilled_ts"] = i.fulfilled
            ent = QuantumEntity(eid=eid, cls=CLASS_GOAL, properties=props)
            if est.docs.get(eid) is None:
                ent.position = list(i.probe or [])
                est.upsert(ent)
            if i.status == "open":
                self.selection.pin(eid, 0.5)
            i.status = "migrated"
            moved += 1
        if moved:
            self.save()

    # ---- persistence ----
    def save(self) -> None:
        try:
            self.path.write_text(json.dumps({
                "next_iid": self._next_iid,
                "intents": [i.to_json() for i in self.intents.values()],
            }))
        except Exception:
            pass                              # persistence never breaks chat

    @classmethod
    def load(cls, path, embedder=None) -> "IntentBoard":
        b = cls(path, embedder)
        try:
            r = json.loads(path.read_text())
            for rec in r.get("intents", []):
                i = Intention.from_json(rec)
                b.intents[i.iid] = i
            b._next_iid = int(r.get("next_iid", len(b.intents) + 1))
        except Exception:
            pass
        return b

    # ---- embed helper ----
    def _embed(self, text: str, vec=None) -> list:
        if vec is None:
            if self.embedder is None:
                raise RuntimeError("no embedder")
            v = np.asarray(self.embedder.embed([text])[0], dtype=float)
        else:
            v = np.asarray(vec, dtype=float)
        n = float(np.linalg.norm(v))
        if n <= 1e-12:
            raise RuntimeError("zero vector: the intention is semantically empty")
        return (v / n).tolist()

    # ---- life cycle ----
    def intend(self, text: str, ttl_spec: str = "", vec=None,
               now: float | None = None) -> tuple[Intention, str | None]:
        """Plant (or re-arm) an intention. Returns (intent, evicted_text)."""
        text = (text or "").strip()
        if not text:
            raise RuntimeError("empty intention")
        now = time.time() if now is None else now
        probe = self._embed(text, vec)
        ttl = parse_ttl(ttl_spec) if ttl_spec else DEFAULT_TTL_S
        expires = (now + ttl) if ttl else 0.0
        evicted = None
        # re-arm on the same text: new deadline, keep the history
        for i in self.intents.values():
            if i.text.strip().lower() == text.lower():
                i.created = now
                i.expires = expires
                if i.status in ("fulfilled", "expired"):
                    i.status = "open"
                    i.fulfilled = 0.0
                self.save()
                return i, None
        if len(self.open_intents(now)) >= MAX_INTENTS:
            victim = min((i for i in self.intents.values()
                          if i.status == "open"),
                         key=lambda i: (i.hits, i.created))
            if victim is not None:
                evicted = victim.text
                self.intents.pop(victim.iid, None)
        it = Intention(iid=self._next_iid, text=text, probe=probe,
                       created=now, expires=expires)
        self._next_iid += 1
        self.intents[it.iid] = it
        self.save()
        return it, evicted

    def _resolve(self, spec: str, now: float) -> list[Intention]:
        """'#N' / index / case-insensitive substring -> matching intents."""
        spec = (spec or "").strip().lower().lstrip("#")
        if not spec:
            return []
        if spec.isdigit():
            i = self.intents.get(int(spec))
            return [i] if i else []
        return [i for i in self.intents.values() if spec in i.text.lower()]

    def fulfill(self, spec: str, now: float | None = None) -> tuple[int, str]:
        """Reality signs: close by '#N' or substring. Returns (n, note)."""
        now = time.time() if now is None else now
        hits = [i for i in self._resolve(spec, now) if i.status == "open"]
        for i in hits:
            i.status = "fulfilled"
            i.fulfilled = now
        if hits:
            self.save()
        return len(hits), (hits[0].text if hits else "nothing open matched")

    def drop(self, spec: str) -> tuple[int, str]:
        """Withdraw without a fulfillment claim (a mistake or a retraction)."""
        hits = self._resolve(spec, time.time())
        for i in hits:
            self.intents.pop(i.iid, None)
        if hits:
            self.save()
        return len(hits), (hits[0].text if hits else "nothing matched")

    def open_intents(self, now: float | None = None) -> list[Intention]:
        """Open intents. GOAL-BACKED when an entity store is attached: the
        seat's active set over the shared Goal entities (no TTL -- goals
        do not expire; short-livedness is the selection dynamics). The
        JSON intents are the bare-library fallback."""
        if self.selection is not None:
            return self.selection.open_intents(now or time.time())
        now = time.time() if now is None else now
        for i in self.intents.values():
            if i.status == "open" and i.expires and now >= i.expires:
                i.status = "expired"
        return [i for i in self.intents.values() if i.status == "open"]

    # ---- the steering query (the dream loop's hook; no embed call) ----
    def nearest(self, vec, now: float | None = None
                ) -> tuple[Intention | None, float]:
        """(best open intent, cos) for a unit vector -- the steering
        coordinate. GOAL-BACKED when attached: over the seat's ACTIVE SET
        only (the selection steers, not every goal in the layer)."""
        if self.selection is not None:
            return self.selection.nearest(vec, now or time.time())
        if vec is None or len(vec) == 0:
            return None, 0.0
        v = np.asarray(vec, dtype=float)
        n = float(np.linalg.norm(v))
        if n <= 1e-12:
            return None, 0.0
        v = v / n
        best, bs = None, -2.0
        for i in self.open_intents(now):
            if not i.probe:
                continue
            c = float(v @ np.asarray(i.probe, dtype=float))
            if c > bs:
                best, bs = i, c
        return best, bs

    def on_turn(self, ex) -> Intention | None:
        """Per-learn hook: count a near turn (context only; never auto-closes).
        Returns the intent if a hit happened (for an audit note), else None."""
        vec = getattr(ex, "vec", None)
        if not vec:
            return None
        i, c = self.nearest(vec)
        if i is None or c < CONFIRM_SIM:
            return None
        i.hits += 1
        i.last_hit = time.time()
        if i.hits % 5 == 0:               # persist the counter occasionally
            self.save()
        return i

    # ---- listing ----
    def report(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        open_ = self.open_intents(now)
        # THE GOAL-BACKED VIEW'S REPORT (the 'board silently cleared'
        # exhibit's root, 2026-10-06): the report read ONLY the legacy
        # json intents -- the goal tier (the real active set) rendered
        # 'none held' THROUGH every plant. The goal-backed board owns
        # the render now: its open set + the goal entities' landings.
        if self.selection is not None:
            goals = self.selection.refresh(now or time.time())
            if not goals:
                return ("Membrane intentions: none held. Plant one with "
                        "`membrane: intend that <goal>` (optionally "
                        "'... for 2w'); fulfill with `membrane: fulfill #N`.")
            lines = [f"Membrane intentions ({len(goals)} active on this "
                     "seat's set):"]
            for g in goals:
                props = g.properties or {}
                lands = props.get("landings") or []
                near = (f", {len(lands)} landing(s)"
                        if lands else "")
                lines.append(f"- {g.eid.rsplit(':', 1)[-1]} "
                             f"{(props.get('name') or '')[:100]!r}"
                             f"{near}")
            return "\n".join(lines)
        closed = [i for i in self.intents.values() if i.status != "open"]
        if not self.intents:
            return ("Membrane intentions: none held. Plant one with "
                    "`membrane: intend that <goal>` (optionally '... for 2w'); "
                    "fulfill with `membrane: fulfill #N`. "
                    "Dreams are steered toward open intentions; expiry keeps "
                    "zombie goals out.")
        lines = [f"Membrane intentions ({len(open_)}/{MAX_INTENTS} open"
                 + (f", {len(closed)} closed" if closed else "") + "):"]
        for i in sorted(self.intents.values(), key=lambda x: x.iid):
            if i.status == "open":
                left = (f", expires in {(i.expires - now) / 86400:.1f}d"
                        if i.expires else ", no expiry")
                near = (f", {i.hits} near turns"
                        + (f" (maybe-done? last cos hit at "
                           f"{time.strftime('%m-%d %H:%M', time.localtime(i.last_hit))})"
                           if i.hits >= 2 else "")
                        if i.last_hit else "")
                lines.append(f"- #{i.iid} {i.text!r} "
                             f"(since {time.strftime('%m-%d', time.localtime(i.created))}"
                             f"{left}{near})")
            else:
                how = "fulfilled" if i.status == "fulfilled" else "expired"
                when = (i.fulfilled if i.status == "fulfilled" else i.expires)
                lines.append(f"- #{i.iid} [{how}] {i.text!r}"
                             + (f" at {time.strftime('%m-%d %H:%M', time.localtime(when))}"
                                if when else ""))
        return "\n".join(lines)

# ---- the goal-backed board (the operator's design: 'the fact that a goal
# became active makes it an intent') -----------------------------------------
# NO TTL: goals are long-lived shared entities (status open/fulfilled/
# dropped, data stays); an INTENT is not an object -- it is a goal that is
# CURRENTLY SELECTED. The active set = the top-K open goals by urgency,
# re-scored on the idle cadence with hysteresis (a challenger replaces an
# incumbent only at +margin, so the set does not flap). Selection is
# seat-local (suppression is a seat-local set); fulfillment is shared
# (a project commitment done is done for every seat).

GOAL_SET_CAP = 3              # active intents per seat
GOAL_SWAP_MARGIN = 0.10       # the challenger's edge to replace an incumbent
_GOAL_WEIGHTS = (0.5, 0.3, 0.2)   # present-touch / recency / hit-rate
_GOAL_DECAY_S = 14 * 86400.0      # recency's half-ish window


class GoalSelection:
    """The seat's active-intent set over shared Goal entities. The board's
    open_intents()/nearest() delegate here when an entity store is
    attached (the goals ARE the intents; the Intention objects below are
    the view shape the consumers keep)."""

    def __init__(self, est, path=None) -> None:
        self.est = est
        self.suppressed: set[str] = set()   # seat-local: eids this seat dropped
        self.pinned: dict[str, float] = {}  # eid -> priority bonus (explicit intend)
        self._active: list[str] = []        # the current selection (eids, ranked)
        # THE CHOICE SURVIVES A RESTART (2026-10-08, the operator: "why are
        # you resetting its goals with every restart?"): the pins and the
        # drops ride a file beside the board; closed goals fall out
        self.path = Path(path) if path else None
        self._load()

    def _is_open(self, eid: str) -> bool:
        e = (self.est.docs or {}).get(eid) if self.est is not None else None
        return e is not None and (e.properties or {}).get("status", "open") == "open"

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text() or "{}")
        except (OSError, ValueError):
            return
        for eid, bonus in (d.get("pinned") or {}).items():
            if self._is_open(eid):
                try:
                    self.pinned[eid] = float(bonus)
                except (TypeError, ValueError):
                    pass
        self.suppressed |= {e for e in (d.get("suppressed") or []) if self._is_open(e)}

    def save(self) -> None:
        if self.path is None:
            return
        try:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({
                "pinned": {e: b for e, b in self.pinned.items() if self._is_open(e)},
                "suppressed": sorted(e for e in self.suppressed if self._is_open(e))}))
            tmp.replace(self.path)
        except OSError:
            pass

    def pin(self, eid: str, bonus: float) -> None:
        self.pinned[eid] = max(self.pinned.get(eid, 0.0), float(bonus))
        self.suppressed.discard(eid)
        self.save()

    def suppress(self, eid: str) -> None:
        self.suppressed.add(eid)
        self.pinned.pop(eid, None)
        self.save()

    # ---- the ranking ------------------------------------------------------
    def _open_goals(self) -> list:
        # no geometry requirement (#147): a goal whose position was lost
        # (the old save stripped it) is still held -- it ranks on recency,
        # hits and pins; only the present-touch term needs a position
        return [e for e in self.est.docs.values()
                if e.cls == CLASS_GOAL
                and (e.properties or {}).get("status", "open") == "open"]

    def _urgency(self, ent, now: float, now_vec) -> float:
        import numpy as _np
        w_present, w_recency, w_hits = _GOAL_WEIGHTS
        score = 0.0
        try:
            if now_vec is not None:
                gv = _np.asarray(ent.position, dtype=float)
                gn = _np.linalg.norm(gv)
                nv = _np.asarray(now_vec, dtype=float)
                nn = _np.linalg.norm(nv)
                if gn > 1e-12 and nn > 1e-12:
                    score += w_present * float(nv @ (gv / gn))
        except Exception:
            pass
        born = float((ent.properties or {}).get("born") or 0.0)
        if born:
            age = max(0.0, now - born)
            score += w_recency * (0.5 ** (age / _GOAL_DECAY_S))
        hits = int((ent.properties or {}).get("hits") or 0)
        score += w_hits * (hits / (hits + 5.0))     # saturating
        score += self.pinned.get(ent.eid, 0.0)
        return score

    def _top(self, k: int, now: float, now_vec=None) -> list:
        scored = []
        for e in self._open_goals():
            if e.eid in self.suppressed:
                continue
            scored.append((self._urgency(e, now, now_vec), e.eid, e))
        scored.sort(key=lambda x: -x[0])
        return scored[:max(1, int(k))]

    # ---- the hysteresis swap (the set does not flap) -----------------------
    def refresh(self, now: float, now_vec=None, k: int = GOAL_SET_CAP) -> list:
        """Re-score the open goals; swap incumbents only when a challenger
        beats the lowest incumbent by GOAL_SWAP_MARGIN. Returns the active
        goal entities (ranked)."""
        top = self._top(k, now, now_vec)
        if not top:
            self._active = []
            return []
        incumbent_ids = set(self._active)
        challenger_ids = [eid for _s, eid, _e in top]
        if not incumbent_ids:
            self._active = challenger_ids
            return [e for _s, eid, e in top]
        # the incumbents that still rank (keep them); the challengers that
        # beat a displaced incumbent's floor swap in
        kept = [eid for eid in challenger_ids if eid in incumbent_ids]
        floor = min((s for s, eid, _e in top if eid not in kept),
                    default=None)
        for s, eid, _e in top:
            if len(kept) >= k:
                break
            if eid in kept or eid in self._active:
                continue
            # a challenger enters only above the swap margin over the
            # weakest non-selected score (the hysteresis: no flapping)
            others = [s2 for s2, eid2, _ in top if eid2 not in kept
                      and eid2 != eid]
            if not others or s >= min(others) + 0.0:
                kept.append(eid)
        self._active = kept[:k]
        return [self.est.docs[eid] for eid in self._active
                if eid in self.est.docs]

    # ---- the view shape (what the consumers keep) --------------------------
    def open_intents(self, now: float, now_vec=None) -> list["Intention"]:
        """The active set as Intention objects (the consumers' shape): the
        goal's text/position ride the view; iid is synthetic (the goal's
        hash -- 'fulfill #N' resolves through the board's resolver)."""
        goals = self.refresh(now, now_vec)
        out = []
        for g in goals:
            props = g.properties or {}
            out.append(Intention(
                iid=int(str(abs(hash(g.eid)))[:6]) or 1,
                text=str(props.get("name") or props.get("summary")
                         or g.describe())[:200],
                probe=list(g.position or []),
                created=float(props.get("born") or 0.0),
                expires=0.0,                    # no TTL: goals do not expire
                status="open",
                hits=int(props.get("hits") or 0),
                eid=g.eid,                      # the goal's real id (the
                                                # wake derivation's address)
            ))
        return out

    def nearest(self, vec, now: float, now_vec=None
                ) -> tuple["Intention | None", float]:
        """(best active intent, cos) over the ACTIVE SET only (the seat's
        selection steers the dreamer, not every goal in the layer)."""
        import numpy as _np
        acts = self.open_intents(now, now_vec)
        if not acts or vec is None or len(vec) == 0:
            return None, 0.0
        v = _np.asarray(vec, dtype=float)
        n = float(_np.linalg.norm(v))
        if n <= 1e-12:
            return None, 0.0
        v = v / n
        best, bs = None, -2.0
        for i in acts:
            p = _np.asarray(i.probe or [], dtype=float)
            pn = _np.linalg.norm(p)
            if pn < 1e-12:
                continue
            s = float(v @ (p / pn))
            if s > bs:
                best, bs = i, s
        return best, max(0.0, bs)
