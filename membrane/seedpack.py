"""#980 Q3: the versioned seed pack.

A pack is packaged source: {name, version, entities:[{eid, cls, properties}]}. load() runs at boot:
same version -> no-op; newer -> the release wins. An entity the seat edited (its properties differ
from what the pack last installed) keeps that edit as an overlay in the ledger entity
`membrane::seedpack:<name>` (status pending -> reapply() or discard()). Scripts only ride a
release pack (release=True); a peer import goes through strip_scripts() and never carries them.
"""
from __future__ import annotations

import copy

from .entities import MEMBRANE_NS, QuantumEntity

SCRIPT_KEYS = ("tick_script", "getter_script", "watch_script", "thought_script", "dreamed_script",
               "mention_script", "nudge_script", "delete_script")


class PackRefused(Exception):
    pass


def _is_script(k: str) -> bool:
    return k in SCRIPT_KEYS or k.endswith("_script")


def _led_eid(name: str) -> str:
    return MEMBRANE_NS + "seedpack:" + name


def strip_scripts(props: dict) -> dict:
    return {k: v for k, v in (props or {}).items() if not _is_script(k)}


def _ledger(est, name: str) -> dict:
    d = (est.docs or {}).get(_led_eid(name))
    if d is None:
        est.upsert(QuantumEntity(eid=_led_eid(name), cls="Other", properties={
            "name": "seed pack " + name, "version": 0, "base": {}, "overlays": [],
            "silent_birth": True}), embedder=None)
        d = est.docs[_led_eid(name)]
    return d.properties


def installed_version(est, name: str) -> int:
    d = (est.docs or {}).get(_led_eid(name))
    try:
        return int((d.properties or {}).get("version", 0)) if d else 0
    except (TypeError, ValueError):
        return 0  # a corrupt ledger version must not skip the pack


def pack_hash(pack: dict) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(pack.get("entities", []), sort_keys=True, default=str).encode()).hexdigest()[:16]


def overlays(est, name: str) -> list:
    d = (est.docs or {}).get(_led_eid(name))
    return list((d.properties or {}).get("overlays", [])) if d else []


def load(est, pack: dict, release: bool = False) -> dict:
    """Idempotent, per entity, every boot (content, not the version number, decides):
    missing -> (re)installed and printed; first adoption -> base recorded, the seat's edits untouched;
    later -> only keys where new != base are written; a seat edit on such a key is parked as an overlay."""
    name = pack["name"]
    try:
        ver = int(pack["version"])
    except (TypeError, ValueError):
        ver = 0
    ents = pack.get("entities", [])
    if not release:
        for e in ents:
            if any(_is_script(k) for k in (e.get("properties") or {})):
                raise PackRefused(f"pack {name}: {e.get('eid')} carries a script; only a release pack may")
    out = {"installed": 0, "updated": 0, "overlaid": 0, "adopted": 0, "reinstalled": []}
    led = _ledger(est, name)
    base, ovs = dict(led.get("base") or {}), list(led.get("overlays") or [])
    for e in ents:
        eid, new = e["eid"], copy.deepcopy(e.get("properties") or {})
        d = (est.docs or {}).get(eid)
        if d is None:
            est.upsert(QuantumEntity(eid=eid, cls=e.get("cls", "Other"), properties=dict(new)), embedder=None)
            out["installed"] += 1
            if eid in base:
                out["reinstalled"].append(eid)
        elif eid not in base:
            out["adopted"] += 1  # first adoption: record base, never overwrite
        else:
            cur = copy.deepcopy(d.properties)
            old = base[eid]
            edited = {}
            for k, v in new.items():
                if k in old and old[k] == v:
                    continue
                if k in cur and cur[k] != v and cur[k] != old.get(k):
                    edited[k] = copy.deepcopy(cur[k])
                if cur.get(k) != v:
                    cur[k] = copy.deepcopy(v)
            if cur != d.properties:
                d.properties.clear()
                d.properties.update(cur)  # upsert merges (never overwrites), so set in place, then journal
                est.upsert(QuantumEntity(eid=eid, cls=d.cls, properties=dict(cur)), embedder=None)
                out["updated"] += 1
            if edited:
                ovs = [o for o in ovs if not (o["eid"] == eid and o["status"] == "pending")]
                ovs.append({"eid": eid, "version": ver, "status": "pending", "properties": edited})
                out["overlaid"] += 1
        base[eid] = copy.deepcopy(new)
    led["base"], led["overlays"], led["version"], led["hash"] = base, ovs, ver, pack_hash(pack)
    if out["reinstalled"]:
        print(f"[seedpack] {name}: re-installed {', '.join(out['reinstalled'])}")
    return out


def _find(est, name, eid):
    for o in _ledger(est, name)["overlays"]:
        if o["eid"] == eid and o["status"] == "pending":
            return o
    return None


def reapply(est, name: str, eid: str) -> dict:
    o = _find(est, name, eid)
    if o is None or eid not in (est.docs or {}):
        return {"ok": False, "why": "no pending overlay"}
    est.docs[eid].properties.update(strip_scripts(copy.deepcopy(o["properties"])))
    o["status"] = "applied"
    return {"ok": True}


def discard(est, name: str, eid: str) -> dict:
    o = _find(est, name, eid)
    if o is None:
        return {"ok": False, "why": "no pending overlay"}
    o["status"] = "discarded"
    return {"ok": True}
