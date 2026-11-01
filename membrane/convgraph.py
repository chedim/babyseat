"""THE CONVERSATION AS A GRAPH OVER THE FIELD (the operator, 2026-11-01: "WE CAN REPRESENT
THE WHOLE CONVERSATION AS A GRAPH over semantic field").

Every chat turn mints both messages as `msg:<message id>` entities and writes the edges
(relations rows) that tie the talk to the rest of the field:

  person msg -follows->   the reply before it        reply -follows-> its person msg
  either     -refers-to-> the specific entities it names (files, commands, store
                          entities by name: eids.mentions_of)
  person msg -led-to->    every program root its turn ran (lang.run -> note_root)
  reply      -asks:N->    the Decision card of its N-th open decision
  person msg -answers->   the card its "N. value" answers (decision_map.answer)

DECISIONS IN THE CHAT: the numbered items under a reply's "Open decisions" are Decision
cards in that chat (the same question is the same card; when a reply lists its decisions,
an earlier text question it no longer lists is dropped, quietly); a card an agent opened in
the chat and nobody asked yet is posted by membrane, numbered after the reply's own. The
person answers in the chat: "1. yes", "2. no", a bare answer when one card is open,
"yesx2" for two. A bare answer with several open answers nothing (a free-text match would
need a cut-off the operator has not set).

THE REFERENT ("delete it"): an imperative whose object is a pronoun reads its antecedent
from the reply before (its -refers-to-> targets). Readings that can't apply drop: when a
candidate has a method of the verb's name, those without one; then a candidate whose state
is closed. One left is recorded (-refers-to-> from the person's message); two or more are
asked back in the chat ('"it": a or b?') and the turn does not run -- the person's reply
then reads normally. The answer resolves that one reference and trains nothing.
"""
from __future__ import annotations

import hashlib
import re
import time

from .entities import CLASS_DECISION, MEMBRANE_NS, QuantumEntity

MSG = MEMBRANE_NS + "msg:"
CLASS_MESSAGE = "Message"
SOURCE = "chat"
TEXT_MAX = 4000
ASKED_FROM_TEXT = "chat-text"          # a card made from a reply's numbered question

_HEAD = re.compile(r"^[\W_]*open\s+decisions?[\W_]*(:.*)?$", re.I)
_ITEM = re.compile(r"^\s*(\d+)[.)]\s+(.+?)\s*$")
_YESNO = r"(?:yes|y|yep|yeah|no|nope|n)"
# an answer's marker: "N." / "N)" then its text, or a bare "N" then a yes/no word ("1 yes")
_MARK = re.compile(r"(?:^|(?<=\s))(\d+)(?:[.)]\s*|\s+(?=" + _YESNO + r"\b))", re.I)
_TIMES = re.compile(r"^\s*(yes|no)\s*x\s*(\d+)\s*$", re.I)
_BARE = re.compile(r"^\s*(" + _YESNO + r")\s*[.!]*\s*$", re.I)
_NOT_ACTS = {"is", "was", "are", "be", "do", "does", "did", "have", "has", "had", "get", "got", "like", "love"}
_CLOSED = {"closed", "dropped", "fulfilled", "merged", "retired", "done", "deleted"}


def is_chat(agent) -> bool:
    """A person's chat (build / plan / a speaker), never a loop's session."""
    return str(agent or "").strip().lower() in ("", "build", "plan", "speaker")


def msg_eid(mid: str) -> str:
    return MSG + str(mid)


def _rel(srv):
    from .night import _relations_backend_of
    return _relations_backend_of(srv)


def _out(be, origin, eid: str, prefix: str = "") -> list[dict]:
    from . import relations as R
    rows = [r for r in R.of(be, origin, eid) if r.get("subj") == eid
            and str(r.get("rel") or "").startswith(prefix)]
    return sorted(rows, key=lambda r: (float(r.get("first_ts") or r.get("ts") or 0), str(r.get("rel"))))


