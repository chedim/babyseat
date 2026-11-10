"""THE SCRIPTS UPGRADE (#1044, the operator 2026-11-10): a seat starting on data from before the stdlib moves its entity
scripts (`*_script`, Python run by night.run_entity_script) into `.c1f`, once, at boot -- as the store moves to SQLite.

Per entity carrying a script:
- Blurp's toys (a `toybox` property) are skipped while Blurp is off (0.7.5: they are rewritten later).
- A finished one-off is retired: every absolute path it names is under /tmp (or /private/tmp) and gone. Its script is
  cleared; the text stays on the entity (`script_port.code`).
- A known template (RULES) becomes a kind: the entity keeps its class, gains `cls:<kind>` among its ISA parents (that
  kind's pack getters apply to it, membrane/packs/ported.c1f), and its script is cleared -- only after a REPLAY check:
  the Python script's output equals what the kind computes from the entity's fields.
- Anything else stays Python, marked `script_port.pending = "model"` (the model step translates it, gated the same way).
`script_port` = {to | retired | pending, code, ts}: what happened, and the original text. Running again changes nothing.
"""
from __future__ import annotations

import os
import re
import time
import types

SCRIPT_KINDS = ("getter_script", "tick_script", "watch_script", "nudge_script", "mention_script", "thought_script",
                "dreamed_script", "delete_script")
_TMP_PATH = re.compile(r"""['"]((?:/private)?/tmp/[^'"\s]+)['"]""")
_ANY_PATH = re.compile(r"""['"](/[^'"\s]+)['"]""")


def _norm(code: str) -> str:
    return " ".join(str(code or "").split())


# ---- the rules: a template -> (kind, the replay: python output vs what the kind computes) ----------------------

def _heard_state(props: dict) -> dict:
    """ChatThing (membrane/packs/ported.c1f): `heard_n getter: this.heard_n or 0`."""
    v = props.get("heard_n")
    return {"heard_n": v or 0}


RULES = [
    (_norm("result = {'state': {'heard_n': int(membrane.me.properties.get('heard_n') or 0)}}"), "getter_script",
     "ChatThing", _heard_state),
]


def _python_result(code: str, props: dict) -> dict:
    from .night import run_entity_script
    me = types.SimpleNamespace(properties=dict(props))
    api = types.SimpleNamespace(me=me)
    return run_entity_script(code, {"properties": dict(props)}, membrane=api) or {}  # type: ignore[arg-type]


def _replay(code: str, props: dict, kind_state) -> bool:
    """The Python script's `state` equals the kind's fields on the same entity."""
    try:
        r = _python_result(code, props)
    except Exception:                                        # noqa: BLE001
        return False
    if not isinstance(r, dict) or "error" in r:
        return False
    want = r.get("state") or {}
    got = kind_state(props)
    return set(want) <= set(got) and all(got[k] == want[k] for k in want)


def _retirable(code: str) -> bool:
    """Every absolute path it names is a /tmp one that is gone (the run it watched is over)."""
    paths = _ANY_PATH.findall(code or "")
    tmp = _TMP_PATH.findall(code or "")
    return bool(tmp) and len(tmp) == len(paths) and not any(os.path.exists(p) for p in tmp)


def upgrade(est, blurp: bool | None = None, now: float | None = None) -> dict:
    """Move this seat's entity scripts (see the module doc). -> {ported, retired, pending, skipped} (entity ids)."""
    from .entities import MEMBRANE_NS
    if blurp is None:
        from .self_super import blurp_on
        blurp = blurp_on()
    now = time.time() if now is None else now
    rep: dict = {"ported": [], "retired": [], "pending": [], "skipped": []}
    for eid, d in list((est.docs or {}).items()):
        p = dict(d.properties or {})
        scripts = {k: p[k] for k in SCRIPT_KINDS if isinstance(p.get(k), str) and p[k].strip()}
        if not scripts:
            continue
        if p.get("toybox") and not blurp:
            rep["skipped"].append(eid)
            continue
        done = p.get("script_port") or {}
        if done.get("to") or done.get("retired"):
            continue
        changed: dict = {}
        port: dict = {"ts": now, "code": scripts if len(scripts) > 1 else next(iter(scripts.values()))}
        if all(_retirable(c) for c in scripts.values()):
            changed = {k: "" for k in scripts}
            port["retired"] = "every file it watches is gone (/tmp): the run it followed is over"
            rep["retired"].append(eid)
        elif len(scripts) == 1:
            (kind_prop, code), = scripts.items()
            rule = next((r for r in RULES if r[0] == _norm(code) and r[1] == kind_prop), None)
            if rule is not None and _replay(code, p, rule[3]):
                parents = list(p.get("parents") or [])
                kid = f"{MEMBRANE_NS}cls:{rule[2].lower()}"
                if kid not in parents:
                    parents.append(kid)
                changed = {kind_prop: "", "parents": parents}
                port["to"] = rule[2]
                rep["ported"].append(eid)
        if not changed:
            if done.get("pending") == "model":
                continue
            port["pending"] = "model"
            rep["pending"].append(eid)
        d.properties = {**p, **changed, "script_port": port}
        if any(k in changed for k in SCRIPT_KINDS):
            for k in SCRIPT_KINDS:
                if changed.get(k) == "":
                    d.properties.pop(k, None)
        est.upsert(d, embedder=None)
    return rep
