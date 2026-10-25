"""#283: the Blurp zoo -- several seats in one Home Assistant.

The integration keeps a LIST of seats (options["seats"]) and ONE active seat
for the whole house (options["active"]); the relay, the field sensors, the
Assist agent and speech follow it. It is switched from a `select.membrane_seat`
entity or from membrane home's 🦠 zoo (a card per seat, fanned out by
GET /api/membrane/zoo; a tap POSTs /api/membrane/zoo/active, admins only).
An unreachable seat answers the relay with a friendly JSON error the page
renders as "<name> is offline -- switch to <other>?".

Home Assistant is not installed here: the modules the integration imports are
stubbed for this module only (and taken out again after, so the other HA
tests build their own family).
"""
import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CC = ROOT / "custom_components" / "membrane"
UI = ROOT / "membrane" / "ui"
PKG = "cc_zoo283"
KEY_HASS = "hass"


def _stubs():
    from aiohttp import web

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

        def json(self, data, status_code=200):
            return web.json_response(data, status=status_code)

    class SelectEntity:                         # HA's Entity reads its _attr_ fields
        hass = None
        entity_id = None
        name = property(lambda self: self._attr_name)
        unique_id = property(lambda self: self._attr_unique_id)

        def async_write_ha_state(self):
            self.written = getattr(self, "written", 0) + 1

    return {
        "homeassistant": mod("homeassistant", True),
        "homeassistant.components": mod("homeassistant.components", True),
        "homeassistant.components.http": mod("homeassistant.components.http", HomeAssistantView=HomeAssistantView),
        "homeassistant.components.select": mod("homeassistant.components.select", SelectEntity=SelectEntity),
        "homeassistant.helpers": mod("homeassistant.helpers", True),
        "homeassistant.helpers.http": mod("homeassistant.helpers.http", KEY_HASS=KEY_HASS),
        "homeassistant.core": mod("homeassistant.core", callback=lambda f: f, HomeAssistant=object),
    }


@pytest.fixture(scope="module")
def cc():
    pytest.importorskip("aiohttp")
    saved = {k: v for k, v in sys.modules.items() if k == "homeassistant" or k.startswith("homeassistant.")}
    for k in saved:
        sys.modules.pop(k)
    sys.modules.update(_stubs())
    pkg = types.ModuleType(PKG); pkg.__path__ = [str(CC)]
    sys.modules[PKG] = pkg
    out = types.SimpleNamespace()
    for name in ("const", "zoo", "relay", "views", "select"):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", CC / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        setattr(out, name, m)
    yield out
    for k in [k for k in sys.modules if k == "homeassistant" or k.startswith("homeassistant.") or k.startswith(PKG)]:
        sys.modules.pop(k)
    sys.modules.update(saved)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


LAPTOP = {"id": "laptop", "name": "Laptop", "host": "192.168.1.107", "serve_port": 4096,
          "proxy_port": 11500, "token": "tok-laptop"}
PHONE = {"id": "phone", "name": "Phone", "host": "192.168.1.112", "serve_port": 4096,
         "proxy_port": 11500, "token": "tok-phone"}


# ---- the migration: one seat becomes a one-seat list, unchanged ----------------------

def _old_connect(data, opts):
    """What __init__.py set up for a connect entry before #283."""
    return {"serve_url": str(data.get("serve_url") or "").rstrip("/"),
            "serve_user": str(data.get("serve_user") or "opencode"),
            "serve_pass": str((opts["serve_pass"] if "serve_pass" in opts else data.get("serve_pass")) or "")}


@pytest.mark.parametrize("data,opts", [
    ({"mode": "connect", "serve_url": "http://192.168.1.112:4096/", "serve_user": "opencode", "serve_pass": "pw"}, {}),
    ({"mode": "connect", "serve_url": "https://brain.example.lan/seat", "serve_user": "boat", "serve_pass": "a"},
     {"serve_pass": "b", "landing": False}),
    ({"mode": "connect", "serve_url": "http://10.0.0.5:4096"}, {"serve_pass": ""}),
])
def test_a_connect_entry_migrates_to_a_one_seat_list_unchanged(cc, data, opts):
    new = cc.zoo.migrate(data, opts)
    assert len(new["seats"]) == 1
    seat = new["seats"][0]
    assert new["active"] == seat["id"]
    for k, v in opts.items():                       # the other settings are kept
        if k != "serve_pass":
            assert new[k] == v
    assert "serve_pass" not in new                  # the token lives on the seat now
    comp = {}
    cc.zoo.apply(comp, seat)
    assert {k: comp[k] for k in ("serve_url", "serve_user", "serve_pass")} == _old_connect(data, opts)
    assert comp["serve_ready"] is True
    assert cc.zoo.migrate(data, new) == new          # idempotent


