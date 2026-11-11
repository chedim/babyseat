"""INSTANCE HISTORIES WITH THE TENSE RULE (0.7.52; bench arms 92e/92f + 91's class ballots, 2026-11-07), over the
WHOLE field: every chat, every person, tool outputs.

  parse      each event's text is read ONCE, when it lands (a background worker; spaCy en_core_web_sm, its own
             pretrained parser -- nothing trained here): its noun phrases, its content lemmas, the places it names,
             who said it. The row is kept with the field (annotations, `_k: "ih"`); a restart loads the rows and
             parses only events past the done-marker that have none (the background backfill)
  phrases    each noun head: its ladder (head <- head + nearest kind modifier ...), its owner (my / your / his /
             Caroline's / my cousin's), and whether it names one thing: "refers" (an owner, a number, the/this/
             that), "new" (THE TENSE RULE: its clause tells one happening -- the verb is past, a participle, or has
             a past auxiliary), or the class (None: "dogs are loyal")
  instances  built as each row is kept (capture order): an owned or definite mention joins the latest instance with
             its head (any owner for "the X"), unless the two carry modifiers and share none; a "new" one starts an
             instance. Speakers' names are never things
  ballot     the question's phrases (parsed at ask time: the question only, and only once spaCy is loaded --
             never a load inside a turn) match instances by head: 1 + its modifier words matched (rarity log(N/df),
             0 for a word no event holds, x nearness: the same word or a shared lemma 1, else the stored seeds'
             cosine) + 1 for the same owner (the question's my = the asker, his/her = the one person it names);
             the best instances' lines, weighted by how narrow they are (log(events / events in lines) / log(events))
  vote       Borda of the person timelines, the place ballot, current recall (points 20 - i) and the instance
             ballot x its weight: the "instance histories" voter (recall_vote.py)
  indexes    every row lands in the indexes as it is kept (who said it, its lemmas, its places, the instances):
             an ask reads postings, never every row

  time       rebuilt without training: each event's MOMENTS, computed once in the same parse -- each DATE / TIME
             phrase spaCy finds, placed by dateparser from when the event was said (facts.span_of); no phrase: when
             it was said. A question's own DATE / TIME words name a span (infer.own_span: the date as fine as it
             stays fixed when "now" changes); the ballot is the events with a moment in it. The original read the
             trained readers' TIME labels. Without dateparser the time ballot is skipped

Dropped from the bench (no training ships): the learned readers' statement web (the person / place / time ballots
order their events by the question's rare lemmas instead); the pronoun joins (they rode the #932 refers-to edges,
not on main).
"""
from __future__ import annotations

import bisect
import math
import queue
import sys
import threading

DEPTH = 20
KIND_MODS = ("amod", "compound", "nmod")
DEFINITE = ("the", "this", "that", "these", "those")
FIRST = ("i", "my", "our", "mine", "me", "we")
SECOND = ("your", "you", "yours")
THIRD = ("his", "her", "their", "its")
WH = ("what", "who", "whom", "whose", "which", "when", "where", "why", "how")
PLACE_LABELS = ("GPE", "LOC", "FAC")
TIME_LABELS = ("DATE", "TIME")
_PERIOD = (("year", "year"), ("month", "month"), ("week", "week"))
_BASES = (1.0e9, 1.61e9)    # two "now"s (Sep 2001, Jan 2021) differing in year, month and day: what stays is named
PLACE_NEAR = 0.5           # a statement's place this near the question's (the bench's rule)
TEXT_MAX = 4000            # chars of an event read (a tool output's head); approved by the operator 2026-11-11
DONE_KEY = "ihdone:"       # annotations: the parse backfill's high-water mark, per origin
NLP_LOCK = threading.Lock()   # every spaCy call here: the parse worker and the ask (blis, see language.py)
_SAID: set = set()


def nlp(load: bool = False):
    """spaCy's English model (language.py manages it) when it is loaded, else None; load=True loads it (the parse
    worker only -- never a turn)."""
    try:
        from . import language as LG
        if load:
            return LG._nlp()
        return LG._NLP.get("m") or None
    except Exception:
        return None


def _say_once(msg: str) -> None:
    if msg not in _SAID:
        _SAID.add(msg)
        print(f"[instances] {msg}", file=sys.stderr)


