"""WHO SAID IT (2026-10-09, the operator: "we should unify the way
information flows to and thru membrane").

Every discourse event names its speaker as an ENTITY, resolved in ONE
place -- where events become field mentions -- so every intake lane gets
it (the web chat, phone calls, the model's replies, the loops, imports):

  person:<slug>   a person: the '[HA user: <name>]' line the HA proxy and
                  calld put first on every message they forward
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
# capture collapses whitespace: the line may run straight into the message
_LINE = re.compile(r"^\s*\[HA user: ([^\]\n]{1,80})\][ \t]*\n?")
SEAT = ("seat:self", "seat")
VOICE = ("seat:voice", "voice")
VOICE_PREFIX = "[membrane voice] "         # voice.py's auto-chat turns
OPERATOR = ("person:operator", "operator")


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "unknown"


def split_speaker(text: str) -> tuple[str | None, str]:
    """(the speaker's name or None, the text without the speaker line)."""
    m = _LINE.match(text or "")
    if not m:
        return None, text or ""
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
