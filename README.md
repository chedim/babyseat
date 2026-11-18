[![Discord](https://img.shields.io/badge/Discord-join%20the%20chat-5865F2?logo=discord&logoColor=white)](https://discord.gg/xeHswygKzX) [![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23550667.svg)](https://doi.org/10.5281/zenodo.23550667) [![Blurps hatched (est.)](https://img.shields.io/badge/%F0%9F%90%A3%20Blurps%20hatched%20%28est.%29-512-f5a623)](https://pypistats.org/packages/babyseat) [![Blurps alive (est.)](https://img.shields.io/badge/%F0%9F%90%A3%20Blurps%20alive%20%28est.%29-~155-f5a623)](https://github.com/chedim/babyseat/issues/1096) [![Docker pulls](https://img.shields.io/docker/pulls/chedim/babyseat?label=%F0%9F%90%B3%20Docker%20pulls&color=f5a623)](https://hub.docker.com/r/chedim/babyseat)
![babyseat](docs/logo/logo-1280x640.png)

**Talk to it. It remembers.**

babyseat is an AI seat whose memory is a graph the model itself programs. You talk to one chat; the model answers through the
LLM, and everything it knows about you, your files and your work lives as **entities in a field** it reads and writes with
four tools. A banner the model keeps in its own words carries what matters from one turn to the next, so the chat keeps its thread
without replaying history.

The seat is one small native program, `cliffvm` (Rust, one SQLite file, no Python, no Ollama, no database server). Its packs and its page are
files written in **c1f**, the field's own language, so the model can read, change and replay all of it.

> **The names.** **babyseat** is the project (PyPI `babyseat`, Docker `chedim/babyseat`, the Home Assistant integration). **membrane**
> is its memory layer, so the `membrane` command alias, the `membrane/` source folder, the `.membrane` folders and the Home Assistant domain keep that
> name. **cliffvm** is the program. **cliffhanger** is the reactive graph language it runs ([chedim/cliffhanger](https://github.com/chedim/cliffhanger)).

Version **0.7.63**. This is a rebuild: the 0.7 Python seat is retired (see [Upgrading](#upgrading-from-the-python-seat-07x)).

## What you get

- **One chat, in your browser.** The page has Chat (markdown answers, a Stop button, the tools the model ran folded under each answer) and
  Settings (the model, limits, what the seat knows about itself). It is a set of c1f view packs that run in the browser.
- **The model works on the field, not on a prompt.** Four tools only: `entity_query`, `entity_read`, `entity_write` and `run_cliff`
  (a small block of c1f run in one call). Files, commands, HTTP, SQL and Home Assistant are entities the model writes to; the answer is a receipt it reads.
  The model can load its own packs and replace its own banner ([docs/cliffvm.md](docs/cliffvm.md)).
- **Your history comes along.** A seat started over a 0.7 install imports the old chats by itself, newest first.
- **Home Assistant.** A HACS integration runs the seat inside Home Assistant (or connects to one you run), makes it an Assist conversation agent and lets the model use the house
  ([docs/home-assistant.md](docs/home-assistant.md)).
- **Your choice of model.** The default backend is a local Ollama (`seat_backend`, `http://127.0.0.1:11434`): no model is chosen at the start, and the page opens a window while a model or a name is not chosen, with a dropdown of the chat models Ollama lists. Any OpenAI-compatible endpoint works too (the boot fact `cloud_models`); the `claude` CLI is a developer mode ([docs/cliffvm.md](docs/cliffvm.md)). Pull a model first: `ollama pull <name>`.
- **Seats and programs can share a graph (0.7.63).** Local programs over shared memory (the fact `peers`), other machines through `graphlink-bridge` (TCP with tokens and TLS), and two seats reading each other's nodes: [docs/graph-link.md](docs/graph-link.md), section 18. The protocol and its crates: [chedim/graphlink](https://github.com/chedim/graphlink).
- **Local and strict.** The door listens on `127.0.0.1`; every route needs a signed token; the page gets one only from this machine ([docs/auth.md](docs/auth.md)).

Not in this release: the 3D field view (built, switched off: `membrane/apps/field.c1f`), the Home Assistant sidebar panel and relay, Blurp and the
quota/self/toybox features of 0.7. They are in the tag `python-seat-last` and the 0.7.53 package.

## Install

You need a model for the chat: a local [Ollama](https://ollama.com) with a model pulled (the setup is under [Which model answers](#which-model-answers)), or any OpenAI-compatible endpoint. Developers can use the `claude` CLI instead (same section).
macOS and Linux (x86_64 and arm64); there is no Windows build. The macOS binaries are unsigned: `pip` and the installer are not affected by Gatekeeper; a tarball downloaded in a browser needs right-click Open once or `xattr -dr com.apple.quarantine <folder>`.

| How | Command | State |
|---|---|---|
| pip | `pip install babyseat`, then `babyseat` (`--port N`, `--data-dir <dir>`) | at the 0.7.63 release |
| installer | `curl -fsSL https://raw.githubusercontent.com/chedim/membrane/main/install.sh \| sh` (installs `~/.local/share/membrane-app/<version>` and a `~/.local/bin/babyseat` link; `--version X.Y.Z` picks one) | at the 0.7.63 release |
| Docker | `docker run -p 127.0.0.1:4096:4096 -v babyseat-data:/data chedim/babyseat --fact seat_backend=http://host.docker.internal:11434` (the image has no `claude` CLI; Ollama runs on the host; the admin token: `docker exec <container> cat /data/admin.token`) | at the 0.7.63 release |
| binary | the GitHub release's `babyseat-0.7.63-<target>.tar.gz` and its `.sha256` (targets: `aarch64-apple-darwin`, `x86_64-apple-darwin`, `x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`, `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl`): check the sha256, unpack, run `bin/cliffvm serve` | at the 0.7.63 release |
| Home Assistant | the HACS integration downloads the seat's binary and runs it inside Home Assistant (next section) | at the 0.7.63 release |
| from source | the next section | works today |

`babyseat` and `membrane` are one command: it starts `cliffvm serve` and nothing else. The old subcommands (`opencode`, `store`, `ingest`, ...) are gone.

## Quick start (from source, verified)

```
git clone https://github.com/chedim/membrane && cd membrane
scripts/dist/fetch_cliffvm.sh "$(cat CLIFFVM_VERSION)" <target> ./engine   # the pinned engine release (aarch64-apple-darwin, x86_64-unknown-linux-gnu, ...)
scripts/dist/build.sh --bin ./engine/bin/cliffvm --engine-version "$(cat CLIFFVM_VERSION)" --out ./dist/0.7.63   # assembles the dist folder
ollama pull <a chat model> && ollama pull mxbai-embed-large   # the model that makes the memory's vectors
./dist/0.7.63/bin/cliffvm serve                 # then pick the model in the window the page opens
```

The model is picked in the page; see [Which model answers](#which-model-answers). It prints, in this order:

```
cliffvm serve: claude CLI: /path/to/claude      (only when the claude CLI is on the PATH: the developer mode)
cliffvm serve: data ~/Library/Application Support/membrane (the door's secret and the admin token are made here)
cliffvm: door listening on 127.0.0.1:4096
cliffvm serve: open http://127.0.0.1:4096/
cliffvm serve: a localhost peer is not the admin: a local tool sends the admin token, written (mode 600, new at every start) to <data>/admin.token
cliffvm serve: curl -H "Authorization: Bearer $(cat <data>/admin.token)" http://127.0.0.1:4096/global/health
```

Open the printed URL: the page loads and you are in the chat. The port is 4096 (`--port <n>` changes it, `--port 0` takes any free port); the data folder is
`membrane` under `$XDG_DATA_HOME`, else `~/Library/Application Support` (macOS) or `~/.local/share` (`--data-dir <dir>` changes it). It holds `seat.sqlite`
(the whole memory), `door.secret` (made at the first start) and `admin.token` (new at every start). Stop the seat with ctrl+c; start it again and the chat is as you left it.

From a checkout you can run the same seat on the repo's own `membrane/` folder: `scripts/seat [--open] [--port <n>]` (data in `./.cliffvm`; the development wrapper).

### Which model answers

The local Ollama, and the model is YOURS to pick: the seat starts with none chosen, and a message sent before you pick one starts nothing and says so. The page opens a first-run window
(a dropdown of the models Ollama lists, embedding models left out, and the seat's name) at every load until both are chosen; the same choice is the *Model* row of Settings (a dropdown; a name that is set but not listed stays as an option marked not available).
If the list is empty, Ollama is not running or has nothing pulled: `ollama pull <name>`, then *Refresh*. **Developer mode:** `--fact seat_default=claude` makes the `claude` CLI the model of the chat (a `minds` or `cloud_models` fact works too).

**Which credential the claude CLI uses** (developer mode only). The claude CLI answers with the Anthropic API key in the seat's environment (`ANTHROPIC_API_KEY`).
The key is handed to the CLI child and is never written to a property, a row, the journal, a log or a reply. To use the CLI's own login instead, start with
`--fact chat_auth=login` (the dev wrapper: `CLAUDE_AUTH=login scripts/seat`). With neither, the seat says so at start and a message is answered with how to set it.

**Any OpenAI-compatible endpoint.** `--fact 'cloud_models=[{"name":"<model>","backend":"openai","base_url":"<root URL>","api_key_env":"OPENAI_API_KEY"}]'` plus the same `minds` with that model; the key
is read from the seat's environment (`OPENAI_API_KEY`) and never stored. Anthropic's API works the same way through its OpenAI-compatible endpoint (`"backend":"anthropic"`, `"api_key_env":"ANTHROPIC_API_KEY"`).


**Reaching the seat from another machine.** The door opens on `127.0.0.1` only. `--bind 0.0.0.0` opens it to the network (every route still needs a token), and
`--fact host_names=seat.local` lets the page hand out its cookie for a name you reach it by; from another machine the page itself needs a token (the Home Assistant component carries one).

## Home Assistant

1. HACS, three dots, *Custom repositories*, `https://github.com/chedim/membrane`, category *Integration*; install **babyseat (membrane)** and restart Home Assistant.
2. *Settings, Devices & services, Add integration, babyseat (membrane)*, then one of:
   - **Run a seat inside Home Assistant** (the default): pick the provider (Anthropic with an API key, an OpenAI-compatible endpoint, or Ollama: Home Assistant's own Ollama server and model are filled in) and submit.
     The integration downloads the seat of its own release for this machine (Linux x86_64/aarch64: the static musl build; macOS), checks its sha256 and every file, keeps its data in `<config>/membrane/` and runs it on 127.0.0.1;
     *Configure* changes the provider, model or key later.
   - **Connect to a seat I run**: the seat's address (`http://<seat-machine>:4096`; the seat needs `--bind` for that) and the token in `<data>/admin.token`.
3. *Settings, Voice assistants*: pick the **Membrane seat** conversation agent.

An entry of 0.7.53 that ran the seat inside Home Assistant comes back by itself on its Ollama server, and the new seat imports its chat history. The integration installs no pip package. It makes a hidden Home Assistant user, *Membrane*, and runs every call the model writes as that
user, so Home Assistant's own permissions decide what the model may touch. Details, what is not verified yet, the allow-list and the entities: [docs/home-assistant.md](docs/home-assistant.md).
A Supervisor add-on (`addon/`) is in the repository; it is not supported.

## Upgrading from the Python seat (0.7.x)

Start the new seat once. At its start it looks for the old store (`field.sqlite3` in the places the 0.7 seat used: the new seat's own data folder and its `.membrane` subfolder, `./.membrane`, `~/membrane/.membrane`,
Home Assistant's `/config/membrane/.membrane`), opens it read-only and imports the chat history in the background, newest session first. Nothing else is converted; the old field stays a
read-only source the model can pull from (`Help:legacy`). To name the old store yourself: `cliffvm serve --legacy <path to field.sqlite3, or its folder>`. Details: [docs/cliffvm.md](docs/cliffvm.md), "The history cutover".
Your old data directory is never written. To stay on the Python line: `pip install babyseat==0.7.53` (the source is the tag `python-seat-last`).

## macOS

The seat runs on macOS and Linux alike. On macOS the data folder is `~/Library/Application Support/membrane`. The binary is not signed or notarized: a downloaded copy may be quarantined by Gatekeeper (`xattr -d com.apple.quarantine bin/cliffvm`).

## Documentation

[docs/README.md](docs/README.md) lists every page: what a user reads, what an operator reads, what a developer reads.

## Development

```
cd native && cargo test --release --no-default-features --features bin   # the seat: Rust, real processes, no model
node tests/js/<name>.test.mjs                                            # the page (some need Chrome: CHROME=...)
pytest tests/ha tests/wrapper                                            # the Home Assistant component and the thin Python launcher
```

Layout: `seat-tests/` (the seat's tests, on the pinned engine; the engine itself, the VM and the host in Rust, is github.com/chedim/cliffvm, taken at `CLIFFVM_VERSION`), `membrane/packs/` (the seat's packs, c1f), `membrane/apps/` (the barest app: the packs and the page; `field.c1f`, the disabled 3D view),
`membrane/launcher.py` (the whole Python: finds the binary and execs it), `custom_components/membrane/` (Home Assistant), `scripts/` (`seat`, `dist/`), `docs/`.
[AGENTS.md](AGENTS.md) is the checklist for contributors and coding agents.

## Status

A working lab used every day to build itself. Version **0.7.63**; the API and flag surfaces may still move. Licensing: **MIT** (see [LICENSE](LICENSE)); releases up to 0.7.2 stay under PolyForm Noncommercial 1.0.0.

## Cite

If you use babyseat in research, please cite it. The citation metadata is in [CITATION.cff](CITATION.cff) (GitHub's "Cite this repository" reads it), and
every release is archived on Zenodo under the concept DOI [10.5281/zenodo.23550667](https://doi.org/10.5281/zenodo.23550667), which always resolves to the newest version.
