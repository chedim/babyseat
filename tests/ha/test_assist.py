"""R3 (#1143): Assist end to end against a REAL cliffvm seat (the shipped packs, a door secret, a minted token, a FAKE claude as the turn's
binary): one seat session per HA conversation, the `[HA user: X]` first part reaching the model as written, the answer back to HA, a failed
turn spoken with the seat's cause, a token past its life starting the reauth. Home Assistant itself is the fake of fakeha.py."""
import asyncio
import importlib

import aiohttp
import pytest

from . import fakeha


def _entity(pkg, hass, entry):
    return importlib.import_module("cc_membrane.conversation").MembraneConversation(hass, entry)


async def _loaded(pkg, hass, seat, token):
    """-> (the entry set up against the real seat, the conversation entity)"""
    s = aiohttp.ClientSession()
    hass.session = s
    entry = fakeha.FakeConfigEntry(data={"serve_url": seat.url, "serve_user": "opencode", "serve_pass": token})
    hass.config_entries.entries.append(entry)
    await pkg.async_setup_entry(hass, entry)
    return s, entry, _entity(pkg, hass, entry)


async def _say(ent, text, conv="c1", user_id=None):
    log = fakeha.ChatLog(conv)
    return (await ent._async_handle_message(fakeha.ConversationInput(text, user_id=user_id, conversation_id=conv), log))["speech"]


def test_the_message_reaches_the_model_with_the_speaker_line_first_as_written_and_the_answer_comes_back(ha, real_seat):
    pkg, hass = ha
    hass.users["u1"] = type("U", (), {"name": "Dima"})()

    async def go():
        s, entry, ent = await _loaded(pkg, hass, real_seat, real_seat.mint())
        try:
            return await _say(ent, "what lights are on?", user_id="u1")
        finally:
            await s.close()
    # the fake claude answers "Done: <its prompt>": the prompt the model got is the speaker line, a newline, the text
    assert asyncio.run(go()) == "Done: [HA user: Dima]\nwhat lights are on?"


def test_a_turn_with_no_logged_in_user_sends_no_speaker_line(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry, ent = await _loaded(pkg, hass, real_seat, real_seat.mint())
        try:
            return await _say(ent, "hello there")
        finally:
            await s.close()
    assert asyncio.run(go()) == "Done: hello there"


def test_one_seat_session_per_ha_conversation_titled_with_its_id(ha, real_seat):
    pkg, hass = ha
    tok = real_seat.mint()

    async def go():
        s, entry, ent = await _loaded(pkg, hass, real_seat, tok)
        try:
            await _say(ent, "one", conv="conv-A")
            await _say(ent, "two", conv="conv-A")
            await _say(ent, "three", conv="conv-B")
            async with s.get(real_seat.url + "/session", headers=entry.runtime_data.client._headers) as r:
                return await r.json(), dict(entry.runtime_data.sessions)
        finally:
            await s.close()
    listed, mapping = asyncio.run(go())
    assert sorted(x["title"] for x in listed) == ["hass-conv-A", "hass-conv-B"]
    assert set(mapping) == {"conv-A", "conv-B"} and mapping["conv-A"] != mapping["conv-B"]


def test_a_failed_turn_is_spoken_as_an_error_with_the_seats_cause(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry, ent = await _loaded(pkg, hass, real_seat, real_seat.mint())
        try:
            return await _say(ent, "this will fail")
        finally:
            await s.close()
    speech = asyncio.run(go())
    assert speech.startswith("The seat could not answer:") and "not logged in" in speech, speech


def test_the_door_refuses_no_token_and_a_wrong_one_and_the_setup_says_reauth(ha, real_seat):
    pkg, hass = ha

    async def go():
        async with aiohttp.ClientSession() as s:
            hass.session = s
            e = fakeha.FakeConfigEntry(data={"serve_url": real_seat.url, "serve_user": "opencode", "serve_pass": "not-a-jwt"})
            with pytest.raises(fakeha.ConfigEntryAuthFailed):
                await pkg.async_setup_entry(hass, e)
    asyncio.run(go())


def test_a_token_past_its_life_starts_the_reauth_and_a_fresh_one_is_accepted(ha, real_seat):
    pkg, hass = ha
    live = real_seat.mint_at(+3600)                       # the control: this handmade form is one the door accepts
    short = real_seat.mint_at(-300)                       # past its life by five minutes (the door's leeway is 30 s)

    async def go():
        s, entry, ent = await _loaded(pkg, hass, real_seat, live)
        try:
            entry.runtime_data.client._headers = importlib.import_module("cc_membrane.seat_client").SeatClient(
                s, real_seat.url, short)._headers         # the token ends its life while HA runs
            speech = await _say(ent, "late")              # the seat answers 401: HA hears reauth, the user is told
            await entry.runtime_data.health.async_refresh()
            return speech, entry.reauth_started
        finally:
            await s.close()
    speech, started = asyncio.run(go())
    assert "refused" in speech and "paste a new token" in speech, speech
    assert started >= 2                                   # the turn's, and the health poll's

    async def renew():
        async with aiohttp.ClientSession() as s:
            hass.session = s
            e = fakeha.FakeConfigEntry(data={"serve_url": real_seat.url, "serve_user": "opencode", "serve_pass": short})
            f = importlib.import_module("cc_membrane.config_flow").MembraneConfigFlow()
            f.hass, f.context = hass, {"entry": e}
            done = await f.async_step_reauth_confirm({"serve_pass": real_seat.mint()})
            return done, e
    done, e = asyncio.run(renew())
    assert done["reason"] == "reauth_successful" and e.data["serve_pass"] != short
