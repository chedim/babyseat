"""HOME ASSISTANT'S STATES, LIVE IN THE FIELD (2026-10-12, the operator: "can it
just... integrate?"): when Home Assistant is available to the seat (the membrane
integration inside it runs the seat and sets HASS_URL / HASS_TOKEN in the child's
environment; a DETACHED seat gets its token pushed over its own serve instead --
POST /membrane/ha/token writes ~/.config/membrane/hass.env, 2026-10-12), the seat
acts through HA's REST API, and HA pushes the state changes
of the entities the seat chose to mirror:

  - the seat marks a field entity with properties.ha_entity = "<HA entity_id>"
  - GET  /membrane/ha/subscriptions  -> the entity_ids so marked (HA polls it)
  - POST /membrane/ha/states         <- [{entity_id, state, attributes, last_changed}]
    each marked entity's ha_state / ha_attributes / ha_changed are set, so its
    watches fire on the change -- no polling, no getter needed for the reading

No Home Assistant code here: the seat only knows the routes and the property.
Which HA entities to mirror, and what to make of them, is the seat's own choice.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

PROP = "ha_entity"
MAX_ATTRS = 4000          # bytes of attributes kept per entity (a media player's can be huge)
ENV_FILE = "~/.config/membrane/hass.env"    # where a pushed token lives (toolset.py's convention)
_env_cache: dict = {"mtime": None, "val": None}


def _env_path() -> Path:
    return Path(os.path.expanduser(os.environ.get("MEMBRANE_HASS_ENV", ENV_FILE)))


def save_token(url: str, token: str) -> dict:
    """POST /membrane/ha/token (Home Assistant's integration, detached mode):
    the pair into the env file, 0600, atomic; commands source it
    (`. ~/.config/membrane/hass.env; curl ...`)."""
    url, token = str(url or "").strip().rstrip("/"), str(token or "").strip()
    if not url.startswith("http") or not token:
        return {"ok": False, "error": "give url and token"}
    p = _env_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        body = (f"export HASS_URL='{url}'\n"
                f"export HASS_TOKEN='{token}'\n")
        tmp = p.with_suffix(".tmp")
        tmp.write_text(body)
        os.replace(tmp, p)
        os.chmod(p, 0o600)
    except OSError as e:
        return {"ok": False, "error": f"could not write the env file ({e.__class__.__name__})"}
    _env_cache["mtime"], _env_cache["val"] = None, None          # read fresh next turn
    return {"ok": True, "path": str(p)}


def load_token() -> dict | None:
    """HASS_URL / HASS_TOKEN from the env file (pushed by HA), cached on mtime.
    None when there is none or it does not parse."""
    try:
        p = _env_path()
        m = p.stat().st_mtime if p.is_file() else None
    except OSError:
        return None
    if m is None:
        return None
    if _env_cache["mtime"] == m and _env_cache["val"] is not None:
        return _env_cache["val"]
    out: dict = {}
    try:
        with p.open() as f:
            for line in f:
                line = line.strip()
                if line.startswith("export ") and "=" in line:
                    k, _, v = line[len("export "):].partition("=")
                    out[k.strip()] = v.strip().strip("'\"")
    except (OSError, ValueError):
        return None
    if not (out.get("HASS_URL") and out.get("HASS_TOKEN")):
        return None
    _env_cache["mtime"], _env_cache["val"] = m, out
    return out


def hass_available() -> bool:
    """Env vars (an HA child) or a pushed env file (detached) -- HA is reachable."""
    return bool(os.environ.get("HASS_URL") and os.environ.get("HASS_TOKEN")) or load_token() is not None


def _marked(srv) -> dict[str, list]:
    est = getattr(srv, "entity_store", None)
    out: dict[str, list] = {}
    for ent in list((getattr(est, "docs", None) or {}).values()):
        hid = str((ent.properties or {}).get(PROP) or "").strip()
        if hid:
            out.setdefault(hid, []).append(ent)
    return out


def subscriptions(srv) -> list[str]:
    return sorted(_marked(srv))


def apply(srv, rows) -> dict:
    """Set the pushed states on the marked entities -> {updated, unknown}."""
    import json
    est = getattr(srv, "entity_store", None)
    if est is None or not isinstance(rows, list):
        return {"updated": 0, "unknown": 0}
    marked = _marked(srv)
    n = unknown = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        ents = marked.get(str(row.get("entity_id") or ""))
        if not ents:
            unknown += 1
            continue
        attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        if len(json.dumps(attrs, default=str)) > MAX_ATTRS:
            attrs = {k: v for k, v in attrs.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) < 200}
        for ent in ents:
            pp = dict(ent.properties or {})
            if pp.get("ha_state") == row.get("state") and pp.get("ha_attributes") == attrs:
                continue
            pp["ha_state"] = row.get("state")
            pp["ha_attributes"] = attrs
            pp["ha_changed"] = str(row.get("last_changed") or "") or time.strftime("%Y-%m-%dT%H:%M:%S")
            ent.properties = pp
            try:
                est.upsert(ent, embedder=None)
            except Exception:
                continue
            n += 1
    return {"updated": n, "unknown": unknown}


def card_line() -> str:
    """The seat card's line when Home Assistant is available (an HA child: the
    pair is already in the process environment; detached: pushed into
    hass.env, which commands source first)."""
    has_env = bool(os.environ.get("HASS_URL") and os.environ.get("HASS_TOKEN"))
    how = ("both in your commands' environment" if has_env else
           "in your commands; if they are not set, source them first: . ~/.config/membrane/hass.env;")
    return ("\n- HOME ASSISTANT: Home Assistant is around you and you may use it -- its REST API is at "
            "$HASS_URL, authorized by $HASS_TOKEN (" + how + ": "
            "curl -s -H \"Authorization: Bearer $HASS_TOKEN\" $HASS_URL/api/states/<entity_id>; "
            "services: POST $HASS_URL/api/services/<domain>/<service>). Build your own readers "
            "and actuators on it. To keep an entity of yours live without polling, give it "
            "properties.ha_entity = '<entity_id>': Home Assistant then sets its ha_state and "
            "ha_attributes on every change (its watches fire). Once Home Assistant has pushed its catalog, its "
            "entities and services are in the field: class ha:<domain> per domain, entity ha:<entity_id> per "
            "entity (live), each service a method on its class -- <service> or <service>_with "
            "data='<json>' (e.g. turn_on_with data='{\"brightness_pct\": 40}'); entity-less "
            "services sit on the class (ha:notify.notify_with). "
            "When HA runs MQTT, give an entity properties.mqtt_topic = '<topic filter>' and HA "
            "sets its mqtt_payload / mqtt_last_topic on every message; publish with "
            "ha:mqtt.publish_with. What you do in the house is real: "
            "act only when asked, or when a goal of theirs plainly calls for it.\n")


# ---------------------------------------------------------------------------
# HOME ASSISTANT, MAPPED ONTO THE GRAPH (2026-10-30, the operator: "automatic
# homeassistant entity, services and methods mapping onto membrane graph").
# Home Assistant pushes its catalog -- POST /membrane/ha/catalog
#   {entities: [{entity_id, name}], services: {domain: {svc: {target, fields}}}, mqtt}
# -- and the seat builds, with the machinery it already has:
#   - the root class `ha`, holding the two generic actuators every HA class
#     inherits: service(service, data) on an entity, domain_service(service, data)
#     on a class (entity-less services such as mqtt.publish)
#   - one class per domain, ha:<domain> (ha_domain set, parent `ha`)
#   - one entity per HA entity, ha:<entity_id> (ha_entity set, so the state push
#     above keeps it live; parent its domain class; its own parents are kept)
#   - each service as a composite method on its domain class: <svc> (no data)
#     and <svc>_with(data) -- one step calling the generic actuator, so there is
#     ONE curl template on the seat, not one per service (#741)
# The same catalog again is a no-op (its digest sits on `ha`).
ROOT = "ha"
SERVICE_CMD = ("[ -f ~/.config/membrane/hass.env ] && . ~/.config/membrane/hass.env; "
               "python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d[\"entity_id\"]=sys.argv[2]; "
               "print(json.dumps(d))' {{data}} {{self.ha_entity}} | "
               "curl -sf -X POST -H \"Authorization: Bearer $HASS_TOKEN\" -H 'Content-Type: application/json' "
               "-d @- $HASS_URL/api/services/{{self.ha_domain}}/{{service}}")
DOMAIN_CMD = ("[ -f ~/.config/membrane/hass.env ] && . ~/.config/membrane/hass.env; "
              "printf '%s' {{data}} | "
              "curl -sf -X POST -H \"Authorization: Bearer $HASS_TOKEN\" -H 'Content-Type: application/json' "
              "-d @- $HASS_URL/api/services/{{self.ha_domain}}/{{service}}")
_GENERIC = ("service", "domain_service")


def entity_eid(ha_entity_id: str) -> str:
    from .entities import CLASS_COMPONENT, entity_id
    return entity_id(CLASS_COMPONENT, f"{ROOT}:{ha_entity_id}")


def _ensure(est, name: str, props: dict, summary: str = "", parent: str | None = None):
    """One entity by name: created silently, its props set, `parent` ADDED to
    its parents (the ones it already has stay). Writes only on a change."""
    from .entities import CLASS_COMPONENT, QuantumEntity, entity_id
    eid = entity_id(CLASS_COMPONENT, name)
    doc = est.docs.get(eid)
    created = doc is None
    if created:
        doc = QuantumEntity(eid=eid, cls=CLASS_COMPONENT, properties={"name": name, "silent_birth": True})
        est.docs[eid] = doc
    p = dict(doc.properties or {})
    before = dict(p)
    p.update(props)
    if summary:
        p["summary"] = summary
    if parent and parent not in (p.get("parents") or []):
        p["parents"] = list(p.get("parents") or []) + [parent]
    if created or p != before:
        doc.properties = p
        est.upsert(doc, embedder=None)
    return doc


def _digest(cat: dict) -> str:
    import hashlib
    import json
    return hashlib.sha1(json.dumps(cat, sort_keys=True, default=str).encode()).hexdigest()[:16]


def import_catalog(srv, cat) -> dict:
    """Home Assistant's catalog -> classes, live entities and methods."""
    from . import night as N
    est = getattr(srv, "entity_store", None)
    if est is None or not isinstance(cat, dict):
        return {"ok": False, "error": "no entity layer or no catalog"}
    ents = [e for e in (cat.get("entities") or []) if isinstance(e, dict) and "." in str(e.get("entity_id") or "")]
    services = cat.get("services") if isinstance(cat.get("services"), dict) else {}
    digest = _digest({"entities": ents, "services": services, "mqtt": bool(cat.get("mqtt"))})
    root = _ensure(est, ROOT, {}, summary="Home Assistant: the root class of every imported HA domain")
    if (root.properties or {}).get("catalog_digest") == digest:
        return {"ok": True, "unchanged": True}
    fld, mem = getattr(srv, "efield", None), getattr(srv, "membrane", None)
    errors: list[str] = []
    for name, cmd in zip(_GENERIC, (SERVICE_CMD, DOMAIN_CMD)):
        r = N.method_define(srv, est, fld, mem, root.eid, name, cmd)
        if not r.get("ok"):
            errors.append(f"{ROOT}.{name}: {r.get('error')}")
    by_domain: dict[str, list] = {}
    for e in ents:
        by_domain.setdefault(str(e["entity_id"]).split(".", 1)[0], []).append(e)
    domains = set(by_domain) | {d for d, svcs in services.items() if isinstance(svcs, dict)
                                and any(isinstance(s, dict) and not s.get("target") for s in svcs.values())}
    n_ent = n_m = 0
    for dom in sorted(domains):
        cls = _ensure(est, f"{ROOT}:{dom}", {"ha_domain": dom}, parent=root.eid,
                      summary=f"Home Assistant domain {dom}: its entities and services")
        for e in by_domain.get(dom, []):
            hid = str(e["entity_id"])
            label = str(e.get("name") or "").strip()
            _ensure(est, f"{ROOT}:{hid}", {PROP: hid}, parent=cls.eid,
                    summary=f"{label} (Home Assistant {hid})" if label else f"Home Assistant {hid}")
            n_ent += 1
        for svc, spec in sorted((services.get(dom) or {}).items()):
            spec = spec if isinstance(spec, dict) else {}
            mname = re.sub(r"[^A-Za-z0-9_]+", "_", str(svc)).strip("_")
            if not mname or mname in _GENERIC:
                continue
            call = "service" if spec.get("target") else "domain_service"
            fields = [str(f) for f in (spec.get("fields") or [])]
            for name, data in ((mname, "{}"), (mname + "_with", "{{data}}")):
                r = N.method_define(srv, est, fld, mem, cls.eid, name, "",
                                    steps=[{"name": "call", "call": call, "object": "self",
                                            "args": {"service": str(svc), "data": data}}])
                if not r.get("ok"):
                    errors.append(f"{dom}.{name}: {r.get('error')}")
                    continue
                n_m += 1
                if data != "{}" and fields:
                    m = est.docs.get(r["eid"])
                    if m is not None:
                        m.properties = {**(m.properties or {}), "summary": f"{dom}.{svc}; data keys: {', '.join(fields)}"}
                        est.upsert(m, embedder=None)
    root.properties = {**(root.properties or {}), "catalog_digest": digest, "mqtt": bool(cat.get("mqtt"))}
    est.upsert(root, embedder=None)
    return {"ok": not errors, "classes": len(domains), "entities": n_ent, "methods": n_m, "errors": errors[:10]}


