"""Run a seat inside Home Assistant (#1143, operator 2026-11-15: the default path again; "connect to a seat I run" is the second). The config flow's menu,
the provider forms, the entry they make, its setup (the seat runner downloads the release of the component's version from a local release server, starts
the FAKE cliffvm of tests/ha/fakedist.py, builds the client with the local URL and the seat's token), the unload and Home Assistant's stop (the seat is
gone), the repair when the runner gives up, the options flow (provider, model, key: the seat restarts), the migration of a 0.7.53 inside entry (it comes
back by itself as provider ollama with its old URL and model), and the add-on's discovery leaving an inside entry alone. A fake Home Assistant; loopback only."""
import asyncio
import importlib
import json
import os
from pathlib import Path

import aiohttp
import pytest

from . import fakeha
from .fakedist import ReleaseServer, make_release

CC = Path(__file__).resolve().parents[2] / "custom_components" / "membrane"
VERSION = json.loads((CC / "manifest.json").read_text())["version"]
KEY = "sk-ant-SECRET-abcdef"


def _cf():
    return importlib.import_module("cc_membrane.config_flow")


def _flow(hass, source="user"):
    f = _cf().MembraneConfigFlow()
    f.hass, f.context = hass, {"source": source}
    return f


def _fields(form):
    return form["data_schema"].fields()


def _ollama(hass, url="http://ollama.lan:11434", model="llama3.2"):
    e = fakeha.FakeConfigEntry(domain="ollama", entry_id="o1", unique_id=None, data={"url": url, "model": model})
    e.subentries = {}
    hass.config_entries.entries.append(e)


# ---- the flow --------------------------------------------------------------------------------------------------------------------------------------

def test_the_first_step_is_a_menu_run_a_seat_inside_first_then_connect(ha, run):
    pkg, hass = ha
    menu = run(_flow(hass).async_step_user())
    assert menu["type"] == "menu" and menu["step_id"] == "user" and menu["menu_options"] == ["inside", "connect"]


def test_inside_asks_the_provider_anthropic_by_default_ollama_when_home_assistant_has_one(ha, run):
    pkg, hass = ha
    form = run(_flow(hass).async_step_inside())
    assert form["step_id"] == "inside" and set(_fields(form)) == {"provider"}
    assert next(k for k in form["data_schema"].schema if getattr(k, "schema", k) == "provider").default == "anthropic"
    _ollama(hass)
    form = run(_flow(hass).async_step_inside())
    assert next(k for k in form["data_schema"].schema if getattr(k, "schema", k) == "provider").default == "ollama"
    sel = form["data_schema"].fields()["provider"]
    assert [o["value"] if isinstance(o, dict) else o for o in sel.config["options"]] == ["anthropic", "openai", "ollama"], "claude_cli is not offered"


def test_anthropic_needs_a_key_as_a_password_and_makes_the_inside_entry(ha, run):
    pkg, hass = ha
    f = _flow(hass)
    form = run(f.async_step_inside({"provider": "anthropic"}))
    assert form["step_id"] == "anthropic" and set(_fields(form)) == {"anthropic_api_key", "model", "seat_name"}
    assert _fields(form)["anthropic_api_key"].config["type"] == "password"
    assert next(k for k in form["data_schema"].schema if getattr(k, "schema", k) == "model").default == "claude-sonnet-5-5"
    bad = run(f.async_step_anthropic({"anthropic_api_key": "  ", "model": "claude-sonnet-5-5"}))
    assert bad["type"] == "form" and bad["errors"] == {"anthropic_api_key": "required"}
    done = run(f.async_step_anthropic({"anthropic_api_key": KEY, "model": "claude-sonnet-5-5"}))
    assert done["type"] == "create_entry"
    d = done["data"]
    assert d["mode"] == "inside" and d["provider"] == "anthropic" and d["anthropic_api_key"] == KEY and d["model"] == "claude-sonnet-5-5"
    assert isinstance(d["serve_port"], int) and d["serve_port"] > 0
    assert "serve_url" not in d and "serve_pass" not in d


