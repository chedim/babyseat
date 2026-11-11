"""THE 🧩 ON RECALL (#932, the design adopted 2026-11-05): recall finds; the 🧩 reads the facts of the
messages recall ranked and speaks from the best one, using the graph only where its edges are reliable --
time and order (the spine's moments and threads), timeline (real or possible) and speaker.

  1. the question, read by the structure reader: its blocks, the block it asks for (learned from how
     questions like it were answered; else what / who / where / when / how / why), the people it names by
     name, whether it asks about what is possible;
  2. the candidates: the facts of recall's messages, in recall's order. Preferences, never gates: a fact
     holding the asked block first, then one said by a person the question names, then one on the
     question's kind of timeline; recall's order among equals;
  3. order on a thread (first / now / latest / currently / between, joined by or / and): the facts of the
     thing the question asks about, in moment order -- its earliest, its latest, or the days between two;
  4. speaking: a "when" says the fact's period; otherwise the fact's words in the asked block (the SRL
     encoder's head for that role, when it is on disk, widened to the reader's run), then the fact as said.
  5. reasoning (2026-11-06): a reply is read with the turns it answers (context: its message's follows edges,
     back over the messages that say something), so a fact says the words of the turn before it; resolve: a
     pronoun in a fact's block takes that block's words from the turn its message answers ("we did it
     yesterday" after "did you go on the hike?": it = hike), for finding and for speaking; and join by role:
     from its fact the 🧩 goes to the pool's fact holding a shared thing (one of the fact's or the question's
     rare words) in the question's asked block, and on (JOIN_DEPTH facts in all), next in the ranking.
  6. the answer check (2026-11-06, the operator: yes/no tests, not weights): each candidate fact is asked
     whether it holds the asked part, whether the person the question names said it or is in it, and whether its
     moment falls in a date the question names by itself ("in July 2023"; "yesterday" names none), and whether
     its asked part MEANS what the question says -- its in-sentence word vectors near the question's content
     words', at or above the median of the pool's candidates (the cut-off from the data, as keyword's); facts passing
     more tests come first, ties in the pool's order. part: say the asked part only; fuse: join the asked parts
     of the facts that pass every test, from different messages, up to JOIN_DEPTH.
Nothing fits: "I don't know". A question writes nothing.
"""
from __future__ import annotations

import re

from . import facts as F
from . import structures as STR

LAST: dict = {}
WH = {"when": "TIME", "where": "PLACE", "how": "MANNER", "why": "CAUSE", "who": "DOER", "whom": "OBJ",
      "what": "OBJ", "which": "OBJ"}
ORDER = {"first": "first", "now": "last", "latest": "last", "currently": "last", "recent": "last",
         "between": "between"}
JOIN = {"or", "and"}
JOIN_DEPTH = 3          # facts in a join chain; cliffhanger's depth (approved 2026-11-06)
PRONOUNS = {"it", "this", "that", "these", "those", "they", "them", "there", "one", "he", "she", "him", "her"}
# ^ the words resolve replaces (a closed word table like WH; flagged for the operator)


_RARE: dict = {}


def _rare(word: str) -> bool:
    return bool(_RARE.get("st") and _RARE["st"].keyword(word))


def _people(est) -> set:
    return {str(f.get("speaker") or "").lower() for f in F.clauses(est) if f.get("speaker")}


def read_question(srv, text: str, learned_only: bool = False) -> dict:
    st = srv.structures
    r = next((x for x in st.read(text) if any(l.startswith("WH_") for l in x["labels"])), None) or \
        next(iter(st.read(text)), None)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    low = [w.lower() for w in words]
    wh = next((w for w in low if w in WH), None)
    asked = None
    if r is not None:
        taught = st.asked_for(r["labels"], r["words"])
        asked = {"SELF": "DOER"}.get(taught, taught)
    if learned_only:                                     # 2026-11-06: the learned asked part only, no WH table
        pass
    elif wh in ("when", "where", "why") or asked is None:
        asked = WH.get(wh) or asked
    if wh == "how" and r is not None and not learned_only:                    # how long / often: a time, as the reader reads it
        i = next((k for k, w in enumerate(r["words"]) if w.lower() == "how"), None)
        if i is not None and i + 1 < len(r["labels"]) and r["labels"][i + 1] == "TIME":
            asked = "TIME"
    people = _people(srv.entity_store)
    named = {re.sub(r"'s?$", "", w.lower()) for w in words if w[:1].isupper()} & people
    keys = [w for w in words if w.lower() not in WH and w.lower() not in ORDER and w.lower() not in JOIN
            and re.sub(r"'s?$", "", w.lower()) not in people and st.keyword(w)]
    return {"reading": r, "asked": asked, "wh": wh, "named": named, "keys": keys, "words": words,
            "possible": bool(r and any(r["possible"].values())),
            "order": next((ORDER[w] for w in low if w in ORDER), None),
            "join": next((k for k, w in enumerate(low) if w in JOIN), None)}


