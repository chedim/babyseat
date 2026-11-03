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
    """#891: a definition taught over an invented meaning corrects it (counted, never lost)."""
    from . import verbs as V
    for eid in (_adj_eid(word), V.verb_eid(word)):
        d = (est.docs or {}).get(eid)
        if d is not None and (d.properties or {}).get("invented"):
            d.properties = {**d.properties, "invented": False, "corrected": True, "corrected_ts": time.time()}
            est.upsert(d, embedder=None)


def teach(est, step: dict) -> str:
    """Keep a definition step: the adjective, the verb synonym, the other name. -> what it means."""
    from . import verbs as V
    p, word = step["plan"], step["word"]
    _correct(est, word)
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


def answer_on() -> bool:
    """Membrane's own 🧩 answers: on by default; MEMBRANE_ANSWER=0 turns them off."""
    import os
    return os.environ.get("MEMBRANE_ANSWER", "1").strip().lower() not in ("0", "off", "false", "no")


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


def gap_fill_on() -> bool:
    """On by default; MEMBRANE_GAP_FILL=0 turns it off."""
    import os
    return os.environ.get("MEMBRANE_GAP_FILL", "1").strip().lower() not in ("0", "off", "false", "no")


def _gap_eid(word: str) -> str:
    return GAP + re.sub(r"[^a-z0-9_-]+", "-", word.lower()).strip("-")


def gap_prompt(est, text: str, words: list) -> str:
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
    return "".join(text), usage


# ---- novelty (#891): a meaning invented as code, proposed by tensors, checked on the field ------
INVENT_CANDIDATES = 5       # the operator, 2026-11-03: the nearest candidates checked per word
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
    for t in (tok, tok.head):
        if any(c.dep_ == "neg" for c in t.children):
            return True
    return False


def _state_values(est, eid: str) -> list:
    """(prop, value) of a thing's plain one-word states: the conditions it could mean."""
    p = ((est.docs or {}).get(eid) or QuantumEntity(eid="", cls="")).properties or {}
    return [(k, v) for k, v in p.items() if isinstance(v, str) and not k.startswith("_") and k not in _NOT_STATE
            and k.isidentifier() and v and v.replace("-", "").replace("_", "").isalpha()]


INVENT_MIN_SEEN = 2         # the operator, 2026-11-03: messages that must agree before an invention is used
INVENT_ALPHA = 0.05         # the operator, 2026-11-03: the chance-agreement limit (p ** k, see _confirmed)
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


def _states(tok) -> bool:
    """Does the word sit in a plain statement the field can check? Not a question, a negation, a
    conditional or "whether" clause, a modal ("should be landed"), or a clause under another verb
    ("I want goal two landed", "check whether it landed") (Memo on #894)."""
    if _asks_or_denies(tok):
        return False
    for t in (tok, tok.head):
        if any(c.dep_ == "mark" or (c.dep_ == "aux" and c.tag_ == "MD") for c in t.children):
            return False
    clause = tok.head if tok.dep_ in ("acomp", "attr", "oprd") else tok
    return clause.dep_ not in ("xcomp", "ccomp", "advcl", "csubj")


def observe(srv, sid: str, text: str, word: str) -> dict | None:
    """The word's association edges in this chat for this message (the operator, 2026-11-03: "unique
    to the convo associations"): every message it appears in (`seen`), the distinct things a plain
    statement says it of (`things`, a `said_of` edge each) with which of their one-word states held
    (`held`: condition -> those things), and the distinct things it acts on as a verb (`acted_on`).
    Kept on membrane::assoc:<chat>:<word>; a message is counted once. None: the word isn't in it."""
    est = srv.entity_store
    if " " in word.strip():
        return None
    doc = _doc(text)
    tok = next((t for t in doc if t.text == word or t.lower_ == word.lower()), None)
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
        if tok.i in range(span.start, span.end):
            continue
        how = _tie(tok, span)
        if how == "state" and _states(tok):
            said += [e for e in _known_targets(srv, sid, est, span) if e not in said]
        elif how == "object" and not _asks_or_denies(tok):
            acted += [e for e in _known_targets(srv, sid, est, span) if e not in acted]
    p["seen"] = int(p.get("seen") or 0) + 1
    things, held = list(p.get("things") or []), {k: list(v) for k, v in (p.get("held") or {}).items()}
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
    """Does the chat's evidence carry `cond`: said of k distinct things, true of every one, over at
    least INVENT_MIN_SEEN messages, and too rare to agree by chance (its base rate p over their
    peers: p ** k <= INVENT_ALPHA; 88 fulfilled of 98 goals would take ~29 things)? -> {p, k}."""
    things = list(a.get("things") or [])
    k = len(things)
    if int(a.get("seen") or 0) < INVENT_MIN_SEEN or not k:
        return None
    if recorded and set((a.get("held") or {}).get(cond) or []) != set(things):
        return None                              # (a model's proposal is any expression: checked below)
    if not all(_holds(est, cond, e) is True for e in things):
        return None
    vals = [_holds(est, cond, e) for e in _peers(est, things)]
    vals = [v for v in vals if v is not None]
    if not vals or all(vals):
        return None                              # true of every such thing: it can't fail, so it says nothing
    pr = sum(1 for v in vals if v) / len(vals)
    return {"p": round(pr, 3), "k": k} if pr ** k <= INVENT_ALPHA else None


