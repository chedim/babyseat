# SPIKE: memory-KV bank — goal membrane::synth:membrane-spike

Ask: a KV bank with LRU 2GiB, pinned (non-evictable) arrays, and model-local RoPE specs; bankMemory prefills the canonical-prefix so the repeated system prompt never re-prefills.

Why: textualizing old context (summaries) loses meaning; keeping raw KV keeps it.

TO GROUND (next pass):
1. Serving stack facts: ollama / llama-server version on this box, and whether it exposes prompt cache or slot save/restore.
2. Where the canonical-prefix is assembled (membrane side) and its size — tokens x call rate = the win.
3. RoPE today: per-model or global — confirm model-local specs are actually needed.

Exit: one-page feasibility verdict — which layer hosts the bank, what exists for free, what must be built.
3. RoPE today: per-model settings live in ollama/modelfiles, not in membrane code — enumerate `ollama show` next pass.

## GROUNDED 2026-10-13
- Serving stack: ollama 0.34.1 on this box; no llama-server binary. KV-bank must ride ollama: it holds a per-loaded-model prompt cache (prev-prompt prefix reuse) but exposes NO slot save/restore API -> durable slot save/restore needs llama-server (llama.cpp `--cache-reuse`, slot API) or a patched ollama.
- Canonical-prefix assembly: no prompt-side "canonical" hits in membrane/*.py (only night.py entity-id canonicalization) -> the repeated prefix is assembled by the seat proxy per call (doctrine + field blocks), so THAT is where bankMemory prefill hooks in; size not yet measured (tokens x call rate = the win).
- RoPE: not in membrane code; keep model-local specs out of the bank, read from the model registry instead.

## REMAINING
- ollama show <models>: context length / rope settings.
- Measure canonical-prefix tokens x call rate for a day.
- Decide: llama-server sidecar (true slot restore) vs ollama-only prefix optimization.
