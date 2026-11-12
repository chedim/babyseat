# The c1f standard library (design, 2026-11-12)

c1f has no functions and no macros. A pack is getters and setters over entities, so a "library" can only be **data contracts and
shared getters on a class** that every pack imports. The standard library is therefore the contract of the classes the OS is
built on, plus a small set of builtins. Principles:

1. **Declare, don't restate.** A pack states what it is (allowed methods, whether it is public) as a property; the shared getter does
   the work once. A route family never writes its own 405 or 401.
2. **Data, not code, crosses packs.** Facts the launcher knows are one dict; a family reads a key.
3. **No new builtin unless the language cannot say it.** Each proposed builtin below has a reason a pack cannot work around.
4. **Every shared getter has a spec** (`tests/specs/*.json`) and every contract is documented here.

## The Request contract (`membrane/packs/door.c1f` is the library for the class `Request`)
A request is an entity `Request:<n>`. A family pack `IMPORT door 1` and defines, for its family `<f>` (`models decisions state voice
session self tools`):

| getter | meaning | required |
|---|---|---|
| `Request.o_<f>` | True when the path is the family's | yes |
| `Request.r_<f>` | the reply `{status, headers?, body?\|file?}`; None while an effect has not landed | yes |
| `Request.m_<f>` | the allowed methods, a list, e.g. `['GET', 'POST']`; the door answers 405 + `allow` for any other | no (without it the family checks its own) |

Shared getters a family may read (all on `Request`): `method path query headers body peer`, `is_read`, `authed`, `sub`, `person`
(the verified name), `entrant`, `https`, `facts` (the launcher's facts dict), `jbody` (the body parsed as JSON, `{}` when it is
not), `qpairs` (the query as `[[k, v], ...]`), `ms` (now in epoch milliseconds, whole). Per-request outbound work is a per-request
entity: `('Fetch:' + this.conn).request = {...}` and read its `request_result`.

## Boot facts
The launcher writes each fact as a property of `Boot:seat` and also the whole set as the dict `facts`; a pack reads
`prop(this.facts, 'seat_name')` instead of a comprehension over `instances('Boot')`.
They are assembled by ONE pure function, `fields_live.seat_facts(env, settings, srv, known)` (tests/test_seat_facts.py): a missing
source leaves its fact out (never None); `door_port` exists only when `MEMBRANE_DOOR_PORT` is set (no door by default);
`door_secret_file` is a path, never the secret. The router's facts (`router_on`, `cloud_models`, `minds`) are only known after the
router step: they are added then (`boot_started(..., announce=False)`), after `started` was said, so a setter on `started` sees
them empty (see the router pack's `router-rungs` / `router-minds`).

## Builtins proposed (each needs the operator's approval; none is added yet)
| builtin | why a pack cannot work around it |
|---|---|
| `int(x)` | truncating a float is `words(replace(str(x), '.', ' '))[0]` today: wrong for negatives and exponents |
| `dict(rows)` | build `{k: v}` from `[[k, v], ...]` (query params, grouped stats): c1f can only write a dict with fixed keys |
| `merge(a, b)` | one dict from two: a reply's headers, a family's extra fields |
| `keys(d)` | enumerate a dict (listing a settings object) |
| `urldecode(s)` | `%20` and `+` in a query value: no string primitive can say it |

## Conventions (the traps that bit)
- An expression has at most 64 nodes: split into named getters, prefixed by the family (`st_`, `v_`).
- Never name a getter or a dict key after another getter in the same class (the engine orders getters by finding names, strings
  excepted only as the last argument of a call); never name a property `this`.
- `concat` joins lists (`+` is vector addition); there is no `int()` yet; `clock` is epoch seconds.
- A class a shipped pack declares is owned: other packs `IMPORT` it.

## The contracts are executable
`native/tests/contracts.rs` fails when a contract is broken: every shipped pack loads together (view packs that import `dom` are
the browser's); a route family pack imports door, defines both `o_<f>` and `r_<f>`, is a reserved family, and does not redefine the
door's getters; every Boot fact a pack reads is in `membrane/packs/facts.json` (with a `why`), and a fact marked `wired` is written
by the launcher. `wired: false` entries are the launcher's to-do list for the cutover. A new fact = a registry entry; a new family =
a reserved name in `FAMILIES` and in the door's chain.

## The surface snapshot: a pack as a black box
`membrane/packs/surface.json` records, per pack, everything it touches from outside: its imports, its declared classes (and a count
of members), the Boot facts it reads, the paths it claims, and the properties it WRITES on other entities (`Goal:*.status`, `*.answer` for
a dynamic target, `ref(goal).replan` through a reference). `native/tests/contracts.rs` recomputes it from the pack text and fails on any
difference. Reviewing a pack, an agent's branch or Memo's edit means reading the surface diff, not the code: a pack that starts
writing a new property, claiming a new path or importing a new pack fails the test until the change is reviewed and recorded
(`SURFACE_UPDATE=1 cargo test --test contracts surface`). There is no `private` keyword: the contract is the surface.

## Order of work
1. This document and the Request contract's shared getters with specs (`tests/specs/door_std.json`): `facts`, `jbody`, `qpairs`,
   `person`, and the declarative method gate `m_<f>`.
2. The builtins above, one at a time, each with a Rust test, after approval.
3. Migrate the families the agents wrote to the contract (drop their own 405 checks, fact comprehensions and JSON parses).
