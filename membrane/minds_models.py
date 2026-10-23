"""THE MINDS' MODELS (2026-10-08): the background mechanisms' models, set
at runtime (the chat's picker only sets one session's model).

  loops   the watch loops' turns (the serve's loop runner model)
  cortex  the idle cortex: the thinker, the dreamer, the claim / A-B judges

Applied live, saved beside the field (models.json), reapplied at boot. The
think dial rides the same surface (`think`: on|off|auto -- 2026-10-15,
the switch in the chat menu): `on`/`off` inject `think` into the seats'
bodies, `auto` forwards verbatim (a non-thinking card never learns a
think:true it would 400 on).

THE MINDS' CREDENTIALS (2026-10-23, the operator): a mind picked onto a
claude-agent rung runs it with its own credentials -- `chat` (the default:
whatever the chat's rung uses, claude_agent.auth_mode()), `api-key` or
`login` -- saved beside the models (`loops_auth`, `cortex_auth`). Background
lanes take a Claude rung ONLY on an explicit pick (this module's apply: the
⚙ window, /membrane/models, an active-model entity, or a saved pick at
boot); without one the #384 guard holds and no Claude call is made.
"""
from __future__ import annotations

import json
import os
import re

MINDS = ("loops", "cortex")
AUTH_CHOICES = ("chat", "api-key", "login")    # chat = the chat rung's own mode
_AUTH_KEYS = tuple(m + "_auth" for m in MINDS)
_THINK = ("on", "off", "auto")
_NAME = re.compile(r"^[\w.:/@~+-]{1,120}$")


def read(srv) -> dict:
    # the dial as the seat holds it right now (boot default auto = None = verbatim)
    think = getattr(srv, "think", None)   # the seat speaks the value (True/False/None)
    return {"loops": str(getattr(getattr(srv, "_loop_runner", None), "model", "") or ""),
            "cortex": str(getattr(srv, "idle_cortex_model", "") or ""),
            "seat_default": str(getattr(srv, "default_seat_model", "") or ""),
            "loops_auth": _auth_of(srv, "loops"), "cortex_auth": _auth_of(srv, "cortex"),
            "think": "on" if think is True else "off" if think is False else "auto"}


def _auth_of(srv, mind: str) -> str:
    v = (getattr(srv, "_minds_auth", None) or {}).get(mind)
    return v if v in AUTH_CHOICES else "chat"


def credential_mode(srv, mind: str) -> str | None:
    """The mind's credentials for a claude-agent call: None = the chat's
    (claude_agent.credential_env's default path), else MODE_KEY / MODE_LOGIN."""
    a = _auth_of(srv, mind)
    return None if a == "chat" else a


def claude_allowed(srv, mind: str, model: str) -> bool:
    """May this background mind call the claude-agent rung `model`? Only
    when the operator explicitly picked that rung for it (#384 relaxed)."""
    return bool(model) and (getattr(srv, "_minds_picked", None) or {}).get(mind) == model


def pickable(srv) -> list[str] | None:
    """The models a mind may be switched to: the chat-capable ones the
    backend serves plus the claude-agent rungs on the router. None = the
    seat could not say (no validation)."""
    out = []
    try:
        from .lanes import _chat_capable_models
        out = list(_chat_capable_models(getattr(srv, "backend", "")) or [])
    except Exception:
        out = []
    try:
        from . import claude_agent as _CA
        recs = getattr(getattr(srv, "router", None), "records", None) or {}
        out += [n for n, r in recs.items()
                if getattr(r, "backend", "") == _CA.PROVIDER and n not in out]
    except Exception:
        pass
    return out or None


def _set(srv, mind: str, model: str) -> None:
    srv.__dict__.setdefault("_minds_picked", {})[mind] = model
    if mind == "loops":
        runner = getattr(srv, "_loop_runner", None)
        if runner is not None:
            runner.model = model
    elif mind == "cortex":
        srv.idle_cortex_model = model
        judge = getattr(srv, "_claim_judge", None)
        if judge is not None and hasattr(judge, "model"):
            judge.model = model


def apply(srv, loops: str | None = None, cortex: str | None = None,
          think: str | None = None, known=None, persist: bool = True,
          mirror: bool = True, loops_auth: str | None = None,
          cortex_auth: str | None = None) -> dict:
    """Set either mind's model, its credentials and/or the think dial;
    ValueError on a bad name (or one not in `known`, when the caller passes
    the models it sees), with nothing set."""
    want = {k: v for k, v in (("loops", loops), ("cortex", cortex)) if v is not None}
    auths = {k: v for k, v in (("loops_auth", loops_auth), ("cortex_auth", cortex_auth))
             if v is not None}
    for k, v in auths.items():
        if v not in AUTH_CHOICES:
            raise ValueError(f"{k}: one of {'|'.join(AUTH_CHOICES)}, not: {v!r}")
    for mind, model in want.items():
        if not isinstance(model, str) or not _NAME.match(model):
            raise ValueError(f"{mind}: not a model name: {model!r}")
        if known is not None and model not in known:
            raise ValueError(f"{mind}: membrane doesn't see {model!r} on its backends")
    if think is not None and (not isinstance(think, str) or think not in _THINK):
        raise ValueError(f"think: one of {'|'.join(_THINK)}, not: {think!r}")
    for mind, model in want.items():
        _set(srv, mind, model)
        if mirror:                       # the active-model entity follows the operator's switch
            try:
                from . import active_models as _AM
                _AM.mirror(srv, mind, model)
            except Exception:
                pass
    for k, v in auths.items():
        srv.__dict__.setdefault("_minds_auth", {})[k[:-len("_auth")]] = v
    if think is not None:                # the dial as the seat holds it (runtime readers read per call)
        from .rag import think_value
        srv.think = think_value(think)
    if persist and (want or auths or think is not None):
        path = getattr(srv, "_models_path", None)
        if path:
            saved = {}
            try:
                saved = json.load(open(path)) or {}
            except (OSError, ValueError):
                pass
            saved.update(want)
            saved.update(auths)
            if think is not None:        # the file speaks names, the seat speaks the value
                saved["think"] = think
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({k: saved[k] for k in MINDS + _AUTH_KEYS + ("think",) if k in saved}, f)
            os.replace(tmp, path)
    return read(srv)


def load(srv, cli_think: str | None = None) -> dict:
    """Boot: reapply the saved picks (nothing saved -> the boot's own). An
    explicit CLI think (on/off) beats the file; auto (the default) defers."""
    path = getattr(srv, "_models_path", None)
    try:
        saved = json.load(open(path)) if path else {}
    except (OSError, ValueError):
        saved = {}
    picks = {k: saved[k] for k in MINDS + _AUTH_KEYS if isinstance(saved.get(k), str)}
    if picks:
        try:
            apply(srv, persist=False, **picks)
        except ValueError:
            pass
    think = cli_think if cli_think in ("on", "off") else saved.get("think")
    if isinstance(think, str) and think in _THINK:
        try:
            apply(srv, persist=False, think=think)
        except ValueError:
            pass
    return read(srv)
