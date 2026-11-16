"""The add-on's Supervisor discovery (#1143): the add-on announces its seat to Home Assistant as service `membrane` with config {host, port, token}
(host: the add-on's name on the Supervisor network; token: the seat's admin token). `async_step_hassio` builds http://host:port, checks the seat's
health with the token, and asks the user to confirm (Home Assistant's rule: a discovery never creates an entry by itself). One entry per Home
Assistant, keyed by the seat (unique id host:port): a later announcement of the same seat stores its new token and reloads the entry (no manual
step), the same for an entry that names the seat's address, and an entry that ran the seat inside Home Assistant (no address yet) is given the
add-on's seat. A fake Home Assistant and a fake seat on loopback."""
import importlib

import aiohttp
import pytest

from . import fakeha
from .fakeseat import FakeSeat


def _flow(hass):
    f = importlib.import_module("cc_membrane.config_flow").MembraneConfigFlow()
    f.hass, f.context = hass, {"source": "hassio"}
    return f


def _info(port, token, host="127.0.0.1"):
    return fakeha.HassioServiceInfo({"host": host, "port": port, "token": token})


def _with_seat(run, hass, token, body):
    async def go():
        async with FakeSeat(token) as seat, aiohttp.ClientSession() as s:
            hass.session = s
            return await body(seat)
    return run(go())


def _flow_result(coro_fn):
    """The flow's answer, an abort raised by HA's helpers turned into the abort result HA shows."""
    async def go():
        try:
            return await coro_fn()
        except fakeha.AbortFlow as e:
            return {"type": "abort", "reason": e.reason}
    return go()


def test_a_discovered_seat_is_checked_then_confirmed_then_made_an_entry(ha, run):
    pkg, hass = ha

    async def body(seat):
        f = _flow(hass)
        form = await f.async_step_hassio(_info(seat.port, "tok-1"))
        assert form["type"] == "form" and form["step_id"] == "hassio_confirm", form
        assert form["data_schema"] is None, "a confirmation: no fields"
        assert form["description_placeholders"] == {"addon": "babyseat"}
        assert [p for _, p, _, _ in seat.seen] == ["/global/health"], "the seat's health is checked with the announced token"
        done = await f.async_step_hassio_confirm({})
        return seat, f, done
    seat, f, done = _with_seat(run, hass, "tok-1", body)
    assert done["type"] == "create_entry"
    assert done["data"] == {"serve_url": seat.url, "serve_user": "opencode", "serve_pass": "tok-1"}
    assert f.unique_id == f"127.0.0.1:{seat.port}"


def test_a_discovery_whose_token_or_address_fails_is_aborted_with_the_reason(ha, run):
    pkg, hass = ha

    async def body(seat):
        bad = await _flow_result(lambda: _flow(hass).async_step_hassio(_info(seat.port, "nope")))
        dead = await _flow_result(lambda: _flow(hass).async_step_hassio(_info(1, "x")))
        empty = await _flow_result(lambda: _flow(hass).async_step_hassio(fakeha.HassioServiceInfo({"host": "", "port": 4096, "token": "t"})))
        notoken = await _flow_result(lambda: _flow(hass).async_step_hassio(fakeha.HassioServiceInfo({"host": "h", "port": 4096})))
        return bad, dead, empty, notoken
    bad, dead, empty, notoken = _with_seat(run, hass, "tok-1", body)
    assert bad == {"type": "abort", "reason": "invalid_auth"}
    assert dead == {"type": "abort", "reason": "cannot_connect"}
    assert empty == notoken == {"type": "abort", "reason": "invalid_discovery"}
    assert hass.config_entries.entries == []


def test_the_port_defaults_to_4096_when_the_announcement_leaves_it_out(ha, run):
    pkg, hass = ha
    f = _flow(hass)
    checked = []

    async def check(url, token, user="opencode"):        # no real address is contacted: a seat may listen on 4096 here
        checked.append(url)
        return "cannot_connect"
    f._check = check
    res = run(_flow_result(lambda: f.async_step_hassio(fakeha.HassioServiceInfo({"host": "abc-babyseat", "token": "t"}))))
    assert f.unique_id == "abc-babyseat:4096" and checked == ["http://abc-babyseat:4096"] and res["reason"] == "cannot_connect"


