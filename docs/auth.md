# Auth on the cliffvm seat: a localhost peer is not the owner

*This section is the standalone `cliffvm` seat (`scripts/seat-full`, docs/cliffvm.md). The rest of this page describes the Python 0.7 seat (seat token, `MEMBRANE_JWT`), kept until the swap.*

**The rule** (the operator's, #842): an address is not a credential. When the door has a secret (the fact `door_secret_file`, set by `scripts/seat-full`), every request needs a verified JWT, a request
from this machine included. Public: the page's shell (`/`, `/index.html`, `/membrane`, `/favicon.ico`), `/ui/www/*`, `/ui/login`, `/ui/logout` (the released seat's set; `/global/health` and the opencode probes are NOT public).
No token, a bad one, one signed by another key or one past its life: `401`, `WWW-Authenticate: Bearer realm="membrane"`, `{"error":{"message":"missing or invalid token","type":"unauthorized"}}`, the same for each.
The only mode without a secret is the BAREST seat (`scripts/seat`, no `door_secret_file`): no security at all, by decision (docs/cliffvm.md).

**The owner token file.** `cliffvm run` with `door_secret_file`:
1. makes the secret if it is missing (64 hex characters of `/dev/urandom`, mode 0600) and keeps it afterwards;
2. mints an owner JWT at EVERY boot (`sub` = the fact `operator`, default `owner`; life = the fact `owner_token_ttl_s`, default 2592000 s = 30 days, a threshold flagged for the operator) and writes it to `<seat_data_dir>/owner.token`
   (mode 0600, a temp file renamed over the old one); the path is the Boot fact `owner_token_file`. Rotation is the restart: the old token stays valid until its own `exp` (only a new secret revokes it).
3. holds the token's value nowhere else but the process's environment (`MEMBRANE_OWNER_TOKEN`): it is no property of the field, no journal row, no log line and no reply.

**Local tools** read the file: `curl -H "Authorization: Bearer $(cat <seat_data_dir>/owner.token)" http://127.0.0.1:<port>/global/health` (`scripts/seat-full` prints this line with the real paths).
**The page** needs nothing: a page fetched from this machine (a loopback peer, no `X-Forwarded-For`/`Forwarded`) is given the owner cookie `membrane_jwt_<8 hex>` (HttpOnly, SameSite=Strict, 7 days); `POST /ui/login {"token": "<jwt>"}` turns any token into the same cookie.
**The opencode TUI** takes its token from the environment, as on the Python seat: `OPENCODE_SERVER_PASSWORD="$(cat <seat_data_dir>/owner.token)" opencode attach http://127.0.0.1:<port>` (Basic password = the token), and the generated config's `apiKey` reads `{env:MEMBRANE_JWT_TOKEN}`.
**The chat turn**: the `claude` CLI the turn starts calls the seat's `/mcp`. Its MCP config (`chat_turn.c1f`, `mcp_json`) carries `"headers": {"Authorization": "Bearer ${MEMBRANE_OWNER_TOKEN}"}` and the run's `env_from` hands the launcher's value to the child's environment: the token is never in `argv` (a process listing shows `argv`)
and never in a row. A turn whose CLI sends a wrong token fails visibly: the `401` is in the answer.

**Other people, and who is the owner (w34).** `cliffvm token mint --sub alice --name Alice --ttl 30d --secret-file <seat>/door.secret` prints a token (ttl required, no default; `12h`, `15m`, `45s` or seconds work too). The first verified request of a token makes `Person:<sub>` (sub, name, first_seen, last_seen). The owner is the person whose sub is the fact `operator` (default `owner`), the sub of the boot's owner token; only the owner's calls to `/mcp` `entity_write` (packs included) and `run_cliff` run, any other person gets `403` (`for the owner`); reads are open to every verified person.

## The Python seat: the seat token, and JWT mode

A seat has two ports: the OpenAI proxy (:11500) and the serve API + UI (:4096).
Both listen on every interface, so the phone and Home Assistant reach the seat
over Wi-Fi. Two levels protect them:

- **The seat token (only when JWT mode is off: `MEMBRANE_JWT=0`).** Every request,
  reads included, needs it, localhost too: an address is not a credential. The one
  open read on the Ollama port is `GET /api/tags`. The page's cookie is minted only
  on this machine (in JWT mode it carries an owner JWT).
