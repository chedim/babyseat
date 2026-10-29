#!/bin/sh
# The on-device voice model for membrane home (2026-10-10): Vosk's small
# English model (Apache-2.0, ~40 MB), packed as the tar.gz vosk-browser loads.
# Kept out of git; build_release.py packs whatever is in assets/.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/assets/vosk-model-en.tar.gz"
[ -f "$OUT" ] && { echo "have $OUT"; exit 0; }
T=$(mktemp -d)
curl -sL -o "$T/m.zip" https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
(cd "$T" && unzip -q m.zip && tar czf "$OUT" vosk-model-small-en-us-0.15)
rm -rf "$T"; ls -la "$OUT"
