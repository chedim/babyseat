"""MESSAGES AS RELATIONS ON THE GRAPH, READ BY OUR OWN READER (2026-11-06, the operator: "represent that web as a
cliffhanger graph we can query"; "our own reader, swap the reader" -- no parser at read time; the teacher only taught).

  statements  each CLAUSE the structure reader reads is one statement: its verb(s), and its parts' runs as role edges
              (doer -> agent, done-to -> obj, place, time -> when, manner, cause). The teacher's clause id is each
              word's governing verb, so the reader's learned clause split IS the verb binding. I / me / my -> the
              speaker, you -> the listener, we -> the speaker's group; other pronouns stay unresolved (ref:<word>).
              A clause the reader's possible axis reads as possible is on the speaker's possible branch (frames such
              as "thinking of ..." put what they frame there); a NEG word makes the statement negated. Questions
              are never asserted.
  pattern     a question, read by the question reader: its verb(s), its parts' words as roles, and a VARIABLE at the
              asked part (learned per question word, as memspeak reads it).
  answer      every statement scored by `near` role by role (verbs against verbs, each role's words against the
              statement's words in that role); the variable's filler of the best answers -- scored choice.
"""
from __future__ import annotations

SELF = {"i", "me", "my", "myself", "mine"}
YOU = {"you", "your", "yourself", "yours"}
WE = {"we", "us", "our", "ours", "ourselves"}
ROLE = {"DOER": "agent", "SELF": "agent", "OBJ": "obj", "PLACE": "place", "TIME": "when", "MANNER": "manner",
        "CAUSE": "cause"}


def _key(word: str, speaker: str, listener: str) -> str:
    w = str(word).lower()
    if w in SELF:
        return speaker.lower() or "i"
    if w in YOU:
        return listener.lower() or "you"
    if w in WE:
        return "we:" + (speaker.lower() or "i")
    return w


def _runs(words, labels, idx):
    """[(role, [word index])] runs of one part inside a clause's word indices (determiners join the run after them)."""
    out, cur, role = [], [], None
    for i in idx:
        r = ROLE.get(labels[i])
        if r is not None and (r == role or not cur):
            cur.append(i)
            role = r
        else:
            if cur and role:
                out.append((role, cur))
            cur, role = ([i], r) if r is not None else ([], None)
    if cur and role:
        out.append((role, cur))
    return out


def from_reading(reading: dict, speaker: str = "", listener: str = "", msg=None, said=None, verbs=(),
                 bound=None) -> list[dict]:
    """The statements of one sentence the reader read (`verbs`: the reader's verb labels). With `bound` (the binder's
    verb index per word), one statement per VERB of the words bound to it; else one per reader clause."""
    if not reading or reading.get("text", "").strip().endswith("?"):
        return []
    words, labels, clauses = reading["words"], reading["labels"], reading["clauses"]
    rclause = list(clauses)                                # the reader's clauses: where the possible axis was read
    if bound is not None and len(bound) == len(words):
        clauses = [b if b >= 0 else -1 - i for i, b in enumerate(bound)]
    out = []
    for c in sorted(set(clauses)):
        idx = [i for i, k in enumerate(clauses) if k == c]
        vs = [words[i].lower() for i in idx if labels[i] in verbs]
        if not vs:
            continue
        roles, surf = {}, {}
        for role, run in _runs(words, labels, idx):
            for i in run:
                roles.setdefault(role, []).append(_key(words[i], speaker, listener))
                surf.setdefault(role, []).append(reading.get("said", words)[i] if i < len(reading.get("said", words)) else words[i])
        out.append({"id": f"{msg}:{c}" if msg else str(c), "verbs": vs, "roles": roles, "surfaces": surf,
                    "neg": any(labels[i] == "NEG" for i in idx), "possible": bool((reading.get("possible") or {}).get(rclause[next((i for i in idx if labels[i] in verbs), idx[0])])),
                    "speaker": speaker, "msg": msg, "said": said, "text": reading.get("text", "")})
    return out


def pattern(reading: dict, asked: str | None, asker: str = "", wh=(), verbs=()) -> dict:
    """The question's statement with a variable at the asked part (`wh`: the question words to leave out); the asked
    part's own words ("awareness" in "raise awareness for") are kept as its context."""
    if not reading:
        return {}
    words, labels = reading["words"], reading["labels"]
    var = ROLE.get(asked or "", "obj")
    roles, vs, ctx = {}, [], []
    for w, l in zip(words, labels):
        if w.lower() in wh or l.startswith("WH_"):
            continue
        if l in verbs:
            vs.append(w.lower())
            continue
        r = ROLE.get(l)
        if r == var:
            ctx.append(_key(w, asker, ""))
        elif r:
            roles.setdefault(r, []).append(_key(w, asker, ""))
    return {"verbs": vs, "var": var, "roles": roles, "ctx": ctx, "question": reading.get("text", "")}


