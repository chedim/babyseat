"""THE MOVE LOG (0.7.52; bench #932 / arm 58, 2026-11-05..07): how far each event moved each thing, over the WHOLE
field (every chat, every person, tool outputs), recorded at capture and kept with the field.

  moves      event key -> {thing: its largest single-sentence move} (|GAIN (x - before)|, the field's own update,
             recorded in EntityField._update, and for a nursery thing as a K_MAT=1 field's update would)
  msum       event key -> {thing: the sum of its sentences' moves} (the bench's field.moves; entity recall reads it)
  turns      each event's place in its chat by seq; its neighbor is the previous TURN (a person's or the model's
             discourse line) in that chat, never a tool output
  held       event -> its own moves and its neighbor's (the larger of each), kept current as moves land
  kept       both maps ride the event's one index delta row ('e:<key>': `mv`, `moves`, `msum`, `chat`, `turn`); a
             materialization replay that raises an older event's moves re-writes that row at the next flush; a
             restart loads the rows in capture order (seq), nothing is replayed. Events applied before 0.7.52 are
             backfilled once (a done-marker keeps later boots from scanning again): specific things from their
             stored delta rows, words and phrases as a K_MAT=1 field moves them (stored seed and sentence vectors)
  nursery    the bench ran with K_MAT=1 (every word lives at its first mention), so a thing still in this field's
             nursery moves too: exactly as a K_MAT=1 _update would, from its seed through its earlier mentions
             (EntityField._nursery_move) -- the field's own K_MAT is unchanged

settle(): the question's things (eids.mentions_of), each weighed by its rarity log(events / events holding it);
round 1 scores each event by its cued things' held moves x their rarity. Ask time reads only the cued things'
postings -- never the whole field.
"""
from __future__ import annotations

import bisect
import math
import re
import threading

from . import eids as E

ROUNDS = 1                 # round 2 floods on LoCoMo (the operator 2026-11-06: 1)
DONE_KEY = "mvdone:"       # annotations: the backfill's done-marker, per origin