def test_ollama_is_prefilled_from_home_assistants_ollama_and_checks_the_url(ha, run):
    pkg, hass = ha
    _ollama(hass)
    f = _flow(hass)
    form = run(f.async_step_inside({"provider": "ollama"}))
    assert form["step_id"] == "ollama" and set(_fields(form)) == {"ollama_url", "model", "seat_name"}
    defaults = {getattr(k, "schema", k): k.default for k in form["data_schema"].schema}
    assert defaults["ollama_url"] == "http://ollama.lan:11434" and defaults["model"] == "llama3.2"
    bad = run(f.async_step_ollama({"ollama_url": "ollama.lan:11434", "model": "llama3.2"}))
    assert bad["errors"] == {"ollama_url": "invalid_url"}
    done = run(f.async_step_ollama({"ollama_url": "http://ollama.lan:11434/", "model": "llama3.2"}))
    assert done["data"]["provider"] == "ollama" and done["data"]["ollama_url"] == "http://ollama.lan:11434" and done["data"]["mode"] == "inside"


def test_openai_needs_a_url_and_a_model_and_the_key_is_optional(ha, run):
    pkg, hass = ha
    f = _flow(hass)
    form = run(f.async_step_inside({"provider": "openai"}))
    assert form["step_id"] == "openai" and set(_fields(form)) == {"base_url", "model", "api_key", "seat_name"}
    assert run(f.async_step_openai({"base_url": "nope", "model": "m"}))["errors"] == {"base_url": "invalid_url"}
    assert run(f.async_step_openai({"base_url": "http://x:1", "model": " "}))["errors"] == {"model": "required"}
    done = run(f.async_step_openai({"base_url": "http://x:1", "model": "m"}))
    assert done["type"] == "create_entry" and "api_key" not in done["data"]


def test_an_unsupported_host_aborts_the_inside_path_with_the_reason(ha, run, monkeypatch):
    pkg, hass = ha
    sr = importlib.import_module("cc_membrane.seat_runner")
    monkeypatch.setattr(sr.platform, "machine", lambda: "armv7l")
    res = run(_flow(hass).async_step_inside())
    assert res["type"] == "abort" and res["reason"] == "unsupported_platform"


def test_one_entry_per_home_assistant_holds_for_the_menu(ha, run):
    pkg, hass = ha
    hass.config_entries.entries.append(fakeha.FakeConfigEntry(data={"mode": "inside", "provider": "ollama"}))
    with pytest.raises(fakeha.AbortFlow):
        run(_flow(hass).async_step_user())


# ---- the setup: the runner starts the seat ---------------------------------------------------------------------------------------------------------

def _inside_entry(hass, release_url, **data):
    e = fakeha.FakeConfigEntry(minor_version=5, data={"mode": "inside", "provider": "anthropic", "anthropic_api_key": KEY, "model": "claude-sonnet-5-5",
                                                      "serve_port": importlib.import_module("cc_membrane.seat_runner").free_port(0),
                                                      "release_base": release_url, **data})
    hass.config_entries.entries.append(e)
    return e


def _runs(cfg: Path) -> list:
    f = cfg / "membrane" / "fake-runs.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _setup_inside(pkg, hass, tmp_path, run, body, **data):
    sr = importlib.import_module("cc_membrane.seat_runner")
    rel = make_release(tmp_path, VERSION, sr.release_target())
    hass.config.config_dir = str(tmp_path / "config")

    async def go():
        async with ReleaseServer(rel) as srv, aiohttp.ClientSession() as s:
            hass.session = s
            e = _inside_entry(hass, srv.url, **data)
            try:
                return await body(e, srv)
            finally:
                if e.runtime_data is not None and e.runtime_data.seat is not None:
                    await e.runtime_data.seat.stop()
    return run(go())


def test_setup_downloads_the_release_starts_the_seat_and_talks_to_it_with_its_token(ha, run, tmp_path):
    pkg, hass = ha

    async def body(e, srv):
        await pkg.async_setup_entry(hass, e)
        d = e.runtime_data
        out = {"url": d.client.url, "token": d.seat.token, "port": e.data["serve_port"], "health": d.health.data["health"], "asked": list(srv.asked),
               "pid": d.seat.pid}
        assert await pkg.async_unload_entry(hass, e)
        out["alive_after_unload"] = _alive(out["pid"])
        return out
    out = _setup_inside(pkg, hass, tmp_path, run, body)
    cfg = tmp_path / "config"
    sr = importlib.import_module("cc_membrane.seat_runner")
    assert out["asked"] == [f"/babyseat-{VERSION}-{sr.release_target()}.tar.gz", f"/babyseat-{VERSION}-{sr.release_target()}.tar.gz.sha256"]
    assert (cfg / "membrane" / "app" / VERSION / "bin" / "cliffvm").is_file()
    assert out["port"] > 0 and out["url"] == f"http://127.0.0.1:{out['port']}"
    assert out["health"] == {"healthy": True} and out["token"].startswith("tok-")
    r = _runs(cfg)[-1]
    assert "--data-dir" in r["argv"] and r["argv"][r["argv"].index("--data-dir") + 1] == str(cfg / "membrane")
    assert r["env"]["ANTHROPIC_API_KEY"] == KEY and all(KEY not in a for a in r["argv"])
    assert not out["alive_after_unload"], "the unload stops the seat"


