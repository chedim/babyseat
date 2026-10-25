"""Membrane for Home Assistant: constants."""

DOMAIN = "membrane"

# the setup form (config_flow.py)
CONF_MODE = "mode"
MODE_INSIDE = "inside"          # HA runs the seat itself (supervisor.py)
MODE_CONNECT = "connect"        # a seat elsewhere (the boat's ThinkPhone, a server)
CONF_BACKEND = "backend"        # Ollama URL the seat thinks with
CONF_MODEL = "model"
CONF_EMBED_MODEL = "embed_model"
CONF_SERVE_URL = "serve_url"    # connect mode: the seat's serve surface
CONF_SERVE_USER = "serve_user"
CONF_SERVE_PASS = "serve_pass"
# options
OPT_LANDING = "landing"         # membrane home is the landing page (Agatha's way)
OPT_STT_URL = "stt_url"         # a whisper-server for speech; "" = the browser listens itself
OPT_LOOPS = "loops"             # the seat's watch-loop pool
OPT_HA_ACCESS = "ha_access"     # the seat may use Home Assistant (ha_access.py)
OPT_HA_BASE_URL = "ha_base_url" # connect mode: HA's address as THE SEAT sees it ("" = HA's own)

DEFAULT_BACKEND = "http://localhost:11434"
DEFAULT_SERVE_USER = "opencode"
DEFAULT_NAME = "Bob"            # until the seat says its own: naming.DEFAULT, the seat's default
DEFAULT_LOOPS = 1               # an HA box is smaller than the boat's brain phone

PROXY_TIMEOUT_S = 1800.0        # a model turn may run minutes
SEAT_DIR = "membrane"           # <config>/membrane: the seat's data, home, log
