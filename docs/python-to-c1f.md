# Python to cliffhanger: the migration

**End state (operator, 2026-11-11):** Python is only the pip package (install, entry points) and basic VM
initialization. Everything else is `.c1f` packs on the field (getters, setters, steps as entities) or a native
primitive the VM calls (store, network listener, model runtimes). A model living on the seat can then read, change
and replay all of it.

Measured size today: 166 modules, about 92,800 lines of Python in `membrane/`.

## What stays Python (bare necessities)
- the package: `pyproject`, the `membrane` entry points, `cli.py`'s argument handling;
- VM initialization: open the store, start the VM, load the boot pack (stage 1);
- until native replacements exist: the model runtimes (spaCy, embedding and rerank models) as host jobs the VM runs
  off the engine; the HTTP listener's glue.

## What becomes a native primitive (Rust, called by the VM)
the store (done: #1111), the search structures (vectors, word index, episode matrix), the HTTP/SSE listener, the
JWT check, the model runtimes. These are drivers, like `sql()`, `fs` and `proc` already are.

## What becomes `.c1f` (everything that decides or reacts)
In order, each piece deployed on its own and replacing its Python:
1. **Boot:** `main()`'s 18 steps as entities with `after` edges and states; the heavy ones native; background steps
   after the UI is up (stage 1 native, stage 2 `boot.c1f`).
2. **The model's tools** (`mcp_server.py`, `toolset.py`: find, entity, exec, goal, memory, track, field_watch, state,
   peer): each a class whose setters are the effects.
3. **The language layer** (`lang.py`, `language.py`, `verbs.py`, `instances.py`, `convgraph.py`): verbs and aliases
   as getters on methods (stored, recomputed only on change), pronouns and registers as entities, the parser a host
   job.
4. **Sweeps and loops** (`night.py`, `watch_agents.py`, `thinker.py`, `autonode.py`): each pass a trigger over native
   scans with nothing built, scheduled at background priority.
5. **Goals and decisions** (`goalgraph.py` remainder, `intent.py`, `decision_map.py`).
6. **The turn path and serve** (`lanes.py`, `server.py`, `ui_serve.py`): routes as entities (a request is a write, the
   reply its value); the injection, the gate and the banner already are packs.
7. **Router, voice, peers, restarts, toybox, absorb, codegraph, predict** and the rest, one by one.
8. **Last: the entity store and the field engine** (`entities.py`, `efield.py`, `efstore.py`, `cblstore.py`): native.

The UI follows its own track: each window a `dom` view pack in the browser VM (the About window is the first).

## Rules for every piece
- failing test first; the Python it replaces is deleted in the same change;
- a piece keeps the seat bootable: a failed step records its error and the boot continues;
- every step has a way back (replay from the journal, or the shipped pack restored);
- stopgaps written in Python while a piece is pending are labelled and removed with it.
