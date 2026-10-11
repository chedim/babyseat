"""THE MINDS' MODELS (2026-10-08): the background mechanisms' models, set
at runtime (the chat's picker only sets one session's model).

  loops   the watch loops' turns (the serve's loop runner model)
  cortex  the idle cortex: the thinker, the dreamer, the claim / A-B judges

Applied live, saved beside the field (models.json), reapplied at boot.
"""
from __future__ import annotations

import json
import os
import re

MINDS = ("loops", "cortex")
_NAME = re.compile(r"^[\w.:/@~+-]{1,120}$")


def read(srv) -> dict:
    runner = getattr(srv, "_loop_runner", None)
    return {"loops": str(getattr(runner, "model", "") or ""),
            "cortex": str(getattr(srv, "idle_cortex_model", "") or ""),
            "seat_default": str(getattr(srv, "default_seat_model", "") or "")}


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
          known=None, persist: bool = True, mirror: bool = True) -> dict:
    """Set either or both; ValueError on a bad name (or one not in `known`,
    when the caller passes the models it sees)."""
    want = {k: v for k, v in (("loops", loops), ("cortex", cortex)) if v is not None}
    for mind, model in want.items():
        if not isinstance(model, str) or not _NAME.match(model):
            raise ValueError(f"{mind}: not a model name: {model!r}")
        if known is not None and model not in known:
            raise ValueError(f"{mind}: membrane doesn't see {model!r} on its backends")
    for mind, model in want.items():
        _set(srv, mind, model)
        if mirror:                       # the active-model entity follows the operator's switch
            try:
                from . import active_models as _AM
                _AM.mirror(srv, mind, model)
            except Exception:
                pass
    if persist and want:
        path = getattr(srv, "_models_path", None)
        if path:
            saved = {}
            try:
                saved = json.load(open(path)) or {}
            except (OSError, ValueError):
                pass
            saved.update(want)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({k: saved[k] for k in MINDS if k in saved}, f)
            os.replace(tmp, path)
    return read(srv)


def load(srv) -> dict:
    """Boot: reapply the saved picks (nothing saved -> the boot's own)."""
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
    return read(srv)