def test_an_inside_entry_migrates_to_this_home_assistant(cc):
    new = cc.zoo.migrate({"mode": "inside", "backend": "http://o:11434", "model": "m", "embed_model": "e"},
                         {"loops": 2})
    assert new["seats"] == [{"id": "inside", "name": "this Home Assistant", "inside": True}]
    assert new["active"] == "inside" and new["loops"] == 2


def test_the_inside_seat_follows_its_child(cc):
    # the supervisor writes into the inside seat's slot; the house's connection
    # (comp) mirrors it only while that seat is the active one
    comp = {}
    slot = cc.zoo.SeatSlot(comp)
    comp["inside_slot"] = slot
    inside = {"id": "inside", "name": "this Home Assistant", "inside": True}
    cc.zoo.apply(comp, inside)
    slot["serve_url"] = "http://127.0.0.1:18096"
    slot["serve_ready"] = True
    assert comp["serve_url"] == "http://127.0.0.1:18096" and comp["serve_ready"] is True
    cc.zoo.apply(comp, LAPTOP)
    slot["serve_ready"] = False                     # the child respawns: the laptop is unaffected
    assert comp["serve_url"] == "http://192.168.1.107:4096" and comp["serve_ready"] is True
    assert comp["serve_pass"] == "tok-laptop"
    cc.zoo.apply(comp, inside)
    assert comp["serve_url"] == "http://127.0.0.1:18096" and comp["serve_ready"] is False


def test_the_integration_migrates_on_the_minor_version():
    init = (CC / "__init__.py").read_text()
    flow = (CC / "config_flow.py").read_text()
    assert "async def async_migrate_entry" in init and "zoo.migrate(" in init
    assert "MINOR_VERSION = 2" in flow


# ---- the active seat routes everything ----------------------------------------------

def test_the_relay_url_builder_follows_the_active_seat(cc):
    comp = {}
    cc.zoo.apply(comp, LAPTOP)
    assert cc.relay.upstream_url(comp, "session/status") == "http://192.168.1.107:4096/session/status"
    assert cc.relay.upstream_headers(comp)["Authorization"] == cc.relay.basic_header("opencode", "tok-laptop")
    comp["sessions"] = {"conv1": "ses_x"}           # Assist's chats belong to the old seat
    cc.zoo.apply(comp, PHONE)
    assert cc.relay.upstream_url(comp, "/global/health") == "http://192.168.1.112:4096/global/health"
    assert cc.relay.upstream_headers(comp)["Authorization"] == cc.relay.basic_header("opencode", "tok-phone")
    assert comp["sessions"] == {} and comp["active_seat"] == "phone" and comp["seat_name"] == "Phone"


class _Entry:
    def __init__(self, options):
        self.entry_id, self.options, self.data = "e1", options, {"mode": "connect"}


class _Hass:
    def __init__(self, comp):
        self.data = {"membrane": comp}
        self.updates, self.tasks = [], []
        self.config_entries = types.SimpleNamespace(async_update_entry=self._upd)

    def _upd(self, entry, options=None, **kw):
        self.updates.append(options)
        entry.options = options

    def async_create_task(self, coro, *a, **k):
        self.tasks.append(coro)
        coro.close()


def _house(cc, active="laptop"):
    opts = {"seats": [dict(LAPTOP), dict(PHONE)], "active": active, "landing": True}
    entry = _Entry(opts)
    comp = {"entry": entry, "seats": opts["seats"]}
    cc.zoo.apply(comp, cc.zoo.find(opts["seats"], active))
    hass = _Hass(comp)
    return hass, entry, comp