_CTX: dict = {}


def context(srv, msg: str, n: int) -> list:
    """The n messages before `msg` in its chat that say something, nearest first: what a reply replies to."""
    if not n or not msg:
        return []
    if (msg, n) in _CTX:
        return _CTX[(msg, n)]
    from .convgraph import _out, _rel
    be, origin = _rel(srv)
    saying = _CTX.get("saying")
    if saying is None:
        saying = _CTX["saying"] = {f.get("msg") for f in F.clauses(srv.entity_store) if f.get("text")}
    out, cur, seen = [], msg, {msg}
    while len(out) < n:
        nxt = next((r["obj"] for r in _out(be, origin, cur, "follows") if r.get("obj") not in seen), None)
        if nxt is None:
            break
        seen.add(nxt)
        if nxt in saying:
            out.append(nxt)
        cur = nxt
    _CTX[(msg, n)] = out
    return out


def _by_msg(est) -> dict:
    out: dict = {}
    for g in F.clauses(est):
        out.setdefault(g.get("msg"), []).append(g)
    return out


_RES: dict = {}


def resolved(srv, f: dict) -> list:
    """[(pronoun, its words, block)]: each pronoun of the fact, by the same block's words in the turn it answers."""
    if f.get("eid") in _RES:
        return _RES[f["eid"]]
    pros = [(w, l) for w, l in (tuple(x) for x in f.get("words") or []) if w.lower() in PRONOUNS and l != "DET"]
    prev = context(srv, f.get("msg"), 1) if pros else []
    out = []
    if prev:
        before = sorted(_by_msg(srv.entity_store).get(prev[0], []), key=lambda g: g["eid"])
        for w, l in pros:
            for g in before:
                run = [x for x, b in (tuple(y) for y in g.get("words") or [])
                       if b == l and x.lower() not in PRONOUNS and x.lower() not in WH]
                if run:
                    out.append((w, run, l))
                    break
    _RES[f.get("eid")] = out
    return out


def _sayer(srv, n: int, resolve: bool = False):
    """says(f, word): the fact names the word; or (n > 0) a fact of the n turns its message answers does; or
    (resolve) a pronoun of the fact stands for it."""
    if not n and not resolve:
        return F.names
    by_msg = _by_msg(srv.entity_store) if n else {}

    def says(f, w):
        if F.names(f, w):
            return True
        if resolve and any(F.lemmas(w) & F.lemmas(x) for _p, run, _l in resolved(srv, f) for x in run):
            return True
        return bool(n) and any(F.names(g, w) for m in context(srv, f.get("msg"), n) for g in by_msg.get(m, []))
    return says


def binding(srv, text: str, facts: list, df: dict | None = None, n: int | None = None) -> dict:
    """THE BINDING TEST (2026-11-06, the operator: a natural field answer-reader, no model): {fact eid: score}. A fact
    binds the question when (a) this clause itself names the question's rare words -- each by its rarity among all
    facts, log(facts / facts naming it) -- and it (b) holds the asked part, (c) with a filler the question does not
    say. Passing (b) and (c) is worth more than any coverage: score = coverage + (the largest possible coverage, when
    it passes); a question echo (no new filler) ranks below the answer."""
    import math
    q = read_question(srv, text)
    est = srv.entity_store
    qwords = [w for w in q["words"] if w.lower() not in WH and w.lower() not in ORDER and w.lower() not in JOIN]
    if df is None or n is None:                          # how many facts name each word (a caller may pass its index)
        every = F.clauses(est)
        n = len(every)
        df = {w: sum(1 for g in every if F.names(g, w)) for w in qwords}
    n = max(1, n)
    df = {w: df.get(w, 0) for w in qwords}
    rare = {w: math.log(n / df[w]) for w in qwords if df[w]}
    top = sum(rare.values()) or 1.0
    asked = {"DOER": ("DOER", "SELF")}.get(q["asked"], (q["asked"],))
    qlem = {lem for w in q["words"] for lem in F.lemmas(w)}
    out = {}
    for f in facts:
        cov = sum(r for w, r in rare.items() if F.names(f, w))
        holds = bool(q["asked"]) and _holds(q, f)
        filler = [w for w, l in (tuple(x) for x in f.get("words") or []) if l in asked]
        new = any(not (F.lemmas(w) & qlem) for w in filler) or (q["asked"] == "TIME" and bool(f.get("span") or f.get("side"))
                                                                    and not filler)
        out[f["eid"]] = cov + (top if holds and new else 0.0)
    return out


