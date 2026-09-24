"""Spatial triggers in the field: the agent (or operator) plants WATCHES --
(probe vector, radius) pairs -- and membrane reports every NEW memory that
lands inside one. Delivered as marked context blocks on the next model turn;
never a mid-stream interrupt (the pull-only chat path is untouched).

The black-hole guards (the "if you ask for too much it's a black hole"
intuition, in two honest directions):
  * COLLAPSE -- gravity is measured by mass actually swallowed, not declared
    volume: a watch that matches more than COLLAPSE_RATE of the arrivals it
    sees auto-shrinks its radius (volume claims get earned, not declared).
  * DORMANCY -- two failure modes put a watch to sleep:
      > SWALLOW-ALL: hit rate over DORM_RATE on a trailing window -> dormant
        (it grabbed an importance it did not earn).
      > STARVATION: charge (hit-refilled, miss-eroded) hits 0 -> dormant
        (nobody's attention is OWED).
  * CAP -- at most MAX_WATCHES live; a new watch evicts the lowest-charge
    one (tie: oldest). Attention is a scarce resource by construction.

Events live in memory only (a proxy restart loses undelivered reports --
the same covenant as the thought-ask ledger). Watch definitions persist in
<field>.watches.json next to the field.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field as dc_field

import numpy as np

MAX_WATCHES = 7          # live spatial triggers at once (new evicts weakest)
                         # -- the operator's ratio: 3 tracks / 7 watches
                         # (tracks are the expensive lane, watches cheap)
RADIUS_DEFAULT = 0.70    # cosine radius of a new watch
RADIUS_MIN = 0.35        # collapse.floor: below this the watch removes itself
COLLAPSE_RATE = 0.15     # hit rate above which the radius shrinks
COLLAPSE_SHRINK = 0.92   # per-collapse radius factor
DORM_RATE = 0.30         # hit rate above which the watch sleeps (black hole)
DORMANT_S = 600.0        # sleep after a dormancy trip
CHARGE_START = 2.0
CHARGE_HIT = 0.5         # a matching arrival refills
CHARGE_MISS = 0.01       # every watched arrival costs a little
CHARGE_MAX = 3.0
WINDOW = 20              # trailing arrivals measured for the rates
MIN_WINDOW = 10          # rates only judged after this many arrivals
EVENT_MAX = 32           # report ring capacity


def _entity_key_of(ex) -> str:
    """An exemplar's entity keys -> the strongest single key for watch
    matching (first of its harvested tags; '' when untagged)."""
    tags = getattr(ex, "extra", None) or {}
    ents = tags.get("entities") if isinstance(tags, dict) else None
    if isinstance(ents, dict):
        for kind, values in ents.items():
            for v in values or []:
                return f"{kind}:{str(v).lower()}"
    return ""


@dataclass
class Watch:
    wid: int
    label: str               # the planted topic text
    probe: list              # unit vector
    radius: float = RADIUS_DEFAULT
    filter_key: str = ""     # entity key or kind prefix ('' = all memories)
    junk_sim: float | None = None   # per-watch junk cut (None = default)
    charge: float = CHARGE_START
    hits: int = 0
    seen: int = 0            # arrivals since last dormancy/reset
    window: list = dc_field(default_factory=list)   # trailing 0/1 outcomes
    dormant_until: float = 0.0
    last_hit: float = 0.0
    collapse_note: str = ""

    def to_json(self) -> dict:
        d = {"wid": self.wid, "label": self.label, "probe": self.probe,
             "radius": round(self.radius, 4),
             "filter_key": self.filter_key,
             "charge": round(self.charge, 3),
             "hits": self.hits, "seen": self.seen,
             "dormant_until": self.dormant_until,
             "last_hit": self.last_hit, "collapse_note": self.collapse_note}
        if self.junk_sim is not None:
            d["junk_sim"] = round(self.junk_sim, 3)
        return d

    @classmethod
    def from_json(cls, r: dict) -> "Watch":
        js = r.get("junk_sim")
        return cls(wid=int(r["wid"]), label=str(r.get("label", "")),
                   probe=[float(x) for x in r.get("probe", [])],
                   radius=float(r.get("radius", RADIUS_DEFAULT)),
                   filter_key=str(r.get("filter_key", "")),
                   junk_sim=(None if js is None
                             else max(0.0, min(1.0, float(js)))),
                   charge=float(r.get("charge", CHARGE_START)),
                   hits=int(r.get("hits", 0)), seen=int(r.get("seen", 0)),
                   dormant_until=float(r.get("dormant_until", 0.0)),
                   last_hit=float(r.get("last_hit", 0.0)),
                   collapse_note=str(r.get("collapse_note", "")))


class WatchBoard:
    """All watches for one field: set/drop, the per-append gravity check,
    and the pending-report queue the chat path drains. An optional
    junk_gate callable (vec -> bool, wired by the proxy to the entity
    store's anchor screen) keeps machinery from tripping the probes."""

    def __init__(self, path, embedder=None, junk_gate=None) -> None:
        self.path = path
        self.embedder = embedder
        self.junk_gate = junk_gate     # vec -> bool (True = clean)
        self.watches: dict[int, Watch] = {}
        self._next_wid = 1
        self.events: list[dict] = []          # newest last, capped EVENT_MAX

    # ---- persistence ----
    def save(self) -> None:
        try:
            self.path.write_text(json.dumps({
                "next_wid": self._next_wid,
                "watches": [w.to_json() for w in self.watches.values()],
            }))
        except Exception:
            pass                              # persistence never breaks chat

    @classmethod
    def load(cls, path, embedder=None) -> "WatchBoard":
        b = cls(path, embedder)
        try:
            r = json.loads(path.read_text())
            for w in r.get("watches", []):
                rec = Watch.from_json(w)
                b.watches[rec.wid] = rec
            b._next_wid = int(r.get("next_wid", len(b.watches) + 1))
            # THE CAP ENFORCES ON LOAD: a pre-covenant board may hold more
            # than MAX_WATCHES -- boot trims, lowest-charge (tie: oldest
            # hit) first. Same covenant as the tracks' load trim.
            over = len(b.watches) - MAX_WATCHES
            if over > 0:
                victims = sorted(b.watches.values(),
                                 key=lambda w: (w.charge, w.last_hit))[:over]
                for v in victims:
                    b.watches.pop(v.wid, None)
        except Exception:
            pass
        return b

    # ---- set / drop ----
    def set_watch(self, label: str, vec) -> tuple[Watch, str | None]:
        """Plant (or re-arm) a watch by topic; returns (watch, evicted_label).
        ``vec`` (unit list) optional: None -> embed the label via the board's
        embedder. A re-plant of the same label refreshes radius+charge instead
        of doubling the probes."""
        if self.embedder is None and vec is None:
            raise RuntimeError("no embedder")
        if vec is None:
            v = np.asarray(self.embedder.embed([label])[0], dtype=float)
        else:
            v = np.asarray(vec, dtype=float)
        n = float(np.linalg.norm(v))
        if n <= 1e-12:
            raise RuntimeError("zero vector: the topic is semantically empty")
        probe = (v / n).tolist()
        evicted = None
        for w in self.watches.values():
            if w.label.strip().lower() == label.strip().lower():
                w.probe = probe
                w.radius = max(RADIUS_MIN, w.radius)
                w.charge = max(w.charge, CHARGE_START)
                w.dormant_until = 0.0
                w.window = []
                return w, None
        if len(self.watches) >= MAX_WATCHES:
            victim = min(self.watches.values(),
                         key=lambda w: (w.charge, w.last_hit))
            evicted = victim.label
            self.watches.pop(victim.wid)
        w = Watch(wid=self._next_wid, label=label.strip(), probe=probe)
        self._next_wid += 1
        self.watches[w.wid] = w
        self.save()
        return w, evicted

    def drop_watch(self, spec: str) -> tuple[int, str]:
        """Drop by '#N', 'all', or label substring. Returns (removed, note)."""
        spec = (spec or "").strip().lower()
        if not spec:
            return 0, "give an index (#N), 'all', or a label fragment"
        if spec == "all":
            n = len(self.watches)
            self.watches.clear()
            self.save()
            return n, "cleared"
        if spec.startswith("#") and spec[1:].isdigit():
            w = self.watches.pop(int(spec[1:]), None)
            if w is None:
                return 0, f"no watch {spec}"
            self.save()
            return 1, w.label
        low = spec.lstrip("#")
        if low.isdigit():
            w = self.watches.pop(int(low), None)
            if w is None:
                return 0, f"no watch #{low}"
            self.save()
            return 1, w.label
        hits = [w for w in self.watches.values()
                if spec in w.label.strip().lower()]
        for w in hits:
            self.watches.pop(w.wid, None)
        if hits:
            self.save()
        return len(hits), (hits[0].label if hits else "nothing matched")

    # ---- the per-append check (the hook target) ----
    def on_learn(self, ex, idx: int) -> dict | None:
        """One new exemplar arrived; check every watch. Returns the event
        recorded (or None). Pure numpy dot products: no embed calls, and
        everything is bounded (<= MAX_WATCHES comparisons)."""
        vec = getattr(ex, "vec", None)
        if not vec:
            return None
        v = np.asarray(vec, dtype=float)
        n = float(np.linalg.norm(v))
        if n <= 1e-12:
            return None
        v = v / n
        now = time.time()
        ev = None
        pv = np.asarray([w.probe for w in self.watches.values()], dtype=float) \
            if self.watches else None
        if pv is None or not len(pv):
            return None
        sims = pv @ v
        ex_key = _entity_key_of(ex)
        for w, sim in zip(list(self.watches.values()), sims):
            # the junk gate: machinery (raw tool output) never trips a
            # watch -- the vec-vs-anchor screen, per-watch cut or default.
            # A gated arrival counts as a miss (it WAS seen, just refused)
            if self.junk_gate is not None and not self.junk_gate(vec, w):
                w.seen += 1
                w.window.append(0)
                if len(w.window) > WINDOW:
                    w.window = w.window[-WINDOW:]
                w.charge = max(0.0, w.charge - CHARGE_MISS)
                continue
            # the filter: a filtered watch fires only on matching arrivals
            # (entity key exact or kind prefix; '' = all memories)
            if w.filter_key:
                fk = w.filter_key.lower()
                if not (ex_key == fk or ex_key.startswith(fk)
                        or ex_key.split(":", 1)[0] + ":" == fk):
                    w.seen += 1
                    w.window.append(0)
                    if len(w.window) > WINDOW:
                        w.window = w.window[-WINDOW:]
                    w.charge = max(0.0, w.charge - CHARGE_MISS)
                    continue
            hit = float(sim) >= w.radius
            w.seen += 1
            if hit:
                w.hits += 1
                w.last_hit = now
                w.charge = min(CHARGE_MAX, w.charge + CHARGE_HIT)
            else:
                w.charge = max(0.0, w.charge - CHARGE_MISS)
            w.window.append(1 if hit else 0)
            if len(w.window) > WINDOW:
                w.window = w.window[-WINDOW:]
            rate = (sum(w.window) / len(w.window)) if w.window else 0.0
            awake = now >= w.dormant_until
            if awake and w.charge <= 0.0:
                # STARVATION: nobody's attention is owed -- a watch that
                # earned nothing (no hits to refill it) sleeps it off.
                w.dormant_until = now + DORMANT_S
                w.collapse_note = ("dormant: starved (charge 0, no earned "
                                   "hits); wakes to catch up on missed time")
                w.window = []
                w.charge = CHARGE_START * 0.5   # a small welcome-back stipend
            elif awake and len(w.window) >= MIN_WINDOW:
                if rate > DORM_RATE:
                    w.dormant_until = now + DORMANT_S
                    w.collapse_note = (f"dormant: swallowed "
                                       f"{sum(w.window)}/{len(w.window)} "
                                       f"arrivals (black hole); re-armed "
                                       f"with a fresh window")
                    w.window = []
                elif rate > COLLAPSE_RATE and w.radius > RADIUS_MIN:
                    w.radius = max(RADIUS_MIN, w.radius * COLLAPSE_SHRINK)
                    w.collapse_note = (f"collapsed: radius -> "
                                       f"{w.radius:.2f} (hit rate "
                                       f"{rate:.0%} > {COLLAPSE_RATE:.0%})")
            elif not awake and w.seen and w.seen % 50 == 0:
                pass                    # sleeping watches keep counting, cheap
            if hit and now >= w.dormant_until and ev is None:
                ev = {"ts": now, "wid": w.wid, "label": w.label,
                      "sim": round(float(sim), 4), "ex_idx": idx,
                      "situation": (ex.situation or "")[:300],
                      "action": (getattr(ex, "action", "") or "")[:300],
                      "delivered": False}
                w.charge = min(CHARGE_MAX, w.charge + CHARGE_HIT)
                self.events.append(ev)
                if len(self.events) > EVENT_MAX:
                    self.events = self.events[-EVENT_MAX:]
        return ev

    # ---- delivery: undelivered events -> one marked report block ----
    def take_report(self) -> tuple[list[dict], str] | None:
        """(events, block) for every undelivered event, or None. The caller
        marks delivered ONLY after the block actually reached the model."""
        pend = [e for e in self.events if not e.get("delivered")
                and time.time() - e["ts"] < 3600.0]
        if not pend:
            return None
        parts = ["[membrane-watch] your spatial triggers caught new memories:",
                 "These are watch reports -- machinery context, not user text. "
                 "Mention on your turn only if relevant; do not restate verbatim."]
        for e in pend[:6]:
            parts.append(f"- watch {e['label']!r} (sim {e['sim']}, "
                         f"exemplar #{e['ex_idx']}):\n"
                         f"    situation: {e['situation']}\n"
                         f"    response: {e.get('action', '')}")
        if len(pend) > 6:
            parts.append(f"- ... and {len(pend) - 6} more (see `membrane: watches`)")
        return pend, "\n".join(parts)

    def mark_served(self, events: list[dict]) -> None:
        for e in events:
            e["delivered"] = True

    # ---- reporting ----
    def report(self) -> str:
        now = time.time()
        if not self.watches:
            return ("Membrane watches: none planted. Plant one with "
                    "`membrane: watch <topic>` (cap " + str(MAX_WATCHES) +
                    "); every new memory inside its radius is reported to "
                    "you as a marked block.")
        lines = [f"Membrane watches ({len(self.watches)}/{MAX_WATCHES} live):"]
        for w in sorted(self.watches.values(), key=lambda x: x.wid):
            state = "dormant" if now < w.dormant_until else "live"
            rate = (sum(w.window) / len(w.window)) if w.window else 0.0
            lines.append(f"- #{w.wid} {w.label!r} radius {w.radius:.2f} "
                         f"charge {w.charge:.1f} hits {w.hits}/{w.seen} "
                         f"(recent rate {rate:.0%}) [{state}]"
                         + (f" -- {w.collapse_note}" if w.collapse_note else ""))
        undel = [e for e in self.events if not e.get("delivered")]
        lines.append(f"- undelivered reports: {len(undel)} "
                     f"(delivered as [membrane-watch] blocks on model turns)")
        return "\n".join(lines)