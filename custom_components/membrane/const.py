"""Membrane for Home Assistant: constants."""

DOMAIN = "membrane"

# the config entry: where the seat is and the token that opens its door. The password field of the old component's Basic pair carries
# the token (cliffvm reads a Basic password as the JWT: docs/auth.md), so the entry keeps the shape the old connect form made.
CONF_SERVE_URL = "serve_url"
CONF_SERVE_USER = "serve_user"
CONF_SERVE_PASS = "serve_pass"
DEFAULT_SERVE_USER = "opencode"
DEFAULT_PORT = 4096              # `cliffvm serve`'s port: the add-on's when its announcement names none, and the inside seat's when it is free
DOCS_URL = "https://github.com/chedim/membrane/blob/main/docs/home-assistant.md"   # the forms' link (a placeholder: hassfest refuses a URL in a string)

POLL_S = 60                      # the seat's health, as often as the old component polled the seat (decision 11 of docs/hass-plan.md)
HEALTH_TIMEOUT_S = 10            # the config flow's check (the old form's)
SESSION_TIMEOUT_S = 15           # creating a session (the old conversation's)
TURN_TIMEOUT_S = 600.0           # a model turn may run minutes (the old conversation's REQUEST_TIMEOUT_S)
SESSION_TITLE_PREFIX = "hass-"   # a seat session per Home Assistant conversation: "hass-<conversation id>"

# THE SEAT'S USE OF HOME ASSISTANT (slice 2): the component pulls the calls the model wrote (docs/home-assistant.md, "The seat uses Home Assistant")
CALL_PULL_S = 2.0                # how often the pending calls are asked for: the old component's batch flush (FLUSH_S, decision 11 of docs/hass-plan.md), kept as it was
CATALOG_S = POLL_S               # how often the catalog is rebuilt and, when it changed, pushed: the old component's one minute
STATE_MAX = 255                  # Home Assistant's own limit on a state's length (a longer one is refused by HA), not ours

# THE USER A CALL RUNS AS: the old component's hidden system user (non-admin, local only, group USER); its id is kept in the entry
HA_USER_NAME = "Membrane"
CONF_HA_USER_ID = "ha_user_id"

# A SEAT RUN INSIDE HOME ASSISTANT (operator 2026-11-15: the default path again; seat_runner.py runs it, seat_options.py maps its settings)
CONF_MODE = "mode"
MODE_INSIDE = "inside"           # the entry runs its own seat; an entry with no mode connects to a seat (serve_url + serve_pass)
CONF_PROVIDER = "provider"       # anthropic | openai | ollama (seat_options.PROVIDERS)
CONF_MODEL = "model"
CONF_ANTHROPIC_KEY = "anthropic_api_key"
CONF_API_KEY = "api_key"         # provider openai's key (optional)
CONF_BASE_URL = "base_url"       # provider openai's endpoint root
CONF_OLLAMA_URL = "ollama_url"
CONF_SEAT_NAME = "seat_name"
CONF_SERVE_PORT = "serve_port"   # the seat's port on 127.0.0.1, kept so its URL is stable
CONF_RELEASE_BASE = "release_base"   # where the release tarballs are (tests and the smoke run set it; else seat_runner.RELEASE_BASE)
SEAT_KEYS = (CONF_PROVIDER, CONF_MODEL, CONF_ANTHROPIC_KEY, CONF_API_KEY, CONF_BASE_URL, CONF_OLLAMA_URL, CONF_SEAT_NAME)   # the settings the options flow changes
SEAT_DIR = "membrane"            # <config>/membrane: the seat's data folder (0.7.53's SEAT_DIR: its old store is <config>/membrane/.membrane)
CONF_BACKEND = "backend"         # 0.7.53's inside entry: the Ollama URL its seat thought with (migrated to ollama_url)
ISSUE_SEAT_STOPPED = "seat_stopped"