def test_a_second_announcement_of_the_same_seat_updates_the_token_and_reloads_no_duplicate(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(data={"serve_url": "http://127.0.0.1:4096", "serve_user": "opencode", "serve_pass": "old"},
                               unique_id="127.0.0.1:4096")
    e.loaded = False                                    # its token was refused: the setup failed (HA reloads such an entry only when told to)
    hass.config_entries.entries.append(e)
    res = run(_flow_result(lambda: _flow(hass).async_step_hassio(_info(4096, "new"))))
    assert res == {"type": "abort", "reason": "already_configured"}
    assert e.data == {"serve_url": "http://127.0.0.1:4096", "serve_user": "opencode", "serve_pass": "new"}
    assert hass.config_entries.reloaded == "e1", "the entry whose token was refused is set up again with the new token"
    assert len(hass.config_entries.entries) == 1


def test_an_announcement_that_changes_nothing_is_quiet(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(data={"serve_url": "http://127.0.0.1:4096", "serve_user": "opencode", "serve_pass": "same"},
                               unique_id="127.0.0.1:4096")
    hass.config_entries.entries.append(e)
    res = run(_flow_result(lambda: _flow(hass).async_step_hassio(_info(4096, "same"))))
    assert res["reason"] == "already_configured"
    assert hass.config_entries.updated == [] and hass.config_entries.reloaded is None


def test_an_entry_typed_by_hand_with_the_seats_address_is_repaired_by_the_announcement(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(data={"serve_url": "http://127.0.0.1:4096", "serve_user": "opencode", "serve_pass": "refused"})   # unique id "membrane"
    hass.config_entries.entries.append(e)
    res = run(_flow_result(lambda: _flow(hass).async_step_hassio(_info(4096, "fresh"))))
    assert res == {"type": "abort", "reason": "already_configured"}
    assert e.data["serve_pass"] == "fresh" and e.unique_id == "127.0.0.1:4096"
    assert hass.config_entries.reloaded == "e1"


def test_an_entry_that_ran_the_seat_inside_home_assistant_is_given_the_add_ons_seat(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(data={"serve_url": "", "serve_user": "opencode", "serve_pass": ""})
    hass.config_entries.entries.append(e)
    res = run(_flow_result(lambda: _flow(hass).async_step_hassio(_info(4096, "tok", host="abc-babyseat"))))
    assert res == {"type": "abort", "reason": "already_configured"}
    assert e.data == {"serve_url": "http://abc-babyseat:4096", "serve_user": "opencode", "serve_pass": "tok"}
    assert e.unique_id == "abc-babyseat:4096" and hass.config_entries.reloaded == "e1"


def test_an_entry_for_another_seat_is_left_alone(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(data={"serve_url": "http://192.168.1.20:4096", "serve_user": "opencode", "serve_pass": "mine"})
    hass.config_entries.entries.append(e)
    res = run(_flow_result(lambda: _flow(hass).async_step_hassio(_info(4096, "tok", host="abc-babyseat"))))
    assert res == {"type": "abort", "reason": "already_configured"}
    assert e.data["serve_pass"] == "mine" and e.unique_id == "membrane" and hass.config_entries.reloaded is None


def test_the_form_aborts_when_the_discovery_already_made_the_entry(ha, run):
    pkg, hass = ha
    hass.config_entries.entries.append(fakeha.FakeConfigEntry(
        data={"serve_url": "http://abc-babyseat:4096", "serve_user": "opencode", "serve_pass": "t"}, unique_id="abc-babyseat:4096"))
    f = importlib.import_module("cc_membrane.config_flow").MembraneConfigFlow()
    f.hass = hass
    res = run(_flow_result(lambda: f.async_step_user()))
    assert res == {"type": "abort", "reason": "already_configured"}


def test_a_confirm_left_open_while_the_seat_was_set_up_by_hand_aborts(ha, run):
    """Real Home Assistant 2026.11 (scripts/ha_core_smoke.py) made a SECOND entry here: the discovery's confirm was open, the person finished the
    manual form meanwhile (another unique id: nothing in Home Assistant stops it), then confirmed. One entry per Home Assistant: the confirm checks again."""
    pkg, hass = ha

    async def body(seat):
        f = _flow(hass)
        form = await f.async_step_hassio(_info(seat.port, "tok-1"))
        hass.config_entries.entries.append(fakeha.FakeConfigEntry(data={"serve_url": seat.url, "serve_user": "opencode", "serve_pass": "tok-1"}))
        return form, await _flow_result(lambda: f.async_step_hassio_confirm({}))
    form, late = _with_seat(run, hass, "tok-1", body)
    assert form["step_id"] == "hassio_confirm"
    assert late == {"type": "abort", "reason": "already_configured"}


def test_a_second_announcement_while_the_confirm_is_open_is_already_in_progress(ha, run):
    """Real Home Assistant (scripts/ha_core_smoke.py): a flow that sets the unique id of a flow still open (not a reauth) aborts `already_in_progress`,
    so the add-on announcing again while the person has not confirmed yet makes no second confirm. The fake did not model flows in progress."""
    pkg, hass = ha

    async def body(seat):
        first = await _flow(hass).async_step_hassio(_info(seat.port, "tok-1"))
        second = await _flow_result(lambda: _flow(hass).async_step_hassio(_info(seat.port, "tok-1")))
        return first, second
    first, second = _with_seat(run, hass, "tok-1", body)
    assert first["step_id"] == "hassio_confirm"
    assert second == {"type": "abort", "reason": "already_in_progress"}


def test_an_ignored_seat_stays_ignored_and_the_manual_form_is_still_offered(ha, run):
    """The person pressed Ignore on the add-on's discovery: Home Assistant keeps an entry of source `ignore` with the seat's unique id. Real Home
    Assistant (scripts/ha_core_smoke.py): a later announcement aborts and leaves it alone; the manual form does not count it. The fake had no
    `source` and returned ignored entries whatever `include_ignore` said."""
    pkg, hass = ha
    ign = fakeha.FakeConfigEntry(data={}, unique_id="127.0.0.1:4096", source="ignore")
    hass.config_entries.entries.append(ign)
    res = run(_flow_result(lambda: _flow(hass).async_step_hassio(_info(4096, "tok"))))
    assert res == {"type": "abort", "reason": "already_configured"}
    assert ign.data == {} and hass.config_entries.updated == [] and hass.config_entries.reloaded is None
    f = importlib.import_module("cc_membrane.config_flow").MembraneConfigFlow()
    f.hass, f.context = hass, {"source": "user"}
    form = run(_flow_result(lambda: f.async_step_user()))
    assert form["type"] == "menu" and form["step_id"] == "user"
    form = run(_flow_result(lambda: f.async_step_connect()))
    assert form["type"] == "form" and form["step_id"] == "connect"


def test_the_manifest_needs_no_discovery_key_for_the_supervisor():
    """Home Assistant's docs (2026-11-15, contributors.home-assistant.io: config flow "hassio" step, apps "Share services and discover
    integrations"): the Supervisor discovery needs only the `hassio` step; no manifest matcher and no dependency is named for it."""
    import json
    from pathlib import Path
    man = json.loads((Path(__file__).resolve().parents[2] / "custom_components" / "membrane" / "manifest.json").read_text())
    assert "hassio" not in man and "hassio" not in man["dependencies"]


@pytest.mark.parametrize("key", ["cannot_connect", "invalid_auth", "invalid_discovery", "already_configured"])
def test_every_abort_the_discovery_can_show_has_words(key):
    import json
    from pathlib import Path
    s = json.loads((Path(__file__).resolve().parents[2] / "custom_components" / "membrane" / "strings.json").read_text())
    assert s["config"]["abort"][key]
    assert "{addon}" in s["config"]["step"]["hassio_confirm"]["description"]
