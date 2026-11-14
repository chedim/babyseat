# Release verification: the auth checklist (#1143, 2026-11-14)

What was verified, by which test, and what it found. Seats under test are REAL private seats started by the release launcher (`scripts/seat`: the shipped barest packs and app, the strict door,
its own data folder, a free port, a fake `claude`; never a live seat, never a model). `PASS` = proved by a test that passes on this tree; `GAP` = a fact the operator must decide, nothing built.

Tests: `native/tests/release_verify_1143.rs` (Rust, real processes), `tests/js/release_verify.test.mjs` (headless Chrome), `tests/ha/test_release_launcher.py` (the Home Assistant component).

## The table

| # | Item | Evidence (test) | Status |
|---|---|---|---|
| 1a | The door secret persists across restarts; a token minted before a restart verifies after; the owner token regenerated at boot is valid and different; a token of a different secret is 401; `door.secret` and `owner.token` are mode 0600 | `the_secret_persists_across_restarts_tokens_survive_it_and_a_foreign_secret_is_refused` | PASS |
| 1b | The secret and every token (owner, person, the page's cookie token) are in no reply of 26 routes, no entity (all of them, whole), no stdout or stderr line, no byte of the store or its WAL (running and stopped), no process listing; also not base64url or hex encoded | `the_secret_and_every_token_are_in_no_reply_log_store_or_journal` | PASS |
| 1c | Reuse: a second seat started on a copy of the first seat's `door.secret` keeps the file, accepts the first seat's owner and person tokens (and the first accepts the second's); the cookie name is the same (a hash of the secret). A Python seat's `token_urlsafe(48)` secret is kept and its tokens verify | `a_second_seat_started_with_the_first_ones_secret_accepts_the_first_ones_tokens` | PASS (documented below) |
| 1d | Secret file missing, empty or blank: made (64 hex, 0600), door strict. Shorter than 32 bytes: the seat REFUSES to start (exit 2, names 32), writes no owner token, leaves the file. Unreadable (mode 000) or a folder: REFUSES, secret untouched | `a_missing_or_empty_secret_file_is_made_and_a_short_one_is_a_refusal_not_an_open_door`, `an_unreadable_secret_file_is_a_refusal_the_secret_is_never_silently_replaced` | PASS (after fix 5) |
| 1e | The only way to an open door is naming no secret on purpose (`--fact door_secret_file=`): then no owner token, `/ui/conf` says `auth: none` | `naming_no_secret_at_all_is_the_open_barest_seat_and_says_so` | PASS (by decision) |
| 2a | The page fetched from loopback is given its owner cookie: name `membrane_jwt_<8 hex of the secret's hash>`, HttpOnly, SameSite=Strict, Path=/, Max-Age 604800, no Secure on http, no Domain; the token is the owner's, signed with the secret; only the page itself carries it, not its files; `operator=ann` makes it ann's | `the_page_fetched_from_this_machine_is_given_the_owner_cookie_with_the_attributes_the_door_names` | PASS |
| 2b | In a REAL browser: the cookie is set (HttpOnly, Strict, Path /, 7 days, signed by the seat's secret, `document.cookie` is empty), then the chat works (message, answer, history after a reload, no 401 on the page's own calls) and Settings read and write (the seat holds the write); no token or secret in the page | `release_verify.test.mjs` part 1 | PASS |
| 2c | The same page through the machine's LAN address (`--bind 0.0.0.0`): no cookie; every protected route is the same plain `401` JSON; the page says in text "The page could not start: the seat's /ui/fieldvm answered 401" (never blank) | `the_page_through_the_machines_lan_address_...` and `release_verify.test.mjs` part 2 | PASS |
| 2d | A way in for a person with only a token | the page has NO sign-in screen. The door has the route: `POST /ui/login {"token"}` (JSON only, 415 for a form, 401 for a bad token, cookie as long as the token, Secure behind an https proxy, `/ui/logout` clears it); from the page's own origin it works and the page then runs | **GAP** (decision 2) |
| 3a | A second person (`cliffvm token mint --sub bob`) reads: health, chat history, the settings feed (all rows), sessions, `/ui/conf` (says who), `/mcp` tools/list, entity_read, entity_query | `a_second_person_reads_everything_the_page_reads_and_is_refused_every_owner_only_write_with_nothing_changed` | PASS |
| 3b | Every owner-only write is 403 `{"type":"error","error":{"message":"for the owner"}}` and CHANGES NOTHING (entity list, setting, minds, options compared before and after): `/mcp` entity_write (Note, `Setting:*.set` for the model, the step limit and the allow-list, `HaAllow`, `HaCall`, a `Pack:`, `Person:owner`), run_cliff, `POST /membrane/options`, `POST /membrane/models`, the same through the `/ui/serve/` prefix, all four `/membrane/ha/*` routes | same test | PASS |
| 3c | A token with another sub (`ha`) is 403 on every `/membrane/ha/*` route and is still a verified person (health works); a token minted with `--sub owner` IS the owner; with `operator=ann` the sub `owner` is nobody and `ann` is | `a_token_with_the_sub_ha_cannot_reach_the_home_assistant_routes_and_the_operator_fact_moves_the_owner` | PASS |
| 3d | Tokenless: the public set is `/`, `/index.html`, `/membrane`, `/favicon.ico`, `/ui/www/*`, `/ui/login`, `/ui/logout`; everything else (health, the opencode probes, `/ui/conf`, `/ui/fieldvm`, the sessions, the streams, `/v1/models`) is 401; no climbing out of `ui/www` | `a_tokenless_caller_meets_the_documented_public_set_and_nothing_else` | PASS |
| 3e | What the one-chat page does for a second person (FACTS) | `the_one_chat_has_no_second_person_...` and `release_verify.test.mjs` part 3: see decision 1 | FACTS |
| 4 | Home Assistant on the release launcher: connect with a minted owner token; a call HaAllow permits is pulled, run in (fake) Home Assistant, answered; a denied call is an error at once and never reaches Home Assistant; a non-owner token cannot pull calls; the model select and the metrics | `tests/ha/test_release_launcher.py` (4 tests) | PASS |
| B1 | Bypasses, tokens: alg none (3 forms), NONE, HS512, hs256, RS256, no alg, `crit`, expired 10 s (ok) / 60 s / 1 h, no exp, exp as text or null, no sub, empty sub, non-text sub, control character sub, nbf +10 s (ok) / +60 s / text, iat +60 s / text, `aud`, `iss` (ok), one-character signature change, claims swapped under an old signature, 4th segment, 2 segments, padded and standard-alphabet base64, 9000 characters, secret one character off, secret in upper case: all the same 401 body and `WWW-Authenticate`; the leeway is exactly 30 s | `forged_expired_and_malformed_tokens_...`, `the_token_checks_of_the_door_are_the_old_seats_...` | PASS (after fix 4) |
| B2 | Bypasses, placement: a token in the query string (7 names) is never read; header name in any case; `Bearer` or `bearer`; Basic with the token as the password (Home Assistant); Basic with an empty password, the token as the user name, `Token`, `Digest`, no scheme: 401; this seat's cookie, the old shared cookie name; another seat's cookie name 401; a bad Authorization never falls back to a good cookie; two Authorization headers: the last one decides (the bad one) | `a_token_is_read_only_from_the_authorization_header_or_this_seats_cookie` | PASS |
| B3 | Bypasses, paths and methods: 12 protected routes x 24 spellings (`..`, `//`, `/./`, trailing `/` and `//`, upper case, `%2f..`, `%00`, `;x=1`, `?x=/ui/www/`, `#`, the `/ui/serve` prefix with `//`, `/ui/www/../`, `/ui/www/%2e%2e/`, `/ui/login/../`, `/index.html/../`, `/favicon.ico/../`) x 8 methods (GET HEAD OPTIONS POST PUT DELETE PATCH TRACE): over 2000 requests, never an answer but 401 (or a 404/405 for the public shell's lookalikes); a non-owner is never given `/membrane/ha/*` by any spelling | `path_tricks_methods_and_headers_never_open_...` | PASS (after fix 6) |
| B4 | A request with NO token changes nothing: 22 write attempts (sessions, messages, the chat, abort, the seat controls, quota, HA catalog and claim, models, options, `/mcp` writes, login) all answer 401 AND leave every entity, session title, model and message as it was | `a_request_with_no_token_changes_nothing_whatever_it_answers` | PASS (after fix 1) |
| B5 | A proxied request is not this machine: `X-Forwarded-For`, `Forwarded`, `X-Real-IP` each: the shell is served, no cookie | `a_request_through_a_proxy_is_not_this_machine_whichever_header_says_so` | PASS (after fix 2) |
| B6 | An owner's held connection (the page's event stream) is a live `Request` entity; a person who reads it with `entity_read` must not get the owner's token | `an_open_connection_of_the_owner_does_not_hand_its_credentials_...` | PASS (after fix 3) |
| B7 | Host header (DNS rebinding): a loopback peer naming ANY host is given the owner's cookie | `fact_the_page_is_given_the_owner_cookie_whatever_host_name_the_request_carries` | **GAP** (decision 3) |

## Defects found and fixed (each with its red test first)

1. **A request with no token ran its writes (critical).** Every `on-setter` of a pack runs whatever the reply is, and the door's 401 is only the REPLY. The session, chat and models families never
   checked who asked: a tokenless `POST /session`, `PATCH`/`DELETE /session/<sid>`, `POST /session/<sid>/prompt_async` and `/message`, `POST /membrane/chat/message` and `POST .../abort` answered `401`
   and DID the write: created and renamed and deleted sessions, and **started model turns** (spending the API key, with the owner's token in the turn's environment). `GET /v1/models` made the seat fetch
   its backend, and a tokenless GET of a session's messages could open a legacy import. Fix: the getters that start a write or an effect read `this.entrant` (door.c1f: the verified caller)
   in `door_session.c1f` (`ss_make`, `ss_new_title`, `ss_new_model`, `ss_gone`, `sm_post`, `ab_on`, `ml_ask`), `chat.c1f` (`cm_go`) and `door_models.c1f` (`m_ask`). The seat's other setters were already gated
   (`/mcp` by `owner`, models/options by `owner`, seat control by `authed`, HA by `gate`+`owner`). Red: the test, with the created sessions, messages and `Fetch:tags` listed.
2. **`X-Real-IP` did not make a request off-box** although docs/auth.md says it does: a proxy that sets only `X-Real-IP` (the common nginx recipe) on the same machine gave every visitor the OWNER cookie.
   Fix: `Request.fwd` (door.c1f) reads `x-real-ip` too.
3. **A verified person could read the owner's token** (privilege escalation): the page keeps `GET /event` open, its `Request` entity is alive while it is held and carried the `authorization` and
   `cookie` headers; `entity_read {spec: "Request:<n>", prop: "headers"}` is any verified person's tool. Fix: the door (http.rs) removes `authorization`, `cookie` and `proxy-authorization` from the
   Request's headers after it verified them (no pack reads them).
4. **The verifier was weaker than the old seat's** (jwtauth.py): a header with `crit`, a token with an `aud` (scoped to someone else) and a non-numeric `nbf`/`iat` were accepted. Fix: jwt.rs refuses each.
   (Only a holder of the secret can sign one, so this is parity, not an open door.)
5. **An unreadable secret file was silently replaced**: `ensure_secret` treated every read error as "missing", removed the file and made a new secret: every token, cookie and the Home Assistant entry die
   silently after a permissions slip. Fix (owner.rs): only a missing or empty file is made; any other read error (or a folder) refuses to start, naming the file.
6. **A HEAD to a family route held its connection for ever** (no answer, no close) for a verified caller: the families' replies are written for the methods they list. Fix: door.c1f `method_ok` no longer
   treats a HEAD as a GET: a HEAD to a family route is a 405 with `allow`; the probes and the page's files still answer it.

Docs honest in the same change: docs/auth.md (cliffvm section) says the above; the Python-seat section is unchanged.

## Documented behaviour (not defects)

- **Secret reuse:** a seat's secret is a file of at least 32 bytes of text (trimmed). A second seat started with a COPY of the first's file accepts the first's tokens and cookies and the first accepts
  the second's (the owner token is signed by the same key). Two seats on one host with one secret also share the cookie NAME (cookies are per host, not per port): harmless, the value verifies on both.
  To reuse the Python seat's login, point `door_secret_file` at its `jwt.secret`: it is a text file of 64 url-safe characters and is kept as it is.
- **Two Authorization headers:** the last one wins (the door keeps one value per header name); the old seat refused two. It never mixes them to the better token.
- **A cookie with an Authorization header that is not `Bearer`/`Basic`** (e.g. `Token x`) is ignored and the cookie decides. A bad Bearer never falls back.
- **`--fact door_secret_file=` (empty)** is the open barest seat by name; the page's config says `auth: none`.
- **Home Assistant over the LAN:** `scripts/seat` has no `--bind` (loopback only); a seat for another machine is `cliffvm run ... --bind <addr>` (docs/home-assistant.md).
- **Docs name `scripts/seat-full`** in places (auth.md, home-assistant.md); the script is `scripts/seat`.

## Open decisions

1. **"Everyone with access is the owner" (the one chat), the facts.** Only `/mcp` `entity_write` and `run_cliff` and `/membrane/models`, `/membrane/options` and the `/membrane/ha/*` routes check the owner. Every other route
   takes any verified person: bob sees all sessions and the one chat, renames and DELETES the owner's sessions, can send the seat controls (not refused by who he is; on a seat with no presence entity the route answers 503), and his chat message is the chat's (the stored message carries no `person`). His message starts a TURN whose CLI carries the OWNER's token: a model that obeys the message writes entities as the owner (`the_one_chat_has_no_second_person_...`
   makes a note this way while bob's own write is a 403). So a second person is a reader of the owner's world and a remote control of the owner's model. Options: (a) keep, document as "tokens are for people you
   trust like yourself"; (b) the session and chat routes refuse a non-owner (403 `for the owner`, ~8 getters like fix 1); (c) per-person chat (a design, not a gate).
2. **Sign-in screen (retirement blocker of the second machine).** The page has none: over the LAN it says "The page could not start: the seat's /ui/fieldvm answered 401" and stops. Smallest fix: when the
   boot read answers 401, `boot.js` replaces the text with one `<form>` (a password-type field "Access token" and a button) that `POST /ui/login`s and reloads: the token goes to the door, comes back as the HttpOnly
   cookie, and is never stored by the page (no localStorage). About 25 lines of plain JS in the boot file, no c1f view needed (the engine has not started when this happens), and the 401 text would say "paste a token".
3. **Host allow-list (DNS rebinding).** A loopback peer is "this machine" whatever `Host:` it names. A page on `evil.example` whose name resolves to 127.0.0.1 is given the owner cookie and its fetches carry it.
   Proposal: give the owner cookie only when `Host` is `localhost`, `127.0.0.1` or `[::1]` (port any), plus a launch fact `host_names` for a seat named by DNS. A threshold-free rule, but it changes who is the
   owner on a machine reached by name (`http://mybox.local:4096/` on the machine itself).
4. **Duplicate headers and `aud`/`crit` parity.** Two Authorization headers: last wins (old seat: refused). Keep, or refuse (one line in http.rs)?
5. **32 bytes** is the minimum secret (carried from jwtauth.py, jwt.rs `MIN_SECRET`); a threshold the operator never set on this seat. Keep?
