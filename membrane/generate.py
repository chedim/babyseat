"""GENERATION: membrane words what it did and what it couldn't, itself (the operator, 2026-11-01:
"can the same mechanism be used in reverse to generate responses?"; lemminflect approved).

The reverse of living language: the calls a sentence compiled to, and their results, become a
sentence -- the verb in its past form (lemminflect; plain rules without it), the objects'
names joined as English, the count when there are several. What didn't map is worded the same
way, so a refusal reads as an answer. No model writes these.
"""
from __future__ import annotations

import re

_VOWEL = "aeiou"


def _rule_past(verb: str) -> str:
    """A regular past form by the plain rules (lemminflect missing)."""
    v = verb.lower()
    if v.endswith("e"):
        return v + "d"
    if v.endswith("y") and len(v) > 1 and v[-2] not in _VOWEL:
        return v[:-1] + "ied"
    if (len(v) >= 3 and v[-1] not in _VOWEL + "wxy" and v[-2] in _VOWEL and v[-3] not in _VOWEL
            and len(re.findall(f"[{_VOWEL}]+", v)) == 1):
        return v + v[-1] + "ed"                  # one syllable, consonant-vowel-consonant: stopped
    return v + "ed"


def past(verb: str) -> str:
    """The verb's past form; a phrasal verb inflects its first word ("turned on")."""
    words = str(verb or "").split()
    if not words:
        return ""
    head = words[0]
    try:
        from lemminflect import getInflection
        got = getInflection(head, tag="VBD")
        head = got[0] if got else _rule_past(head)
    except Exception:
        head = _rule_past(head)
    return " ".join([head] + words[1:])


def join(names: list) -> str:
    names = [str(n) for n in names if str(n)]
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _first_line(s) -> str:
    return next((ln.strip() for ln in str(s or "").splitlines() if ln.strip()), "")[:160]


def done(verb: str, names: list, failed: list) -> str:
    """What a step did: "Closed goal one.", "Closed 2 things: a and b.", "Couldn't close b: why."
    `failed`: [(name, why)]."""
    out = []
    ok = [n for n in names if n not in {f for f, _ in failed}]
    if ok:
        out.append(_cap(f"{past(verb)} {ok[0]}.") if len(ok) == 1
                   else _cap(f"{past(verb)} {len(ok)} things: {join(ok)}."))
    for name, why in failed:
        out.append(f"Couldn't {verb} {name}" + (f": {_first_line(why)}" if _first_line(why) else "") + ".")
    return " ".join(out)


def not_understood(verb: str, unmapped: list) -> str:
    """Why a step didn't map, in words."""
    out = []
    for u in unmapped:
        u = str(u)
        if u == verb:
            out.append(f"I don't know how to {verb}.")
        elif u.startswith(verb + " needs "):
            out.append(f"To {verb} that I need {join(u[len(verb) + 7:].split(', '))}.")
        elif u.startswith(verb + " "):
            out.append(f"I can't {verb} {u[len(verb) + 1:]}: nothing gives it that meaning.")
        elif u == "(no object)":
            out.append(f"{_cap(verb)} what?")
        else:
            out.append(f"I don't know what \"{u}\" is.")
    return " ".join(out)


def needs_all(verb: str, names: list) -> str:
    return f"That would {verb} {len(names)} things ({join(names)}). Say 'all' to do it."