def invent(srv, sid: str, text: str, word: str, embed=None) -> dict | None:
    """A meaning for `word` the chat's association edges confirm (see _confirmed) -> {word, kind,
    meaning, score, p, k}, kept at once and marked invented; None otherwise (the model is asked
    then). The word's vector ranks the candidates; the top INVENT_CANDIDATES are checked. No verb
    is invented (Memo on #894: "please review goal one" made review = check)."""
    import numpy as np
    est = srv.entity_store
    a = observe(srv, sid, text, word)
    if not a:
        return None
    every = [c for c, ts in (a.get("held") or {}).items() if set(ts) == set(a.get("things") or [])
             and all(_holds(est, c, e) is True for e in (a.get("things") or []))]   # and still true now
    if not every:
        return None                              # a mood word: no state held every time ("great")
    emb = embed or getattr(getattr(srv, "efield", None), "emb", None)
    ranked = [(0.0, c) for c in every]
    if emb is not None:
        try:
            vals = [c.split("==", 1)[1].strip().strip("'\"") for c in every]
            vecs = np.asarray(emb.embed([word] + vals), dtype=float)
            w, cv = vecs[0], vecs[1:]
            sims = cv @ w / ((np.linalg.norm(cv, axis=1) * (np.linalg.norm(w) or 1.0)) + 1e-12)
            ranked = sorted(zip(sims.tolist(), every), key=lambda x: -x[0])
        except Exception:
            pass
    for score, meaning in ranked[:INVENT_CANDIDATES]:
        ok = _confirmed(est, a, meaning)
        if ok is None:
            continue
        eid = define_adjective(est, word.lower(), meaning)
        d = est.docs[eid]
        d.properties = {**(d.properties or {}), "invented": True, "invented_from": str(text)[:300],
                        "invented_score": round(float(score), 3), "invented_ts": time.time(),
                        "invented_evidence": ok, "candidates": [m for _, m in ranked[:INVENT_CANDIDATES]]}
        est.upsert(d, embedder=None)
        return {"word": word, "kind": "adjective", "meaning": meaning, "score": round(float(score), 3), **ok}
    return None


