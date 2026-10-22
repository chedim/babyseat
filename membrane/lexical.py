"""THE EXACT-WORD LANE (#429): a small BM25 index over the field's own
sentences and tool-output chunks, next to the embeddings.

Embeddings blur exact tokens: an identifier (MEMBRANE_JWT_SECRET), a
license name (PolyForm), an issue number (#277) or a file name
(seat-token) lands near everything that sounds like it. This index finds
the rows that carry the very word. It lives in RAM only: built from the
events (already in RAM) at boot, on a background thread, and kept current
by capture (efield.embed_pending and the chunker call `add_event` /
`add_chunk`). Nothing is stored; a restart rebuilds it.

A document is one sentence of one event (its sid, which the event already
holds) or one tool-output chunk (a tool result's own observation line is
left out: its chunks carry the output). Postings are packed ints (doc id << 4 |
term count), so the index costs a few bytes per (word, sentence) pair.

The constants below await the operator's approval (#429)."""

from __future__ import annotations

import math
import re
import sys
import threading
import time
from array import array

# BM25 (Robertson/Sparck Jones; Lucene's defaults, borrowed): K1 saturates
# a repeated word, B scales by sentence length.
K1 = 1.2
B = 0.75
# A query word in more than this share of all documents is no exact-word
# evidence (it is the embeddings' job): skipped. Measured on a field copy.
DF_MAX_FRAC = 0.01
# ...but never below this many documents (a young field: with few rows a
# share of them is a handful). A guess; it binds under 2,000 sentences.
DF_MIN = 20
# Lucene's English stop set (EnglishAnalyzer.ENGLISH_STOP_WORDS_SET, 33
# words), borrowed: never indexed, never searched.
STOPWORDS = frozenset((
    "a an and are as at be but by for if in into is it no not of on or such that the "
    "their then there these they this to was will with").split())
# A token longer than this is a blob (base64, a minified line): not indexed.
MAX_TOKEN = 64
TF_CAP = 15                                         # 4 bits in a posting

_COMPOUND = re.compile(r"[a-z0-9]+(?:[_.\-/:][a-z0-9]+)*")
_PART = re.compile(r"[a-z0-9]+")


def _stem(w: str) -> str:
    """Lucene's EnglishMinimalStemFilter (plural -> singular), borrowed."""
    n = len(w)
    if n < 3 or w[-1] != "s" or not w.isalpha():
        return w
    if w.endswith("ies") and not w.endswith(("eies", "aies")) and n > 3:
        return w[:-3] + "y"
    if w.endswith("es") and not w.endswith(("aes", "ees", "oes")):
        return w[:-1]
    if not w.endswith(("us", "ss")):
        return w[:-1]
    return w


def tokens(text: str) -> list[str]:
    """Lower-cased words; a compound (snake_case, a-b, a.b, a/b) counts as
    itself, its path segments and its parts, so 'MEMBRANE_JWT_SECRET' and
    'jwt secret' both find it. Plurals fold to the singular."""
    out = []
    for m in _COMPOUND.finditer((text or "").lower()):
        c = m.group(0)
        if len(c) > MAX_TOKEN:
            continue
        if c.isalnum():
            if c not in STOPWORDS:
                out.append(_stem(c))
            continue
        out.append(c)
        if "/" in c:
            out += [s for s in c.split("/") if s and not s.isalnum()]
        out += [_stem(p) for p in _PART.findall(c) if p not in STOPWORDS]
    return out


_WRITTEN = re.compile(r"[A-Za-z0-9]+(?:[_.\-/:][A-Za-z0-9]+)*")


def exact_words(text: str) -> set:
    """The ask's words written as names, not prose: with a digit or a
    separator (MEMBRANE_JWT_SECRET, #277, seat-token, self_loop.py), or a
    capital past the first letter (PolyForm, MIT). A row that matches one
    of these is recalled past the similarity floor."""
    out = set()
    for m in _WRITTEN.finditer(text or ""):
        w = m.group(0)
        if len(w) > MAX_TOKEN:
            continue
        if not w.isalpha() or (len(w) > 1 and any(c.isupper() for c in w[1:])):
            out.update(tokens(w)[:1])
    return out


