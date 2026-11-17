#!/bin/sh
# Assemble the dist folder from a release build (docs/packaging-plan.md, decided 2026-11-14: packs and apps as FILES in a version-stamped folder next to the binary).
#   scripts/dist/build.sh [--bin <cliffvm>] [--out <dir>] [--target <rust triple>] [--archive <file.tar.gz>]
# --bin      the binary to put in bin/ (default: build it: `cargo build --release --no-default-features --features bin` in native/, with --target when given); a graphlink-bridge beside it goes into bin/ too
# --out      the folder to make (default: <repo>/dist/<version>; <version> is native/Cargo.toml's package version). An existing folder is replaced only if it is empty or a dist (it has a VERSION file).
# --archive  also pack the folder as <file.tar.gz> (the one top folder is the out folder's name) and write <file.tar.gz>.sha256 next to it
# The tree (what `cliffvm serve` finds by itself, relative to bin/cliffvm; the repo's membrane/ folder has the same shape, so `cliffvm serve --root membrane` is the dev run):
#   bin/cliffvm  packs/*.c1f + boot.json ...  apps/barest.packs apps/barest.c1f/  LICENSE  VERSION  MANIFEST.sha256
# MANIFEST.sha256 is `<sha256>  <path>` per file but itself, sorted by path (bytes): the same inputs give the same manifest; scripts/dist/verify.sh checks a folder against it.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/native/Cargo.toml" | head -1)
[ -n "$version" ] || { echo "dist/build.sh: no version in native/Cargo.toml" >&2; exit 1; }
bin=''; out=''; target=''; archive=''
while [ $# -gt 0 ]; do
  case "$1" in
    --bin) bin=${2:?--bin needs a path}; shift 2 ;;
    --out) out=${2:?--out needs a folder}; shift 2 ;;
    --target) target=${2:?--target needs a rust triple}; shift 2 ;;
    --archive) archive=${2:?--archive needs a file name}; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "dist/build.sh: unknown option $1 (--bin --out --target --archive)" >&2; exit 2 ;;
  esac
done
if [ -z "$bin" ]; then
  if [ -n "$target" ]; then
    (cd "$root/native" && cargo build --release --no-default-features --features bin --target "$target") >&2
    bin=$root/native/target/$target/release/cliffvm
  else
    (cd "$root/native" && cargo build --release --no-default-features --features bin) >&2
    bin=$root/native/target/release/cliffvm
  fi
fi
# x is set by the eval on the next line (shellcheck cannot see it)
# shellcheck disable=SC2154
for v in bin out archive; do eval "x=\$$v"; case $x in ""|/*) ;; *) eval "$v=\$PWD/\$x" ;; esac; done
case $bin in /*) ;; *) bin=$PWD/$bin ;; esac   # the paths are used from inside other folders below
case $out in ''|/*) ;; *) out=$PWD/$out ;; esac
case $archive in ''|/*) ;; *) archive=$PWD/$archive ;; esac
[ -f "$bin" ] || { echo "dist/build.sh: no binary at $bin (build it, or name it with --bin)" >&2; exit 1; }
[ -n "$out" ] || out=$root/dist/$version
if [ -e "$out" ] && [ -n "$(ls -A "$out" 2>/dev/null)" ] && [ ! -f "$out/VERSION" ]; then
  echo "dist/build.sh: $out exists and is not a dist folder (no VERSION file): not replaced" >&2; exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then sha() { sha256sum "$1" | cut -d' ' -f1; }; else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi
mem=$root/membrane
tmp=$out.partial.$$
rm -rf "$tmp"
mkdir -p "$tmp/bin" "$tmp/packs" "$tmp/apps"
cp "$bin" "$tmp/bin/cliffvm"
chmod 755 "$tmp/bin/cliffvm"
# the bridge program the seat's peers run (graphlink-bridge, built by the same cargo build) goes beside it when there is one
bridge=$(dirname "$bin")/graphlink-bridge
if [ -f "$bridge" ]; then cp "$bridge" "$tmp/bin/graphlink-bridge"; chmod 755 "$tmp/bin/graphlink-bridge"; fi
# ONLY what the barest seat loads: the packs apps/barest.packs lists, the launcher pack (`cliffvm serve` reads its defaults), and boot.json (the one data file read at run time).
# The Python-era packs of membrane/packs/ and membrane/help/*.md (nothing loads them) stay out.
grep -v '^[[:space:]]*#' "$mem/apps/barest.packs" | grep -v '^[[:space:]]*$' | while IFS= read -r n; do
  [ -f "$mem/packs/$n.c1f" ] || { echo "dist/build.sh: apps/barest.packs lists $n but membrane/packs/$n.c1f does not exist" >&2; exit 1; }
  cp "$mem/packs/$n.c1f" "$tmp/packs/"
done
cp "$mem/packs/launcher.c1f" "$mem/packs/boot.json" "$tmp/packs/"
# the barest app: the list of its packs and the app folder (the full seat's apps/field.c1f is not shipped)
cp "$mem/apps/barest.packs" "$tmp/apps/"
cp -R "$mem/apps/barest.c1f" "$tmp/apps/barest.c1f"

cp "$root/LICENSE" "$tmp/LICENSE"
printf '%s\n' "$version" > "$tmp/VERSION"
find "$tmp" -name .DS_Store -delete
(cd "$tmp" && find . -type f ! -name MANIFEST.sha256 | sed 's|^\./||' | LC_ALL=C sort | while IFS= read -r f; do printf '%s  %s\n' "$(sha "$f")" "$f"; done > "$tmp.manifest") && mv "$tmp.manifest" "$tmp/MANIFEST.sha256"
rm -rf "$out"
mkdir -p "$(dirname "$out")"
mv "$tmp" "$out"
if [ -n "$archive" ]; then
  mkdir -p "$(dirname "$archive")"
  tar -C "$(dirname "$out")" -czf "$archive" "$(basename "$out")"
  # the sidecar install.sh checks the download against: `<sha256>  <file name>` (release-binaries.yml uploads both)
  printf '%s  %s\n' "$(sha "$archive")" "$(basename "$archive")" > "$archive.sha256"
  echo "dist/build.sh: $archive"
fi
echo "dist/build.sh: $out ($version, $(wc -l < "$out/MANIFEST.sha256" | tr -d ' ') files)"
