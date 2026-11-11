"""THE MOVE LOG (0.7.52; bench #932 / arm 58, 2026-11-05..07): how far each event moved each thing it named, over
the WHOLE field (every chat, every person, tool outputs), recorded at capture and kept with the field.

  moves      event key -> {thing: how far it moved it}: the largest single-sentence move (|GAIN (x - before)|,
             the field's own update), recorded by EntityField._update as the event applies
  episodes   each event's place in its chat (session) by seq: the turn before it is its neighbor
  kept       the moves ride the event's one index delta row ('e:<key>', fields `mv`, `moves`, `chat`): a restart
             loads them (load), nothing is replayed. Events applied before 0.7.52 are backfilled once in the
             background (backfill): a specific thing's move from its stored delta row, a word's or phrase's
             (no delta row) from the sentence vector and the thing's seed, as a first update moves it
  nursery    a thing not yet materialized (under the field's K_MAT mentions) moves from its seed, the way its
             first update will

settle(): recall the way cues settle -- the question's things (eids.mentions_of), each weighed by its rarity
log(episodes / episodes that moved it); round 1 scores each episode by its cued things' moves x their rarity; an
episode also holds what the turn before it in its chat moved (a reply carries the turn it answers).
"""
from __future__ import annotations

import bisect
import math
import re
import threading

from . import eids as E

ROUNDS = 1                 # round 2 floods on LoCoMo (the operator 2026-11-06: 1)


class MoveLog:
    def __init__(self):
        self.lock = threading.RLock()
        self.moves: dict = {}            # key -> {eid: step}
        self.by_thing: dict = {}         # eid -> set(keys)
        self._place: dict = {}           # key -> (chat, seq)
        self._chats: dict = {}           # chat -> sorted [(seq, key)]
        self.gen = 0                     # bumps on every change (the readers' cache key)
        self.loaded = False

    # ---- capture --------------------------------------------------------------------------------
    def note(self, key: str, eid: str, step: float) -> None:
        with self.lock:
            m = self.moves.setdefault(key, {})
            if step > m.get(eid, -1.0):
                m[eid] = float(step)
                self.by_thing.setdefault(eid, set()).add(key)
                self.gen += 1

    def place(self, key: str, chat: str, seq: int) -> None:
        with self.lock:
            if key in self._place:
                return
            self._place[key] = (chat, int(seq))
            bisect.insort(self._chats.setdefault(chat, []), (int(seq), key))
            self.gen += 1

    def drop(self, key: str) -> None:
        with self.lock:
            for e in self.moves.pop(key, {}):
                s = self.by_thing.get(e)
                if s:
                    s.discard(key)
            pl = self._place.pop(key, None)
            if pl:
                lst = self._chats.get(pl[0], [])
                i = bisect.bisect_left(lst, (pl[1], key))
                if i < len(lst) and lst[i] == (pl[1], key):
                    lst.pop(i)
            self.gen += 1

    def chat_of(self, key: str) -> str | None:
        pl = self._place.get(key)
        return pl[0] if pl else None

    def prev_of(self, key: str) -> str | None:
        pl = self._place.get(key)
        if not pl:
            return None
        lst = self._chats.get(pl[0], [])
        i = bisect.bisect_left(lst, (pl[1], key))
        return lst[i - 1][1] if i > 0 else None

    # ---- kept with the field --------------------------------------------------------------------
    def load(self, field) -> int:
        """The move log from the stored index rows (no replay). Returns the events loaded."""
        n = 0
        for _k, d in field.b.scan("entity_deltas", {"origin": field.origin, "mv": 1}):
            key = d.get("event")
            if not key:
                continue
            for e, s in (d.get("moves") or {}).items():
                self.note(key, e, float(s))
            self.place(key, str(d.get("chat") or ""), int(d.get("seq") or 0))
            n += 1
        self.loaded = True
        return n

    def backfill(self, field, batch: int = 500, stop=None) -> int:
        """Events applied before the move log (no `mv` on their index row): a specific thing's move from its stored
        delta row (gain x sqrt(2 - 2 cos_before)); a word's or phrase's (no delta row) from the stored sentence
        vector and the thing's cached seed, |GAIN (x - seed)|. Written onto the index row once. One pass over the
        deltas; background only, never a turn; no embedder call (a seed not cached is skipped)."""
        import numpy as np
        from .efield import GAIN, unit
        from .efstore import unpack_f16
        idx, steps = {}, {}
        for k, d in field.b.scan("entity_deltas", {"origin": field.origin}):
            ev, e = d.get("event"), str(d.get("eid") or "")
            if not ev or not e:
                continue
            if e.startswith("e:"):
                if not d.get("mv"):
                    idx[ev] = (k, d)
                continue
            c = float(d.get("cos_before", 1.0))
            s = float(d.get("gain", 0.0)) * math.sqrt(max(0.0, 2.0 - 2.0 * c))
            m = steps.setdefault(ev, {})
            m[e] = max(m.get(e, 0.0), s)
        done = 0
        todo = list(idx.items())
        for lo in range(0, len(todo), max(1, batch)):
            if stop is not None and stop():
                break
            part = todo[lo:lo + batch]
            evs = field.b.get_multi("events", [ev for ev, _ in part])
            sents = field.b.get_multi("sentences", list({sid for d in evs.values() for sid in d.get("sids") or []}))
            out = {}
            for ev, (rk, row) in part:
                doc = evs.get(ev)
                if not doc:
                    continue
                mv = dict(steps.get(ev, {}))
                sids = doc.get("sids") or []
                for m in doc.get("mentions") or []:
                    e, sidx, surf = m[0], int(m[1]), m[2] if len(m) > 2 else ""
                    if e in mv or e.split(":", 1)[0] not in ("w", "p") or sidx >= len(sids):
                        continue
                    srow = sents.get(sids[sidx])
                    t = field.b.get("terms", field._term_key(surf or e.split(":", 1)[-1]))
                    if not srow or not t:
                        continue
                    x = unit(srow["vec"])
                    mv[e] = float(np.linalg.norm(GAIN * (x - unpack_f16(t["vec"]))))
                chat = str(doc.get("session") or "")
                for e, s in mv.items():
                    self.note(ev, e, s)
                self.place(ev, chat, int(doc.get("seq") or row.get("seq") or 0))
                out[rk] = dict(row, mv=1, moves={e: round(s, 5) for e, s in mv.items()}, chat=chat)
                done += 1
            if out:
                field.b.upsert_multi("entity_deltas", out)
        return done


