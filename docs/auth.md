# JWT mode — a token on every request

By default a seat has no login: the OpenAI proxy (:11500) and the serve API + UI
(:4096) answer anyone who can reach them, and standalone's page trusts the local
network. That is fine on a network you own. To put a seat anywhere else (behind an
ingress, on a shared network), turn on **JWT mode**: every request to either port
then needs a signed token, and the token says who is talking.

**It is off unless you set a secret.** With no `MEMBRANE_JWT_SECRET` /
`MEMBRANE_JWT_SECRET_FILE`, nothing on this page applies and the seat behaves
exactly as before.

## Turn it on

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
bytes, empty, unreadable, or both variables set). It never starts half-protected.
The boot log says `[auth] JWT mode ON`.

## Mint a token

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

## Give a client its token

- **OpenAI-compatible clients (:11500)**: the token is the API key. The client
  sends `Authorization: Bearer <token>`.
- **The web UI (:4096, standalone)**: open the page and paste the token when it asks.
  `POST /ui/login` turns it into a cookie (`membrane_jwt`: HttpOnly,
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

## What changes when it is on

- **Every verb on both ports** answers `401` with `WWW-Authenticate: Bearer` and
  `{"error": {"message": "missing or invalid token"}}` without a valid token. Only
  standalone's static page shell (`/`, `/index.html`, `/ui/www/*`, the favicon) and
  `POST /ui/login` are public, so the token prompt can render.
- **The address no longer matters.** Standalone's LAN guard (no login on the local
  network, `403` for anything proxied) is replaced by the token: a valid token
  works from the LAN or behind an ingress, and a missing one is refused on the LAN
  too.
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

## The seat's own calls

The seat calls itself over loopback: the turn loop's `/v1` calls, the MCP tools'
relay, the restart notices, the serve's field reads, the peer asks. In JWT mode
these carry an **internal token** the seat mints in memory at boot (`sub`
`seat:internal`, one hour, re-minted before it runs out). There is no exemption for
loopback addresses. Internal requests keep the speaker line they carry (the serve
already stamped the person's), and they see every session.

When `membrane opencode` launches the opencode TUI, the child gets a token of its
own in its environment only (`MEMBRANE_JWT_TOKEN`, which the generated config's
`apiKey` reads as `{env:MEMBRANE_JWT_TOKEN}`, and `OPENCODE_SERVER_PASSWORD` for
`attach`). No token is written to disk. The MCP child inherits the secret and
mints its own internal token.

Peer seats answer each other's asks only when they share the secret.

## Rotation

Change the secret and restart the seat. Every outstanding token stops working at
once, and the seat mints a new internal token. Then mint new tokens for your
clients.

## Threat model and limits

- HS256 only. A token whose header names any other `alg` (`none`, `HS512`,
  `RS256`, a lower-case `hs256`) or carries `crit` is refused. The signature is
  checked with a constant-time compare before the claims are read.
- `exp` is required. `nbf` and `iat` are checked when present, with 30 seconds of
  leeway for clock skew. `sub` is required.
- A token scoped to an audience is refused by a seat that names no audience.
- Base64url is decoded strictly: no padding, no standard-alphabet characters, no
  extra segments, at most 8 KB.
- An `Authorization` header decides on its own: a bad one never falls back to the
  cookie, and two `Authorization` headers or two `membrane_jwt` cookies are refused.
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
