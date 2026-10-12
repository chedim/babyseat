"""THE NAME CEREMONY (standalone mode, 2026-10-12, the operator: "it should pick
a diff name :) ... 'Hey, <AI name that it picks itself>'").

A standalone seat with no name in settings.json names ITSELF at its first boot:
one model call proposes a few names; the first one a person can call it by
wins -- one word, two or three syllables, and a word the page's on-device
recognizer (Vosk's small English model) knows, or "Hey <name>" can never be
heard. The name goes to settings.json, the doctrine and the seat card (the
model's own prompt), /ui/conf (the page's labels and wake words).
"""
from __future__ import annotations

import json
import re
import struct
import sys
import tarfile
import urllib.request
from pathlib import Path

from . import settings as S

TAKEN = {"argus", "membrane", "standalone", "assistant", "ai", "bot", "seat", "agatha", "claude"}
FALLBACK = ("Juniper", "Atlas", "Willow", "Orion", "Ember", "Marlow", "Nova", "Sable")
VOSK_ARCHIVE = "vosk-model-en.tar.gz"
_SYL = re.compile(r"[aeiouy]+")

PROMPT = (
    "You are a new AI seat booting for the first time on someone's computer. Choose your own name: "
    "the people here will call you by it out loud (\"Hey <name>\"), so it must be ONE ordinary English "
    "word or common first name, two or three syllables, easy to say and to hear, distinct from everyday "
    "words people say all the time. Not Argus, not Membrane, not Claude. "
    "Reply with JSON only: {\"names\": [five candidates, your favourite first], \"why\": \"one sentence\"}")


def syllables(word: str) -> int:
    w = word.lower()
    n = len(_SYL.findall(w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def vosk_words(dirs) -> set[str] | None:
    """The small model's vocabulary (its Gr.fst symbol table), cached as
    vosk-words.txt in the data dir; None when no model is at hand."""
    cache = S.data_dir() / "vosk-words.txt"
    try:
        if cache.exists():
            return set(cache.read_text().split())
    except OSError:
        pass
    for d in dirs:
        arc = Path(d) / VOSK_ARCHIVE
        if not arc.is_file():
            continue
        try:
            with tarfile.open(arc, "r:gz") as tf:
                m = next((x for x in tf.getmembers() if x.name.endswith("graph/Gr.fst")), None)
                if m is None:
                    continue
                b = tf.extractfile(m).read()
        except (OSError, tarfile.TarError):
            continue
        words = _fst_symbols(b)
        if words:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text("\n".join(sorted(words)))
            except OSError:
                pass
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


def acceptable(name: str, vocab: set[str] | None) -> bool:
    n = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z]{3,10}", n) or n.lower() in TAKEN:
        return False
    if not 2 <= syllables(n) <= 3:
        return False
    return vocab is None or n.lower() in vocab


def propose(backend: str, model: str, timeout: float = 60.0) -> list[str]:
    """One model call (Ollama's /api/chat, or an OpenAI-compatible /v1) -> candidates."""
    body = {"model": model, "stream": False, "format": "json",
            "messages": [{"role": "user", "content": PROMPT}]}
    text = ""
    try:
        req = urllib.request.Request(backend.rstrip("/") + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = (json.loads(r.read()).get("message") or {}).get("content") or ""
    except Exception:
        try:
            body2 = {"model": model, "messages": body["messages"]}
            req = urllib.request.Request(backend.rstrip("/") + "/v1/chat/completions", data=json.dumps(body2).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = json.loads(r.read())["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[naming] the model did not answer ({e!r})"[:200], file=sys.stderr)
            return []
    m = re.search(r"\{.*\}", text, re.S)
    try:
        names = json.loads(m.group(0)).get("names") if m else None
    except ValueError:
        names = None
    if not isinstance(names, list):
        names = re.findall(r"\b[A-Z][a-z]{2,9}\b", text)
    return [str(x).strip() for x in names if str(x).strip()]


def ensure(backend: str, model: str, dirs) -> str:
    """The seat's name: settings.json's, or one it picks now (and keeps)."""
    st = S.load()
    if st.get("name"):
        return str(st["name"])
    vocab = vosk_words(dirs)
    pick = next((n for n in propose(backend, model) if acceptable(n, vocab)), None)
    how = "chose"
    if pick is None:
        pick = next((n for n in FALLBACK if acceptable(n, vocab)), FALLBACK[0])
        how = "fell back to"
    pick = pick[:1].upper() + pick[1:].lower()
    S.save({"name": pick})
    print(f"[naming] the seat {how} its name: {pick}", file=sys.stderr)
    return pick


def personalize(text: str, name: str) -> str:
    """The doctrine as this seat reads it: the boat's seat name out, its own in."""
    return re.sub(r"\bArgus\b", name, text) if name and name != "Argus" else text
