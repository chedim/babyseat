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
# the seat caches .wasm for a week and the JS for no time: the loader asks for the wasm by its content hash
h=$(shasum -a 256 ../membrane/ui/fieldvm_core_bg.wasm | cut -c1-12)
printf '// written by native/build-web.sh: the wasm build the loader asks for (a cache key)\nexport const WASM_VERSION = "%s";\n' "$h" > ../membrane/ui/fieldvm_version.js
echo "built ../membrane/ui/fieldvm_core.js and fieldvm_core_bg.wasm ($(wc -c < ../membrane/ui/fieldvm_core_bg.wasm) bytes)"
