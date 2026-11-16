#!/bin/sh
# babyseat container entrypoint: `cliffvm serve` as PID 1 (exec: docker stop's SIGTERM lands on the seat, which ends cleanly). The defaults are the container's, not the seat's:
#   --port 4096 --bind 0.0.0.0 (the published port reaches the container from docker's bridge, not loopback) --data-dir /data (the volume).
# Anything after the image name is passed on and, coming last, wins:  docker run chedim/babyseat --port 5000 --fact operator=ann
# The claude CLI is not in the image: CLAUDE_BIN names a mounted one (see the Dockerfile); ANTHROPIC_API_KEY (the default auth) is read by the seat from the environment.
set -eu
case "${1:-}" in
    --version|-V) exec /opt/babyseat/bin/cliffvm --version ;;
    sh|bash|env|cat|ls) exec "$@" ;;            # `docker run ... sh` for a look inside
esac
exec /opt/babyseat/bin/cliffvm serve --port 4096 --bind 0.0.0.0 --data-dir /data "$@"
