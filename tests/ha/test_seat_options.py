"""The seat's settings of an inside entry -> its launch line (#1143, operator 2026-11-15: the component runs the seat itself again). seat_options.py is
addon/options.sh's mapping in Python, the same facts (cloud_models, minds, seat_backend, seat_name) and the same rule: a secret is an environment variable,
never an argument. Pure: no Home Assistant, no process."""
import importlib.util
import json
from pathlib import Path

import pytest

CC = Path(__file__).resolve().parents[2] / "custom_components" / "membrane"
KEY = "sk-test-SECRET-0123456789"
CLAUDE_KEY = "sk-ant-SECRET-abcdef"


@pytest.fixture
def so():
    spec = importlib.util.spec_from_file_location("seat_options_under_test", CC / "seat_options.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def facts(argv, binary="/app/bin/cliffvm"):
    base = [binary, "serve", "--port", "4096", "--bind", "127.0.0.1", "--data-dir", "/config/membrane"]
    assert argv[: len(base)] == base, argv
    rest = argv[len(base):]
    assert len(rest) % 2 == 0 and all(x == "--fact" for x in rest[::2]), rest
    return {k: json.loads(v) for k, v in (x.split("=", 1) for x in rest[1::2])}


def launch(so, settings):
    return so.launch("/app/bin/cliffvm", 4096, "/config/membrane", settings)


MINDS = lambda m: {"loops": m, "cortex": m, "workers": m, "seat_default": m, "loops_auth": "chat", "cortex_auth": "chat", "workers_auth": "chat", "think": "auto"}
ANTHROPIC = {"backend": "anthropic", "base_url": "https://api.anthropic.com", "api_key_env": "ANTHROPIC_API_KEY"}


def test_anthropic_is_the_openai_compatible_endpoint_with_the_default_model_and_the_key_only_in_the_environment(so):
    argv, env = launch(so, {"provider": "anthropic", "anthropic_api_key": CLAUDE_KEY, "api_key": KEY})
    f = facts(argv)
    assert f == {"cloud_models": [{"name": "claude-sonnet-5-5", **ANTHROPIC}], "minds": MINDS("claude-sonnet-5-5")}
    assert env == {"ANTHROPIC_API_KEY": CLAUDE_KEY}
    assert all(CLAUDE_KEY not in a and KEY not in a for a in argv)


def test_anthropic_takes_the_model_and_refuses_no_key(so):
    argv, _ = launch(so, {"provider": "anthropic", "model": "claude-opus-5-5", "anthropic_api_key": CLAUDE_KEY})
    assert facts(argv)["cloud_models"] == [{"name": "claude-opus-5-5", **ANTHROPIC}]
    for s in ({"provider": "anthropic"}, {"provider": "anthropic", "anthropic_api_key": "  "}):
        with pytest.raises(so.SeatOptionsError) as e:
            launch(so, s)
        assert e.value.field == "anthropic_api_key"


def test_openai_is_one_cloud_models_entry_with_the_key_by_environment_or_none(so):
    argv, env = launch(so, {"provider": "openai", "base_url": "https://api.example.com/", "model": "gpt-x", "api_key": KEY})
    assert facts(argv) == {"cloud_models": [{"name": "gpt-x", "backend": "openai", "base_url": "https://api.example.com/", "api_key_env": "OPENAI_API_KEY"}],
                           "minds": MINDS("gpt-x")}
    assert env == {"OPENAI_API_KEY": KEY} and all(KEY not in a for a in argv)
    argv, env = launch(so, {"provider": "openai", "base_url": "http://lmstudio.local:1234", "model": "m"})
    assert facts(argv)["cloud_models"] == [{"name": "m", "backend": "openai", "base_url": "http://lmstudio.local:1234"}] and env == {}


def test_ollama_is_seat_backend_and_no_key(so):
    argv, env = launch(so, {"provider": "ollama", "ollama_url": "http://192.168.1.5:11434", "model": "llama3.2", "api_key": KEY})
    assert facts(argv) == {"seat_backend": "http://192.168.1.5:11434", "minds": MINDS("llama3.2")} and env == {}


def test_the_name_is_a_json_string_fact_and_absent_when_empty(so):
    argv, _ = launch(so, {"provider": "ollama", "ollama_url": "http://o:11434", "model": "m", "seat_name": "Blurp 'the' \"seat\""})
    assert facts(argv)["seat_name"] == "Blurp 'the' \"seat\""
    argv, _ = launch(so, {"provider": "ollama", "ollama_url": "http://o:11434", "model": "m", "seat_name": "42"})
    assert facts(argv)["seat_name"] == "42"
    argv, _ = launch(so, {"provider": "ollama", "ollama_url": "http://o:11434", "model": "m", "seat_name": ""})
    assert "seat_name" not in facts(argv)


@pytest.mark.parametrize("settings, field", [
    ({"provider": "gemini"}, "provider"),
    ({"provider": "claude_cli", "anthropic_api_key": "k"}, "provider"),          # the add-on's claude_cli is not offered here: no claude CLI in Home Assistant
    ({}, "provider"),
    ({"provider": "openai", "model": "m"}, "base_url"),
    ({"provider": "openai", "base_url": "ftp://x", "model": "m"}, "base_url"),
    ({"provider": "openai", "base_url": "https://x"}, "model"),
    ({"provider": "ollama", "ollama_url": "", "model": "m"}, "ollama_url"),
    ({"provider": "ollama", "ollama_url": "ollama.lan:11434", "model": "m"}, "ollama_url"),
    ({"provider": "ollama", "ollama_url": "http://h:11434"}, "model"),
])
def test_bad_settings_are_refused_with_the_field_named(so, settings, field):
    with pytest.raises(so.SeatOptionsError) as e:
        launch(so, {**settings, "api_key": KEY, "anthropic_api_key": CLAUDE_KEY} if settings.get("provider") == "openai" else settings)
    assert e.value.field == field
    assert KEY not in str(e.value) and CLAUDE_KEY not in str(e.value)


def test_the_mapping_matches_the_addons_for_every_provider_it_shares(so):
    """The add-on's options.sh is the reference (do not edit addon/): the same fact names and the same model default."""
    sh = (Path(__file__).resolve().parents[2] / "addon" / "options.sh").read_text()
    assert "model=claude-sonnet-5-5" in sh and so.DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5-5"
    assert 'base_url: "https://api.anthropic.com"' in sh and so.ANTHROPIC_BASE_URL == "https://api.anthropic.com"
    assert 'loops_auth: "chat", cortex_auth: "chat", workers_auth: "chat", think: "auto"' in sh
    assert so.PROVIDERS == ("anthropic", "openai", "ollama")
