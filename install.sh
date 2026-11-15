#!/bin/sh
# babyseat installer, for a machine without pip (#1143, docs/packaging-plan.md):
#
#   curl -fsSL https://raw.githubusercontent.com/chedim/membrane/main/install.sh | sh
#   sh install.sh --version 0.7.60
#
# It downloads the release tarball of your platform (macOS arm64/x86_64, Linux x86_64/aarch64 on glibc or musl), CHECKS its sha256 against the release's .sha256 file BEFORE extracting,
# extracts it into a staging folder, checks every file against the folder's own MANIFEST.sha256, and only then moves it to
#   ~/.local/share/membrane-app/<version>/        (the dist folder: bin/cliffvm packs/ apps/ ...)
#   ~/.local/share/membrane-app/babyseat          (a two-line launcher: `cliffvm serve "$@"` of the CURRENT version)
#   ~/.local/share/membrane-app/current           (a link to the installed version)
#   ~/.local/bin/babyseat                     (a link to the launcher)
# Never uses sudo; nothing outside those two folders is touched. A failed run leaves the previous install as it was. Run it again to upgrade.
#
#   --version <X.Y.Z>   the release to install (default: the latest GitHub release)
#   --base-url <url>    where babyseat-<version>-<target>.tar.gz and its .sha256 are (default: the GitHub release of the version; file:// works)
#   --prefix <dir>      the install folder (default ~/.local/share/membrane-app, apart from the seat's data folder; env MEMBRANE_INSTALL_DIR)
#   --bin-dir <dir>     where the babyseat link goes (default ~/.local/bin; env MEMBRANE_BIN_DIR)
#   --target <triple>   the platform's rust triple, instead of detecting it
#   --print-target      print the detected triple and stop
# Also pip: `pip install babyseat` carries the same binary (and is the way to the Home Assistant / docker-less Python worlds).
set -eu

REPO=${MEMBRANE_REPO_SLUG:-chedim/membrane}
version=${MEMBRANE_VERSION:-}
base=${MEMBRANE_RELEASE_URL:-}
prefix=${MEMBRANE_INSTALL_DIR:-}
bindir=${MEMBRANE_BIN_DIR:-}
target=''
print_target=0

say() { printf '[babyseat] %s\n' "$*"; }
die() { printf '[babyseat] %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) version=${2:?--version needs a number}; shift 2 ;;
    --base-url) base=${2:?--base-url needs a url}; shift 2 ;;
    --prefix) prefix=${2:?--prefix needs a folder}; shift 2 ;;
    --bin-dir) bindir=${2:?--bin-dir needs a folder}; shift 2 ;;
    --target) target=${2:?--target needs a rust triple}; shift 2 ;;
    --print-target) print_target=1; shift ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see: sh install.sh --help)" ;;
  esac
done

detect_target() {
  os=${MEMBRANE_UNAME_S:-$(uname -s)}
  cpu=${MEMBRANE_UNAME_M:-$(uname -m)}
  case "$cpu" in
    arm64|aarch64) arch=aarch64 ;;
    x86_64|amd64) arch=x86_64 ;;
    *) arch='' ;;
  esac
  case "$os" in
    Darwin) [ -n "$arch" ] && { echo "$arch-apple-darwin"; return 0; } ;;
    Linux)
      if [ -n "$arch" ]; then
        libc=${MEMBRANE_LIBC:-}
        if [ -z "$libc" ]; then
          if [ -e /etc/alpine-release ] || (ldd --version 2>&1 | grep -qi musl); then libc=musl; else libc=gnu; fi
        fi
        echo "$arch-unknown-linux-$libc"; return 0
      fi ;;
  esac
  echo "[babyseat] no binary for this platform ($os $cpu): the releases cover macOS and Linux on x86_64 and arm64. Try: pip install babyseat (it says so too when there is no wheel)." >&2
  return 1
}

if [ -z "$target" ]; then target=$(detect_target) || exit 1; fi
if [ "$print_target" = 1 ]; then echo "$target"; exit 0; fi

command -v curl >/dev/null 2>&1 || die "curl is needed to download the release"
if command -v sha256sum >/dev/null 2>&1; then sha() { sha256sum "$1" | cut -d' ' -f1; }; else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi

