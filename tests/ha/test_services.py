"""Slice 2 (#1143), C: `membrane.send_message` and `membrane.ask` against a REAL cliffvm seat with a fake claude: the same seat sessions Assist uses, the caller as the speaker line, the answer
as the response data of `ask`, the seat's own words when it could not answer, a refused token starting the reauth."""
import asyncio
import importlib
import json
import time

import aiohttp
import pytest

from . import fakeha
from .test_entities import _loaded


def _call(hass, service, data, user_id=None, response=True):
    return hass.services.async_call("membrane", service, data, blocking=True, context=fakeha.Context(user_id), return_response=response)


def test_the_two_services_are_registered_ask_answers_and_unload_takes_them_away(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            here = (hass.services.has_service("membrane", "ask"), hass.services.has_service("membrane", "send_message"),
                    hass.services.supports_response("membrane", "ask"), hass.services.supports_response("membrane", "send_message"))
            await pkg.async_unload_entry(hass, entry)
            return here, (hass.services.has_service("membrane", "ask"), hass.services.has_service("membrane", "send_message"))
        finally:
            await s.close()
    here, gone = asyncio.run(go())
    assert here == (True, True, "only", "none") and gone == (False, False)


def test_ask_returns_the_seats_answer_as_response_data_with_the_callers_name_as_the_speaker_line(ha, real_seat):
    pkg, hass = ha
    hass.users["u1"] = type("U", (), {"name": "Dima"})()

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            return await _call(hass, "ask", {"text": "is the door locked?", "session": "doors"}, user_id="u1"), dict(entry.runtime_data.sessions)
        finally:
            await s.close()
    out, sessions = asyncio.run(go())
    assert out == {"answer": "Done: [HA user: Dima]\nis the door locked?", "session": "doors"}
    assert list(sessions) == ["doors"]


def test_the_same_session_name_continues_the_same_seat_session_the_default_is_service_and_titles_say_hass(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            await _call(hass, "ask", {"text": "one"})
            await _call(hass, "ask", {"text": "two"})
            await _call(hass, "ask", {"text": "three", "session": "other"})
            async with s.get(real_seat.url + "/session", headers=entry.runtime_data.client._headers) as r:
                return await r.json(), dict(entry.runtime_data.sessions)
        finally:
            await s.close()
    listed, mapping = asyncio.run(go())
    assert sorted(x["title"] for x in listed) == ["hass-other", "hass-service"]
    assert set(mapping) == {"service", "other"}


def test_send_message_returns_at_once_and_the_seat_answers_in_its_own_time(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            out = await _call(hass, "send_message", {"text": "the garage is open", "session": "garage"}, response=False)
            sid = entry.runtime_data.sessions["garage"]
            for _ in range(200):
                async with s.get(real_seat.url + f"/session/{sid}/message", headers=entry.runtime_data.client._headers) as r:
                    rows = await r.json()
                texts = [p.get("text") for row in rows for p in row.get("parts", []) if p.get("type") == "text"]
                if any((t or "").startswith("Done:") for t in texts):
                    return out, texts
                await asyncio.sleep(0.1)
            return out, texts
        finally:
            await s.close()
    out, texts = asyncio.run(go())
    assert out is None
    assert "the garage is open" in texts and "Done: the garage is open" in texts


def test_a_turn_the_seat_could_not_answer_is_an_error_with_the_seats_cause_and_empty_text_is_refused(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            with pytest.raises(fakeha.HomeAssistantError) as e1:
                await _call(hass, "ask", {"text": "this will fail"})
            with pytest.raises(fakeha.HomeAssistantError) as e2:
                await _call(hass, "ask", {"text": "   "})
            return str(e1.value), str(e2.value)
        finally:
            await s.close()
    e1, e2 = asyncio.run(go())
    assert e1.startswith("The seat could not answer:") and "not logged in" in e1
    assert "text" in e2


def test_a_token_the_seat_refuses_starts_the_reauth_and_the_error_says_to_paste_a_new_one(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat, token=real_seat.mint_at(+3600))
        try:
            entry.runtime_data.client._headers = importlib.import_module("cc_membrane.seat_client").SeatClient(s, real_seat.url, real_seat.mint_at(-300))._headers
            with pytest.raises(fakeha.HomeAssistantError) as e:
                await _call(hass, "ask", {"text": "late"})
            return str(e.value), entry.reauth_started
        finally:
            await s.close()
    msg, started = asyncio.run(go())
    assert "refused" in msg and "paste a new token" in msg and started == 1
