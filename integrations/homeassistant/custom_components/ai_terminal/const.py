"""ai_terminal constants."""

DOMAIN = "ai_terminal"

# ---- the membrane serve lane (the dashboard = a web chat client) ----
# The panel speaks membrane's native serve surface (the opencode-compatible
# REST/SSE of membrane/server.py) on the BRAIN phone, PROXIED through HA's
# own server (the serve credential stays server-side; HA is the one origin
# reachable from the LAN and from outside via the chegate tunnel). membrane
# never runs on agdroid (RAM). Empty serve_pass = lane off.
CONF_SERVE_URL = "serve_url"             # e.g. http://192.168.1.112:4096
DEFAULT_SERVE_URL = "http://192.168.1.112:4096"
CONF_SERVE_USER = "serve_user"           # membrane serve basic auth user
DEFAULT_SERVE_USER = "opencode"
CONF_SERVE_PASS = "serve_pass"           # from the brain's serve-auth.json
DEFAULT_SERVE_PASS = ""
PROXY_BASE = "/api/ai_terminal/serve"
PROXY_TIMEOUT_S = 1800.0                 # a model turn may run minutes

# ---- voice in the field UI (2026-10-10): the browser's recordings go to the
# whisper-server on the brain phone (the one calld uses), through HA
CONF_STT_URL = "stt_url"
DEFAULT_STT_URL = "http://192.168.1.112:11510/inference"
