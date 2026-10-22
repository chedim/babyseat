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
