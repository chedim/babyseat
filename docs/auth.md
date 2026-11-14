# Auth: who may talk to the seat

Status: describes `cliffvm serve` at 0.7.60 (`membrane/packs/door.c1f`, `door_auth.c1f`, `native/src/{jwt,admin,http}.rs`). Pinned by `native/tests/door_auth_1143.rs` and `release_verify_1143.rs` (real processes; see docs/release-verification.md for the checklist).

## No users: every valid token is the admin

There is one identity, `admin`. A request that carries a VALID token (signature, `exp`, `nbf` and the rest checked) or the page's cookie is the admin, whatever the token's `sub` says:
the door replaces `sub` and `name` by `admin` after it has verified the token, so no pack reads a `sub` as an identity. There is no person entity, no second tier, nothing that
answers a verified caller `403`: the one refusal is the `401` of a request with no valid token. A non-human credential that must be limited (an ESP32, an integration) is a separate design
(the `ns` claim of docs/graph-link.md); nothing is built for it. The word `owner` survives only for graph ownership (a class is owned by its pack: docs/c1f-layers.md).

**One chat, many sessions.** There are no per-person chats (the 0.7 seat's one-chat-per-person rule is gone). The chat is one entity, `Chat:main`; the page's sessions are chats of their own
(`POST /session` always makes a new one). The history of an older seat comes in through the cutover (docs/migration-design.md).

## The rule: an address is not a credential

When the door has a secret (`cliffvm serve` always makes one), EVERY request needs a verified JWT, a request from this machine included. Public: the page's shell
(`/`, `/index.html`, `/membrane`, `/favicon.ico`), `/ui/www/*`, `/ui/login`, `/ui/logout`. `/global/health` is NOT public.
No token, a bad one, one signed by another key or one past its life: `401`, `WWW-Authenticate: Bearer realm="membrane"`,
`{"error":{"message":"missing or invalid token","type":"unauthorized"}}`, the same body for each. A request with no token also changes nothing: the getters that start a write or an effect read `Request.entrant`, not the reply.

The only way to an open door is to name no secret on purpose (`--fact door_secret_file=`): then there is no admin token, `/ui/conf` says `auth: none`, and every caller is the admin. That is for tests and trusted sandboxes.

## The files `cliffvm serve` makes (the data folder, `--data-dir`)

- `door.secret`: 64 hex characters from `/dev/urandom`, mode 0600, made at the first start and kept. A missing or empty file is made; one shorter than 32 bytes, unreadable, or a folder REFUSES the start (the seat never
  replaces a secret it cannot read: that would log everyone out). A second seat started on a copy of the file accepts the first's tokens (an old Python seat's secret is accepted too).
- `admin.token`: an admin JWT minted at EVERY start (`sub` `admin`; life = the fact `admin_token_ttl_s`, default 2592000 s = 30 days), mode 0600, a temp file renamed over the old one. The path is the Boot fact `admin_token_file`.
  Rotation is the restart; the old token stays valid until its own `exp` (only a new secret revokes it).
- The token's value is held nowhere else but the process's environment (`MEMBRANE_ADMIN_TOKEN`): it is in no property, row, journal line, log line or reply.

## How each caller authenticates

- **A tool on this machine** reads the file: `curl -H "Authorization: Bearer $(cat <data-dir>/admin.token)" http://127.0.0.1:4096/global/health` (`cliffvm serve` prints this line with the real paths).
  A token is read only from the `Authorization` header (Bearer, or Basic with the token as the password: what Home Assistant sends) or this seat's cookie; never from the query string.
- **The page.** A page fetched from this machine is given the admin cookie `membrane_jwt_<8 hex of the secret's hash>` (HttpOnly, SameSite=Strict, Path `/`, 7 days). "From this machine" is three tests together:
  a loopback peer, no `X-Forwarded-For` / `Forwarded` / `X-Real-IP` header, and a `Host` that is `localhost`, `127.0.0.1`, `[::1]` or a name in the launch fact `host_names` (a JSON list or comma text; port ignored, case-insensitive).
  The host check stops a hostile site that points its own name at `127.0.0.1` (DNS rebinding) from being handed the cookie. A page reached through the LAN address or a proxy gets no cookie and its calls are `401`; the page says so in text.
  `POST /ui/login {"token": "<jwt>"}` turns any valid token into the same cookie (JSON only: 415 for a form, 401 for a bad token; `Secure` behind https; `/ui/logout` clears it).
  A sign-in screen in the page that uses this route is in progress (branch `w120-signin`); until it lands, a page on another machine cannot get in without a cookie set by that route.
- **Home Assistant** sends the token as the Basic password; paste `admin.token` (or any minted token) in the form (docs/home-assistant.md).
- **The chat turn.** The `claude` CLI the turn starts calls the seat's `/mcp`. Its MCP config (`chat_turn.c1f`, `mcp_json`) carries `"headers": {"Authorization": "Bearer ${MEMBRANE_ADMIN_TOKEN}"}` and the run's `env_from` hands the
  seat's value to the child's environment, so the token is never in `argv` (a process listing shows `argv`) or a row. A turn whose CLI sends a wrong token fails visibly: the `401` is in the answer.
- **More tokens.** `cliffvm token mint --sub ha --name "Home Assistant" --ttl 30d --secret-file <data-dir>/door.secret` prints a token (`--ttl` is required, no default; `12h`, `15m`, `45s` or plain seconds work).
  `--sub` and `--name` are labels only: the token is the admin like every valid token. `seat:` subs are refused (reserved). The secret file must already exist (a started seat made it).

## What the door checks (the verified list)

The door refuses `crit`, an `aud` and a non-numeric `nbf`/`iat`; the leeway is exactly 30 s; `alg` must be HS256; the door removes `authorization` and `cookie` from the Request entity after verifying them
(an open connection's entity never hands its credentials to a reader); a HEAD to a family route is a 405 with `allow`; an `/ui/serve/` prefix, `..`, `//`, upper case, `%2f` and the like never reach a route without the gate.
The evidence for each is in docs/release-verification.md.

## The claude turn's credential

The turn's CLI answers with the Anthropic API key of the seat's environment variable `ANTHROPIC_API_KEY` by default; `--fact chat_auth=login` opts into the CLI's own login (the dev wrapper maps `CLAUDE_AUTH=login`).
The key travels like the admin token: the run's `env_from` hands the seat's value to the child's environment, so it is never in `argv`, a property, a row, the journal, a log or a reply, and the Command driver masks it (`***`)
in anything the child prints (only values of 16 or more characters: a shorter one is no credential). In key mode the credentials the CLI would rank above the key (`ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK` / `_VERTEX` / `_FOUNDRY`,
`CLAUDE_CODE_OAUTH_TOKEN`) are removed from the child's environment, so a stored login cannot answer. With no key and no opt-in the turn is refused with the way to set it; no `claude` process starts.
`GET /membrane/options` shows the mode (`background.chat_auth`).

## Environment variables: what a pack may read

A pack reads the seat's environment through ONE entity, `Environment:host` (docs/cliffvm.md, "The environment"; `Help:environment` for the model): its properties are the variables. Only a short default list (`HOME`, `USER`, `LANG`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `TMPDIR`, `CLAUDE_BIN`, `MEMBRANE_DATA_DIR`) and the names in the launch fact `env_allow` carry their value.
A name holding KEY, TOKEN, SECRET, PASSWORD, PASSWD or CREDENTIAL reads the text `set` when set, never its value, and `env_allow` refuses such a name. So `ANTHROPIC_API_KEY` is visible to a pack only as `set`; the key itself still reaches only the claude child.
