"""HOME ASSISTANT'S STATES, LIVE IN THE FIELD (2026-10-12, the operator: "can it
just... integrate?"): when Home Assistant runs the seat (the membrane integration,
custom_components/membrane), the seat acts through HA's REST API with the token HA
gave it (HASS_URL / HASS_TOKEN in its environment), and HA pushes the state changes
of the entities the seat chose to mirror:

  - the seat marks a field entity with properties.ha_entity = "<HA entity_id>"
  - GET  /membrane/ha/subscriptions  -> the entity_ids so marked (HA polls it)
  - POST /membrane/ha/states         <- [{entity_id, state, attributes, last_changed}]
    each marked entity's ha_state / ha_attributes / ha_changed are set, so its
    watches fire on the change -- no polling, no getter needed for the reading

No Home Assistant code here: the seat only knows the two routes and the property.
Which HA entities to mirror, and what to make of them, is the seat's own choice.
"""
from __future__ import annotations

import time

PROP = "ha_entity"
MAX_ATTRS = 4000          # bytes of attributes kept per entity (a media player's can be huge)


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
    """The seat card's line when Home Assistant runs this seat (HASS_URL is set)."""
    return ("\n- HOME ASSISTANT: you run inside Home Assistant and may use it -- its REST API is at "
            "$HASS_URL, authorized by $HASS_TOKEN (both in your commands' environment: "
            "curl -s -H \"Authorization: Bearer $HASS_TOKEN\" $HASS_URL/api/states/<entity_id>; "
            "services: POST $HASS_URL/api/services/<domain>/<service>). Build your own readers "
            "and actuators on it. To keep an entity of yours live without polling, give it "
            "properties.ha_entity = '<entity_id>': Home Assistant then sets its ha_state and "
            "ha_attributes on every change (its watches fire). What you do in the house is real: "
            "act only when asked, or when a goal of theirs plainly calls for it.\n")
