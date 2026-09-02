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
    p = PROVIDERS.get(provider)
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


def is_free_model(row: dict, mid: str) -> bool:
    """Zero-token-cost detection for a listing row: OpenRouter carries
    per-token `pricing` (prompt/completion "0" == free tier) and marks free
    ids with a `:free` suffix; other providers just don't charge for their
    listing shape (no pricing data -> not free by declaration)."""
    if str(mid).lower().endswith(":free"):
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
    if not prov or not key:
        return []
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
                if mid:
                    out.append((mid, is_free_model(m, mid),
                                int(m.get("context_length") or 0)))
        return out
    except Exception as e:
        print(f"[membrane] {provider} model listing failed ({e}); "
              f"no {provider} rungs this boot", file=sys.stderr)
        return []