- **JWT mode (on by default).** Every request on both ports, reads included, needs
  a signed token, and the token says who is talking. A caller on this machine
  needs a token too; the page served to this machine gets an owner cookie. Use it to
  put a seat behind an ingress or on a network other people share.

## The seat token

The seat makes a random token on its first boot and keeps it at
`~/.membrane/seat-token` (mode 0600). Print it on the seat's machine:

```bash
membrane token seat              # prints the token (makes it if it is missing)
membrane token seat --path       # where it lives
membrane token seat --rotate     # a new one; the old one stops working at once
# in Docker: docker exec membrane membrane token seat
```

It lives in your home directory, not in the project's `.membrane/`, so the CLI
finds the same file whatever directory you run it from, and every seat you run
as the same user shares it. `MEMBRANE_SEAT_TOKEN_FILE` moves it. In the Docker
image `HOME` is `/data`, so the token is on the volume (`/data/.membrane/seat-token`).

**Who needs it.** A caller is *local* only when the socket's peer address is
loopback (`127.0.0.1`, `::1`). Headers never make a caller local: a request
carrying `X-Forwarded-For`, `Forwarded` or `X-Real-IP` counts as off-box even on
loopback, so everyone behind a reverse proxy needs the token. Without it, an
off-box write gets `401` with `WWW-Authenticate: Bearer`.

**How to send it.**

- **OpenAI-compatible clients and scripts (:11500 and :4096):**
  `Authorization: Bearer <seat token>`.