# ---- reading one text ---------------------------------------------------------------------------
def _mods(tok):
    out = []
    for m in sorted([c for c in tok.children if c.dep_ in KIND_MODS], key=lambda c: -c.i):
        ws = [x for x in m.subtree if x.dep_ not in ("det", "poss", "case", "punct") and x.pos_ != "PRON"]
        if ws:
            out.append([x.lemma_.lower() for x in sorted(ws, key=lambda x: x.i)])
    return out


def _owner(tok):
    """'@1' (I/my), '@2' (you/your), '@3' (his/her), a noun owner with its own ('@1's cousin'), or None."""
    for c in tok.children:
        if c.dep_ != "poss":
            continue
        w = c.text.lower()
        if w in FIRST:
            return "@1"
        if w in SECOND:
            return "@2"
        if w in THIRD:
            return "@3"
        inner = _owner(c)
        return (inner + "'s " if inner else "") + c.lemma_.lower()
    return None


def _episodic(tok) -> bool:
    """THE TENSE RULE: the noun's clause tells one happening -- its verb is past, a participle, or has a past
    auxiliary (a present-simple or modal clause is generic)."""
    v = tok.head
    while v.pos_ not in ("VERB", "AUX") and v.head.i != v.i:
        v = v.head
    if v.pos_ not in ("VERB", "AUX"):
        return False
    return v.tag_ in ("VBD", "VBN", "VBG") or any(a.dep_ in ("aux", "auxpass") and a.tag_ == "VBD" for a in v.children)


def phrases(doc) -> list:
    """[(ladder, owner, 'refers' | 'new' | None, modifier words)] for each noun head."""
    out = []
    for tok in doc:
        if tok.pos_ not in ("NOUN", "PROPN") or tok.dep_ == "compound":
            continue
        ladder = [tok.lemma_.lower()]
        mods = _mods(tok)
        for m in mods:
            ladder.append(" ".join(m) + " " + ladder[-1])
        own = _owner(tok)
        if own is not None or any(c.dep_ == "nummod" or (c.dep_ == "det" and c.text.lower() in DEFINITE)
                                  for c in tok.children):
            particular = "refers"
        elif _episodic(tok):
            particular = "new"
        else:
            particular = None
        out.append((ladder, own, particular, [w for m in mods for w in m]))
    return out


def _lemmas(doc) -> list:
    return list(dict.fromkeys(t.lemma_.lower() for t in doc
                              if t.is_alpha and not t.is_stop and t.text.lower() not in WH))


def read(doc) -> dict:
    return {"phr": [[a, b, c, d] for a, b, c, d in phrases(doc)], "lemmas": _lemmas(doc),
            "places": list(dict.fromkeys(e.text.lower() for e in doc.ents if e.label_ in PLACE_LABELS))}


def _text_of(ev: dict) -> str:
    if ev.get("stream") == "observation":
        t = " ".join(x for x in (ev.get("result_head"), ev.get("result_tail")) if x)
    else:
        t = ev.get("text") or ""
    return str(t)[:TEXT_MAX]


def _who(ev: dict) -> str:
    spk = ev.get("speaker") or []
    return str(spk[1]).lower() if len(spk) > 1 and spk[1] else ""


# ---- time (the bench's facts.span_of / infer.own_span / time_fits) ------------------------------------------
def have_dateparser() -> bool:
    try:
        import dateparser  # noqa: F401
        return True
    except Exception:
        return False


def span_of(phrase: str, base: float):
    """(start, end, period) a time phrase points to, from when it was said (dateparser); None when it can't."""
    try:
        import datetime as dt
        import dateparser
        got = dateparser.parse(phrase, settings={"RELATIVE_BASE": dt.datetime.fromtimestamp(base),
                                                 "PREFER_DATES_FROM": "past"})
    except Exception:
        return None
    if got is None:
        return None
    low = phrase.lower()
    per = next((p for w, p in _PERIOD if w in low), "day")
    start = got.replace(hour=0, minute=0, second=0, microsecond=0)
    if per == "year":
        start = start.replace(month=1, day=1)
    elif per == "month":
        start = start.replace(day=1)
    days = {"year": 365, "month": 31, "week": 7, "day": 1}[per]
    return (start.timestamp(), start.timestamp() + days * 86400, per)