def _edge(be, origin, subj: str, rel: str, obj: str, source: str = SOURCE) -> None:
    from . import relations as R
    if be is not None and subj and obj:
        R.upsert(be, origin, subj, rel, obj, source, ts=time.time())


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w#./:-]+", " ", str(s).lower()).split())


# ---- the turn's runs ---------------------------------------------------------------------

def note_root(srv, root: str) -> None:
    """lang.run: a program root ran in the current chat turn (the runner's per-thread sid)."""
    runner = getattr(srv, "_loop_runner", None)
    sid = getattr(runner, "_current_turn_sid", None) if runner is not None else None
    if sid and root:
        roots = runner.__dict__.setdefault("_cg_roots", {})
        roots.setdefault(sid, [])
        if root not in roots[sid]:
            roots[sid].append(root)


# ---- messages ----------------------------------------------------------------------------

def _words(text: str) -> tuple[str | None, str]:
    """(the speaker, the person's words): a chat client's speaker line ('[user: X]',
    the JWT stamp) heads the text and is not part of what they said."""
    from .speakers import split_speaker, strip_speaker_lines
    who, _rest = split_speaker(text or "")
    return who, strip_speaker_lines(text or "").strip()


def _mint(est, sid: str, mid: str, role: str, text: str, ts=None, speaker=None) -> str:
    eid = msg_eid(mid)
    if eid not in (est.docs or {}):
        first = next((ln for ln in str(text or "").splitlines() if ln.strip()), "")
        props = {"name": " ".join(first.split())[:80] or f"{role} message", "role": role, "sid": sid,
                 "mid": mid, "text": str(text or "")[:TEXT_MAX], "ts": ts or time.time(),
                 "silent_birth": True}
        if speaker:
            props["speaker"] = speaker
        est.upsert(QuantumEntity(eid=eid, cls=CLASS_MESSAGE, properties=props), embedder=None)
    return eid


def _row_before(runner, sid: str, mid: str, role: str | None = None):
    rows = runner.messages.list(sid) if runner is not None else []
    idx = next((i for i, r in enumerate(rows) if (r.get("info") or {}).get("id") == mid), None)
    for r in reversed(rows[:idx] if idx is not None else rows):
        info = r.get("info") or {}
        if role is None or info.get("role") == role:
            return info
    return None


_GH = re.compile(r"^u:https?://github\.com/([\w.-]+/[\w.-]+)/(?:pull|issues)/(\d+)")
# "#N" is an issue or PR only after a word that says so: membrane numbers its own events "#N"
_HASH_N = re.compile(r"\b(?:prs?|pull(?:\s+request)?|issues?|merged?|review(?:ed|ing)?|closes|fixes)"
                     r"\s+(?:[\w-]+\s+)?#(\d{1,6})\b", re.I)
# a turn membrane itself started (a watch, a decision, a goal's report): not the person's words
_NOTICE = re.compile(r"^\s*(?:\[(?:membrane|watch|goal|decision|seat|job|loop|triage)\b|goal \S+ (?:fulfilled|\())", re.I)


def from_person(text: str) -> bool:
    return not _NOTICE.match(_words(text)[1])
_REPO: dict = {}


def _repo(srv) -> str | None:
    """owner/repo of the seat's project (its git remote), for "#803": None without one."""
    if "r" not in _REPO:
        import subprocess
        try:
            url = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except Exception:
            url = ""
        m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
        _REPO["r"] = m.group(1) if m else None
    return _REPO["r"]


def _issue(repo: str, n) -> str:
    """One entity per issue or PR (GitHub numbers them together): the issue URL."""
    return f"u:https://github.com/{repo}/issues/{int(n)}"


