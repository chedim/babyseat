# Playing membrane as an exploration game

> **RETIRED — kept as history.** The toy world, its proxy, TUI and world
> viewer were cut (`070af25` / `b01b111`: membrane is the opencode seat
> only). Nothing below runs any more; the manual stays as the record of
> what the field did when worn as a world.

> **For the gamer, not the engineer.** This is a player's manual. The build/run
> details live in the [`readme`](../README.md); you don't need them here. Assume
> a world is already running and the viewer is open in your browser
> (`http://127.0.0.1:8770/viewer`).

membrane is a **self-writing world you step into as a character.** It is not a
chatbot and not a scripted game. The world grows its own memory of scenes on its
own clock; you inhabit it as a voiced protagonist; your words are that
character's *thoughts*, and the world narrates back from the scenes your
character has lived. You explore by thinking, and the world writes the next scene
from where you stand.

---

## The screen

- **Left: the chat.** This is where you live. Your messages appear as your
  character's thoughts (a ❝ marker + their name). The world's reply is narrator
  prose — no attribution, just the next beat of the story.
- **Top right: the 3D map.** Every star is a scene the world remembers. Stars are
  **colored by character** — the scenes that touched a character carry that
  character's color; untagged scenes are green (safe) or red (fallen/demoted).
  The camera **stays centered on your character**, so as the story moves, the
  map moves with you. Drag to orbit, scroll to zoom.
- **Mid right: the situation panel.** A snapshot of **where your character is
  right now** — the most recent scene they lived. It updates every turn. Read it
  before you type: it's your "what just happened."
- **Bottom right: the controls.** Play/pause, speed, and the starve/balance/feed
  dial (below).
- **Top bar: stats.** World level, free energy, the cast count, the in-world day
  and time. These are flavor; you can ignore them.

---

## The three world-drive modes (in SETTINGS)

This is the single most important setting. It decides **how the world writes
itself while you explore.** Open the menu (☰), go to SETTINGS, and pick
**WORLD DRIVE**:

| mode | how the world writes | play feel |
|---|---|---|
| **reactive** *(default)* | the world writes **one scene after each of your moves** — a beat that follows directly from what you just did/said | the world is a co-author that responds to you. Best for **directed exploration**: you steer, the world answers. |
| **pov-follow** | the world writes **on its own clock**, a chronicle that follows your protagonist around | the world lives and breathes without you; you drop in and out. Best for **watching a life unfold**, then steering when you want. |
| **explore** | the world **never writes on its own** — it grows **only from your chat** | pure sandbox; nothing happens unless you make it happen. Best for **slow, deliberate worldbuilding**. |

Start in **reactive.** It's the mode that feels most like a game: you act, the
world reacts, one scene at a time.

> In reactive and explore, the **AUTO-FLOOD** toggle is disabled — those modes
> don't use the autonomous chronicle, so the toggle has nothing to do. It lights
> up only in pov-follow.

---

## How to play

1. **Read the situation panel.** That's where your character is.
2. **Type a thought** in the chat box and hit Enter. Phrase it as your
   character's inner monologue or speech: *"I pocket the credit card and back
   toward the portal"* — not a command to an AI. You *are* the character.
3. **The world replies** with the next scene (in reactive) or the chat recalls a
   lived scene (the narrator speaks from memory). Either way, the story advances
   and the map gains a star.
4. **Repeat.** Each thought moves your character's state; the field follows you,
   so future narration accrues where you steer.

Your thoughts are **the ground** — the world doesn't check them, it *follows*
them. So steer deliberately: where you put your attention is where the world
goes.

---

## The controls (bottom right)

- **▶ / ⏸ play-pause** — arms or pauses the world's own clock. In reactive this
  mostly controls background digestion; the reactive beats fire on your chat
  either way. In pov-follow this is how you start/stop the autonomous chronicle.
- **1x · 2x · 4x** — the world-clock speed. 2x and 4x make the in-world day/night
  clock and the chronicle advance faster. (You can ignore this in reactive.)
- **starve · balance · feed** — how greedily the world *absorbs new scenes*:
  - **starve** — no new scenes absorb (the world digests what it has; it gets
    hungrier/sharper but doesn't grow).
  - **balance** — a slow, steady diet (default feel).
  - **feed** — fast growth; the field fattens quickly.

  In reactive, feeding still happens one beat per move — these presets tune how
  greedily each beat is absorbed. In pov-follow they set the autonomous feed
  rate. In explore they do nothing (the world only grows from you).

---

## The cast (menu → CAST)

The world has a small ensemble of named characters (max 12). Each is a colored
walking-region of the map. For each character you can:

- **POV** — switch who *you* are. The camera and the narrator follow the new
  protagonist; your thoughts become theirs.
- **PIN** — keep a character from being pruned (a permanent fixture).
- **RETIRE** — write a character out (their scenes lose their color).
- **click a row** — zoom the map to that character.

The cast is seeded once at the world's first chapter and then walks as scenes
tag them. New characters don't auto-appear; the world stays short and legible.

---

## Worlds (menu → WORLD)

- **NEW** — start a blank world (the cast is re-seeded, the map resets).
- **SAVE / LOAD** — keep multiple worlds; each is its own field + cast.
- **✕ delete** — remove a saved world (you can't delete the one currently live).

Your current world's mode, story model, and world-drive setting persist across
restarts.

---

## Tips for a good session

- **It needs seeding.** A fresh world starts nearly empty. Before you expect rich
  replies, let it run a bit: in pov-follow hit PLAY and watch a chapter land; in
  reactive just start typing — the first beat seeds the field.
- **Write in character.** First person, present-ish, concrete actions and
  observations. The world narrates *around* you; give it something to narrate.
- **Use the situation panel as your compass.** If the chat feels chaotic, you may
  have drifted from your character — check the panel, maybe switch POV, and
  re-anchor.
- **Slow down to steer.** In reactive the world only writes when you do, so the
  pace is yours. Pause the clock, think, type one move, read the beat.
- **Switch modes mid-session.** Start reactive to get rolling, flip to pov-follow
  to watch the world breathe on its own, flip to explore to worldbuild by hand.
  The setting is live in SETTINGS.

Have fun. The world is yours to walk.