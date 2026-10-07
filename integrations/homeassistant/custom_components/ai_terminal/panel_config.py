"""The panel contract builders (pure functions -- unit-testable).

THE CONTRACT (ha-panel-custom.ts:94, HA frontend 20260128.6): the custom
panel loader reads ``panel.config._panel_custom`` and expects an element
config with ``name`` (the custom element's tag) + ``module_url`` (its ES
module source); without the nesting ``config._panel_custom`` is undefined
and the loader crashes with "can't access property html_url". A
programmatic registration IS the yaml loader here.
"""
from __future__ import annotations

PANEL_URL_PATH = "ai-terminal"
PANEL_ELEMENT = "ai-terminal-panel"


def panel_custom_config(domain: str = "ai_terminal") -> dict:
    """The config dict async_register_built_in_panel must receive.

    Shape (pinned by tests):
      {"_panel_custom": {"name": <element>, "module_url": <panel js>,
                         "trust_external": True}}
    """
    return {"_panel_custom": {
        "name": PANEL_ELEMENT,
        "module_url": f"/api/{domain}/www/panel.js",
        # same-origin (HA's own host serves /api/ai_terminal/www) -- the
        # trust prompt is bypassed legitimately
        "trust_external": True,
    }}


def static_paths(domain: str = "ai_terminal") -> list[tuple[str, str]]:
    """The (url_path, dir_name) static mounts the integration serves."""
    return [(f"/api/{domain}/www", "www")]