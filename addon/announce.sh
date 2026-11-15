#!/bin/sh
# babyseat add-on: tell Home Assistant where the seat is (#1143). Usage: announce.sh <admin.token file> [<cksum of the token file before this start>]
# run.sh starts it in the background before it execs the seat, at EVERY start (the seat writes a new admin token at every start, so the announcement is renewed with it).
#   1. wait for the door: GET http://127.0.0.1:4096/global/health with the token of the file must answer 200, and the file must not be the previous start's (its cksum differs from
#      the one run.sh noted before the seat started). Bounded: TRIES checks, one second apart; then it gives up and says so.
#   2. POST http://supervisor/discovery  {"service": "membrane", "config": {"host": <this container's host name>, "port": 4096, "token": <the admin token>}}
#      with the add-on's SUPERVISOR_TOKEN (the Supervisor's discovery API: contributors.home-assistant.io/apps/guides/services-discovery/). config.yaml lists `membrane` under `discovery`.
# No secret is ever on a command line or in the output: curl reads the headers (-H @file) and the body (--data-binary @file) from files in a private temporary folder (umask 077),
# removed on exit. Needs curl and jq (in every Home Assistant base image).
set -eu

TRIES=120                      # the door's start-up wait, in checks one second apart (FLAGGED: a threshold for the operator)
SEAT=http://127.0.0.1:4096
SUPERVISOR=http://supervisor
PORT=4096

say() { printf 'babyseat add-on: %s\n' "$*" >&2; }
[ $# -ge 1 ] || { say "usage: announce.sh <admin.token file> [<old cksum>]"; exit 2; }
tokfile=$1
old=${2:-}

if [ -z "${SUPERVISOR_TOKEN:-}" ]; then
  say "no SUPERVISOR_TOKEN (not run by the Supervisor): the seat is not announced to Home Assistant"
  exit 0
fi

umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM

# the header file of a token file: "Authorization: Bearer <first line of the file>" (read by the shell, written to a file, never an argument)
bearer_file() {
  IFS= read -r t < "$1" || [ -n "${t:-}" ]
  printf 'Authorization: Bearer %s\n' "$t" > "$2"
}

ready=""
i=0
while [ "$i" -lt "$TRIES" ]; do
  i=$((i + 1))
  if [ -s "$tokfile" ] && [ "$(cksum < "$tokfile")" != "$old" ]; then
    bearer_file "$tokfile" "$work/seat.h"
    code=$(curl -s -o /dev/null -w '%{http_code}' -H @"$work/seat.h" "$SEAT/global/health" 2>/dev/null || true)
    if [ "$code" = 200 ]; then ready=1; break; fi
  fi
  sleep 1
done
if [ -z "$ready" ]; then
  say "the seat did not answer /global/health with this start's admin token within $TRIES s: not announced to Home Assistant (restart the add-on to try again)"
  exit 1
fi

host=$(hostname 2>/dev/null || true)
[ -n "$host" ] || host=${HOSTNAME:-}
[ -n "$host" ] || { say "no host name for this container: not announced"; exit 1; }

printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$SUPERVISOR_TOKEN" > "$work/sup.h"
jq -cn --arg host "$host" --argjson port "$PORT" --rawfile token "$tokfile" \
  '{service: "membrane", config: {host: $host, port: $port, token: ($token | split("\n")[0])}}' > "$work/body.json"
code=$(curl -s -o "$work/reply.json" -w '%{http_code}' -X POST -H @"$work/sup.h" --data-binary @"$work/body.json" "$SUPERVISOR/discovery" 2>/dev/null || true)
if [ "$code" != 200 ]; then
  say "the Supervisor refused the discovery (HTTP ${code:-none}): Home Assistant was not told about the seat"
  exit 1
fi
say "announced the seat to Home Assistant (host $host, port $PORT)"
