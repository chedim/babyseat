"""The DETACHED seat's Home Assistant access (2026-10-12, the operator asked to
migrate the boat's own Hass membrane: "can you do that for me without losing my
current field?") -- ha_access.py's connect-mode parts, without Home Assistant
installed: the token POSTed to the seat over its own serve, and the
restarts.py notices (ha-start, ha-stop) riding the same door.

ha_access imports homeassistant at the top; the modules it needs are stubbed.
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CC = ROOT / "custom_components" / "membrane"


_HA_FAMILY = ("homeassistant", "homeassistant.auth", "homeassistant.auth.const",
              "homeassistant.const", "homeassistant.core")


@pytest.fixture(scope="module")
def haa():
    pytest.importorskip("aiohttp")
    # A real Home Assistant answers first (a dev env with it installed --
    # never CI); when any name fails to come up, top the family up with
    # stubs -- EVERY missing name, not all-or-nothing: a partial
    # "homeassistant" module from some other import path (the CI exhibit:
    # the guard `if "homeassistant" not in sys.modules` skipped the
    # install and homeassistant.auth came up missing) must not leave the
    # family half-built.
    real = True
    for name in _HA_FAMILY:
        if name in sys.modules:
            continue
        try:
            importlib.import_module(name)
        except Exception:
            real = False
    if not real:
        if "homeassistant" in sys.modules and \
                not hasattr(sys.modules["homeassistant"], "__path__"):
            for name in _HA_FAMILY:            # a partial stub: out with it
                sys.modules.pop(name, None)
        ha = types.ModuleType("homeassistant")
        ha.__path__ = []                       # a namespace package
        auth = types.ModuleType("homeassistant.auth")
        auth.__path__ = []
        auth_const = types.ModuleType("homeassistant.auth.const")
        auth_const.GROUP_ID_USER = "user"
        const = types.ModuleType("homeassistant.const")
        const.EVENT_STATE_CHANGED = "state_changed"
        core = types.ModuleType("homeassistant.core")

        class HomeAssistant:                       # never constructed in these tests
            pass

        class Event:                               # never constructed
            pass

        core.HomeAssistant, core.Event, core.callback = HomeAssistant, Event, (lambda f: f)
        ha.auth, ha.const, ha.core = auth, const, core
        auth.const = auth_const                # the attribute chain, for import
        for name, stub in {"homeassistant": ha, "homeassistant.auth": auth,
                           "homeassistant.auth.const": auth_const,
                           "homeassistant.const": const,
                           "homeassistant.core": core}.items():
            if sys.modules.get(name) is None:
                sys.modules[name] = stub
    pkg = types.ModuleType("cc_membrane"); pkg.__path__ = [str(CC)]
    sys.modules["cc_membrane"] = pkg
    for name in ("const", "ha_access"):
        spec = importlib.util.spec_from_file_location(f"cc_membrane.{name}", CC / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    return sys.modules["cc_membrane.ha_access"]


class _Seat:
    """The fake seat: /membrane/ha/token and /membrane/seat/event are the only
    doors a detached integration knocks on."""

    def __init__(self):
        from aiohttp import web
        self.token, self.events = [], []
        app = web.Application()
        app.router.add_post("/membrane/ha/token", self._tok)
        app.router.add_post("/membrane/seat/event", self._ev)
        self.app, self._web = app, web

    async def _tok(self, request):
        self.token.append(await request.json())
        return self._web.json_response({"ok": True})

    async def _ev(self, request):
        self.events.append(await request.json())
        return self._web.json_response({"ok": True})


@pytest.fixture
def serve(haa):
    from aiohttp import test_utils
    seat = _Seat()
    server = test_utils.TestServer(seat.app)
    loop = asyncio.get_event_loop_policy().new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.start_server())
    seat.url, seat.port = f"http://127.0.0.1:{server.port}", server.port
    yield seat
    try:
        loop.run_until_complete(server.close())
    finally:
        asyncio.set_event_loop(None)
        loop.close()


class _Config:
    internal_url = "http://192.168.1.15:8123"
    external_url = None
    api = None


class _Hass:
    config = _Config()


def _comp(seat):
    return {"serve_url": seat.url, "serve_user": "opencode", "serve_pass": "pw"}


async def _fake_mint(hass, entry):
    return "fresh-tok"


def test_the_token_rides_to_the_seat(serve, haa, monkeypatch):
    monkeypatch.setattr(haa, "mint_token", _fake_mint)
    out = asyncio.get_event_loop_policy().get_event_loop().run_until_complete(
        haa.push_token(_Hass(), None, _comp(serve)))
    assert out is True
    assert serve.token == [{"url": "http://192.168.1.15:8123", "token": "fresh-tok"}]   # HA's own answer


def test_the_token_rides_to_the_seat_with_an_override(serve, haa, monkeypatch):
    monkeypatch.setattr(haa, "mint_token", _fake_mint)
    out = asyncio.get_event_loop_policy().get_event_loop().run_until_complete(
        haa.push_token(_Hass(), None, _comp(serve) | {"ha_base_url": "https://home.example.org"}))
    assert out is True and serve.token[-1]["url"] == "https://home.example.org"


def test_a_seat_that_never_answered(serve, haa, monkeypatch):
    monkeypatch.setattr(haa, "mint_token", _fake_mint)
    run = lambda c: asyncio.get_event_loop_policy().get_event_loop().run_until_complete(
        haa.push_token(_Hass(), None, c))
    assert run({"serve_url": "http://127.0.0.1:1", "serve_pass": "x"}) is False     # nothing answers there
    assert run({"serve_url": serve.url}) is False                                   # no pair, no knock
    assert serve.token == []


def test_ha_start_and_stop_are_told(serve, haa):
    run = lambda kind: asyncio.get_event_loop_policy().get_event_loop().run_until_complete(
        haa.seat_event(_comp(serve), kind))
    assert run("ha-start") is True and run("ha-stop") is True
    assert serve.events == [{"kind": "ha-start"}, {"kind": "ha-stop"}]
    assert asyncio.get_event_loop_policy().get_event_loop().run_until_complete(
        haa.seat_event({}, "ha-start")) is False            # nothing configured: silence


def test_the_sync_word_reaches_the_seat_too(serve, haa):
    """ha-stop posts by hand from an executor thread during HA's shutdown
    (2026-10-12 docker rehearsal: the sync listener's background task died
    with 'loop is not the running loop')."""
    serve._loop = asyncio.get_event_loop_policy().get_event_loop()
    import threading

    def pump():
        serve._loop.run_forever()

    pumper = threading.Thread(target=pump, daemon=True)      # the fixture's loop must run for the
    pumper.start()                                           # aiohttp server to answer a plain thread
    try:
        out = []
        t = threading.Thread(target=lambda: out.append(haa.seat_event_sync(_comp(serve), "ha-stop")))
        t.start()
        t.join(30)
        assert out == [True]                                # no loop, no coroutines: plain urllib
        assert serve.events[-1] == {"kind": "ha-stop"}
        assert haa.seat_event_sync({}, "ha-stop") is False
        assert haa.seat_event_sync({"serve_url": "http://127.0.0.1:1", "serve_pass": "x"}, "ha-stop") is False
    finally:
        serve._loop.call_soon_threadsafe(serve._loop.stop)


def test_ha_urls_a_loopback_falls_back(haa):
    class C:                                    # no configured URLs: HA's local door
        internal_url = None
        external_url = None
    h = _Hass()
    h.config = C()
    assert haa.routable_url(h).startswith("http://127.0.0.1")
    assert haa.routable_url(_Hass()) == "http://192.168.1.15:8123"