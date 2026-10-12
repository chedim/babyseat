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
     (`membrane serve` there): its URL and the pair from its `serve-auth.json`.

Needs: an Ollama server with a chat model and an embedding model, and 1–3 GB of
free RAM for the seat.

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

| Option | What |
|---|---|
| Chat model / Embedding model | from the same Ollama server's list (keep the embedder once the field has grown) |
| Let the seat use Home Assistant | the hidden user + the state push (above) |
| Open on membrane home | the landing page + the folded sidebar |
| Speech server URL | a whisper-server; empty = the browser transcribes |
| Watch loops (1–3) | the seat's background loops; fewer is lighter, more is livelier |

Changing an option restarts the seat.

## Where things are

| Path | What |
|---|---|
| `<config>/membrane/.membrane/` | the seat's data: field, sessions, `settings.json` (its name), `serve-auth.json` |
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