def test_a_free_4096_is_the_default_port_and_a_taken_port_is_replaced_in_the_entry(ha, run, tmp_path):
    pkg, hass = ha
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    taken = s.getsockname()[1]

    async def body(e, srv):
        await pkg.async_setup_entry(hass, e)
        return e.data["serve_port"]
    try:
        port = _setup_inside(pkg, hass, tmp_path, run, body, serve_port=taken)
    finally:
        s.close()
    assert port != taken and port > 0
    flow_mod = _cf()
    sr = importlib.import_module("cc_membrane.seat_runner")
    assert sr.DEFAULT_SERVE_PORT == 4096 and flow_mod.DEFAULT_PORT == 4096


def test_home_assistants_stop_stops_the_seat(ha, run, tmp_path):
    pkg, hass = ha

    async def body(e, srv):
        await pkg.async_setup_entry(hass, e)
        pid = e.runtime_data.seat.pid
        await hass.fire("homeassistant_stop")
        return pid, _alive(pid)                   # before the test's own cleanup stops it
    pid, alive = _setup_inside(pkg, hass, tmp_path, run, body)
    assert not alive


def test_a_seat_that_cannot_start_is_not_ready_and_leaves_no_process(ha, run, tmp_path):
    pkg, hass = ha
    (tmp_path / "config" / "membrane").mkdir(parents=True)
    (tmp_path / "config" / "membrane" / "fake.json").write_text(json.dumps({"mode": "exit", "code": 7}))

    async def body(e, srv):
        try:
            await pkg.async_setup_entry(hass, e)
        except fakeha.ConfigEntryNotReady as err:
            return str(err)
    err = _setup_inside(pkg, hass, tmp_path, run, body)
    assert err and "7" in err and KEY not in err


def test_a_bad_release_is_not_ready_with_the_reason_and_an_unsupported_host_is_an_error(ha, run, tmp_path, monkeypatch):
    pkg, hass = ha

    async def body(e, srv):
        (srv.folder / next(p.name for p in srv.folder.iterdir() if p.name.endswith(".sha256"))).write_text("0" * 64 + "  x\n")
        try:
            await pkg.async_setup_entry(hass, e)
        except fakeha.ConfigEntryNotReady as err:
            return str(err)
    assert "sha256" in _setup_inside(pkg, hass, tmp_path, run, body)
    sr = importlib.import_module("cc_membrane.seat_runner")
    monkeypatch.setattr(sr.platform, "system", lambda: "Windows")
    e = _inside_entry(hass, "http://127.0.0.1:1")
    with pytest.raises(fakeha.ConfigEntryError):
        run(pkg.async_setup_entry(hass, e))


def test_settings_it_cannot_start_with_are_an_error_before_any_download(ha, run, tmp_path):
    pkg, hass = ha

    async def body(e, srv):
        try:
            await pkg.async_setup_entry(hass, e)
        except fakeha.ConfigEntryError as err:
            return str(err), list(srv.asked)
    err, asked = _setup_inside(pkg, hass, tmp_path, run, body, anthropic_api_key="")
    assert "Anthropic API key" in err and asked == []


def test_a_seat_the_runner_gave_up_on_is_a_repair_and_a_good_start_clears_it(ha, run, tmp_path):
    pkg, hass = ha

    async def body(e, srv):
        await pkg.async_setup_entry(hass, e)
        e.runtime_data.seat.on_give_up("the seat exited with code 9 after 0 s")
        issue = dict(hass.issues)
        await pkg.async_unload_entry(hass, e)
        await pkg.async_setup_entry(hass, e)
        return issue, dict(hass.issues)
    issue, after = _setup_inside(pkg, hass, tmp_path, run, body)
    (dom, iid), kw = next(iter(issue.items()))
    assert dom == "membrane" and kw["translation_key"] == "seat_stopped" and "code 9" in kw["translation_placeholders"]["reason"]
    assert after == {}
    strings = json.loads((CC / "strings.json").read_text())
    assert "{reason}" in strings["issues"]["seat_stopped"]["description"]


