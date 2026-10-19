# Standalone mode — membrane with its own web UI

`membrane standalone` runs the seat with the web UI built in: membrane home (the
field in 3D, following you), the chat on glass over it, and voice. No Home
Assistant, no opencode, no terminal UI — a browser is the client.

## Run it

```bash
pip install membrane-seat              # or: uv tool install membrane-seat
mkdir ~/bob && cd ~/bob                # the seat's data lives in ./.membrane
membrane standalone                    # opens http://localhost:4096/
```

In a container instead: the image `chedim/membrane` runs this same mode (UI on
:4096, proxy on :11500, the data on a `/data` volume); see [docker.md](docker.md).

| Flag | Default | What |
|---|---|---|
| `--backend URL` | `http://localhost:11434` | the Ollama server the seat thinks with |
| `--model M` | the best chat model Ollama lists | the chat model |
| `--embed-model E` | a known embedding model Ollama has | the field's embedder (keep it once the field has grown) |
| `--serve-port N` | 4096 (walks up if taken) | the UI and the seat's API |
| `--listen N` | 11500 (walks up if taken) | the seat's OpenAI-compatible `/v1` |
| `--no-browser` | — | don't open the browser (it only opens on an interactive launch anyway) |

**When a tab opens.** One tab, and only when none is open: on a cold boot the seat
waits ~8 s after its port is up; if a membrane page reconnects in that window (an
open tab's `/event` stream retries every ≤5 s), no new tab opens, otherwise one does.
A restart the seat asked for (`presence.restart`) re-execs with
`MEMBRANE_REEXEC=1` and never opens a tab: the open page reconnects and resyncs on
its own. `--no-browser` or `MEMBRANE_NO_BROWSER=1` turns it off entirely.

Ollama needs one chat model and one embedding model, e.g.
`ollama pull qwen3:8b && ollama pull mxbai-embed-large` (a cloud model Ollama serves
works for chat too).

## The first run

1. The page asks **your** name once (kept in that browser). Every message you send
   carries it, so the seat knows who is talking. (In [JWT mode](auth.md) it asks for
   your token instead, and the token carries your name.)
2. The seat is **Bob** ("Hey Bob") until you agree on a name. It asks for one in its
   first reply; when you've chosen, it adopts it itself (the `name` entity). A name
   must be one word of two or three syllables that the in-page speech recognizer
   knows — otherwise "Hey <name>" could never be heard, and the seat says why and
   asks again. The open page reloads into the new name within ~15 s.

## Who can reach it

- **Your machine and your local network, no login**: loopback, private addresses
  (192.168.x, 10.x, 172.16–31.x) and Tailscale's 100.64.0.0/10.
- **Anything else is refused** (403, never a login prompt): a public address, or a
  request a reverse proxy forwarded (`X-Forwarded-For` / `Forwarded`). That guard
  covers the page; the serve's API itself has no login -- keep the seat on your
  network, or put a reverse proxy with its own login in front.
- `"ui_lan_open": false` in `.membrane/settings.json` closes the LAN door too.
- **JWT mode** (optional, off by default: set `MEMBRANE_JWT_SECRET`) replaces all of
  this with a token. Every request to the page's API and the seat's needs one, from
  the LAN or behind an ingress alike. The page asks for the token instead of a name,
  and the token says who you are. See [auth.md](auth.md).

## Voice

- **Hey <name>** (👂): a wake word heard on the device (Vosk), then your request
  transcribed in the browser (Whisper base.en via transformers.js) — say the
  request in the same breath as the name. 🔒 dictates without the wake word.
- **Spoken replies** (🔊): the seat answers voice messages in a sentence or two,
  spoken by the browser's own voice.
- Browsers allow the microphone only on a **secure origin**: `http://localhost`
  is one, `http://192.168.x.x` is not — from another device the UI works but the
  mic stays off (the page says so).
- The speech models (~65 MB) download the first time a page needs them, from the
  GitHub release matching your version, checked against their SHA-256, and stay
  in `.membrane/assets/`. `MEMBRANE_ASSETS_URL` points at a mirror.
- A whisper-server instead of the browser: `"stt": "http://host:port/inference"`
  in `.membrane/settings.json`.
- The page's voice log (what it heard, how loud, which engine, what failed) is
  `.membrane/page-voice.log`.

## settings.json

`.membrane/settings.json` beside the field. The keys read today:

| Key | What |
|---|---|
| `name`, `aliases` | the seat's agreed name (written by the seat when you agree on one) and what speech hears for it |
| `stt` | `"browser"` (default) or a whisper-server URL |
| `ui_lan_open` | `true` (default): no login on the local network (ignored in [JWT mode](auth.md), where the token decides) |

## Updates

On an interactive launch, a PyPI install checks for a newer `membrane-seat`, shows
where the changes are listed, and asks before upgrading (then restarts into it).
`membrane update` does the same by hand; `MEMBRANE_NO_UPDATE_CHECK=1` turns the
check off. A git checkout updates with `git pull --ff-only` the same way.

## Bugs and ideas

[github.com/chedim/membrane/issues](https://github.com/chedim/membrane/issues). Your
seat knows this too: when it finds a bug in membrane it drafts the issue and gives
you a prefilled link — it never posts anything itself.
