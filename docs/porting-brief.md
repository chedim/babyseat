# Porting brief (read this first if you are porting a piece to c1f / Rust)

End state (operator, 2026-11-12): Python is the package wrapper and launcher only. Everything that decides is `.c1f`; Rust stays
minimal (sockets, files, token checks, SQLite, vectors). The HTTP door is Rust and a request is an entity a pack answers. No
forwarding to Python. Master doc: `docs/python-to-c1f.md`; tracking issue #1143; state notes in the repo's memory.

## Rules (all of them bit someone)
1. **Test first, always.** A failing test, seen red for the right reason, then the code. A test that passes the first time has not
   been proven to test anything: break the code and watch it fail (mutation check) before you trust it. Pack behavior is a JSON
   spec in `tests/specs/*.json` (`{name, packs, given, writes, expect}`, run by `native/src/spec.rs` through
   `cargo test --test specs`; expectations take a dotted path: `reply.body.name`). Rust drivers get a Rust test in `native/tests/`.
   Python tests only for what is still Python.
2. **No new Python logic.** Python you touch only shrinks. A driver (Rust) performs the bare effect and returns raw data; parsing,
   policy, wording and formatting are getters/setters in a pack. The Python a port replaces is deleted in the same change.
3. **Writes, not calls.** New capability = an entity property or a verb write (`Request.respond`, `Store.run`, `Fetch.request`,
   `Command.run`), never a new builtin or host callback. New Rust only when strictly necessary.
4. **Never invent.** Build what the area needs with existing mechanisms; if a design fork is genuinely new, stop and report it.
5. **Keep the seat bootable; never deploy, never restart a seat, never post to Memo's chat, never touch Memo's entities.**
6. Commits: message ends with `(#issue)` only, no AI attribution lines. Do not edit CHANGELOG.md. Base on your branch's start.

## The route hook (the door, `membrane/packs/door.c1f`)
A route family is its own pack: `PACK door_<area> 1`, `IMPORT door 1`, and two getters on `Request`:
`Request.o_<family>` (True when the path is yours) and `Request.r_<family>` (the reply `{status, headers?, body?|file?}`, or None
while an effect has not landed). Reserved families: `models decisions state voice session self tools map fieldvm`. The door's gate (token or
bare loopback) runs before you. Facts the launcher knows arrive on `Boot:seat` (`seat_version`, `seat_cwd`, ...): read them with
`[b.x for b in instances('Boot')]`. A reply the door sends is data: `body` is any JSON, `file` names a file to send.
Per-request outbound work (a model list, a backend call) is a per-request entity: write `('Fetch:' + this.conn).request = {...}`
and read its `request_result`.

## The contracts (docs/c1f-stdlib.md, enforced by `native/tests/contracts.rs`)
A family pack defines `o_<f>`/`r_<f>` (and `m_<f>` for the declarative 405: prefer it to your own checks); use the shared getters
`facts jbody qpairs person` and register every Boot fact you read in `membrane/packs/facts.json` (`wired: false` if the launcher
does not write it yet). `cargo test --test contracts` must pass. If your pack's *surface* changed (new write, path, import, fact), that is expected:
review the diff it prints, then regenerate `membrane/packs/surface.json` with `SURFACE_UPDATE=1 cargo test --test contracts surface` and commit it.

## Engine rules that bit (c1f)
- An expression has no size cap; use a **local** (`name = expr` line in a getter or setter block, docs/c1f-stdlib.md) to name a part. An evaluation has a step budget (default 10000, `MEMBRANE_EVAL_BUDGET`): over it the value is a null with the reason `error: budget exceeded`.
- A setter sees the getters as they were **before** the round: chain `stamp -> plan -> act`, carry data in the dict.
- A class a shipped pack declares is **owned**: another pack declaring on it must `IMPORT` that pack. Do not declare on `Message`,
  `Chat` or other classes Memo's packs use.
- A property named `this` is read by every getter (a cycle): never name one `this`. Names of getters appear in expressions as
  tokens; strings are ignored except the last argument of a call (`prop(row, 'name')`).
- A class must be declared by a pack to be mirrored into the engine: give it at least one getter.
- `x[i]` works on lists/dicts/entities (miss -> none), no slices; `+` on lists is vector addition: use `concat`; no `int()`:
  `words(replace(str(n), '.', ' '))[0]` truncates; `clock` is epoch seconds.
- Python-hosted tests need a fresh library: `rm -f membrane/membrane_vm.so && sh native/build.sh`; a test calling the door over
  HTTP needs `@pytest.mark.tokenless`; helpers like `_until` that return on any non-None treat `False` as done.
- `cmd | tail` hides the exit code of `cmd`: read the output. Count a Rust run from every `test result:` line (`cargo test` stops at the first failing binary, so a partial count is not a pass).
- A mutation script that restores the original file must `touch` it afterwards: cargo trusts modification times, and a restored copy with an old time keeps running the last mutated build (a false red).

## Done means
Specs and Rust tests green, mutation-checked; the Python it replaces deleted with its tests; `docs/python-to-c1f.md`'s status row
updated; one commit per piece on your branch with a message that says what moved to cliff.
