"""Pronoun binding: capture-time referent resolution for the field.

A CHAT TURN THE CONVERSATION GRAPH LOOKED AT does not use this lexicon (2026-11-01: it
rewrote "can you benchmark it?" as "... benchmark explain?"): seat.Turn binds by the graph's
reading (convgraph.capture_binding, the pronoun registers), and rewrites nothing when the
graph found nothing. This module still binds every other client's turn.

THE FAILURE (field sim 0.9, the kettle/lamp case): "turn it off" for the
kettle and "turn it off" for the lamp embed identically -- a pronoun is a
deictic pointer whose referent lives OUTSIDE the sentence, so the vector
cannot tell the two situations apart. The field then dedups/merges them or
replays the wrong device's answer with high confidence.

THE FIX is capture-time binding (the sibling rule), deterministic and $0:
when the query's pronoun resolves against the recent turns' text, REWRITE
the situation with the referent substituted BEFORE the embed+store (the vec
matches the stored text -- the same lesson as the capture-time strip) and
stamp the binding on the cell (``extra["bind"]``, original in
``extra["sit_raw"]``). The gate is symmetric: the incoming query is bound
the same way before its embed, and a recalled cell whose binding CONFLICTS
with the turn's binding is forced to escalate. The failure asymmetry is the
design: a wrong binding must never replay a device action; a missed binding
just escalates (safe).

WHY NOT THE ENTITY GRAPH as the binder: the graph holds project concepts
whose ``session:now`` co-occurrence would happily bind "it" to an ambient
entity the user never named. A lexicon over the user's own recent words
binds only to what was actually said (audit-first; extend the pronoun set
on evidence, the demonstratives are deliberately excluded -- "I think
that..." is a conjunction, not a pointer).
"""
from __future__ import annotations

import re

__all__ = ["resolve", "substitute", "conflict", "stamp", "strip_reminders",
           "has_pronoun", "PRONOUNS"]

# The object-deictic core. Demonstratives (this/that/these/those) are
# excluded on purpose: they double as conjunctions/subjects ("I think
# that...") and would misfire; extend on audit evidence only.
PRONOUNS = frozenset({
    "it", "them", "him", "her", "itself", "himself", "herself",
})

_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>",
                          re.DOTALL)

_WORD_RE = re.compile(r"[a-z0-9']+")

# Function words + common chat filler. A referent candidate must NOT be in
# here (2-char device names like "tv"/"pc" survive via the length floor).
_STOPWORDS = frozenset("""
a an the and or but if then else when while of to in on at by for with from
as is are was were be been being am do does did doing done have has had
having will would can could should shall may might must not no nor so too
very just now here there it its they them their he she his her him we us
our you your i me my mine myself yourself ourselves themselves what which
who whom whose where why how all any both each few more most other some
such only own same than that this these those s t d ll m o re ve y ain
aren couldn didn doesn hadn hasn haven isn ma mightn mustn needn shan
shouldn wasn weren won wouldn don off out up down over under again still
also please ok okay yeah yes sure thanks thank hey hi okied okie back once
about into onto per via thing stuff something anything nothing everything
""".split())

# Prepositions/particles: a trailing PP tail is NOT the referent ("add a
# kettle TO home assistant" -> kettle; "turn off the lamp AFTER dinner" ->
# lamp). The segment before the LAST one carries the direct object.
_PREPS = frozenset("""
to into onto on in at by for with from after before over under about around
near past until till during against between among through towards toward of
""".split())

# Bare verbs (a small imperative lexicon -- no POS tagger at $0): a turn
# whose ONLY content is a verb has its object spoken as the pronoun itself
# ("turn it on" -> seg [turn, it]) and yields NO referent -- the walk falls
# through to the older turn that named the thing.
_VERBS = frozenset("""
add remove delete create make build run stop start restart kill turn switch
set get put show list tell check fix try use call do go keep drop write read
update change move open close play pause test deploy install enable disable
print watch wait send take give find look see say speak talk work works
launch quit exit toggle reload save load sync fetch scan clean clear reset
""".split())


def strip_reminders(text: str | None) -> str:
    """Drop opencode's <system-reminder> spans (machinery, never discourse)."""
    return _REMINDER_RE.sub(" ", text or "")


