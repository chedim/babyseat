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
import threading
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
# ONE lock around every call into the shared spaCy model (this module's and instances.py's): blis aborts the
# process when two threads parse at once on its memory pool (the 2026-11-01 crash dialog)
NLP_LOCK = threading.Lock()


def _nlp():
    if _NLP["m"] is None and not enabled():
        return None
    if _NLP["m"] is None:
        try:
            import spacy
            _NLP["m"] = spacy.load("en_core_web_sm")
            _no_blis_finalize()
        except Exception:
            _NLP["m"] = False
    return _NLP["m"] or None


def _no_blis_finalize() -> None:
    """blis (under spaCy) finalizes at exit and abort()s when a parse on another thread still
    holds its memory pool -- a Python crash dialog on the Mac (2026-11-01). The OS frees it."""
    try:
        import atexit
        import blis.cy
        atexit.unregister(blis.cy.finalize)
    except Exception:
        pass


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
    with NLP_LOCK:
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

def is_learned(est, d) -> bool:
    """A meaning the person did not give: flagged `learned`, or a LEGACY guess -- no `learned`
    flag at all, not seeded, and the model's gap answer was kept for the word (before #894 those
    counted as the owner's definitions, so a contradiction could not remove them). A definition
    taught by the person carries learned=False, and a seed carries `seeded`: never a guess."""
    p = (d.properties or {}) if d is not None else {}
    if "learned" in p:
        return bool(p["learned"])
    if p.get("seeded") or p.get("corrected"):
        return False
    g = (est.docs or {}).get(_gap_eid(str(p.get("name") or "")))
    return bool(g is not None and (g.properties or {}).get("kept"))


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
                                                 "verb:", "adjective:", "gap:", "assoc:"))


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


def _correct(est, word: str) -> None:
    """A definition taught over a learned meaning corrects it (counted, never lost); the new
    definition is the person's own (or the chat-confirmed one confirm_proposed marks after), so
    it is not `learned` -- a contradiction never deletes it, and a write may use it (Memo on #894)."""
    from . import verbs as V
    for eid in (_adj_eid(word), V.verb_eid(word)):
        d = (est.docs or {}).get(eid)
        if d is not None and is_learned(est, d):
            d.properties = {**d.properties, "learned": False, "corrected": True, "corrected_ts": time.time()}
            est.upsert(d, embedder=None)


