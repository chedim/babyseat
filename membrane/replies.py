"""membrane/replies.py: per-person reply models, "about X" counts and a failure ledger (#942 stage 1).

membrane sits BETWEEN the speakers. Each message is counted as the reply to the message before it in its chat: into
its speaker's table and everyone's. A person's table backs off to everyone's, and everyone's to chance, each the prior
worth one sample (no other constant):
  t(b|a)   = (n(a -> b) + P(b)) / (n(a) + 1)                 everyone: word a in a message, word b in its reply
  t_X(b|a) = (n_X(a -> b) + t(b|a)) / (n_X(a) + 1)           X's replies only
  P(w)     = (messages holding w + 1) / (messages + 2)
Three scores of a stored message m (said by X) against an incoming message q, each a positive log lift (0 = chance):
  listener  would X's m be heard as the reply to q:  mean over a in q of log(mean over b in m of t_X(a|b) / P(a))
  said      how close m is to X's predicted reply:   mean over b in m of log(mean over a in q of t_X(b|a) / P(b))
  about     what gets said where q's things are named (same-message counts, people's names left out):
            mean over b in m of log(mean over a in q of t_co(b|a) / P(b)),  t_co(b|a) = (co(a,b) + P(b)) / (n(a) + 1)
The FAILURE LEDGER: before a live message is counted, membrane predicts it (the real message among up to 9 others the
same person said) with everyone's model and with the person's, and records which was right, keyed by (who, does the
previous message ask?) backing off to (who,) and to everyone. Misses are kept, never pruned. The two models are then
weighted by their records. Bench (LoCoMo, all ten talks, held out): the next message picked out of 10, 0.439 -> 0.470.
DREAMS (predicted messages) are read, never counted: dream() changes no table and no record.

Counts are kept as postings (which messages hold a word) and a word's table is built the first time a score needs it,
then kept up to date as messages arrive: a long model reply costs its words, not their pairs."""

from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict

TOK = re.compile(r"[a-z0-9']+")


def words(text: str) -> set:
    return set(TOK.findall((text or "").lower()))


class Ledger:
    """Which model predicted the next message right, for each score:
    key -> [everyone right (listener), person right (listener), everyone right (said), person right (said), steps]."""

    SLOT = {"listen": (0, 1), "say": (2, 3)}

    def __init__(self):
        self.c: dict = defaultdict(lambda: [0, 0, 0, 0, 0])

    @staticmethod
    def keys(who: str, asks: bool) -> list:
        return [(who, asks), (who,), ()]

    def add(self, who: str, asks: bool, hits: tuple) -> None:
        """hits = (everyone, person) for listener, then (everyone, person) for said -- misses included."""
        for k in self.keys(who, asks):
            v = self.c[k]
            for j, h in enumerate(hits):
                v[j] += bool(h)
            v[4] += 1

    def rates(self, who: str, asks: bool, score: str = "listen") -> tuple[float, float]:
        """(everyone's hit rate, the person's) for one score, coarse to fine, each level the prior worth one sample."""
        out = []
        for j in self.SLOT[score]:
            r = 0.5
            for k in reversed(self.keys(who, asks)):
                v = self.c.get(k)
                if v is not None:
                    r = (v[j] + r) / (v[4] + 1)
            out.append(r)
        return out[0], out[1]


