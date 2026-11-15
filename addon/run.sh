#!/bin/sh
# babyseat add-on: the container's ENTRYPOINT (#1143). The seat `cliffvm serve` is the add-on's main process: `exec`, so the Supervisor's stop signal (SIGTERM, through Docker's
# init: config.yaml leaves `init` at its default true because this image does not use the base image's s6 /init) reaches the seat, which ends cleanly.
#   1. options.sh turns /data/options.json into the launch line and the environment (secrets read from the options file, never printed); a bad option stops the start here.
#   2. announce.sh, in the background (re-parented to the init, which reaps it), waits for the door with THIS start's admin token and POSTs the Supervisor discovery.
#   3. exec the seat: data in /data (the add-on's persistent folder), the door on 0.0.0.0:4096; Home Assistant's config folder is mounted read-only at /config, where the
#      history cutover looks for the 0.7 seat's store (/config/membrane/.membrane).
set -eu
here=/opt/babyseat-addon
plan=$("$here/options.sh" /data/options.json) || exit 1
eval "$plan"
# the fingerprint of the previous start's token (the seat replaces the file at start): announce.sh waits for a different one
old=""
[ ! -f /data/admin.token ] || old=$(cksum < /data/admin.token)
( "$here/announce.sh" /data/admin.token "$old" & )
exec "$@"
