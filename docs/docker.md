# babyseat in Docker

The public image `chedim/babyseat` (babyseat, formerly membrane) runs
`membrane standalone` in a container:
the serve API and web UI on **:4096**, the OpenAI-compatible memory proxy on
**:11500**. Point a browser at the first, or any OpenAI-compatible client (an
agent, an IDE plugin, a pod in Kubernetes) at `http://<host>:11500/v1`. Ollama
clients aim at `http://<host>:11500`: `/api/chat` and `/api/generate` run the same
memory turn, the rest of `/api/*` passes through to Ollama.

Every published release pushes the tags `X.Y.Z`, `X.Y` and `latest` for
`linux/amd64` and `linux/arm64` (`.github/workflows/docker.yml`). The same
tags go to `chedim/membrane`, the image's name before 0.7, so an existing
`docker pull chedim/membrane` keeps updating; new setups use `chedim/babyseat`.
Inside the image nothing was renamed: the `membrane` command, the `MEMBRANE_*`
variables and the `/data/.membrane` state are the same, so a container (and its
volume) moves to the new image by changing only the image name.

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
  chedim/babyseat:latest
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
  chedim/babyseat:latest
```

Ollama in another container on the same network: set
`MEMBRANE_BACKEND=http://ollama:11434` (the service name) instead.

**Compose:**

```yaml
services:
  membrane:
    image: chedim/babyseat:latest
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
| `MEMBRANE_SEAT_TOKEN_PRINT` | | `1` (the entrypoint sets it) | print the [seat token](auth.md#the-seat-token) to the log at boot |
| `MEMBRANE_JWT_SECRET` | | unset (JWT mode off) | turns on [JWT mode](auth.md): every request on both ports needs a token. At least 32 bytes |
| `MEMBRANE_JWT_SECRET_FILE` | | unset | the secret read from a mounted file instead (a Docker / Kubernetes secret); set one or the other |
| `MEMBRANE_JWT_ISSUER` / `MEMBRANE_JWT_AUDIENCE` | | unset | optional `iss` / `aud` every token must carry |

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
`settings.json`) and the per-user state under `~/.membrane` (`auth.json`,
`seat-token`, the global field, the project env), which here is the same directory.
Keep the volume to keep the memory.

The container runs as uid 10001. A named volume takes that ownership on first
use; a bind mount must be writable by uid 10001.

## Ports

| Port | What |
|---|---|
| 4096 | the serve API (opencode-compatible REST/SSE) and the web UI |
| 11500 | the memory proxy: OpenAI's `/v1`, Ollama's `/api/chat` and `/api/generate` (other `/api/*` pass through), and `/membrane/state` |

Both are fixed inside the container (outside one the seat walks up to the next
free port); remap them with `-p`, e.g. `-p 14096:4096`.

**Out of the box, reads are open and writes need the seat token.** Inside a
container no caller is on loopback (Docker's port mapping arrives from the
bridge), so everything that changes something (a chat turn, an entity, a
script, a model, the seat's controls) needs the [seat token](auth.md#the-seat-token).
The seat makes it on first boot at `/data/.membrane/seat-token` (on the volume,
so it survives upgrades) and prints it to the log:

```bash
docker logs membrane 2>&1 | grep 'seat token:'
docker exec membrane membrane token seat            # the same, any time
docker exec membrane membrane token seat --rotate   # a new one
```

The web UI asks for it once per browser; an OpenAI-compatible client sends it
as its API key (`Authorization: Bearer <seat token>`). Reads (the field, the
UI's pages, `/v1/models`) need no token, so publish the ports only on a network
you trust, or turn on JWT mode: give the container a secret and every request
on both ports, reads included, needs a token.

```bash
docker run -d --name membrane \
  -p 4096:4096 -p 11500:11500 -v membrane-data:/data \
  -e MEMBRANE_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  -e MEMBRANE_BACKEND=http://host.docker.internal:11434 \
  --stop-timeout 60 chedim/babyseat:latest

# a token for a person (exec inherits the container's environment)
docker exec membrane membrane token mint --sub alice --name Alice
```

Keep the secret somewhere you can find it again: a new secret invalidates
every token. Prefer `MEMBRANE_JWT_SECRET_FILE` with a mounted secret over a
plain `-e` in shared environments. An OpenAI-compatible client sends the token
as its API key (`Authorization: Bearer <token>`); the web UI asks for it once
and keeps it in a cookie. With a valid token the UI also works behind a
reverse proxy. Details: [auth.md](auth.md).

## Commands

The default command is `standalone`. Pass another after the image name; the
seat commands (`standalone`, `serve`, `opencode`) get the env flags and the
fixed ports, the rest run as given:

```bash
docker run ... chedim/babyseat serve --no-dreamer     # headless, no web UI
docker exec membrane membrane doctor                   # in a running seat
docker run --rm chedim/babyseat --help
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
