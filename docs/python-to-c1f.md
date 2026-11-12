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
   **Status (2026-11-12):** the order, edges, `essential` flags and background steps are data (`packs/boot.json`,
   `packs/boot.c1f`) and the native VM walks them (`Fields.boot` / `boot_up`, `native/src/plan.rs`). `main()` still holds the
   steps' bodies as Python closures, handed over by name through one bridge (a stopgap that shrinks as each step becomes
   c1f or a native primitive).
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

## The UI is a cliffhanger app too (operator, 2026-11-11)
Today `membrane/ui/` is about 16,100 lines of JavaScript (`home.js` 4,900, `panel.js` 4,100, `field3d.js` 2,600,
`field.js` 1,000, `decisions.js` 1,000, `vosk.js` 800, the rest small). The target is a thin shell and view packs:

- **The shell stays JavaScript:** `index.html`, the WASM loader, and one adapter (`host.js`): DOM nodes as `DomNode`
  entities, events (click, input, key, scroll) as writes, `fetch` and the seat's event stream as entities, WebGL/canvas
  drawing as `dom` built-ins. No UI logic lives in it.
- **Everything the person sees is a view pack** running in the browser VM, loaded from the seat like any pack, so a
  model can read and change its own interface (the About window is the first).
- **Order, smallest first:** the loading and offline field animation (it becomes the graph viewer: rotation, zoom, no
  reload between steps), favicon, the decisions board, the 2D field view, the 3D field view (needs the drawing
  built-ins), the chat panel (messages, tool and injection cards, composer), the home page and its windows, voice.
- **Needs first:** the event and draw built-ins in the `dom` library, and list virtualization for long chats.


## Rules for every piece
- failing test first; the Python it replaces is deleted in the same change;
- a piece keeps the seat bootable: a failed step records its error and the boot continues;
- every step has a way back (replay from the journal, or the shipped pack restored);
- stopgaps written in Python while a piece is pending are labelled and removed with it.

## Status (2026-11-12) and the end state

**End state (operator, 2026-11-12):** Python is the package wrapper and launcher only; everything else is Rust (native VM, webasm for
pages) and `.c1f`, mostly c1f and Rust minimal (sockets, files, token checks, SQLite, vectors); the HTTP door is Rust, with no
forwarding to Python; writes into entities through setters, not function calls; every piece test first.

| piece | state |
|---|---|
| 1 boot | native plan (`plan.rs`); the door starts from the boot facts (`door.c1f`); the rest of `main()` still Python |
| 2 model's tools | `learn`, `forget`, `intend`, `fulfill`, `intents`, `answer_card`, `peer_post`, `attention` are c1f writes (`memory`, `intents`, `decisions`, `peer`, `attention` packs); the watch family and `junk_cut` wait for piece 8; entity methods are retired (#1101): `methods.c1f` translates each mechanical one into a `Command:<name>` at boot (`{{param}}` -> `"$1"`...), the rest is listed on `Methods:review`; the four method tools, the `exec` define/call/each/inspect actions and the `/membrane/method/*` routes are gone. `night.method_*` Python stays while the adapters' `invoke`, the goal graph, the HA bridge, `lang.py`/`language.py` and `self_sheet` still call it |
| 3 language layer | pronouns: `Turn`/`Ctx` getters (`turn.c1f`); registers and `convgraph` not yet |
| 4 sweeps and loops | not started |
| 5 goals and decisions | decisions and the board's pen are c1f; the selection (pin, cap, ranking) stays with the dream loop |
| 6 turn path and serve | the Rust door (`http.rs`, `jwt.rs`), the UI shell and files (`door.c1f`); every request is an entity a pack answers; 0 of ~140 routes ported; the message turn is untouched |
| 7 the rest | not started |
| 8 entity store and field engine | not started; the SQLite store is native (`db.rs`), `Store:<x>.run` writes from c1f |

**"All" for the door** is the routes the UI, Memo, `opencode attach` and Home Assistant actually use; the routes that call the old
engines (map tiles, the universe, `night`) answer 404 until their engine is native. Tests: JSON specs (`tests/specs/*.json`, run by
`native/src/spec.rs`) for pack behavior, Rust tests for drivers, pytest for what remains Python. Engine rules found the hard way:
the engine orders getters by finding other getters' *names* in an expression, strings included (a property named `this`, or a dict
key equal to a getter's name, makes a cycle); a class a shipped pack declares is owned by it (other packs `IMPORT` it).