def has_pronoun(query: str) -> bool:
    """True when the query carries an object-deictic pronoun."""
    return any(t in PRONOUNS for t in _WORD_RE.findall(
        strip_reminders(query).lower()))


def _referent_of(text: str) -> str | None:
    """The likely direct object of one turn's text (deterministic, $0).

    Segment at the LAST preposition: "add a kettle to home assistant" ->
    "add a kettle" -> the last content word before the tail is "kettle".
    Skips pronouns AND bare verbs (a verb-only segment like "turn" in "turn
    it on" has no object -- the pronoun IS the object). Returns None when no
    noun candidate survives ("run it" / "okay yes").
    """
    toks = _WORD_RE.findall(strip_reminders(text).lower())
    if not toks:
        return None
    # last preposition's index: everything before it is the core clause
    cut = max((i for i, t in enumerate(toks) if t in _PREPS), default=-1)
    seg = toks[:cut] if cut > 0 else toks          # leading preposition = no cut
    for t in reversed(seg):
        if t in _STOPWORDS or t in PRONOUNS:
            continue
        if t in _VERBS:
            continue
        if len(t) < 2:
            continue
        return t
    return None


def resolve(query: str, prev_texts: list[str] | None) -> dict | None:
    """Resolve the query's FIRST pronoun against the recent turns.

    ``prev_texts`` is most-recent-first (the runtime's deque, newest last --
    the caller reverses). Walk newest -> oldest and take the first turn that
    yields a referent: the immediate antecedent wins, older turns only fill
    in when the nearer turn has no candidate (verb-only imperative, bare
    ack). The referent is the segment-picked content word of that turn
    (``_referent_of``). Returns ``{"pronoun", "referent"}`` or None (no
    pronoun / no candidate -- a missed binding escalates, safe).
    """
    if not prev_texts:
        return None
    q_toks = _WORD_RE.findall(strip_reminders(query).lower())
    p = next((t for t in q_toks if t in PRONOUNS), None)
    if p is None:
        return None
    for prev in prev_texts:                      # newest first
        ref = _referent_of(prev)
        if ref is not None and ref != p:
            return {"pronoun": p, "referent": ref}
    return None


def substitute(query: str, binding: dict) -> str:
    """Rewrite the query's first pronoun occurrence with the referent.

    "turn it off" + {"pronoun": "it", "referent": "lamp"} -> "turn lamp off".
    Reminder spans are dropped first (the bound text becomes the stored
    situation and the embed input, so vec matches text). Case-preserving on
    the pronoun ("Turn it off" -> "Turn lamp off").
    """
    p, ref = binding.get("pronoun", ""), binding.get("referent", "")
    if not p or not ref:
        return strip_reminders(query)
    clean = strip_reminders(query)
    pat = re.compile(rf"\b{re.escape(p)}\b", re.IGNORECASE)
    m = pat.search(clean)
    if not m:
        return clean
    out = ref if m.group(0).islower() else ref.capitalize()
    return pat.sub(out, clean, count=1)


def conflict(query: str, binding: dict | None,
             cell_binding) -> bool:
    """The gate rule: must NOT replay a stored cell's action for this turn?

    A cell with a binding may only serve a turn whose binding AGREES:
      - cell unbound            -> never conflicts (geometry decides);
      - query has no pronoun    -> never conflicts (explicit query);
      - query pronoun, unresolved (binding None) -> conflict (a replay
        would be a guess -- missed binding escalates, safe);
      - resolved but a DIFFERENT referent than the cell's -> conflict
        (the kettle/lamp case: same text, different device).
    """
    if not isinstance(cell_binding, dict) or not cell_binding:
        return False
    if not has_pronoun(query):
        return False
    if not binding:
        return True
    return (str(binding.get("referent", "")).lower()
            != str(cell_binding.get("referent", "")).lower())


def stamp(extra: dict, verdict: dict) -> None:
    """Ride the capture's binding onto the field row (learn time).

    ``extra["bind"]`` is the binding the situation was written under;
    ``extra["sit_raw"]`` preserves the user's original words (the bound
    situation is a derived text -- archaeology needs the source). No-op when
    the turn carried no binding (legacy shape, byte-compatible).
    """
    b = verdict.get("binding")
    if not b:
        return
    extra["bind"] = dict(b)
    q_raw = verdict.get("query_raw")
    if q_raw:
        extra["sit_raw"] = q_raw