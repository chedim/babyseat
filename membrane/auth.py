"""Per-operator auth for cloud backends (multi-provider).

The key is a secret: it lives OUTSIDE the repo, under ~/.membrane/auth.json
(chmod 600), never in a project file (a project env file is per-project and
this credential is per-operator), never in the repo, never logged. Resolution
order per provider: the provider's environment variable WINS (the operator's
shell is the louder authority), then the auth store. Readers get the raw key;
everything user-facing goes through mask().

PROVIDERS is the seam: one registry row per cloud backend -- base URL, chat
path (all OpenAI-compatible /v1/chat/completions shapes), model-listing path,
env var names, and a default relative price for the router's ladder. Adding a
provider is a registry row, not a code path. The proxy keeps keys in MEMORY
ONLY (srv.cloud_keys, per provider) and sends them as outgoing headers per
call -- the same passthrough discipline as --anthropic mode's _anthropic_auth,
minus the client-header hop: here the proxy itself is the client.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


def auth_path(home: Path | None = None) -> Path:
    return (Path(home) if home else Path.home()) / ".membrane" / "auth.json"


def _read_store(home: Path | None = None) -> dict:
    try:
        return json.loads(auth_path(home).read_text())
    except Exception:
        return {}


def save_api_key(provider: str, key: str, home: Path | None = None) -> Path:
    """Store one provider key (merges; never removes others). 600 on create."""
    p = auth_path(home)
    store = _read_store(home)
    store[provider] = key.strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=1) + "\n")
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return p


def _env_names(provider: str) -> tuple[str, ...]:
    p = PROVIDERS.get(provider) or AGENT_PROVIDERS.get(provider)
    env = p.get("env") if p else None
    if env is None:
        return (provider.upper() + "_API_KEY",)
    return env if isinstance(env, tuple) else (env,)


def load_api_key(provider: str = "anthropic",
                 home: Path | None = None,
                 environ: dict | None = None) -> str:
    """The working key: env WINS, then the auth store. Empty string = none."""
    env = os.environ if environ is None else environ
    for name in _env_names(provider):
        v = env.get(name, "").strip()
        if v:
            return v
    return str(_read_store(home).get(provider) or "").strip()


def has_key(provider: str = "anthropic",
            home: Path | None = None,
            environ: dict | None = None) -> bool:
    return bool(load_api_key(provider, home, environ))


def mask(key: str) -> str:
    """For logs/boot lines: keep the shape, hide the secret."""
    if not key:
        return "(none)"
    return f"{key[:7]}...{key[-4:]}" if len(key) > 14 else "***"


# ---- the provider registry ---------------------------------------------------
# Every provider speaks the OpenAI-compatible chat surface at base + chat_path;
# "auth_style" picks the header shape ("bearer" vs anthropic's x-api-key).
# default_price: relative money/turn seed for the router's ladder (router.py
# ModelRecord.price_override); None = fall through to PRICE_TABLE.

PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "base": "https://api.anthropic.com",
        "chat_path": "/v1/chat/completions",
        "models_path": "/v1/models",
        "auth_style": "anthropic",
        "env": ("ANTHROPIC_API_KEY",),
        "default_price": None,
        "key_hint": "sk-ant-...",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api",
        "chat_path": "/v1/chat/completions",
        "models_path": "/v1/models",
        "auth_style": "bearer",
        "env": ("OPENROUTER_API_KEY",),
        "default_price": 1.0,
        "key_hint": "sk-or-...",
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "chat_path": "/chat/completions",
        "models_path": "/models",
        "auth_style": "bearer",
        "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "default_price": 1.0,
        "key_hint": "AIza...",
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "chat_path": "/chat/completions",
        "models_path": "/models",
        "auth_style": "bearer",
        "env": ("GROQ_API_KEY",),
        "default_price": 0.75,
        "key_hint": "gsk_...",
    },
    "github": {
        "base": "https://models.github.ai",
        "chat_path": "/inference/chat/completions",
        "models_path": "/catalog/models",
        "auth_style": "bearer",
        "env": ("GITHUB_TOKEN", "GITHUB_MODELS_TOKEN"),
        "default_price": 1.0,
        "key_hint": "github_pat_... (classic PAT works too)",
    },
    # OpenCode Zen: paid by default (1.0) -- only the ids it DECLARES free
    # (big-pickle, the *-free rows) pin near-floor, so a zero budget never
    # opens its paid models. Free-period caveat (Zen docs): prompts on
    # some free models may be used for training.
    "zen": {
        "base": "https://opencode.ai/zen/v1",
        "chat_path": "/chat/completions",
        "models_path": "/models",
        "auth_style": "bearer",
        "env": ("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"),
        "default_price": 1.0,
        "free_ids": ("big-pickle",),
        "free_suffixes": ("-free",),
        "key_hint": "the OpenCode Zen API key (opencode.ai/auth)",
    },
    # OpenAI proper (#630). default_price None: the router's PRICE_TABLE
    # (gpt-5 -> 8.0, gpt -> 5.0) prices it as the paid rung it is. Its
    # /v1/models lists every modality, so `skip_substrings` drops the ids
    # that are not chat (embeddings, speech, images, moderation, realtime).
    "openai": {
        "base": "https://api.openai.com",
        "chat_path": "/v1/chat/completions",
        "models_path": "/v1/models",
        "auth_style": "bearer",
        "env": ("OPENAI_API_KEY",),
        "default_price": None,
        "skip_substrings": ("embedding", "whisper", "tts", "dall-e",
                            "image", "moderation", "transcribe",
                            "realtime", "audio", "davinci", "babbage",
                            "instruct", "-pro", "codex", "computer-use"),
        "key_hint": "sk-...",
    },
    "mistral": {
        "base": "https://api.mistral.ai/v1",
        "chat_path": "/chat/completions",
        "models_path": "/models",
        "auth_style": "bearer",
        "env": ("MISTRAL_API_KEY",),
        "default_price": 1.25,
        "key_hint": "...",
    },
}

# ---- the agent-SDK providers (beside PROVIDERS, not API rows) ----------------
# Claude through the Claude Agent SDK (issue #384): NOT an API row. The SDK
# drives the `claude` CLI; by default (#413) the CLI runs on the anthropic
# row's API key, handed to it in the SDK's options env -- no key of its own.
# `membrane auth claude-agent` checks that key exists and stores only a
# non-secret mode mark (`--login`: the CLI's own login, a development opt-in;
# membrane never reads its token). The rungs are opt-in
# (router.OPT_IN_BACKENDS); Claude gets only membrane's own tools, and its
# replies are learned like any model's (#389; membrane/claude_agent.py,
# docs/claude-agent.md).
AGENT_PROVIDERS: dict[str, dict] = {
    "claude-agent": {
        "kind": "agent-sdk",
        "base": "",
        "chat_path": "",
        "models_path": "",
        "auth_style": "none",
        "env": ("MEMBRANE_CLAUDE_AGENT",),
        "default_price": None,
        "key_hint": "no key of its own: the anthropic API key",
    },
}


def _headers(provider: str, key: str) -> dict:
    """Outgoing auth headers for one provider's API call."""
    style = (PROVIDERS.get(provider) or {}).get("auth_style", "bearer")
    if style == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    h = {"Authorization": "Bearer " + key}
    if provider == "openrouter":
        h["X-Title"] = "membrane"        # openrouter's app attribution
    return h