def test_switch_applies_live_and_persists(cc):
    hass, entry, comp = _house(cc)
    heard = []
    comp.setdefault("on_switch", []).append(lambda: heard.append(comp["active_seat"]))

    class Coord:
        async def async_request_refresh(self):
            pass
    comp["coordinator"] = Coord()
    assert cc.zoo.switch(hass, entry, "phone") is True
    assert comp["serve_url"] == "http://192.168.1.112:4096"
    assert entry.options["active"] == "phone" and entry.options["landing"] is True
    assert heard == ["phone"] and len(hass.tasks) == 1          # the sensors re-read the new seat
    # the update listener must not reload for a switch alone (it was applied live)
    assert comp["applied_opts"] == cc.zoo.settings_of(entry.options)
    assert cc.zoo.switch(hass, entry, "nope") is False
    assert comp["active_seat"] == "phone"


def test_the_reload_listener_skips_a_switch():
    init = (CC / "__init__.py").read_text()
    assert 'comp.get("applied_opts") == zoo.settings_of(entry.options)' in init


# ---- select.membrane_seat -----------------------------------------------------------

def test_the_select_entity_switches(cc):
    hass, entry, comp = _house(cc)
    ent = cc.select.MembraneSeatSelect(hass, entry)
    assert ent.name == "Membrane seat" and ent.unique_id == "e1_seat"
    assert ent.options == ["Laptop", "Phone"]
    assert ent.current_option == "Laptop"
    _run(ent.async_select_option("Phone"))
    assert comp["active_seat"] == "phone" and ent.current_option == "Phone"
    assert entry.options["active"] == "phone"


def test_select_names_are_unique_and_never_empty(cc):
    seats = [{"id": "a", "name": "", "host": "10.0.0.1", "serve_port": 4096},
             {"id": "b", "name": "Blurp", "host": "10.0.0.2", "serve_port": 4096},
             {"id": "c", "name": "Blurp", "host": "10.0.0.3", "serve_port": 4096}]
    assert cc.zoo.names(seats) == ["10.0.0.1", "Blurp", "Blurp (10.0.0.3)"]


def test_the_platform_is_registered():
    assert "Platform.SELECT" in (CC / "__init__.py").read_text()


# ---- the zoo fan-out ------------------------------------------------------------------

def _seat_app(token, card=None, sheet=None, recent=None, slow=0.0, conf=None):
    import base64
    from aiohttp import web

    def ok(request):
        a = request.headers.get("Authorization", "")
        return a == "Basic " + base64.b64encode(("opencode:" + token).encode()).decode()

    async def h_card(request):
        if slow:
            await asyncio.sleep(slow)
        if not ok(request):
            return web.Response(status=401)
        if card is None:
            return web.Response(status=404, text="no such path")
        return web.json_response(card)

    async def h_sheet(request):
        if not ok(request):
            return web.Response(status=401)
        return web.json_response(sheet or {})

    async def h_recent(request):
        return web.json_response({"lines": recent or []})
    async def h_conf(request):
        if not ok(request):
            return web.Response(status=401)
        if conf is None:
            return web.Response(status=404, text="no such path")
        return web.json_response(conf)
    app = web.Application()
    app.router.add_get("/ui/conf", h_conf)
    app.router.add_get("/membrane/self/card", h_card)
    app.router.add_get("/membrane/self/sheet", h_sheet)
    app.router.add_get("/membrane/self/recent", h_recent)
    return app


