"""LIVE HOME ASSISTANT STATE (2026-10-08, the transcript review).

The field is MEMORY, not telemetry. "How's the battery?" on a phone call
cost 20+ executor shell calls hunting the HA token, then ended in a false
"the BMS never publishes a SOC" (sensor.agatha_bms_battery does). This is
the one read of live HA: an entity id, or a word search over ids and
friendly names. The token stays here (the seat's hass.env); the model sees
states, never credentials. Read-only: no service calls.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

DEFAULT_ENV = os.path.expanduser(os.environ.get("MEMBRANE_HASS_ENV",
                                                "~/.config/membrane/hass.env"))


def load_env(path: str = DEFAULT_ENV) -> dict:
    """hass.env (HASS_URL=..., HASS_TOKEN=..., `export` and quotes allowed)
    -> {url, token}; {} when absent."""
    out: dict = {}
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            k, sep, v = line.partition("=")
            if not sep:
                continue
            v = v.strip().strip("'\"")
            if k.strip() == "HASS_URL":
                out["url"] = v.rstrip("/")
            elif k.strip() == "HASS_TOKEN":
                out["token"] = v
    except OSError:
        return {}
    return out if out.get("url") and out.get("token") else {}


def _get(env: dict, path: str, timeout: float = 8):
    req = urllib.request.Request(env["url"] + path,
                                 headers={"Authorization": "Bearer " + env["token"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_one(env: dict, entity_id: str) -> dict:
    return _get(env, "/api/states/" + urllib.parse.quote(entity_id, safe="._"))


def fetch_all(env: dict) -> list:
    return _get(env, "/api/states", timeout=15) or []


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w]


def search(states: list, q: str, k: int = 8) -> list:
    """Every query word must appear in the entity id or friendly name
    (word starts count); the ids with the most whole-word hits rank first."""
    want = _words(q)
    if not want:
        return []
    scored = []
    for st in states or []:
        eid = str(st.get("entity_id") or "")
        name = str((st.get("attributes") or {}).get("friendly_name") or "")
        hay = _words(eid) + _words(name)
        if all(any(h.startswith(w) for h in hay) for w in want):
            exact = sum(1 for w in want if w in hay)
            scored.append((-exact, len(eid), eid, st))
    scored.sort(key=lambda t: t[:3])
    return [t[3] for t in scored[:max(1, k)]]


def render(st: dict) -> str:
    a = st.get("attributes") or {}
    unit = a.get("unit_of_measurement")
    val = f"{st.get('state')}" + (f" {unit}" if unit else "")
    name = a.get("friendly_name") or ""
    when = str(st.get("last_changed") or "")[:19].replace("T", " ")
    return (f"{st.get('entity_id')} = {val}"
            + (f"  ({name}" + (f"; changed {when} UTC" if when else "") + ")" if name or when else ""))


def render_rows(rows: list) -> str:
    return "\n".join(render(r) for r in rows) if rows else "no Home Assistant entity matched"