def answer(pat: dict, web: list, near, idf: dict | None = None, weigh=None) -> list[dict]:
    """[{statement, score, filler}] best first. Each of the question's words counts by its rarity (`idf`, 1 when not
    given) times its best `near` in the statement's same part (verbs against verbs; the asked part's context against
    the statement's asked part). The variable's role is preferred, never required: a statement without it answers
    with its words the question did not say. Asserted before possible on ties."""
    if not pat:
        return []
    w8 = (lambda x: idf.get(x, max(idf.values()) if idf else 1.0)) if idf else (lambda x: 1.0)
    # weigh(a, b, s): a per-statement weight of question word a matched by statement word b (e.g. how far s's message
    # moved b's thing in the field); else the word's rarity

    def best(a, have, s):
        if weigh is None:
            return w8(a) * max((near(a, b) for b in have), default=0.0)
        return max((near(a, b) * weigh(a, b, s) for b in have), default=0.0)
    qwords = set(pat["verbs"]) | {x for ns in pat["roles"].values() for x in ns} | set(pat.get("ctx") or [])
    out = []
    for s in web:
        sc = sum(best(a, s["verbs"], s) for a in pat["verbs"])
        for role, nodes in pat["roles"].items():
            sc += sum(best(a, s["roles"].get(role, []), s) for a in nodes)
        have = s["roles"].get(pat["var"], [])
        sc += sum(best(a, have, s) for a in pat.get("ctx") or [])
        if have:
            filler = " ".join(s["surfaces"].get(pat["var"], []))
        else:                                          # the statement's new words: what the question did not say
            new = [(k, t) for r in s["roles"] for k, t in zip(s["roles"][r], s["surfaces"].get(r, []))
                   if not any(near(k, q) >= 1.0 for q in qwords)]
            filler = " ".join(t for _k, t in new)
        out.append({"statement": s, "score": sc, "filler": filler})
    out.sort(key=lambda r: (-r["score"], r["statement"]["possible"]))
    return out


JOIN_DEPTH = 3          # statements in a chain at most: cliffhanger's depth (approved 2026-11-06)


def _cover(pat, chain, near, w8):
    """{question word: its best weighted match over the chain, role by role} -- the chain's coverage, per word."""
    out = {}
    for a in pat["verbs"]:
        out[("v", a)] = max((w8(a) * near(a, b) for s in chain for b in s["verbs"]), default=0.0)
    for role, nodes in pat["roles"].items():
        for a in nodes:
            out[(role, a)] = max((w8(a) * near(a, b) for s in chain for b in s["roles"].get(role, [])), default=0.0)
    for a in pat.get("ctx") or []:
        out[("ctx", a)] = max((w8(a) * near(a, b) for s in chain for b in s["roles"].get(pat["var"], [])), default=0.0)
    return out


def chains(pat: dict, web: list, near, idf: dict | None = None, rare: set | None = None, rank: str = "coverage",
           people: set | None = None, answer_side: bool = False) -> list:
    """STATEMENT CHAINS (2026-11-06, the operator: "rank statement chains by their length together with how we rank"):
    chains of up to JOIN_DEPTH statements, each next one sharing a RARE thing (`rare`) with one before it -- or, when it
    holds the asked role, the person the question is about (`people`; "chains not forming around people is a bug").
    A chain counts only when every statement adds coverage the others do not (no free riders); with `answer_side`, a
    later statement's new answer words count too. rank="coverage": coverage first, the shorter on ties;
    rank="per-statement": coverage / length. [{chain, score, filler}] best first.
    Fast form: each statement's best weighted match per question word is computed ONCE (a statements x words table);
    a chain's coverage is the max over its rows; only chains that added coverage grow (a beam over free-rider-free
    chains -- the same chains the exhaustive search keeps)."""
    import numpy as np
    if not pat:
        return []
    w8 = (lambda x: idf.get(x, max(idf.values()) if idf else 1.0)) if idf else (lambda x: 1.0)
    rare = rare or set()
    people = {p.lower() for p in (people or set())}
    cols = [("v", a) for a in pat["verbs"]] + [(r, a) for r, ns in pat["roles"].items() for a in ns] + \
        [("ctx", a) for a in (pat.get("ctx") or [])]
    if not web:
        return []
    T = np.zeros((len(web), max(1, len(cols))), dtype=np.float32)
    for i, st in enumerate(web):
        for j, (role, a) in enumerate(cols):
            have = st["verbs"] if role == "v" else st["roles"].get(pat["var"] if role == "ctx" else role, [])
            T[i, j] = max((w8(a) * near(a, b) for b in have), default=0.0)
    var_words = [set(st["roles"].get(pat["var"], [])) for st in web]
    nodes = [{x for ns in st["roles"].values() for x in ns} & rare for st in web]
    pnodes = [({x for ns in st["roles"].values() for x in ns} & people) if var_words[i] else set()
              for i, st in enumerate(web)]

    def score(idx):
        cov = float(T[idx].max(axis=0).sum()) if cols else 0.0
        if answer_side:
            seen = set(var_words[idx[0]])
            for k in idx[1:]:
                cov += sum(w8(x) for x in var_words[k] - seen)
                seen |= var_words[k]
        return cov
    found = []
    frontier = [[i] for i in range(len(web)) if score([i]) > 0]
    for depth in range(1, JOIN_DEPTH + 1):
        nxt = []
        for idx in frontier:
            cov = score(idx)
            if len(idx) > 1 and any(score([j for j in idx if j != i]) >= cov for i in idx):
                continue                                   # a free rider adds nothing: drop it, never grow it
            found.append((idx, cov))
            if depth < JOIN_DEPTH:
                linked = set().union(*[nodes[i] for i in idx])
                plinked = set().union(*[pnodes[i] for i in idx])
                for j in range(max(idx) + 1, len(web)):
                    if nodes[j] & linked or pnodes[j] & plinked:
                        nxt.append(idx + [j])
        frontier = nxt
    key = (lambda t: (-t[1], len(t[0]))) if rank == "coverage" else (lambda t: (-t[1] / len(t[0]), len(t[0])))
    out = []
    for idx, cov in sorted(found, key=key):
        ch = [web[i] for i in idx]
        filler = " ".join(x for st in ch for x in st["surfaces"].get(pat["var"], []))
        out.append({"chain": ch, "score": cov if rank == "coverage" else cov / len(ch), "filler": filler})
    return out
