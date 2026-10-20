# Standalone mode — babyseat with its own web UI

`babyseat` (the same as `membrane standalone`; babyseat was called membrane
before 0.7) runs the seat with the web UI built in: membrane home (the
field in 3D, following you), the chat on glass over it, and voice. No Home
Assistant, no opencode, no terminal UI — a browser is the client.

## Run it

```bash
pip install babyseat                   # or: uv tool install babyseat
mkdir ~/bob && cd ~/bob                # the seat's data lives in ./.membrane
babyseat                               # = membrane standalone; opens http://localhost:4096/
```

In a container instead: the image `chedim/babyseat` runs this same mode (UI on
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

**New page code after a restart.** `/ui/conf` carries `build`, a hash of the files
under `membrane/ui/` taken at boot. On every reconnect the open tab compares it with
the build it loaded and, if the seat came back with new code, reloads itself once
(the open chat, the 👶 panel and the camera mode are remembered in the browser). With
unsent text in the chat box it shows a "new version · click to reload" chip instead,
and it never reloads twice for the same build within a minute.

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

- **Your machine: no login.** On `localhost` the page reads and writes freely.
- **Your local network reads freely and writes with the seat token**: private
  addresses (192.168.x, 10.x, 172.16–31.x) and Tailscale's 100.64.0.0/10. A page
  opened from another machine (your phone) asks once for the
  [seat token](auth.md#the-seat-token): run `membrane token seat` on the seat's
  machine and paste what it prints. The page keeps it in an HttpOnly cookie,
  then asks your name as usual. The same goes for the serve's API: from another
  machine, everything that changes something needs the token.
- **Anything else is refused** (403, never a login prompt): a public address, or a
  request a reverse proxy forwarded (`X-Forwarded-For` / `Forwarded`). For a seat
  behind a proxy, use JWT mode.
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
| `ui_lan_open` | `true` (default): the page reads without a login on the local network; writes from another machine need the [seat token](auth.md#the-seat-token) either way (ignored in [JWT mode](auth.md), where the token decides) |

## Updates

On an interactive launch, a PyPI install checks for a newer `babyseat`, shows
where the changes are listed, and asks before upgrading (then restarts into it).
`membrane update` does the same by hand; `MEMBRANE_NO_UPDATE_CHECK=1` turns the
check off. A git checkout updates with `git pull --ff-only` the same way.
An install of the old `membrane-seat` (0.6 and before) moves over by itself: its
updater finds the last `membrane-seat` release, which only installs `babyseat`
(or by hand: `pip install -U membrane-seat`, or `pip install babyseat`). The
`membrane` command, the `.membrane` data and the `MEMBRANE_*` variables stay.
That last `membrane-seat` carries the same module as babyseat (pip installs
babyseat first, then removes the old membrane-seat's files, so an empty package
would take the module with it). Both stay installed; to drop the old name later:
`pip uninstall membrane-seat && pip install --force-reinstall babyseat`.

## Bugs and ideas

[github.com/chedim/membrane/issues](https://github.com/chedim/membrane/issues). Your
seat knows this too: when it finds a bug in membrane it drafts the issue and gives
you a prefilled link — it never posts anything itself.
