"""Per-person reply models (the operator 2026-11-08): membrane sits BETWEEN the speakers and keeps, per person, counts of
how they reply -- which words of the message before meet which words of their reply -- backing off to everyone's counts
(the prior worth one sample at each level; no other constant).

For a question q and a person X who might answer it:
  generated  g_X(b|q) = mean over a in q of t_X(b|a) / P(b)      how X's reply to q would read (word weights)
  listener   l_X(m|q) = mean over a in q of log(mean over b in m of t_X(a|b) / P(a))
                                                                  would X's message m be heard as the reply to q
  t_X(b|a) = (n_X(a -> b) + t(b|a)) / (n_X(a) + 1),   t(b|a) = (n(a -> b) + P(b)) / (n(a) + 1)
Bench results (LoCoMo, all ten, held out): as graph elements, both together lift single-hop @1/@3/@8/@20
0.41/0.57/0.69/0.79 -> 0.45/0.64/0.76/0.85; temporal @1 0.51 -> 0.48 (the generated side)."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from . import facts as F

TOK = re.compile(r"[a-z0-9']+")


def words(text: str) -> set:
    return set(TOK.findall((text or "").lower()))


class Table:
    def __init__(self):
        self.pair = defaultdict(Counter)    # a -> Counter(b): prev word a, reply word b
        self.rpair = defaultdict(Counter)   # b -> Counter(a)
        self.na = Counter()
        self.nb = Counter()

    def add(self, pw: set, cw: set):
        self.na.update(pw); self.nb.update(cw)
        for a in pw:
            self.pair[a].update(cw)
        for b in cw:
            self.rpair[b].update(pw)


class Replies:
    """Built from the talk's messages in order (one per message id, from the field's clause facts)."""

    def __init__(self, est, background=None):
        msgs = {}
        for f in F.clauses(est):
            m = f.get("msg")
            if m and m not in msgs:
                msgs[m] = (float(f.get("said") or 0), m, f.get("speaker") or "", words(f.get("text") or ""))
        self.talk = sorted(msgs.values())
        self.df, self.n = Counter(), 0
        self.all, self.person = Table(), defaultdict(Table)
        self.who = {m: s for _t, m, s, _w in self.talk}
        self.w = {m: w for _t, m, _s, w in self.talk}
        for _t, _m, _s, w in self.talk:
            self.df.update(w); self.n += 1
        for (_t1, _m1, _s1, pw), (_t2, _m2, s2, cw) in zip(self.talk, self.talk[1:]):
            self.all.add(pw, cw)
            self.person[s2].add(pw, cw)
        for sess in background or ():                   # other talk (other chats in the field; the bench: the other
            for w in sess:                              # conversations): everyone's counts only, never a person's
                self.df.update(w); self.n += 1
            for pw, cw in zip(sess, sess[1:]):
                self.all.add(pw, cw)
        self.people = sorted(self.person)

    def P(self, w: str) -> float:
        return (self.df[w] + 1) / (self.n + 2)

    def _t(self, a: str, b: str, who: str | None) -> float:          # t_X(b|a)
        t = ((self.all.pair[a][b] if a in self.all.pair else 0) + self.P(b)) / (self.all.na[a] + 1)
        pt = self.person.get(who) if who else None
        if pt is not None:
            t = ((pt.pair[a][b] if a in pt.pair else 0) + t) / (pt.na[a] + 1)
        return t

    def _r(self, a: str, b: str, who: str | None) -> float:          # t_X(a|b), the reply turned round
        t = ((self.all.rpair[b][a] if b in self.all.rpair else 0) + self.P(a)) / (self.all.nb[b] + 1)
        pt = self.person.get(who) if who else None
        if pt is not None:
            t = ((pt.rpair[b][a] if b in pt.rpair else 0) + t) / (pt.nb[b] + 1)
        return t

    def generated(self, q: set, who: str | None) -> dict:
        """X's predicted reply to q: log lift per word, for every word of the talk above chance."""
        if not q:
            return {}
        out = {}
        for b in self.df:
            v = math.log(sum(self._t(a, b, who) for a in q) / len(q) / self.P(b))
            if v > 0:
                out[b] = v
        return out

    def score(self, q: set, msg: str, who: str | None = None) -> float:
        """The person's pragmatic score of message msg for question q: generated + listener, positive parts,
        by the message's own speaker unless `who` is given."""
        m = self.w.get(msg) or set()
        if not q or not m:
            return 0.0
        who = who or self.who.get(msg)
        gen = sum(math.log(sum(self._t(a, b, who) for a in q) / len(q) / self.P(b)) for b in m) / len(m)
        lis = sum(math.log(sum(self._r(a, b, who) for b in m) / len(m) / self.P(a)) for a in q) / len(q)
        return max(0.0, gen) + max(0.0, lis)


_CACHE: dict = {}
BACKGROUND: list = []        # sessions of other talk (lists of word sets) for everyone's counts; set by the caller


def of(est) -> Replies:
    """The reply models of this field, rebuilt when its clause count or the background changes."""
    n = sum(1 for d in (est.docs or {}).values() if d.cls == F.CLASS_CLAUSE)
    key = (id(est), id(BACKGROUND), len(BACKGROUND))
    got = _CACHE.get(key)
    if got is None or got[0] != n:
        got = (n, Replies(est, BACKGROUND))
        _CACHE[key] = got
    return got[1]