def _mentions(srv, text: str) -> list[str]:
    from . import eids as E
    fld = getattr(srv, "efield", None)
    graph = None
    phrases = getattr(fld, "phrases", None) if fld is not None else None
    try:
        gf = getattr(fld, "graph_fn", None)
        graph = gf() if callable(gf) else None
    except Exception:
        graph = None
    out = [e for e, _ in E.mentions_of(text or "", graph, phrases) if E.is_specific(e)]
    est = srv.entity_store
    for i, e in enumerate(out):
        if e.startswith("m:"):            # the field's mirror of a store entity: the store's eid
            d = (est.docs or {}).get(MEMBRANE_NS + e[2:])
            if d is not None:
                out[i] = d.eid
        m = _GH.match(e)
        if m:                             # a pasted PR or issue link: the issue's one entity
            out[i] = _issue(m.group(1), m.group(2))
    repo = _repo(srv)
    if repo:
        out += [_issue(repo, n) for n in _HASH_N.findall(text or "")]
    return list(dict.fromkeys(out))


# ---- decisions -----------------------------------------------------------------------------

def asked_items(text: str) -> list[tuple[int, str]] | None:
    """The numbered items under the reply's last "Open decisions" header; None when it has
    no such header ([] for "Open decisions: none")."""
    lines = str(text or "").splitlines()
    at = None
    for i, ln in enumerate(lines):
        if _HEAD.match(ln.strip()):
            at = i
    if at is None:
        return None
    items = []
    for ln in lines[at + 1:]:
        m = _ITEM.match(ln)
        if m:
            items.append((int(m.group(1)), m.group(2).strip()))
        elif ln.strip() and items:
            break
    return items


def answers_in(text: str, open_n: list[int]) -> list[tuple[int, str]]:
    t = str(text or "").strip()
    m = _TIMES.match(t)
    if m:
        k = int(m.group(2))
        return [(n, m.group(1).lower()) for n in open_n[:k]] if k == len(open_n) else []
    marks = list(_MARK.finditer(t))
    if marks and marks[0].start() == 0:
        got = []
        for i, mk in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
            v = t[mk.end():end].strip().rstrip(".,;").strip()
            if v:
                got.append((int(mk.group(1)), v))
        return [(n, v) for n, v in got if n in open_n]
    if len(open_n) == 1 and _BARE.match(t):
        return [(open_n[0], t.rstrip(".!").strip().lower())]
    return []


def _card_eid(sid: str, q: str) -> str:
    return MEMBRANE_NS + "decision:chat:" + hashlib.sha1(f"{sid}|{_norm(q)}".encode()).hexdigest()[:16]


def _open_cards(est, sid: str) -> list:
    return [d for d in list((est.docs or {}).values()) if d.cls == CLASS_DECISION
            and (d.properties or {}).get("chat") == sid
            and (d.properties or {}).get("status") == "open"]


def _card_for(est, sid: str, q: str):
    for d in _open_cards(est, sid):
        if _norm((d.properties or {}).get("name") or "") == _norm(q):
            return d
    eid = _card_eid(sid, q)
    d = (est.docs or {}).get(eid)
    if d is None:
        d = QuantumEntity(eid=eid, cls=CLASS_DECISION, properties={
            "name": q[:160], "summary": q, "status": "open", "var": {"type": "string"},
            "planted_by": "chat", "chat": sid, "asked_from": ASKED_FROM_TEXT})
        est.upsert(d, embedder=None)
    elif (d.properties or {}).get("status") != "open":
        return None                       # answered or dropped before: not asked again
    return d


def _answer(srv, sid: str, u_mid: str, u_eid: str, text: str, prev_a: str | None, be, origin) -> int:
    from . import decision_map as DM
    if not prev_a:
        return 0
    est = srv.entity_store
    asked = {}
    for r in _out(be, origin, prev_a, "asks:"):
        d = (est.docs or {}).get(r["obj"])
        if d is not None and (d.properties or {}).get("status") == "open":
            asked[int(str(r["rel"]).split(":", 1)[1])] = d.eid
    n = 0
    for k, value in answers_in(text, sorted(asked)):
        res = DM.answer(est, getattr(srv, "user_ledger", None), asked[k], value, conv=sid,
                        message=u_mid, tell=False)
        if res.get("ok"):
            _edge(be, origin, u_eid, "answers", asked[k])
            n += 1
    return n


