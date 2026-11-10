#!/bin/sh
# Build the field engine for the browser (WebAssembly) and put it where the seat serves its UI files:
# membrane/ui/fieldvm_core.js + fieldvm_core_bg.wasm (loaded by membrane/ui/fieldvm.js, served at /ui/www/).
# Needs rustup's toolchain with the wasm32-unknown-unknown target, and wasm-pack.
set -e
cd "$(dirname "$0")"
RUSTUP_BIN="${RUSTUP_BIN:-/opt/homebrew/opt/rustup/bin}"
[ -d "$RUSTUP_BIN" ] && PATH="$RUSTUP_BIN:$PATH"
out="$(mktemp -d)"
wasm-pack build . --release --target web --out-dir "$out" --out-name fieldvm_core -- --no-default-features --features wasm >/dev/null
for f in fieldvm_core.js fieldvm_core_bg.wasm; do
  cp "$out/$f" "../membrane/ui/.$f.$$"
  mv -f "../membrane/ui/.$f.$$" "../membrane/ui/$f"
done
rm -rf "$out"
echo "built ../membrane/ui/fieldvm_core.js and fieldvm_core_bg.wasm ($(wc -c < ../membrane/ui/fieldvm_core_bg.wasm) bytes)"