class MoveLog:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Empty (a rebuild or re-embed re-applies every event: the log is re-made, never added to)."""
        self._parts: dict = {}           # key -> {eid: {sentence: step}} (a note is a set: never counted twice)
        self.asker: dict = {}            # chat -> its latest person (the banner's asker, no scan)
        self.moves: dict = {}            # key -> {eid: max step}
        self.msum: dict = {}             # key -> {eid: summed step}
        self.by_thing: dict = {}         # eid -> {key: None} (capture order), over msum
        self.held: dict = {}             # key -> {eid: step}
        self.held_by: dict = {}          # eid -> {key: None} (capture order), over held
        self._place: dict = {}           # key -> (chat, seq, turn)
        self._turns: dict = {}           # chat -> sorted [(seq, key)] of its turns
        self._next: dict = {}            # key -> the events whose neighbor it is
        self.dirty: set = set()          # keys whose moves changed after their index row was built
        self.ent_rank: dict = {}         # eid -> the order it was first moved (the bench's worldlines order)
        self._idx: dict = {}             # (eid, key) -> the key's place among the eid's events
        self.gen = 0
        self.loaded = False

    # ---- capture --------------------------------------------------------------------------------
    def note(self, key: str, eid: str, step: float, part=0) -> None:
        """This event moved this thing `step` in sentence `part`: SET (a re-apply of the same sentence is the same
        note); the event's max and sum are over its sentences."""
        with self.lock:
            c = self._parts.setdefault(key, {}).setdefault(eid, {})
            if "*" in c:                                  # loaded aggregates: a re-apply re-makes them
                c.clear()
            if c.get(part) == float(step):
                return
            c[part] = float(step)
            mx, sm = max(c.values()), sum(c.values())
            m = self.moves.setdefault(key, {})
            old = m.get(eid)
            m[eid] = mx
            self.msum.setdefault(key, {})[eid] = sm
            self._post(eid, key)
            if old != mx:
                self._rehold(key)
                for k in self._next.get(key, ()):
                    self._rehold(k)
            self.dirty.add(key)
            self.gen += 1

    def _post(self, eid: str, key: str) -> None:
        if eid not in self.ent_rank:
            self.ent_rank[eid] = len(self.ent_rank)
        book = self.by_thing.setdefault(eid, {})
        if key not in book:
            self._idx[(eid, key)] = len(book)
            book[key] = None

    def order_key(self, key: str) -> tuple:
        """The bench's tie order: its move log's events, thing by thing (in the order things were first moved),
        each thing's events in capture order -- an event sits where its earliest thing first lists it."""
        own = self.moves.get(key) or {}
        return min(((self.ent_rank.get(e, 1 << 30), self._idx.get((e, key), 1 << 30)) for e in own),
                   default=(1 << 30, 1 << 30))

    def place(self, key: str, chat: str, seq: int, turn: bool = True) -> None:
        with self.lock:
            if key in self._place:
                return
            self._place[key] = (chat, int(seq), bool(turn))
            lst = self._turns.setdefault(chat, [])
            prev = self._prev_turn(chat, int(seq))
            if prev is not None:
                self._next.setdefault(prev, []).append(key)
            if turn:
                i = bisect.bisect_left(lst, (int(seq), key))
                lst.insert(i, (int(seq), key))
                # a later turn's neighbor may now be this one (an out-of-order load)
                if i + 1 < len(lst):
                    nk = lst[i + 1][1]
                    old = self._prev_turn(chat, lst[i + 1][0], exclude=key)
                    if old is not None and nk in self._next.get(old, []):
                        self._next[old].remove(nk)
                    self._next.setdefault(key, []).append(nk)
                    self._rehold(nk)
            self._rehold(key)
            self.gen += 1

    def _prev_turn(self, chat: str, seq: int, exclude: str | None = None):
        lst = self._turns.get(chat, [])
        i = bisect.bisect_left(lst, (seq, ""))
        while i > 0:
            i -= 1
            if lst[i][1] != exclude:
                return lst[i][1]
        return None

    def prev_of(self, key: str) -> str | None:
        pl = self._place.get(key)
        return self._prev_turn(pl[0], pl[1], exclude=key) if pl else None

    def chat_of(self, key: str) -> str | None:
        pl = self._place.get(key)
        return pl[0] if pl else None

    def _rehold(self, key: str) -> None:
        own = self.moves.get(key)
        if own is None and key not in self.held:
            return
        h = dict(self.moves.get(self.prev_of(key), {}) if key in self._place else {})
        for e, s in (own or {}).items():
            h[e] = max(h.get(e, 0.0), s)
        old = self.held.get(key, {})
        for e in old:
            if e not in h:
                self.held_by.get(e, {}).pop(key, None)
        for e in h:
            self.held_by.setdefault(e, {})[key] = None
        if h:
            self.held[key] = h
        else:
            self.held.pop(key, None)

    def drop(self, key: str) -> None:
        with self.lock:
            self._parts.pop(key, None)
            for e in self.msum.pop(key, {}):
                self.by_thing.get(e, {}).pop(key, None)
                self._idx.pop((e, key), None)
            self.moves.pop(key, None)
            for e in self.held.pop(key, {}):
                self.held_by.get(e, {}).pop(key, None)
            pl = self._place.pop(key, None)
            if pl and pl[2]:
                lst = self._turns.get(pl[0], [])
                i = bisect.bisect_left(lst, (pl[1], key))
                if i < len(lst) and lst[i] == (pl[1], key):
                    lst.pop(i)
            for k in self._next.pop(key, []):
                self._rehold(k)
            self.dirty.discard(key)
            self.gen += 1

    def row_fields(self, key: str) -> dict:
        with self.lock:
            return {"mv": 1, "moves": {e: round(v, 5) for e, v in (self.moves.get(key) or {}).items()},
                    "msum": {e: round(v, 5) for e, v in (self.msum.get(key) or {}).items()}}

    # ---- kept with the field --------------------------------------------------------------------
    def load(self, field) -> int:
        """The move log from the stored index rows, in capture order (seq). Returns the events loaded."""
        rows = [d for _k, d in field.b.scan("entity_deltas", {"origin": field.origin, "mv": 1}) if d.get("event")]
        rows.sort(key=lambda d: (int(d.get("seq") or 0), str(d.get("event"))))
        with self.lock:
            for d in rows:
                self._take(d["event"], d)
            self.loaded = True
        return len(rows)

    def _take(self, key: str, d: dict) -> None:
        self.place(key, str(d.get("chat") or ""), int(d.get("seq") or 0), bool(d.get("turn", True)))
        mx, sm = d.get("moves") or {}, d.get("msum") or d.get("moves") or {}
        self.moves[key] = {e: float(v) for e, v in mx.items()}
        self.msum[key] = {e: float(v) for e, v in sm.items()}
        self._parts[key] = {e: {"*": (self.moves[key].get(e, 0.0), v)} for e, v in self.msum[key].items()}
        for e in self.msum[key]:
            self._post(e, key)
        self._rehold(key)
        for k in self._next.get(key, ()):
            self._rehold(k)
        self.dirty.discard(key)
        self.gen += 1

    def backfill(self, field, batch: int = 500, stop=None) -> int:
        """Events applied before the move log (no `mv` on their index row), once (a done-marker skips the scan on
        later boots); background only, no embedder:
          a specific thing   its stored delta rows (gain and cos_before: step = gain x sqrt(2 - 2 cos))
          a word or phrase   (no delta row) the K_MAT=1 field's moves: from its STORED seed through its mentions in
                             order, each step |GAIN (x - state)| on the STORED sentence vector; a word whose seed
                             is not stored is skipped"""
        from .efield import move_step, move_to, unit, at_stored
        from .efstore import unpack_f16
        if field.b.get("annotations", DONE_KEY + field.origin):
            return 0
        idx, mx, sm = {}, {}, {}

        def put(ev, e, s):
            a, b = mx.setdefault(ev, {}), sm.setdefault(ev, {})
            a[e] = max(a.get(e, 0.0), s)
            b[e] = b.get(e, 0.0) + s
        for k, d in field.b.scan("entity_deltas", {"origin": field.origin}):
            ev, e = d.get("event"), str(d.get("eid") or "")
            if not ev or not e:
                continue
            if e.startswith("e:"):
                if not d.get("mv"):
                    idx[ev] = (k, d)
                continue
            c = float(d.get("cos_before", 1.0))
            put(ev, e, float(d.get("gain", 0.0)) * math.sqrt(max(0.0, 2.0 - 2.0 * c)))
        todo = sorted(idx.items(), key=lambda kv: int(kv[1][1].get("seq") or 0))
        docs: dict = {}
        words: dict = {}                       # word -> [(seq, sidx, event, sid, surface)]
        for lo in range(0, len(todo), max(1, batch)):
            if stop is not None and stop():
                return 0
            got = field.b.get_multi("events", [ev for ev, _ in todo[lo:lo + batch]])
            for ev, d in got.items():
                docs[ev] = {"session": d.get("session"), "seq": d.get("seq"), "stream": d.get("stream"),
                            "role": d.get("role"), "mentions": d.get("mentions") or []}
                sids = d.get("sids") or []
                for m in d.get("mentions") or []:
                    e, sidx = m[0], int(m[1])
                    if e.split(":", 1)[0] in ("w", "p") and sidx < len(sids):
                        words.setdefault(e, []).append((int(d.get("seq") or 0), sidx, ev, sids[sidx],
                                                        m[2] if len(m) > 2 else ""))
        for e, ms in words.items():
            if stop is not None and stop():
                return 0
            ms.sort(key=lambda t: (t[0], t[1]))
            t = field.b.get("terms", field._term_key(ms[0][4] or e.split(":", 1)[-1]))
            if not t or t.get("vec") is None:
                continue
            sv = at_stored(unpack_f16(t["vec"]))
            vecs = field.b.get_multi("sentences", list({m[3] for m in ms}))
            for _seq, _sidx, ev, sid, _surf in ms:
                if sid not in vecs:
                    continue
                x = unit(vecs[sid]["vec"])
                put(ev, e, move_step(sv, x))
                sv = move_to(sv, x)
        done = 0
        for lo in range(0, len(todo), max(1, batch)):
            out = {}
            for ev, (rk, row) in todo[lo:lo + batch]:
                doc = docs.get(ev)
                if not doc:
                    continue
                order = list(dict.fromkeys([m[0] for m in doc["mentions"]] + list(mx.get(ev, {}))))
                new = dict(row, mv=1, chat=str(doc.get("session") or ""), turn=is_turn(doc),
                           moves={e: round(mx[ev][e], 5) for e in order if e in mx.get(ev, {})},
                           msum={e: round(sm[ev][e], 5) for e in order if e in sm.get(ev, {})})
                with self.lock:
                    self._take(ev, new)
                out[rk] = new
                done += 1
            if out:
                field.b.upsert_multi("entity_deltas", out)
        field.b.upsert("annotations", DONE_KEY + field.origin, {"_k": "mvdone", "origin": field.origin, "n": done})
        return done


