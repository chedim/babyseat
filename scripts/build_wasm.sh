#!/bin/sh
# The browser build of the engine (the page's fieldvm_core.js, fieldvm_core_bg.wasm and fieldvm_version.js, in membrane/apps/barest.c1f/ui/): built from the pinned cliffvm release's source
# (github.com/chedim/cliffvm, tag v<CLIFFVM_VERSION>) by its own build-web.sh; needs rustup's toolchain with the wasm32-unknown-unknown target, wasm-pack and git.
# Commit the three rebuilt files ALONE, after CLIFFVM_VERSION moves to an engine change that the page must run.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
ver=$(cat "$root/CLIFFVM_VERSION")
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM
git clone -q --depth 1 --branch "v$ver" "${CLIFFVM_REPO:-https://github.com/chedim/cliffvm}" "$tmp/cliffvm"
ui=membrane/apps/barest.c1f/ui
mkdir -p "$tmp/$ui"   # build-web.sh writes to ../membrane/apps/barest.c1f/ui next to its own folder
sh "$tmp/cliffvm/build-web.sh"
for f in fieldvm_core.js fieldvm_core_bg.wasm fieldvm_version.js; do cp "$tmp/$ui/$f" "$root/$ui/$f"; done
echo "scripts/build_wasm.sh: the browser engine of cliffvm $ver is in $ui" >&2
