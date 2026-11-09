#!/bin/sh
# Build the native VM (#971) and put the extension where `from membrane import membrane_vm` finds it.
set -e
cd "$(dirname "$0")"
PY="${PYO3_PYTHON:-$HOME/.membrane/venv/bin/python}"
PYO3_PYTHON="$PY" cargo build --release
lib=target/release/libmembrane_vm.dylib
[ -f "$lib" ] || lib=target/release/libmembrane_vm.so
cp "$lib" ../membrane/membrane_vm.so
echo "built ../membrane/membrane_vm.so"