- **Home Assistant, opencode attach (:4096):** HTTP Basic with the token as the
  password (the user name is ignored). In the integration's *Connect to a seat
  running elsewhere* form, put it in **Password**; for an integration that is
  already set up, use *Configure* → **Edit a seat** → **Seat token** (each seat of
the [zoo](home-assistant.md#the-zoo-several-seats) carries its own). A seat Home Assistant runs
  itself listens on loopback and needs none.
  `OPENCODE_SERVER_PASSWORD=<seat token> opencode attach http://seat:4096`
- **The web UI from another machine (:4096, standalone):** the page shell loads,
  then asks for the seat token once; GET reads need the token too, not only
  writes (only the shell, `/ui/www/`, `/ui/conf` and health stay open). The
  cookie is minted on loopback only, and the seat's internal readers send
  loopback headers. `POST /ui/login` turns it into the same
  HttpOnly cookie JWT mode uses (`membrane_jwt_<8 hex>`, named per seat, SameSite=Strict, a 400-day life,
  Secure over https). The page then asks your name as usual.

**What needs it** (from off-box, JWT mode off):

| Port | Needs the token | Open |
|---|---|---|
| :11500 | every `POST`/`PUT`/`PATCH`/`DELETE`: `/v1/chat/completions`, Ollama's `/api/chat` and `/api/generate`, `/v1/embeddings`, every `/membrane/*` write (entity add/edit/remove/inherit, method define/call/each, filetool, learn/forget, intend/fulfill, watch, attention, entity-track, junk-cut, delegate, peer/post), and every other `/api/*` write passed to Ollama (pull, delete, create, ...); also `GET /membrane/intend`, which creates an intent; every `GET`/`HEAD` under `/v1/`, `/membrane/` and `/state` needs the token too | only `GET /api/tags` (and other `/api/*` reads); no `POST` is open: the queries (`/membrane/entity-query`, `/membrane/codegraph`, ...) read the field and need the token, as does every `GET`/`HEAD` under `/v1/` and `/membrane/` and `/state` |
| :4096 | every `POST`/`PATCH`/`DELETE`: sessions and their messages and prompts, `/membrane/*` (toybox poke, models, seat control and restart asks, field purge, voice, HA token and states, the JS executor), `/tui/*`, `/log`, `/instance/dispose`, the page's `/ui/serve/*`, `/ui/stt`, `/ui/log` | every `GET`; `POST /ui/login` and `/ui/logout` |

The chat routes count as writes: every turn is captured into the field, its
frames (`membrane: learn`, `forget`, `watch`, ...) change it directly, and the
turn's tool rounds reach the entity, script and executor lanes.

Fields that hold a script (retired in 0.7.5, still refused here: `tick_script`, `getter_script`, `watch_script`, every
`*_script`) and a method's command are code: an off-box caller without the
token never lands one, whatever the settings say. The entity edit and method
routes check this a second time behind the route's gate.

The login page's visitors (`/membrane/stranger/*`, GET) stay open: they are
built for anonymous visitors, take only numbers, and are capped.

The seat's own calls (the turn loop, the MCP tools, restarts, the field reads,
peer asks) come from loopback and need nothing.

## JWT mode — a token on every request

**It is on by default.** Nothing to set: at boot the seat makes two secrets
(both 0600) and JWT mode replaces the seat token. The seat token is not
accepted, and a JWT is needed for every request, as described here.

- **Per-folder people secret**: `<seat folder>/.membrane/jwt.secret`. It signs the
  tokens people use (`membrane token mint`), so each folder's seat has its own and
  a token from one folder is refused by another. Made at boot if missing; a command
  run from a folder with no seat reports "no seat here" and writes nothing.
- **Per-machine startup secret**: `~/.membrane/jwt.secret`. It signs only the
  seat's own internal token (`seat:internal`), so the seat's children and loopback
  callers work with no setup. It can never sign a person.
- **Explicit secret wins**: `MEMBRANE_JWT_SECRET` / `MEMBRANE_JWT_SECRET_FILE`
  replaces the folder secret (use it for an ingress or a shared secret mount).
- **Opt out**: `MEMBRANE_JWT=0` turns JWT mode off whatever else is set, and the
  seat token applies again.

### Use your own secret

```bash
export MEMBRANE_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
membrane standalone            # or: membrane serve / membrane opencode
```

| Variable | What |
|---|---|
| `MEMBRANE_JWT_SECRET` | the HS256 secret, at least 32 bytes |
| `MEMBRANE_JWT_SECRET_FILE` | read the secret from a file instead (a Kubernetes / Docker secret mount; a trailing newline is dropped). Set one or the other, not both |
| `MEMBRANE_JWT_ISSUER` | optional: tokens must carry this `iss` |
| `MEMBRANE_JWT_AUDIENCE` | optional: tokens must carry this `aud` (a string, or a list containing it) |

The seat refuses to start when the secret is set but unusable (shorter than 32
bytes, empty, unreadable, or both variables set), or when it cannot make its
secret file (read-only folder or home). It never starts half-protected.
The boot log says `[auth] JWT mode ON`.

### Mint a token

```bash
membrane token mint --sub alice --name Alice                 # 30 days
membrane token mint --sub kitchen-tablet --ttl 12h
# in Docker: docker exec membrane membrane token mint --sub alice --name Alice
```

`--sub` is the person's stable id: their memory is keyed by it. `--name` is what the
seat calls them. `--ttl` takes `30d`, `12h`, `15m`, `45s` or plain seconds. The
command reads the same environment as the seat (and adds `iss`/`aud` when those are
set). Ids starting with `seat:` are the seat's own and can't be minted.

Any HS256 JWT signed with the secret works, so an identity provider that can sign
with a shared secret can issue them too.

### Give a client its token

- **OpenAI-compatible clients (:11500)**: the token is the API key. The client
  sends `Authorization: Bearer <token>`.
- **The web UI (:4096, standalone)**: open the page and paste the token when it asks.
  `POST /ui/login` turns it into a cookie (`membrane_jwt_<8 hex>`, the seat's own name -- a hash of its secret, so two seats on one host
  never overwrite each other's cookie; the old `membrane_jwt` is still read: HttpOnly,
  SameSite=Strict, and Secure when the page came over https, including through a
  proxy that sets `X-Forwarded-Proto: https`). The page's script never keeps the
  token. `POST /ui/logout` clears the cookie.
- **opencode attach, Home Assistant, scripts that speak Basic auth (:4096)**: use
  the token as the password (the user name is ignored), e.g.
  `OPENCODE_SERVER_PASSWORD=<token> opencode attach http://seat:4096`.
- **The membrane MCP server** run by another client against a JWT seat: set
  `MEMBRANE_JWT_TOKEN=<token>` in its environment.

A token in the query string (`?access_token=...`) is never read: URLs end up in
logs and browser history.

### What changes when it is on

- **Every verb on both ports** answers `401` with `WWW-Authenticate: Bearer` and
  `{"error": {"message": "missing or invalid token"}}` without a valid token. Only
  standalone's static page shell (`/`, `/index.html`, `/ui/www/*`, the favicon) and
  `POST /ui/login` are public, so the token prompt can render.
- **The address no longer matters.** Standalone's LAN guard (no login on the local
  network, `403` for anything proxied) and the seat token are replaced by the JWT:
  a valid token works from the LAN or behind an ingress, and a missing one is
  refused on the LAN and behind a proxy. On localhost (a loopback peer with no
  `X-Forwarded-For`, `Forwarded` or `X-Real-IP`) a request with no token at all is
  the seat itself: it keeps its own speaker line, sees every session, and the page
  asks for a name. A token it does send is checked like any other, so a bad one is
  still `401`.
