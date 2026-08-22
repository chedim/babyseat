#!/usr/bin/env bash
# membrane launcher -- one entry point. Default = TUI + proxy, zero flags.
#
#   ./run.sh                TUI, and starts a proxy on :11435 if one isn't
#                           already up; tears it down when you quit the TUI
#   ./run.sh tui            same as above (explicit alias)
#   ./run.sh proxy          just the proxy, foreground (no TUI)
#   ./run.sh review         print what the field learned (training-loop surface)
#   ./run.sh demo           in-process end-to-end loop (no chat model needed)
#
# In the default/TUI mode extra args are routed to the right process by flag,
# so you can mix freely -- proxy flags go to the proxy, --refresh goes to the
# TUI, --field/--audit go to both (so they watch the same files):
#   ./run.sh --listen 11440 --judge --judge-model granite3.3:8b --refresh 250
# For proxy/review/demo, args are forwarded verbatim.

set -eo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# ---- single-process subcommands: forward raw args ---------------------------
case "${1:-}" in
  proxy)  shift; exec "$PY" -m membrane.proxy "$@" ;;
  review) shift; exec "$PY" -m membrane.review "$@" ;;
  demo)   shift; exec "$PY" experiments/proxy_demo.py "$@" ;;
  tui)    shift ;;            # then fall through to the TUI+proxy path
  -h|--help|help) sed -n '3,16p' "$0"; exit 0 ;;
esac

# ---- default / `tui`: route args by flag, then proxy+tui --------------------
proxy_args=()
tui_args=()
while [ $# -gt 0 ]; do
  a="$1"
  case "$a" in
    --judge) proxy_args+=("$a"); shift ;;
    --listen|--backend|--sim-min|--consensus-min|--demote-threshold|\
    --judge-model|--judge-timeout|--judge-backend)
      proxy_args+=("$a"); shift
      if [ $# -gt 0 ]; then proxy_args+=("$1"); shift; fi ;;
    --refresh)
      tui_args+=("$a"); shift
      if [ $# -gt 0 ]; then tui_args+=("$1"); shift; fi ;;
    --field|--audit)
      proxy_args+=("$a"); tui_args+=("$a"); shift
      if [ $# -gt 0 ]; then proxy_args+=("$1"); tui_args+=("$1"); shift; fi ;;
    *) proxy_args+=("$a"); tui_args+=("$a"); shift ;;   # unknown -> both (module rejects)
  esac
done

# which port will the proxy bind? (default 11435)
port=11435
for ((i=0; i<${#proxy_args[@]}; i++)); do
  if [ "${proxy_args[i]}" = "--listen" ] && (( i+1 < ${#proxy_args[@]} )); then
    port="${proxy_args[i+1]}"; break
  fi
done

port_open() {
  "$PY" - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket(); s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
PY
}

started_pid=""
if ! port_open "$port"; then
  # no proxy up on that port -- start one, logs to membrane-proxy.log
  "$PY" -m membrane.proxy "${proxy_args[@]}" >membrane-proxy.log 2>&1 &
  started_pid=$!
  for _ in $(seq 1 40); do
    if port_open "$port"; then break; fi
    kill -0 "$started_pid" 2>/dev/null || { started_pid=""; break; }
    sleep 0.25
  done
  if ! port_open "$port"; then
    echo "warning: proxy did not come up on :$port -- see membrane-proxy.log" >&2
    echo "         (the TUI still works; it shows the last field state)" >&2
  fi
fi

# tear down the proxy we started when the TUI exits (any reason)
if [ -n "$started_pid" ]; then
  trap "kill $started_pid 2>/dev/null; wait $started_pid 2>/dev/null" EXIT INT TERM
fi

exec "$PY" -m membrane.tui "${tui_args[@]}"