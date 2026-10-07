#!/system/bin/sh
# brain-watch - agdroid supervises the brain phone (ThinkPhone, membrane
# serve on 192.168.1.112:4096) over the USB adb link.
#
# Wiring (2026-10-07): the ThinkPhone sits on agdroid's powered hub
# (22b8:2e81, adb serial ZY22JPNL78; key = /data/local/tmp/adbhome). The
# ThinkPhone is NOT rooted: inside it, Termux runs membrane-supervise.sh
# (respawns `membrane serve`), armed by Termux:Boot and by ~/.profile on
# every Termux session. This watch is the outer layer: when the serve
# stays dead for 3 checks (beyond the inner supervisor's own 120 s boot
# grace) and the backend (the mac's ollama) is up, it force-stops and
# relaunches Termux over adb -> a fresh session -> ~/.profile re-arms the
# supervisor. 10 min cooldown between kicks. Never touches boat hardware.
#
# The ThinkPhone has a SECURE lock screen and no root: a Termux activity
# launched behind the keyguard opens no session (verified 2026-10-07), and
# after a reboot Termux's data stays encrypted until the first unlock. So
# the kick fires ONLY while the ThinkPhone is unlocked; while locked the
# watch just reports (killing Termux then would turn "membrane down" into
# "Termux down"). State: retained MQTT `agdroid/brain/health` JSON
# (up | down | needs_unlock | backend_down | usb_missing) via the
# loopback-anon broker (127.0.0.1:2883), best-effort.
#
# Disarm: `su -c 'pkill -f brain-watc[h]'` then delete this service file.

LOG=/data/local/tmp/brain-watch.log

if [ "$1" != "--bg" ]; then
    nohup sh "$0" --bg >>"$LOG" 2>&1 &
    exit 0
fi

SERIAL=ZY22JPNL78
SERVE=http://192.168.1.112:4096/global/health
BACKEND=http://192.168.1.107:11434/api/version
T=/data/data/com.termux/files/usr/bin
export HOME=/data/local/tmp/adbhome
ADB="$T/adb -s $SERIAL"
COOLDOWN=600

say() { echo "$(date '+%F %T') brain-watch: $*"; }
MPUB=""
for p in $T/mosquitto_pub /system/bin/mosquitto_pub; do
    [ -x "$p" ] && MPUB="$p" && break
done
last_state=""
publish() {                               # $1 = state; on change only
    [ "$1" = "$last_state" ] && return 0
    last_state="$1"
    say "state -> $1"
    [ -n "$MPUB" ] || return 0
    "$MPUB" -h 127.0.0.1 -p 2883 -r -t agdroid/brain/health \
        -m "{\"state\":\"$1\",\"ts\":$(date +%s)}" >/dev/null 2>&1
}
unlocked() {
    $ADB shell 'dumpsys window | grep -q "isKeyguardShowing=true" && exit 1; dumpsys power | grep -q "mWakefulness=Awake"' >/dev/null 2>&1
}
code() { "$T/curl" -s -m 10 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null; }

# one instance only
for p in $(pgrep -f "brain-watc[h].sh --bg"); do
    [ "$p" != "$$" ] && { say "already running ($p)"; exit 0; }
done

sleep 90                                  # let the boot settle (adb, wifi)
say "up (pid $$)"
fails=0; last_kick=0
while true; do
    c=$(code "$SERVE")
    if [ "$c" = 401 ] || [ "$c" = 200 ]; then
        [ $fails -gt 0 ] && say "serve back ($c)"
        fails=0
        publish up
    else
        fails=$((fails + 1))
        say "serve check failed (http=$c, $fails/3)"
    fi
    if [ $fails -ge 3 ]; then
        now=$(date +%s)
        if [ "$(code "$BACKEND")" != 200 ]; then
            publish backend_down          # the mac's ollama: nothing to kick
        elif ! $ADB get-state >/dev/null 2>&1; then
            publish usb_missing
            $T/adb start-server >/dev/null 2>&1
        elif ! unlocked; then
            publish needs_unlock
        elif [ $((now - last_kick)) -ge $COOLDOWN ]; then
            say "kicking Termux on the ThinkPhone (unlocked)"
            $ADB shell 'am force-stop com.termux; sleep 2; am start -n com.termux/.app.TermuxActivity' 2>&1
            last_kick=$now
            fails=0
        else
            publish down
        fi
    fi
    sleep 60
done
