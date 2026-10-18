# membrane in Docker

The public image `chedim/membrane` runs `membrane standalone` in a container:
the serve API and web UI on **:4096**, the OpenAI-compatible memory proxy on
**:11500**. Point a browser at the first, or any OpenAI-compatible client (an
agent, an IDE plugin, a pod in Kubernetes) at `http://<host>:11500/v1`.

Every published release pushes the tags `X.Y.Z`, `X.Y` and `latest` for
`linux/amd64` and `linux/arm64` (`.github/workflows/docker.yml`).

The image holds the installed package and nothing else. It has no baked-in
backend address, model, key or name: everything specific to your setup comes
in at run time through env and the `/data` volume.

## Run it

The seat needs an [Ollama](https://ollama.com) server with one chat model and
one embedding model, e.g. `ollama pull qwen3:8b && ollama pull mxbai-embed-large`.
Inside a container `localhost` is the container itself, so the default backend
is the Docker host: `http://host.docker.internal:11434`.

**Docker Desktop (macOS, Windows):** `host.docker.internal` resolves to the
host out of the box.

```bash
docker run -d --name membrane \
  -p 4096:4096 -p 11500:11500 \
  -v membrane-data:/data \
  -e MEMBRANE_EMBED_MODEL=mxbai-embed-large \
  --stop-timeout 60 \
  chedim/membrane:latest
```

**Linux:** add the host-gateway mapping so `host.docker.internal` reaches the
host (and make Ollama listen beyond loopback, e.g. `OLLAMA_HOST=0.0.0.0`):

```bash
docker run -d --name membrane \
  --add-host=host.docker.internal:host-gateway \
  -p 4096:4096 -p 11500:11500 \
  -v membrane-data:/data \
  -e MEMBRANE_EMBED_MODEL=mxbai-embed-large \
  --stop-timeout 60 \
  chedim/membrane:latest
```

Ollama in another container on the same network: set
`MEMBRANE_BACKEND=http://ollama:11434` (the service name) instead.

**Compose:**

```yaml
services:
  membrane:
    image: chedim/membrane:latest
    ports: ["4096:4096", "11500:11500"]
    volumes: ["membrane-data:/data"]
    environment:
      MEMBRANE_BACKEND: http://ollama:11434
      MEMBRANE_EMBED_MODEL: mxbai-embed-large
    stop_grace_period: 60s
  ollama:
    image: ollama/ollama
    volumes: ["ollama:/root/.ollama"]
volumes:
  membrane-data:
  ollama:
```

The first boot takes about a minute; the container reports `healthy` once the
proxy answers `GET /membrane/state` (the healthcheck allows a 90 s start period).

## Environment

| Variable | Flag | Default | What |
|---|---|---|---|
| `MEMBRANE_BACKEND` | `--backend` | `http://host.docker.internal:11434` | the Ollama server the seat thinks with |
| `MEMBRANE_MODEL` | `--model` | the best chat model Ollama lists | the chat model |
| `MEMBRANE_EMBED_MODEL` | `--embed-model` | a known embedding model Ollama has | the field's embedder; chat fails without one. Keep it once the field has grown |

Set in the image, all `1`: `MEMBRANE_NO_UPDATE_CHECK` (no boot-time update
check; upgrade by pulling a newer tag), `MEMBRANE_NO_AUTOINSTALL` (no skills
written into the workdir), `MEMBRANE_NO_BROWSER`, `MEMBRANE_NO_ASK` (no
interactive prompts).

Cloud models: run `membrane auth` once inside the container
(`docker exec -it membrane membrane auth`); the key lands in
`/data/.membrane/auth.json` on the volume.

## Volume

`/data` is the working directory **and** `HOME`, so one volume holds both the
project field (`/data/.membrane/`: the field, the audit log, sessions,
`settings.json`) and the per-user state under `~/.membrane` (`auth.json`, the
global field, the project env), which here is the same directory.
Keep the volume to keep the memory.

The container runs as uid 10001. A named volume takes that ownership on first
use; a bind mount must be writable by uid 10001.

## Ports

| Port | What |
|---|---|
| 4096 | the serve API (opencode-compatible REST/SSE) and the web UI |
| 11500 | the OpenAI-compatible proxy (`/v1`) and `/membrane/state` |

Both are fixed inside the container (outside one the seat walks up to the next
free port); remap them with `-p`, e.g. `-p 14096:4096`.

**Neither port has authentication today.** The proxy's write and exec routes
and the serve API answer anyone who can reach them. Publish the ports only on
a network you trust, or put a reverse proxy with its own login in front (the
web UI refuses requests a reverse proxy forwarded; see
[standalone.md](standalone.md#who-can-reach-it)). An authenticated mode for
the container is tracked in issue #203.

## Commands

The default command is `standalone`. Pass another after the image name; the
seat commands (`standalone`, `serve`, `opencode`) get the env flags and the
fixed ports, the rest run as given:

```bash
docker run ... chedim/membrane serve --no-dreamer     # headless, no web UI
docker exec membrane membrane doctor                   # in a running seat
docker run --rm chedim/membrane --help
```

Extra seat flags follow the command; anything after `--` passes through.

## Stopping

`docker stop` sends SIGTERM to the seat (it is PID 1) and the graceful
teardown flushes the field. That teardown can take up to 90 s on a large
field, while Docker's default grace period is 10 s before SIGKILL. Give it at
least 60 s: `--stop-timeout 60` on `docker run`, `stop_grace_period: 60s` in
Compose, `terminationGracePeriodSeconds: 60` in Kubernetes.

## Building it yourself

```bash
docker build -t membrane:dev .
```

The build context is an allowlist (`.dockerignore`): only `pyproject.toml`,
`README.md`, `LICENSE`, `membrane/` and `docker/` reach the build, so a dev
tree's `.membrane/`, field dumps and logs never enter an image.
