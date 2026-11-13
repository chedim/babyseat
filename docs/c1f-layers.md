# c1f apps and layers (decided 2026-11-13; BUILT 2026-11-13, #1143: see the status table)

A c1f APP is a folder named `<name>.c1f/` (the extension is the same one a single pack file has; the loader tells a file from a
directory). It follows the original cliffhanger modules (`~/projects/cliffhanger/README.md`: a module is a folder whose files map
onto the data tree and whose `.ct` files define triggers, each module a separate trigger layer), carried over to today's
class-based c1f.

```
chat.c1f/
  chat.c1f            packs: c1f files, loaded in dependency order (a pack after the packs it imports)
  chat_turn.c1f       packs with no import relation load in ALPHABETICAL order of their file path
  ui/index.html       every other file is DATA at its path (served by the door; an entity of the file's path)
  ui/chat.js
```

`cliffvm run <app> [<app>...]` (a folder `<name>.c1f/` or one pack file). Several apps are loaded in the order given on the command line;
each app is a LAYER and a later app is above an earlier one (within an app, a pack that imports another is above it). `--ui-dir` stays, as
the LOWEST layer of data files. The FIRST app is the root of trust (see Privilege).

## Status (what is built, what is open)

| piece | status |
|---|---|
| app folders, several apps, load order (dependency, then alphabetical by path), skip a pack already loaded by name | built (`apps.rs`, `Host::load_app`) |
| trust by position (the first app's packs are privileged, nothing by name) | built; tests `apps_1143.rs` |
| data files served by the door, upper layer shadowing (`Boot:seat.app_dirs`, `door.c1f`, one stat at a time) | built; `door_static.rs`, `cliffvm_process.rs` |
| replacing a pack's text with one that lacks a getter clears the value | built; `apps_1143.rs` |
| getter arms stacked across packs, top first, an upper default ends it | built; `layers_1143.rs` |
| one resolution rule across the classes of an entity (own class, `parents` in order, root `Entity`); root pack `entity.c1f` with `Entity.summary` | built; `resolution_1143.rs` |
| setters: top first, same-name replacement, first write wins (quiet skip before the right-hand side), in-layer double write stays the error | built |
| `Why`: `Fields::why_layer` (`layer: <pack>`, `overridden by <pack>: <pack>'s <setter> write skipped`) | built as a Rust API; no door route shows it yet |
| safety rule (override only what you import), stale-setter check across layers | built |
| scoped override exception: importing B alone lets a layer override exactly what B defines on a class another pack owns | built; `layers_1143.rs` |
| a runtime `Pack:<name>` takes its layer position | by load order: a new pack is on top of everything loaded before it; a replaced pack keeps its place |
| infer.rs types across layers | a getter defined by several packs is typed from the LOWEST pack's arms (`or_insert`); the snapshot is unchanged |
| data files as entities of their path | not built: the door searches the folders with the fs driver; no entity per file |
| a layer's setter that differs in name and writes a target a lower layer also writes, without importing it | not refused (writes are computed, so it cannot be known at load); the first write wins |

## Layers: the upper layer wins (the way an overlay filesystem works)

| construct | rule |
|---|---|
| getter | the arms of every layer form ONE list, the top layer's arms first; the first arm whose `WHEN` holds wins and the search falls through to lower layers; a default arm (`ELSE:`) in an upper layer ends it there. This is the getter-arms machinery (`fold_arms`) with arms ordered across packs. |
| classes of one entity | ONE rule over getters (#1143): for each (entity, property) one ordered list of arms, NEAREST CLASS first: the entity's own class (the eid class), then its `parents` in list order, then the implicit root `Entity`; within a class the top layer first (the row above). The first arm whose `WHEN` holds wins, an `ELSE:` arm ends the search, a failing `WHEN` falls through to the next-nearer class. `Trip.summary` beats `Entity.summary`; a `parents` class never beats the entity's own class. Setters keep their own rule (below). `native/tests/resolution_1143.rs`. |
| setter | every setter on the same property fires, TOP layer first (the original: "reverse order of layer loading"). A setter with the SAME NAME as a lower layer's replaces it: the lower one is dropped when the layers are composed and is never evaluated (explicit override). A differently named one stacks, and when two stacked setters write the same target (an entity and a property) in one round the FIRST write wins, which is the top layer's: before a write line's right-hand side is evaluated the engine checks whether its target was already written this round by a higher layer, and if so skips the line without evaluating it and without running any effect (an overridden `Command:x.run` never runs). A setter's `WHEN` is still evaluated, because the engine cannot know statically which writes it would make. Inside ONE layer a double write to a target stays the existing error ("already written this round"); across layers it is the override and a quiet skip, and `Why` says `overridden by <layer>`. |
| class | the lowest layer that declares a class owns it. An upper layer must `IMPORT` the owner, EXCEPT when every declaration it makes on that class overrides what a pack it imports DIRECTLY defines (see below); otherwise it is refused with `add IMPORT <owner>`. |
| data file | a file at the same path in an upper layer shadows the lower one. |
| `Why` | names the layer that supplied a value (`layer: <pack>`), so a value never comes from nowhere. |

**The safety rule:** a layer can override ONLY what it imports. An unrelated pack cannot silently change another pack's behaviour.

## Checked when it was built

- A pack may add arms to a getter another pack defines when it imports the class owner (class ownership allowed it); before, two packs
  defining one getter were two getters writing one field (the later won, silently). Now they are one list, and the pack that adds to a
  getter of another pack must import THAT pack too (the safety rule, refused at load with `add IMPORT <pack> <major>`).
- Levels and the type inference read the folded expression of the whole list; the stale-setter check runs across all packs (a setter
  in one pack against the getters of every other, both ways).
- The scoped override exception (operator decision 2026-11-13): a pack that imports B may override exactly what B defines on a class
  owned by another pack A (B's getters by class+property, B's setters by class+property+name) with ONE import line, B. It is not
  transitive trust: the rule is per class, over ALL of the pack's declarations on that class. Each must be an override of a definition of
  a pack it imports DIRECTLY, or the pack needs `IMPORT A` (so overriding one getter of B and adding another, or overriding what A itself
  defines through B's import, is refused with `belongs to pack A: add IMPORT A`). The safety rule (`add IMPORT <pack>` for an override of
  a pack you do not import, transitively counted) and the write rule B are unchanged.

## Open points

- How a model-loaded `Pack:<name>` entity (the pack-loading piece, `docs/cliffvm.md`) takes its layer position: a runtime pack is
  loaded last and is therefore on top.
- The doctrine says a model "replaces the banner completely by giving its entity a class of its own"; with layers a model could
  instead override the banner's `text` arms. Keep both until a real case chooses.

## Privilege (built 2026-11-13, #1143)

The ownership rule has two parts. A: only the owner of a class (the pack that declares it) defines its getters and setters; other packs
`IMPORT` it. B: a pack may WRITE only entities of classes it owns or imports (a guard against a pack's own mistakes; the seat has no
security by decision). A PRIVILEGED pack is exempt from B only: it may write, and create, entities of any class (a class no pack owns too:
the class comes from the eid, as creation does). A stays: privilege never lets a pack define getters or setters on a class it does not
own or import (`compose` refuses it, tested).

- **Granted by a write.** `Pack:<name>.privileged = true`. The grant is by NAME (`Fields::note_grant`): it holds for a pack loaded from a
  file of the packs folder, from `Pack:<name>.text`, before it is loaded and after it is removed. A Pack entity with no text does not
  remove a file-loaded pack (start-up skips such a row; only a written empty text removes). No header keyword, no self-declaration.
  `privileged = false` (or none) revokes.
- **Root of trust: by position, not by name** (decided 2026-11-13). The host marks the packs of the FIRST app given to `cliffvm run`
  privileged (`Host::load_app(.., trusted = true)`), whatever they are called, and nothing else: a pack named `boot` that is not first is
  not trusted, a later app has no privilege until a grant. The shipped line is `cliffvm run membrane/packs/boot.c1f membrane/packs`.
  boot.c1f grants `door_tools` at start with a setter on `Boot.started` (`Pack:door_tools.privileged = True`), so the model's
  `entity_write` can write the class a model-written pack owns. The model's tool (privileged) can write the text of the root's packs;
  consistent with "no security, the model owns its environment".
- **Only a privileged writer grants.** A setter's write to `Pack:<x>.privileged`, by any path (direct, a computed name, a constructor
  argument, a chain of setters: all run as the pack that declared the setter), needs a privileged pack, whether or not that pack owns or
  imports the class Pack; otherwise a receipt (`pack <p> can't grant privilege`) and nothing is written. A privileged pack grants to a
  pack it loads (a chain of trust: boot -> `door_tools` -> packs the model loads through it). Outside writes (the host, the door's
  person) are never subject to rule B or to this check.
- **Provenance.** The host mirrors `Pack:<name>.granted_by` as a derived, non-journalled write (like `loaded`, `error`): `pack:<pack>:<setter>`
  for a setter's grant, the outside writer's name (`person`, `host`) otherwise; never from the pack's own text. A grant persists until a
  privileged writer revokes it; it does not die with the granter. A stored `privileged = true` row is read back at start.
- **Replay.** A person's grant is an outside write (journalled); a setter's grant re-derives from the write that triggered the setter, so a
  replay ends in the same privilege state (`host_loop.rs`, `privilege_1143.rs`).
- **Review.** The surface snapshot lists `Pack:door_tools.privileged` among boot's writes (`surface.json`; `types.json` shows `Pack.privileged`);
  a new grant is a diff the contracts test fails on until reviewed.
- **Removed pack cleanup.** When a pack is removed, the values its getters left on entities are cleared (derived writes of none), so stale
  values of a pack that no longer exists do not linger (`Fields::remove_pack_clean`). The same when a pack's text is REPLACED by one that lacks a getter the old text had (`Fields::set_pack_clean`); a refused text changes nothing.
- A getter named `privileged` on the class Pack computes a stored value but grants nothing: only writes grant.
