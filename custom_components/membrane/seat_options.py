"""The settings of a seat the component runs inside Home Assistant -> its launch line (#1143, operator 2026-11-15). The same mapping as the add-on's
addon/options.sh (the reference; keep the two the same): the same facts (`cloud_models`, `minds`, `seat_backend`, `seat_name`), and a secret is an
ENVIRONMENT variable of the seat, never an argument (argv is visible to every process of the host). No Home Assistant import: pure, tested alone.

  provider anthropic -> Anthropic's OpenAI-compatible endpoint: --fact cloud_models=[{name: <model, default claude-sonnet-5-5>, backend: anthropic,
                        base_url: https://api.anthropic.com, api_key_env: ANTHROPIC_API_KEY}] + --fact minds=...; ANTHROPIC_API_KEY=<anthropic_api_key> (required;
                        a key from the Claude Console, scoped to a workspace). No /v1 in base_url: chat_oai.c1f appends /v1/chat/completions.
  provider openai    -> --fact cloud_models=[{name: <model>, backend: openai, base_url: <base_url>, api_key_env: OPENAI_API_KEY}] + minds; OPENAI_API_KEY=<api_key> (optional)
  provider ollama    -> --fact seat_backend=<ollama_url> + minds; no key
The add-on's `claude_cli` is not offered: Home Assistant has no claude CLI to run.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

PROVIDERS = ("anthropic", "openai", "ollama")
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"


class SeatOptionsError(ValueError):
    """A setting the seat cannot start with; `field` names it (the form's field). The message never holds a secret's value."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def is_url(u: str) -> bool:
    p = urlsplit(str(u or "").strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


def minds(model: str) -> dict:
    return {"loops": model, "cortex": model, "workers": model, "seat_default": model,
            "loops_auth": "chat", "cortex_auth": "chat", "workers_auth": "chat", "think": "auto"}


def _text(settings: dict, key: str) -> str:
    return str(settings.get(key) or "").strip()


def seat_facts(settings: dict) -> tuple[list[tuple[str, object]], dict]:
    """-> ([(fact name, JSON value)], {environment variable: secret}). Raises SeatOptionsError naming the field."""
    provider, model = _text(settings, "provider"), _text(settings, "model")
    facts: list[tuple[str, object]] = []
    env: dict = {}
    if _text(settings, "seat_name"):
        facts.append(("seat_name", _text(settings, "seat_name")))
    if provider == "anthropic":
        model = model or DEFAULT_ANTHROPIC_MODEL
        if not _text(settings, "anthropic_api_key"):
            raise SeatOptionsError("anthropic_api_key", "provider anthropic needs an Anthropic API key (Claude Console, scoped to a workspace)")
        facts.append(("cloud_models", [{"name": model, "backend": "anthropic", "base_url": ANTHROPIC_BASE_URL, "api_key_env": "ANTHROPIC_API_KEY"}]))
        env["ANTHROPIC_API_KEY"] = _text(settings, "anthropic_api_key")
    elif provider == "openai":
        base = _text(settings, "base_url")
        if not is_url(base):
            raise SeatOptionsError("base_url", "provider openai needs base_url, an http:// or https:// address (the endpoint's root)")
        if not model:
            raise SeatOptionsError("model", "provider openai needs a model (the model id the endpoint is asked for)")
        entry = {"name": model, "backend": "openai", "base_url": base}
        if _text(settings, "api_key"):
            entry["api_key_env"] = "OPENAI_API_KEY"
            env["OPENAI_API_KEY"] = _text(settings, "api_key")
        facts.append(("cloud_models", [entry]))
    elif provider == "ollama":
        url = _text(settings, "ollama_url")
        if not is_url(url):
            raise SeatOptionsError("ollama_url", "provider ollama needs ollama_url, an http:// or https:// address (e.g. http://192.168.1.10:11434)")
        if not model:
            raise SeatOptionsError("model", "provider ollama needs a model the Ollama server has pulled (e.g. llama3.2)")
        facts.append(("seat_backend", url))
    else:
        raise SeatOptionsError("provider", f"provider must be one of {', '.join(PROVIDERS)}, got: {provider or '(none)'}")
    facts.append(("minds", minds(model)))
    return facts, env


def launch(binary: str, port: int, data_dir: str, settings: dict) -> tuple[list[str], dict]:
    """-> (argv of `cliffvm serve` on 127.0.0.1, the secrets for its environment)."""
    facts, env = seat_facts(settings)
    argv = [str(binary), "serve", "--port", str(int(port)), "--bind", "127.0.0.1", "--data-dir", str(data_dir)]
    for name, value in facts:
        argv += ["--fact", f"{name}={json.dumps(value, separators=(',', ':'))}"]
    return argv, env
