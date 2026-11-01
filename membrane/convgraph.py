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
_NUMBERED = re.compile(r"(?:^|(?<=\s))(\d+)[.)]?\s+(.+?)(?=\s+\d+[.)]\s|\s*$)", re.S)
_TIMES = re.compile(r"^\s*(yes|no|ok)\s*x\s*(\d+)\s*$", re.I)
_BARE = re.compile(r"^\s*(yes|y|yep|yeah|no|nope|n|ok|okay|sure|go|go ahead|do it)\s*[.!]*\s*$", re.I)
_PRONOUN = re.compile(r"^\s*(?:pls\s+|please\s+)?([a-z][a-z-]*)\s+(it|that|this|them|that one|this one)"
                      r"(?:\s+(?:pls|please|now))?\s*[.!]?\s*$", re.I)
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

def _mint(est, sid: str, mid: str, role: str, text: str, ts=None) -> str:
    eid = msg_eid(mid)
    if eid not in (est.docs or {}):
        first = next((ln for ln in str(text or "").splitlines() if ln.strip()), "")
        est.upsert(QuantumEntity(eid=eid, cls=CLASS_MESSAGE, properties={
            "name": " ".join(first.split())[:80] or f"{role} message", "role": role, "sid": sid,
            "mid": mid, "text": str(text or "")[:TEXT_MAX], "ts": ts or time.time(),
            "silent_birth": True}), embedder=None)
    return eid


def _row_before(runner, sid: str, mid: str, role: str | None = None):
    rows = runner.messages.list(sid) if runner is not None else []
    idx = next((i for i, r in enumerate(rows) if (r.get("info") or {}).get("id") == mid), None)
    for r in reversed(rows[:idx] if idx is not None else rows):
        info = r.get("info") or {}
        if role is None or info.get("role") == role:
            return info
    return None


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
    for e in out:
        if e.startswith("m:"):            # the field's mirror of a store entity: the store's eid
            d = (est.docs or {}).get(MEMBRANE_NS + e[2:])
            if d is not None:
                out[out.index(e)] = d.eid
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
    got = [(int(n), v.strip().rstrip(".").strip()) for n, v in _NUMBERED.findall(t)]
    got = [(n, v) for n, v in got if n in open_n and v]
    if got and _NUMBERED.match(t):
        return got
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
    u = _mint(est, sid, u_mid, "user", user_text)
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
    _answer(srv, sid, u_mid, u, user_text, pa if pa in (est.docs or {}) else None, be, origin)
    _ask(srv, runner, sid, a, reply, be, origin)


# ---- the referent ------------------------------------------------------------------------------

def _label(est, eid: str) -> str:
    d = (est.docs or {}).get(eid)
    if d is not None:
        return str((d.properties or {}).get("name") or eid)
    return eid.split(":", 1)[1] if ":" in eid else eid


def before_turn(srv, runner, sid: str, user_info: dict, text: str) -> dict | None:
    """"delete it": resolve the pronoun against the reply before. None: nothing to resolve
    (or nothing it could mean); {"resolved": eid}; {"reask": line} after asking back."""
    m = _PRONOUN.match(str(text or ""))
    if not m or m.group(1).lower() in _NOT_ACTS:
        return None
    verb, word = m.group(1).lower(), m.group(2).lower()
    est = getattr(srv, "entity_store", None)
    be, origin = _rel(srv)
    mid = str((user_info or {}).get("id") or "")
    prev_a = _row_before(runner, sid, mid, "assistant")
    if est is None or be is None or prev_a is None:
        return None
    pa = msg_eid(prev_a.get("id"))
    cands = list(dict.fromkeys(r["obj"] for r in _out(be, origin, pa, "refers-to")))
    if not cands:
        return None
    from . import night as N
    docs = est.docs or {}
    with_method = [c for c in cands if c in docs and N.resolve_method(est, docs[c], verb)[0] is not None]
    if with_method:
        cands = with_method
    cands = [c for c in cands if str(((docs.get(c).properties or {}) if c in docs else {})
                                         .get("status") or "").lower() not in _CLOSED]
    if not cands:
        return None
    u = msg_eid(mid) if mid else ""
    if len(cands) == 1:
        if u:
            _edge(be, origin, u, "refers-to", cands[0], source="resolver")
        return {"resolved": cands[0]}
    from . import speech as SP
    line = SP.say("reask", word=word, options=[_label(est, c) for c in cands])
    if mid:
        _mint(est, sid, mid, "user", text)
        _edge(be, origin, u, "follows", pa)
    runner.post_line(sid, line)
    return {"reask": line, "readings": cands}
