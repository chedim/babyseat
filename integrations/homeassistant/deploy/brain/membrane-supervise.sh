#!/data/data/com.termux/files/usr/bin/sh
# membrane-supervise.sh — keeps `membrane serve` (0.0.0.0:4096, + the /v1 seat
# on :11500) alive on the ThinkPhone. Started by ~/.termux/boot/01-tp-serve.sh.
# Backend = the mac's ollama (glm cloud models ride its `ollama signin`).
BACKEND=http://192.168.1.107:11434
MODEL=glm-5.3-flash:cloud   # flash: the full glm-5.3 burned the ollama cloud credits (2026-10-07)
LOCK=$HOME/.membrane-supervise.pid
LOG=$HOME/membrane/supervise.log

if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  echo "supervisor already running (pid $(cat "$LOCK"))"; exit 0
fi
echo $$ > "$LOCK"

# healthy = the serve answers HTTP on the LAN address (401 without auth is
# an answer; a bound-but-hung serve is not)
serve_up() {
  python - <<PY 2>/dev/null
import sys, urllib.request, urllib.error
try:
    urllib.request.urlopen("http://127.0.0.1:4096/global/health", timeout=10)
except urllib.error.HTTPError:
    pass
except Exception:
    sys.exit(1)
PY
}
backend_up() {
  python -c "import urllib.request;urllib.request.urlopen('$BACKEND/api/version',timeout=5)" 2>/dev/null
}
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
# the old seat must be GONE before the new one boots: a SIGTERM'd seat
# shuts down gracefully and still holds :4096/:11500 for a while -- a
# relaunch 3s later booted on :4097/:11501, the health probe (:4096) never
# saw it, and 120s on the healthy seat was killed again (2026-10-08). The
# [.] keeps pkill off any shell whose command line merely quotes the name.
SEAT_RE='membrane[.](cli serve|opencode)'
stop_serve() {
  pkill -f "$SEAT_RE"
  i=0
  while [ $i -lt 30 ] && pgrep -f "$SEAT_RE" >/dev/null; do
    sleep 1; i=$((i+1))
  done
  if pgrep -f "$SEAT_RE" >/dev/null; then
    log "old seat still alive after 30s: SIGKILL"
    pkill -9 -f "$SEAT_RE"; sleep 2
  fi
}

log "supervisor up (pid $$, ppid $PPID)"
# the death's fingerprint: a catchable signal or a plain exit logs here;
# NO line before the next 'supervisor up' = SIGKILL from outside
trap 'log "supervisor exit (rc $?)"' EXIT
for s in INT TERM QUIT PIPE USR1 USR2 ALRM; do
  trap "log 'supervisor got SIG$s'; exit 1" $s
done
while true; do
  if ! serve_up; then
    if ! backend_up; then
      log "backend $BACKEND down; waiting"
      sleep 30; continue
    fi
    log "serve down: (re)starting membrane"
    stop_serve
    cd "$HOME/membrane" && nohup python -m membrane.cli serve \
      --backend "$BACKEND" --embed-model mxbai-embed-large --model "$MODEL" \
      --serve-host 0.0.0.0 \
      >> "$HOME/membrane/serve.log" 2>&1 &
    # boot takes ~40-60s (router seeding, field load): give it room
    i=0; while [ $i -lt 24 ] && ! serve_up; do sleep 5; i=$((i+1)); done
    serve_up && log "serve up" || log "serve did not come up in 120s"
  fi
  sleep 30
done
