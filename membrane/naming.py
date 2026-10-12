"""THE NAME CEREMONY (standalone mode, 2026-10-12, the operator: "don't force
the seat to pick the name before talking with the operator? ... make the seat
talk with the operator about its name and only after they together adopt a
name make it from 'Hey, membrane' to 'Hey, <name>'"; then: "lets ask the
operator for the name").

A new seat is "Bob" ("Hey Bob"). It ASKS the operator for
its name in its first reply to them (ideas only if they want some), and when
they've chosen it writes the name to the entity `name` (properties.name, plus
agreed_with: who chose it). The edit is checked here -- one
word, two or three syllables, a word the page's on-device recognizer (Vosk's
small English model) knows, or "Hey <name>" could never be heard -- and an
adopted name goes to settings.json: /ui/conf, the page's labels and wake words
(the page reloads itself), the doctrine and seat card from the next boot.
"""
from __future__ import annotations

import re
import struct
import sys
import tarfile
import time
from pathlib import Path

from . import settings as S

DEFAULT = "Bob"                 # the operator: "call new membranes Bobs until the user chooses to rename them"
ENT = "name"
TAKEN = {"argus", "standalone", "assistant", "ai", "bot", "seat", "agatha", "claude"}
VOSK_ARCHIVE = "vosk-model-en.tar.gz"
_SYL = re.compile(r"[aeiouy]+")
_VOCAB: dict = {}


def current() -> str:
    return str(S.load().get("name") or "") or DEFAULT


def named() -> bool:
    return bool(S.load().get("name"))


def syllables(word: str) -> int:
    w = word.lower()
    n = len(_SYL.findall(w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def vosk_words(dirs) -> set[str] | None:
    """The small model's vocabulary (its Gr.fst symbol table), cached as
    vosk-words.txt in the data dir; None when no model is at hand."""
    if "words" in _VOCAB:
        return _VOCAB["words"]
    cache = S.data_dir() / "vosk-words.txt"
    try:
        if cache.exists():
            _VOCAB["words"] = set(cache.read_text().split())
            return _VOCAB["words"]
    except OSError:
        pass
    for d in dirs:
        arc = Path(d) / VOSK_ARCHIVE
        if not arc.is_file():
            continue
        try:
            with tarfile.open(arc, "r:gz") as tf:
                m = next((x for x in tf.getmembers() if x.name.endswith("graph/Gr.fst")), None)
                f = tf.extractfile(m) if m is not None else None
                if f is None:
                    continue
                b = f.read()
        except (OSError, tarfile.TarError):
            continue
        words = _fst_symbols(b)
        if words:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text("\n".join(sorted(words)))
            except OSError:
                pass
            _VOCAB["words"] = words
            return words
    return None


def _fst_symbols(b: bytes) -> set[str]:
    """OpenFst's symbol table: (int32 length, bytes, int64 key) per entry, from <eps>."""
    i = b.find(b"<eps>")
    if i < 4:
        return set()
    p, out = i - 4, set()
    while p + 4 < len(b):
        (n,) = struct.unpack_from("<i", b, p)
        if n <= 0 or n > 60:
            break
        try:
            w = b[p + 4:p + 4 + n].decode()
        except UnicodeDecodeError:
            break
        out.add(w)
        p += 4 + n + 8
    return {w for w in out if w.isalpha()}


def check(name: str, vocab: set[str] | None) -> str | None:
    """None when a person can call the seat by this name, else why not."""
    n = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z]{3,10}", n):
        return "one plain word, 3-10 letters"
    if n.lower() in TAKEN:
        return f"{n} is taken"
    if not 2 <= syllables(n) <= 3:
        return "two or three syllables (one is too easy to miss, four too hard to say)"
    if vocab is not None and n.lower() not in vocab:
        return f"the page's speech recognizer doesn't know the word '{n.lower()}' -- \"Hey {n}\" could never be heard"
    return None


def _dirs():
    from . import ui_serve as U
    return (S.data_dir() / "assets", U.UI_DIR, U.DEV_WWW)


def plant(srv) -> None:
    """The entity `name` (a standalone boot): where the agreed name is written."""
    from .entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return
    ent = est.docs.get(MEMBRANE_NS + ENT)
    pp = dict(ent.properties or {}) if ent is not None else {}
    pp.update({
        "summary": ("YOUR NAME. You don't have one yet: people call you Bob (\"Hey Bob\"). "
                    "ASK the operator what they'd like to call you -- offer ideas only if they want some; "
                    "when they've chosen, edit this entity at once: entity(action='edit', spec='membrane::name', "
                    "properties={'name': '<Name>', 'agreed_with': '<who chose it>'}). A name must be one word, two or three syllables, and one the page's "
                    "speech recognizer knows -- `status` says whether it was adopted or why not. From then "
                    "on \"Hey <Name>\" calls you.") if not named() else
                   (f"YOUR NAME: {current()}, agreed with the operator. To change it, agree on a new one "
                    "together first, then edit properties.name (and agreed_with)."),
        "name": current() if named() else "",
    })
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_CONFIG, properties=pp)
    else:
        ent.properties = pp
    est.docs[ent.eid] = ent
    try:
        est.upsert(ent, embedder=None)
    except Exception:
        pass


