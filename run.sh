#!/usr/bin/env bash
# membrane launcher -- one entry point. Default = TUI + proxy, zero flags.
#
#   ./run.sh                TUI, and starts a proxy on :11435 if one isn't
#                           already up; tears it down when the TUI exits (ctrl-C
#                           OR a clean quit -- the proxy we started is killed)
#   ./run.sh tui            same as above (explicit alias)
#   ./run.sh restart        kill any proxy on the port, then start fresh (TUI+proxy).
#                           Use this to pick up code changes -- ./run.sh alone only
#                           attaches if a proxy is already up.
#   ./run.sh proxy          just the proxy, foreground (no TUI)
#   ./run.sh opencode       field-backed opencode: starts the OpenAI-compatible
#                           RAG proxy on :11500 and opens opencode pointed at it
#                           (math/state answered internally; model work proxied
#                           to Ollama; the selected model is the cortex's too).
#                           Flags forward verbatim; opencode's own args go after
#                           `--`: ./run.sh opencode --model qwen3:14b -- --prompt x
#   ./run.sh anthropic      TUI+proxy in --anthropic mode (Claude Code front door,
#                           observer-only by default; set ANTHROPIC_API_KEY and
#                           point Claude Code at http://127.0.0.1:11435). Extra
#                           --anthropic-mode flags route to the proxy.
#   ./run.sh review         print what the field learned (training-loop surface)
#   ./run.sh demo           in-process end-to-end loop (no chat model needed)
#
# The live model-judge is ON by default (fires only on a demotion, never per
# turn). --no-judge disables it. In the default/TUI mode extra args are routed
# to the right process by flag, so you can mix freely -- proxy flags go to the
# proxy, --refresh goes to the TUI, --field/--audit go to both:
#   ./run.sh --listen 11440 --no-judge --judge-model granite3.3:8b --refresh 250
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
  opencode) shift; exec "$PY" -m membrane.opencode "$@" ;;
  anthropic) shift; set -- --anthropic "$@" ;;   # fall through with --anthropic prepended
  review) shift; exec "$PY" -m membrane.review "$@" ;;
  demo)   shift; exec "$PY" experiments/proxy_demo.py "$@" ;;
  tui)    shift ;;            # then fall through to the TUI+proxy path
  restart) shift              # kill whatever's on the port, then start fresh (TUI+proxy)
           rport=11435
           prev=""
           for a in "$@"; do
             if [ "$prev" = "--listen" ]; then rport="$a"; break; fi
             prev="$a"
           done
           lpids=$(lsof -nP -iTCP:"$rport" -sTCP:LISTEN -t 2>/dev/null || true)
           if [ -n "$lpids" ]; then
             echo "[run.sh] restart: killing proxy on :$rport (pid $lpids)" >&2
             kill $lpids 2>/dev/null || true
             sleep 0.5
           else
             echo "[run.sh] restart: nothing on :$rport, starting fresh" >&2
           fi
           ;;                # fall through to the TUI+proxy path
  -h|--help|help) sed -n '4,22p' "$0"; exit 0 ;;
esac

# ---- default / `tui`: route args by flag, then proxy+tui --------------------
proxy_args=()
tui_args=()
while [ $# -gt 0 ]; do
  a="$1"
  case "$a" in
    --judge|--no-judge|--no-neg-followup|--anthropic|--observer|--absorb|\
    --no-vague-filter|--variety-gate|--variety-temp) proxy_args+=("$a"); shift ;;
    --listen|--backend|--sim-min|--consensus-min|--dedup-sim|--demote-threshold|\
    --judge-model|--judge-timeout|--judge-backend|--vague-min-tokens|\
    --temp-driver|--temp-tau|--sim-min-floor|--nudge-sigma|--nudge-seed|\
    --system|--warmup|--warmup-model|--warmup-seed|--tag|--cells)
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

# tear down the proxy we started when the TUI exits (any reason). Guarded with
# || true so a no-op kill (process already gone) doesn't trip `set -e` and
# corrupt the exit code -- the EXIT trap fires AFTER `exit "$tui_rc"`.
if [ -n "$started_pid" ]; then
  trap "kill $started_pid 2>/dev/null || true; wait $started_pid 2>/dev/null || true" EXIT INT TERM
fi

# Run the TUI as a child (NOT exec) so the trap above survives a clean quit --
# exec would replace the shell and discard the trap, orphaning the proxy we
# started. ctrl-C also reaches the proxy directly (same process group).
"$PY" -m membrane.tui "${tui_args[@]}"
tui_rc=$?
# explicit teardown after a clean TUI exit (the EXIT trap covers signal paths)
if [ -n "$started_pid" ]; then
  kill "$started_pid" 2>/dev/null || true
  wait "$started_pid" 2>/dev/null || true
fi
exit "$tui_rc"