def resolve_facts(srv) -> int:
    """UNDERSTANDING AT HEARING TIME (2026-11-06): every fact, in the order said, stored with its pronouns resolved
    through the turn its message answers ("we did it yesterday" after "did you go on the hike?" -> "...did the
    hike yesterday"): its words and text carry the antecedent, `said_as` keeps what was said. Once per fact."""
    from .entities import QuantumEntity
    est = srv.entity_store
    facts = sorted(F.clauses(est), key=lambda f: (float(f.get("said") or 0), f["eid"]))
    _RES.clear()
    _CTX.pop("saying", None)
    n = 0
    for f in facts:
        if f.get("said_as") is not None:
            continue
        res = resolved(srv, f)
        if not res:
            continue
        words = [list(x) for x in f.get("words") or []]
        for p, run, lab in res:
            i = next((j for j, (w, _l) in enumerate(words) if w == p), None)
            if i is not None:
                words[i:i + 1] = [[w, lab] for w in run]
        doc = (est.docs or {}).get(f["eid"])
        if doc is None:
            continue
        props = dict(doc.properties or {})
        props.update({"words": words, "text": resolve_text(srv, f), "said_as": f.get("text") or ""})
        est.upsert(QuantumEntity(eid=f["eid"], cls=doc.cls, properties=props), embedder=None)
        cur = (est.docs or {}).get(f["eid"])
        if cur is not None:                                # an upsert MERGES (old keys win): set them outright
            cur.properties = {**(cur.properties or {}), **props}
        n += 1
    _RES.clear()
    return n


def resolve_text(srv, f: dict) -> str:
    """The fact as said, each resolved pronoun replaced by its words."""
    text = f.get("text") or ""
    for p, run, _l in resolved(srv, f):
        text = re.sub(rf"\b{re.escape(p)}\b", " ".join(run), text, count=1, flags=re.I)
    return text


def _join(est, q: dict, ranked: list, first: dict) -> list:
    """Join by role: from `first`, the pool's fact (another message's) holding the most of the last fact's or the
    question's rare words IN the question's asked block (a shared thing in the asked role), and on."""
    people = _people(est)
    asked = {"DOER": ("DOER", "SELF")}.get(q.get("asked"), (q.get("asked"),))
    chain = [first]
    while len(chain) < JOIN_DEPTH:
        last = chain[-1]
        rare = {w.lower() for w, _l in last.get("words") or [] if re.sub(r"'s?$", "", w.lower()) not in people
                and _rare(w) and w.lower() not in WH} | {w.lower() for w in q.get("keys") or []}
        used = {c.get("msg") for c in chain}
        best, top = None, 0
        for g in ranked:
            if g.get("msg") in used:
                continue
            role = [x for x, b in (tuple(y) for y in g.get("words") or []) if b in asked]
            k = sum(1 for w in rare if any(F.lemmas(w) & F.lemmas(x) for x in role))
            if k > top:
                best, top = g, k
        if best is None:
            break
        chain.append(best)
    return chain


_BASES = (1.0e9, 1.61e9)    # two "now"s (Sep 2001, Jan 2021) differing in year, month and day: what stays is named


def own_span(phrase: str):
    """(start, end, period) of the date a phrase names by itself, as fine as it stays fixed when "now" changes."""
    import datetime as dt
    got = [F.span_of(phrase, b) for b in _BASES] if str(phrase or "").strip() else [None]
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


def time_fits(span, f: dict) -> bool:
    m = f.get("moment") if f.get("moment") is not None else f.get("said")
    return m is not None and span[0] <= float(m) < span[1]