def test_the_fan_out_reads_every_seat_and_survives_an_offline_one(cc):
    import aiohttp
    from aiohttp import test_utils

    async def go():
        a = test_utils.TestServer(_seat_app("ta", card={"name": "Pip", "level": 3, "rung_name": "take turns", "next": 4,
                                                        "mode": "watch", "awake": True, "generated": 1760000001.0,
                                                        "last_activity": "2025-10-09T08:53:20Z"}))
        b = test_utils.TestServer(_seat_app("tb", sheet={"header": {"name": "Blurp", "level": 6,
                                                                    "rung_name": "meet an other", "mode": "off"}},
                                            recent=[{"ts": 1760000123.5, "kind": "cycle"}]))
        slow = test_utils.TestServer(_seat_app("ts", card={"name": "Slow"}, slow=5.0))
        for s in (a, b, slow):
            await s.start_server()
        seats = [{"id": "a", "name": "", "host": "127.0.0.1", "serve_port": a.port, "token": "ta"},
                 {"id": "b", "name": "Boat", "host": "127.0.0.1", "serve_port": b.port, "token": "tb"},
                 {"id": "off", "name": "Phone", "host": "127.0.0.1", "serve_port": 1, "token": "x"},
                 {"id": "slow", "name": "Slowpoke", "host": "127.0.0.1", "serve_port": slow.port, "token": "ts"}]
        t0 = asyncio.get_running_loop().time()
        async with aiohttp.ClientSession() as sess:
            out = await cc.zoo.fan_out(sess, {}, seats, "b", timeout_s=0.5)
        took = asyncio.get_running_loop().time() - t0
        for s in (a, b, slow):
            await s.close()
        return out, took
    out, took = _run(go())
    assert took < 2.0                                     # in parallel, each capped
    by = {r["id"]: r for r in out}
    assert [r["id"] for r in out] == ["a", "b", "off", "slow"]
    assert by["a"]["online"] is True and by["a"]["name"] == "Pip"       # no name set: the card's
    assert by["a"]["card"] == {"name": "Pip", "level": 3, "rung_name": "take turns", "mode": "watch",
                               "state": "awake", "last_activity": "2025-10-09T08:53:20Z"}
    assert by["b"]["active"] is True and by["a"]["active"] is False
    assert by["b"]["name"] == "Boat"                      # the operator's name wins
    assert by["b"]["card"]["state"] == "asleep" and by["b"]["card"]["level"] == 6
    assert by["b"]["card"]["name"] == "Blurp" and by["b"]["card"]["last_activity"] == 1760000123.5
    assert by["off"]["online"] is False and "card" not in by["off"] and by["off"]["error"]
    assert by["slow"]["online"] is False and by["slow"]["error"] == "timeout"
    assert by["off"]["host"] == "127.0.0.1"


def test_a_connected_seat_is_called_by_its_own_name(cc):
    # issue #6: HA connected to a named seat said "Bob": the name comes from the
    # seat itself (its /ui/conf); until it answers, or when it serves none
    # (an older seat), the seat's own default
    import aiohttp
    from aiohttp import test_utils

    async def go():
        named = test_utils.TestServer(_seat_app("tn", conf={"name": "Memo", "aliases": ["memmo"]}))
        plain = test_utils.TestServer(_seat_app("tp"))
        for s in (named, plain):
            await s.start_server()
        out = []
        async with aiohttp.ClientSession() as sess:
            for srv, tok in ((named, "tn"), (plain, "tp")):
                comp = {}
                cc.zoo.apply(comp, {"id": "x", "name": "", "host": "127.0.0.1", "serve_port": srv.port, "token": tok})
                before = (comp["name"], comp["aliases"])
                await cc.zoo.fetch_name(sess, comp)
                out.append((before, (comp["name"], comp["aliases"])))
        for s in (named, plain):
            await s.close()
        return out
    (b1, a1), (b2, a2) = _run(go())
    assert b1 == b2 == a2 == (cc.const.DEFAULT_NAME, [])
    assert a1 == ("Memo", ["memmo"])


def test_the_zoo_views(cc):
    hass, entry, comp = _house(cc)
    called = {}

    async def fake_fan_out(sess, c, seats, active, timeout_s=cc.zoo.CARD_TIMEOUT_S):
        called["seats"], called["active"] = seats, active
        return [{"id": s["id"], "active": s["id"] == active} for s in seats]

    class Req(dict):
        def __init__(self, user, body=None):
            super().__init__(hass_user=user)
            self.app = {KEY_HASS: hass}
            self._body = body

        async def json(self):
            return self._body
    orig = cc.zoo.fan_out
    cc.zoo.fan_out = fake_fan_out
    try:
        r = _run(cc.views.ZooView().get(Req(types.SimpleNamespace(id="u", is_admin=False))))
    finally:
        cc.zoo.fan_out = orig
    assert r.status == 200 and called["active"] == "laptop" and len(json.loads(r.body)) == 2
    assert cc.views.ZooView.url == "/api/membrane/zoo" and cc.views.ZooView.requires_auth
    v = cc.views.ZooActiveView()
    assert v.url == "/api/membrane/zoo/active" and v.requires_auth
    r = _run(v.post(Req(types.SimpleNamespace(id="u", is_admin=False), {"id": "phone"})))
    assert r.status == 403 and comp["active_seat"] == "laptop"
    r = _run(v.post(Req(types.SimpleNamespace(id="u", is_admin=True), {"id": "phone"})))
    assert r.status == 200 and comp["active_seat"] == "phone"
    r = _run(v.post(Req(types.SimpleNamespace(id="u", is_admin=True), {"id": "ghost"})))
    assert r.status == 404