_LEADS = ("last ", "this ", "past ", "previous ")


def place_phrase(phrase: str, base: float):
    """span_of, tolerant of what spaCy hands over: a leading "last / this" dateparser does not read ("last May" is
    the May before, which PREFER_DATES_FROM=past already gives) and words the entity ran into ("May Caroline")
    -- the phrase, then without its lead, each trimmed from the end until one places."""
    words = str(phrase or "").split()
    tries = [words]
    if words and (words[0].lower() + " ") in _LEADS:
        tries.append(words[1:])
    for ws in tries:
        for end in range(len(ws), 0, -1):
            sp = span_of(" ".join(ws[:end]), base)
            if sp is not None:
                return sp
    return None


def own_span(phrase: str):
    """(start, end, period) of the date a phrase names by itself, as fine as it stays fixed when "now" changes."""
    import datetime as dt
    got = [span_of(phrase, b) for b in _BASES] if str(phrase or "").strip() else [None]
    if not all(got):
        return None
    a, b = (dt.datetime.fromtimestamp(g[0]) for g in got)
    if (a.year, a.month, a.day) == (b.year, b.month, b.day):
        return got[0]
    if (a.year, a.month) == (b.year, b.month):
        start = a.replace(day=1)
        nxt = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
        return (start.timestamp(), nxt.timestamp(), "month")
    if a.year == b.year:
        return (a.replace(month=1, day=1).timestamp(), a.replace(year=a.year + 1, month=1, day=1).timestamp(), "year")
    return None


def time_fits(span, moment) -> bool:
    return moment is not None and span[0] <= float(moment) < span[1]


def moments_of(doc, ts) -> list:
    """An event's moments: each DATE / TIME phrase placed from when it was said; none placed: when it was said."""
    if ts is None:
        return []
    got = []
    for e in doc.ents:
        if e.label_ in TIME_LABELS:
            sp = place_phrase(e.text, float(ts))
            if sp is not None and sp[0] not in got:
                got.append(sp[0])
    return got or [float(ts)]


def lemmas(word: str) -> set:
    """The bench's lemma set (facts.lemmas): the word, its noun lemma, its verb lemma."""
    w = str(word).lower().strip("'-")
    out = {w}
    try:
        from lemminflect import getLemma
        for upos in ("NOUN", "VERB"):
            got = getLemma(w, upos)
            out.add(got[0] if got else w)
    except Exception:
        pass
    return out - {""}


def _clash(a, b) -> bool:
    return bool(a) and bool(b) and not (set(a) & set(b))


def _resolve(own, me: str, you: str, them: str) -> str | None:
    """'@1' -> me, '@2' -> you, '@3' -> them: a field row is (its speaker, the one it answers, ''), a question is
    (the asker, '', the one person it names) -- the bench's E.phrases(doc, me, you, them)."""
    if own is None:
        return None
    for mark, who in (("@1", me), ("@2", you), ("@3", them)):
        if own == mark:
            return who
        if own.startswith(mark + "'s "):
            rest = own[len(mark) + 3:]
            return (who + "'s " if who else "") + rest
    return own


