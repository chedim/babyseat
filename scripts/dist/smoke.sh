#!/bin/sh
# The smoke test of a built dist folder (CI runs it on every platform; run it by hand the same way): the folder is intact, `cliffvm --version` names the stamped version,
# `cliffvm serve --port 0` comes up from the folder alone (cwd elsewhere), /global/health answers 200 with the owner token and 401 without, and the seat is stopped.
#   scripts/dist/smoke.sh <dist folder>
# Needs curl. No claude CLI and no API key are used: no chat turn runs.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
d=$(cd "${1:?usage: scripts/dist/smoke.sh <dist folder>}" && pwd)
sh "$here/verify.sh" "$d"
ver=$(cat "$d/VERSION")
"$d/bin/cliffvm" --version | grep -q " $ver\$" || { echo "smoke: cliffvm --version does not say $ver" >&2; exit 1; }
work=$(mktemp -d)
log=$work/serve.log
pid=''
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; [ -n "$pid" ] && wait "$pid" 2>/dev/null || true; rm -rf "$work"; }
trap cleanup EXIT INT TERM
cd "$work"
# SMOKE_LAUNCH names the user-facing command instead (scripts/dist/smoke_wheel.sh: the wheel's `babyseat`, which runs `cliffvm serve` itself)
if [ -n "${SMOKE_LAUNCH:-}" ]; then
  env -u ANTHROPIC_API_KEY CLAUDE_BIN=/bin/sh "$SMOKE_LAUNCH" --port 0 --data-dir "$work/data" > "$log" 2>&1 &
else
  env -u ANTHROPIC_API_KEY CLAUDE_BIN=/bin/sh "$d/bin/cliffvm" serve --port 0 --data-dir "$work/data" > "$log" 2>&1 &
fi
pid=$!
port=''
i=0
while [ "$i" -lt 300 ]; do   # 30 s at most
  port=$(sed -n 's/^cliffvm: door listening on .*:\([0-9][0-9]*\)$/\1/p' "$log" | head -1)
  [ -n "$port" ] && break
  kill -0 "$pid" 2>/dev/null || { echo "smoke: serve ended early:" >&2; cat "$log" >&2; exit 1; }
  i=$((i + 1)); sleep 0.1
done
[ -n "$port" ] || { echo "smoke: the door did not come up:" >&2; cat "$log" >&2; exit 1; }
url=http://127.0.0.1:$port
code=$(curl -s -o /dev/null -w '%{http_code}' "$url/global/health")
[ "$code" = 401 ] || { echo "smoke: health without a token answered $code (want 401)" >&2; exit 1; }
tok=$(cat "$work/data/owner.token")
body=$(curl -s -w ' %{http_code}' -H "Authorization: Bearer $tok" "$url/global/health")
case "$body" in *healthy*' 200') ;; *) echo "smoke: health with the owner token answered: $body" >&2; exit 1 ;; esac
echo "smoke: ok ($ver, port $port)"
