"""#529: the URL a detached seat is handed for Home Assistant follows where the
seat is -- its serve_url host: on HA's host (loopback) the local door may stay;
on HA's LAN (private / link-local) HA's LAN address; outside it HA's external
address; never a loopback address to a seat elsewhere -- with no usable
address a warning names the ha_base_url option and nothing is sent. The
ha_base_url option overrides everything. HA's own get_url answers (stubbed
here with its semantics: internal_url, else the source IP on HA's port;
external_url; NoURLAvailableError)."""
import asyncio
import logging
import sys
import types

import pytest

from tests.test_ha_detached import haa  # noqa: F401  (the module fixture)


class _NoURL(Exception):
    pass


def _fake_get_url(hass, *, allow_internal=True, allow_external=True, prefer_external=None, **_kw):
    """homeassistant.helpers.network.get_url, the parts that matter: internal =
    internal_url, else http://<api.local_ip>:<api.port> when that IP is not
    loopback; external = external_url; order by prefer_external."""
    c, api = hass.config, hass.config.api

    def internal():
        if c.internal_url:
            return c.internal_url
        if api is not None and api.local_ip and not api.local_ip.startswith("127."):
            return f"http://{api.local_ip}:{api.port}"
        return None

    def external():
        return c.external_url or None

    order = [external, internal] if prefer_external else [internal, external]
    for f, ok in ((f, allow_internal if f is internal else allow_external) for f in order):
        if ok and (u := f()):
            return u
    raise _NoURL()


@pytest.fixture
def ha_net(monkeypatch, haa):  # noqa: F811
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    net = types.ModuleType("homeassistant.helpers.network")
    net.get_url, net.NoURLAvailableError = _fake_get_url, _NoURL
    helpers.network = net
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.network", net)
    return net


class _Api:
    def __init__(self, local_ip="192.168.1.15", host=None, port=8124, use_ssl=False):
        self.local_ip, self.host, self.port, self.use_ssl = local_ip, host, port, use_ssl


class _Config:
    def __init__(self, internal=None, external=None, api=None):
        self.internal_url, self.external_url, self.api = internal, external, api or _Api()


class _Hass:
    def __init__(self, **kw):
        self.config = _Config(**kw)


def _url(haa, hass, seat, **extra):  # noqa: F811
    comp = {"serve_url": seat, "serve_ready": True} | extra
    return asyncio.run(haa.seat_ha_url(hass, comp))


LAN_SEAT, PUBLIC_SEAT, LOCAL_SEAT = "http://192.168.1.40:4096", "http://8.8.8.8:4096", "http://127.0.0.1:4096"


def test_a_lan_seat_gets_has_lan_address(haa, ha_net):  # noqa: F811
    # the live bug: no internal/external URL configured -- the seat used to get 127.0.0.1:8124
    assert _url(haa, _Hass(), LAN_SEAT) == "http://192.168.1.15:8124"
    assert _url(haa, _Hass(internal="http://ha.lan:8123/"), LAN_SEAT) == "http://ha.lan:8123"
    assert _url(haa, _Hass(), "http://169.254.3.4:4096") == "http://192.168.1.15:8124"   # link-local


def test_a_public_seat_gets_has_external_address(haa, ha_net):  # noqa: F811
    h = _Hass(internal="http://192.168.1.15:8123", external="https://home.example.org")
    assert _url(haa, h, PUBLIC_SEAT) == "https://home.example.org"


def test_a_public_seat_without_an_external_address_gets_no_loopback(haa, ha_net, caplog):  # noqa: F811
    with caplog.at_level(logging.WARNING):
        assert _url(haa, _Hass(internal="http://192.168.1.15:8123"), PUBLIC_SEAT) is None
    assert "ha_base_url" in caplog.text


def test_ha_bound_to_loopback_offers_no_source_ip(haa, ha_net, caplog):  # noqa: F811
    # behind a reverse proxy: HA binds 127.0.0.1:8124, the proxy answers :8123 --
    # <source ip>:8124 is no LAN address; no guessing ports, a warning instead
    h = _Hass(api=_Api(host="127.0.0.1"))
    with caplog.at_level(logging.WARNING):
        assert _url(haa, h, LAN_SEAT) is None
    assert "ha_base_url" in caplog.text
    # ... unless HA was told its LAN address
    h = _Hass(internal="http://192.168.1.15:8123", api=_Api(host="127.0.0.1"))
    assert _url(haa, h, LAN_SEAT) == "http://192.168.1.15:8123"


def test_no_loopback_reaches_a_seat_elsewhere(haa, ha_net):  # noqa: F811
    h = _Hass(internal="http://127.0.0.1:8124", api=_Api(local_ip="127.0.0.1"))
    assert _url(haa, h, LAN_SEAT) is None
    assert _url(haa, h, PUBLIC_SEAT) is None


def test_a_local_seat_keeps_the_local_url(haa, ha_net):  # noqa: F811
    assert _url(haa, _Hass(api=_Api(local_ip="127.0.0.1")), LOCAL_SEAT) == "http://127.0.0.1:8124"
    assert _url(haa, _Hass(api=_Api(local_ip="127.0.0.1")), "http://localhost:4096") == "http://127.0.0.1:8124"


def test_ha_base_url_overrides_everything(haa, ha_net):  # noqa: F811
    for seat in (LAN_SEAT, PUBLIC_SEAT, LOCAL_SEAT):
        assert _url(haa, _Hass(), seat, ha_base_url="https://my.ha:9") == "https://my.ha:9"


def test_a_public_seat_with_nowhere_to_go_is_not_knocked(haa, ha_net, monkeypatch):  # noqa: F811
    async def mint(hass, entry):
        return "tok"
    monkeypatch.setattr(haa, "mint_token", mint)
    posted = []

    class _S:                                      # any POST would land here
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            posted.append(a)
            raise AssertionError("no POST with no usable address")
    monkeypatch.setattr(haa.aiohttp, "ClientSession", _S)
    out = asyncio.run(haa.push_token(_Hass(), None, {"serve_url": PUBLIC_SEAT, "serve_ready": True}))
    assert out is False and posted == []
