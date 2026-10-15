"""THE MINDS' MODELS (2026-10-08): the background mechanisms' models, set
at runtime (the chat's picker only sets one session's model).

  loops   the watch loops' turns (the serve's loop runner model)
  cortex  the idle cortex: the thinker, the dreamer, the claim / A-B judges

Applied live, saved beside the field (models.json), reapplied at boot. The
think dial rides the same surface (`think`: on|off|auto -- 2026-10-15,
the switch in the chat menu): `on`/`off` inject `think` into the seats'
bodies, `auto` forwards verbatim (a non-thinking card never learns a
think:true it would 400 on).
"""
from __future__ import annotations

import json
import os
import re

MINDS = ("loops", "cortex")
_THINK = ("on", "off", "auto")
_NAME = re.compile(r"^[\w.:/@~+-]{1,120}$")


def read(srv) -> dict:
    # the dial as the seat holds it right now (boot default auto = None = verbatim)
    think = getattr(srv, "think", None)   # the seat speaks the value (True/False/None)
    return {"loops": str(getattr(getattr(srv, "_loop_runner", None), "model", "") or ""),
            "cortex": str(getattr(srv, "idle_cortex_model", "") or ""),
            "seat_default": str(getattr(srv, "default_seat_model", "") or ""),
            "think": "on" if think is True else "off" if think is False else "auto"}


def _set(srv, mind: str, model: str) -> None:
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
          mirror: bool = True) -> dict:
    """Set either mind's model and/or the think dial; ValueError on a bad
    name (or one not in `known`, when the caller passes the models it sees)."""
    want = {k: v for k, v in (("loops", loops), ("cortex", cortex)) if v is not None}
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
    if think is not None:                # the dial as the seat holds it (runtime readers read per call)
        from .rag import think_value
        srv.think = think_value(think)
    if persist and (want or think is not None):
        path = getattr(srv, "_models_path", None)
        if path:
            saved = {}
            try:
                saved = json.load(open(path)) or {}
            except (OSError, ValueError):
                pass
            saved.update(want)
            if think is not None:        # the file speaks names, the seat speaks the value
                saved["think"] = think
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({k: saved[k] for k in MINDS + ("think",) if k in saved}, f)
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
    picks = {k: saved[k] for k in MINDS if isinstance(saved.get(k), str)}
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
