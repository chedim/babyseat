# c1f apps and layers (design, decided 2026-11-13; not built yet)

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

`cliffvm run <app>.c1f` replaces the separate packs folder and `--ui-dir`. Several apps may be loaded, in the order given on the
command line; each app is a LAYER and a later app is above an earlier one (within an app, a pack that imports another is above it).

## Layers: the upper layer wins (the way an overlay filesystem works)

| construct | rule |
|---|---|
| getter | the arms of every layer form ONE list, the top layer's arms first; the first arm whose `WHEN` holds wins and the search falls through to lower layers; a default arm (`ELSE:`) in an upper layer ends it there. This is the getter-arms machinery (`fold_arms`) with arms ordered across packs. |
| setter | every setter on the same property fires, TOP layer first (the original: "reverse order of layer loading"). A setter with the SAME NAME as a lower layer's replaces it: the lower one is dropped when the layers are composed and is never evaluated (explicit override). A differently named one stacks, and when two stacked setters write the same target (an entity and a property) in one round the FIRST write wins, which is the top layer's: before a write line's right-hand side is evaluated the engine checks whether its target was already written this round by a higher layer, and if so skips the line without evaluating it and without running any effect (an overridden `Command:x.run` never runs). A setter's `WHEN` is still evaluated, because the engine cannot know statically which writes it would make. Inside ONE layer a double write to a target stays the existing error ("already written this round"); across layers it is the override and a quiet skip, and `Why` says `overridden by <layer>`. |
| class | the lowest layer that declares a class owns it. An upper layer must `IMPORT` the owner, as today; a pack that declares on a class it does not import is still refused. |
| data file | a file at the same path in an upper layer shadows the lower one. |
| `Why` | names the layer that supplied a value (`layer: <pack>`), so a value never comes from nowhere. |

**The safety rule:** a layer can override ONLY what it imports. An unrelated pack cannot silently change another pack's behaviour.

## Open points to verify when it is built

- Whether the engine today allows a pack to add arms to a getter another pack defines (class ownership may refuse it; that rule
  changes, only for packs that import the owner).
- Whether getter ordering (levels) and the stale-setter check treat arms and setters from several layers correctly.
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
- **Root of trust.** The host marks the pack named `boot` (packs/boot.c1f) privileged and nothing else (`Host::open`); boot.c1f grants
  `door_tools` at start with a setter on `Boot.started` (`Pack:door_tools.privileged = True`), so the model's `entity_write` can write the
  class a model-written pack owns. Anything that loads a pack named `boot` gets that trust: the model's tool (privileged) can write
  `Pack:boot.text`; consistent with "no security, the model owns its environment".
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
  values of a pack that no longer exists do not linger (`Fields::remove_pack_clean`).
- A getter named `privileged` on the class Pack computes a stored value but grants nothing: only writes grant.
