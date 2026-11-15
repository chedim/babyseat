# babyseat add-on

The add-on runs the babyseat seat (`cliffvm serve`) under the Supervisor and announces it to Home Assistant. Install the **babyseat (membrane)** integration (HACS) too:
the integration is the Assist conversation agent; the add-on is the seat it talks to.

## Install

1. *Settings, Add-ons, Add-on store*, three dots, *Repositories*: add `https://github.com/chedim/membrane`.
2. Install **babyseat**. The Supervisor builds the image on your device: it downloads this version's release tarball from GitHub and checks it against its sha256.
3. Set the options (below), start the add-on.
4. Home Assistant shows a discovered **babyseat (membrane)** integration: confirm it. The address and the token come from the add-on.

## Options

| option | for | meaning |
|---|---|---|
| `seat_name` | all | the model's name; empty keeps the name the seat has (a 0.7 seat's name is carried over once) |
| `provider` | all | `anthropic` (the default), `openai`, `ollama` or `claude_cli` (advanced) |
| `model` | anthropic, openai, ollama | the model id: a Claude model for anthropic (default `claude-sonnet-5-5`), else the endpoint's, e.g. `gpt-4o-mini` or `llama3.2` (change it when you change the provider) |
| `base_url` | openai | the endpoint's root, e.g. `https://api.openai.com`; the seat calls `<base_url>/v1/chat/completions` |
| `api_key` | openai | the endpoint's key (empty: none is sent) |
| `ollama_url` | ollama | the Ollama server, default `http://homeassistant.local:11434` |
| `claude_path` | claude_cli | the absolute path of a claude CLI executable inside the add-on |
| `anthropic_api_key` | anthropic, claude_cli | a Claude API key from the Claude Console, scoped to a workspace |

Examples:

```yaml
provider: anthropic
model: claude-sonnet-5-5
anthropic_api_key: <your Claude API key>
```

```yaml
provider: openai
base_url: https://api.openai.com
model: gpt-4o-mini
api_key: <your key>
```

```yaml
provider: ollama
ollama_url: http://192.168.1.10:11434
model: llama3.2
```

A bad combination (no `anthropic_api_key` for anthropic, no `base_url` or `model` for openai, no `model` for ollama, a `claude_path` that is not an executable) stops the start with a line in the log that names the option.

**The keys** are passwords in the form. They reach the seat only as environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`): never on a command line, in the log or in the seat's store.

**The model pick is kept.** The provider and model you set become the seat's default model at its FIRST start. The seat stores its picks, so after that a change of `provider` or `model` here
does not move the default: pick the model on the seat's page (Settings) as well.

**anthropic (the default).** The seat talks to Claude through Anthropic's OpenAI-compatible endpoint, `https://api.anthropic.com/v1/chat/completions`, with your key sent as the bearer token
(`cloud_models` entry `{name: <model>, backend: anthropic, base_url: https://api.anthropic.com, api_key_env: ANTHROPIC_API_KEY}`). Know this before you rely on it:
- The key must be a **Claude API key from the Claude Console** (https://platform.claude.com/settings/keys).
- Anthropic describes this endpoint as an **OpenAI-compatibility layer intended for testing and comparing models, not a long-term or production solution** for most uses (though it is intended to stay
  functional without breaking changes): https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk
- **Prompt caching is not supported** through it, so every turn pays for its whole prompt.
- A **personal or service-account key that is not scoped to a workspace** needs an `anthropic-workspace-id` header on every request, which the seat cannot send: **use a workspace-scoped key**.

**Ollama.** `homeassistant.local` may not resolve inside an add-on. Use the machine's address (`http://192.168.1.10:11434`), or, for an Ollama add-on on the same Home Assistant, its host name
on the internal network (the add-on's slug with `_` written as `-`, e.g. `http://<repository id>-ollama:11434`). The model must be pulled on that server.

**claude_cli (advanced).** The claude CLI is Anthropic's program and is NOT in the add-on (its licence). To use it, put a Linux claude executable built for musl (the add-on is Alpine) where the add-on sees it:
Home Assistant's config folder is mounted read-only at `/config`, so `/config/claude/claude` on the add-on is `claude/claude` in your config folder. Set `claude_path` to it and
`anthropic_api_key`. Without `claude_path` the seat starts and the chat answers each message with how to supply it. The anthropic, openai and ollama providers need nothing installed.

## What the add-on does at each start

1. Reads the options and builds the seat's launch line: `cliffvm serve --port 4096 --bind 0.0.0.0 --data-dir /data` and `--fact` arguments
   (`seat_name`; `cloud_models` and `minds` for anthropic and openai; `seat_backend` and `minds` for ollama).
2. Starts the seat as the add-on's main process (a stop of the add-on stops the seat cleanly).
3. Waits until the door answers `/global/health` with this start's admin token (at most 120 s), then sends the Supervisor discovery
   `{"service": "membrane", "config": {"host": "<this add-on's host name>", "port": 4096, "token": "<the admin token>"}}`.
   The seat makes a new admin token at every start, so the announcement is sent again at every start.

## Data and the upgrade from the 0.7 seat

The seat's data (`seat.sqlite`, `door.secret`, `admin.token`) is in the add-on's own folder, which Home Assistant backs up. Home Assistant's config folder is mounted read-only at `/config`:
at its start the seat looks for the 0.7 seat's store at `/config/membrane/.membrane` and imports the chat history in the background, read-only (the old folder is never changed).

## The page

Port 4096 is published on the host (change or close it in the add-on's Network section). The page asks for a token: the admin token is in the add-on's data folder (`/data/admin.token`
inside the add-on); the integration gets it by the discovery.

## Build it yourself

The add-on's `Dockerfile` takes the tarballs from `addon/dist/` when that folder holds them (a local build), else from the GitHub release of the version (the build argument
`BABYSEAT_BASE_URL` names another place). From a checkout, with a Linux musl `cliffvm` binary:

```sh
sh scripts/dist/build.sh --bin <musl cliffvm> --target x86_64-unknown-linux-musl --archive addon/dist/babyseat-<version>-x86_64-unknown-linux-musl.tar.gz
docker buildx build --build-arg BUILD_ARCH=amd64 --build-arg BUILD_VERSION=<version> -t local/babyseat-addon addon
```

`<version>` is `version` in `addon/config.yaml`. To try it as a local add-on, copy the `addon/` folder (with `dist/`) into the Supervisor's `/addons` folder (`local_apps`).

## Licence

MIT (the repository's `LICENSE`). The claude CLI is not part of this add-on.
