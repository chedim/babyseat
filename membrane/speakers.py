"""WHO SAID IT (2026-10-09, the operator: "we should unify the way
information flows to and thru membrane").

Every discourse event names its speaker as an ENTITY, resolved in ONE
place -- where events become field mentions -- so every intake lane gets
it (the web chat, phone calls, the model's replies, the loops, imports):

  person:<slug>   a person: the '[HA user: <name>]' line the HA proxy and
                  calld put first on every message they forward
  person:<sub>    a person who presented a token (JWT mode, jwtauth.py): the
                  '[user#<sub-slug>: <name>]' line the seat stamps itself --
                  the person is keyed by the token's `sub`, `name` labels it
  person:operator a user message with no speaker line (the TUI, a script)
  seat:self       the model's own words (role model / reasoning) -- the
                  seat's voice, never its chosen name: the name is the
                  seat's to remember, not the engine's to inject

The speaker rides as the event's first mention (like a script lane's
named subject), so its state is where that speaker's words land in the
field, and it co-occurs with everything they talk about. The speaker line
itself is not prose: it is cut from what gets embedded (the stored text
keeps it), so a name stops being a heavy word node (w:dmitrii, 2317
cells of header).
"""
from __future__ import annotations

import re

SPEAKER_PREFIX = "[HA user: "
STANDALONE_PREFIX = "[user: "               # standalone mode's page (ui_serve.py): no HA in the line
# capture collapses whitespace: the line may run straight into the message
_LINE = re.compile(r"^\s*\[(?:HA )?user: ([^\]\n]{1,80})\][ \t]*\n?")
# JWT mode's line (jwtauth.stamp_text): the token's sub rides beside the name,
# so the person entity is keyed by the sub; the forms above never match it
ID_PREFIX = "[user#"
_ID_LINE = re.compile(r"^\s*\[user#([a-z0-9][a-z0-9-]{0,79}): ([^\]\n]{1,80})\][ \t]*\n?")
SEAT = ("seat:self", "seat")
VOICE = ("seat:voice", "voice")
VOICE_PREFIX = "[membrane voice] "         # old auto-chat voice rows (the babble role, gone in #318)
OPERATOR = ("person:operator", "operator")


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "unknown"


def _clean_name(name: str) -> str:
    return " ".join(str(name or "").replace("]", ")").replace("[", "(").split())[:80]


def id_line(sub: str, name: str | None = None) -> str:
    """JWT mode's speaker line: '[user#<slug(sub)>: <name or sub>]'."""
    return f"{ID_PREFIX}{slug(sub)[:80]}: {_clean_name(name or sub) or slug(sub)}]"


def split_speaker_id(text: str) -> tuple[str | None, str | None, str]:
    """(the id, the name, the rest) of a JWT-mode line; (None, None, text) otherwise."""
    m = _ID_LINE.match(text or "")
    if not m:
        return None, None, text or ""
    return m.group(1), " ".join(m.group(2).split()), (text or "")[m.end():]


def strip_speaker_lines(text: str) -> str:
    """The text without any leading speaker line(s), of any form (a client's
    own lines, stacked or not, before the seat stamps the token's)."""
    t = text or ""
    while True:
        m = _LINE.match(t) or _ID_LINE.match(t)
        if not m:
            return t
        t = t[m.end():]


def split_speaker(text: str) -> tuple[str | None, str]:
    """(the speaker's name or None, the text without the speaker line)."""
    m = _LINE.match(text or "")
    if not m:
        _id, name, rest = split_speaker_id(text)
        return (name, rest) if _id else (None, text or "")
    return " ".join(m.group(1).split()), (text or "")[m.end():]


def speaker_of(ev: dict) -> tuple[str, str] | None:
    """(eid, surface) of who said a discourse event, or None (tools, code,
    notes and digests speak for no one)."""
    if ev.get("stream") != "discourse":
        return None
    role = ev.get("role") or ""
    if role in ("model", "reasoning"):
        return SEAT
    if role == "user":
        if (ev.get("text") or "").startswith(VOICE_PREFIX):
            return VOICE
        pid, pname, _ = split_speaker_id(ev.get("text") or "")
        if pid:
            return f"person:{pid}", pname or pid
        name, _ = split_speaker(ev.get("text") or "")
        return (f"person:{slug(name)}", name) if name else OPERATOR
    return None


def prose_of(ev: dict) -> str:
    """The text a discourse event contributes to the field's geometry."""
    t = ev.get("text") or ""
    if ev.get("stream") == "discourse" and (ev.get("role") or "") == "user":
        if t.startswith(VOICE_PREFIX):
            return ""          # the voice's babble never enters the field's memory
        return split_speaker(t)[1]
    return t
