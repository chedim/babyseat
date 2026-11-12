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
| 2 model's tools | `learn`, `forget`, `intend`, `fulfill`, `intents`, `answer_card`, `peer_post`, `attention` are c1f writes (`memory`, `intents`, `decisions`, `peer`, `attention` packs); the watch family and `junk_cut` wait for piece 8; entity methods are disabled (creation refused, every call carries the deprecation note), to be translated to `Command:` entities and removed |
| 3 language layer | pronouns: `Turn`/`Ctx` getters (`turn.c1f`); registers and `convgraph` not yet |
| 4 sweeps and loops | not started |
| 5 goals and decisions | decisions and the board's pen are c1f; the selection (pin, cap, ranking) stays with the dream loop. The decision routes are the door's (`door_decisions.c1f`, family `decisions`): the board, goals, work graph, answer, drop, chat (specs `tests/specs/door_decisions.json`) and entity-history / item-chat (store rows; `native/tests/door_decisions_sql.rs`); `Decision.drop` is a decisions-pack write. Still Python: `/membrane/item-chat/say` (no relation-write verb), the work graph's watch nodes and rocket runs, an entity's `properties` and field changes |
| 6 turn path and serve | the Rust door (`http.rs`, `jwt.rs`), the UI shell and files (`door.c1f`); every request is an entity a pack answers; the decision family ported too (see row 5; about 25 of ~140 routes in all); the message turn is untouched; **state family** (`door_state.c1f`, `tests/specs/door_state.json`): GET `/membrane/seat`, `/membrane/seat/pause`, `/membrane/state`, `/membrane/self/card` are getters over the presence entity (`membrane::presence`, class Seat; `state`/`events`) and `Boot:seat.nerve_pause_off`; POST `/membrane/seat/pause` and `/membrane/seat/control` write `membrane::presence.control = {id, by, body}` for the verified person and answer 202. **Gaps (Python still serves them; the door answers 501):** nothing consumes `control` yet (the effect is still `seat_panel.control` / `set_pause`), the state read is the panel's cached `state` not the full instrument, `/membrane/stats` (needs a dict with data-driven keys: `models` is keyed by model name; fork), `/membrane/quota`, `/membrane/minds`, `/membrane/self/sheet`, `/membrane/self/recent` (live Python state), the state `?section=` pull |
| outbound http | `Fetch:<x>.request` (`fetch.rs`) speaks http and https: TLS is rustls (ring) with the webpki roots, behind the `tls` feature (on with `python`, absent from wasm); `ca_pem` in the request adds trusted roots (private CA, tests); a certificate failure is a receipt (rc 1, head names it) |
| 7 the rest | **models (router data + routes):** `Model:<name>` entities and the opencode screen, owner and autoselect lanes are c1f (`packs/router.c1f`, `fetch.c1f`); `/v1/models` and `/membrane/models` (GET, POST) are a door family (`packs/door_models.c1f`, `Request.o_models`); the minds' models are `Minds:seat`. **Still Python, on purpose:** the live scoring/ladder/outages/ceilings/spend (`router.py`, needs embeddings), the `/membrane/options` route (reads settings.json, energy and the live runner), and what *applies* a minds pick (runner.model, models.json) in `minds_models.py`. The launcher must tell `Boot:seat.seat_backend`, `.router_on`, `.cloud_models` ([{name, backend}]) and `.minds` before `started`; later rung registrations need a write too. The Python handlers stay until the door takes over those paths; nothing is deleted yet. Not ported: owner-only writes (the door has no owner check yet), the `/v1/models` order of local tags is entity order |
| 7 the rest (others) | not started |
| 8 entity store and field engine | not started; the SQLite store is native (`db.rs`), `Store:<x>.run` writes from c1f |

**"All" for the door** is the routes the UI, Memo, `opencode attach` and Home Assistant actually use; the routes that call the old
engines (map tiles, the universe, `night`) answer 404 until their engine is native. Tests: JSON specs (`tests/specs/*.json`, run by
`native/src/spec.rs`) for pack behavior, Rust tests for drivers, pytest for what remains Python. Engine rules found the hard way:
the engine orders getters by finding other getters' *names* in an expression, strings included (a property named `this`, or a dict
key equal to a getter's name, makes a cycle); a class a shipped pack declares is owned by it (other packs `IMPORT` it).