if [ -z "$version" ]; then
  # the redirect of /releases/latest names the tag: v0.7.60
  tag=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest") || die "could not ask GitHub for the latest release: name one with --version"
  version=${tag##*/v}
  case "$version" in ''|*[!0-9.]*) die "could not read a version from $tag: name one with --version" ;; esac
fi
version=${version#v}
[ -n "$base" ] || base=https://github.com/$REPO/releases/download/v$version
[ -n "$prefix" ] || prefix=${HOME:?no HOME in the environment: name the folder with --prefix}/.local/share/membrane-app
[ -n "$bindir" ] || bindir=${HOME:?no HOME in the environment: name the folder with --bin-dir}/.local/bin
asset=babyseat-$version-$target.tar.gz

work=$(mktemp -d)
mkdir -p "$prefix"
stage=$prefix/.partial.$$
trap 'rm -rf "$work" "$stage"' EXIT INT TERM

say "downloading $asset ($base)"
curl -fsSL -o "$work/$asset" "$base/$asset" || die "could not download $base/$asset (is there a release $version for $target?)"
curl -fsSL -o "$work/$asset.sha256" "$base/$asset.sha256" || die "could not download $base/$asset.sha256: no sha256 to check the download against, nothing installed"
want=$(cut -d' ' -f1 < "$work/$asset.sha256")
have=$(sha "$work/$asset")
[ -n "$want" ] && [ "$want" = "$have" ] || die "the sha256 of $asset is $have, the release says ${want:-nothing}: the download is not the release, nothing installed"

mkdir -p "$stage"
tar -xzf "$work/$asset" -C "$stage"
n=$(ls "$stage" | wc -l | tr -d ' ')
[ "$n" = 1 ] || die "the tarball should hold one folder, it holds $n: nothing installed"
dist=$stage/$(ls "$stage")
[ -f "$dist/MANIFEST.sha256" ] && [ -x "$dist/bin/cliffvm" ] || die "the tarball is not a dist folder (no MANIFEST.sha256 or bin/cliffvm): nothing installed"
bad=0
while IFS= read -r line; do
  w=${line%%  *}; f=${line#*  }
  { [ -f "$dist/$f" ] && [ "$(sha "$dist/$f")" = "$w" ]; } || { echo "[babyseat] MANIFEST.sha256: $f is missing or changed" >&2; bad=1; }
done < "$dist/MANIFEST.sha256"
[ "$bad" = 0 ] || die "the folder does not match its MANIFEST.sha256: nothing installed"
[ "$(cat "$dist/VERSION")" = "$version" ] || die "the folder is version $(cat "$dist/VERSION"), not $version: nothing installed"

# verified: replace the version folder, then flip the links (a failure above leaves the previous install untouched)
rm -rf "${prefix:?}/$version"
mv "$dist" "$prefix/$version"
ln -sfn "$version" "$prefix/current"
cat > "$prefix/.babyseat.$$" <<EOF
#!/bin/sh
# babyseat: the seat of the current install ($prefix/current). Written by install.sh.
case "\${1:-}" in --version|-V) exec "$prefix/current/bin/cliffvm" --version ;; esac
exec "$prefix/current/bin/cliffvm" serve "\$@"
EOF
chmod 755 "$prefix/.babyseat.$$"
mv "$prefix/.babyseat.$$" "$prefix/babyseat"
mkdir -p "$bindir"
ln -sfn "$prefix/babyseat" "$bindir/babyseat"

say "installed babyseat $version ($target) in $prefix/$version"
say "start it:   babyseat            (the seat on http://127.0.0.1:4096; --port N for another port, --data-dir <dir> for its data, babyseat --help for the rest)"
say "it chats with Claude through the claude CLI or an API key: export ANTHROPIC_API_KEY=... before starting"
case ":$PATH:" in *":$bindir:"*) ;; *) say "$bindir is not on your PATH: add it, e.g.  echo 'export PATH=\"$bindir:\$PATH\"' >> ~/.profile" ;; esac
say "macOS: files installed this way are not quarantined. A tarball downloaded in a browser is: right-click the binary and choose Open once, or run  xattr -dr com.apple.quarantine <folder>"
say "upgrade: run this installer again. Remove: rm -rf $prefix $bindir/babyseat (the seat's data is in its own folder, not in these)"
