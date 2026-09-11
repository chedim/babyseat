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

import json
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
    last_hit: float = 0.0

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
        """Open intents, lazily expiring (expired -> status flip, no delete)."""
        now = time.time() if now is None else now
        for i in self.intents.values():
            if i.status == "open" and i.expires and now >= i.expires:
                i.status = "expired"
        return [i for i in self.intents.values() if i.status == "open"]

    # ---- the steering query (the dream loop's hook; no embed call) ----
    def nearest(self, vec, now: float | None = None
                ) -> tuple[Intention | None, float]:
        """(best open intent, cos) for a unit vector -- the steering coordinate."""
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