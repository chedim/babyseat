"""LIVING LANGUAGE (the operator, 2026-11-01: "lets implement 'living language'"; "there was a
design of cliffhanger that looked like prose at some point"). English is a language of the
code tree (`en`, beside sh and js): a sentence is parsed (spaCy, the optional `nlp` extra)
into a tree of node entities that prints back exactly, and compiles to calls on the field:

  the verb      a verb entity (verbs.py), phrasal ones too ("turn on"); only commands compile:
                a verb with a subject other than "you" is a report or a plan, not a call
  its objects   a pronoun's register (convgraph), an entity named in full ("goal one", a file,
                PR #803), or a class's members ("the goals") narrowed by its adjectives
  adjectives    Adjective entities, each a cliffhanger filter over x ("open": x.status == 'open');
                seeded ones at boot, anyone may define more
  adverbs       run options ("quietly": no chat line; others are left to the reader)

A sentence runs like any code: exec(action='run', lang='en', code=...) on tool:language. What
doesn't map (an unknown verb, an object that names nothing, an adjective nobody defined) is
said, and nothing runs. A chat message that compiles fully is told to the model as its
program; the model decides whether to run it -- membrane runs nothing on its own yet.

A noun phrase a pronoun points at but the field doesn't know ("on field ui redesign -- park
that") becomes a Concept entity, so the registers can hold it.
"""
from __future__ import annotations

import re
import time

from .entities import CLASS_CONCEPT, MEMBRANE_NS, QuantumEntity

EXECUTOR = MEMBRANE_NS + "tool:language"
CONCEPT = MEMBRANE_NS + "concept:"
CLASS_ADJECTIVE = "Adjective"
INSTALL = "living language needs spaCy: pip install 'babyseat[nlp]' && python -m spacy download en_core_web_sm"
ADVERBS = {"quietly": {"quiet": True}, "silently": {"quiet": True}}
SEED_ADJECTIVES = {"open": "x.status == 'open'",
                   "closed": "x.status == 'closed' or x.status == 'fulfilled' or x.status == 'done'",
                   "blocked": "x.status == 'blocked'", "dropped": "x.status == 'dropped'",
                   "decided": "x.status == 'decided'"}
_FIRST_SEEDS = {"closed": "x.status == 'closed'"}    # #812's first seeds, before the `seeded` marker
_NLP: dict = {"m": None}
MODEL = "en_core_web_sm"


def _nlp():
    if _NLP["m"] is None and not enabled():
        return None
    if _NLP["m"] is None:
        try:
            import spacy
            _NLP["m"] = spacy.load("en_core_web_sm")
        except Exception:
            _NLP["m"] = False
    return _NLP["m"] or None


def available() -> bool:
    return _nlp() is not None


def enabled() -> bool:
    """On by default (the operator, 2026-11-01); MEMBRANE_LANGUAGE=0 turns it off."""
    import os
    return os.environ.get("MEMBRANE_LANGUAGE", "1").strip().lower() not in ("0", "off", "false", "no")