class LexIndex:
    def __init__(self):
        self.lock = threading.Lock()
        self.post: dict[str, object] = {}           # word -> int (one posting) | array('I')
        self.lens = array("I")                      # doc id -> token count
        self.ref: list = []                         # doc id -> sid / chunk id (shared string)
        self.ev: list = []                          # doc id -> event key (shared string)
        self.seen: set = set()                      # event keys / chunk ids indexed
        self.total = 0
        self.ready = False

    def __len__(self) -> int:
        return len(self.lens)

    def _add(self, ref, ev_key, text: str) -> None:
        toks = tokens(text)
        did = len(self.lens)
        self.lens.append(len(toks))
        self.ref.append(ref)
        self.ev.append(ev_key)
        self.total += len(toks)
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        post = self.post
        for t, c in tf.items():
            p = (did << 4) | min(c, TF_CAP)
            cur = post.get(t)
            if cur is None:
                post[t] = p
            elif isinstance(cur, int):
                post[t] = array("I", (cur, p))
            else:
                cur.append(p)

    def add_event(self, key: str, ev: dict, sents: list[str]) -> None:
        """One event's sentences (zipped with its sids), once per event. A
        tool result's own line is not indexed: its chunks are."""
        sids = ev.get("sids") or []
        if ev.get("stream") == "observation":
            return
        with self.lock:
            if key in self.seen:
                return
            self.seen.add(key)
            for sid, s in zip(sids, sents):
                self._add(sid, key, s)

    def add_chunk(self, cid: str, ev_key: str, text: str) -> None:
        with self.lock:
            if cid in self.seen:
                return
            self.seen.add(cid)
            self._add(cid, ev_key, text)

    def df(self, word: str) -> int:
        p = self.post.get(word)
        return 0 if p is None else 1 if isinstance(p, int) else len(p)

    def _ids(self, word: str) -> list[int]:
        p = self.post.get(word)
        return [] if p is None else [p >> 4] if isinstance(p, int) else [x >> 4 for x in p]

    def docs_with(self, words) -> list[int]:
        """The documents that carry every one of `words`."""
        with self.lock:
            docs = None
            for w in words:
                ids = set(self._ids(w))
                docs = ids if docs is None else docs & ids
            return sorted(docs or ())

    def search(self, query: str, n: int | None = None, events=None,
               drop=()) -> list[tuple[int, float]]:
        """(doc id, BM25) best first over the query's words that are rare
        (df within the cap) and not in `drop`; with `events` (a set of
        event keys), only their documents."""
        with self.lock:
            N = len(self.lens)
            if not N:
                return []
            cap = max(DF_MIN, int(DF_MAX_FRAC * N))
            avg = self.total / N if self.total else 1.0
            acc: dict[int, float] = {}
            for w in dict.fromkeys(tokens(query)):
                if w in drop:
                    continue
                p = self.post.get(w)
                if p is None:
                    continue
                ps = (p,) if isinstance(p, int) else p
                df = len(ps)
                if df > cap:
                    continue
                idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
                lens, norm = self.lens, K1 * (1 - B)
                for x in ps:
                    i, tf = x >> 4, x & 15
                    if events is not None and self.ev[i] not in events:
                        continue
                    acc[i] = acc.get(i, 0.0) + idf * tf * (K1 + 1) / (
                        tf + norm + K1 * B * lens[i] / avg)
            out = sorted(acc.items(), key=lambda t: -t[1])
            return out[:n] if n is not None else out


_BUILDING = object()
_LOCK = threading.Lock()


def build(field) -> LexIndex:
    """Index every event of this origin (sentences re-split from the event
    itself, so nothing is read from the cold sentence store) and its tool
    chunks."""
    ix = LexIndex()
    for key, ev in list(field.b.scan("events", {"origin": field.origin})):
        if not ev.get("sids") or ev.get("status") == "forgotten":
            continue
        try:
            ix.add_event(key, ev, field._sentences_of(ev))
        except Exception:
            continue
    try:
        for cid, row in list(field.b.scan("chunks", None)):
            if row.get("event"):
                ix.add_chunk(cid, row["event"], row.get("text") or "")
    except Exception:
        pass
    ix.ready = True
    return ix


def _install(field, ix: LexIndex) -> None:
    """The built index goes live, with what capture noted meanwhile."""
    with _LOCK:
        for args in getattr(field, "_lex_pending", None) or ():
            (ix.add_chunk if len(args) == 3 else ix.add_event)(*args)
        field._lex_pending = []
        field._lex = ix


def index_of(field) -> LexIndex | None:
    """The field's word index: built here on first use (a lab field, a
    tool), or None while the boot's background build runs (the lane
    sits that turn out)."""
    ix = getattr(field, "_lex", None)
    if ix is _BUILDING:
        return None
    if ix is None:
        with _LOCK:
            if getattr(field, "_lex", None) is None:
                field._lex = _BUILDING
                started = True
            else:
                started = False
        if not started:
            return index_of(field)
        try:
            _install(field, build(field))
        except Exception:
            field._lex = None
            raise
    return field._lex


def warm(field) -> threading.Thread | None:
    """The seat's boot: build on a background thread, so the first turns
    never wait (they run without the word lane until it is ready)."""
    with _LOCK:
        if getattr(field, "_lex", None) is not None:
            return None
        field._lex = _BUILDING

    def run():
        t = time.time()
        try:
            ix = build(field)
        except Exception as e:
            field._lex = None
            print(f"[efield] word index failed ({e})", file=sys.stderr)
            return
        _install(field, ix)
        print(f"[efield] word index: {len(ix)} sentences, {len(ix.post)} words "
              f"in {time.time() - t:.1f}s", file=sys.stderr)

    th = threading.Thread(target=run, name="efield-words", daemon=True)
    th.start()
    return th


def note_event(field, key: str, ev: dict, sents: list[str]) -> None:
    """Capture's hook: a newly embedded event's sentences, if an index is
    live (or queued while the boot builds it)."""
    ix = getattr(field, "_lex", None)
    if ix is None:
        return
    with _LOCK:
        ix = field._lex
        if ix is _BUILDING:
            field._lex_pending = (getattr(field, "_lex_pending", None) or []) + [(key, ev, sents)]
            return
    ix.add_event(key, ev, sents)


def note_chunk(field, cid: str, ev_key: str, text: str) -> None:
    ix = getattr(field, "_lex", None)
    if ix is None:
        return
    with _LOCK:
        ix = field._lex
        if ix is _BUILDING:
            field._lex_pending = (getattr(field, "_lex_pending", None) or []) + [(cid, ev_key, text)]
            return
    ix.add_chunk(cid, ev_key, text)
