"""The brain's field metrics (pure: no HA imports -- unit-tested).

Source: the seat's /membrane/state (relayed by the serve, 60s-cached on the
seat) + the serve's /agent rows (the loop workers' live claims). Each
METRICS spec becomes one hass entity; metrics_from() maps a state payload
to {key: value}, None for anything the payload lacks.
"""
from __future__ import annotations

import re

# key, name, platform, unit, state_class, icon
METRICS: list[dict] = [
    {"key": "field_live", "name": "Field entities", "platform": "sensor",
     "unit": "entities", "state_class": "measurement", "icon": "mdi:graph"},
    {"key": "field_nursery", "name": "Nursery entities", "platform": "sensor",
     "unit": "entities", "state_class": "measurement", "icon": "mdi:sprout"},
    {"key": "events_applied", "name": "Events applied", "platform": "sensor",
     "unit": "events", "state_class": "total_increasing", "icon": "mdi:counter"},
    {"key": "events_waiting", "name": "Events waiting", "platform": "sensor",
     "unit": "events", "state_class": "measurement", "icon": "mdi:timer-sand"},
    {"key": "eff_rank", "name": "Field effective rank", "platform": "sensor",
     "unit": None, "state_class": "measurement", "icon": "mdi:axis-arrow"},
    {"key": "free_energy", "name": "Field free energy", "platform": "sensor",
     "unit": None, "state_class": "measurement", "icon": "mdi:flash"},
    {"key": "conflict_pct", "name": "Field conflicts", "platform": "sensor",
     "unit": "%", "state_class": "measurement", "icon": "mdi:alert-decagram"},
    {"key": "audit_records", "name": "Audit records", "platform": "sensor",
     "unit": "records", "state_class": "total_increasing", "icon": "mdi:file-document-multiple"},
    {"key": "verdicts", "name": "Verdicts", "platform": "sensor",
     "unit": None, "state_class": "measurement", "icon": "mdi:gavel"},
    {"key": "scripts_failing", "name": "Failing watch scripts", "platform": "sensor",
     "unit": "scripts", "state_class": "measurement", "icon": "mdi:script-text-outline"},
    {"key": "thinker_beats", "name": "Thinker beats", "platform": "sensor",
     "unit": "beats", "state_class": "total_increasing", "icon": "mdi:head-cog"},
    {"key": "think_queue", "name": "Thinker queue", "platform": "sensor",
     "unit": None, "state_class": "measurement", "icon": "mdi:tray-full"},
    {"key": "tracks", "name": "Entity tracks", "platform": "sensor",
     "unit": None, "state_class": "measurement", "icon": "mdi:radar"},
    {"key": "loops_busy", "name": "Loop workers busy", "platform": "sensor",
     "unit": "workers", "state_class": "measurement", "icon": "mdi:sync"},
    {"key": "events_queued", "name": "Loop events queued", "platform": "sensor",
     "unit": "events", "state_class": "measurement", "icon": "mdi:format-list-numbered"},
    {"key": "ambient", "name": "Ambient mode", "platform": "sensor",
     "unit": None, "state_class": None, "icon": "mdi:weather-night"},
    {"key": "paused", "name": "Paused", "platform": "binary_sensor",
     "unit": None, "state_class": None, "icon": "mdi:pause-circle"},
]


def _dig(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _num(v, nd=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return round(float(v), nd) if nd is not None else v


def _len(v):
    return len(v) if isinstance(v, (list, tuple, dict)) else None


def metrics_from(state, agents) -> dict:
    st = state if isinstance(state, dict) else {}
    ev = _dig(st, "efield", "events") or {}
    geo = _dig(st, "efield", "geometry") or {}
    applied = [v for k, v in ev.items() if k.endswith("/applied") and isinstance(v, int)]
    waiting = [v for k, v in ev.items()
               if (k.endswith("/waiting") or k.endswith("/pending")) and isinstance(v, int)]
    cf = _num(geo.get("conflict_frac"))
    out = {
        "field_live": _num(_dig(st, "efield", "entities", "live")),
        "field_nursery": _num(_dig(st, "efield", "entities", "nursery")),
        "events_applied": sum(applied) if applied else None,
        "events_waiting": sum(waiting) if (waiting or ev) else None,
        "eff_rank": _num(geo.get("eff_rank"), 1),
        "free_energy": _num(geo.get("free_energy"), 3),
        "conflict_pct": round(cf * 100, 2) if cf is not None else None,
        "audit_records": _num(st.get("audit_records")),
        "verdicts": _num(st.get("n_verdicts")),
        "scripts_failing": _len(_dig(st, "scripts", "failing")),
        "thinker_beats": _num(_dig(st, "think", "stats", "processed")),
        "think_queue": _len(_dig(st, "think", "queue")),
        "tracks": _len(st.get("entity_tracks")),
        "paused": st.get("paused") if isinstance(st.get("paused"), bool) else None,
        "loops_busy": None, "events_queued": None, "ambient": None,
    }
    rows = agents if isinstance(agents, list) else []
    loops = [a for a in rows if isinstance(a, dict) and re.fullmatch(r"loop-\d+", str(a.get("name", "")))]
    if loops:
        out["loops_busy"] = sum(1 for a in loops if "(processing" in str(a.get("description", "")))
        q = re.search(r"(\d+) event\(s\) queued", str(loops[0].get("description", "")))
        out["events_queued"] = int(q.group(1)) if q else None
    th = next((a for a in rows if isinstance(a, dict) and a.get("name") == "thinker"), None)
    if th:
        m = re.search(r"\((day|night)\b", str(th.get("description", "")))
        out["ambient"] = m.group(1) if m else None
    return out