def _ask(srv, runner, sid: str, a_eid: str, reply: str, be, origin) -> None:
    est = srv.entity_store
    items = asked_items(reply)
    listed = []
    if items is not None:
        for _n, q in items:
            d = _card_for(est, sid, q)
            if d is not None:
                listed.append(d)
        keep = {d.eid for d in listed}
        for d in _open_cards(est, sid):
            p = d.properties or {}
            if p.get("asked_from") == ASKED_FROM_TEXT and d.eid not in keep:
                d.properties = {**p, "status": "dropped", "dropped_why": "no longer asked",
                                "dropped_by": "membrane"}
                est.upsert(d, embedder=None)
    unasked = [d for d in _open_cards(est, sid) if not (d.properties or {}).get("asked_in")
               and d.eid not in {x.eid for x in listed}]
    extra = [(len(listed) + i, d) for i, d in enumerate(unasked, 1)]
    numbered = list(enumerate(listed, 1)) + extra
    for k, d in numbered:
        _edge(be, origin, a_eid, f"asks:{k}", d.eid)
        d.properties = {**(d.properties or {}), "asked_in": a_eid}
        est.upsert(d, embedder=None)
    if extra and runner is not None:
        from . import speech as SP
        runner.post_line(sid, SP.say("asks", items=[(k, (d.properties or {}).get("name") or d.eid)
                                                    for k, d in extra]))


# ---- the turn ------------------------------------------------------------------------------

def on_turn_end(srv, runner, sid: str, user_info: dict, user_text: str,
                a_info: dict, reply: str) -> None:
    """The finished chat turn into the graph (both messages, their edges, the answers to the
    reply before and the decisions this reply asks)."""
    est = getattr(srv, "entity_store", None)
    if est is None:
        return
    be, origin = _rel(srv)
    u_mid, a_mid = str((user_info or {}).get("id") or ""), str((a_info or {}).get("id") or "")
    if not u_mid or not a_mid:
        return
    person = from_person(user_text)
    who, user_text = _words(user_text)
    u = _mint(est, sid, u_mid, "user" if person else "membrane", user_text, speaker=who)
    a = _mint(est, sid, a_mid, "assistant", reply)
    prev = _row_before(runner, sid, u_mid)
    prev_a = _row_before(runner, sid, u_mid, "assistant")
    if prev is not None and msg_eid(prev.get("id")) in (est.docs or {}):
        _edge(be, origin, u, "follows", msg_eid(prev.get("id")))
    _edge(be, origin, a, "follows", u)
    for eid, text in ((u, user_text), (a, reply)):
        for t in _mentions(srv, text):
            _edge(be, origin, eid, "refers-to", t)
    for root in (getattr(runner, "_cg_roots", None) or {}).pop(sid, []) if runner is not None else []:
        _edge(be, origin, u, "led-to", root)
    pa = msg_eid(prev_a.get("id")) if prev_a is not None else None
    if person:
        _answer(srv, sid, u_mid, u, user_text, pa if pa in (est.docs or {}) else None, be, origin)
    _ask(srv, runner, sid, a, reply, be, origin)


# ---- the referent: pronouns are registers ------------------------------------------------------
# A person's pronouns are field entities, register:<person>:<pronoun> (the chat is the person),
# each with live -holds-> edges to what it means now. A resolution loads it (the old edge is
# superseded, so its history stays) and the message that loaded it -sets:<pronoun>-> the same
# target. Reading: the reply before first (the freshest mention), the register when that reply
# names nothing that applies. No types: a register holds whatever the conversation last put in it.