class Replies:
    def __init__(self, seed: int = 942):
        self.msgs: list = []                 # (session, who, words)
        self.asks: list = []                 # does the message ask? (its last character is "?")
        self.prev: list = []                 # index of the message before, in its chat, or None
        self.last: dict = {}                 # session -> index of its last message
        self.post: dict = defaultdict(list)  # word -> messages holding it
        self.post_prev: dict = defaultdict(list)   # word a -> replies whose previous message holds a
        self.post_reply: dict = defaultdict(list)  # word b -> replies holding b
        self.by: dict = defaultdict(list)    # who -> their messages
        self.na: dict = defaultdict(Counter)  # who/None -> word -> replies whose previous holds it
        self.nb: dict = defaultdict(Counter)  # who/None -> word -> replies holding it
        self._fwd: dict = {}                 # (a, who) -> Counter of reply words
        self._rev: dict = {}                 # (b, who) -> Counter of previous words
        self._co: dict = {}                  # a -> Counter of words sharing a message
        self.ledger = Ledger()
        self.rng = random.Random(seed)
        self._cut_v, self._cut_n = 0, -1
        self.df_hist: Counter = Counter()      # how many words are held by d messages (the cutoff, kept exact)
        self.occ = 0                           # word occurrences over all messages

    # --- counting -----------------------------------------------------------------------------------------
    @property
    def n(self) -> int:
        return len(self.msgs)

    @property
    def people(self) -> list:
        return [w for w in self.by if w]

    def add(self, session: str, who: str, text: str, predict: bool = True) -> None:
        """Count a REAL message (never a dream). With predict, the ledger first records which model saw it coming."""
        w = words(text)
        p = self.last.get(session)
        if predict and p is not None and w:
            self._predict(p, who, w)
        i = len(self.msgs)
        self.occ += len(w)
        self.msgs.append((session, who, w))
        self.asks.append(_asks(text))
        self.prev.append(p)
        self.last[session] = i
        self.by[who].append(i)
        for x in w:
            d = len(self.post[x])
            if d:
                self.df_hist[d] -= 1
            self.df_hist[d + 1] += 1
            self.post[x].append(i)
            co = self._co.get(x)
            if co is not None:
                co.update(w)
        if p is None:
            return
        pw = self.msgs[p][2]
        for key in (None, who):
            self.na[key].update(pw)
            self.nb[key].update(w)
        for a in pw:
            self.post_prev[a].append(i)
            for key in (None, who):
                c = self._fwd.get((a, key))
                if c is not None:
                    c.update(w)
        for b in w:
            self.post_reply[b].append(i)
            for key in (None, who):
                c = self._rev.get((b, key))
                if c is not None:
                    c.update(pw)

    def _fwd_of(self, a: str, who) -> Counter:
        c = self._fwd.get((a, who))
        if c is None:
            c = Counter()
            for i in self.post_prev.get(a, ()):
                if who is None or self.msgs[i][1] == who:
                    c.update(self.msgs[i][2])
            self._fwd[(a, who)] = c
        return c

    def _rev_of(self, b: str, who) -> Counter:
        c = self._rev.get((b, who))
        if c is None:
            c = Counter()
            for i in self.post_reply.get(b, ()):
                if who is None or self.msgs[i][1] == who:
                    c.update(self.msgs[self.prev[i]][2])
            self._rev[(b, who)] = c
        return c

    def _co_of(self, a: str) -> Counter:
        c = self._co.get(a)
        if c is None:
            c = Counter()
            for i in self.post.get(a, ()):
                c.update(self.msgs[i][2])
            self._co[a] = c
        return c

    def count(self, a: str, b: str, who: str | None = None) -> int:
        """Replies (by who, or anyone) holding b whose previous message held a."""
        return self._fwd_of(a, who)[b]

    # --- the models ---------------------------------------------------------------------------------------
    def P(self, w: str) -> float:
        return (len(self.post.get(w, ())) + 1) / (self.n + 2)

    def t(self, a: str, b: str, who: str | None) -> float:          # t_X(b|a): b in X's reply after a
        pb = self.P(b)
        t = (self._fwd_of(a, None)[b] + pb) / (self.na[None][a] + 1)
        if who and who in self.by:
            t = (self._fwd_of(a, who)[b] + t) / (self.na[who][a] + 1)
        return t

    def r(self, a: str, b: str, who: str | None) -> float:          # t_X(a|b): a before X's reply word b
        pa = self.P(a)
        t = (self._rev_of(b, None)[a] + pa) / (self.nb[None][b] + 1)
        if who and who in self.by:
            t = (self._rev_of(b, who)[a] + t) / (self.nb[who][b] + 1)
        return t

    def _cut(self) -> int:
        """KEYWORDS (the reader's keyword() rule): a word is a keyword when fewer messages hold it than hold the
        talk's typical word by occurrence (the occurrence-weighted median), from a running histogram (exact, no
        re-sort of the vocabulary)."""
        if self._cut_n != self.n:
            acc, cut = 0, 0
            for d in sorted((d for d, k in self.df_hist.items() if k > 0), reverse=True):
                acc += d * self.df_hist[d]
                if acc >= self.occ / 2:
                    cut = d
                    break
            self._cut_v, self._cut_n = cut, self.n
        return self._cut_v

    def _known(self, ws) -> set:
        """The words a score reads: seen in the talk (a word never seen carries no evidence) and keywords. Common
        words sit near chance and would cost a walk over thousands of messages each (#942: 88 s banners)."""
        cut = self._cut()
        return {w for w in ws if w in self.post and len(self.post[w]) < cut}

    def _listen(self, q: set, who, m: set) -> float:
        q, m = self._known(q), self._known(m)
        if not q or not m:
            return 0.0
        return sum(math.log(sum(self.r(a, b, who) for b in m) / len(m) / self.P(a)) for a in q) / len(q)

    def _say(self, q: set, who, m: set) -> float:
        q, m = self._known(q), self._known(m)
        if not q or not m:
            return 0.0
        return sum(math.log(sum(self.t(a, b, who) for a in q) / len(q) / self.P(b)) for b in m) / len(m)

    def _mixed(self, f, q: set, who: str, m: set, asks: bool) -> float:
        rs, rp = self.ledger.rates(who, asks, "listen" if f == self._listen else "say")
        return (rs * f(q, None, m) + rp * f(q, who, m)) / (rs + rp)

    def listener(self, q: str, who: str, text: str) -> float:
        """Would `who`'s message `text` be heard as the reply to q? Positive log lift, the two models weighed by
        the ledger's record."""
        qw, m = words(q), words(text)
        return max(0.0, self._mixed(self._listen, qw, who, m, _asks(q)))

    def said(self, q: str, who: str, text: str) -> float:
        """How close `text` is to `who`'s predicted reply to q. Positive log lift, ledger-weighted."""
        qw, m = words(q), words(text)
        return max(0.0, self._mixed(self._say, qw, who, m, _asks(q)))

    def about(self, q: str, text: str) -> float:
        """What gets said where q's things are named (people's names left out). Positive log lift."""
        names = {x.lower() for w in self.people for x in TOK.findall(w.lower())}
        qa = [a for a in self._known(words(q)) if a not in names]
        m = self._known(words(text))
        if not qa or not m:
            return 0.0
        tot = 0.0
        for b in m:
            pb = self.P(b)
            t = sum((self._co_of(a)[b] + pb) / (len(self.post[a]) + 1) for a in qa) / len(qa)
            tot += math.log(t / pb)
        return max(0.0, tot / len(m))

    # --- the ledger and dreams ----------------------------------------------------------------------------
    def _predict(self, p: int, who: str, real: set) -> None:
        pw = self.msgs[p][2]
        others = [self.msgs[i][2] for i in self.by.get(who, ())]
        cands = [real] + self.rng.sample(others, min(9, len(others)))
        if len(cands) < 2:
            return

        def hit(f, model):
            sc = [f(pw, model, x) for x in cands]
            top = max(sc)
            return sc[0] == top and sum(v == top for v in sc) == 1
        self.ledger.add(who, self.asks[p], (hit(self._listen, None), hit(self._listen, who),
                                            hit(self._say, None), hit(self._say, who)))

    def dream(self, session: str, who: str, texts: list) -> list:
        """Rank possible next messages by `who` in this chat, best first: (score, text). Read-only: no table, no
        record changes (a dream is never counted)."""
        p = self.last.get(session)
        q = self.msgs[p][2] if p is not None else set()
        sc = [(self._listen(q, who, words(t)) + self._say(q, who, words(t)), t) for t in texts]
        return sorted(sc, key=lambda x: -x[0])