def confirm_proposed(srv, sid: str, text: str, word: str) -> dict | None:
    """A meaning the model proposed for `word` (kept on its gap mark), taught once the chat backs it
    up (the operator, 2026-11-03: the model's answers go through the same d-build): a condition by
    _confirmed; a verb synonym once the word acted on things that verb applies to, read-only only,
    in INVENT_MIN_SEEN messages (a write is never taught this way); another name once the word
    recurs in INVENT_MIN_SEEN messages. -> {word, meaning} when taught."""
    from . import verbs as V
    est = srv.entity_store
    g = (est.docs or {}).get(_gap_eid(word))
    plan = ((g.properties or {}).get("proposed") or {}) if g is not None else {}
    if not plan or (g.properties or {}).get("kept"):
        return None
    a = observe(srv, sid, text, word) or (est.docs or {}).get(_assoc_eid(sid, word))
    a = a.properties if hasattr(a, "properties") else a
    if not a or int(a.get("seen") or 0) < INVENT_MIN_SEEN:
        return None
    if plan["kind"] == "adjective":
        if _confirmed(est, a, plan["filter"], recorded=False) is None:
            return None
    elif plan["kind"] == "verb":
        objs = list(a.get("acted_on") or [])
        ms = [V.implementation(est, (est.docs or {}).get(e), plan["same_as"])[0] for e in objs]
        if not objs or not all(m is not None and (m.properties or {}).get("read_only") for m in ms):
            return None
    shown = teach(est, {"plan": plan, "word": word.lower()})
    g.properties = {**g.properties, "kept": True, "confirmed_ts": time.time()}
    est.upsert(g, embedder=None)
    return {"word": word, "meaning": shown}


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
    later = []                                   # #891: asked before, nothing kept: the conversation may still tell
    for w in dict.fromkeys(str(w).strip() for w in words):
        g = (est.docs or {}).get(_gap_eid(w)) if w else None
        if g is not None and not (g.properties or {}).get("kept"):
            try:
                ok = confirm_proposed(srv, sid, text, w)
            except Exception:
                ok = None
            if ok is not None:                   # the model's proposal, now backed by the chat
                later.append({"word": w, "answer": (g.properties or {}).get("answer"), "kept": True,
                              "meaning": ok["meaning"], "confirmed": True})
                continue
            try:
                inv = invent(srv, sid, text, w)
            except Exception:
                inv = None
            if inv is not None:
                _mark_asked(est, sid, w, inv["meaning"], True, invented=True)
                later.append({"word": w, "answer": inv["meaning"], "kept": True, "meaning": inv["meaning"],
                              "invented": True, "score": inv["score"]})
    wanted = [w for w in dict.fromkeys(str(w).strip() for w in words) if w and _gap_eid(w) not in (est.docs or {})]
    with _ASKING_LOCK:
        todo = [w for w in wanted if w.lower() not in _ASKING]
        _ASKING.update(w.lower() for w in todo)
    pending = [w for w in wanted if w not in todo]
    if not todo:
        return {"words": later, "usage": {}, **({"pending": pending} if pending else {})}
    try:
        invented = list(later)                   # #891: the conversation's associations first, the model for the rest
        for w in list(todo):
            try:
                inv = invent(srv, sid, text, w)
            except Exception:
                inv = None
            if inv is not None:
                _mark_asked(est, sid, w, inv["meaning"], True, invented=True)
                invented.append({"word": w, "answer": inv["meaning"], "kept": True, "meaning": inv["meaning"],
                                 "invented": True, "score": inv["score"]})
        ask_for = [w for w in todo if w not in {i["word"] for i in invented}]
        if not ask_for:
            return {"words": invented, "usage": {}, **({"pending": pending} if pending else {})}
        try:
            reply, usage = (ask or _ask_model)(gap_prompt(est, text, ask_for))
        except Exception as e:
            return {"words": invented, "usage": {}, "error": f"{type(e).__name__}: {e}"[:200]}
        got = _keep_answers(srv, sid, est, ask_for, reply, usage, pending, text)
        got["words"] = invented + got["words"]
        return got
    finally:
        with _ASKING_LOCK:
            _ASKING.difference_update(w.lower() for w in todo)


def _mark_asked(est, sid: str, w: str, answer, kept: bool, invented: bool = False, proposed=None) -> None:
    est.upsert(QuantumEntity(eid=_gap_eid(w), cls="Gap", properties={
        "name": w.lower(), "answer": answer, "kept": kept, "asked_ts": time.time(), "sid": sid,
        "silent_birth": True, **({"invented": True} if invented else {}),
        **({"proposed": proposed} if proposed else {})}), embedder=None)


def _keep_answers(srv, sid: str, est, todo: list, reply, usage, pending: list, text: str = "") -> dict:
    answers: dict = {}
    for line in str(reply or "").splitlines():
        m = _NONE.match(line)
        if m:
            answers.setdefault(m.group(1).strip().lower(), None)
            continue
        m = _DEFINE.match(line.strip(" -*"))
        if m:
            answers.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    out = []
    for w in todo:
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
    """#891: how the filled words got their meaning -- invented by tensors, kept from the model, none."""
    ws = [w for r in rows for w in (((r.get("filled") or {}).get("words")) or []) if isinstance(w, dict)]
    return {"invented": sum(1 for w in ws if w.get("invented")),
            "model_kept": sum(1 for w in ws if w.get("kept") and not w.get("invented")),
            "none": sum(1 for w in ws if not w.get("kept"))}


def shadow_cli(argv=None) -> int:
    """`membrane shadow`: the shadow's rates on this seat (reads the field, never writes)."""
    import json as _json
    from pathlib import Path
    from . import cblite as CB
    db_path = Path(".membrane") / "field.cblite"
    if not db_path.exists():
        print("no seat here (no .membrane/field.cblite)")
        return 1
    try:
        d = CB.Database(CB.load(), "field", str(db_path))
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
