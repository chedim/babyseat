"""Every seat path the UI fetches is relayed under Home Assistant (#470).

Under HA the pages speak the seat through custom_components/membrane/relay.py,
which forwards only allow-listed paths. A path the UI calls but the relay does
not list answers 404 there (the decision cockpit: "can't read the decisions:
/membrane/decisions -> 404"). This scans membrane/ui/*.js for the seat paths it
fetches -- string literals starting "/membrane/", the *_PATH string constants,
and `<NAME>_PATH + "/suffix"` joins -- and asserts relay.allowed_path() lets
each through. The seat's owner-only writes the UI calls must be admin-gated on
HA (ADMIN_WRITE_EXACT), like membrane/models.

Home Assistant is not installed here: the modules relay.py imports are stubbed
for this module only (and taken out again after).
"""
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CC = ROOT / "custom_components" / "membrane"
UI = ROOT / "membrane" / "ui"
PKG = "cc_relay_paths"

# Paths the UI calls that are intentionally NOT reachable through the seat relay.
EXCLUDED = {
    # HA's own speech view: views.py rewrites "/ui/stt" to /api/membrane/stt, it
    # never goes through the seat relay (/api/membrane/serve/...)
    "ui/stt",
}

# The seat's owner-only writes (membrane/server.py: every write is behind the
# owner's door, seatauth.py; the relay holds the seat token) that the UI POSTs:
# on HA only an admin may make them.
UI_OWNER_WRITES = (
    "membrane/models",
    "membrane/seat/control",
    "membrane/seat/pause",
    "membrane/decision/answer",
    "membrane/decision/chat",
    "membrane/quota/resume",
    "membrane/options",
)


def _stubs():
    def mod(name, pkg=False, **attrs):
        m = types.ModuleType(name)
        if pkg:
            m.__path__ = []
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    class HomeAssistantView:
        url = name = None
        requires_auth = True

    return {
        "homeassistant": mod("homeassistant", True),
        "homeassistant.components": mod("homeassistant.components", True),
        "homeassistant.components.http": mod("homeassistant.components.http", HomeAssistantView=HomeAssistantView),
        "homeassistant.helpers": mod("homeassistant.helpers", True),
        "homeassistant.helpers.http": mod("homeassistant.helpers.http", KEY_HASS="hass"),
    }


@pytest.fixture(scope="module")
def relay():
    pytest.importorskip("aiohttp")
    saved = {k: v for k, v in sys.modules.items() if k == "homeassistant" or k.startswith("homeassistant.")}
    for k in saved:
        sys.modules.pop(k)
    sys.modules.update(_stubs())
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(CC)]
    sys.modules[PKG] = pkg
    out = None
    for name in ("const", "relay"):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", CC / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        out = m
    yield out
    for k in [k for k in sys.modules if k == "homeassistant" or k.startswith("homeassistant.") or k.startswith(PKG)]:
        sys.modules.pop(k)
    sys.modules.update(saved)


def _norm(p: str) -> str:
    return p.split("?", 1)[0].strip("/")


def ui_paths() -> dict:
    """{seat path (no leading slash, no query): [files]} the UI fetches."""
    found = {}
    for js in sorted(UI.glob("*.js")):
        src = js.read_text(encoding="utf-8")
        consts = dict(re.findall(r'\b([A-Z][A-Z0-9_]*_PATH)\s*=\s*["\'`]([^"\'`]*)["\'`]', src))
        paths = set(re.findall(r'["\'`](/membrane/[^"\'`?$\s]*)', src))
        paths.update(v for v in consts.values() if v.startswith("/"))
        for name, suffix in re.findall(r'\b([A-Z][A-Z0-9_]*_PATH)\s*\+\s*["\'`](/[^"\'`?$\s]*)', src):
            if name in consts:
                paths.add(consts[name] + suffix)
        for p in paths:
            p = _norm(p)
            if p:
                found.setdefault(p, []).append(js.name)
    return found


def test_the_scan_sees_the_cockpit():
    found = ui_paths()
    for p in ("membrane/decisions", "membrane/decision/answer", "membrane/decision/chat",
              "membrane/quota/resume", "membrane/models"):
        assert p in found, p


def test_every_ui_seat_path_is_relayed(relay):
    missing = sorted(f"{p} ({', '.join(f)})" for p, f in ui_paths().items()
                     if p not in EXCLUDED and not relay.allowed_path(p))
    assert not missing, "the HA relay 404s these UI paths: " + "; ".join(missing)


def test_the_exclusions_are_still_called(relay):
    found = ui_paths()
    for p in EXCLUDED:
        assert p in found, f"{p} is no longer called by the UI: drop it from EXCLUDED"
        assert not relay.allowed_path(p), f"{p} is relayed now: drop it from EXCLUDED"


def test_owner_writes_are_admin_only(relay):
    found = ui_paths()
    for p in UI_OWNER_WRITES:
        assert p in found, p
        assert relay.allowed_path(p), p
        assert p in relay.ADMIN_WRITE_EXACT, p
        assert not relay.read_only_path(p), p


def test_cockpit_reads_are_get_only(relay):
    for p in ("membrane/decisions", "membrane/goals", "membrane/stats", "membrane/quota", "membrane/map/search"):
        assert relay.read_only_path(p), p
