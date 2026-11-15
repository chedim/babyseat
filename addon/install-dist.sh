#!/bin/sh
# babyseat add-on: put the seat's dist folder for this add-on version in place (#1143). Run by the add-on's Dockerfile at build time.
#   install-dist.sh --version <X.Y.Z> --arch <amd64|aarch64> --dest <dir> [--local <folder>] [--base-url <url>]
# The tarball is the release's MUSL build (static: the image is Alpine), babyseat-<version>-<x86_64|aarch64>-unknown-linux-musl.tar.gz with its .sha256 sidecar
# (scripts/dist/build.sh --archive makes both; release-binaries.yml builds them; publish.yml attaches them to the GitHub release).
#   --local <folder>   take them from a folder (a local build: the add-on's dist/ folder, see DOCS.md)
#   --base-url <url>   download them from there (default https://github.com/chedim/membrane/releases/download/v<version>; file:// works)
# As install.sh: the tarball's sha256 is checked against the sidecar BEFORE it is unpacked, then every file against the folder's MANIFEST.sha256; nothing lands in --dest otherwise.
set -eu

die() { printf 'install-dist.sh: %s\n' "$*" >&2; exit 1; }
version="" arch="" dest="" local_dir="" base=""
while [ $# -gt 0 ]; do
  case $1 in
    --version) version=${2:-}; shift 2 ;;
    --arch) arch=${2:-}; shift 2 ;;
    --dest) dest=${2:-}; shift 2 ;;
    --local) local_dir=${2:-}; shift 2 ;;
    --base-url) base=${2:-}; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$version" ] && [ -n "$dest" ] || die "--version and --dest are needed"
case $arch in
  amd64) target=x86_64-unknown-linux-musl ;;
  aarch64) target=aarch64-unknown-linux-musl ;;
  *) die "no seat binary for the architecture '$arch' (the add-on supports amd64 and aarch64)" ;;
esac
[ ! -e "$dest" ] || die "$dest exists: not replaced"
if command -v sha256sum >/dev/null 2>&1; then sha() { sha256sum "$1" | cut -d' ' -f1; }; else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi

asset=babyseat-$version-$target.tar.gz
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
if [ -n "$local_dir" ]; then
  [ -f "$local_dir/$asset" ] || die "no $asset in $local_dir"
  [ -f "$local_dir/$asset.sha256" ] || die "no $asset.sha256 in $local_dir: nothing to check the tarball against"
  cp "$local_dir/$asset" "$local_dir/$asset.sha256" "$work/"
else
  [ -n "$base" ] || base=https://github.com/chedim/membrane/releases/download/v$version
  curl -fsSL -o "$work/$asset" "$base/$asset" || die "could not download $base/$asset (is there a release $version with a $target tarball?)"
  curl -fsSL -o "$work/$asset.sha256" "$base/$asset.sha256" || die "could not download $base/$asset.sha256: nothing to check the tarball against"
fi
want=$(cut -d' ' -f1 < "$work/$asset.sha256")
have=$(sha "$work/$asset")
[ -n "$want" ] && [ "$want" = "$have" ] || die "the sha256 of $asset is $have, its .sha256 says ${want:-nothing}: not the release"

mkdir "$work/x"
tar -xzf "$work/$asset" -C "$work/x"
d=$work/x/$version
[ -f "$d/MANIFEST.sha256" ] && [ -x "$d/bin/cliffvm" ] || die "the tarball is not a dist folder of $version (no $version/MANIFEST.sha256 or bin/cliffvm)"
[ "$(cat "$d/VERSION")" = "$version" ] || die "the dist folder says version $(cat "$d/VERSION"), not $version"
bad=0
while read -r w f; do
  { [ -f "$d/$f" ] && [ "$(sha "$d/$f")" = "$w" ]; } || { printf 'install-dist.sh: MANIFEST.sha256: %s is missing or changed\n' "$f" >&2; bad=1; }
done < "$d/MANIFEST.sha256"
[ "$bad" = 0 ] || die "the folder does not match its MANIFEST.sha256"
mkdir -p "$(dirname "$dest")"
mv "$d" "$dest"
printf 'install-dist.sh: babyseat %s (%s) in %s\n' "$version" "$target" "$dest"
