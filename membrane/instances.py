"""INSTANCE HISTORIES WITH THE TENSE RULE (0.7.52; bench arms 92e/92f + 91's class ballots, 2026-11-07), over the
WHOLE field: every chat, every person, tool outputs.

  parse      each event's text is read ONCE, when it lands (a background worker; spaCy en_core_web_sm, its own
             pretrained parser -- nothing trained here): its noun phrases, its content lemmas, the places it names,
             who said it. The row is kept with the field (annotations, `_k: "ih"`); a restart loads the rows and
             parses only events that have none (the background backfill)
  phrases    each noun head: its ladder (head <- head + nearest kind modifier ...), its owner (my / your / his /
             Caroline's / my cousin's), and whether it names one thing: "refers" (an owner, a number, the/this/
             that), "new" (THE TENSE RULE: its clause tells one happening -- the verb is past, a participle, or has
             a past auxiliary), or the class (None: "dogs are loyal")
  instances  built from the kept rows in field order: an owned or definite mention joins the latest instance with
             its head (any owner for "the X"), unless the two carry modifiers and share none; a "new" one starts an
             instance. Speakers' names are never things
  ballot     the question's phrases (parsed at ask time: the question only) match instances by head: 1 + its
             modifier words matched (rarity x nearness) + 1 for the same owner; the best instances' lines,
             weighted by how narrow they are (log(events / events in the lines) / log(events))
  vote       Borda of the person timelines, the place ballot, current recall (points 20 - i) and the instance
             ballot x its weight: the "instance histories" voter (recall_vote.py)

Dropped from the bench (no training ships): the learned readers' statement web (the person / place ballots order
their events by the question's rare lemmas instead), the time ballot (it read the readers' TIME labels and the
facts' moments), the pronoun joins (they rode the #932 refers-to edges, not on main).
"""
from __future__ import annotations

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
PLACE_NEAR = 0.5           # a statement's place this near the question's (the bench's rule)
TEXT_MAX = 4000            # chars of an event read (a tool output's head)
_SAID: set = set()


def nlp():
    """spaCy's English model (language.py manages it), or None."""
    try:
        from . import language as LG
        return LG._nlp()
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