# ---- the kept rows and their indexes ------------------------------------------------------------
class Store:
    def __init__(self, field):
        self.field = field
        self.lock = threading.RLock()
        self.rows: dict = {}          # event key -> row
        self.pos: dict = {}           # event key -> its place in capture order
        self.df: dict = {}            # lemma -> events holding it
        self.by_who: dict = {}        # speaker -> [keys]
        self.by_lemma: dict = {}      # lemma -> [keys]
        self.by_place: dict = {}      # place -> [keys]
        self.by_moment: list = []     # sorted [(moment, key)]
        self.names: set = set()
        self.inst: dict = {}          # instance key -> {ladder, owner, mods, line}
        self.by_head: dict = {}       # head -> [instance keys], latest last
        self._last_who: dict = {}     # chat -> its latest speaker
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.upto = 0                 # the highest seq kept
        self.gen = 0

    def keep(self, key: str, row: dict, persist: bool = True) -> None:
        with self.lock:
            if key in self.rows:
                return
            self.rows[key] = row
            self.pos[key] = len(self.pos)
            for w in row.get("lemmas") or []:
                self.df[w] = self.df.get(w, 0) + 1
                self.by_lemma.setdefault(w, []).append(key)
            who = row.get("who") or ""
            if who:
                self.names.add(who)
                self.by_who.setdefault(who, []).append(key)
            for pl in row.get("places") or []:
                self.by_place.setdefault(pl, []).append(key)
            for m in row.get("moments") or ([row["ts"]] if row.get("ts") is not None else []):
                bisect.insort(self.by_moment, (float(m), key))
            self._absorb(key, row)
            self.upto = max(self.upto, int(row.get("seq") or 0))
            self.gen += 1
        if persist:
            try:
                self.field.b.upsert("annotations", f"ih:{key}",
                                    dict(row, _k="ih", origin=self.field.origin, event=key))
            except Exception as e:
                _say_once(f"a row was not kept ({type(e).__name__})")

    def _latest(self, head, mods, owner):
        for k in reversed(self.by_head.get(head, [])):
            it = self.inst[k]
            if (owner is None or it["owner"] == owner) and not _clash(it["mods"], mods):
                return k
        return None

    def _absorb(self, key: str, r: dict) -> None:
        """92f's build, one row at a time (capture order)."""
        me = r.get("who") or ""
        chat = r.get("chat")
        prev = self._last_who.get(chat, "")
        you = prev if prev != me else ""
        if me:
            self._last_who[chat] = me
        n = r.get("n")
        for j, (ladder, own, particular, mods) in enumerate(r.get("phr") or []):
            head = ladder[0]
            if head in self.names or not particular:
                continue
            own = _resolve(own, me, you, "")
            k = self._latest(head, mods, own) if particular == "refers" else None
            if k is None:
                k = (own or "", ladder[-1], key, j)
                self.inst[k] = {"ladder": list(ladder), "owner": own or "", "mods": list(mods), "line": [n]}
            else:
                it = self.inst[k]
                if len(ladder) > len(it["ladder"]):
                    it["ladder"] = list(ladder)
                it["mods"] = list(dict.fromkeys(it["mods"] + list(mods)))
                if it["line"][-1] != n:
                    it["line"].append(n)
                self.by_head[head].remove(k)
            self.by_head.setdefault(head, []).append(k)

    def parse(self, items: list) -> None:
        """[(key, light event)] -> rows, one pipe call under the spaCy lock."""
        m = nlp(load=True)
        if m is None:
            return
        texts = [_text_of(ev) for _k, ev in items]
        with NLP_LOCK:
            docs = list(m.pipe(texts, batch_size=64))
        for (key, ev), doc in zip(items, docs):
            row = read(doc)
            row.update(n=ev.get("n"), chat=str(ev.get("session") or ""), seq=int(ev.get("seq") or 0),
                       ts=ev.get("ts"), who=_who(ev), moments=moments_of(doc, ev.get("ts")))
            self.keep(key, row)

    def _run(self) -> None:
        while True:
            batch = [self.q.get()]
            try:
                while len(batch) < 64:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            try:
                self.parse(batch)
                if self.q.empty():                     # caught up: the next boot scans only past this seq
                    self.field.b.upsert("annotations", DONE_KEY + self.field.origin,
                                        {"_k": "ihdone", "origin": self.field.origin, "upto": self.upto})
            except Exception as e:
                _say_once(f"parse failed ({type(e).__name__}: {e})")
            finally:
                for _ in batch:
                    self.q.task_done()


def _light(ev: dict) -> dict:
    return {k: ev.get(k) for k in ("n", "session", "seq", "ts", "speaker", "stream", "role", "text",
                                   "result_head", "result_tail", "status")}


def _wanted(ev: dict) -> bool:
    return bool(_text_of(ev).strip()) and ev.get("status") != "forgotten"


def store_of(field) -> Store | None:
    return getattr(field, "_instance_store", None)


