#!/bin/sh
# membrane container entrypoint: map env -> seat flags, then exec the seat
# as PID 1 (no wrapper child, so docker stop's SIGTERM lands on the seat and
# its graceful teardown runs).
#
#   MEMBRANE_BACKEND      -> --backend      (default http://host.docker.internal:11434)
#   MEMBRANE_MODEL        -> --model        (unset: the seat picks)
#   MEMBRANE_EMBED_MODEL  -> --embed-model  (unset: the seat picks; chat needs one)
#
# The ports are pinned (--listen 11500 --serve-port 4096 --serve-host
# 0.0.0.0): outside a container the seat walks up to the next free port,
# which would break the image's EXPOSE/HEALTHCHECK. Remap with -p instead.
#
# Seat flags go right after the command, so they always precede a `--`
# passthrough, and flags given in the CMD come later and win. Non-seat
# commands (doctor, stop, review, --help, ...) run bare.
set -e

cmd="${1:-standalone}"
[ $# -gt 0 ] && shift

case "$cmd" in
    standalone|serve|opencode)
        if [ -n "${MEMBRANE_EMBED_MODEL:-}" ]; then
            set -- --embed-model "$MEMBRANE_EMBED_MODEL" "$@"
        fi
        if [ -n "${MEMBRANE_MODEL:-}" ]; then
            set -- --model "$MEMBRANE_MODEL" "$@"
        fi
        set -- \
            --backend "${MEMBRANE_BACKEND:-http://host.docker.internal:11434}" \
            --listen 11500 --serve-port 4096 --serve-host 0.0.0.0 \
            "$@"
        ;;
esac

exec membrane "$cmd" "$@"