def fetch_models(provider: str, key: str, timeout: float = 8.0) -> list[str]:
    """GET the provider's model listing -> chat model ids. Every failure
    degrades to no models (the seat just doesn't offer the rung) -- never
    raises. Parses both OpenAI shapes ({data: [...]}) and bare lists
    (github's /catalog/models)."""
    return [mid for mid, _free, _ctx in fetch_models_info(provider, key,
                                                          timeout)]


def is_free_model(row: dict, mid: str, provider: str | None = None) -> bool:
    """Zero-token-cost detection for a listing row: OpenRouter carries
    per-token `pricing` (prompt/completion "0" == free tier) and marks free
    ids with a `:free` suffix; a provider may DECLARE its free ids / id
    suffixes in PROVIDERS (Zen: big-pickle, *-free); everyone else just
    doesn't charge for their listing shape (no pricing data -> not free by
    declaration)."""
    low = str(mid).lower()
    if low.endswith(":free"):
        return True
    prov = PROVIDERS.get(provider or "") or {}
    if low in prov.get("free_ids", ()) or any(
            low.endswith(sfx) for sfx in prov.get("free_suffixes", ())):
        return True
    p = row.get("pricing")
    if isinstance(p, dict):
        try:
            if float(p.get("prompt") or 1) == 0.0 \
                    and float(p.get("completion") or 1) == 0.0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def fetch_models_info(provider: str, key: str,
                       timeout: float = 8.0) -> list[tuple[str, bool, int]]:
    """fetch_models with the free-tier flag and context_length per id:
    [(id, is_free, ctx_len), ...].  Same degrade-to-empty covenant; the
    flag feeds the router's near-floor pin (free models become zero-cost
    expert rungs on the ladder) and the context_length lets the router
    prefer rungs with more room when prices tie.
    """
    prov = PROVIDERS.get(provider)
    if not prov or not key or not prov.get("base"):
        return []                      # no listing (the agent-sdk row)
    import urllib.request
    try:
        req = urllib.request.Request(
            prov["base"] + prov["models_path"], headers=_headers(provider, key))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        rows = data if isinstance(data, list) \
            else (data.get("data") or data.get("models") or [])
        out = []
        for m in rows:
            if isinstance(m, dict):
                mid = str(m.get("id") or m.get("name") or "").strip()
                skip = prov.get("skip_substrings", ())
                if mid and not any(x in mid.lower() for x in skip):
                    out.append((mid, is_free_model(m, mid, provider),
                                int(m.get("context_length") or 0)))
        return out
    except Exception as e:
        print(f"[membrane] {provider} model listing failed ({e}); "
              f"no {provider} rungs this boot", file=sys.stderr)
        return []