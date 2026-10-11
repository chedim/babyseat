"""THE ACTIVE MODELS (2026-10-10, the operator: "'active model' nodes for each
type of model usage that, linked with a model, switch between models in
ollama ... react to my controls in the chat ... the idea is to let membrane
choose those models and change them as they want/instructed ... maybe even
per-user or per-seat").

One entity per role, in each seat's own store (so per seat by construction):

  active-model:chat            the seat's chat turns (the person-facing lanes)
  active-model:chat:<person>   one person's chats (overrides the line above)
  active-model:loops           the watch loops' turns
  active-model:cortex          the idle cortex: thinker, dreamer, the judges

LINKING one to a model switches that role: its ISA parent is the model's
entity (models:<name>, an AIModel), or properties.model names it. Membrane
does it with its own hands (entity inherit / edit); the operator's controls
(the chat's model picker, the minds' panel) write the same entities. Only a
model the seat's backends actually serve is taken -- anything else is
refused, and the entity says why.
"""
from __future__ import annotations

import re
import time

ROLES = ("chat", "loops", "cortex")
PREFIX = "active-model:"
_SUMMARY = {
    "chat": "the model the seat's chat turns run on",
    "loops": "the model the watch loops' turns run on",
    "cortex": "the model the idle cortex runs on: thinker, dreamer, the claim and A-B judges",
}
_HOW = ("Switch it: link a model -- entity inherit spec='{name}' parents=['models:<name>'] "
        "(or edit properties.model). Only a model the backends serve is taken.")


def slug(name: str) -> str:
    from .speakers import slug as _slug
    return _slug(name)


def ent_name(role: str, person: str | None = None) -> str:
    return PREFIX + role + (":" + slug(person) if person else "")


def _est(srv):
    return getattr(srv, "entity_store", None)


def model_of(est, ent) -> str:
    """The model an active-model entity is linked to: properties.model, else
    its first ISA parent that is a model (the parent's own name)."""
    if ent is None:
        return ""
    pp = ent.properties or {}
    m = str(pp.get("model") or "").strip()
    if m:
        return m
    for pid in pp.get("parents") or []:
        p = est.docs.get(pid) if est is not None else None
        if p is None:
            continue
        if str(getattr(p, "cls", "")) == "AIModel" or ":models:" in pid or pid.startswith("models:"):
            return str((p.properties or {}).get("name") or pid.rsplit("models:", 1)[-1]).strip()
    return ""


def ensure(srv) -> list[str]:
    """Plant the role entities (idempotent; the seat's edits survive)."""
    est = _est(srv)
    if est is None:
        return []
    made = []
    from .entities import CLASS_CONFIG
    for role in ROLES:
        name = ent_name(role)
        if est._resolve_spec(name) is not None:
            continue
        r = est.add(cls=CLASS_CONFIG, name=name,
                    summary=f"ACTIVE MODEL ({role}): {_SUMMARY[role]}. " + _HOW.format(name=name))
        if isinstance(r, dict) and r.get("eid"):
            d = est.docs.get(r["eid"])
            if d is not None:
                d.properties["active_model_role"] = role
                cur = current_runtime(srv, role)
                if cur:
                    d.properties["model"] = cur
                made.append(r["eid"])
    return made


def current_runtime(srv, role: str) -> str:
    if role == "loops":
        return str(getattr(getattr(srv, "_loop_runner", None), "model", "") or "")
    if role == "cortex":
        return str(getattr(srv, "idle_cortex_model", "") or "")
    return str((getattr(srv, "_active_models", None) or {}).get("chat") or "")


def known_models(srv) -> list[str] | None:
    try:
        from .lanes import _chat_capable_models
        return list(_chat_capable_models(getattr(srv, "backend", "")) or []) or None
    except Exception:
        return None


def on_change(srv, ent, known=None) -> dict | None:
    """An active-model entity was edited or linked: switch its role (or say
    why not, on the entity). Returns what happened, None when not ours."""
    est = _est(srv)
    if est is None or ent is None:
        return None
    pp = ent.properties or {}
    name = str(pp.get("name") or "")
    role = pp.get("active_model_role") or (name[len(PREFIX):].split(":")[0] if name.startswith(PREFIX) else None)
    if role not in ROLES:
        return None
    person = name[len(PREFIX) + len(role) + 1:] if name.startswith(PREFIX + role + ":") else ""
    model = model_of(est, ent)
    if not model:
        return {"role": role, "model": "", "applied": False, "why": "not linked to a model"}
    known = known if known is not None else known_models(srv)
    if known is not None and model not in known:
        pp["model_refused"] = f"{model!r} is not served here -- choose one of: {', '.join(known[:20])}"
        ent.properties = pp
        return {"role": role, "model": model, "applied": False, "why": pp["model_refused"]}
    pp.pop("model_refused", None)
    pp["model"] = model
    pp["active_model_role"] = role
    pp["switched_ts"] = time.time()
    ent.properties = pp
    if role in ("loops", "cortex"):
        from . import minds_models as MM
        MM.apply(srv, **{role: model}, mirror=False)
    else:
        am = srv.__dict__.setdefault("_active_models", {})
        am["chat:" + person if person else "chat"] = model
    return {"role": role, "person": person or None, "model": model, "applied": True}


def mirror(srv, role: str, model: str, person: str | None = None) -> None:
    """The operator's controls changed a role: the entity follows."""
    est = _est(srv)
    if est is None or not model:
        return
    name = ent_name(role, person)
    ent = est._resolve_spec(name)
    if ent is None:
        from .entities import CLASS_CONFIG
        r = est.add(cls=CLASS_CONFIG, name=name,
                    summary=(f"ACTIVE MODEL ({role}, {person}'s): " if person else f"ACTIVE MODEL ({role}): ")
                    + _SUMMARY[role] + ". " + _HOW.format(name=name))
        ent = est.docs.get(r.get("eid", "")) if isinstance(r, dict) else None
        if ent is None:
            return
    pp = ent.properties or {}
    if pp.get("model") == model:
        return
    pp.update({"model": model, "active_model_role": role, "switched_ts": time.time()})
    ent.properties = pp
    if role == "chat":
        srv.__dict__.setdefault("_active_models", {})["chat:" + slug(person) if person else "chat"] = model
    try:
        est.upsert(ent, embedder=None)
    except Exception:
        pass


def for_turn(srv, text: str, lane: str) -> str | None:
    """The model a serve turn runs on, by its role: a person's chat -> their
    own entity, else the seat's chat entity; a loop -> the loops entity.
    None = no active-model says (the old rules stand)."""
    am = getattr(srv, "_active_models", None) or {}
    if lane == "listener":
        return current_runtime(srv, "loops") or None
    from .speakers import split_speaker
    who = split_speaker(str(text or ""))[0]
    if who and am.get("chat:" + slug(who)):
        return am["chat:" + slug(who)]
    return am.get("chat") or None


def load(srv) -> None:
    """Boot: the entities are the truth -- each linked role is reapplied."""
    est = _est(srv)
    if est is None:
        return
    ensure(srv)
    known = known_models(srv)
    for d in list((est.docs or {}).values()):
        if str((d.properties or {}).get("name") or "").startswith(PREFIX):
            try:
                on_change(srv, d, known=known)
            except Exception:
                pass


_ROLE_RE = re.compile(r"^" + re.escape(PREFIX) + r"(" + "|".join(ROLES) + r")(?::.+)?$")


def is_active_model(ent) -> bool:
    return bool(_ROLE_RE.match(str((getattr(ent, "properties", None) or {}).get("name") or "")))