# ---------------------------------------------------------------------------
# MQTT THROUGH HOME ASSISTANT (2026-10-30, the operator: "auto-integrate mqtt
# with the field if its configured in hass"): when HA runs its MQTT integration,
# the seat needs no broker of its own. An entity marked
# properties.mqtt_topic = '<topic filter>' (+ and # wildcards) is subscribed by
# HA (GET /membrane/ha/subscriptions carries mqtt_topics); HA pushes
#   POST /membrane/ha/mqtt  <- {messages: [{topic, payload}]}
# and each matching entity's mqtt_payload / mqtt_last_topic / mqtt_changed are
# set, so its watches fire. Publishing is HA's own service, imported above:
# ha:mqtt.publish_with(data='{"topic": ..., "payload": ...}').
MQTT_PROP = "mqtt_topic"
MAX_PAYLOAD = 4000


def topic_matches(flt: str, topic: str) -> bool:
    f, t = str(flt).split("/"), str(topic).split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


def _mqtt_marked(srv) -> dict[str, list]:
    est = getattr(srv, "entity_store", None)
    out: dict[str, list] = {}
    for ent in list((getattr(est, "docs", None) or {}).values()):
        t = str((ent.properties or {}).get(MQTT_PROP) or "").strip()
        if t == "#" and not (ent.properties or {}).get("mqtt_allow_all"):
            continue  # a bare '#' mirrors the whole broker: needs mqtt_allow_all=True
        if t:
            out.setdefault(t, []).append(ent)
    return out