- **The token's identity is who said it.** Any speaker line a client sends
  (`[user: …]`, `[HA user: …]`, the page's `X-Membrane-Speaker`) is dropped, and
  the seat stamps `[user#<sub>: <name>]` itself, on `/v1/chat/completions` and on
  Ollama's `/api/chat` and `/api/generate` alike. In the field the person is
  `person:<sub>` (slugged), labelled with the name.
- **Sessions on :4096 belong to the token's `sub`.** `GET /session`,
  `/session/status`, every `/session/<id>/…` route and the `/event` stream show a
  person only their own sessions (and those sessions' children). Anyone else's
  session is a `404`. The seat's own news (its name, the field pulse) still reaches
  everyone with a token.
- **Your token never goes upstream.** The proxy drops the inbound `Authorization`
  header and cookies before forwarding anything to Ollama or a provider.

### The seat's own calls

The seat calls itself over loopback: the turn loop's `/v1` calls, the MCP tools'
relay, the restart notices, the serve's field reads, the peer asks. In JWT mode
these carry an **internal token** the seat mints in memory at boot (`sub`
`seat:internal`, one hour, re-minted before it runs out). A bare loopback request
(no token, no forwarding header) passes as the same identity. Internal requests keep the speaker line they carry (the serve
already stamped the person's), and they see every session.

When `membrane opencode` launches the opencode TUI, the child gets a token of its
own in its environment only (`MEMBRANE_JWT_TOKEN`, which the generated config's
`apiKey` reads as `{env:MEMBRANE_JWT_TOKEN}`, and `OPENCODE_SERVER_PASSWORD` for
`attach`). No token is written to disk. The MCP child inherits the secret and
mints its own internal token.

Peer seats answer each other's asks only when they share the secret.

### Rotation

Change the secret and restart the seat. Every outstanding token stops working at
once, and the seat mints a new internal token. Then mint new tokens for your
clients.

## Threat model and limits

- The seat token is one shared secret: whoever holds it can do anything the
  owner can from off-box. There are no people behind it (the page still asks a
  name, as on localhost). For several people, use JWT mode.
- The seat token is compared in constant time. A query-string token is never
  read, and an `Authorization` header that is wrong never falls back to the
  cookie.

- HS256 only. A token whose header names any other `alg` (`none`, `HS512`,
  `RS256`, a lower-case `hs256`) or carries `crit` is refused. The signature is
  checked with a constant-time compare before the claims are read.
- `exp` is required. `nbf` and `iat` are checked when present, with 30 seconds of
  leeway for clock skew. `sub` is required.
- A token scoped to an audience is refused by a seat that names no audience.
- Base64url is decoded strictly: no padding, no standard-alphabet characters, no
  extra segments, at most 8 KB.
- An `Authorization` header decides on its own: a bad one never falls back to the
  cookie, and two `Authorization` headers or two cookies of the same name are refused.
- A refused token is told only `missing or invalid token`, never why.
- The login only accepts JSON, so a cross-site form can't post a token into
  someone's browser.
- Every valid token is equal: there are no roles yet. Anyone with a token can use
  the seat-wide routes (models, the seat's controls, the field).
- One token is one person. A relay that speaks for several people under one token
  (Home Assistant's chat relay) shows up as that token's single person.
- The dev viewer on :11500 (`--viewer`, off by default) is a browser page that
  can't send a Bearer header, so it doesn't work in JWT mode.
- A self-hosted ingress should terminate TLS: the token is a bearer secret on
  the wire.
