# babyseat in Home Assistant

*Slice 1 of the move to cliffvm (#1143, plan: [hass-plan.md](hass-plan.md)), component version 0.7.60. This page describes the component as it ships now.*

The **babyseat (membrane)** integration (HACS; the domain is `membrane`) is a **thin client of a seat you already run**. Today it gives Home Assistant one thing: the seat as an **Assist conversation agent**. It connects to the seat's address with a token. It does **not** start a seat inside Home Assistant and it installs no package (its manifest has `requirements: []`).

## What works, and what does not yet

| | now |
|---|---|
| Assist: the seat answers Home Assistant's conversations (one seat session per HA conversation, the speaker's name sent as a first line `[HA user: <name>]`) | works |
| Setup form: seat URL + token; a health check at setup | works |
| A token that ran out: Home Assistant asks for a new one (reauth) | works |
| The seat as Home Assistant's child process ("Inside Home Assistant") | gone: run the seat yourself (below); existing "inside" entries ask for a seat address and token once |
| The seat acting on Home Assistant (states, calling services, MQTT) | later (slice 2) |
| Sensors (`sensor.membrane_*`, `binary_sensor`), the select of several seats | later; the old entities show as unavailable: delete them in *Settings -> Entities* |
| The sidebar panels, membrane home as landing page, the chat and field map inside HA, speech input | later (slice 3, after the page rebuild) |

## Install

1. HACS -> the three dots -> *Custom repositories* -> `https://github.com/chedim/membrane`, category *Integration*. Install **babyseat (membrane)** and restart Home Assistant.
2. Run a seat (next section) and mint a token for Home Assistant.
3. *Settings -> Devices & services -> Add integration -> babyseat (membrane)*: the seat's URL and the token.
4. *Settings -> Voice assistants*: pick the **Membrane seat** conversation agent for an assistant.

## Run a seat

The seat is `cliffvm` (docs/cliffvm.md): `scripts/seat-full` starts the whole set of packs from a checkout (`scripts/seat-full --data-dir <dir> --port 4096`, once `cd native && cargo build --release --no-default-features --features bin` has built the binary). The turn is answered through the `claude` CLI with an Anthropic API key in the seat's environment (`ANTHROPIC_API_KEY`; docs/auth.md, "The claude turn's credential").

**The seat listens on 127.0.0.1 by default.** Home Assistant on the same machine reaches it as it is. A seat on another machine must be started with `cliffvm run ... --bind <address>` (for example `--bind 0.0.0.0` on a network you trust: every route needs a token, but the port is then open to the network); `scripts/seat-full` has no `--bind` option yet.

## Mint a token, paste it

Every route of a seat with a door secret needs a token, Home Assistant's calls too (docs/auth.md). On the seat's machine:

```
cliffvm token mint --sub ha --name "Home Assistant" --ttl 30d --secret-file <seat data dir>/door.secret
```

It prints the token (the life, `--ttl`, is required and yours to choose; `12h`, `30d` or seconds). Paste it in the form's *Token* field: the component sends it as the Basic password (`Authorization: Basic opencode:<token>`), which the seat reads as the token. The first request makes the person `ha` on the seat; that person's messages are the speaker lines Assist sends.

## When the token runs out (reauth)

Home Assistant polls the seat's `GET /global/health` every 60 seconds with the token. When the seat answers `401` (the token ran out, or the seat's secret changed), Home Assistant shows the integration as needing attention (*Settings -> Devices & services -> Membrane -> Reconfigure*): mint a new token as above and paste it. A turn that meets the `401` first tells the person in Assist, in words, to do the same. A seat that is down is a retry, not a reauth.

An entry made by the old component: a connect entry keeps its address, and its old seat token is asked for again if the seat is a cliffvm seat (the old tokens are not JWTs); an entry that ran the seat inside Home Assistant has no seat to connect to and asks for an address and a token.

## Errors in Assist

A failed turn is spoken as an error with the seat's own cause, for example `The seat could not answer: claude failed (rc 3): ...`; an unreachable seat says so; a refused token starts the reauth.

## Where things are

The entry is `{serve_url, serve_user, serve_pass}`; `serve_pass` holds the token. The component has no files of its own on disk and starts no process. The old component's seat directory `<config>/membrane/` (data of a seat it started) is left alone; the old field in it is the legacy import's input when you start a new seat (docs/migration-design.md).

## Tests

`pytest tests/ha` runs the component against a fake Home Assistant (tests/ha/fakeha.py) and, where a `cliffvm` binary is built (`cd native && cargo build --features bin`), against a real seat with a fake `claude`. `native/tests/session_1143.rs` pins the routes the component calls.
