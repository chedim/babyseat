#!/bin/sh
# The payload of the binary wheel (docs/packaging-plan.md, the wrapper): the dist folder, verified, in the shape the wrapper packages.
#   scripts/dist/assemble_wheel_payload.sh <dist folder> <payload dir>
# THE INTERFACE (this script makes, the wrapper's setup.py/pyproject take):
#   <payload dir>/dist/            the whole dist folder, no version folder above it:
#       bin/cliffvm  packs/  apps/  LICENSE  VERSION  MANIFEST.sha256
#   The wrapper copies `<payload dir>/dist/` to `membrane/dist/` in the wheel (package-data `dist/**`, so `membrane/dist/bin/cliffvm` is what launcher.py execs with `serve`),
#   tags the wheel `py3-none-<platform>` (one payload per platform: the binary is the platform's), and keeps the executable bit of bin/cliffvm (a wheel zip carries the mode;
#   the launcher may `chmod +x` as a fallback). `cliffvm serve` finds packs/ apps/ above its own bin/, so nothing else is passed.
#   After install, `membrane/dist/VERSION` equals the wheel's version (a wrapper test compares them) and `scripts/dist/verify.sh membrane/dist` passes.
# The script refuses a dist folder that fails verify.sh, and a payload dir that is not empty.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
src=${1:?usage: scripts/dist/assemble_wheel_payload.sh <dist folder> <payload dir>}
payload=${2:?usage: scripts/dist/assemble_wheel_payload.sh <dist folder> <payload dir>}
[ -x "$src/bin/cliffvm" ] || { echo "payload: no executable $src/bin/cliffvm" >&2; exit 1; }
sh "$here/verify.sh" "$src" >/dev/null || { echo "payload: $src does not match its MANIFEST.sha256 (run scripts/dist/verify.sh)" >&2; exit 1; }
if [ -n "$(ls -A "$payload" 2>/dev/null)" ]; then echo "payload: $payload is not empty" >&2; exit 1; fi
mkdir -p "$payload/dist"
cp -R "$src/." "$payload/dist/"
sh "$here/verify.sh" "$payload/dist" >/dev/null
echo "payload: $payload/dist ($(cat "$payload/dist/VERSION"))"