def _asks(text: str) -> bool:
    return (text or "").rstrip().endswith("?")


# --- the field ------------------------------------------------------------------------------------------------
_OF: dict = {}
TALK_STREAMS = ("discourse",)
NOTE_KEYS = ("seq", "session", "speaker", "role", "text", "stream")


def _who(ev: dict) -> str:
    spk = ev.get("speaker") or []
    return str(spk[1]) if len(spk) > 1 and spk[1] else str(ev.get("role") or "")


_LOCK = __import__("threading").Lock()


LEDGER_COLL, LEDGER_KEY = "config", "replies_ledger"


def _load_ledger(field, R) -> None:
    """The ledger saved in the field: it survives restarts (replaying predictions over the history instead costs
    minutes of CPU on a seat-sized talk)."""
    try:
        doc = field.b.get(LEDGER_COLL, LEDGER_KEY) or {}
        for k, v in doc.get("c") or []:
            R.ledger.c[tuple(k)] = [int(x) for x in v]
    except Exception:
        pass


def _save_ledger(field, R) -> None:
    try:
        field.b.upsert(LEDGER_COLL, LEDGER_KEY, {"c": [[list(k), list(v)] for k, v in R.ledger.c.items()]})
    except Exception:
        pass


def _read(field, st) -> None:
    """The history, once, at the first build: every talk message in event order, counted (not predicted: the
    ledger comes from the field, where every live prediction is saved)."""
    R = st["R"]
    _load_ledger(field, R)
    rows = sorted(field.b.scan("events", {"origin": field.origin}), key=lambda kd: kd[1].get("seq") or 0)
    for _k, ev in rows:
        if ev.get("status") == "pending":
            continue                                # not embedded yet: it arrives through note() when it is
        if ev.get("stream") not in TALK_STREAMS or not (ev.get("text") or "").strip():
            continue
        st["seq"] = max(st["seq"], int(ev.get("seq") or 0))
        R.add(str(ev.get("session") or ""), _who(ev), ev.get("text") or "", predict=False)


