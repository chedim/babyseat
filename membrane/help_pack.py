"""#1043 (the operator 2026-11-10): the long-form docs as field entities. Each `membrane/help/<topic>.md` is the entity
`membrane::help:<topic>` (class Help, its `text`), shipped as the `help` seed pack (seedpack.py: a release updates a
topic, a seat's own edit is kept as an overlay). help(topic) reads the entity; the always-sent doctrine keeps a short
core and points here, so a topic costs tokens only when it is read."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .entities import MEMBRANE_NS

DIR = Path(__file__).resolve().parent / "help"
CLASS = "Help"


def eid(topic: str) -> str:
    return f"{MEMBRANE_NS}help:{topic}"


def topics() -> list[str]:
    return sorted(p.stem for p in DIR.glob("*.md")) if DIR.is_dir() else []


def packaged(topic: str) -> str | None:
    p = DIR / f"{topic}.md"
    return p.read_text().strip() if p.is_file() else None


def pack() -> dict:
    ents = [{"eid": eid(t), "cls": CLASS,
             "properties": {"name": f"help: {t}", "topic": t, "text": packaged(t), "silent_birth": True}}
            for t in topics()]
    ver = int(hashlib.sha256("".join(e["properties"]["text"] for e in ents).encode()).hexdigest()[:8], 16)
    return {"name": "help", "version": ver, "entities": ents}


def install(est) -> dict:
    """At boot (and in tests): every packaged topic is an entity on this seat."""
    from . import seedpack
    return seedpack.load(est, pack(), release=True)


def read(topic: str, est=None) -> str | None:
    """The topic's text: this seat's entity (edits included), else the packaged file."""
    d = (getattr(est, "docs", None) or {}).get(eid(topic)) if est is not None else None
    txt = (d.properties or {}).get("text") if d is not None else None
    return str(txt) if txt else packaged(topic)


def listing(est=None) -> list[str]:
    seen = set(topics())
    for k, d in list((getattr(est, "docs", None) or {}).items()) if est is not None else []:
        if d.cls == CLASS and k.startswith(f"{MEMBRANE_NS}help:"):
            seen.add(k.split("help:", 1)[1])
    return sorted(seen)
