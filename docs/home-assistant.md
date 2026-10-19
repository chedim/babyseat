# Membrane in Home Assistant

The **Membrane** integration (HACS) installs the membrane package into Home
Assistant, runs the seat as Home Assistant's own child process, and makes
membrane home the face of Home Assistant — the way it runs on the boat it was
built on.

## Install

1. HACS → ⋮ → *Custom repositories* → `https://github.com/chedim/membrane`,
   category *Integration*. Install **Membrane** and restart Home Assistant.
2. *Settings → Devices & services → Add integration → Membrane*:
   - **Inside Home Assistant** — Home Assistant runs the seat. If it already has the
     Ollama integration, that server is used (its agent's model offered first);
     otherwise give Ollama's address. Pick a chat model and an embedding model.
   - **Connect to a seat running elsewhere** — a seat on another machine
     (`membrane serve` there): its URL (a user and password only if a reverse proxy
     in front of it asks for them).

Needs: an Ollama server with a chat model and an embedding model, and 1–3 GB of
free RAM for the seat.

## Connect mode = your own detached seat

"Connect to a seat running elsewhere" is the mode when **you** own the seat:
you run it on a machine you like (a Mac, a Linux box, even unrooted Android via
Termux) with `membrane serve`, or in a container from the public image
(`chedim/membrane`, [docker.md](docker.md); its :4096 is the URL to give), the
integration only introduces itself over the network. Nothing is spawned inside Home Assistant, and **a restart of Home
Assistant never restarts the seat** — its field and sessions keep running.

What the integration still does for such a seat:

- **Panels, chat and Assist** ride the seat through a proxy: your Home Assistant
  login is the only credential the browser needs; the seat itself has no login
  (a proxy's, if you put one in front, stays server-side). Sessions created from Home Assistant
  are owned by the HA user who made them.
- **Let the seat use Home Assistant** (on by default) works in this mode too, in
  two halves:
  - the seat's HA access is **delivered instead of inherited**: at every Home
    Assistant start the integration mints a fresh token for its hidden user and
    POSTs it to the seat; the seat files it in `~/.config/membrane/hass.env`
    (0600) and reads it on its next turn — so an HA restart reaches the seat
    without a seat restart. Set **HA's address as the seat sees it** in the
    options when the seat is on another machine (Home Assistant's own
    `internal_url` answer otherwise; empty = that default).
  - **State push** is the mirror of the inside mode: entities the seat marks
    `ha_entity` are pushed to it (in batches) on every change, watches fire
    without polling.
- **Restarts are announced to a detached seat**: the integration posts
  `ha-start` when Home Assistant is up and `ha-stop` on the way down
  (`restarts.py` notices); the seat never reads an HA restart as the world
  going quiet.

Keeping the seat current is the seat's owner's job — on a machine where the
code is a git clone of this repo, that's `git pull` (or `membrane update`)
plus a seat restart the seat itself performs; the integration is independent
of the seat's version.

## The zoo: several seats

One Home Assistant can know several seats — the one it runs itself, a laptop, a
phone — each with its own Blurp. The house talks to **one at a time** (the
active seat): the chat and membrane home, the field sensors, the Assist agent and
speech all follow it.

- **Add a seat:** Settings → Devices & services → Membrane → *Configure* →
  **Add a seat**: its host (e.g. `192.168.1.107`), the serve port (4096), the
  proxy port (11500), its seat token (`membrane token seat` on that machine) and
  a name (empty: its Blurp's own name, else the host). *Edit a seat* changes one
  (Home Assistant's own seat takes a name only); *Remove a seat* drops one (never
  the last one, never Home Assistant's own). An existing setup is a one-seat list
  that works exactly as before.
- **Switch:** the 🦠 button in membrane home's top bar opens the zoo — a card per
  seat with its Blurp's name, Lv, rung, awake/asleep, a dot for reachable, its last
  activity; tap a card to make that seat the house's (an admin's switch; the page
  reloads onto it). Automations use the **`select.membrane_seat`** entity (one
  option per seat). A switch takes effect at once — no restart, the seats keep
  running.
- **An unreachable seat** shows in the chat as "<name> is offline — switch to
  <another seat>?" with a button, when another seat answers.
- What the house gives a seat stays per mode: Home Assistant's own seat always
  gets the state push; in connect mode the delivered token and the state push go
  to the active seat (and to a seat as it becomes active).

Behind it: `GET /api/membrane/zoo` (every seat's card, read in parallel, a few
seconds each — `/membrane/self/card`, or `/membrane/self/sheet` on a seat
without it) and `POST /api/membrane/zoo/active` `{id}` (admins only).

## What it changes

- **Membrane home is the landing page** for everyone: opening Home Assistant goes to
  `/membrane` (the field in 3D, the chat, voice). Home Assistant's sidebar folds away
  there; the ☰ menu lists Home Assistant's own pages, and leaving through it keeps
  you in Home Assistant until you open Membrane again. Turn the landing off in the
  integration's options.
- **Panels**: *Membrane* (everyone), *Membrane chat* and *Membrane field* (admins).
- **Assist**: the seat is a conversation agent (`conversation.membrane_seat_<name>`) —
  pick it in a voice assistant.
- **Sensors**: the field's readings (entities, events, the thinker, loops, paused).

Each person's chats are their own; every message carries `[HA user: <name>]`.
(A seat running the optional [JWT mode](auth.md) accepts a token as the serve
password, but then attributes every relayed message to that token's one person.)

## What the seat may do in your house

On by default (*Let the seat use Home Assistant* in the options):

- **Act**: the integration creates a hidden system user, *Membrane* — not an admin,
  local only, not in your people list — and gives the seat a token for it at every
  start (`HASS_URL`, `HASS_TOKEN` in its environment). The seat reads states and
  calls services through Home Assistant's REST API, building its own readers and
  scripts. It **can't** change Home Assistant's configuration, integrations or
  users (those need an admin).
- **Sense**: the seat marks a field entity with `ha_entity: <entity_id>`; Home
  Assistant then pushes that entity's changes into it (`ha_state`,
  `ha_attributes`), so the seat's watches fire without polling. The seat chooses
  what to mirror.
- Its commands are written by a model: it acts on what people ask, and its prompt
  says so — but if you'd rather it only talks, switch the option off. Removing the
  integration removes the user.

The seat never gets Home Assistant's own Supervisor token: its environment is
chosen, not inherited.

## Options

*Configure* opens a menu: **Settings** (below), **Add a seat**, **Edit a seat**,
**Remove a seat** ([the zoo](#the-zoo-several-seats); a seat's token lives on it).

| Option | What |
|---|---|
| Chat model / Embedding model | from the same Ollama server's list (keep the embedder once the field has grown) — inside mode only |
| Let the seat use Home Assistant | the hidden user + the state push (above); connect mode also gets the delivered token (`hass.env`) |
| HA's address as the seat sees it | connect mode: what URL the seat reaches Home Assistant at (empty: HA's own `internal_url`) |
| Open on membrane home | the landing page + the folded sidebar |
| Speech server URL | a whisper-server; empty = the browser transcribes |
| Watch loops (1–3) | the seat's background loops; fewer is lighter, more is livelier (inside mode only) |

Changing an option or the seat list restarts the seat; switching the active seat
does not.

## Where things are

| Path | What |
|---|---|
| `<config>/membrane/.membrane/` | the seat's data: field, sessions, `settings.json` (its name) |
| `<config>/membrane/seat.log` | the seat's output (rotated at 5 MB) |
| `<config>/membrane/assets/` | the speech models, fetched on first use and checksum-verified |
| `<config>/membrane/home/` | the seat's HOME |

The seat listens on loopback only (ports 18096 / 18500, or the next free ones):
Home Assistant's own login is the only way in.

## The seat's life

- Started once Home Assistant is up; stopped with it (TERM, then KILL after 30 s).
- Restarted if it exits: at once after a run of 5 minutes or more (its own restart
  for new code), with a backoff (5 s, 30 s, 2 min) if it dies at boot; restarted if
  it stops answering for two minutes.
- Updates come through HACS: a new integration version pins a new `membrane-seat`,
  which Home Assistant installs.

## Voice

As in [standalone mode](standalone.md#voice): "Hey <name>" on the device, the
request transcribed in the browser (or by the speech server set in the options),
replies spoken by the browser's own voice (Home Assistant's TTS engine only when the
browser has none).
Home Assistant over plain `http://` from another device has no microphone (not a
secure origin).

## Troubleshooting

- **"The seat is starting"** in the chat: the first boot takes up to a minute or two;
  `<config>/membrane/seat.log` shows it.
- **No answer**: the Ollama server must answer from Home Assistant's machine
  (`curl <ollama>/api/tags`), and the chosen models must exist there.
- **Bugs and ideas**: [github.com/chedim/membrane/issues](https://github.com/chedim/membrane/issues).