def attach(field, backfill: bool = True) -> Store:
    """Hook the field: every event that lands is queued for one parse (a background worker), the kept rows load
    (capture order), and (backfill) events past the done-marker with no row are queued too. Idempotent."""
    st = store_of(field)
    if st is not None:
        return st
    st = field._instance_store = Store(field)
    kept = [d for _k, d in field.b.scan("annotations", {"origin": field.origin, "_k": "ih"}) if d.get("event")]
    kept.sort(key=lambda d: int(d.get("seq") or 0))
    for d in kept:
        st.keep(d["event"], {k: v for k, v in d.items() if k not in ("_k", "origin", "event")}, persist=False)

    def heard(key, ev):
        if key not in st.rows and _wanted(ev):
            st.q.put((key, _light(ev)))
    field.event_listeners.append(heard)
    if backfill:
        mark = field.b.get("annotations", DONE_KEY + field.origin)
        eq = {"origin": field.origin}
        if mark:
            eq["seq"] = {"gt": int(mark.get("upto") or 0)}
        todo = [(k, d) for k, d in sorted(field.b.scan("events", eq), key=lambda kd: int(kd[1].get("seq") or 0))
                if k not in st.rows and d.get("status") == "applied" and _wanted(d)]
        for k, d in todo:
            st.q.put((k, _light(d)))
    st.worker = threading.Thread(target=st._run, name="instances-parse", daemon=True)
    st.worker.start()
    return st


def drain(field) -> None:
    """Wait until every queued event is parsed (tests; the seat never waits)."""
    st = store_of(field)
    if st is not None:
        st.q.join()


def instances(field) -> dict:
    st = store_of(field)
    return st.inst if st is not None else {}


# ---- the ballots --------------------------------------------------------------------------------
def _near_fn(field):
    """1 for the same word or a shared lemma; else the cosine of the two words' STORED seeds (the field's term
    cache: never the embedder, never a write); a word with no stored seed is near nothing."""
    import numpy as np
    from .efstore import unpack_f16
    cache: dict = {}

    def vec(w):
        if w not in cache:
            v = None
            try:
                d = field.b.get("terms", field._term_key(w))
                if d and d.get("vec") is not None:
                    v = unpack_f16(d["vec"])
                    v = v / (np.linalg.norm(v) + 1e-12)
            except Exception:
                v = None
            cache[w] = v
        return cache[w]

    def near(a, b) -> float:
        if a == b or (lemmas(a) & lemmas(b)):
            return 1.0
        va, vb = vec(a), vec(b)
        return max(0.0, float(np.dot(va, vb))) if va is not None and vb is not None else 0.0
    return near


def _idf(st, w: str) -> float:
    """log(events / events holding w); 0 for a word no event holds (the bench's idf.get(w, 0.0))."""
    c = st.df.get(w, 0)
    return math.log(max(1, len(st.rows)) / c) if c else 0.0


def _scored(st, keys: set, want: list) -> list:
    """Event ns from `keys`, ordered by the question's lemmas they hold (summed rarity; ties: capture order), read
    from the lemma postings -- never every row."""
    sc: dict = {}
    for w in want:
        a = _idf(st, w)
        if a <= 0:
            continue
        for k in st.by_lemma.get(w, ()):
            if k in keys:
                sc[k] = sc.get(k, 0.0) + a
    hit = sorted(sc, key=lambda k: (-sc[k], st.pos[k]))
    rest = sorted((k for k in keys if k not in sc), key=lambda k: st.pos[k])[:DEPTH] if len(hit) < DEPTH else []
    return [st.rows[k].get("n") for k in (hit + rest)[:DEPTH]]


def question(text: str):
    """The question parsed (under the spaCy lock), or None while spaCy is not loaded (never loaded in a turn)."""
    m = nlp()
    if m is None:
        return None
    with NLP_LOCK:
        return m(text)