def test_a_changed_setting_reloads_the_entry_and_another_data_change_does_not(ha, run, tmp_path):
    pkg, hass = ha

    async def body(e, srv):
        await pkg.async_setup_entry(hass, e)
        assert e.update_listeners, "the setup listens for changes of its entry"
        listener = e.update_listeners[0]
        hass.config_entries.reloaded = None
        hass.config_entries.async_update_entry(e, data={**e.data, "ha_user_id": "other"})
        await listener(hass, e)
        first = hass.config_entries.reloaded
        hass.config_entries.async_update_entry(e, options={"provider": "anthropic", "anthropic_api_key": KEY, "model": "claude-opus-5-5"})
        await listener(hass, e)
        return first, hass.config_entries.reloaded
    first, second = _setup_inside(pkg, hass, tmp_path, run, body)
    assert first is None and second == "e1"


# ---- the options flow ------------------------------------------------------------------------------------------------------------------------------

def _options(hass, entry):
    of = _cf().MembraneConfigFlow.async_get_options_flow(entry)
    of.hass, of.config_entry = hass, entry
    return of


def test_the_options_flow_changes_provider_model_and_key_and_an_empty_key_keeps_the_old(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(minor_version=5, data={"mode": "inside", "provider": "anthropic", "anthropic_api_key": KEY, "model": "claude-sonnet-5-5",
                                                      "serve_port": 4096})
    hass.config_entries.entries.append(e)
    assert _cf().MembraneConfigFlow.async_supports_options_flow(e) is True
    assert _cf().MembraneConfigFlow.async_supports_options_flow(fakeha.FakeConfigEntry(data={"serve_url": "http://x", "serve_pass": "t"})) is False
    of = _options(hass, e)
    form = run(of.async_step_init())
    assert form["step_id"] == "init" and {getattr(k, "schema", k): k.default for k in form["data_schema"].schema}["provider"] == "anthropic"
    form = run(of.async_step_init({"provider": "anthropic"}))
    defaults = {getattr(k, "schema", k): getattr(k, "default", None) for k in form["data_schema"].schema}
    assert form["step_id"] == "anthropic" and defaults["model"] == "claude-sonnet-5-5"
    assert KEY not in json.dumps(defaults, default=str), "the stored key is never sent back to the form"
    done = run(of.async_step_anthropic({"anthropic_api_key": "", "model": "claude-opus-5-5"}))
    assert done["type"] == "create_entry" and e.options == {"provider": "anthropic", "anthropic_api_key": KEY, "model": "claude-opus-5-5"}
    of = _options(hass, e)
    run(of.async_step_init({"provider": "ollama"}))
    bad = run(of.async_step_ollama({"ollama_url": "x", "model": "m"}))
    assert bad["errors"] == {"ollama_url": "invalid_url"}
    run(of.async_step_ollama({"ollama_url": "http://o:11434", "model": "m"}))
    assert e.options == {"provider": "ollama", "ollama_url": "http://o:11434", "model": "m"}
    seat = importlib.import_module("cc_membrane").seat_settings(e)
    assert seat["provider"] == "ollama" and seat["ollama_url"] == "http://o:11434" and "anthropic_api_key" not in seat


# ---- the migration of a 0.7.53 inside entry, the reauth, the discovery -----------------------------------------------------------------------------

def _migrate(pkg, hass, entry):
    hass.config_entries.entries.append(entry)
    assert asyncio.run(pkg.async_migrate_entry(hass, entry)) is True
    return entry


def test_a_0753_inside_entry_comes_back_as_an_inside_seat_on_its_ollama(ha):
    pkg, hass = ha
    e = _migrate(pkg, hass, fakeha.FakeConfigEntry(minor_version=1, data={"mode": "inside", "backend": "http://ollama:11434/", "model": "qwen3:4b",
                                                                          "embed_model": "nomic-embed-text"},
                                                   options={"landing": True, "loops": 1}))
    assert (e.version, e.minor_version) == (1, 5)
    assert e.data == {"mode": "inside", "provider": "ollama", "ollama_url": "http://ollama:11434", "model": "qwen3:4b"}
    assert e.options == {}


def test_the_zoo_inside_seat_and_the_options_model_win(ha):
    pkg, hass = ha
    e = _migrate(pkg, hass, fakeha.FakeConfigEntry(minor_version=2, data={"mode": "inside", "backend": "http://o:11434", "model": "old", "embed_model": "e"},
                                                   options={"model": "picked", "seats": [{"id": "inside", "inside": True}], "active": "inside"}))
    assert e.data == {"mode": "inside", "provider": "ollama", "ollama_url": "http://o:11434", "model": "picked"}


def test_an_inside_entry_without_its_ollama_url_is_left_for_reauth_no_seat(ha):
    pkg, hass = ha
    e = _migrate(pkg, hass, fakeha.FakeConfigEntry(minor_version=2, data={"mode": "inside"}, options={"seats": [{"id": "s1", "inside": True}], "active": "s1"}))
    assert e.data == {"serve_url": "", "serve_user": "opencode", "serve_pass": ""} and e.minor_version == 5


def test_a_1_4_entry_is_bumped_to_1_5_unchanged(ha):
    pkg, hass = ha
    e = _migrate(pkg, hass, fakeha.FakeConfigEntry(minor_version=4, data={"serve_url": "http://x", "serve_user": "opencode", "serve_pass": "t"}))
    assert e.minor_version == 5 and e.data == {"serve_url": "http://x", "serve_user": "opencode", "serve_pass": "t"}


def test_a_refused_token_on_an_inside_seat_asks_only_for_a_restart(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(minor_version=5, data={"mode": "inside", "provider": "ollama", "ollama_url": "http://o:11434", "model": "m", "serve_port": 4096})
    hass.config_entries.entries.append(e)
    f = _cf().MembraneConfigFlow()
    f.hass, f.context = hass, {"source": "reauth", "entry": e}
    form = run(f.async_step_reauth(dict(e.data)))
    assert form["step_id"] == "reauth_inside" and form["data_schema"] is None
    done = run(f.async_step_reauth_inside({}))
    assert done["type"] == "abort" and hass.config_entries.reloaded == "e1"


def test_the_addons_discovery_leaves_an_inside_entry_alone(ha, run):
    pkg, hass = ha
    e = fakeha.FakeConfigEntry(minor_version=5, data={"mode": "inside", "provider": "ollama", "ollama_url": "http://o:11434", "model": "m", "serve_port": 4096})
    hass.config_entries.entries.append(e)
    f = _flow(hass, "hassio")

    async def go():
        try:
            return await f.async_step_hassio(fakeha.HassioServiceInfo({"host": "127.0.0.1", "port": 4096, "token": "t"}))
        except fakeha.AbortFlow as err:
            return {"type": "abort", "reason": err.reason}
    res = run(go())
    assert res["type"] == "abort" and res["reason"] == "already_configured"
    assert e.data["mode"] == "inside" and "serve_url" not in e.data and hass.config_entries.reloaded is None


def test_a_setup_that_fails_after_the_seat_started_stops_the_seat(ha, run, tmp_path, monkeypatch):
    pkg, hass = ha
    coord = importlib.import_module("cc_membrane.coordinator")

    async def down(self):
        raise fakeha.UpdateFailed("the first poll failed")
    monkeypatch.setattr(coord.SeatHealth, "_async_update_data", down)

    async def body(e, srv):
        try:
            await pkg.async_setup_entry(hass, e)
        except fakeha.ConfigEntryNotReady:
            pass
        pid = _runs(tmp_path / "config")[-1]["pid"]
        return pid, _alive(pid)
    pid, alive = _setup_inside(pkg, hass, tmp_path, run, body)
    assert not alive


def test_the_entrys_model_is_the_seats_pick_at_every_start(ha, run, tmp_path):
    """The seat seeds its pick (Minds:seat) from the `minds` fact only at its FIRST start (router.c1f): a model changed in Configure would never reach it
    (the real Home Assistant run, scripts/ha_core_smoke.py, answered "No Anthropic API key" after the change). The setup writes the entry's model through
    the seat's own options route when the seat runs on another; a seat already on it gets no write."""
    pkg, hass = ha
    posts = tmp_path / "config" / "membrane" / "fake-posts.jsonl"

    async def body(e, srv):
        await pkg.async_setup_entry(hass, e)
        first = posts.read_text().splitlines() if posts.exists() else []
        await pkg.async_unload_entry(hass, e)
        await pkg.async_setup_entry(hass, e)
        return first, posts.read_text().splitlines() if posts.exists() else []
    first, after = _setup_inside(pkg, hass, tmp_path, run, body, model="claude-opus-5-5")
    assert [json.loads(x) for x in first] == [{"loops": "claude-opus-5-5"}]
    assert after == first, "the seat already runs on it: no second write"
