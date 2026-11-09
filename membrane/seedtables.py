"""#980 rows 11,16,17: phrase/anchor/imprint tables as data entities.

Junk-anchor texts, the semantic-intercept seeds + anti-seeds and the router's
imprint interview prompt ship as a seed pack (pack "seedtables"); readers take
them from the entity store and fall back to the built-in tuples when the pack
is absent. Embedding compute stays native -- only the text tables are data.
"""
from __future__ import annotations

from .entities import JUNK_ANCHOR_SEEDS, JUNK_EID_PREFIX, MEMBRANE_NS

PACK_NAME = "seedtables"
PREFIX = MEMBRANE_NS + "seedtable:"
IMPRINT_PROMPT = ("In exactly 3 short bullet lines: (1) the tasks you are best at, "
                  "(2) the tasks you are worst at, (3) what you were trained for. "
                  "Plain text, no preamble.")


def _seeds():
    from .lanes import _INTERCEPT_ANTI_SEEDS, _INTERCEPT_SEEDS
    return list(_INTERCEPT_SEEDS), list(_INTERCEPT_ANTI_SEEDS)


def build_pack(version: int = 1) -> dict:
    ents = []
    for i, s in enumerate(JUNK_ANCHOR_SEEDS):
        ents.append({"eid": f"{JUNK_EID_PREFIX}seed-{i}", "cls": "Component",
                     "properties": {"name": f"junk-anchor-seed-{i}", "summary": s, "junk_anchor": True}})
    seeds, anti = _seeds()
    for kind, rows in (("intercept", seeds), ("anti", anti)):
        for i, s in enumerate(rows):
            ents.append({"eid": f"{PREFIX}{kind}:{i}", "cls": "Other",
                         "properties": {"name": f"{kind}-seed-{i}", "summary": s,
                                        "seed_table": kind, "idx": i}})
    ents.append({"eid": PREFIX + "imprint:prompt", "cls": "Other",
                 "properties": {"name": "router-imprint-prompt", "summary": IMPRINT_PROMPT,
                                "seed_table": "imprint", "idx": 0}})
    return {"name": PACK_NAME, "version": version, "entities": ents}


def _rows(est, kind: str) -> list:
    docs = getattr(est, "docs", None) or {}
    rows = [(int((d.properties or {}).get("idx", 0)), str((d.properties or {}).get("summary") or ""))
            for d in docs.values() if (d.properties or {}).get("seed_table") == kind]
    return [s for _, s in sorted(rows) if s]


def intercept_seeds(est=None) -> list:
    return _rows(est, "intercept") or _seeds()[0]


def anti_seeds(est=None) -> list:
    return _rows(est, "anti") or _seeds()[1]


def imprint_prompt(est=None) -> str:
    return (_rows(est, "imprint") or [IMPRINT_PROMPT])[0]


def junk_anchor_texts(est=None) -> list:
    docs = getattr(est, "docs", None) or {}
    got = [str((d.properties or {}).get("summary") or "") for d in docs.values()
           if d.eid.startswith(JUNK_EID_PREFIX) or (d.properties or {}).get("junk_anchor")]
    return [s for s in got if s] or list(JUNK_ANCHOR_SEEDS)
