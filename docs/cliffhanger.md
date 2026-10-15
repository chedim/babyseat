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

See also: [entity-field.md](entity-field.md) (the executor link lane),
[background-mind.md](background-mind.md) (entity scripts).
