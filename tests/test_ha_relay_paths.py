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
    "membrane/decision/drop",
    "membrane/quota/resume",
    "membrane/options",
)


class FakeStore:
    """HA's Store, as far as the relay uses it: one disk per key, a load that
    yields to the loop (HA's reads the file in an executor) and a save that
    lands at once (HA's lands after the delay)."""
    disk = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        import asyncio
        await asyncio.sleep(0.01)
        return FakeStore.disk.get(self.key)

    def async_delay_save(self, data_func, delay=0):
        FakeStore.disk[self.key] = data_func()


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
        "homeassistant.helpers.storage": mod("homeassistant.helpers.storage", Store=FakeStore),
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


# ---- methods matter, not only paths: the relay view end to end ---------------------
# A fake seat records what reaches it; the view runs on a mocked HA request.

def _relay_call(relay, method, rest, uid="u1", is_admin=False, owners=None, body=b""):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestServer, make_mocked_request

    seen = []

    async def seat(request):
        seen.append((request.method, request.path))
        return web.json_response(True)

    async def go():
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", seat)
        async with TestServer(app) as srv:
            comp = {"serve_url": str(srv.make_url("")), "serve_ready": True,
                    "owners": dict(owners or {}),
                    "owners_store": types.SimpleNamespace(async_delay_save=lambda *a, **k: None)}
            hass = types.SimpleNamespace(data={"membrane": comp})
            req = make_mocked_request(method, "/api/membrane/serve/" + rest,
                                      match_info={"path": rest}, app={"hass": hass},
                                      payload=None)
            req["hass_user"] = types.SimpleNamespace(id=uid, name="Tester", is_admin=is_admin)
            if body:
                async def _read():
                    return body
                req.read = _read
            view = relay.ServeProxyView()
            resp = await getattr(view, method.lower())(req, path=rest)
            if comp.get("http_session") is not None:
                await comp["http_session"].close()
            return resp.status, seen, comp

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


def test_the_chat_delete_reaches_the_seat(relay):
    # the panel's 🗑 "Delete chat": DELETE serve/session/<id> of the caller's own chat
    status, seen, comp = _relay_call(relay, "DELETE", "session/ses_1", owners={"ses_1": "u1"})
    assert status == 200, status
    assert seen == [("DELETE", "/session/ses_1")], seen
    assert "ses_1" not in comp["owners"]


def test_someone_elses_chat_delete_is_a_404(relay):
    status, seen, _ = _relay_call(relay, "DELETE", "session/ses_1", owners={"ses_1": "u2"})
    assert (status, seen) == (404, [])


def test_an_owner_write_needs_an_admin(relay):
    status, seen, _ = _relay_call(relay, "POST", "membrane/decision/answer", body=b'{"card": "x"}')
    assert (status, seen) == (403, [])
    status, seen, _ = _relay_call(relay, "POST", "membrane/decision/answer", is_admin=True, body=b'{"card": "x"}')
    assert status == 200 and seen == [("POST", "/membrane/decision/answer")]


def test_a_cockpit_read_is_get_only(relay):
    status, seen, _ = _relay_call(relay, "GET", "membrane/decisions")
    assert status == 200 and seen == [("GET", "/membrane/decisions")]
    status, seen, _ = _relay_call(relay, "POST", "membrane/decisions", body=b"{}")
    assert (status, seen) == (405, [])


def test_a_chat_round_trip_through_the_relay_to_a_real_seat(relay):
    # the panel's chat lifecycle against membrane/server.py's own handler:
    # 🆕 POST session, the list, ✎ PATCH title, 🗑 DELETE -- every method relayed
    import asyncio
    import json
    import threading
    from http.server import ThreadingHTTPServer

    from aiohttp.test_utils import make_mocked_request

    from membrane import server as NS

    sessions = NS.SessionStore(None)
    handler = NS.make_handler(hub=NS.SSEHub(), sessions=sessions, messages=NS.MessageStore(),
                              config={}, runner=None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    comp = {"serve_url": f"http://127.0.0.1:{httpd.server_address[1]}", "serve_ready": True,
            "owners": {}, "owners_store": types.SimpleNamespace(async_delay_save=lambda *a, **k: None)}

    async def call(method, rest, body=b""):
        hass = types.SimpleNamespace(data={"membrane": comp})
        req = make_mocked_request(method, "/api/membrane/serve/" + rest,
                                  match_info={"path": rest}, app={"hass": hass})
        req["hass_user"] = types.SimpleNamespace(id="u1", name="Tester", is_admin=False)

        async def _read():
            return body
        req.read = _read
        resp = await getattr(relay.ServeProxyView(), method.lower())(req, path=rest)
        return resp.status, json.loads(resp.body or b"null")

    async def go():
        try:
            st, s = await call("POST", "session", b"{}")
            assert st == 200 and s["id"] in comp["owners"]
            sid = s["id"]
            st, rows = await call("GET", "session")
            assert st == 200 and [r["id"] for r in rows] == [sid]
            st, s = await call("PATCH", "session/" + sid, b'{"title": "renamed"}')
            assert st == 200 and s["title"] == "renamed"
            st, out = await call("DELETE", "session/" + sid)
            assert (st, out) == (200, True)
            assert sid not in comp["owners"] and sid not in sessions.sessions
        finally:
            if comp.get("http_session") is not None:
                await comp["http_session"].close()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
        httpd.shutdown()


def test_the_owners_load_once_when_the_first_requests_race(relay):
    # the live trace (#470): right after HA starts, the page's first requests
    # (the chat list, the status, the live stream) all find no owners map and
    # each loaded its own; the chat POSTed into one of them (and saved it to
    # .storage), while the next request read another -- so the 🗑 DELETE of
    # the caller's own fresh chat answered 404 though the store had its owner
    import asyncio
    import json
    import threading
    from http.server import ThreadingHTTPServer

    from aiohttp.test_utils import make_mocked_request

    from membrane import server as NS

    FakeStore.disk.clear()
    sessions = NS.SessionStore(None)
    handler = NS.make_handler(hub=NS.SSEHub(), sessions=sessions, messages=NS.MessageStore(),
                              config={}, runner=None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    comp = {"serve_url": f"http://127.0.0.1:{httpd.server_address[1]}", "serve_ready": True}
    hass = types.SimpleNamespace(data={"membrane": comp})

    async def call(method, rest, body=b""):
        req = make_mocked_request(method, "/api/membrane/serve/" + rest,
                                  match_info={"path": rest}, app={"hass": hass})
        req["hass_user"] = types.SimpleNamespace(id="u1", name="Tester", is_admin=True)

        async def _read():
            return body
        req.read = _read
        resp = await getattr(relay.ServeProxyView(), method.lower())(req, path=rest)
        return resp.status, json.loads(resp.body or b"null")

    async def go():
        try:
            (st, s), _, _ = await asyncio.gather(call("POST", "session", b'{"title": "relay test"}'),
                                                 call("GET", "session"), call("GET", "session/status"))
            assert st == 200
            sid = s["id"]
            assert FakeStore.disk["membrane.sessions"]["owners"] == {sid: "u1"}
            st, out = await call("DELETE", "session/" + sid)
            assert (st, out) == (200, True)
            assert sid not in sessions.sessions
        finally:
            if comp.get("http_session") is not None:
                await comp["http_session"].close()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
        httpd.shutdown()
