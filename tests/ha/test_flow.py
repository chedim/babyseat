"""R1 (#1143): the connect-only config flow, the migration of entries made by the old component, the manifest. A fake Home Assistant
(fakeha.py) and a fake seat on loopback (fakeseat.py); nothing else is contacted."""
import asyncio
import importlib
import json
import sys
from pathlib import Path

import aiohttp

from . import fakeha
from .fakeseat import FakeSeat

CC = Path(__file__).resolve().parents[2] / "custom_components" / "membrane"


def _flow(ha, hass):
    mod = importlib.import_module("cc_membrane.config_flow")
    f = mod.MembraneConfigFlow()
    f.hass = hass
    return mod, f


def _fields(form):
    return set(form["data_schema"].fields())


def test_the_manifest_needs_no_pip_requirement_and_is_the_release_version():
    man = json.loads((CC / "manifest.json").read_text())
    assert man["requirements"] == []
    assert man["version"] == "0.7.60"
    assert man["domain"] == "membrane"
    assert "http" not in man["dependencies"] and "frontend" not in man["dependencies"]   # no panels in slice 1


def test_the_first_step_is_a_form_for_the_url_and_the_token_no_menu(ha, run):
    pkg, hass = ha
    _, f = _flow(pkg, hass)
    form = run(f.async_step_user())
    assert form["type"] == "form" and form["step_id"] == "user"
    assert _fields(form) == {"serve_url", "serve_pass"}


def test_a_good_url_and_token_make_the_entry_with_the_token_in_serve_pass(ha, run):
    pkg, hass = ha

    async def go():
        async with FakeSeat("tok-1") as seat, aiohttp.ClientSession() as s:
            hass.session = s
            _, f = _flow(pkg, hass)
            return seat, await f.async_step_user({"serve_url": seat.url + "/", "serve_pass": "tok-1"})
    seat, res = run(go())
    assert res["type"] == "create_entry"
    assert res["data"] == {"serve_url": seat.url, "serve_user": "opencode", "serve_pass": "tok-1"}


def test_a_wrong_token_is_invalid_auth_and_a_dead_address_cannot_connect(ha, run):
    pkg, hass = ha

    async def go():
        async with FakeSeat("tok-1") as seat, aiohttp.ClientSession() as s:
            hass.session = s
            _, f = _flow(pkg, hass)
            bad = await f.async_step_user({"serve_url": seat.url, "serve_pass": "nope"})
            dead = await f.async_step_user({"serve_url": "http://127.0.0.1:1", "serve_pass": "x"})
            notaurl = await f.async_step_user({"serve_url": "seat.local", "serve_pass": "x"})
            return bad, dead, notaurl
    bad, dead, notaurl = run(go())
    assert bad["type"] == "form" and bad["errors"] == {"base": "invalid_auth"}
    assert dead["errors"] == {"base": "cannot_connect"}
    assert notaurl["errors"] == {"serve_url": "invalid_url"}


def test_one_entry_per_home_assistant(ha, run):
    pkg, hass = ha
    hass.config_entries.entries.append(fakeha.FakeConfigEntry())
    _, f = _flow(pkg, hass)
    try:
        run(f.async_step_user())
        raise AssertionError("no abort")
    except fakeha.AbortFlow as e:
        assert e.reason == "already_configured"


def test_the_flow_is_version_1_minor_3(ha):
    pkg, hass = ha
    mod, _ = _flow(pkg, hass)
    assert (mod.MembraneConfigFlow.VERSION, mod.MembraneConfigFlow.MINOR_VERSION) == (1, 3)


# ---- async_migrate_entry: the entries the old component made ----------------------------------------------------------------

def _migrate(pkg, hass, entry):
    hass.config_entries.entries.append(entry)
    assert asyncio.run(pkg.async_migrate_entry(hass, entry)) is True
    return entry


def test_a_connect_entry_keeps_its_url_and_password_and_loses_what_has_no_place_now(ha):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(minor_version=2,
                               data={"mode": "connect", "serve_url": "http://seat:4096", "serve_user": "opencode", "serve_pass": "old-seat-token"},
                               options={"landing": True, "stt_url": "http://w", "loops": 2, "ha_access": True,
                                        "seats": [{"id": "seat-4096", "host": "seat", "serve_port": 4096, "token": "old-seat-token", "url": "http://seat:4096"}],
                                        "active": "seat-4096"})
    _migrate(pkg, hass, e)
    assert (e.version, e.minor_version) == (1, 3)
    assert e.data == {"serve_url": "http://seat:4096", "serve_user": "opencode", "serve_pass": "old-seat-token"}
    assert e.options == {}


def test_the_active_seat_of_the_zoo_is_the_one_kept(ha):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(minor_version=2,
                               data={"mode": "connect", "serve_url": "http://a:4096", "serve_user": "opencode", "serve_pass": "ta"},
                               options={"seats": [{"id": "a", "host": "a", "serve_port": 4096, "token": "ta", "url": "http://a:4096"},
                                                  {"id": "b", "host": "b.lan", "serve_port": 4097, "token": "tb", "user": "u2"}],
                                        "active": "b"})
    _migrate(pkg, hass, e)
    assert e.data == {"serve_url": "http://b.lan:4097", "serve_user": "u2", "serve_pass": "tb"}


def test_an_inside_entry_has_no_seat_to_connect_to_and_is_left_for_reauth(ha):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(minor_version=2, data={"mode": "inside", "backend": "http://ollama:11434", "model": "m", "embed_model": "e"},
                               options={"seats": [{"id": "inside", "inside": True}], "active": "inside"})
    _migrate(pkg, hass, e)
    assert e.data == {"serve_url": "", "serve_user": "opencode", "serve_pass": ""}
    assert e.minor_version == 3


def test_a_migrated_entry_is_not_migrated_twice(ha):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(minor_version=3, data={"serve_url": "http://x", "serve_user": "opencode", "serve_pass": "t"})
    _migrate(pkg, hass, e)
    assert hass.config_entries.updated == []