def ensure(log=print) -> bool:
    """Boot (on its own thread): spaCy and its English model, installed when missing --
    ready-made wheels only, never a source build (Home Assistant's Alpine has none for some
    platforms). A failure logs one line and leaves the old pronoun rules in place."""
    import importlib
    import subprocess
    import sys
    import os
    if not enabled():
        return False
    # Home Assistant's supervisor sets MEMBRANE_NO_AUTOINSTALL for the seats it runs: install
    # nothing beside HA's pinned packages unless MEMBRANE_LANGUAGE=1 asks for it (Memo, #812)
    may_install = not os.environ.get("MEMBRANE_NO_AUTOINSTALL") or os.environ.get("MEMBRANE_LANGUAGE") == "1"
    pip = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    try:
        importlib.import_module("spacy")
    except ImportError:
        if not may_install:
            log("[language] spaCy is not installed and installs are off here: living language is off")
            return False
        r = subprocess.run(pip + ["--only-binary=:all:", "spacy>=3.8.18"], capture_output=True, text=True,
                           timeout=900)
        if r.returncode != 0:
            log(f"[language] spaCy could not be installed (no wheel here?): living language is off "
                f"({(r.stderr or r.stdout).strip().splitlines()[-1:] or ['?']})")
            return False
        importlib.invalidate_caches()
    try:
        importlib.import_module(MODEL)
    except ImportError:
        if not may_install:
            log(f"[language] the {MODEL} model is not installed and installs are off here")
            return False
        r = subprocess.run([sys.executable, "-m", "spacy", "download", MODEL, "--", "-q"],
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            log(f"[language] the {MODEL} model could not be downloaded: living language is off")
            return False
        importlib.invalidate_caches()
    try:                                   # generation's verb forms (generate.py has plain rules too)
        importlib.import_module("lemminflect")
    except ImportError:
        if may_install:
            try:
                r = subprocess.run(pip + ["--only-binary=:all:", "lemminflect"], capture_output=True, text=True,
                                   timeout=600)
                if r.returncode != 0:
                    log("[language] lemminflect could not be installed: verb forms use plain rules")
            except Exception as e:
                log(f"[language] lemminflect install failed ({type(e).__name__}): verb forms use plain rules")
    _NLP["m"] = None                       # load on next use
    ok = available()
    log(f"[language] living language {'on' if ok else 'off'} ({MODEL})")
    return ok


def _doc(text: str):
    nlp = _nlp()
    if nlp is None:
        from .lang import NoGrammar
        raise NoGrammar(INSTALL)
    return nlp(str(text or ""))


# ---- the tree ------------------------------------------------------------------------------

def parse(text: str):
    """A sentence -> a Node tree: the program's text with a hole per noun phrase, verb, adverb
    and adjective (its own text as the leaf), so printing it is filling the holes."""
    from .lang import HOLE, Node
    doc = _doc(text)
    spans = [(c.start_char, c.end_char, "noun") for c in doc.noun_chunks]
    covered = {i for c in doc.noun_chunks for i in range(c.start, c.end)}
    kinds = {"VERB": "verb", "AUX": "verb", "ADV": "adverb", "ADJ": "adjective"}
    for t in doc:
        if t.i not in covered and t.pos_ in kinds:
            spans.append((t.idx, t.idx + len(t.text), kinds[t.pos_]))
    spans.sort()
    tpl, kids, last = [], [], 0
    for a, b, kind in spans:
        if a < last:
            continue
        tpl += [text[last:a], HOLE]
        kids.append(Node(kind, text[a:b]))
        last = b
    tpl.append(text[last:])
    return Node("program", "".join(tpl), kids) if kids else Node("program", text)


# ---- adjectives, concepts, the executor ------------------------------------------------------

def _adj_eid(name: str) -> str:
    return MEMBRANE_NS + "adjective:" + re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")


def define_adjective(est, name: str, expr: str) -> str:
    """An adjective: a named cliffhanger filter over x (refused when it doesn't parse)."""
    from .ch import _check, _parse
    _check(_parse(expr), {"x"})
    eid = _adj_eid(name)
    props = {"name": name.lower(), "filter": expr, "silent_birth": True}
    d = (est.docs or {}).get(eid)
    if d is not None:                        # a redefinition replaces the filter (upsert would merge it)
        d.properties = {**(d.properties or {}), **props}
        est.upsert(d, embedder=None)
    else:
        est.upsert(QuantumEntity(eid=eid, cls=CLASS_ADJECTIVE, properties=props), embedder=None)
    return eid


def seed(est) -> int:
    """Boot: the seeded adjectives, made once (an edited one is kept)."""
    n = 0
    for name, expr in SEED_ADJECTIVES.items():
        d = (est.docs or {}).get(_adj_eid(name))
        p = (d.properties or {}) if d is not None else {}
        seeded = p.get("seeded") or _FIRST_SEEDS.get(name)        # the seed it got when it had no marker
        if d is None or (seeded and p.get("filter") == seeded and seeded != expr):
            define_adjective(est, name, expr)       # new, or still the seed of an older boot
            est.docs[_adj_eid(name)].properties["seeded"] = expr
            n += 1
    return n


def seed_executor(est) -> bool:
    if EXECUTOR in (est.docs or {}):
        return False
    from .entities import CLASS_TOOL
    est.upsert(QuantumEntity(eid=EXECUTOR, cls=CLASS_TOOL, properties={
        "name": "language", "lang": "en", "silent_birth": True,
        "summary": "the en executor: a sentence linked to it runs as the calls it compiles to"}))
    return True


def concept(est, phrase: str) -> str:
    """A noun phrase as an entity (made on first use), so a register can hold it."""
    name = " ".join(str(phrase).split()).strip(" .,;:-").lower()
    eid = CONCEPT + re.sub(r"[^a-z0-9_-]+", "-", name).strip("-")
    if eid not in (est.docs or {}):
        est.upsert(QuantumEntity(eid=eid, cls=CLASS_CONCEPT, properties={
            "name": name, "silent_birth": True, "born_ts": time.time()}), embedder=None)
    return eid


def concept_before(est, text: str, pos: int) -> str | None:
    """The noun phrase a pronoun at `pos` points back to, as a Concept, or None: the nearest
    run of nouns before it that agrees in number ("them" wants a plural, "that" a singular),
    never the subject of the pronoun's own clause ("Claude fixed them": not Claude)."""
    if not available():
        return None
    doc = _doc(text)
    from . import convgraph as CG
    pron = next((t for t in doc if t.idx >= pos), None)
    if pron is None:
        return None
    own_subj = {c.i for c in pron.head.children if c.dep_ in ("nsubj", "nsubjpass")}
    plural = CG.FORMS.get(pron.lower_) in CG.PLURAL
    runs, cur = [], []
    for t in doc[:pron.i]:
        if t.pos_ in ("NOUN", "PROPN") or (t.pos_ == "ADJ" and cur):
            cur.append(t)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    runs = [r for r in runs if not any(t.i in own_subj for t in r) and r[-1].lower_ not in CG.FORMS]
    agree = [r for r in runs if (r[-1].tag_ in ("NNS", "NNPS")) == plural]
    best = (agree or runs or [None])[-1]
    return concept(est, " ".join(t.text for t in best)) if best else None


# ---- compiling --------------------------------------------------------------------------------

def _adjectives(est) -> dict:
    return {(d.properties or {}).get("name"): d for d in list((est.docs or {}).values())
            if d.cls == CLASS_ADJECTIVE}


def _members(est, lemma: str) -> list:
    from .night import ancestors
    out = []
    for d in list((est.docs or {}).values()):
        if d.cls in (CLASS_ADJECTIVE, "Verb", "BannerRule", "Message", "Register") or d.eid.startswith(_MACHINERY):
            continue
        if (d.cls or "").lower() == lemma or any(
                str((a.properties or {}).get("name") or "").lower() == lemma for a in ancestors(est, d)):
            out.append(d)
    return out


_MACHINERY = tuple(MEMBRANE_NS + k for k in ("node:", "result:", "msg:", "register:", "bannerrule:",
                                                 "verb:", "adjective:"))


def _by_name(est, name: str):
    """The thing with that name (or a taught other name) -- never membrane's own machinery:
    a sentence that ran is minted as code-tree nodes named after its phrases."""
    n = name.lower().strip()
    for d in list((est.docs or {}).values()):
        if d.eid.startswith(_MACHINERY):
            continue
        p = d.properties or {}
        if str(p.get("name") or "").lower() == n or n in [str(a).lower() for a in (p.get("aliases") or [])]:
            return d
    return None


_DEFINE = re.compile(r"^\s*(?:from now on,?\s+)?[\"']?([a-z][\w' -]*?)[\"']?\s+"
                     r"(?:means|is the same as|stands for)\s+(.+?)\s*[.!]?\s*$", re.I)
_PROP_VALUE = re.compile(r"^([a-z_][a-z0-9_]*)\s+(?:is\s+|=\s*)?([\w.:/-]+)$", re.I)


def _adjective_expr(est, meaning: str) -> str | None:
    """"not closed", "pending and mine", "status blocked" -> a cliffhanger filter over x; None."""
    known = _adjectives(est)
    parts = []
    for part in re.split(r"\s+and\s+", meaning.strip(), flags=re.I):
        neg = part.lower().startswith("not ")
        core = part[4:].strip() if neg else part.strip()
        adj = known.get(core.lower())
        if adj is not None:
            expr = (adj.properties or {}).get("filter")
        else:
            pv = _PROP_VALUE.match(core)
            if not pv:
                return None
            expr = f"x.{pv.group(1)} == {pv.group(2)!r}"
        parts.append(f"not ({expr})" if neg else f"({expr})")
    return " and ".join(parts) if parts else None


def _meaning(srv, est, word: str, meaning: str) -> dict | None:
    """What a definition teaches: an adjective's filter, a verb synonym, another name for an
    entity, or a filter from a property and a value -- in that order; None when none fits."""
    from . import verbs as V
    m = meaning.strip().strip("\"'").strip()
    if m.lower() in _adjectives(est) or re.match(r"(?i)^not\s|.+\sand\s", m):
        expr = _adjective_expr(est, m)
        if expr:
            return {"kind": "adjective", "filter": expr, "shown": m}
    lem = " ".join(t.lemma_.lower() for t in _doc(m)) if m else ""
    for cand in (m.lower(), lem):
        if cand and (est.docs or {}).get(V.verb_eid(cand)) is not None:
            return {"kind": "verb", "same_as": cand, "shown": cand}
    d = _by_name(est, m)
    if d is not None:
        return {"kind": "entity", "target": d.eid, "shown": m}
    expr = _adjective_expr(est, m)
    if expr:
        return {"kind": "adjective", "filter": expr, "shown": m}
    return None


def _define_step(srv, est, word: str, meaning: str, text: str) -> dict:
    plan = _meaning(srv, est, word, meaning)
    return {"kind": "define", "verb": "define", "word": word, "plan": plan, "meaning": meaning,
            "targets": [], "calls": [], "options": {}, "writes": True, "needs_all": False,
            "mapped": plan is not None, "unmapped": [] if plan else [f"define {word}"],
            "text": str(text).strip()}


_WH = {"what", "which", "who", "how", "is", "are", "does", "do", "did", "list", "show", "where", "why", "when"}
_Q_MEANING = re.compile(r"(?i)^what does [\"']?(.+?)[\"']? mean$")
_Q_COUNT = re.compile(r"(?i)^how many (.+?)(?:\s+(?:are there|exist|do we have|are there now))?$")
_Q_LIST = re.compile(r"(?i)^(?:which|what|list|show me|show)\s+(?:are\s+)?(?:the\s+)?(.+?)"
                     r"(?:\s+(?:are there|exist|do we have|are left|there are))?$")
_Q_IS = re.compile(r"(?i)^is (.+) (\w+)$")


def _is_question(text: str) -> bool:
    t = str(text or "").strip()
    first = (t.split() or [""])[0].lower()
    return t.endswith("?") or first in ("which", "how")


def _phrase(srv, sid: str, est, phrase: str):
    """A noun phrase's members (a class narrowed by its adjectives) and its tokens."""
    toks = [t for t in _doc(phrase) if not t.is_punct]
    unmapped: list = []
    return (_span_targets(srv, sid, est, toks, unmapped) if toks else []), toks, unmapped


def _query_step(srv, sid: str, est, text: str) -> dict:
    q = str(text).strip().rstrip("?!. ").strip()
    plan, unmapped = None, []
    m = _Q_MEANING.match(q)
    if m:
        plan = {"ask": "meaning", "word": m.group(1).strip().lower()}
    elif _Q_COUNT.match(q):
        phrase = _Q_COUNT.match(q).group(1)
        targets, toks, unmapped = _phrase(srv, sid, est, phrase)
        plan = {"ask": "count", "targets": targets, "phrase": phrase, "toks": toks}
    elif _Q_IS.match(q) and _Q_IS.match(q).group(2).lower() in _adjectives(est):
        name, adj = _Q_IS.match(q).group(1), _Q_IS.match(q).group(2).lower()
        d = _by_name(est, re.sub(r"(?i)^the\s+", "", name))
        if d is None:
            unmapped.append(name)
        plan = {"ask": "is", "target": d.eid if d is not None else None, "adjective": adj, "name": name}
    elif _Q_LIST.match(q):
        phrase = _Q_LIST.match(q).group(1)
        targets, toks, unmapped = _phrase(srv, sid, est, phrase)
        plan = {"ask": "list", "targets": targets, "phrase": phrase, "toks": toks}
    else:
        unmapped.append("question")
    return {"kind": "query", "verb": "answer", "plan": plan, "targets": (plan or {}).get("targets") or [],
            "calls": [], "options": {}, "writes": False, "needs_all": False,
            "mapped": plan is not None and not unmapped, "unmapped": unmapped, "text": str(text).strip()}


def _numbered(toks, n: int) -> str:
    """The phrase in the number n wants: "open goals" -> "open goal" for one."""
    words = [t.text for t in toks if t.pos_ != "DET"]
    if n == 1 and toks:
        head = [t for t in toks if t.pos_ != "DET"][-1]
        words[-1] = head.lemma_ if head.tag_ in ("NNS", "NNPS") else head.text
    return " ".join(words)


def answer(srv, step: dict) -> tuple[bool, str]:
    """A query step's answer in words: (answered, the sentence). Reads only."""
    from . import convgraph as CG
    from . import generate as GEN
    from . import verbs as V
    from .ch import EntityView, Refused, evaluate
    est = srv.entity_store
    p = step.get("plan") or {}
    if not step.get("mapped"):
        return False, ("I can't answer that from the field: it isn't a question about what the field "
                       "holds, or I don't know " + GEN.join([f'"{u}"' for u in step.get("unmapped") or []]) + ".")
    if p["ask"] in ("list", "count"):
        n, names = len(p["targets"]), [CG._label(est, e) for e in p["targets"]]
        if n == 0:
            return True, f"The field has no {_numbered(p['toks'], 2)}."
        if p["ask"] == "count":
            return True, f"{n} {_numbered(p['toks'], n)}."
        return True, f"{n} {_numbered(p['toks'], n)}: {GEN.join([GEN._name(x) for x in names])}."
    if p["ask"] == "is":
        adj = _adjectives(est)[p["adjective"]]
        d = est.docs[p["target"]]
        try:
            yes = bool(evaluate((adj.properties or {}).get("filter") or "False", {"x": EntityView(est, d.eid)}))
        except Refused:
            yes = False
        name = CG._label(est, d.eid)
        if yes:
            return True, f"Yes, {name} is {p['adjective']}."
        st = (d.properties or {}).get("status")
        return True, f"No: {name} is {st}." if st else f"No: {name} isn't {p['adjective']}."
    w = p["word"]
    v = (est.docs or {}).get(V.verb_eid(w))
    if v is not None and (v.properties or {}).get("same_as"):
        return True, f'"{w}" means {v.properties["same_as"]}.'
    a = _adjectives(est).get(w)
    if a is not None:
        return True, f'"{w}" means {(a.properties or {}).get("filter")}.'
    d = _by_name(est, w)
    if d is not None and w in [str(x).lower() for x in ((d.properties or {}).get("aliases") or [])]:
        return True, f'"{w}" means {CG._label(est, d.eid)}.'
    if v is not None:
        return True, f'"{w}" is a verb: ' + GEN.join([CG._label(est, m) for m in (v.properties or {}).get(
            "implemented_by") or []] or ["nothing implements it"]) + "."
    return False, f'I don\'t know what "{w}" means.'


def teach(est, step: dict) -> str:
    """Keep a definition step: the adjective, the verb synonym, the other name. -> what it means."""
    from . import verbs as V
    p, word = step["plan"], step["word"]
    if p["kind"] == "adjective":
        define_adjective(est, word, p["filter"])
    elif p["kind"] == "verb":
        eid = V.verb_eid(word)
        d = (est.docs or {}).get(eid) or QuantumEntity(eid=eid, cls=V.CLASS_VERB, properties={
            "name": V.lemma(word), "word": "w:" + V.lemma(word).split(" ", 1)[0], "implemented_by": [],
            "silent_birth": True})
        d.properties = {**(d.properties or {}), "same_as": p["same_as"]}
        est.upsert(d, embedder=None)
    else:
        d = est.docs[p["target"]]
        names = list((d.properties or {}).get("aliases") or [])
        if word not in names:
            names.append(word)
        d.properties = {**(d.properties or {}), "aliases": names}
        est.upsert(d, embedder=None)
    return p["shown"]


_LEADS = {"please", "pls", "and", "then", "also", "now", "so", "ok", "okay", "yes", "can", "could",
          "would", "will", "you", "just", "first", "next"}
_BREAK = {"and", "then", ",", ";", "."}
_ALL = {"all", "every", "each", "both"}      # the quantifier that confirms a write on many things


def _verb_at(est, doc, i: int):
    """(verb lemma, tokens it spans) when a known verb starts at token i (phrasal: two words)."""
    from . import verbs as V
    docs = est.docs or {}
    t = doc[i]
    for cand in ([(t.lemma_.lower() + " " + doc[i + 1].lower_, 2), (t.lower_ + " " + doc[i + 1].lower_, 2)]
                 if i + 1 < len(doc) else []) + [(t.lemma_.lower(), 1), (t.lower_, 1)]:
        if V.verb_eid(cand[0]) in docs:
            return cand
    return None


def _clause_starts(doc) -> set[int]:
    """Where a command may start: the sentence's start, and after and / then / a comma, past
    leading words like please, can you, yes."""
    out, at = set(), 0
    for i, t in enumerate(doc):
        if i == at:
            if t.lower_ in _LEADS or t.is_punct:
                at = i + 1
                continue
            out.add(i)
        if t.lower_ in _BREAK or t.is_sent_end:
            at = i + 1
    return out


def compile(srv, sid: str, text: str) -> list[dict]:
    """The sentence's commands: [{verb, targets, options, mapped, unmapped, text}], in order.
    The field's own words lead: a known verb at a clause's start is the command whatever the
    parser tagged it; an unknown one is still read off the parse, and said as unmapped."""
    est = srv.entity_store
    m = _DEFINE.match(str(text or ""))
    if m:                                        # "X means Y": a definition, not a command
        return [_define_step(srv, est, m.group(1).strip().lower(), m.group(2).strip(), text)]
    if _is_question(text):                       # a question: a read-only query over the field
        return [_query_step(srv, sid, est, text)]
    doc = _doc(text)
    starts = _clause_starts(doc)
    heads = []                                   # (token index, verb lemma, verb width)
    for i in sorted(starts):
        hit = _verb_at(est, doc, i)
        if hit:
            heads.append((i, hit[0], hit[1]))
        elif doc[i].pos_ == "VERB" and not [c for c in doc[i].children
                                              if c.dep_ in ("nsubj", "nsubjpass") and c.lower_ != "you"]:
            heads.append((i, doc[i].lemma_.lower(), 1))
    steps = []
    for n, (i, verb, width) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(doc)
        span = [t for t in doc[i + width:end] if not (t.lower_ in _BREAK or t.is_punct)]
        while span and span[-1].lower_ in _LEADS:
            span.pop()
        options: dict = {}
        kept = []
        for t in span:
            if t.lower_ in ADVERBS:
                options.update(ADVERBS[t.lower_])
            elif t.lower_ not in ("please", "pls"):
                kept.append(t)
        unmapped: list = [] if _verb_at(est, doc, i) else [verb]
        targets = _span_targets(srv, sid, est, kept, unmapped) if kept else []
        if not kept:
            unmapped.append("(no object)")
        calls: list = []
        if not unmapped:                        # "compiles" means every target has the verb's meaning
            said = {t.lower_ for t in kept} | {t.lemma_.lower() for t in kept}
            for e in targets:
                c = _call(est, verb, e, said, unmapped)
                if c is not None:
                    calls.append(c)
        writes = any(c["writes"] for c in calls)
        everyone = any(t.lower_ in _ALL for t in kept)
        steps.append({"verb": verb, "targets": targets, "options": options, "calls": calls,
                      "mapped": not unmapped and bool(calls), "unmapped": unmapped,
                      "writes": writes, "needs_all": writes and len(calls) > 1 and not everyone,
                      "text": doc[i:end].text.strip(" ,.;")})
    return steps


_KIND_WORDS = {"u": {"pr", "pull", "request", "issue"}, "f": {"file"}, "c": {"command"}}


def _value(est, eid: str) -> str:
    """What an object is as an argument: a PR's number, a file's path, an entity's name."""
    from . import convgraph as CG
    m = CG._GH.match(eid)
    if m:
        return m.group(2)
    d = (est.docs or {}).get(eid)
    if d is not None:
        return str((d.properties or {}).get("name") or eid)
    return eid.split(":", 1)[1] if ":" in eid else eid


def _call(est, verb: str, e: str, said: set, unmapped: list) -> dict | None:
    """The call `verb` means on object `e`: its class's method on it (verbs.implementation), or a
    compound name's (`pr_checks` for "check PR #812") on that method's owner, the object bound
    to its one free parameter."""
    from . import night as N
    from . import verbs as V
    d = (est.docs or {}).get(e)
    m = V.implementation(est, d, verb)[0] if d is not None else None
    if m is not None:
        return {"object": e, "method": (m.properties or {}).get("method_name"), "args": {},
                "writes": not (m.properties or {}).get("read_only"), "target": e}
    kinds = set(_KIND_WORDS.get(e.split(":", 1)[0], set()))
    if d is not None:
        kinds |= {w.lower() for w in str((d.properties or {}).get("name") or "").split()} | {(d.cls or "").lower()}
    for method, noun in V.aliased(est, verb):
        if not set(noun.split()) <= (said | kinds):
            continue                            # the name's noun must be what the sentence's object is
        mp = method.properties or {}
        owner = (mp.get("method_of") or [None])[0]
        if not owner:
            continue
        params = N.command_params(str(mp.get("command") or "")) or list(mp.get("params") or [])
        if len(params) > 1:
            unmapped.append(f"{verb} needs {', '.join(params)}")
            return None
        return {"object": owner, "method": mp.get("method_name"),
                "args": {params[0]: _value(est, e)} if params else {}, "writes": not mp.get("read_only"),
                "target": e}
    from . import generate as GEN
    unmapped.append(GEN.no_meaning(verb, CG_label(est, e)))
    return None


def CG_label(est, eid: str) -> str:
    from . import convgraph as CG
    return CG._label(est, eid)


def _span_targets(srv, sid: str, est, toks: list, unmapped: list) -> list[str]:
    """What an object span means: a pronoun's register, what it names in full (a file, #803,
    an entity's name), or a class's members narrowed by its adjectives."""
    from . import convgraph as CG
    from .ch import EntityView, Refused, evaluate
    words = [t for t in toks if t.pos_ != "DET" and t.lower_ not in _ALL]
    if len(words) == 1 and words[0].pos_ == "PRON" and words[0].lower_ in CG.FORMS:
        held = CG.register(srv, sid, words[0].lower_) if sid else None
        got = [held] if isinstance(held, str) else list(held or [])
        if not got:
            unmapped.append(words[0].text)
        return got
    text = toks[0].doc.text[toks[0].idx:toks[-1].idx + len(toks[-1].text)]   # the original words, punctuation kept
    named = CG._mentions(srv, text)
    if named:
        return named
    d = _by_name(est, " ".join(t.text for t in words))
    if d is not None:
        return [d.eid]
    known = _adjectives(est)
    adjs = [t for t in words[:-1] if (t.lemma_.lower() in known or t.lower_ in known)]
    rest = [t for t in words if t not in adjs]
    if not rest:
        unmapped.append(text)
        return []
    members = _members(est, rest[-1].lemma_.lower())
    if not members:
        unmapped.append(text)
        return []
    for a in adjs:
        adj = known.get(a.lemma_.lower()) or known.get(a.lower_)
        expr = (adj.properties or {}).get("filter") or "False"
        kept = []
        for m in members:
            try:
                if evaluate(expr, {"x": EntityView(est, m.eid)}):
                    kept.append(m)
            except Refused:
                pass
        members = kept
    return [m.eid for m in members]


def describe(est, steps: list[dict]) -> str:
    from . import convgraph as CG
    out = []
    for s in steps:
        if s.get("kind") == "query":
            out.append(f"answers {s['text']!r}")
            continue
        if s.get("kind") == "define":
            out.append(f'teaches "{s["word"]}" = {(s.get("plan") or {}).get("shown") or s.get("meaning")}')
            continue
        names = ", ".join(CG._label(est, e) for e in s["targets"])
        opts = " (" + ", ".join(k for k in s["options"]) + ")" if s["options"] else ""
        out.append(f"{s['verb']} {names}{opts}")
    return "; ".join(out)


# ---- running -----------------------------------------------------------------------------------

def run(srv, text: str, timeout_s=None) -> dict:
    """The `en` runner: the sentence's calls, in order; nothing when any part doesn't map."""
    from . import night as N
    from . import verbs as V
    from .opencode import _efield_on
    t0 = time.time()
    runner = getattr(srv, "_loop_runner", None)
    sid = str(getattr(runner, "_current_turn_sid", None) or "")
    est = srv.entity_store
    try:
        steps = compile(srv, sid, text)
    except Exception as e:
        return {"ok": False, "rc": 2, "outcome": "refused", "output": str(e), "error": str(e),
                "head": str(e)[:200], "tail": str(e)[-200:], "ms": 1}
    from . import generate as GEN
    from . import convgraph as CG

    def refused(msg):
        return {"ok": False, "rc": 2, "outcome": "refused", "output": msg, "error": msg, "said": msg,
                "head": msg[:200], "tail": msg[-200:], "ms": max(1, int((time.time() - t0) * 1000))}
    if not steps:
        return refused("I didn't find a command in that.")
    if steps[0].get("kind") == "query":          # a question: read the field, word the answer
        ok, line = answer(srv, steps[0])
        r = {"ok": ok, "rc": 0 if ok else 2, "outcome": "ok" if ok else "refused", "output": line, "said": line,
             "head": line, "tail": line, "error": "" if ok else line, "ms": max(1, int((time.time() - t0) * 1000))}
        return r
    if steps[0].get("kind") == "define":         # teaching a word (generate.py words it)
        s = steps[0]
        if not s["mapped"]:
            return refused(GEN.cant_define(s["word"]))
        line = GEN.taught(s["word"], teach(est, s))
        return {"ok": True, "rc": 0, "outcome": "ok", "output": line, "said": line, "head": line,
                "tail": line, "error": "", "ms": max(1, int((time.time() - t0) * 1000))}
    unclear = [s for s in steps if s["unmapped"] or not s["mapped"]]
    if unclear:                                  # worded by membrane (generate.py), nothing runs
        return refused(" ".join(GEN.not_understood(s["verb"], s["unmapped"] or ["(no object)"]) for s in unclear))
    many = [s for s in steps if s.get("needs_all")]
    if many:
        return refused(" ".join(GEN.needs_all(s["verb"], [CG._label(est, c["target"]) for c in s["calls"]])
                                for s in many))
    said, lines, rc = [], [], 0
    for s in steps:
        names, failed = [], []
        for c in s["calls"]:
            out = N.method_call(srv, est, _efield_on(srv), getattr(srv, "membrane", None), c["object"],
                                c["method"], dict(c["args"]))
            r = int(out.get("rc") or (0 if out.get("ok") else 1))
            rc = max(rc, r)
            name = CG._label(est, c["target"])
            names.append(name)
            if r != 0:
                failed.append((name, out.get("error") or out.get("output") or "", r))
            lines.append(f"{s['verb']} {c['object']}: {out.get('output') or out.get('error') or out.get('status')}")
        said.append(GEN.done(s["verb"], names, failed))
    text_out = " ".join(said) + ("\n\n" + "\n".join(lines) if lines else "")
    return {"ok": rc == 0, "rc": rc, "outcome": "ok" if rc == 0 else "error", "output": text_out,
            "said": " ".join(said), "head": text_out[:200], "tail": text_out[-200:],
            "error": "" if rc == 0 else text_out, "ms": max(1, int((time.time() - t0) * 1000))}


def _register_lang() -> None:
    from . import lang as LG
    LG.LANGS["en"] = LG.Lang("en", parse, LG.print_, EXECUTOR, run)


_register_lang()
