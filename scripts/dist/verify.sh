#!/bin/sh
# Check a dist folder against its MANIFEST.sha256: every listed file is there with its sha256, and no file is there that the manifest does not list.
#   scripts/dist/verify.sh <dist folder>      exit 0 = intact; exit 1 = the differing paths on stderr
set -eu
d=${1:?usage: scripts/dist/verify.sh <dist folder>}
[ -f "$d/MANIFEST.sha256" ] || { echo "verify: no MANIFEST.sha256 in $d" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then sha() { sha256sum "$1" | cut -d' ' -f1; }; else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi
bad=0
while IFS= read -r line; do
  want=${line%%  *}; f=${line#*  }
  if [ ! -f "$d/$f" ]; then echo "verify: missing $f" >&2; bad=1
  elif [ "$(sha "$d/$f")" != "$want" ]; then echo "verify: changed $f" >&2; bad=1; fi
done < "$d/MANIFEST.sha256"
(cd "$d" && find . -type f ! -name MANIFEST.sha256 | sed 's|^\./||' | LC_ALL=C sort) > "${TMPDIR:-/tmp}/verify.$$"
cut -d' ' -f3- "$d/MANIFEST.sha256" | LC_ALL=C sort > "${TMPDIR:-/tmp}/verify-m.$$"
extra=$(LC_ALL=C comm -23 "${TMPDIR:-/tmp}/verify.$$" "${TMPDIR:-/tmp}/verify-m.$$")
rm -f "${TMPDIR:-/tmp}/verify.$$" "${TMPDIR:-/tmp}/verify-m.$$"
if [ -n "$extra" ]; then printf '%s\n' "$extra" | sed 's/^/verify: not in the manifest /' >&2; bad=1; fi
[ "$bad" = 0 ] && echo "verify: $d intact ($(wc -l < "$d/MANIFEST.sha256" | tr -d ' ') files)"
exit "$bad"
