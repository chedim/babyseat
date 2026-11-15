# Documentation index

Every page here describes what the code does at 0.7.60 (`cliffvm`, the Rust seat). Pages for things that are gone (the 0.7 Python seat, the old page, Blurp, the quota and self features) were removed; they are in git and the 0.7.53 tag.
Anything in progress says so on its page and names its branch. The README at the root has the install and the quick start.

## If you use babyseat (user)

| page | what it is |
|---|---|
| [../README.md](../README.md) | what it is, install, the quick start, the claude credential, Home Assistant, upgrading from the Python seat |
| [home-assistant.md](home-assistant.md) | the HACS integration: Assist, entities, services, the seat's use of the house, the token, reauth |

## If you run a seat (operator)

| page | what it is |
|---|---|
| [auth.md](auth.md) | who may talk to the seat: no users, every valid token is the admin, the secret and token files, the page cookie and its host check, the claude credential |
| [cliffvm.md](cliffvm.md) | the reference: `cliffvm serve` and `run`, facts, the data folder, the dist folder, the door's routes, the chat turn, storage modes, packs the model loads, the history cutover |
| [settings-groups.md](settings-groups.md) | the Settings window: every row, the one write door, what is launch-only |
| [release-verification.md](release-verification.md) | the auth checklist: what was proved by which test |
| [packaging-plan.md](packaging-plan.md) | packaging and the launcher: what is decided, built and in progress (wheels, installer, image) |

## If you change the code (developer)

| page | what it is |
|---|---|
| [porting-brief.md](porting-brief.md) | the rules for c1f and Rust work (read first) |
| [c1f-layers.md](c1f-layers.md) | apps and layers, trust by position, privilege, class ownership, storage-mode header lines |
| [c1f-stdlib.md](c1f-stdlib.md) | the Request contract, boot facts, the builtins, locals, entity literals, constructors, the step budget, the contract tests |
| [c1f-error.md](c1f-error.md) | the `Error` value kind: a failure that is not a null |
| [cliffhanger-fields.md](cliffhanger-fields.md) | the field language's design: getters, setters, packs, the browser VM's host API (section 9) |
| [semtext-and-legs.md](semtext-and-legs.md) | SemText: how text becomes graph entities (the tokenizer, spans, mentions, sentence vectors, the field, entity recall by worldline, the Memory class and the banner recall are built; legs are design) |
| [chat-keys.md](chat-keys.md) | the session id in the message's key: why and how |
| [doctrine-barest.md](doctrine-barest.md) | the doctrine (the system prompt), the banner and the message: what the model gets, and what it promises |
| [ui-rebuild-plan.md](ui-rebuild-plan.md) | the page as c1f view packs: status per slice, the shell and view-pack contract, markdown and Stop |
| [graph-link.md](graph-link.md) | joining the page's graph and the seat's: slices 1 and 2 built (the page reports, the engine hooks), the rest design |

## Measurements and design notes (dated; not part of the release surface)

| page | what it is |
|---|---|
| [measurements-2026-11-14.md](measurements-2026-11-14.md) | the seat measured against the 0.7 records |
| [bench-system-pilot.md](bench-system-pilot.md) | an end-to-end pilot with the model in the loop on an empty seat |
| [reply-size.md](reply-size.md) | the size of an `entity_write` reply and the design that shrank it |
| [token-growth.md](token-growth.md) | why input tokens per turn grow on a real store |
| [append-write.md](append-write.md) | an append write for c1f: design, not built |
| [lexicon-design.md](lexicon-design.md) | a lexicon in the graph: design, nothing built |
| [logo/README.md](logo/README.md) | the logo files |

## The model's own pages

The topics the model reads with `entity_read Help:<topic>` are files in `membrane/apps/barest.c1f/help/` (`c1f`, `packs`, `upgrade`, `banner`, `cliff`, `semtext`, `sql`, `legacy`, `ha`, `settings`, `page`);
every code block in them is a test (`native/tests/help_1143.rs`).