def _holds(q: dict, f: dict) -> bool:
    if q["asked"] == "TIME":
        return bool(f.get("span") or f.get("side")) or "TIME" in _blocks(f)
    return q["asked"] in _blocks(f)


def _content(q: dict) -> set:
    r = q.get("reading")
    if not r:
        return set()
    return {w.lower() for w, l in zip(r["words"], r["labels"]) if l in ("DOER", "SELF", "OBJ", "PLACE") + STR.VERBS
            and w.lower() not in WH and w.lower() not in ORDER and re.sub(r"'s?$", "", w.lower()) not in q["named"]}


def _mean_vec(st, text: str, want: set, space: bool = False):
    import numpy as np
    try:
        words, V = st.words([text])[0]
    except Exception:
        return None
    if space:                                            # in the hole maps' space (raw or leftover)
        V = st.hole_vecs(words, V)
        if V is None:
            return None
    idx = [i for i, w in enumerate(words) if str(w).lower() in want]
    if not idx:
        return None
    v = np.mean(np.asarray(V, dtype=np.float32)[idx], axis=0)
    return v / (np.linalg.norm(v) + 1e-12)


def _hole_vec(st, q: dict):
    """The question's hole: its asked block's meaning as predicted from its other words (structures' learned map)."""
    r = q.get("reading")
    if not r or not q["asked"]:
        return None
    if getattr(st, "holes", None) is None:
        st.learn_holes()
    try:
        words, vecs = st.words([r.get("text") or " ".join(r["words"])])[0]
    except Exception:
        return None
    if len(words) != len(r["labels"]):
        return None
    return st.hole(words, vecs, r["labels"], {"SELF": "DOER"}.get(q["asked"], q["asked"]))


def _near(st, q: dict, text: str, facts: list, hole: bool = False) -> dict:
    """{id(fact): how near its asked part (else its content) sits to the question's content words -- or (hole) to
    the question's predicted hole -- by in-sentence meaning}; empty when there is nothing to compare."""
    want = _content(q)
    qv = None
    if hole and q["asked"] and q["asked"] != "TIME":
        qv = _hole_vec(st, q)
    if qv is None:
        qv = _mean_vec(st, text, want) if want and q["asked"] and q["asked"] != "TIME" else None
    if qv is None:
        return {}
    asked = {"DOER": ("DOER", "SELF")}.get(q["asked"], (q["asked"],))
    sims = {}
    for f in facts:
        part = {w.lower() for w, l in (tuple(x) for x in f.get("words") or []) if l in asked} or \
            {w.lower() for w, l in (tuple(x) for x in f.get("words") or []) if l in ("DOER", "OBJ", "PLACE")}
        fv = _mean_vec(st, f.get("text") or "", part, space=hole) if part else None
        if fv is not None:
            sims[id(f)] = float(fv @ qv)
    return sims