def test_the_zoo_views_are_registered_and_rewritten(cc):
    init = (CC / "__init__.py").read_text()
    assert "ZooView()" in init and "ZooActiveView()" in init
    assert (b'"/ui/zoo', b'"/api/membrane/zoo') in cc.views.REWRITE


# ---- the friendly offline error --------------------------------------------------------

def test_an_unreachable_seat_answers_in_words(cc):
    import aiohttp
    comp = {}
    cc.zoo.apply(comp, PHONE)
    err = aiohttp.ClientConnectionError("Cannot connect to host 192.168.1.112:4096")
    r = cc.relay.offline_response(comp, err)
    assert r.status == 502 and r.content_type == "application/json"
    body = json.loads(r.body)
    assert body["error"] == "seat_offline" and body["seat"] == "Phone" and body["seat_id"] == "phone"
    assert body["message"] == "Phone is offline"
    assert "ClientConnect" not in body["message"]


def test_the_card_is_a_read_only_relay_path(cc):
    assert cc.relay.allowed_path("membrane/self/card")
    assert cc.relay.read_only_path("membrane/self/card")      # GET only, like the sheet


def test_a_card_says_asleep_in_its_own_word(cc):
    c = cc.zoo.card_of({"name": "Pip", "level": 2, "rung_name": "r", "mode": "watch", "awake": False,
                        "last_activity": None, "generated": 1.0})
    assert c["state"] == "asleep" and c["last_activity"] is None
    assert cc.zoo.card_of({"header": {"mode": "off"}})["state"] == "asleep"     # the sheet: by its mode
    assert cc.zoo.card_of({"header": {"mode": "on"}})["state"] == "awake"


def test_the_relay_uses_it_for_unreachable_and_timeouts():
    relay = (CC / "relay.py").read_text()
    up = relay.split("async def _upstream", 1)[1].split("async def _stream_events", 1)[0]
    assert "offline_response(comp, e)" in up and "serve unreachable" not in up
    assert "TimeoutError" in up


# ---- the options flow and its words ----------------------------------------------------

def test_the_options_flow_edits_the_seat_list():
    flow = (CC / "config_flow.py").read_text()
    for step in ("async_step_settings", "async_step_add_seat", "async_step_pick_seat",
                 "async_step_edit_seat", "async_step_remove_seat"):
        assert step in flow
    for fn in (("strings.json",), ("translations", "en.json")):
        s = json.loads((CC.joinpath(*fn)).read_text())
        o = s["options"]["step"]
        assert set(o["init"]["menu_options"]) >= {"settings", "add_seat", "pick_seat", "remove_seat"}
        assert {"host", "serve_port", "proxy_port", "token", "name"} <= set(o["add_seat"]["data"])
        assert "membrane token seat" in o["add_seat"]["description"]


def test_a_new_seat_from_the_form(cc):
    s = cc.zoo.seat_from_form({"host": " 192.168.1.107 ", "serve_port": 4096, "proxy_port": 11500,
                               "token": "t", "name": ""}, [])
    assert s == {"id": "192-168-1-107-4096", "name": "", "host": "192.168.1.107", "serve_port": 4096,
                 "proxy_port": 11500, "token": "t"}
    assert cc.zoo.seat_url(s) == "http://192.168.1.107:4096"
    # the same host and port again: a fresh id, never a clash
    s2 = cc.zoo.seat_from_form({"host": "192.168.1.107", "serve_port": 4096}, [s])
    assert s2["id"] != s["id"]