REG = MEMBRANE_NS + "register:"
CLASS_REGISTER = "Register"
FORMS = {"it": "it", "this": "this", "this one": "this", "that": "that", "that one": "that",
         "they": "they", "them": "they", "these": "they", "those": "they",
         "he": "he", "him": "he", "she": "she", "her": "she", "there": "there", "then": "then"}
PLURAL = {"they"}
_PRON = re.compile(r"\b(this one|that one|" + "|".join(sorted((f for f in FORMS if " " not in f), key=len,
                                                              reverse=True)) + r")\b", re.I)
_LEAD = re.compile(r"^(?:(?:yes|yep|yeah|ok|okay|sure|pls|please|so|and|then|now|(?:can|could|would|will) you)"
                   r"\b[\s,.!-]*)+", re.I)
_VERB_PRON = re.compile(r"^([a-z][a-z-]*)\s+(this one|that one|it|this|that|them|him|her|these|those|there)\b",
                        re.I)
# "this / that / these / those" are pronouns only when nothing starting a noun or a clause
# follows: "park that until later", "do that" -- not "I think that we", "merge this PR"
_DEMONSTRATIVES = {"this", "that", "these", "those", "this one", "that one"}
_AFTER_PRONOUN = {"until", "for", "to", "in", "on", "at", "by", "with", "and", "or", "but", "when",
                  "because", "so", "if", "now", "too", "again", "please", "pls", "first", "later",
                  "instead", "then", "as", "after", "before", "today", "tomorrow", "up", "down", "out",
                  "off", "over", "away", "back", "already", "yet", "anyway", "done", "is", "was"}
_NEXT_WORD = re.compile(r"\s*([A-Za-z#][\w#-]*)")
REASK_MAX = 3            # the operator's cap (2026-11-01): more readings than this are not asked back
_ORDINAL = {"first": 1, "1": 1, "one": 1, "second": 2, "2": 2, "two": 2, "third": 3, "3": 3}


def register_eid(sid: str, pronoun: str) -> str:
    return f"{REG}{sid}:{FORMS.get(pronoun.lower(), pronoun.lower())}"


def _held(be, origin, reg: str) -> list[str]:
    return [r["obj"] for r in _out(be, origin, reg, "holds")]


def register(srv, sid: str, pronoun: str):
    """What the person's pronoun means now: one eid, a tuple for several, or None."""
    be, origin = _rel(srv)
    if be is None:
        return None
    got = sorted(_held(be, origin, register_eid(sid, pronoun)))
    return None if not got else got[0] if len(got) == 1 else tuple(got)


def _load(srv, sid: str, pronoun: str, targets: list[str], by: str = "") -> None:
    from .night import _supersede_edge
    est = srv.entity_store
    be, origin = _rel(srv)
    if be is None or not targets:
        return
    reg = register_eid(sid, pronoun)
    if reg not in (est.docs or {}):
        est.upsert(QuantumEntity(eid=reg, cls=CLASS_REGISTER, properties={
            "name": f"{FORMS.get(pronoun.lower(), pronoun)} ({sid})", "person": sid,
            "pronoun": FORMS.get(pronoun.lower(), pronoun), "silent_birth": True}), embedder=None)
    for old in _held(be, origin, reg):
        if old not in targets:
            _supersede_edge(be, origin, reg, old, rel="holds", receipt={"op": "reloaded", "by": by})
    for t in targets:
        _edge(be, origin, reg, "holds", t, source="register")
        if by:
            _edge(be, origin, by, f"sets:{FORMS.get(pronoun.lower(), pronoun)}", t, source="register")
    d = est.docs[reg]                    # mirrored onto the entity: a reader of properties sees it
    d.properties = {**(d.properties or {}), "holds": list(targets),
                    "holds_names": [_label(est, t) for t in targets], "set_by": by, "set_ts": time.time()}
    est.upsert(d, embedder=None)


_WORD_RUN = re.compile(r"[A-Za-z][A-Za-z'-]*")
_FILLERS = {"pls", "plz", "kindly", "just", "go", "ahead", "let", "lets", "let's"}


