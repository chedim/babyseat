#!/data/data/com.termux/files/usr/bin/sh
# membrane-supervise.sh — keeps `membrane serve` (0.0.0.0:4096, + the /v1 seat
# on :11500) alive on the ThinkPhone. Started by ~/.termux/boot/01-tp-serve.sh.
# Backend = the mac's ollama (glm cloud models ride its `ollama signin`).
BACKEND=http://192.168.1.107:11434
MODEL=glm-5.3-flash:cloud   # flash: the full glm-5.3 burned the ollama cloud credits (2026-10-07)
LOCK=$HOME/.membrane-supervise.pid
LOG=$HOME/membrane/supervise.log

if [ -f "$LOCK" ]; then
  # the pid-recycle guard (the seat's own boot lock has the same rule): a
  # dead supervisor's pid recycled by an unrelated process must not sit in
  # the lock forever -- it made every re-arm silently no-op "already
  # running" (the 2026-10-13 exhibit: the phone's Android killed the
  # supervisor, the pid was recycled, and nothing restarted the serve for
  # hours). The holder counts only if it IS a supervisor.
  _lp="$(cat "$LOCK" 2>/dev/null)"
  _lcmd="$(tr '\0' ' ' < "/proc/$_lp/cmdline" 2>/dev/null)"
  case "$_lcmd" in *membrane-supervise*) echo "supervisor already running (pid $_lp)"; exit 0 ;; esac
  echo "stale lock (pid $_lp, now $(basename "${_lcmd:-gone}")): taking it over" >> "$LOG"
  rm -f "$LOCK"
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
    # OFFLINE MODE (2026-10-11, the walks): the seat starts without the Mac --
    # it runs its scripts, embeds on the phone, and its minds wait
    backend_up || log "backend $BACKEND down: starting the seat offline"
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
