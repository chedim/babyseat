"""Membrane for Home Assistant: constants."""

DOMAIN = "membrane"

# the config entry: where the seat is and the token that opens its door. The password field of the old component's Basic pair carries
# the token (cliffvm reads a Basic password as the JWT: docs/auth.md), so the entry keeps the shape the old connect form made.
CONF_SERVE_URL = "serve_url"
CONF_SERVE_USER = "serve_user"
CONF_SERVE_PASS = "serve_pass"
DEFAULT_SERVE_USER = "opencode"

POLL_S = 60                      # the seat's health, as often as the old component polled the seat (decision 11 of docs/hass-plan.md)
HEALTH_TIMEOUT_S = 10            # the config flow's check (the old form's)
SESSION_TIMEOUT_S = 15           # creating a session (the old conversation's)
TURN_TIMEOUT_S = 600.0           # a model turn may run minutes (the old conversation's REQUEST_TIMEOUT_S)
SESSION_TITLE_PREFIX = "hass-"   # a seat session per Home Assistant conversation: "hass-<conversation id>"