def _says_something(before: str) -> bool:
    """Content words before the pronoun, past the command's own verb (the word right before
    it): "on field ui redesign -- ... park" does, "can you benchmark" / "yes, merge" don't
    (Memo's replay of #806: plain words were skipped and the reply before was borrowed)."""
    from .recall_banner import _STOP
    words = [w.lower() for w in _WORD_RUN.findall(before or "")]
    words = [w for w in words if w not in _STOP and w not in _FILLERS and w not in FORMS and len(w) > 1]
    return len(words) > 1                 # the last content word is the verb the pronoun is the object of


def take_note(srv, sid: str) -> str | None:
    """The resolved reading for this chat's turn (once), for the turn's context."""
    return (getattr(srv, "_cg_notes", None) or {}).pop(sid, None) if sid else None


def _label(est, eid: str) -> str:
    m = _GH.match(eid)
    if m:
        return f"#{m.group(2)}"
    d = (est.docs or {}).get(eid)
    if d is not None:
        return str((d.properties or {}).get("name") or eid)
    return eid.split(":", 1)[1] if ":" in eid else eid


def _applicable(est, cands: list[str], verb: str | None) -> list[str]:
    from . import verbs as V
    docs = est.docs or {}
    if verb:
        with_method = [c for c in cands if c in docs and V.implementation(est, docs[c], verb)[0] is not None]
        if with_method:
            cands = with_method
    return [c for c in cands if str(((docs.get(c).properties or {}) if c in docs else {})
                                       .get("status") or "").lower() not in _CLOSED]


def _resolved(srv, sid, est, be, origin, u, word, targets, source="resolver") -> dict:
    for t in targets:
        if u:
            _edge(be, origin, u, "refers-to", t, source=source)
    _load(srv, sid, word, targets, by=u)
    shown = " and ".join(f"{_label(est, t)} ({t})" for t in targets)
    srv.__dict__.setdefault("_cg_notes", {})[sid] = \
        f'In the person\'s last message, "{word}" means {shown}.'
    return {"resolved": targets[0] if len(targets) == 1 else tuple(targets), "word": word,
            "label": " and ".join(_label(est, t) for t in targets)}


def capture_binding(srv, sid: str):
    """For the seat's capture-time binding (seat.Turn): None when the graph didn't look at this
    turn (another client: the lexical binder stays); {} when it looked and resolved nothing (no
    rewrite: a missed binding is the safe failure); else {"pronoun", "referent"}."""
    seen = (getattr(srv, "_cg_turn", None) or {}) if sid else {}
    if sid not in seen:
        return None
    r = seen[sid] or {}
    return {"pronoun": r["word"], "referent": r["label"]} if r.get("resolved") and r.get("label") else {}


def _answer_reask(srv, sid, est, be, origin, u, text, prev_u) -> dict | None:
    """The person's reply to '"it": a or b?': the reading it names loads the register."""
    d = (est.docs or {}).get(msg_eid(prev_u.get("id"))) if prev_u is not None else None
    pend = ((d.properties or {}).get("reask") if d is not None else None) or None
    if not pend:
        return None
    t = " " + _norm(text) + " "
    readings = list(pend.get("readings") or [])
    hits = [c for c in readings if f" {_norm(_label(est, c))} " in t
            or f" {_norm(_label(est, c)).rsplit('/', 1)[-1]} " in t or f" {_norm(c)} " in t]
    if not hits:
        for w, k in _ORDINAL.items():
            if f" {w} " in t and k <= len(readings):
                hits.append(readings[k - 1])
    hits = list(dict.fromkeys(hits))
    if len(hits) != 1:
        return None
    d.properties = {**(d.properties or {}), "reask": None, "reask_answered": hits[0]}
    est.upsert(d, embedder=None)
    return _resolved(srv, sid, est, be, origin, u, pend.get("word") or "it", hits, source="reask")


