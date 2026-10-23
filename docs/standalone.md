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

## Reading the field (the sphere)

membrane home draws every entity in one ball, nested like Russian dolls (#362):

- **Direction from the centre** says *what* the entity is like: where it points
  on the three chosen axes (the field's three main directions by default, or
  the axes you pick — a kind, or "toward" an entity). Neighbours in direction
  are alike on those axes.
- **Distance from the centre** says *how well* those three axes describe it:
  the share of the entity's vector (measured from the field's average) that
  the three axes capture. The outer shell is what the axes explain well; the
  core is what they barely see — pick other axes to bring it out. The share is
  spread by rank and its cube root taken, so the ball fills evenly by volume
  instead of everyone crowding near the centre: the inner third of the radius
  holds about 1/27 of the entities, the inner two thirds about 8/27.

The faint rings are the shells (the whole ball, two thirds, one third); the far
side of the ball is dimmer; the heaviest entities on the near side carry
labels, edged in their kind's colour. The gold trail is the field's
**current**, where attention is right now (#360): its recent spots in the ball,
each fading as exp(−age / τ) with the seat's τ (`CURRENT_TAU_S`, 180 s), and the
glowing head is where it is now. When no one has spoken lately, there is no
current and no trail. The head comes with the projection, and the trail grows
from the live `field.current` frames, which reach admins only. A picked entity axis (`e:<id>`) is the
signed projection toward that entity, the same as `e:<id>:toward` (before the
sphere it was the distance to it).

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
| `self_cpu_share` | Blurp's CPU share, `0 < share <= 1` (default `0.5`); set in the ⚙ options window (#343, #344) |

## The decision cockpit (🗂)

The babyseat menu (top left) opens the page's views: *Chat only*, *Graph only*,
**Decisions** (`/?view=decisions`) and *Everything*. Decisions is the project
decision map as a cockpit: the time axis runs top to bottom and the chat's
input bar is *now*. The page's 3D field fills the background behind it all
and takes no clicks there; the cockpit
scrolls over it as one page (up: the future, down: older chat and the past);
long lists load as you scroll, older chat messages in pages. The center column holds the open goals the seat
steers toward, the babyseat, the input bar, and the chat below the bar,
newest message first. On both sides, open questions pile up
above the bar's line (the first to decide nearest it), the cards in flight sit
on the line, and decided cards fall below it (the newest nearest; the oldest
are pushed out of view). The cards form a dependency graph: lines run from past
decisions to the work they led to, and from the open questions to the work
they block. Linked cards share a side, and hovering a card lights its links.

An open card asks for one value, pre-filled with the agent's pick; ✓ decides it
(from another machine, the seat token first). Clicking a card loads a message
about the decision into the center bar, pre-filled and never sent, so the talk
happens in the center chat. Hovering a decided card marks the message where it
was decided, when that chat is open; clicking it opens its chat and scrolls
there. On a phone the cockpit is one column: goals, open questions, in flight,
the bar and the chat, then the past. See [decision-map.md](decision-map.md).

## The options window (⚙)

The ⚙ in membrane home's top bar opens the seat's long-term settings (#344).
The 🔔 and the pause (⏸) stay in the top bar. The window reads `GET /membrane/options` when
it opens and saves each change with `POST /membrane/options` at once; the seat
validates it, and a bad value is refused (400) with the reason, which the
window shows. They live in `settings.json`, so a restart keeps them. Per-device
state (the last open chat tab, the camera) stays in the browser. On a phone the
window fills the screen; × or Escape closes it.

| Option | |
|---|---|
| Blurp's CPU share | a slider, 10–100 % (default 50 %): `self_cpu_share`. The self loop and the record cap read the file through one reader, `options.cpu_share(srv)` (the settings.json beside the seat's `self/`), so a change needs no restart (#343 throttles the cycles to it × Blurp's energy) |
| The field current's fade | read-only, `CURRENT_TAU_S` (180 s today): it is a threshold, and changing it needs the operator's approval |
| Blurp's energy | read-only: its energy as a percentage and whether it is awake or asleep (#345, #363); "not running yet" before Blurp has run |

Writes go through the owner's door like every other write: from another
machine they need the [seat token](auth.md#the-seat-token). Home Assistant's
relay doesn't forward `/membrane/options` yet, so the window works in
standalone mode.

## The pause (⏸ / ▶)

The ⏸ beside 💬 in membrane home's top bar is the seat's pause (#348). Like a
media player's button, it shows what a click does (#382): ⏸ while the seat
runs, ▶ while it is paused. Pressing ⏸ stands the seat's acting lanes
down — the loops, the dreamer, the thinker's ticks, the night pass, the
autonode, the idle judges, speech, Blurp's cycle and the scripts' model calls —
and the LLM's entity and file writes are refused; reads, memory, `intend` and
your chat stay. Pressing ▶ resumes, and what the lanes held runs. It is the
same pause as typing `membrane: pause` / `membrane: resume` in the chat, with
the same audit line. The page reads the seat's real state on load, every 15 s
and after each press. A restart keeps your pause (the seat comes back paused); a
cold start runs, and the button shows ⏸ again. Pressing it from another machine needs the
[seat token](auth.md#the-seat-token); through Home Assistant, an admin.
What exactly stops: [background-mind.md](background-mind.md#the-pause-the-operators-stable-point-2026-10-06).
The button drove auto-chat before #348; auto-chat is gone.

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

## Diagnosing a slow seat

The seat measures itself, without root (`membrane/diag.py`). Both ports answer
`/membrane/diag/timing`, `/membrane/diag/profile`,
`/membrane/diag/claude-agent` and `/membrane/diag/deliver`, from localhost only
(a call from anywhere else gets 403). `claude-agent` counts the claude-agent
rung's turn-cap hits and Continues, and sums its prompt-cache split (uncached
input, cache reads, cache writes, output) ([claude-agent.md](claude-agent.md)).
`deliver` counts `busy_timeouts`: turns whose model didn't answer inside the
rung's budget (`rung_budget_s`, 90 s).

**A busy seat fails the turn visibly.** When the model doesn't answer inside
that budget (a loaded seat, a model swap), the turn gets an error saying the
seat is too busy to answer with its memory right now: HTTP 503 with
`"type": "seat_busy"`, or, on a stream that already started, an SSE error
event before `[DONE]`. The request is never resent without memory. The chat
marks that answer and offers **Retry**, which sends the same message again in
the same chat with the same model, once per press. Nothing retries by itself.
The timed-out model is cooled down as before, so the next turn goes to another
rung, with memory.

**Request timing** is off by default. Turn it on at launch with
`MEMBRANE_TIMING=1`, or on a running seat:

    curl -s -X POST localhost:4096/membrane/diag/timing -d '{"on": true}'
    curl -s localhost:4096/membrane/diag/timing | python3 -m json.tool

Both ports share one switch and one ring, which holds the last 4096 requests.
The summary lists each route as `<port> <METHOD> <path>` (ids fold to `*`), with
its `count` and its `p50`, `p90`, `max` and `total` in milliseconds, heaviest
total first. `recent` lists the last 20 requests with their status and thread.
A request slower than `slow_ms` (default 1000) also prints a `[timing] ...` line
to the seat's log. Set the bar with `MEMBRANE_TIMING_SLOW_MS` or
`-d '{"slow_ms": 500}'`. Clear the ring with `-d '{"reset": true}'` and turn
timing off with `-d '{"on": false}'`. The `/event` streams count only when they
end, so their durations are the length of a page's visit.

**The profiler** samples every thread's Python stack 50 times a second, for up
to 120 s; py-spy needs `sudo`, this does not. Run one profile at a time (a second
one gets 409):

    curl -s -X POST 'localhost:4096/membrane/diag/profile?seconds=60'
    sleep 61; curl -s localhost:4096/membrane/diag/profile | python3 -m json.tool

The status shows `samples`, the per-thread totals (`threads`) and the 30 hottest
stacks (`top`). Page-request threads group under `Thread-* (process_request_thread)`.
A thread that is waiting (on a lock, a queue or a socket) is sampled too, so a
deep `threading.py:wait` tower means waiting, not work. The full result is in
`<data dir>/diag/profile-<time>.collapsed` (its path is in `file`). It uses
flamegraph.pl's collapsed format, so drop it on
[speedscope.app](https://www.speedscope.app) or run
`flamegraph.pl profile-….collapsed > flame.svg`.

## Bugs and ideas

[github.com/chedim/membrane/issues](https://github.com/chedim/membrane/issues). Your
seat knows this too: when it finds a bug in membrane it drafts the issue and gives
you a prefilled link — it never posts anything itself.