def of(field) -> MoveLog:
    ml = getattr(field, "move_log", None)
    if ml is None:
        ml = field.move_log = MoveLog()
    return ml


# ---- reading ------------------------------------------------------------------------------------
def cues(field, text: str) -> set:
    graph = field.graph_fn()
    return {eid for s in re.findall(r"[^.!?]+[.!?]*", text or "")
            for eid, _ in E.mentions_of(s, graph, field.phrases)}


def held(ml: MoveLog, neighbors: bool = True) -> dict:
    """event -> {thing: move}: its own moves and (neighbors) the turn before it's, the larger of each."""
    cache = getattr(ml, "_held", None)
    if cache is not None and cache[0] == (ml.gen, neighbors):
        return cache[1]
    with ml.lock:
        out = {}
        for k, m in ml.moves.items():
            h = dict(ml.moves.get(ml.prev_of(k), {})) if neighbors else {}
            for e, s in m.items():
                h[e] = max(h.get(e, 0.0), s)
            out[k] = h
        ml._held = ((ml.gen, neighbors), out)
    return out


def settle(field, text: str, rounds: int = ROUNDS, neighbors: bool = True) -> list:
    """[(event key, score)], best first; events no round lit are left out."""
    ml = of(field)
    hd = held(ml, neighbors)
    by: dict = {}
    for k, m in hd.items():
        for e in m:
            by.setdefault(e, []).append(k)
    n = max(1, len(hd))
    rare = {e: math.log(n / len(ks)) for e, ks in by.items()}
    lit = {e: rare[e] for e in cues(field, text) if e in rare}
    seen = set(lit)
    total: dict = {}
    for r in range(max(1, rounds)):
        score: dict = {}
        for e, a in lit.items():
            for k in by.get(e, ()):
                score[k] = score.get(k, 0.0) + a * hd[k][e]
        top = max(score.values(), default=0.0)
        if top <= 0:
            break
        for k, s in score.items():
            total[k] = total.get(k, 0.0) + s / top
        if r + 1 < rounds:
            nxt: dict = {}
            for k, s in score.items():
                for e, mv in hd[k].items():
                    if e not in seen:
                        nxt[e] = nxt.get(e, 0.0) + (s / top) * mv * rare[e]
            seen |= set(nxt)
            lit = nxt
    return sorted(total.items(), key=lambda kv: -kv[1])
