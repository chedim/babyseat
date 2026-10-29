#!/bin/sh
# The in-browser ML bundle for membrane home (2026-10-10): transformers.js
# (Apache-2.0; whisper speech-to-text on the device) + kokoro-js (Apache-2.0)
# and ONE onnxruntime-web (MIT) runtime. Built, not committed (28 MB of wasm);
# build_release.py packs whatever is in assets/.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"; W="$HERE/assets"; mkdir -p "$W"
T=$(mktemp -d); cd "$T"
npm init -y >/dev/null
npm install --silent @huggingface/transformers@3 kokoro-js esbuild
npm dedupe --silent
printf 'export { pipeline, env } from "@huggingface/transformers";\nexport { KokoroTTS } from "kokoro-js";\n' > entry.mjs
npx esbuild entry.mjs --bundle --format=esm --platform=browser --minify --target=es2020 --outfile="$W/voice-ml.mjs" --log-level=warning
cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.mjs node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.wasm "$W/"
cd /; rm -rf "$T"; ls -la "$W/voice-ml.mjs" "$W"/ort-wasm-simd-threaded.jsep.*