def mqtt_topics(srv) -> list[str]:
    return sorted(_mqtt_marked(srv))


def apply_mqtt(srv, msgs) -> dict:
    """Set the pushed MQTT messages on the entities whose topic filter matches."""
    est = getattr(srv, "entity_store", None)
    if est is None or not isinstance(msgs, list):
        return {"updated": 0, "unknown": 0}
    marked = _mqtt_marked(srv)
    n = unknown = 0
    for msg in msgs:
        if not isinstance(msg, dict) or not msg.get("topic"):
            continue
        topic, payload = str(msg["topic"]), str(msg.get("payload") or "")[:MAX_PAYLOAD]
        ents = [e for flt, es in marked.items() if topic_matches(flt, topic) for e in es]
        if not ents:
            unknown += 1
            continue
        for ent in ents:
            pp = dict(ent.properties or {})
            if pp.get("mqtt_payload") == payload and pp.get("mqtt_last_topic") == topic:
                continue
            pp.update({"mqtt_payload": payload, "mqtt_last_topic": topic,
                       "mqtt_changed": time.strftime("%Y-%m-%dT%H:%M:%S")})
            ent.properties = pp
            try:
                est.upsert(ent, embedder=None)
            except Exception:
                continue
            n += 1
    return {"updated": n, "unknown": unknown}
