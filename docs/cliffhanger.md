# Cliffhanger: membrane's command language

Membrane composes commands in **Cliffhanger**, the operator's graph-operation
language. The language itself is specified and documented in its own repo:

> **<https://github.com/chedim/cliffhanger>** — the language, not the seat.

In membrane it takes the form "membrane as an OOP language": entities are
objects, ISA parents are classes, a **method** is a command attached to a class
or an object, and a **call** is an event on an object — dispatched through the
object's own method, then its classes (ISA order), then global methods. The same
name on two classes is two implementations (polymorphism).

- **Define once, call many:** `exec(action='define', on='ha light',
  method='turn_on', command='…')`; then
  `exec(action='call', object='ha:light.port_strip', method='turn_on')`.
- **Binding:** `{{self.<prop>}}` fills from the object (its own values over its
  classes'); other `{{params}}` bind from `args` — only `{{name}}` binds (`{n}`,
  `$n`, `$1` do not), and an arg the command never names is refused.
- **Compose:** a method's `steps` run in order (composite depth ≤ 3), stopping
  on failure; `{{steps.<name>.state.<field>}}` reads a step's captured result.
- **Guard and read:** `guard="result = {'ok': …}"` runs first (false = blocked,
  nothing runs); `read_only=true` marks a read; `abstract=true` declares an
  interface's required method.
- **Inspect without running:** `exec(action='inspect', object=…, method=…,
  args=…)` shows where the call resolves and the exact command it would run.

The model reads this surface as `help('cliffhanger')` (and `help('exec')`). The
repo linked above is where the language is defined; this page is only membrane's
instantiation of it.

## The LLM as a coprocessor (#454, `MEMBRANE_COPROC=1`, off by default)

With the flag on, a model's reply is read as cliffhanger words. The rule is
rigid only where something runs (decide, remember, intend) and free
everywhere else (the prose the person reads).

- **Surfaces** (picked per model, `coproc` on its router record; defaults
  `qwen*` -> word=node, every other model -> python-shaped):
  - python-shaped: `decide(text)`, `remember(key, value)`, `intend(text)`,
    `assistant.says(text)`, plus membrane's tool names;
  - text: `DECIDE: ...`, `REMEMBER: key: value`, `INTEND: ...`, `SAY: ...`;
  - word=node: sentences that start `Decided:` / `I decide`,
    `Remember: X is Y.`, `Remind: ...`. Every other sentence is prose, and
    the reply is the whole text.
- **Strict parsing:** an action counts only when it parses fully. Python-shaped
  lines are read with `ast`, literals only, and are never exec'd. Unknown or
  garbled lines are lint errors that come back next turn and never run. A tool
  name written as text is refused: tools run through the tool channel.
- **Words:** each action is one flat word. `decide` records a decision,
  `remember` records a fact (a newer value for the same key wins, the history
  is kept), and `intend` puts a goal on the intent board. Each word is
  recorded with who, when, the conversation key and the model, in
  `<field>.coproc.jsonl`. Applied facts and decisions also become notes in the
  field, so recall finds them. An identical action repeated in one answer, or
  repeating the previous turn's applied action, is refused as a lint error.
- **Injection guard:** an action is applied only when the person's message
  in this turn carries it (`TRACE_MIN` of its words). An action that comes
  from a tool round, a web page, recalled text or the model's own idea is held
  as a **proposal**. The reply says so, and only the person applies it
  (`POST /membrane/coproc/confirm {id}`).
- **Rendering:** each turn carries the surface, three varied examples (a
  normal turn, a question with no action, an undo), the memory, this chat's
  recent exchanges and the last turn's receipts and lint errors. The person
  sees only the decompiled reply. On the claude-agent rung, membrane's tools
  named like the calls (`intend`) are hidden while the flag is on.
- **Reading and steering:** `GET /membrane/coproc` shows the facts, decisions,
  proposals, per-model maps and constants. `POST /membrane/coproc/map
  {model, surface, examples}` overrides one model's map.
- **Measuring:** `scripts/coproc_bench.py run` scores a labelled set per
  surface (right, missed, extra, unparsed). `scripts/coproc_bench.py recall`
  compares facts captured as actions with the same facts as prose, asked back
  through `/membrane/ask`.