def note(field, ev: dict) -> None:
    """efield.embed_pending calls this as it writes an embedded event: queued (only the fields used), read at
    the next of(). Before the first build starts nothing is kept -- the build reads the history itself."""
    st = _OF.get(id(field))
    if st is not None and ev.get("stream") in TALK_STREAMS:
        with st["plock"]:
            st["pending"].append({k: ev.get(k) for k in NOTE_KEYS})


_HELD: dict = {}


def hold(field) -> None:
    """A forget / rebuild is running (they may nest): no build until all are done; a banner meanwhile keeps the
    pre-#942 order."""
    with _LOCK:
        _HELD[id(field)] = _HELD.get(id(field), 0) + 1
        _OF.pop(id(field), None)


def reset(field) -> None:
    """A forget / rebuild is done: drop the models; once none runs, the next of() builds them again from what
    is left."""
    with _LOCK:
        d = _HELD.get(id(field), 0) - 1
        if d > 0:
            _HELD[id(field)] = d
        else:
            _HELD.pop(id(field), None)
        _OF.pop(id(field), None)


def _drain(st) -> None:
    """The queued messages after the last one read, in order; each predicted (the ledger), then counted, once
    (the build's watermark). The ledger is saved when it changed."""
    with st["plock"]:
        q, st["pending"] = st["pending"], []
    R = st["R"]
    before = R.ledger.c.get((), [0, 0, 0, 0, 0])[4]
    for ev in sorted(q, key=lambda e: int(e.get("seq") or 0)):
        sq = int(ev.get("seq") or 0)
        if sq <= st["seq"] or not (ev.get("text") or "").strip():
            continue
        st["seq"] = sq
        R.add(str(ev.get("session") or ""), _who(ev), ev.get("text") or "", predict=True)
    if R.ledger.c.get((), [0, 0, 0, 0, 0])[4] != before:
        _save_ledger(st["field"], R)


def _state(field) -> dict:
    import threading
    import time
    with _LOCK:
        st = _OF.get(id(field))
        if st is not None and st.get("failed"):
            _OF.pop(id(field), None)                # a failed build is tried again
            st = None
        if st is None:
            st = {"R": Replies(), "seq": -1, "field": field, "built": False, "failed": False,
                  "done": threading.Event(), "pending": [], "plock": threading.Lock(), "lock": threading.Lock()}
            _OF[id(field)] = st

            def build():
                t0 = time.monotonic()
                try:
                    with st["lock"]:
                        _read(field, st)
                        st["built"] = True
                    import sys
                    print(f"[replies] built from {st['R'].n} messages in {time.monotonic() - t0:.1f} s", file=sys.stderr)
                except Exception as e:                   # never serve partial tables: try again next time
                    st["failed"] = True
                    import sys
                    print(f"[replies] build failed ({e}); trying again at the next banner", file=sys.stderr)
                finally:
                    st["done"].set()
            threading.Thread(target=build, name="replies-build", daemon=True).start()
    return st


def of(field, wait: bool = True) -> Replies | None:
    """The field's reply models. The first build reads the history (counted) and the saved ledger; with wait=False it
    runs in the background and None comes back until it is done. After that each call adds only the messages
    the field handed over (note), never a scan. None after a failed build (tried again at the next call)."""
    if _HELD.get(id(field)):
        return None
    st = _state(field)
    if not st["done"].is_set():
        if not wait:
            return None
        st["done"].wait()
    if not st["built"]:
        return None
    with st["lock"]:
        _drain(st)
    return st["R"]


def ready(field, timeout: float | None = None) -> bool:
    """Start the first build if needed and wait up to `timeout` for it."""
    st = _state(field)
    return bool(st["done"].wait(timeout) and st["built"])


def annotate(field, rows: list, text: str, wait: bool = False) -> list:
    """The banner's rows with the three scores (listener, said, about) against the incoming message, scored
    under the models' lock (a drain can't change the tables mid-score). Rows come back unchanged -- without
    the three keys, so the banner uses its pre-#942 weights -- while the models are building, after a failed
    build, or on any error."""
    try:
        R = of(field, wait=wait)
        if R is None:
            return rows
        st = _OF[id(field)]
        out = []
        with st["lock"]:
            for r in rows:
                ans = " ".join(" ".join(x.split()) for x in (r.get("answer") or []))
                who = str(r.get("speaker") or "")
                out.append(dict(r, listener=R.listener(text, who, ans), said=R.said(text, who, ans),
                                about=R.about(text, ans)))
        return out
    except Exception as e:                          # the models never break recall
        import sys
        print(f"[replies] annotate failed ({e})", file=sys.stderr)
        return rows
