#!/bin/sh
# The engine's release binary for one platform, from the cliffvm repository's GitHub release (github.com/chedim/cliffvm): download `cliffvm-<version>-<target>.tar.gz` and its `.sha256`, check the
# checksum, unpack it, check its MANIFEST.sha256 file by file and that its VERSION is the version asked for, and leave the folder (bin/cliffvm, bin/graphlink-bridge, LICENSE, VERSION, MANIFEST.sha256)
# at <out>. Nothing is left at <out> when a check fails. scripts/dist/build.sh then assembles babyseat's dist folder around that binary (`--bin <out>/bin/cliffvm`).
#   scripts/dist/fetch_cliffvm.sh <version> <target> <out folder>
# CLIFFVM_RELEASE_BASE names another place for the releases (the tests use a folder over file://): `<base>/v<version>/cliffvm-<version>-<target>.tar.gz`.
set -eu
[ "$#" -eq 3 ] || { echo "usage: scripts/dist/fetch_cliffvm.sh <version> <target> <out folder>" >&2; exit 2; }
version=$1; target=$2; out=$3
base=${CLIFFVM_RELEASE_BASE:-https://github.com/chedim/cliffvm/releases/download}
name=cliffvm-$version-$target
if command -v sha256sum >/dev/null 2>&1; then sha() { sha256sum "$1" | cut -d' ' -f1; }; else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM
curl -fsSL -o "$tmp/$name.tar.gz" "$base/v$version/$name.tar.gz" || { echo "fetch_cliffvm: cannot download $base/v$version/$name.tar.gz" >&2; exit 1; }
curl -fsSL -o "$tmp/$name.tar.gz.sha256" "$base/v$version/$name.tar.gz.sha256" || { echo "fetch_cliffvm: cannot download $base/v$version/$name.tar.gz.sha256" >&2; exit 1; }
want=$(cut -d' ' -f1 "$tmp/$name.tar.gz.sha256")
got=$(sha "$tmp/$name.tar.gz")
[ "$want" = "$got" ] || { echo "fetch_cliffvm: $name.tar.gz has sha256 $got, its .sha256 file says $want: refused" >&2; exit 1; }
mkdir "$tmp/x"
tar -C "$tmp/x" -xzf "$tmp/$name.tar.gz"
d=$tmp/x/$name
[ -d "$d" ] || { echo "fetch_cliffvm: the tarball has no folder $name" >&2; exit 1; }
(cd "$d" && while read -r h f; do [ "$(sha "$f")" = "$h" ] || { echo "fetch_cliffvm: MANIFEST.sha256 does not match $f: refused" >&2; exit 1; }; done < MANIFEST.sha256)
[ "$(cat "$d/VERSION")" = "$version" ] || { echo "fetch_cliffvm: the tarball says version $(cat "$d/VERSION"), not $version: refused" >&2; exit 1; }
rm -rf "$out"
mkdir -p "$(dirname "$out")"
mv "$d" "$out"
echo "fetch_cliffvm: $name ok at $out" >&2