def before_turn(srv, runner, sid: str, user_info: dict, text: str) -> dict | None:
    """The person's pronouns, before the turn: an answer to a re-ask loads its register; a
    pronoun reads the reply before, then its register. One reading: recorded, loaded, told to
    the model. Several, in a command ("delete it", "yes, merge it and restart"): asked back
    and the turn doesn't run. Several elsewhere ("is it done?"): left to the model.
    -> None | {"resolved": ...} | {"reask": line}."""
    (getattr(srv, "_cg_notes", None) or {}).pop(sid, None)    # a note lives for its own message only
    if not from_person(text):
        return None
    who, text = _words(text)
    est = getattr(srv, "entity_store", None)
    be, origin = _rel(srv)
    mid = str((user_info or {}).get("id") or "")
    if est is None or be is None or not mid:
        return None
    u = msg_eid(mid)
    prev_u = _row_before(runner, sid, mid, "user")
    got = _answer_reask(srv, sid, est, be, origin, u, text, prev_u)
    if got is not None:
        return got
    found = None
    for m in _PRON.finditer(text):
        w = m.group(1).lower()
        if w in _DEMONSTRATIVES:
            nxt = _NEXT_WORD.match(text, m.end())
            if nxt and nxt.group(1).lower() not in _AFTER_PRONOUN:
                continue                  # a determiner or a conjunction, not a pronoun
        found = m
        break
    if not found:
        return None
    word = found.group(1).lower()
    cmd = _VERB_PRON.match(_LEAD.sub("", text.strip()))
    verb = cmd.group(1).lower() if cmd and cmd.group(1).lower() not in _NOT_ACTS else None
    if verb:
        word = cmd.group(2).lower()
    plural = FORMS.get(word) in PLURAL
    # the person's own message first: the nearest thing it names before the pronoun
    own = _applicable(est, list(reversed(_mentions(srv, text[:found.start()]))), verb)
    if own:
        return _resolved(srv, sid, est, be, origin, u, word, own if plural else own[:1])
    if _says_something(text[:found.start()]):
        from .night import _supersede_edge        # a reading known to be wrong must not stay loaded
        reg = register_eid(sid, word)
        for old in _held(be, origin, reg):
            _supersede_edge(be, origin, reg, old, rel="holds", receipt={"op": "unloaded", "by": u})
        d = (est.docs or {}).get(reg)
        if d is not None:
            d.properties = {**(d.properties or {}), "holds": [], "holds_names": []}
            est.upsert(d, embedder=None)
        return None          # its own words name what it means (unnamed in the field): borrow nothing
    prev_a = _row_before(runner, sid, mid, "assistant")
    fresh = list(dict.fromkeys(r["obj"] for r in _out(be, origin, msg_eid(prev_a.get("id")), "refers-to"))) \
        if prev_a is not None else []
    cands = _applicable(est, fresh, verb) or _applicable(est, _held(be, origin, register_eid(sid, word)), verb)
    if not cands:
        return None
    if len(cands) == 1 or plural:
        return _resolved(srv, sid, est, be, origin, u, word, cands)
    if len(cands) > REASK_MAX:           # a reply naming many things: "it" is most likely its proposal
        srv.__dict__.setdefault("_cg_notes", {})[sid] = (
            f'In the person\'s last message, "{word}" most likely refers to what your last reply '
            "proposed, not to one of the things it named.")
        return {"hint": "proposal", "word": word}
    if not verb:
        return None
    from . import speech as SP
    line = SP.say("reask", word=word, options=[_label(est, c) for c in cands])
    _mint(est, sid, mid, "user", text, speaker=who)
    d = est.docs[u]
    d.properties = {**(d.properties or {}), "reask": {"word": word, "readings": cands}}
    est.upsert(d, embedder=None)
    if prev_a is not None:
        _edge(be, origin, u, "follows", msg_eid(prev_a.get("id")))
    runner.post_line(sid, line)
    return {"reask": line, "readings": cands}
