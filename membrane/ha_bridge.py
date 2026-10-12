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
            "ha_attributes on every change (its watches fire). What you do in the house is real: "
            "act only when asked, or when a goal of theirs plainly calls for it.\n")