def teach(est, step: dict) -> str:
    """Keep a definition step: the adjective, the verb synonym, the other name. -> what it means."""
    from . import verbs as V
    p, word = step["plan"], step["word"]
    _correct(est, word)
    if p["kind"] == "adjective":
        eid = define_adjective(est, word, p["filter"])
        d = est.docs[eid]
        if "learned" not in d.properties:        # the person's own definition: never a guess (legacy-safe)
            d.properties = {**d.properties, "learned": False}
            est.upsert(d, embedder=None)
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
        names: dict = {}
        targets = _span_targets(srv, sid, est, kept, unmapped, names) if kept else []
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
        learned = names.pop("__learned__", [])
        if writes and learned:                  # Memo on #894: "close the failed goals" -- a learned word
            unmapped += [f'"{w}" is learned: name the things to change' for w in learned]
            calls = []                          # never picks what a write acts on
        everyone = any(t.lower_ in _ALL for t in kept)
        steps.append({"verb": verb, "targets": targets, "options": options, "calls": calls, "names": names,
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


def _span_targets(srv, sid: str, est, toks: list, unmapped: list, names: dict | None = None) -> list[str]:
    """What an object span means: a pronoun's register, what it names in full (a file, #803,
    an entity's name), or a class's members narrowed by its adjectives. ``names``: for each
    unmapped span, the name the lookup tried (what a definition has to teach)."""
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
    name = " ".join(t.text for t in words)
    d = _by_name(est, name)
    if d is not None:
        return [d.eid]
    if names is not None:
        names[text] = name
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
        if names is not None and is_learned(est, adj):
            names.setdefault("__learned__", []).append(a.text)    # #894: noted, so a write can refuse it
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


# ---- compile-first routing, stage 1: shadow (the operator, 2026-11-01) ---------------------------
# Every person message is compiled locally (no model call) and the outcome kept on its message
# entity next to what the model then did, so the real rates exist before anything answers itself.

def _gap_type(verb: str, u: str) -> str:
    from . import generate as GEN
    if u == verb:
        return "unknown_verb"
    if GEN.NO_MEANING in u:
        return "no_meaning"
    if " needs " in u:
        return "needs_args"
    if u == "(no object)":
        return "no_object"
    if u.startswith("define "):
        return "unknown_definition"
    return "unknown_object"


def classify(srv, sid: str, text: str) -> dict:
    """compiled / gaps (typed) / thinking, with the compiled program -- local, reads only."""
    try:
        steps = compile(srv, sid, text)
    except Exception as e:
        return {"outcome": "error", "gaps": [], "error": str(e)[:200]}
    if not steps:
        return {"outcome": "thinking", "gaps": []}
    if all(s["mapped"] for s in steps):
        return {"outcome": "compiled", "gaps": [], "program": describe(srv.entity_store, steps),
                "kinds": [s.get("kind") or "command" for s in steps]}
    return {"outcome": "gaps", "gaps": gaps(steps)}


def gaps(steps: list[dict]) -> list[dict]:
    """The steps' unmapped words, typed (the shadow's and the turn's one reading)."""
    return [{"type": _gap_type(s["verb"], str(u)), "what": str(u)[:120]} for s in steps for u in s["unmapped"]]


# stage 3 (#802): the gaps a definition ("X means Y") can fill, and what Y has to be
TEACHABLE = {"unknown_verb": "a verb it knows", "unknown_object": "the name of a thing it knows"}


def teachable(steps: list[dict]) -> list[dict]:
    """The gaps the model can fill by teaching, each once, in order."""
    out, seen = [], set()
    for g in gaps(steps):
        if g["type"] in TEACHABLE and g["what"] not in seen:
            seen.add(g["what"])
            out.append(g)
    names = {k: v for s in steps for k, v in (s.get("names") or {}).items()}
    return [{**g, "word": names.get(g["what"], g["what"])} for g in out]


# ---- understanding (#887): how much of a message compiles ------------------------------------
# Every meaning node of the tree tries to mean something the field knows; the share that does is
# membrane's understanding of the message, the rest is its surprise (what the model would fill).
# No sentence types: a reply, a report and a question are scored the same way.
_GRAMMAR = {"ADP", "AUX", "CCONJ", "SCONJ", "DET", "PART", "PUNCT", "SYM", "SPACE"}   # closed-class (UD)


def _names(est) -> dict:
    """Every name and taught other name of a thing (lower case) -> its words' count."""
    out = {}
    for d in list((est.docs or {}).values()):
        if d.eid.startswith(_MACHINERY):
            continue
        p = d.properties or {}
        for n in [p.get("name")] + list(p.get("aliases") or []):
            n = " ".join(str(n or "").lower().split())
            if n:
                out[n] = len(n.split())
    return out


def _named(doc, names: dict) -> list:
    """The runs of two or more words that are a thing's whole name ("goal one"), longest first."""
    by_first: dict = {}                          # first word -> the lengths of the names it starts
    for n, k in names.items():
        if k > 1:
            by_first.setdefault(n.split(" ", 1)[0], set()).add(k)
    out, i = [], 0
    while i < len(doc):
        hit = None
        for k in sorted(by_first.get(doc[i].lower_, ()), reverse=True):
            j = i + k
            if j <= len(doc) and " ".join(t.lower_ for t in doc[i:j]) in names:
                hit = doc[i:j]
                break
        if hit is not None:
            out.append(hit)
            i = hit.end
        else:
            i += 1
    return out


def _meaning_nodes(doc, names: dict | None = None) -> list:
    """(span, part of speech) per meaning node, in order: the things named in full, the noun
    phrases, and every open-class word outside one."""
    named = _named(doc, names) if names else []
    taken = {i for c in named for i in range(c.start, c.end)}
    chunks = [c for c in doc.noun_chunks if not any(i in taken for i in range(c.start, c.end))]
    covered = taken | {i for c in chunks for i in range(c.start, c.end)}
    out = [(c, "NOUN") for c in named + chunks]
    out += [(doc[t.i:t.i + 1], t.pos_) for t in doc if t.i not in covered and t.pos_ not in _GRAMMAR
            and any(c.isalnum() for c in t.text)]          # a dash the tagger called a noun is not a word
    return sorted(out, key=lambda x: x[0].start)


def _means(srv, sid: str, est, doc, span, pos: str, adjectives: dict) -> bool:
    """Does this node mean something the field knows: a verb, an adjective, a run option, or the
    things it names (a register, a mention, a name, a class's members)."""
    t = span[0]
    if len(span) == 1 and (_verb_at(est, doc, t.i) or _adjective_of(t, adjectives) or t.lower_ in ADVERBS):
        return True                    # what the field knows the word as, whatever the tagger called it
    return bool(_known_targets(srv, sid, est, span))


def _known_targets(srv, sid: str, est, span) -> list:
    """What a node names, without the Concepts (a phrase membrane kept for a pronoun is not a
    meaning it knows); [] when any of it doesn't map. A pronoun means only what its register
    holds, never a thing that happens to be named like it ("I": the predictor heartbeat)."""
    from . import convgraph as CG
    words = [t for t in span if t.pos_ != "DET"]
    if len(words) == 1 and words[0].pos_ == "PRON" and words[0].lower_ not in CG.FORMS:
        return []
    unmapped: list = []
    try:
        got = _span_targets(srv, sid, est, list(span), unmapped)
    except Exception:
        return []
    docs = est.docs or {}
    got = [e for e in got if (docs.get(e) is None or docs[e].cls != CLASS_CONCEPT)]
    return got if got and not unmapped else []


def _adjective_of(t, adjectives: dict):
    return adjectives.get(t.lemma_.lower()) or adjectives.get(t.lower_)


def understand(srv, sid: str, text: str) -> dict:
    """{nodes, mapped, share, surprise}: the message's meaning nodes, how many the field knows,
    their share (None with nothing to score), and the words of the ones it doesn't -- read only."""
    est = srv.entity_store
    doc = _doc(text)
    adjectives = _adjectives(est)
    nodes = _meaning_nodes(doc, _names(est))
    surprise = [span.text for span, pos in nodes if not _means(srv, sid, est, doc, span, pos, adjectives)]
    n = len(nodes)
    return {"nodes": n, "mapped": n - len(surprise), "share": round((n - len(surprise)) / n, 3) if n else None,
            "surprise": surprise}


# ---- the response (#887 C): membrane words a reply from the message's filled graph ----------
# What each condition the message names says about each thing it names (checked on the field),
# a read-only program's result, and what it still doesn't know. No model writes it.

def _state(est, eid: str) -> str:
    return str(((est.docs or {}).get(eid) or QuantumEntity(eid="", cls="")).properties.get("status") or "")


def teach_hint_on() -> bool:
    """The unknown-words hint (stage 3, #802): off in 0.7.5 -- it mis-segments ordinary messages and invites junk
    definitions (#1052); MEMBRANE_TEACH_HINT=1 turns it on until its segmentation is fixed (0.7.6)."""
    import os
    return os.environ.get("MEMBRANE_TEACH_HINT", "0").strip().lower() in ("1", "on", "true", "yes")


def answer_on() -> bool:
    """Membrane's own 🧩 answers (it answers a read-only message itself, before the model): OFF by default for users
    (the operator, 2026-11-10); MEMBRANE_ANSWER=1 turns them on."""
    import os
    return os.environ.get("MEMBRANE_ANSWER", "0").strip().lower() in ("1", "on", "true", "yes")


def respond(srv, sid: str, text: str) -> str:
    """The reply membrane words itself from what it understood of `text`, or "" (nothing understood)."""
    return answer(srv, sid, text)["text"]


def _group_name(span) -> str:
    """What the message called a group: its nouns ("how many goals" -> "goals")."""
    words = [t.text for t in span if t.pos_ in ("NOUN", "PROPN", "NUM")]
    return " ".join(words) or span.text


def _said_of(tok, span) -> bool:
    """Is the condition word said of this node: it modifies the node ("goal two open?"), the
    node is its subject ("goal two landed"), or the node is the subject of the clause it completes
    ("is goal one open")? A condition said of nothing in particular ("make the blocked parts
    links") is checked against nothing."""
    inside, root = range(span.start, span.end), span.root
    if tok.head.i in inside:
        return True
    if root.head.i == tok.i and root.dep_ in ("nsubj", "nsubjpass"):
        return True
    return (tok.head.i == root.head.i and tok.dep_ in ("acomp", "attr", "oprd")
            and root.dep_ in ("nsubj", "nsubjpass", "attr"))


def answer(srv, sid: str, text: str) -> dict:
    """{text, ran}: the reply (see respond) and whether a read-only program ran for it."""
    from collections import Counter
    from . import generate as GEN
    from .ch import EntityView, Refused, evaluate
    est = srv.entity_store
    doc = _doc(text)
    adjectives = _adjectives(est)
    groups, conds, unknown = [], [], []          # groups: (what the message called them, [eids])
    for span, pos in _meaning_nodes(doc, _names(est)):
        t = span[0]
        adj = _adjective_of(t, adjectives) if len(span) == 1 else None
        if adj is not None and not (t.pos_ == "VERB" and _verb_at(est, doc, t.i)):   # "opens it": the verb
            conds.append((str((adj.properties or {}).get("name") or t.lower_),
                          (adj.properties or {}).get("filter") or "False", t))
            continue
        if len(span) == 1 and (_verb_at(est, doc, t.i) or t.lower_ in ADVERBS):
            continue                              # a verb is the program's, below
        got = _known_targets(srv, sid, est, span)
        if got:
            groups.append((_group_name(span), list(dict.fromkeys(got)), span))
        else:
            unknown.append(span.text)
    lines, ran = [], False
    try:
        steps = compile(srv, sid, text)
    except Exception:
        steps = []
    if steps and all(s["mapped"] and s.get("kind") != "define" and not s.get("writes") for s in steps):
        got_run = run(srv, text)                  # a read-only program: its result (stage 3)
        ran = True
        out = str(got_run.get("output") or got_run.get("said") or "").strip()
        if out:
            lines.append(out)

    def holds(expr, eid):
        """True / False, or None when the field can't tell (the condition doesn't evaluate)."""
        try:
            return bool(evaluate(expr, {"x": EntityView(est, eid)}))
        except Exception:
            return None
    for called, eids, span in groups:
        mine = [(w, x) for w, x, tok in conds if _said_of(tok, span)]     # only what the sentence says of it
        if len(eids) == 1:
            e = eids[0]
            label, st = CG_label(est, e), _state(est, e)
            for word, expr in mine:
                h = holds(expr, e)
                lines.append(f"can't tell whether {label} is {word}" if h is None else f"{label} is {word}" if h
                             else f"{label} isn't {word}" + (f" (status {st})" if st else ""))
            if not mine and st:               # a name alone says nothing: only a state is an answer
                lines.append(f"{label}: {st}")
            continue
        for word, expr in mine:
            hs = [holds(expr, e) for e in eids]
            k = sum(1 for h in hs if h)
            unsure = sum(1 for h in hs if h is None)
            lines.append(f"{k} of {len(eids)} {called} {'is' if k == 1 else 'are'} {word}"
                         + (f" ({unsure} can't tell)" if unsure else ""))
        if not mine:
            by = Counter(_state(est, e) or "no status" for e in eids)
            lines.append(f"{called}: {len(eids)} ({', '.join(f'{n} {st}' for st, n in by.most_common())})")
    if not lines:
        return {"text": "", "ran": ran}
    if unknown:
        lines.append("I don't know " + GEN.join([f'"{u}"' for u in unknown]))
    return {"text": "Now: " + "; ".join(lines), "ran": ran}       # the field as it is now, whatever the tense


# ---- filling the gaps (#887 B): membrane asks the model about the surprise words only ---------
# One small call per message (not the chat turn), on GAP_MODEL; each answer the field can keep is
# taught at once through the "X means Y" path, and every word asked is marked, never asked again.
GAP_MODEL = "haiku"          # the operator, 2026-11-03
GAP = MEMBRANE_NS + "gap:"
_NONE = re.compile(r"^\s*[\"']?([\w' -]+?)[\"']?\s*:\s*none\b", re.I)
_COLON = re.compile(r"^\s*[\"']?([a-z][\w' -]*?)[\"']?\s*:\s*(?!none\b)(\S[^:]*?)\s*[.!]?\s*$", re.I)


def gap_fill_on() -> bool:
    """The gap call (a model call that fills a message's unknown words): OFF by default for users (the operator,
    2026-11-10); MEMBRANE_GAP_FILL=1 turns it on."""
    import os
    return os.environ.get("MEMBRANE_GAP_FILL", "0").strip().lower() in ("1", "on", "true", "yes")


def _gap_eid(word: str) -> str:
    return GAP + re.sub(r"[^a-z0-9_-]+", "-", word.lower()).strip("-")


def gap_prompt(est, text: str, words: list) -> str:
    """The gap call's question -- blind: no field candidates in it (the operator, 2026-11-03, with
    Memo: shown the candidates, Haiku picked "failed = open" 1 in 5; blind, never)."""
    from . import verbs as V
    docs = list((est.docs or {}).values())
    verbs = sorted({str((d.properties or {}).get("name") or "") for d in docs if d.cls == V.CLASS_VERB} - {""})
    adjs = sorted(n for n in _adjectives(est) if n)
    return (f"A person wrote to an AI seat: {str(text)[:1500]!r}\n"
            f"The seat could not understand these words: {', '.join(words)}.\n"
            "For each word, answer on its own line in one of these forms, or \"<word>: none\" when none fits:\n"
            f"  <word> means <a verb it knows>: {', '.join(verbs)}\n"
            f"  <word> means <an adjective it knows>: {', '.join(adjs)}\n"
            "  <word> means <property> <value>  (a condition on a thing, e.g. \"landed means status merged\")\n"
            "  <word> means <the exact name of a thing the seat knows>\n"
            "Only when the word means exactly that in this message; when in doubt, \"<word>: none\". "
            "Answer only those lines.")


def _ask_model(prompt: str):
    """(the reply's text, its usage): one call on GAP_MODEL, no chat, no history."""
    from . import claude_agent as CA
    text, usage = [], {}
    for kind, val in CA.iter_reply([{"role": "user", "content": prompt}], GAP_MODEL, stream=False):
        if kind == "text":
            text.append(str(val))
        elif kind == "usage" and isinstance(val, dict):
            usage = val
    try:
        from . import bgusage
        bgusage.from_openai("gap", GAP_MODEL, {"usage": usage})   # no stored row holds it: the tally does
    except Exception:
        pass
    return "".join(text), usage


# ---- novelty (#891): a new meaning needs the model's word (asked blind) and the chat's evidence --
_NOT_STATE = {"name", "summary", "title", "text", "sid", "mid", "role", "speaker", "word", "filter",
              "method_name", "command", "same_as"}


def _tie(tok, span) -> str | None:
    """How the parse ties the word to this node: "state" (the node is its subject, or it is the
    predicate about the node: "846 landed", "is goal one open"), "object" (it acts on the node:
    "please peek goal one"), or None. Not a modifier: the small parser reads an unknown command
    at a sentence's start as one ("scan goal one": scan -amod-> goal)."""
    inside = range(span.start, span.end)
    root = span.root
    if root.head.i == tok.i and root.dep_ in ("dobj", "obj", "pobj", "dative"):
        return "object"
    if root.head.i == tok.i and root.dep_ in ("nsubj", "nsubjpass", "csubj"):
        acts = [c for c in tok.children if c.dep_ in ("dobj", "obj", "dative", "prep", "xcomp", "ccomp")]
        return None if acts else "state"         # "Claude will fix them": an action, not a state
    if tok.head.i == root.head.i and tok.i not in inside and tok.dep_ in ("acomp", "attr", "oprd") \
            and root.dep_ in ("nsubj", "nsubjpass"):
        return "state"
    return None


def _asks_or_denies(tok) -> bool:
    """A question or a negation asserts nothing the field can check the word against (Memo on
    #894: "is goal one merged?" made merged = open; "goal two is not landed" made landed = closed)."""
    sent = tok.sent
    if sent.text.rstrip().endswith("?") or sent[0].pos_ == "AUX":     # "is goal one merged", "did it land"
        return True
    return any(t.dep_ == "neg" for t in sent)      # "goal two and not goal three landed": any negation in the sentence


def _state_values(est, eid: str) -> list:
    """(prop, value) of a thing's plain one-word states: the conditions it could mean."""
    p = ((est.docs or {}).get(eid) or QuantumEntity(eid="", cls="")).properties or {}
    return [(k, v) for k, v in p.items() if isinstance(v, str) and not k.startswith("_") and k not in _NOT_STATE
            and k.isidentifier() and v and v.replace("-", "").replace("_", "").isalpha()]


INVENT_MIN_SEEN = 2         # the operator, 2026-11-03: messages that must agree before an invention is used
ASSOC = MEMBRANE_NS + "assoc:"


def _assoc_eid(sid: str, word: str) -> str:
    """Per chat (the operator: "unique to the convo associations"): a guest's words never mix in."""
    return ASSOC + re.sub(r"[^a-z0-9_-]+", "-", f"{sid}:{word}".lower()).strip("-")


def _holds(est, expr: str, eid: str):
    """True / False, or None when it doesn't evaluate (never counted as a "no")."""
    from .ch import EntityView, evaluate
    try:
        return bool(evaluate(expr, {"x": EntityView(est, eid)}))
    except Exception:
        return None


_PLAIN = {"nsubj", "nsubjpass", "aux", "auxpass", "punct", "det", "cc", "conj", "acomp", "attr", "expl"}


def _states(tok, inside=()) -> bool:
    """Does the word sit in a plain statement the field can check? An allowlist (Memo on #894: each
    round found more exceptions): the predicate's own parts may only be its subject, tense
    auxiliaries (not modals: "should be"), punctuation, and its complement -- no adverb ("almost",
    "probably", "when"), no interjection ("no, ..."), no mark ("if", "whether"), no negation -- and
    its clause stands on its own (not under "I want", "check whether"). Not a question."""
    if _asks_or_denies(tok):
        return False
    clause = tok.head if tok.dep_ in ("acomp", "attr", "oprd") else tok
    for t in {tok, clause}:
        for c in t.children:
            if c in inside:                      # the words of a multi-word gap itself ("big thing")
                continue
            if c.dep_ not in _PLAIN or (c.dep_ == "aux" and c.tag_ == "MD"):
                return False
            if c.dep_ == "det" and c.lower_ in ("no", "neither", "never", "any"):
                return False                     # "is no mess"
            if c.dep_ == "conj" and c.pos_ in ("VERB", "AUX"):
                return False                     # "landed, or did it": a second clause
    for subj in (c for c in clause.children if c.dep_ in ("nsubj", "nsubjpass")):
        if _glued_lead(subj, inside):
            return False                         # "hope goal two landed": a lead word the parse glued on
    return clause.dep_ in ("ROOT", "conj")


def _glued_lead(subj, inside=()) -> bool:
    """A lowercase proper-noun compound in the subject's subtree that is no part of the gap's own
    words: the lead word ("hope", "wish") the small parser glued onto a name. The compound of a
    multi-word name ("blocks panel") is a name's part, so a name's own words are passed in `inside`."""
    return any(c.dep_ == "compound" and c.pos_ == "PROPN" and c.text.islower() and c not in inside
               for c in subj.subtree)


def observe(srv, sid: str, text: str, word: str) -> dict | None:
    """The word's association edges in this chat for this message (the operator, 2026-11-03: "unique
    to the convo associations"): every message it appears in (`seen`), the distinct things a plain
    statement says it of (`things`, a `said_of` edge each) with which of their one-word states held
    (`held`: condition -> those things), and the distinct things it acts on as a verb (`acted_on`).
    Kept on membrane::assoc:<chat>:<word>; a message is counted once. None: the word isn't in it."""
    est = srv.entity_store
    doc = _doc(text)
    parts = word.lower().split()
    if not parts:
        return None
    inside: tuple = ()
    if len(parts) == 1:
        tok = next((t for t in doc if t.text == word or t.lower_ == word.lower()), None)
    else:                                        # a phrase ("big thing"): its last word stands for it
        tok = None
        for i in range(len(doc) - len(parts) + 1):
            if [t.lower_ for t in doc[i:i + len(parts)]] == parts:
                inside = tuple(doc[i:i + len(parts)])
                tok = inside[-1]
                break
    if tok is None:
        return None
    eid = _assoc_eid(sid, word)
    d = (est.docs or {}).get(eid) or QuantumEntity(eid=eid, cls="Association", properties={
        "name": word.lower(), "sid": sid, "seen": 0, "things": [], "held": {}, "acted_on": [],
        "silent_birth": True})
    p = dict(d.properties or {})
    key = " ".join(str(text).split())[:200]
    if p.get("last") == key:                     # the same message, before and after its turn: once
        return p
    said, acted = [], []
    for span, pos in _meaning_nodes(doc, _names(est)):
        if any(t.i in range(span.start, span.end) for t in (inside or (tok,))):
            continue
        how = _tie(tok, span)
        if how == "state" and _states(tok, tuple(inside) + tuple(span)):
            said += [e for e in _known_targets(srv, sid, est, span) if e not in said]
        elif how == "object" and not _asks_or_denies(tok):
            acted += [e for e in _known_targets(srv, sid, est, span) if e not in acted]
    if not said and not acted:
        return p                                 # nothing stated or acted on: not a message about it (Memo)
    p["seen"] = int(p.get("seen") or 0) + 1
    things, held = list(p.get("things") or []), {k: list(v) for k, v in (p.get("held") or {}).items()}
    learned = _adjectives(est).get(word.lower())
    if learned is not None and is_learned(est, learned):
        expr = (learned.properties or {}).get("filter") or "False"
        if any(_holds(est, expr, e) is False for e in said):      # the chat now says otherwise
            _unlearn(est, word, said, text)
    for e in said:
        if e not in things:
            things.append(e)
        for k, v in _state_values(est, e):
            c = f"x.{k} == {v!r}"
            if e not in held.setdefault(c, []):
                held[c].append(e)
    p["acted_on"] = list(dict.fromkeys(list(p.get("acted_on") or []) + acted))
    p.update(things=things, held=held, last=key, ts=time.time())
    d.properties = p
    est.upsert(d, embedder=None)
    try:
        from . import convgraph as CG
        be, origin = CG._rel(srv)
        for e in said:
            CG._edge(be, origin, eid, "said_of", e)
        for e in acted:
            CG._edge(be, origin, eid, "acts_on", e)
    except Exception:
        pass
    return p


def _peers(est, things: list) -> list:
    """The things' siblings (sharing a parent), or else their class: what a state's base rate is over."""
    from .entities import MultiParentOps
    docs = est.docs or {}
    parents = {q for e in things if docs.get(e) is not None for q in MultiParentOps._parents_of(docs[e].properties or {})}
    if parents:
        return [d.eid for d in list(docs.values())
                if set(MultiParentOps._parents_of(d.properties or {})) & parents]
    classes = {docs[e].cls for e in things if docs.get(e) is not None}
    return [d.eid for d in list(docs.values()) if d.cls in classes]


def _confirmed(est, a: dict, cond: str, recorded: bool = True) -> dict | None:
    """Does the chat's evidence carry `cond`: true of every one of k >= 2 distinct things the word
    was said of, over at least INVENT_MIN_SEEN messages, and able to fail (false for some other
    thing of their kind)? -> {k}. No chance test (the operator, 2026-11-03, with Memo): with the
    model naming the meaning blind, the evidence only has to rule meanings out, and p ** k measured
    how rare a state is on the seat, not what the word means."""
    things = list(a.get("things") or [])
    k = len(things)
    if int(a.get("seen") or 0) < INVENT_MIN_SEEN or k < 2:
        return None                              # one thing said twice is one thing (Memo on #894)
    if recorded and set((a.get("held") or {}).get(cond) or []) != set(things):
        return None
    if not all(_holds(est, cond, e) is True for e in things):
        return None
    if not any(_holds(est, cond, e) is False for e in _peers(est, things)):
        return None                              # true of every such thing: it can't fail, so it says nothing
    return {"k": k}


def _says_of(tok, inside=()) -> bool:
    """A looser test than _states, for unlearning only (Memo on #894): a statement still counts
    with a time word, a plain adverb, a place or a "yes," ("goal one landed today", "yes, goal one
    just landed"). Default-deny, an allowlist (each round of exceptions found more): anything the
    parse hangs on the predicate or its subject that is not allowed makes it not a statement --
    hedges ("hopefully", "almost certainly"), "seems", quantifiers ("most"), and any negative in the
    subject ("no", "neither", "nor"). `inside`: the words of the names the field knows, which are
    the subject's own ("field ui"), not lead words."""
    if _asks_or_denies(tok):
        return False
    clause = tok.head if tok.dep_ in ("acomp", "attr", "oprd") else tok
    if clause is not tok and clause.lemma_ != "be":
        return False                             # "seems landed", "looks landed": a report, not a fact
    names = {t.i for t in inside}
    for t in {tok, clause}:
        for c in t.children:
            if c is tok or c is clause:
                continue
            if c.i in names:                     # a name the parse hung on the verb ("field" of "field ui"):
                if all(_plain_subject_part(w, names) for w in c.subtree):     # its own words only, no lead
                    continue
                return False
            if c.dep_ in ("nsubj", "nsubjpass", "punct", "acomp", "attr", "expl", "auxpass"):
                continue
            if c.dep_ == "aux":
                if c.tag_ == "MD":
                    return False
                continue
            if c.dep_ == "cc":
                if c.lower_ in ("and", "&"):
                    continue
                return False
            if c.dep_ == "conj":
                if c.pos_ in ("VERB", "AUX"):
                    return False                 # "landed, or did it": a second clause
                continue
            if c.dep_ in ("advmod", "npadvmod") and _plain_modifier(c, names):
                continue
            if c.dep_ == "intj" and c.lower_ == "yes":
                continue
            if c.dep_ == "prep" and any(o.dep_ == "pobj" and o.pos_ in ("NOUN", "PROPN", "NUM") for o in c.children) \
                    and _plain_modifier(c, names):
                continue
            return False
    for subj in (c for c in clause.children if c.dep_ in ("nsubj", "nsubjpass")):
        if not all(_plain_subject_part(t, names) for t in subj.subtree):
            return False                         # a lead word, a quantifier, a negative, "or": not a plain subject
    return clause.dep_ in ("ROOT", "conj")


def _plain_subject_part(t, names) -> bool:
    """May this word sit in a subject that says something of one thing: a name's own word, an article or
    demonstrative (spaCy's PronType=Art|Dem), "and", punctuation, a number. Anything else ("hope", "no",
    "most", "neither", "or") may not."""
    if t.i in names or t.dep_ == "punct" or t.pos_ == "NUM":
        return True
    return (t.dep_ == "det" and _art_or_dem(t)) or (t.dep_ == "cc" and t.lower_ == "and")


def _art_or_dem(t) -> bool:
    return bool({"Art", "Dem"} & set(t.morph.get("PronType")))


def _plain_modifier(c, names) -> bool:
    """A time, manner or place phrase hung on the predicate that adds nothing to what is said: structural,
    not lexical. Its subtree carries no negative (`neg`), no determiner but an article or demonstrative
    ("no", "any" are not), and no hedge-shaped adverb of its own ("almost certainly")."""
    for w in c.subtree:
        if w.i in names:
            continue
        if w.dep_ == "neg" or (w.dep_ == "det" and not _art_or_dem(w)):
            return False
    return True


def _subjects(tok, est, srv, sid) -> list:
    """The things a statement says `tok` of: every thing named inside its subject's span, so
    joined subjects count too ("goal two and goal one landed"; the parser's tree for those is
    unreliable, the field's names aren't)."""
    clause = tok.head if tok.dep_ in ("acomp", "attr", "oprd") else tok
    subj = [c for c in clause.children if c.dep_ in ("nsubj", "nsubjpass")]
    if not subj:
        return []
    lo = min(t.i for c in subj for t in c.subtree)
    hi = max(t.i for c in subj for t in c.subtree)
    hi = max(hi, max(t.i for t in tok.doc[lo:clause.i]) if clause.i > lo else hi)   # names the parse split off
    nodes = _meaning_nodes(tok.doc, _names(est))
    for span, pos in nodes:                      # a multi-word name the parse split ("field" off "ui") is whole
        if span.end > lo and span.start <= hi and tok.i not in range(span.start, span.end) and _known_targets(srv, sid, est, span):
            lo, hi = min(lo, span.start), max(hi, span.end - 1)
    out = []
    for span, pos in nodes:
        if span.start >= lo and span.end <= hi + 1 and tok.i not in range(span.start, span.end):
            for e in _known_targets(srv, sid, est, span):
                if e not in out:
                    out.append(e)
    return out


def observe_learned(srv, sid: str, text: str) -> list:
    """Every learned word the message uses, in every sentence, is checked against what a
    statement says it of, so a contradiction unlearns it before membrane answers (Memo on #894: a
    learned word is no surprise, so nothing saw it; and natural statements -- "goal one landed
    today", "yes, goal one landed" -- must count). -> the words unlearned."""
    est = srv.entity_store
    learned = {n: d for n, d in _adjectives(est).items() if is_learned(est, d)}
    if not learned:
        return []
    gone = []
    for t in _doc(text):
        w = t.lower_
        if w not in learned or w in gone:
            continue
        inside = tuple(x for span in _named(t.doc, _names(est)) if t.i not in range(span.start, span.end)
                       for x in span)               # the words of the things the field names, no lead word
        if not _says_of(t, inside):
            continue
        if _gap_in(srv, sid, text):              # a word the field does not know (a hedge too): no contradiction
            continue
        expr = (learned[w].properties or {}).get("filter") or "False"
        said = _subjects(t, est, srv, sid)
        if any(_holds(est, expr, e) is False for e in said):
            _unlearn(est, w, said, text)
            gone.append(w)
    return gone


def _gap_in(srv, sid: str, text: str) -> bool:
    """Did the sentence compile with a gap (#887)? A word the meaning has never seen leaves a surprise in
    its understanding, so the sentence cannot contradict anything (Memo on #911: no hedge list)."""
    u = understand(srv, sid, text)
    return bool(u.get("surprise")) or (u.get("share") is not None and u["share"] < 1.0)


def _unlearn(est, word: str, said: list, text: str = "") -> None:
    """A learned meaning the chat contradicts goes back to a proposal (Memo on #894)."""
    eid = _adj_eid(word)
    try:
        est.remove(eid)
    except Exception:
        (est.docs or {}).pop(eid, None)
    g = (est.docs or {}).get(_gap_eid(word))
    if g is not None:
        g.properties = {**g.properties, "kept": False, "contradicted_by": list(said), "contradicted_ts": time.time(),
                        "contradicted_text": " ".join(str(text).split())[:200]}
        est.upsert(g, embedder=None)


def confirm_proposed(srv, sid: str, text: str, word: str) -> dict | None:
    """The model's proposed meaning for `word`, taught once this chat's field evidence agrees
    (the operator, 2026-11-03: a meaning needs both -- the model's word for it and the chat's
    evidence, k >= 2 distinct things -- and never picks a write's targets). Only a condition has
    field evidence (_confirmed); a verb synonym or another name stays a proposal for the person to
    teach ("X means Y"): recurrence isn't evidence (Memo: "review means show" posted "Reviewed
    goal two."). A person's own definition is never replaced. -> {word, meaning, k} when taught."""
    est = srv.entity_store
    g = (est.docs or {}).get(_gap_eid(word))
    plan = ((g.properties or {}).get("proposed") or {}) if g is not None else {}
    if not plan or (g.properties or {}).get("kept") or plan.get("kind") != "adjective":
        return None
    if (g.properties or {}).get("contradicted_text") == " ".join(str(text).split())[:200]:
        return None                              # this very message just unlearned it: not taught back by it
    d0 = (est.docs or {}).get(_adj_eid(word))
    if d0 is not None and not is_learned(est, d0):
        return None                              # the person defined it: a proposal never replaces that (Memo on #894)
    if " " in word.strip():                      # a phrase ("big thing"): nothing reads one as a word yet
        return None                              # (Memo on #894), so it stays a proposal
    a = observe(srv, sid, text, word)
    ok = _confirmed(est, a, plan["filter"], recorded=False) if a else None
    if ok is None:
        return None
    shown = teach(est, {"plan": plan, "word": word.lower()})
    d = (est.docs or {}).get(_adj_eid(word))
    if d is not None:
        d.properties = {**d.properties, "learned": True, "learned_evidence": ok, "learned_ts": time.time()}
        est.upsert(d, embedder=None)
    g.properties = {**g.properties, "kept": True, "confirmed_ts": time.time()}
    est.upsert(g, embedder=None)
    return {"word": word, "meaning": shown, **ok}


GAP_WAIT_S = 30.0       # #887 C: how long a person's turn waits for the gap call (the operator, 2026-11-03: 30 s)
_ASKING: set = set()     # the words a gap call is out for now: never asked twice at once (Memo on #892)
_ASKING_LOCK = threading.Lock()


def fill_gaps_before(srv, sid: str, text: str, words: list, wait_s: float | None = None) -> dict | None:
    """fill_gaps on its own thread, waited for at most GAP_WAIT_S: a slow or hung call never
    holds the person's turn. Late -> {words: [], late: True}; its answers are still kept when
    they come (the words are marked then)."""
    box: dict = {}
    th = threading.Thread(target=lambda: box.update(r=fill_gaps(srv, sid, text, words)), daemon=True,
                          name="gap-fill")
    th.start()
    th.join(GAP_WAIT_S if wait_s is None else wait_s)
    if th.is_alive():
        return {"words": [], "usage": {}, "late": True}
    return box.get("r")


def fill_gaps(srv, sid: str, text: str, words: list, ask=None) -> dict | None:
    """Ask the model what this message's surprise words mean, keep what the field can, mark them
    asked -> {words: [{word, answer, kept, meaning}], usage} ({error} when the call failed: then
    nothing is marked); None when it's switched off."""
    if not gap_fill_on():
        return None
    est = srv.entity_store
    later = []                                   # asked before, proposed: the chat may now back it up
    for w in dict.fromkeys(str(w).strip() for w in words):
        g = (est.docs or {}).get(_gap_eid(w)) if w else None
        if g is not None and not (g.properties or {}).get("kept"):
            try:
                ok = confirm_proposed(srv, sid, text, w)
            except Exception:
                ok = None
            if ok is not None:
                later.append({"word": w, "answer": (g.properties or {}).get("answer"), "kept": True,
                              "meaning": ok["meaning"], "confirmed": True})
    wanted = [w for w in dict.fromkeys(str(w).strip() for w in words) if w and _gap_eid(w) not in (est.docs or {})]
    with _ASKING_LOCK:
        todo = [w for w in wanted if w.lower() not in _ASKING]
        _ASKING.update(w.lower() for w in todo)
    pending = [w for w in wanted if w not in todo]
    if not todo:
        return {"words": later, "usage": {}, **({"pending": pending} if pending else {})}
    try:
        ask_for = list(todo)
        try:
            reply, usage = (ask or _ask_model)(gap_prompt(est, text, ask_for))   # blind (#894)
        except Exception as e:
            return {"words": later, "usage": {}, "error": f"{type(e).__name__}: {e}"[:200]}
        got = _keep_answers(srv, sid, est, ask_for, reply, usage, pending, text)
        got["words"] = later + got["words"]
        return got
    finally:
        with _ASKING_LOCK:
            _ASKING.difference_update(w.lower() for w in todo)


def _mark_asked(est, sid: str, w: str, answer, kept: bool, proposed=None) -> None:
    est.upsert(QuantumEntity(eid=_gap_eid(w), cls="Gap", properties={
        "name": w.lower(), "answer": answer, "kept": kept, "asked_ts": time.time(), "sid": sid,
        "silent_birth": True, **({"proposed": proposed} if proposed else {})}), embedder=None)


def _keep_answers(srv, sid: str, est, todo: list, reply, usage, pending: list, text: str = "") -> dict:
    answers: dict = {}
    for line in str(reply or "").splitlines():
        line = line.replace("`", "").strip(" -*")
        m = _NONE.match(line)
        if m:
            answers.setdefault(m.group(1).strip().lower(), None)
            continue
        m = _DEFINE.match(line)
        if m:                                    # "landed means closed: closed" -> closed
            answers.setdefault(m.group(1).strip().lower(), m.group(2).split(":", 1)[0].strip())
            continue
        m = _COLON.match(line)                   # "landed: closed" (Memo on #894: 13 of 33 right answers)
        if m:
            answers.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    out = []
    for w in todo:
        if w.lower() not in answers:             # the reply never said: not asked, so it's asked again
            out.append({"word": w, "answer": None, "kept": False, "unparsed": True})
            continue
        meaning = answers.get(w.lower())
        plan = None
        if meaning:
            try:
                step = _define_step(srv, est, w.lower(), meaning, f"{w} means {meaning}")
                plan = step["plan"] if step["mapped"] else None
            except Exception:
                plan = None
        _mark_asked(est, sid, w, meaning, False, proposed=plan)  # proposed: taught once the chat backs it up
        got = confirm_proposed(srv, sid, text, w) if plan else None
        out.append({"word": w, "answer": meaning, "kept": got is not None, "proposed": plan is not None,
                    "meaning": (got or {}).get("meaning")})
    return {"words": out, "usage": usage, **({"pending": pending} if pending else {})}


def shadow_report(rows: list[dict]) -> dict:
    """The shadow's rates: how many messages compiled, had gaps (by type), needed thinking; and
    how much of them membrane understood, with the words that surprised it most (#887)."""
    from collections import Counter
    out = Counter(r.get("outcome") for r in rows)
    gaps = Counter(g.get("type") for r in rows for g in (r.get("gaps") or []))
    tools = Counter(r.get("outcome") for r in rows if r.get("model_tools"))
    us = [r["understanding"] for r in rows
          if isinstance(r.get("understanding"), dict) and r["understanding"].get("share") is not None]
    words = Counter(str(w).lower() for u in us for w in (u.get("surprise") or []))
    return {"total": len(rows), "outcomes": dict(out), "gap_types": dict(gaps), "model_used_tools": dict(tools),
            "novelty": _novelty(rows),
            "understanding": {"scored": len(us),
                              "mean_share": round(sum(u["share"] for u in us) / len(us), 3) if us else None,
                              "surprise_top": [[w, c] for w, c in words.most_common(20)]}}


def _novelty(rows: list[dict]) -> dict:
    """#891: how the filled words fared -- learned (the model's word, the chat's evidence),
    proposed and waiting, none."""
    ws = [w for r in rows for w in (((r.get("filled") or {}).get("words")) or []) if isinstance(w, dict)]
    return {"learned": sum(1 for w in ws if w.get("kept")),
            "proposed": sum(1 for w in ws if w.get("proposed") and not w.get("kept")),
            "none": sum(1 for w in ws if not w.get("kept") and not w.get("proposed"))}


def shadow_cli(argv=None) -> int:
    """`membrane shadow`: the shadow's rates on this seat (reads the field, never writes)."""
    import json as _json
    from pathlib import Path
    from .sqlitedb import open_db, store_dir
    db_path = store_dir(Path(".membrane"))
    if db_path is None:
        print("no seat here (no .membrane/field.sqlite or .membrane/field.cblite)")
        return 1
    try:
        d = open_db(db_path)
    except Exception as e:
        print(f"can't open the field: {e}")
        return 1
    try:                                   # an entity row is {e: eid, d: its doc}
        got = d.query("SELECT d.data.properties.shadow AS s FROM membrane.entities "
                      "WHERE d.data.properties.shadow IS VALUED")
    finally:
        d.close()
    rows = [r["s"] for r in got if isinstance(r.get("s"), dict)]
    print(_json.dumps(shadow_report(rows), indent=2))
    return 0


def _register_lang() -> None:
    from . import lang as LG
    LG.LANGS["en"] = LG.Lang("en", parse, LG.print_, EXECUTOR, run)


_register_lang()