See also: [entity-field.md](entity-field.md) (the executor link lane),
[background-mind.md](background-mind.md) (entity scripts).

## Every write is an event (#576, part A)

Every write on the field is an event `{path, old, new, writer, chain, ts}` (`membrane/writes.py`), delivered to the listeners registered with `writes.on_write`:

- **Paths:** `<eid>` (an entity born or removed), `<eid>.<prop>` (a property, `parents` included), `<eid>.position` (a move), `rel:<subj>|<rel>|<obj>` (a new field relation).
- **Every write is an overwrite:** nothing → something is a birth, something → nothing a removal. The store diffs each upsert against what the entity last held, so an in-place edit still reports its old value.
- **Writer:** `field` by default; `llm` for a model's turn and its tool calls, `script:<eid>` for an entity's own script, `person:<name>`, `self`.
- **Chain:** the causal chain the write belongs to. A source starts it: a person's message, a sensor fire, Blurp. Everything it causes carries it on:
  - queued events (the watch pool stamps them);
  - jobs' threads;
  - the MCP relay (the `X-Membrane-Chain` / `X-Membrane-Writer` headers);
  - loop turns.

  So a reply minutes later still belongs to the chain that asked for it. A wake an LLM sets fires in the chain that set it.

  Every start is its own chain: two messages in one session are two chains.
- **Births and removals:** a birth or removal straight through `est.docs` (`docs[eid] = …`, `pop`, `del`) is a write too.
- **Old values:** an old value larger than 2 KB is reported as `writes.BIG`, a string sentinel (only its hash is kept).
- **Births:** a new entity is one birth event (`<eid>`); its first properties ride the entity, not separate events.
- **Other threads:** threads that are not handed the chain start fresh. These are Blurp's loop, the dreamer, consequences, predict, and the claude-agent runner; each is its own root until part D gives Blurp's chain to self. A deleted property and one set to `None` read the same.
- **Listeners** run inside the writer's call. They must be cheap and **must not write**: a listener that reacts queues the work and writes later. One that raises is counted (`writes.errors`).
- **Not authority:** the writer and chain are provenance only, and nothing may gate on them. Any local caller can send the headers.

## Effects are writes (#576, part E)

An effect happens because something writes a property of a field entity, not because a command string is dispatched. `membrane/adapters.py` registers one write listener that maps these writes to the effects the seat already has:

| Entity | Property | Effect |
|---|---|---|
| the chat thing `chat:<person>` | `say` (the line) | `chat_io.on_edit`: posted in the person's latest chat |
| a toy (an entity with `properties.toybox`) | `poke` (`"roll"`, `"knock 3"`, `"nudge up"`; a tower block's `poke` names its block itself) | `toybox.poke`, by the person when a `person:<name>` wrote it, else `model` |
| `membrane::llm` | `call` (`{about, prompt}`; a string is the prompt) | `llm.request`, its caller the write's writer |
| `membrane::llm` | `wake` (`{about, at, prompt}`) / `unwake` (the wake's id) | `llm.set_wake` / `llm.unwake` |
| `membrane::presence` | `restart` (the reason) | `restarts.do_restart` |

- **Outside the listener:** the listener only queues. The effect runs on the adapters' one worker thread (started at boot), in the chain of the write that caused it, so provenance continues through it.
- **Consumed:** the worker sets the property back to `''` and leaves the effect's receipt in `<prop>_result` (the chat thing keeps its own `said` / `say_refused`), so the next write is the next effect. A write that empties a branch, and a birth that carries one, are no effect.
- **Its own writes:** those writes are the adapter's (writer `adapter:<branch>`: `adapter:chat`, `adapter:toys`, `adapter:llm`, `adapter:seat`) and fire nothing. Otherwise the writer is provenance: it labels a toy's poker and is the llm's caller tag (its caps, read-only for `script:`); nothing else gates on it.
- **The edit route** still posts a chat `say` at once and returns its receipt; the adapter then finds the line consumed, so a line is posted once.
- **The hatches** (`llm::`, `toybox::`, `seat::restart` in `night.run_command_entity`) stay until part B's triggers can write; then they retire.