def _meaning(st, q: dict, text: str, facts: list, sims: dict | None = None) -> set:
    """The facts at least as near the question as the pool's median (the meaning test's passes, ids)."""
    sims = _near(st, q, text, facts) if sims is None else sims
    if not sims:
        return {id(f) for f in facts}
    vals = sorted(sims.values())
    median = vals[len(vals) // 2]                     # the upper half passes (the middle one, when odd)
    return {k for k, v in sims.items() if v >= median}


def _checks(q: dict, f: dict, span, near: set | None = None) -> int:
    """How many of the question's yes/no tests the fact fails."""
    bad = 0
    if near is not None and id(f) not in near:
        bad += 1
    if q["asked"] and not _holds(q, f):
        bad += 1
    if q["named"] and str(f.get("speaker") or "").lower() not in q["named"] \
            and not any(F.names(f, n) for n in q["named"]):
        bad += 1
    if span is not None and not time_fits(span, f):
        bad += 1
    return bad


def _question_span(q: dict):
    r = q.get("reading")
    tw = " ".join(w for w, l in zip(r["words"], r["labels"]) if l == "TIME") if r else ""
    return own_span(tw) if tw else None


def _blocks(f: dict) -> set:
    return {"DOER" if l == "SELF" else ("VERB" if l in STR.VERBS else l) for _w, l in f.get("words") or []}


def _ranked(est, q: dict, pool: list, prefer: bool = True, fit_top: int = 0, says=F.names) -> list:
    """The facts of the pool's messages, best first by the preferences, recall's order among equals (prefer=False:
    the pool's own order, its first fact holding the asked block first within a message)."""
    by_msg: dict = {}
    for f in F.clauses(est):
        by_msg.setdefault(f.get("msg"), []).append(f)
    cands = [(i, f) for i, m in enumerate(pool) for f in sorted(by_msg.get(m, []), key=lambda f: f["eid"])]
    # within a message, the fact that fits the question best (the operator 2026-11-05: not its first sentence,
    # often a greeting): how many of the question's words it says, the rare ones weighed more
    people = _people(est)
    qw = [w for w in q["words"] if w.lower() not in WH and w.lower() not in ORDER and w.lower() not in JOIN
          and re.sub(r"'s?$", "", w.lower()) not in people]   # a greeting naming the person fits nothing

    def fit(f):
        return sum((2 if _rare(w) else 1) for w in qw if says(f, w))

    def key(item):
        i, f = item
        if not prefer:
            return (i, -fit(f), q["asked"] not in _blocks(f) and q["asked"] != "TIME")
        holds = (bool(f.get("span") or f.get("side")) or "TIME" in _blocks(f)) if q["asked"] == "TIME" \
            else q["asked"] in _blocks(f)                 # a when: a fact that says a time
        return (bool(q["asked"]) and not holds,
                bool(q["named"]) and str(f.get("speaker") or "").lower() not in q["named"],
                bool(f.get("possible")) != q["possible"], i, -fit(f))
    out = [f for _i, f in sorted(cands, key=key)]
    if fit_top:                                           # across the first fit_top messages: the best-fitting fact
        head = [f for i, f in cands if i < fit_top]
        if head:
            best = max(head, key=lambda f: (fit(f), -pool.index(f["msg"])))
            out = [best] + [f for f in out if f is not best]
    return out


def _when_order(est, q: dict, ranked: list, pool: list, says=F.names) -> list:
    """A "when" question asks about one happening: the fact that SAYS it comes first (how many of the question's
    words it says, the rare ones weighed more), recall's order among equals -- a dated fact about another happening
    never wins for its date alone; its time is its own span, else the day it was said (F.period). A question about
    what happened (not a possible one) never takes a fact on the future side (a plan: "in the next few months")."""
    people = _people(est)
    qw = [w for w in q["words"] if w.lower() not in WH and w.lower() not in ORDER and w.lower() not in JOIN
          and re.sub(r"'s?$", "", w.lower()) not in people]
    place = {m: i for i, m in enumerate(pool)}

    def key(f):
        later = not q["possible"] and f.get("side") == "future"
        other = bool(q["named"]) and str(f.get("speaker") or "").lower() not in q["named"] \
            and not any(F.names(f, n) for n in q["named"])
        return (later, other, -sum((2 if _rare(w) else 1) for w in qw if says(f, w)),
                not _holds(q, f),                         # equal matches: the one that says its own time first
                place.get(f.get("msg"), len(place)))
    return sorted(ranked, key=key)


def _ordered(est, q: dict, ranked: list, says=F.names, within: list | None = None):
    """first / now / between on the threads of the things the question names (within: only those facts -- the
    candidates passing the answer check -- not the whole talk)."""
    every = within if within is not None else F.clauses(est)

    def thread(w):
        return sorted([f for f in every if F.names(f, w)], key=F.order_key)
    if q["order"] is None or not (q["keys"] or q["reading"]):
        return None
    if q["join"] is None or q["order"] == "last":
        # the thread of what the question is about: the facts that say all its content words (its doer, verb,
        # done-to, place), else the facts that say its rarest key word
        r = q["reading"]
        content = [w for w, l in zip(r["words"], r["labels"]) if l in ("DOER", "SELF", "OBJ", "PLACE") + STR.VERBS
                   and w.lower() not in WH and w.lower() not in ORDER] if r else []
        content = [w for w in content if re.sub(r"'s?$", "", w.lower()) not in _people(est)]
        th = sorted([f for f in every if content and all(says(f, w) for w in content)], key=F.order_key) \
            or thread(q["keys"][-1])
        if not th:
            return None
        return ("one", th[0] if q["order"] == "first" else th[-1])
    left = [w for w in q["keys"] if q["words"].index(w) < q["join"]]
    right = [w for w in q["keys"] if q["words"].index(w) > q["join"]]
    sides = []
    for side in (left[-2:], right[:2]):
        th = [f for w in side for f in thread(w)]
        if not th:
            return None
        sides.append((" ".join(side), min(th, key=F.order_key)))
    return ("two", sides)


def speak(f: dict, asked: str) -> str:
    """The fact's words in the asked block: the SRL head's run when the encoder is on disk, else the
    reader's; I / me / my as the speaker."""
    from . import srl
    pairs = STR.drop_tails(f.get("text") or "", [tuple(x) for x in f.get("words") or []])
    want = {"DOER": ("DOER", "SELF")}.get(asked, (asked,))
    idx = [i for i, (_w, l) in enumerate(pairs) if l in want]
    if srl.available():
        verb = next((i for i, (_w, l) in enumerate(pairs) if l in STR.VERBS), None)
        if verb is not None:
            hs = [i for i, b in srl.heads([w for w, _l in pairs], verb).items() if b == asked]
            if hs:                                        # the head, widened to the reader's run around it
                h = hs[0]
                lo = hi = h
                while lo > 0 and pairs[lo - 1][1] in want + ("DET",):
                    lo -= 1
                while hi + 1 < len(pairs) and pairs[hi + 1][1] in want:
                    hi += 1
                idx = list(range(lo, hi + 1))
    who = f.get("speaker") or ""
    out = []
    for i in idx:
        w = pairs[i][0]
        out.append(who if w.lower() in ("i", "me") and who else f"{who}'s" if w.lower() == "my" and who else w)
    return " ".join(out)


FALLBACK_BLOCKS = ("DOER", "SELF", "OBJ", "PLACE", "MANNER")   # the runs a fact lacking the asked part offers


def runs_of(pairs: list, want: tuple) -> list:
    """The runs of `want` blocks (a determiner just before one joins it): lists of indices."""
    out, cur = [], []
    for i, (_w, l) in enumerate(pairs):
        if l in want:
            if not cur and i > 0 and pairs[i - 1][1] == "DET":
                cur.append(i - 1)
            cur.append(i)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


_LEFT: dict = {}


def _leftover_of(st, sentence: str):
    """(tokens, the reader's leftover vectors): a word's part in its sentence, the sentence's context removed."""
    if sentence in _LEFT:
        return _LEFT[sentence]
    import numpy as np
    try:
        toks, V = st.words([sentence])[0]
    except Exception:
        _LEFT[sentence] = ([], None)
        return _LEFT[sentence]
    V = np.asarray(V, dtype=np.float32)
    A = st._alone_of(list(toks)) if len(toks) else None
    R = (st._R or {}).get("statement") if getattr(st, "_R", None) else None
    L = st._leftover(V, A, R["c"]) if (A is not None and R is not None) else STR._unit(V)
    _LEFT[sentence] = (list(toks), L)
    return _LEFT[sentence]


def _run_score(st, q: dict, text: str, words: list, idx: list) -> float:
    """How near a run sits to the question's content words, both in the leftover space."""
    import numpy as np
    want = _content(q)
    qt, QV = _leftover_of(st, q.get("_text") or "")
    st_t, SV = _leftover_of(st, text)
    if QV is None or SV is None or not want:
        return 0.0
    qi = [i for i, w in enumerate(qt) if str(w).lower() in want]
    run = {str(words[i]).lower() for i in idx}
    si = [i for i, w in enumerate(st_t) if str(w).lower() in run]
    if not qi or not si:
        return 0.0
    qv, sv = QV[qi].mean(0), SV[si].mean(0)
    return float(qv @ sv / ((np.linalg.norm(qv) * np.linalg.norm(sv)) + 1e-12))


def speak_best(st, q: dict, text: str, facts: list) -> str:
    """The asked part's run nearest the question, over every clause of the sentence (else its content runs)."""
    want = {"DOER": ("DOER", "SELF")}.get(q.get("asked"), (q.get("asked"),))
    cands = []
    for f in facts:
        pairs = [tuple(x) for x in f.get("words") or []]
        for idx in runs_of(pairs, want) or runs_of(pairs, FALLBACK_BLOCKS):
            cands.append((f, pairs, idx))
    if not cands:
        return ""
    if q.get("_person"):                                 # 2026-11-08: the run the answering person would say --
        R, qw = q["_person"]                             # their predicted reply's word weights, ties by nearness
        gen = {}

        def pscore(c):
            who = c[0].get("speaker") or ""
            if who not in gen:
                gen[who] = R.generated(qw, who)
            ws = [str(c[1][i][0]).lower() for i in c[2]]
            return sum(gen[who].get(w, 0.0) for w in ws) / len(ws) if ws else 0.0
        f, pairs, idx = max(cands, key=lambda c: (pscore(c), _run_score(st, q, c[0].get("text") or "",
                                                                         [w for w, _l in c[1]], c[2])))
    else:
        f, pairs, idx = max(cands, key=lambda c: _run_score(st, q, c[0].get("text") or "", [w for w, _l in c[1]], c[2]))
    who = f.get("speaker") or ""
    out = []
    for i in idx:
        w = pairs[i][0]
        out.append(who if w.lower() in ("i", "me") and who else f"{who}'s" if w.lower() == "my" and who else w)
    return " ".join(out)


def quote(f: dict) -> str:
    import time
    day = time.strftime("%-d %b %Y", time.localtime(float(f.get("said") or 0))) if f.get("said") else ""
    return f"{f.get('speaker') or ''}{' (' + day + ')' if day else ''}: “{f.get('text') or ''}”"


def ask(srv, sid: str, text: str, speaker: str | None = None, pool: list | None = None,
        prefer: bool = True, fit_top: int = 0, whole: bool = True, context: int = 0,
        join: bool = False, resolve: bool = False, check: bool = False, part: bool = False,
        fuse: bool = False, pick: str = "words", learned_only: bool = False, hole: bool = False,
        unit: str = "sentence", asker: str | None = None, order: str = "messages",
        kind=None, person: bool | str = False) -> list[str] | None:
    """The lines answering `text` from the facts of `pool` (recall's messages, best first). `kind` (a word -> bool
    test, "is this a kind of the asked X?"): RESPONSE SHAPING only (2026-11-07) -- the answer says the found message's
    words of that kind; what was found and its order never change."""
    est = srv.entity_store
    LAST.clear()
    if getattr(srv, "structures", None) is None:
        return None
    q = read_question(srv, text, learned_only)
    r0 = q.get("reading")
    if asker and r0 and "SELF" in (r0.get("labels") or []):  # 2026-11-06: "I / my" in a question is the asker
        q["named"] = set(q["named"]) | {str(asker).lower()}
    _RARE["st"] = srv.structures
    _CTX.pop("saying", None)                              # the talk may have grown since the last question
    _RES.clear()
    says = _sayer(srv, context, resolve)
    ranked = _ranked(est, q, list(pool or []), prefer, fit_top, says)
    recalled = list(ranked)                              # recall's own order (prefer=False: the pool's)
    if person:                                           # 2026-11-08 PER PERSON: the run the answering person would say
        # (speak_best, part="best"). The per-person MESSAGE pick was dropped (operator, 2026-11-08): as an equal vote
        # it overrode memspeak's checks (single-hop @1 0.38 -> 0.35, 0.24 with background); recall's graph element
        # carries the per-person signal into memspeak's pool instead.
        from . import replies as RP
        q["_person"] = (RP.of(est), RP.words(text))
    span = _question_span(q) if check else None
    if pick == "learned" and (getattr(srv.structures, "answers", None) or {}).get("W") is not None:
        sims = {id(f): srv.structures.answer_score(text, f.get("text") or "") for f in ranked}   # step C's map
    else:
        sims = _near(srv.structures, q, text, ranked, hole) if (check or pick in ("meaning", "learned")) else {}
    near = _meaning(srv.structures, q, text, ranked, sims) if check else None
    fails = {id(f): _checks(q, f, span, near) for f in ranked} if check else {}
    if check or pick in ("meaning", "learned"):          # passing more tests first; ties keep the order --
        place = {m: i for i, m in enumerate(dict.fromkeys(f["msg"] for f in ranked))}
        nth = {id(f): i for i, f in enumerate(ranked)}   # within a message, by meaning (pick) or as before
        by_fact = order == "facts" and pick in ("meaning", "learned")   # 2026-11-06: speaking on facts alone
        ranked = sorted(ranked, key=lambda f: (fails.get(id(f), 0), 0 if by_fact else place[f["msg"]],
                                               -sims.get(id(f), -9.0) if pick in ("meaning", "learned")
                                               else nth[id(f)], place[f["msg"]]))
    if q["asked"] == "TIME" and check and ranked:        # 2026-11-07 (bench arm 91e): a "when" is about one happening;
        chosen = _when_order(est, q, ranked, list(pool or []), says)[0]   # only it moves up, the rest keep
        ranked = [chosen] + [f for f in recalled if f is not chosen]       # recall's order
    LAST.update({"question": {k: v for k, v in q.items() if k != "reading"},
                 "ranking": list(dict.fromkeys(f["msg"] for f in ranked))})
    passing = [f for f in ranked if fails.get(id(f), 0) == 0] if check else None
    got = _ordered(est, q, ranked, says, passing or (ranked if check else None))
    if got and got[0] == "two":
        (ln, lf), (rn, rf) = got[1]
        LAST["facts"] = [lf, rf]
        LAST["ranking"] = list(dict.fromkeys([lf["msg"], rf["msg"]] + LAST["ranking"]))
        if q["order"] == "between":
            days = abs(float(rf["moment"]) - float(lf["moment"])) / 86400
            return [f"{round(days)} days ({F.period(lf)} → {F.period(rf)})", quote(lf), quote(rf)]
        early, late = ((ln, lf), (rn, rf)) if F.order_key(lf) <= F.order_key(rf) else ((rn, rf), (ln, lf))
        return [f"{early[0]} first ({F.period(early[1])}), then {late[0]} ({F.period(late[1])})",
                quote(early[1]), quote(late[1])]
    f = got[1] if got else (ranked[0] if ranked else None)
    if f is None:
        return ["I don't know"]
    if got:                                              # the thread's fact answers: it leads the ranking
        LAST["ranking"] = [f["msg"]] + [m for m in LAST["ranking"] if m != f["msg"]]
    LAST["facts"] = [f]
    if join:                                             # the shared thing's facts come next
        chain = _join(est, q, ranked, f)
        LAST["facts"] = chain
        LAST["ranking"] = list(dict.fromkeys([c["msg"] for c in chain] + LAST["ranking"]))
    if q["asked"] == "TIME":
        return [F.period(f), quote(f)]

    def say(g):
        if part == "best" and q["asked"]:                # the run nearest the question, across the sentence
            sib = [x for x in ranked if x.get("msg") == g.get("msg") and x.get("text") == g.get("text")] or [g]
            got = speak_best(srv.structures, {**q, "_text": text}, text, sib)
            if got:
                return got
        if unit == "clause":                             # the fact's own clause, not its whole sentence
            got = " ".join(w for w, _l in g.get("words") or [])
            if got:
                return got
        if part is True and q["asked"]:
            got = speak(g, q["asked"])
            if got:
                return got
        return (resolve_text(srv, g) if resolve else g.get("text")) if (whole or part) else \
            (speak(g, q["asked"]) if q["asked"] else "")
    said = say(f)
    if kind is not None and q["asked"] != "TIME":       # shaping: the words of the asked kind -- the found message's
        def answers(x):                                 # the found message first, then the facts of the person
            if x.get("msg") == f.get("msg"):           # the question names (said by or about them); naming no one,
                return True                            # the facts passing every test
            if q["named"]:
                return str(x.get("speaker") or "").lower() in q["named"] or any(F.names(x, n) for n in q["named"])
            return check and fails.get(id(x), 1) == 0
        answering = [x for x in ranked if x is not f and answers(x)]
        typed = [w for g in [f] + answering for w, _l in g.get("words") or [] if kind(w)]
        if typed:
            typed = list(dict.fromkeys(typed))
            said = ", ".join(typed[:-1]) + " and " + typed[-1] if len(typed) > 1 else typed[0]
    if fuse and check:                                   # the asked parts of every fact passing all the tests
        passing, seen = [f], {f.get("msg")}
        for g in ranked:
            if len(passing) >= JOIN_DEPTH:
                break
            if g.get("msg") not in seen and _checks(q, g, span, near) == 0 and g is not f:
                passing.append(g)
                seen.add(g.get("msg"))
        if _checks(q, f, span, near) == 0 and len(passing) > 1:
            parts = list(dict.fromkeys(x for x in (say(g) for g in passing) if x))
            said = ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else said
            LAST["facts"] = passing
            LAST["ranking"] = list(dict.fromkeys([g["msg"] for g in passing] + LAST["ranking"]))
    return [said or (f.get("text") or "I don't know"), quote(f)]