def is_turn(ev: dict) -> bool:
    """A turn: a person's or the model's discourse line (a settling neighbor); tool outputs, code, notes are not."""
    return ev.get("stream") == "discourse" and (ev.get("role") or "") in ("user", "model")


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


def settle(field, text: str, rounds: int = ROUNDS) -> list:
    """[(event key, score)], best first; events no round lit are left out. Reads the cued things' postings only."""
    ml = of(field)
    cue = cues(field, text)
    with ml.lock:
        n = max(1, len(ml.held))
        lit = {e: math.log(n / len(ml.held_by[e])) for e in cue if ml.held_by.get(e)}
        seen = set(lit)
        total: dict = {}
        for r in range(max(1, rounds)):
            score: dict = {}
            for e, a in lit.items():
                for k in ml.held_by.get(e, ()):
                    score[k] = score.get(k, 0.0) + a * ml.held[k][e]
            top = max(score.values(), default=0.0)
            if top <= 0:
                break
            for k, s in score.items():
                total[k] = total.get(k, 0.0) + s / top
            if r + 1 < rounds:
                nxt: dict = {}
                for k, s in score.items():
                    for e, mv in ml.held[k].items():
                        if e not in seen and ml.held_by.get(e):
                            nxt[e] = nxt.get(e, 0.0) + (s / top) * mv * math.log(n / len(ml.held_by[e]))
                seen |= set(nxt)
                lit = nxt
        tie = {k: ml.order_key(k) for k in total}
    return sorted(total.items(), key=lambda kv: (-kv[1], tie[kv[0]]))


def entity_scores(field, hits) -> dict:
    """{event key: sum(sim x the summed move)} for the things hit (entity recall), from their postings."""
    ml = of(field)
    score: dict = {}
    with ml.lock:
        for e, s in hits:
            for k in ml.by_thing.get(e, ()):
                score[k] = score.get(k, 0.0) + float(s) * ml.msum[k][e]
    return score
