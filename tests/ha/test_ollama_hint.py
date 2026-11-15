"""The Ollama hint (#1143; the 0.7.53 component's `ha_ollama`, brought back read-only): when Home Assistant has its own Ollama integration, the discovery's
confirm and the manual form say which server and model it uses, so the seat can be pointed at the same one. Nothing is configured and nothing is
written to the seat; no network call. Without an Ollama entry the forms are the plain ones (their own step ids and texts: no empty sentence)."""
import importlib
import json
import types
from pathlib import Path

import aiohttp

from . import fakeha
from .fakeseat import FakeSeat

STRINGS = Path(__file__).resolve().parents[2] / "custom_components" / "membrane" / "strings.json"


def _ollama(hass, data=None, options=None, subentries=None, entry_id="o1"):
    e = fakeha.FakeConfigEntry(domain="ollama", entry_id=entry_id, unique_id=None, data=data, options=options)
    e.subentries = subentries or {}
    hass.config_entries.entries.append(e)
    return e


def _cf():
    return importlib.import_module("cc_membrane.config_flow")


def test_an_ollama_entry_with_a_url_and_a_model_gives_both(ha):
    pkg, hass = ha
    _ollama(hass, data={"url": "http://ollama.lan:11434/", "model": "llama3.2"})
    assert _cf().ha_ollama(hass) == ("http://ollama.lan:11434", "llama3.2")


def test_the_model_of_a_conversation_subentry_is_the_one_used(ha):
    pkg, hass = ha
    _ollama(hass, data={"url": "http://ollama.lan:11434"}, options={"model": "old"},
            subentries={"s1": types.SimpleNamespace(subentry_type="ai_task_data", data={"model": "tasker"}),
                        "s2": types.SimpleNamespace(subentry_type="conversation", data={"model": "qwen3"})})
    assert _cf().ha_ollama(hass) == ("http://ollama.lan:11434", "qwen3")


def test_the_model_in_the_options_when_the_data_has_none(ha):
    pkg, hass = ha
    _ollama(hass, data={"url": "http://o:11434"}, options={"model": "mistral"})
    assert _cf().ha_ollama(hass) == ("http://o:11434", "mistral")


def test_no_ollama_entry_gives_no_hint_and_an_entry_without_a_url_is_ignored(ha):
    pkg, hass = ha
    assert _cf().ha_ollama(hass) is None
    _ollama(hass, data={"model": "x"}, entry_id="nourl")
    assert _cf().ha_ollama(hass) is None
    _ollama(hass, data={"url": "http://second:11434"}, entry_id="o2")
    assert _cf().ha_ollama(hass) == ("http://second:11434", "")


def _confirm(run, hass):
    async def go():
        async with FakeSeat("tok-1") as seat, aiohttp.ClientSession() as s:
            hass.session = s
            f = _cf().MembraneConfigFlow()
            f.hass, f.context = hass, {"source": "hassio"}
            form = await f.async_step_hassio(fakeha.HassioServiceInfo({"host": "127.0.0.1", "port": seat.port, "token": "tok-1"}))
            done = await getattr(f, f"async_step_{form['step_id']}")({})
            return form, done
    return run(go())


def test_the_discovery_confirm_carries_the_hint_in_its_placeholders(ha, run):
    pkg, hass = ha
    _ollama(hass, data={"url": "http://ollama.lan:11434", "model": "llama3.2"})
    form, done = _confirm(run, hass)
    assert form["step_id"] == "hassio_confirm_ollama"
    assert form["description_placeholders"] == {"addon": "babyseat", "ollama_url": "http://ollama.lan:11434", "ollama_model": "llama3.2"}
    assert done["type"] == "create_entry", "the hint's form confirms like the plain one"
    assert set(done["data"]) == {"serve_url", "serve_user", "serve_pass"}, "nothing of Ollama is stored"


def test_without_ollama_the_confirm_is_the_plain_one(ha, run):
    pkg, hass = ha
    form, done = _confirm(run, hass)
    assert form["step_id"] == "hassio_confirm" and form["description_placeholders"] == {"addon": "babyseat"}
    assert done["type"] == "create_entry"


def test_the_manual_form_carries_the_hint_too_and_submits_the_same(ha, run):
    pkg, hass = ha
    plain = run(_cf().MembraneConfigFlow.async_step_user(_flow(hass)))
    assert plain["step_id"] == "user" and "ollama_url" not in plain["description_placeholders"]
    _ollama(hass, data={"url": "http://ollama.lan:11434", "model": "llama3.2"})
    f = _flow(hass)
    form = run(f.async_step_user())
    assert form["step_id"] == "user_ollama"
    assert form["description_placeholders"] == {"ollama_url": "http://ollama.lan:11434", "ollama_model": "llama3.2"}
    assert set(form["data_schema"].fields()) == {"serve_url", "serve_pass"}

    async def go():
        async with FakeSeat("tok-1") as seat, aiohttp.ClientSession() as s:
            hass.session = s
            return await f.async_step_user_ollama({"serve_url": seat.url, "serve_pass": "tok-1"})
    assert run(go())["type"] == "create_entry"


def _flow(hass):
    f = _cf().MembraneConfigFlow()
    f.hass, f.context = hass, {}
    return f


def test_the_hint_texts_hold_both_placeholders_and_the_plain_texts_none():
    st = json.loads(STRINGS.read_text())["config"]["step"]
    for k in ("hassio_confirm_ollama", "user_ollama"):
        assert "{ollama_url}" in st[k]["description"] and "{ollama_model}" in st[k]["description"], k
    for k in ("hassio_confirm", "user"):
        assert "ollama" not in st[k]["description"].lower(), k
    assert st["user_ollama"]["data"] == st["user"]["data"]
    assert "add-on" in st["hassio_confirm_ollama"]["description"]