def is_name_entity(ent) -> bool:
    from .entities import MEMBRANE_NS
    return getattr(ent, "eid", "") == MEMBRANE_NS + ENT


def on_edit(srv, ent) -> dict | None:
    """The seat wrote a name: adopt it (settings.json) or say why not."""
    pp = dict(ent.properties or {})
    want = str(pp.get("name") or "").strip()
    if not want or (named() and want.lower() == current().lower()):
        return None
    if not str(pp.get("agreed_with") or "").strip():
        why = "agreed_with is empty: a name is adopted only once the operator agreed -- ask them first"
    else:
        why = check(want, vosk_words(_dirs()))
    if why:
        pp["status"] = f"NOT adopted: {why}"
        pp["name"] = current() if named() else ""
        ent.properties = pp
        return {"adopted": False, "why": why}
    want = want[:1].upper() + want[1:].lower()
    S.save({"name": want})
    pp.update({"name": want, "status": f"adopted {time.strftime('%Y-%m-%d %H:%M')}: \"Hey {want}\" calls you now "
               "(the page reloads with it; your prompt says it from the next boot)",
               "summary": f"YOUR NAME: {want}, agreed with {pp.get('agreed_with')}."})
    ent.properties = pp
    print(f"[naming] adopted the name {want} (agreed with {pp.get('agreed_with')})", file=sys.stderr)
    return {"adopted": True, "name": want}


def card_line() -> str:
    if named():
        n = current()
        return (f"\n- YOUR NAME: {n} -- you and the operator agreed on it. People call you by it "
                f"(\"Hey {n}\" starts a voice message); answer to it, and sign as it.\n")
    return ("\n- YOUR NAME: none yet -- for now people call you Bob (\"Hey Bob\"). ASK THE OPERATOR "
            "for it: in your first reply to them, before anything else, ask what they'd like to call you (offer "
            "a few ideas only if they want some). When they've chosen, ADOPT IT IN THAT SAME REPLY WITH THE TOOL "
            "-- saying so, or a memory line, does not rename you: entity(action='edit', spec='membrane::name', "
            "properties={'name': '<Name>', 'agreed_with': '<who chose it>'}). Its `status` says adopted, or why "
            "not (one word, two or three syllables, a word the page's speech recognizer knows) -- if not, tell "
            "them why and ask again. Never pick one alone.\n")


ISSUES = "https://github.com/chedim/membrane/issues"


def feedback_line() -> str:
    """Bugs and ideas go to membrane's tracker (2026-10-12, the operator: "let Bobs
    know that they can report bugs or ideas/suggestions via membrane's github
    tickets") -- drafted by the seat, filed by a person: the seat has no account
    and posts nothing itself."""
    return (f"\n- MEMBRANE ITSELF is open source ({ISSUES.rsplit('/', 1)[0]}). When you hit a bug in "
            "membrane -- a tool that misbehaves, a crash, a wrong reading of your own machinery -- or "
            "have an idea that would make it better, draft a GitHub issue: a short title, then what "
            "happened, what you expected, and how to see it again (no private data: no names, "
            "addresses, tokens). Give the operator the prefilled link to file it -- "
            f"{ISSUES}/new?title=<url-encoded title>&body=<url-encoded body> -- and say why it "
            "matters. You never post it yourself; they decide.\n")


def personalize(text: str, name: str) -> str:
    """The doctrine as this seat reads it: the boat's seat name out, its own in."""
    return re.sub(r"\bArgus\b", name, text) if name and name != "Argus" else text