# ---- the kept rows ------------------------------------------------------------------------------
class Store:
    def __init__(self, field):
        self.field = field
        self.lock = threading.RLock()
        self.rows: dict = {}          # event key -> row
        self.order: list = []         # keys in the order they were kept
        self.df: dict = {}            # lemma -> events holding it
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.gen = 0

    def keep(self, key: str, row: dict, persist: bool = True) -> None:
        with self.lock:
            if key in self.rows:
                return
            self.rows[key] = row
            self.order.append(key)
            for w in row.get("lemmas") or []:
                self.df[w] = self.df.get(w, 0) + 1
            self.gen += 1
        if persist:
            try:
                self.field.b.upsert("annotations", f"ih:{key}",
                                    dict(row, _k="ih", origin=self.field.origin, event=key))
            except Exception as e:
                _say_once(f"a row was not kept ({type(e).__name__})")

    def parse(self, items: list) -> None:
        """[(key, light event)] -> rows, one pipe call."""
        m = nlp()
        if m is None:
            return
        texts = [_text_of(ev) for _k, ev in items]
        for (key, ev), doc in zip(items, m.pipe(texts, batch_size=64)):
            row = read(doc)
            row.update(n=ev.get("n"), chat=str(ev.get("session") or ""), seq=int(ev.get("seq") or 0),
                       ts=ev.get("ts"), who=_who(ev))
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
    """Hook the field: every event that lands is queued for one parse (a background worker), the kept rows load,
    and (backfill) events with no row are queued too. Idempotent."""
    st = store_of(field)
    if st is not None:
        return st
    st = field._instance_store = Store(field)
    for _k, d in field.b.scan("annotations", {"origin": field.origin, "_k": "ih"}):
        if d.get("event"):
            st.keep(d["event"], {k: v for k, v in d.items() if k not in ("_k", "origin", "event")}, persist=False)

    def heard(key, ev):
        if key not in st.rows and _wanted(ev):
            st.q.put((key, _light(ev)))
    field.event_listeners.append(heard)
    if backfill:
        todo = [(k, d) for k, d in sorted(field.b.scan("events", {"origin": field.origin}),
                                          key=lambda kd: int(kd[1].get("seq") or 0))
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


# ---- instances ----------------------------------------------------------------------------------
def _clash(a, b) -> bool:
    return bool(a) and bool(b) and not (set(a) & set(b))


def _resolve(own, me: str, you: str) -> str | None:
    if own is None:
        return None
    for mark, who in (("@1", me), ("@2", you), ("@3", "")):
        if own == mark:
            return who
        if own.startswith(mark + "'s "):
            rest = own[len(mark) + 3:]
            return (who + "'s " if who else "") + rest
    return own


def instances(field) -> dict:
    """key -> {ladder, owner, mods, line: [event n, ...]}, built from the kept rows in field order (cached until a
    row lands)."""
    st = store_of(field)
    if st is None:
        return {}
    cache = getattr(st, "_inst", None)
    if cache is not None and cache[0] == st.gen:
        return cache[1]
    with st.lock:
        keys = sorted(st.order, key=lambda k: (st.rows[k].get("seq") or 0))
        rows = [st.rows[k] for k in keys]
    names = {r.get("who") for r in rows if r.get("who")}
    inst, order, by_head = {}, [], {}
    last_who: dict = {}

    def latest(head, mods, owner):
        for k in reversed(by_head.get(head, [])):
            it = inst[k]
            if (owner is None or it["owner"] == owner) and not _clash(it["mods"], mods):
                return k
        return None
    for key, r in zip(keys, rows):
        me = r.get("who") or ""
        prev = last_who.get(r.get("chat"), "")
        you = prev if prev != me else ""
        if me:
            last_who[r.get("chat")] = me
        n = r.get("n")
        for j, (ladder, own, particular, mods) in enumerate(r.get("phr") or []):
            head = ladder[0]
            if head in names or not particular:
                continue
            own = _resolve(own, me, you)
            k = latest(head, mods, own) if particular == "refers" else None
            if k is None:
                k = (own or "", ladder[-1], key, j)
                inst[k] = {"ladder": list(ladder), "owner": own or "", "mods": list(mods), "line": [n]}
            else:
                it = inst[k]
                if len(ladder) > len(it["ladder"]):
                    it["ladder"] = list(ladder)
                it["mods"] = list(dict.fromkeys(it["mods"] + list(mods)))
                if it["line"][-1] != n:
                    it["line"].append(n)
                by_head[head].remove(k)
            by_head.setdefault(head, []).append(k)
    st._inst = (st.gen, inst)
    return inst


# ---- the ballots --------------------------------------------------------------------------------
def _near_fn(field):
    import numpy as np
    from .efield import unit
    cache: dict = {}

    def vec(w):
        if w not in cache:
            try:
                cache[w] = unit(field._seed(w))
            except Exception:
                cache[w] = None
        return cache[w]

    def near(a, b) -> float:
        if a == b:
            return 1.0
        va, vb = vec(a), vec(b)
        return max(0.0, float(np.dot(va, vb))) if va is not None and vb is not None else 0.0
    return near


def _scored(st, keys, want: list) -> list:
    """Events ordered by the question's rare lemmas they hold (rarity log(N / df)); ties keep field order."""
    n = max(1, len(st.rows))
    idf = {w: math.log(n / max(1, st.df.get(w, 0))) for w in want}
    sc = [(sum(idf[w] for w in want if w in set(st.rows[k].get("lemmas") or ())), i, k) for i, k in enumerate(keys)]
    return [st.rows[k].get("n") for s, _i, k in sorted(sc, key=lambda t: (-t[0], t[1]))][:DEPTH]


def ballot(field, text: str) -> tuple[list, float]:
    """(event ns, weight): the instance ballot for a question."""
    st, m = store_of(field), nlp()
    if st is None or m is None:
        return [], 0.0
    inst = instances(field)
    with st.lock:
        rows = dict(st.rows)
        n_all = max(1, len(rows))
        df = dict(st.df)
    names = {r.get("who") for r in rows.values() if r.get("who")}
    qdoc = m(text)
    named = [w.text.lower() for w in qdoc if w.text.lower() in names]
    them = named[0] if len(set(named)) == 1 else ""
    near = _near_fn(field)
    score: dict = {}
    for ladder, own, _part, mods in phrases(qdoc):
        if ladder[0] in names:
            continue
        own = _resolve(own, "", them)
        for k, it in inst.items():
            if it["ladder"][0] != ladder[0]:
                continue
            sc = 1.0 + sum(math.log(n_all / max(1, df.get(a, 0))) * max((near(a, b) for b in it["mods"]), default=0.0)
                           for a in mods)
            if own and it["owner"] and own.split("'s ")[0] in it["owner"]:
                sc += 1.0
            score[k] = max(score.get(k, -1e9), sc)
    out, used = [], set()
    for k in sorted(score, key=lambda k: -score[k]):
        used |= set(inst[k]["line"])
        for d in inst[k]["line"]:
            if d not in out:
                out.append(d)
        if len(out) >= DEPTH:
            break
    n_in = len(used)
    w = math.log(n_all / n_in) / math.log(n_all) if n_in and n_all > 1 else 0.0
    return out[:DEPTH], w


def class_ballots(field, text: str, current: list) -> list:
    """91's class ballots that need no training: each person the question names (their timeline: what they said
    and what names them), the places it names (statements with a place near), and current recall."""
    st, m = store_of(field), nlp()
    out = []
    if st is not None and m is not None:
        with st.lock:
            keys = sorted(st.order, key=lambda k: (st.rows[k].get("seq") or 0))
        names = {st.rows[k].get("who") for k in keys if st.rows[k].get("who")}
        qdoc = m(text)
        people = sorted({t.text.lower() for t in qdoc if t.text.lower() in names})
        want = [w for w in _lemmas(qdoc) if w not in names]
        for p in people:
            mine = [k for k in keys if st.rows[k].get("who") == p or p in (st.rows[k].get("lemmas") or ())]
            if mine:
                out.append(_scored(st, mine, want))
        places = [e.text.lower() for e in qdoc.ents if e.label_ in PLACE_LABELS]
        if places:
            near = _near_fn(field)
            got = [k for k in keys if any(near(a, b) >= PLACE_NEAR for a in places
                                          for b in st.rows[k].get("places") or ())]
            if got:
                out.append(_scored(st, got, want))
    out.append(list(current)[:DEPTH])
    return out


def vote(field, text: str, current: list) -> list:
    """The "instance histories" voter: Borda of the class ballots (points 20 - i) and the instance ballot x its
    weight, its top 20 (event ns)."""
    pts: dict = {}
    for b in class_ballots(field, text, current):
        for i, d in enumerate(b[:DEPTH]):
            if d is not None:
                pts[d] = pts.get(d, 0) + (DEPTH - i)
    mine, w = ballot(field, text)
    for i, d in enumerate(mine[:DEPTH]):
        if d is not None:
            pts[d] = pts.get(d, 0) + (DEPTH - i) * w
    return sorted(pts, key=lambda d: -pts[d])[:DEPTH]
