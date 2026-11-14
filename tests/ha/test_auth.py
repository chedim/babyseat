"""R2 (#1143): the token. The entry keeps the seat's URL and the token (in `serve_pass`, sent as the Basic password: cliffvm reads it as the
JWT); the setup checks the seat's health with it; a 401 (the token's end of life) starts Home Assistant's reauth, whose form takes a new
token. A fake Home Assistant and a fake seat on loopback."""
import base64

import aiohttp
import pytest

from . import fakeha
from .fakeseat import FakeSeat


def _entry(seat, token, **kw):
    e = fakeha.FakeConfigEntry(data={"serve_url": seat.url, "serve_user": "opencode", "serve_pass": token}, **kw)
    return e


def _setup(pkg, hass, entry):
    hass.config_entries.entries.append(entry)
    return pkg.async_setup_entry(hass, entry)


def _with_seat(run, hass, token, body):
    async def go():
        async with FakeSeat(token) as seat, aiohttp.ClientSession() as s:
            hass.session = s
            return await body(seat)
    return run(go())


def test_setup_checks_the_health_with_the_token_as_the_basic_password_and_loads_assist(ha, run):
    pkg, hass = ha

    async def body(seat):
        e = _entry(seat, "tok-1")
        assert await _setup(pkg, hass, e) is True
        method, path, headers, _ = seat.seen[0]
        assert (method, path) == ("GET", "/global/health")
        user, _, pw = base64.b64decode(headers["Authorization"][6:]).decode().partition(":")
        assert (user, pw) == ("opencode", "tok-1")
        return e
    e = _with_seat(run, hass, "tok-1", body)
    assert hass.config_entries.forwarded == [["conversation"]]
    assert e.runtime_data.client.url.startswith("http://127.0.0.1")


def test_an_expired_or_wrong_token_at_setup_is_reauth_not_a_retry(ha, run):
    pkg, hass = ha

    async def body(seat):
        with pytest.raises(fakeha.ConfigEntryAuthFailed):
            await _setup(pkg, hass, _entry(seat, "tok-OLD"))
    _with_seat(run, hass, "tok-NEW", body)


def test_a_seat_that_is_down_is_a_retry_not_a_reauth(ha, run):
    pkg, hass = ha

    async def body(seat):
        seat.status_override = 503
        with pytest.raises(fakeha.ConfigEntryNotReady):
            await _setup(pkg, hass, _entry(seat, "tok-1"))
    _with_seat(run, hass, "tok-1", body)


def test_an_entry_without_a_seat_address_or_token_asks_for_them_again(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(data={"serve_url": "", "serve_user": "opencode", "serve_pass": ""})
    with pytest.raises(fakeha.ConfigEntryAuthFailed):
        run(pkg.async_setup_entry(hass, e))


def test_the_health_poll_runs_every_sixty_seconds(ha, run):
    pkg, hass = ha

    async def body(seat):
        e = _entry(seat, "tok-1")
        await _setup(pkg, hass, e)
        return e.runtime_data.health.update_interval.total_seconds()
    assert _with_seat(run, hass, "tok-1", body) == 60


def test_a_token_that_expires_while_running_starts_the_reauth_on_the_next_poll(ha, run):
    pkg, hass = ha

    async def body(seat):
        e = _entry(seat, "tok-1")
        await _setup(pkg, hass, e)
        assert e.reauth_started == 0
        seat.token = "rotated"                         # the seat's key changed (or the token's life ended)
        await e.runtime_data.health.async_refresh()
        return e
    e = _with_seat(run, hass, "tok-1", body)
    assert e.reauth_started == 1


def _reauth_flow(pkg, hass, entry):
    import importlib
    f = importlib.import_module("cc_membrane.config_flow").MembraneConfigFlow()
    f.hass, f.context = hass, {"entry": entry}
    return f


def test_the_reauth_form_takes_a_token_and_a_good_one_updates_the_entry(ha, run):
    pkg, hass = ha

    async def body(seat):
        e = _entry(seat, "tok-OLD")
        f = _reauth_flow(pkg, hass, e)
        form = await f.async_step_reauth(dict(e.data))
        assert form["step_id"] == "reauth_confirm" and set(form["data_schema"].fields()) == {"serve_pass"}
        bad = await f.async_step_reauth_confirm({"serve_pass": "still-wrong"})
        assert bad["errors"] == {"base": "invalid_auth"} and e.data["serve_pass"] == "tok-OLD"
        done = await f.async_step_reauth_confirm({"serve_pass": "tok-NEW"})
        assert done == {"type": "abort", "reason": "reauth_successful"}
        return e, seat
    e, seat = _with_seat(run, hass, "tok-NEW", body)
    assert e.data == {"serve_url": seat.url, "serve_user": "opencode", "serve_pass": "tok-NEW"}


def test_the_reauth_of_an_entry_with_no_address_asks_for_the_address_too(ha, run):
    pkg, hass = ha

    async def body(seat):
        e = fakeha.FakeConfigEntry(data={"serve_url": "", "serve_user": "opencode", "serve_pass": ""})
        f = _reauth_flow(pkg, hass, e)
        form = await f.async_step_reauth_confirm()
        assert set(form["data_schema"].fields()) == {"serve_url", "serve_pass"}
        await f.async_step_reauth_confirm({"serve_url": seat.url + "/", "serve_pass": "tok-1"})
        return e, seat
    e, seat = _with_seat(run, hass, "tok-1", body)
    assert e.data["serve_url"] == seat.url and e.data["serve_pass"] == "tok-1"
