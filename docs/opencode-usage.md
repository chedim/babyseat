# Using the `opencode` command

`opencode` here is **membrane's seat server**, not someone else's TUI: a field-backed
seat that serves an OpenAI-compatible `/v1` proxy for remote clients, and (when a TUI
attaches) uses it as a **thin display client** — the TUI is display only.

## Two ways to start it

```
membrane opencode                 # seat command: field-backed seat on :11500 + serve
                                  # server on loopback, opencode TUI attached as thin client
python -m membrane.opencode       # headless serve: /v1 proxy + universe map, no TUI
```

The seat is the brain; the TUI is just a window. Starting the server headless is enough
for remote clients — attach a TUI only where you want a display.

## Flags (`python -m membrane.opencode`)

| flag | default | meaning |
|------|---------|---------|
| `--listen N` | `11500` | port for the membrane OpenAI-compatible proxy |
| `--listen-host HOST` | `0.0.0.0` | the `/v1` proxy's bind address (all interfaces) |
| `--backend URL` | `http://localhost:11434` | model backend (Ollama-compatible) |
| `--model MODEL` | — | model override |
| `--field FILE` | `rag-field.json` | entity field state file |
| `--audit FILE` | `rag-audit.jsonl` | audit log |
| `--embed-model MODEL` | — | embedding model override |
| `--ctx-chars N` | `2400` | per-turn context character budget |
| `--tool-keep N` | `3` | most recent tool results kept |
| `--compact-model MODEL` | — | model used for compacting context |
| `--idle-cortex-model MODEL` | — | model for idle background mind |
| `--with-context` / `--no-context` | on | pass membrane context to model turns |
| `--no-viewer` | off | don't serve the universe map viewer |
| `--no-banner` | off | suppress startup banner |
| `--open` | off | open the universe map in the browser on start |

## Pointing clients at it

Any OpenAI-compatible client can aim at the proxy (default
`http://<seat-host>:11500/v1`); an Ollama client aims at `http://<seat-host>:11500`
(its `/api/chat` and `/api/generate` run the same memory turn, the rest of `/api/*`
passes through to Ollama). With `membrane opencode`, the TUI attaches on the
loopback serve server; other machines use the LAN address (`--listen-host 0.0.0.0`).

## The RECALL block in the banner (#668)

Each turn the banner ends with up to 12 `▸ #N (age, source) fact` lines: what the
field holds for your latest message. `ok` or `1. yes` keeps the previous set. The
chat's own last 8 turns are skipped, and near-duplicate lines collapse. The scorer's
weights are the field entity `config/recall_scorer`; see `docs/entity-field.md`. Each rebuild logs one `[recall-banner]` line to the seat log with its steps' seconds (`vec`, `recall`, `speakers`, `recent`, `pick`) and the pool size.