def test_removing_the_active_seat_moves_the_house(cc):
    opts = {"seats": [dict(LAPTOP), dict(PHONE)], "active": "phone"}
    out = cc.zoo.remove(opts, "phone")
    assert [s["id"] for s in out["seats"]] == ["laptop"] and out["active"] == "laptop"
    assert cc.zoo.remove(out, "laptop") == out          # never the last one
    inside = {"seats": [{"id": "inside", "name": "this Home Assistant", "inside": True}, dict(LAPTOP)],
              "active": "laptop"}
    assert cc.zoo.remove(inside, "inside") == inside     # HA's own child stays


# ---- the page (node) ----------------------------------------------------------------

def _js(file, expr):
    js = 'const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / file), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


@NODE
class TestZooCards(unittest.TestCase):
    def _card(self, row, now=1760000600):
        return _js("home.js", "L.zooCard(%s, %s)" % (json.dumps(row), now))

    def test_an_online_seat(self):
        h = self._card({"id": "laptop", "name": "Laptop", "host": "192.168.1.107", "active": False, "online": True,
                        "card": {"name": "Pip", "level": 3, "rung_name": "take turns", "mode": "on",
                                 "state": "awake", "last_activity": 1760000300}})
        for s in ("Pip", "Lv 3", "take turns", "awake", "Laptop", 'data-id="laptop"', "dot on", "5 min ago"):
            self.assertIn(s, h)
        self.assertNotIn("active", h.split('class="', 1)[1].split('"', 1)[0])

    def test_the_active_seat_is_marked(self):
        h = self._card({"id": "a", "name": "A", "host": "h", "active": True, "online": True,
                        "card": {"name": "Blurp", "level": 0, "rung_name": "be", "state": "asleep"}})
        self.assertIn("zcard active", h)
        self.assertIn("Lv 0", h)
        self.assertIn("asleep", h)
        self.assertIn('aria-current="true"', h)

    def test_an_offline_seat(self):
        h = self._card({"id": "p", "name": "Phone", "host": "192.168.1.112", "active": False, "online": False,
                        "error": "timeout"})
        self.assertIn("dot off", h)
        self.assertIn("offline", h)
        self.assertIn("Phone", h)
        self.assertNotIn("Lv", h)

    def test_it_escapes(self):
        h = self._card({"id": "x\"", "name": "<b>", "host": "h", "online": True,
                        "card": {"name": "<i>", "level": 1, "rung_name": "&", "state": "awake"}})
        self.assertNotIn("<b>", h)
        self.assertNotIn("<i>", h)
        self.assertIn("&lt;b&gt;", h)

    def test_the_list(self):
        h = _js("home.js", "L.zooCards([])")
        self.assertIn("no seats", h)

    def test_an_iso_last_activity(self):
        self.assertEqual(_js("home.js", "L.zooAgo('2025-10-09T08:53:20Z', 1760000000 + 600)"), "10 min ago")

    def test_last_seen(self):
        self.assertEqual(_js("home.js", "L.zooAgo(1000, 1030)"), "just now")
        self.assertEqual(_js("home.js", "L.zooAgo(1000, 1000 + 7200)"), "2 h ago")
        self.assertEqual(_js("home.js", "L.zooAgo(null, 1000)"), "")


@NODE
class TestOfflineNotice(unittest.TestCase):
    ERR = {"error": "seat_offline", "seat": "Phone", "seat_id": "phone", "message": "Phone is offline"}

    def test_offers_an_online_seat(self):
        zoo = [{"id": "phone", "name": "Phone", "online": False, "active": True},
               {"id": "laptop", "name": "Laptop", "online": True, "active": False}]
        n = _js("panel.js", "L.offlineNotice(%s, %s)" % (json.dumps(self.ERR), json.dumps(zoo)))
        self.assertEqual(n["text"], "Phone is offline — switch to Laptop?")
        self.assertEqual(n["switchTo"], {"id": "laptop", "name": "Laptop"})

    def test_nothing_to_switch_to(self):
        zoo = [{"id": "phone", "name": "Phone", "online": False, "active": True},
               {"id": "x", "name": "X", "online": False, "active": False}]
        n = _js("panel.js", "L.offlineNotice(%s, %s)" % (json.dumps(self.ERR), json.dumps(zoo)))
        self.assertEqual(n["text"], "Phone is offline")
        self.assertIsNone(n["switchTo"])

    def test_not_an_offline_error(self):
        self.assertIsNone(_js("panel.js", "L.offlineNotice('serve error', [])"))
