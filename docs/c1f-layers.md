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
