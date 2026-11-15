#!/bin/sh
# Rebuild the browser engine (the wasm core) and its cache key: the exact recipe, no behaviour of its own.
# Needs rustup's toolchain with the wasm32-unknown-unknown target and wasm-pack (/opt/homebrew/opt/rustup/bin on the operator's Mac).
# native/build-web.sh writes membrane/apps/barest.c1f/ui/fieldvm_core.js, fieldvm_core_bg.wasm and fieldvm_version.js.
# Check determinism by running it twice and comparing: shasum -a 256 membrane/apps/barest.c1f/ui/fieldvm_core* membrane/apps/barest.c1f/ui/fieldvm_version.js
# Commit the three rebuilt files ALONE, after every engine change (native/src) that the page must run.
set -e
exec "$(dirname "$0")/../native/build-web.sh"
