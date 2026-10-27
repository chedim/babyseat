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

See also: [entity-field.md](entity-field.md) (the executor and its queue),
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
| a method entity | `invoke` (`{object, args}`) | `night.method_call`: how a learned call acts (#576 C) |

- **Outside the listener:** the listener only queues. The effect runs on the adapters' one worker thread (started at boot), in the chain of the write that caused it, so provenance continues through it.
- **Consumed:** the worker sets the property back to `''` and leaves the effect's receipt in `<prop>_result` (the chat thing keeps its own `said` / `say_refused`), so the next write is the next effect. A write that empties a branch, and a birth that carries one, are no effect.
- **Its own writes:** those writes are the adapter's (writer `adapter:<branch>`: `adapter:chat`, `adapter:toys`, `adapter:llm`, `adapter:seat`) and fire nothing. Otherwise the writer is provenance: it labels a toy's poker and is the llm's caller tag (its caps, read-only for `script:`); nothing else gates on it.
- **The edit route** still posts a chat `say` at once and returns its receipt; the adapter then finds the line consumed, so a line is posted once.
- **The hatches are retired (#576 G2):** `llm::`, `toybox::`, `seat::restart` and `js::` no longer dispatch from a command line. Their methods are write steps (below).

## Triggers: cliffhanger without hatches (#576, part B)

`membrane/ch.py`. A **trigger** is an entity of class `Trigger`: a reified edge from what it watches to what it writes. One line of it reads:

```
WHEN near(?x, op, 0.2) WHERE isa(?x, toy): ?x.close = 1 + (x.close or 0)
```

- **WHEN: watchers.**
  - **Level watchers** fire once per binding when the conjunction turns true, and re-arm when it turns false: `near` / `away` (1 − cos between positions), `above` / `below` (an expression against a bar), `vel`, `align`, and `quiet` (no write on the entity for dt, by anyone but a trigger).
  - **Pulses:** `pos` (moved further than d since its stamp) and `around` (the neighbours within z changed) are true only on the evaluation that sees the change.
  - **Event watchers** fire on the event while the level watchers hold: `changed(?x.prop)` or `changed(<eid>.prop)` (a write on that path) and `every(dt)`. At most one event watcher per trigger.
  - **Seeding:** the first evaluation of a binding only seeds its state. A trigger fires on a crossing it saw, never on what was already true, so a restart or a redefine re-fires nothing.
- **WHERE: patterns, no XPath.** `isa(?x, Class)` (the entity's class or an ISA ancestor), `is(?x, eid)`, `filter(expr)`.
- **WRITE: `[target, expression]`.** The target is `?x.prop` or `<eid>.prop`, `.position` included.
  - A written value is a scalar, a string, `None` or a list of finite numbers: never an entity, never inf / nan. A position keeps its dimension.
  - A trigger never writes another trigger, code (`command`, `*_script`, `script_*`) or a definition (`owner`, `owner_sig`, `when`, `where`, `write`). Any other property of any entity it may write: a trigger is how the field writes.
- **Expressions:** a small safe language over scalars and first-class vectors.
  - Operators: `+ - * /` (Python arithmetic for scalars, numpy only for vectors; division by zero and non-finite results are refused), comparisons, `and` / `or` / `not`.
  - Reads: `x.prop`, `x.position`.
  - Built-ins: `dot`, `norm`, `distance`, `unit`, `abs`, `min`, `max`.
  - Limits: at most 400 characters and 64 nodes. Each expression is parsed once and cached.
- **Validation:** an entity of class `Trigger` made any other way is validated like a definition before it runs.
- **The VM.**
  - It hears every write (part A) and only queues it.
  - One worker thread drains the queue: the `changed` triggers on the written path, and level triggers re-read for every variable the written entity can bind.
  - The same worker runs the sweeps the thinker requests (`request_sweep`) for the geometric and timed watchers. All evaluation happens on one thread, under one lock.
  - One failing trigger never stops the others (`vm.errors`), and nothing runs while the seat is paused.
  - A firing writes through the store as `trigger:<eid>`, in the chain that caused it. A sweep starts a fresh chain per firing.
- **The loop rule:** a firing whose trigger is already in its chain's `via` is refused, unless `self` came after it.
  - A trigger owned by self appends `self`, so Blurp is the one place a loop may run.
  - Self's triggers run only with a learner attached (part C), so its energy bounds the loop.
  - Self's ownership is **signed** by `define`: an HMAC under a key file beside the store (`.ch-key`, mode 0600, never an entity). A trigger that merely says `owner = self` is not self's and falls under the loop rule.
  - The entity API never edits a trigger, and only this seat's triggers run; an imported one never does.
  - A chain arriving over the relay cannot claim `self`.

## Program the field first (#576, part F)

The operator, 2026-10-26: "no one-shot commands ANYWHERE, everything MUST be done by programming the field first and then using it." The model does something in one of two ways: it **defines a method** on the entity the work belongs to (or its class) and **calls** it, or it **writes a property** whose write is the effect (above).

- **No direct execution surface.** The `entity_link` / `entity_unlink` tools, `exec(action='link'/'unlink')` and the `POST /membrane/entity/link|unlink` routes are gone. A stored command is a script: it runs inside a method (its command or its steps), or as an entity's own script.
- **A call straight at the executor is refused.** `method_call` / `method_each` (and `exec(action='call'/'each')`) refuse, before anything runs, a call whose object is `tool:executor` (or inherits from it), the `remote_executor` interface itself, or an entity implementing `remote_executor` called on the interface's own method (`exec`). The refusal ends with `program the field first: define a method on an entity, then call it`. Other methods of an implementation (e.g. `presence.restart`) are not executor calls and still run. The check sits on the method routes, the model's only door to them (`night.direct_exec_refusal`, `night.direct_each_refusal`).
- **Using the executor inside a method still works.** A method the model defined on another entity whose command runs, or whose steps call the executor (`steps=[{name, call: 'exec', object: 'presence', args: {code}}]`), runs as before: that is programming first.
- **A call is recorded for the learner.** Each `method_call` sets `last_call = {object: <eid>, args: <the call's args in full>}` on the method entity in the same upsert that bumps its `runs`, so the write event (`<method eid>.last_call`) carries the call.
- **The library lists methods.** The `commands` section of WHERE WE ARE (`your methods: …`) lists the seat's methods, most called first, with how to call them.

- **A call is recorded for the learner:** `<method>.last_call = {object, args}`.
  - A value whose key looks secret (token, key, password, secret, auth, cookie, credential) is stored as `<redacted>`.
  - A long string is cut with a `…<truncated>` marker, and the whole record is held to 64 KB (`writes.ARG_MAX`, the operator's cap).
  - A secret inside a string value (a header in a command, say) is not caught by key names.
- **Accountability, not capability (the operator's call):** everything goes through a stored, named method; what a method may run stays open.

  - Only `define` sets an owner, and a chain arriving over the relay cannot claim `self`.

## Learning in the VM (#576, part C)

`membrane/ch_learn.py`: Blurp's plasticity, as field physics. Attach it with `vm.learner = Learner(vm, purse, granted)`.

- **The target: the LLM's actions.** Only writes by the LLM (writer `llm`) are guessed at: its property edits, and its method calls, which record `<method>.last_call = {object, args}` (part F). The causes can be anyone's writes. Writes by triggers and adapters never teach or confirm, so Blurp's own acts can't confirm themselves.
- **A call is learned as a call.** The guess writes `<method>.invoke = {object, args}`, and the `methods` adapter runs it. The adapter refuses the executor itself, exactly as the model's route does. The object and arguments are copied from the cause where they match, literal otherwise. In shadow, it predicts the `last_call` the LLM's own call will record.
- **Guess.** An LLM write is explained by one of the last 3 writes before it in the same chain. The guess is a trigger owned by self:
  - `WHEN changed(?x.c) WHERE isa(?x, <class>): ?x.w = <value>` when cause and write are on the same entity;
  - with `is(?y, <eid>)` as the target when they're on different entities.

  The value is a copy of the cause when the values match, else the literal (scalars only). A guess already defined is not defined again.
- **Shadow.** A candidate's firing writes its prediction onto itself (`predicted` = {path, value, chain}), never onto the real path.
- **Score.** A later write in the same chain to the predicted path, with the predicted value, by anyone but the candidate, is a hit. Nothing else is refunded, so no timer is needed. A live firing is confirmed the same way.
- **Energy.** Every Blurp write costs `WRITE_COST` (= `BASAL_PER_S`, one awake second): defining a candidate, and each shadow or live firing. A hit refunds 2× to Blurp's purse and to the candidate's balance. The definition counts against the candidate, so it needs two hits to become a proposal: it has to earn back its own birth. Asleep (`purse.can_spend()` false), nothing is spent, guessed or fired.
- **Stay positive.** A candidate with a balance above 0 is a proposal. On a granted branch (`granted(path)`, the skill tree's Accept) a proposal is suggested to the LLM, never acted (part H). Below 0 it is back in shadow. Sleep prunes every candidate whose balance is negative (`prune()`).
- **Bounds:**
  - at most 3 new guesses per chain and 256 live candidates; at the cap, a new guess replaces the most negative one (operator);
  - a refused guess is remembered and never charged or retried;
  - a nested value a trigger writes is held to 64 KB;
  - the most recent 512 chains are remembered.

## Blurp anew: the learner on Blurp's body (#576, part D)

`membrane/ch_blurp.py`. The old learner (the cycle, the rung ladder and the `self::` hatch) is retired (#580). Blurp's body stays: its identity, the on/off switch, energy, sleep and the record. The VM's learner (part C) is attached to it.

- **The purse.** The learner charges each Blurp write and refunds each hit into the seat's purse.
  - Blurp's loop (its own process, or the seat's thread) exchanges with the purse on **every** step, through the context call `context(only="learn", report={awake, asleep, mode})`. It reports whether it is awake, takes the net energy, and applies it (`energy.apply_delta`).
  - Until Blurp has said it is awake, nothing is spent, so nothing is guessed or fired.
  - When it reports falling asleep, the learner prunes every negative candidate.
- **Branches and grants.**
  - A skill is a **branch**: the kind + type a candidate's target belongs to (`Component:toy`, `Component:chat`, `ha:light`, `ha:lock`…; the operator's grain, so lights are not locks). For a learned call, it's the called object's branch. Each candidate records its branch.
  - Grants are saved first and live after; one Accept at a time. Revoking a grant comes later.
  - Blurp's process going (stopped or crashed) closes the purse, and so does a step that ends in sleep.
  - The retired learner's own methods (`self.try` / `expect` / …) are removed at boot.
  - A branch is **open** while Blurp guesses there, **earned** when a guess there pays for itself, and **granted** by the operator's Accept of `branch:<kind>` (`seat_panel.control`; the seat keeps the grants in `ch-grants.json` beside the store).
  - **A proposal never acts by itself (the operator, #576 H).** On an accepted branch, a proposal firing during an LLM turn becomes a **suggestion** in that conversation's next banner, under BLURP SUGGESTS (each shown once, the newest 3).
    - The LLM adopting it (making that write or call) is a hit, and Blurp is refunded, also in a **later** turn of the same chat: a suggestion is shown in the next banner, which may be the next turn's. Each adoption pays once. Ignoring it drains the guess.
    - A long or multi-line string value is shown as a one-line preview followed by `-> <candidate>.predicted.value`, where the LLM reads the whole value if it adopts it. The banner is small, and this is the operator's form.
    - Suggestions rank above the rest of the banner, so the budget never drops one unseen.
    - Unaccepted branches and shadow guesses stay silent.
    - `TurnRunner.run` records which conversation each turn's chain belongs to (`ch_blurp.note_turn`).
- **The sheet.** The seat adds the branches as the skill tree, the **level** as the number of branches granted (the card too), and the **Code tab** as Blurp's triggers: their line, state (shadow / proposal), balance (in write costs), hits and firings.

## Scripts are graphs (#576, part G)

`membrane/scripts.py`. A stored command is a **graph**, compiled to bash only to run.

- **The graph:**
  - items joined by `&&`, `||`, `;`, `&` or a newline;
  - pipelines of stages joined by `|`, optionally negated with `!`;
  - a stage is env prefixes, words and redirects (`2>&1`, `>`, `>>`, `<`, heredocs with their bodies), or a group `( … )`.
- **Word kinds:** `lit`, `param` (`{{x}}`), `glob`, `var` (`$X`), `subst` (`$( )` or backticks), and `file`.
  - Every node keeps its **raw shell text**, so compiling a parsed command means exactly what the original meant.
  - `bind()` links a stage's program to an existing `shell:<prog>` entity and a file word to an existing `f:` / `files:` entity. No entity is ever made.
- **Defining:** `method_define` refuses a command or command step that isn't a script. The graph is kept on the entity (`script`, with the `src` it was parsed from).
- **Running:** every command (method, step, or an entity's own `run`) runs as the bash its graph compiles to. An edited command is parsed again; one that doesn't parse never runs (`blocked: not a script`).
- **Migration (the operator's call): once per store,** marked by `.ch-commands-migrated` beside it. Only the `shell:` namespace is touched:
  - a generated variant (`shell:<prog>-<label>-<4 hex>`) is deleted;
  - every other `shell:` entity with a stale command (a program like `shell:git-lfs`, a hand-named command) keeps its place without it;
  - methods, their steps and every other entity are left alone.

  A backup (`backup-576-commands-<ts>.json`) is written first; with no backup, nothing changes. **To restore,** put each `deleted` entry back as an entity with that command, and each `stripped` command back on its entity.

## Write steps: the hatches retire (#576, part G2)

A method step may be a **write**: `{name, write: [target, expr]}`.
- **The target** is `self.<prop>` or `<eid>.<prop>`, never code or a definition.
- **The expression** is cliffhanger's safe language over the call's args.
- **What the step does:** it writes the property, an adapter branch runs the effect, and the step returns the adapter's receipt (`<prop>_result`) and its return code (`<prop>_rc`). A failed step's words become the method's.
- **Inline:** on the adapters' own worker (a learned `invoke`), or with no worker running, the effect runs inline, so nothing ever waits on itself.

The seeded methods are now write steps, and each boot puts an older seat's form back:

| method | write step |
|---|---|
| `llm.call` / `wake` / `unwake` | `self.call = {about, prompt}`, `self.wake = {about, at, prompt}`, `self.unwake = id` |
| `toybox.list` / `add` / `remove` / `reset` / `poke` | `self.list`, `self.add = spec`, `self.remove = name`, `self.reset = name`, `self.poke = {toy, action}` |
| `presence.restart` | `self.restart = why` |
| `presence.exec` | `self.js = code` (the adapters' new `js` branch → an opted-in browser tab) |

`night.run_command_entity` now runs shell lines only:
- **A stored line in a retired dialect** (`llm::`, `llm.wake`, `toybox::`, `seat::`, `js::`, `self::`) is **refused** with rc 2 and a pointer to the method. It is never split into a label plus a shell command.
- **Write steps on one property are serialised,** so each call gets its own receipt. An empty value is refused at once.
- **On a timeout,** the receipt says the effect may still be queued or running: don't repeat it.

## Blurp on the field map (#576)

Blurp's triggers show on the 3D field: on the home page and in the HA panel, with a "blurp" toggle on the panel.
- **The VM keeps its recent firings:** 200, each with trigger, from, to and time.
- **`GET /membrane/map/blurp`** returns Blurp's own firings in field ids, each with its trigger's state now: **shadow**, **proposal**, or **suggest** (a proposal on an accepted branch). It also returns the counts over its triggers.
- **The view polls it every 3 s while visible** (`blurpLayers`, pure):
  - a firing between two entities is a line, faint for shadow, gold for a proposal, bright gold for a suggestion;
  - a firing on one entity (most learned guesses: `?x.c` → `?x.w`) is a halo on it;
  - each firing it hadn't seen yet pulses along its line or on its entity.