def ballot(field, text: str, asker: str = "", qdoc=None) -> tuple[list, float]:
    """(event ns, weight): the instance ballot for a question."""
    st = store_of(field)
    qdoc = qdoc if qdoc is not None else question(text)
    if st is None or qdoc is None:
        return [], 0.0
    near = _near_fn(field)
    with st.lock:
        names = set(st.names)
        named = [w.text.lower() for w in qdoc if w.text.lower() in names]
        them = named[0] if len(set(named)) == 1 else ""
        n_all = max(1, len(st.rows))
        score: dict = {}
        for ladder, own, _part, mods in phrases(qdoc):
            if ladder[0] in names:
                continue
            own = _resolve(own, (asker or "").lower(), "", them)
            for k in st.by_head.get(ladder[0], ()):
                it = st.inst[k]
                sc = 1.0 + sum(_idf(st, a) * max((near(a, b) for b in it["mods"]), default=0.0) for a in mods)
                if own and it["owner"] and own.split("'s ")[0] in it["owner"]:
                    sc += 1.0
                score[k] = max(score.get(k, -1e9), sc)
        out, used = [], set()
        for k in sorted(score, key=lambda k: -score[k]):
            used |= set(st.inst[k]["line"])
            for d in st.inst[k]["line"]:
                if d not in out:
                    out.append(d)
            if len(out) >= DEPTH:
                break
    n_in = len(used)
    w = math.log(n_all / n_in) / math.log(n_all) if n_in and n_all > 1 else 0.0
    return out[:DEPTH], w


def time_ballot(field, qdoc, want: list | None = None):
    """The events with a moment inside the span the question's own DATE / TIME words name (event ns, best first),
    or None: no time words, no span, or no dateparser (skipped, logged once). Reads the moment index's range."""
    st = store_of(field)
    if st is None or qdoc is None:
        return None
    tw = " ".join(e.text for e in qdoc.ents if e.label_ in TIME_LABELS)
    if not tw:
        return None
    if not have_dateparser():
        _say_once("dateparser is not installed: the time ballot is skipped")
        return None
    span = own_span(tw)
    if span is None:
        return None
    with st.lock:
        lo = bisect.bisect_left(st.by_moment, (span[0], ""))
        keys = set()
        for m, k in st.by_moment[lo:]:
            if m >= span[1]:
                break
            keys.add(k)
        if not keys:
            return []
        want = want if want is not None else [w for w in _lemmas(qdoc) if w not in st.names]
        return _scored(st, keys, want)


def class_ballots(field, text: str, current: list, qdoc=None, people_cues=()) -> list:
    """91's class ballots without training, from the indexes, in 91's order: each person the question names -- by
    name, or a person: thing the field knows -- (their timeline: what they said and what names them), the time it
    names (events with a moment in its span), the places it names (statements with a place near), current recall."""
    st = store_of(field)
    qdoc = qdoc if qdoc is not None else question(text)
    out = []
    if st is not None and qdoc is not None:
        near = _near_fn(field)
        with st.lock:
            names = set(st.names)
            people = {t.text.lower() for t in qdoc if t.text.lower() in names} | \
                {p for p in (str(x).lower() for x in people_cues) if p}
            want = [w for w in _lemmas(qdoc) if w not in people]
            for p in sorted(people):
                mine = set(st.by_who.get(p, ())) | set(st.by_lemma.get(p, ()))
                if mine:
                    out.append(_scored(st, mine, want))
            tb = time_ballot(field, qdoc, want)                       # 91's order: person, time, place
            if tb:
                out.append(tb)
            places = [e.text.lower() for e in qdoc.ents if e.label_ in PLACE_LABELS]
            if places:
                got: set = set()
                for b, ks in st.by_place.items():
                    if any(near(a, b) >= PLACE_NEAR for a in places):
                        got |= set(ks)
                if got:
                    out.append(_scored(st, got, want))
    out.append(list(current)[:DEPTH])
    return out


def vote(field, text: str, current: list, asker: str = "", people_cues=()):
    """The "instance histories" voter: Borda of the class ballots (points 20 - i) and the instance ballot x its
    weight, its top 20 (event ns). None while spaCy is not loaded (the vote then runs without this voter)."""
    qdoc = question(text)
    if qdoc is None:
        return None
    pts: dict = {}
    for b in class_ballots(field, text, current, qdoc, people_cues):
        for i, d in enumerate(b[:DEPTH]):
            if d is not None:
                pts[d] = pts.get(d, 0) + (DEPTH - i)
    mine, w = ballot(field, text, asker, qdoc)
    for i, d in enumerate(mine[:DEPTH]):
        if d is not None:
            pts[d] = pts.get(d, 0) + (DEPTH - i) * w
    return sorted(pts, key=lambda d: -pts[d])[:DEPTH]
