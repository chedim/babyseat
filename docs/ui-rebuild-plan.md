# UI rebuild plan: the released UI as c1f view packs in the browser VM (#1143, swap step 3)

Plan only, no code. Tree `memo-hotfix-0753` at `242948c1`. Numbers marked **[D]** are operator decisions (list at the end); none is chosen here.

**Important first.** Today the browser VM can only DRAW and follow the seat: a pack cannot call a route, cannot read an input, hears one event (a click on a node),
has no timer but the `clock` tick, and cannot draw on a canvas. So no view except About can be a pack yet. The first slice (chat panel + graph viewer) needs
three wasm rounds first (dom v2 inputs/events, a page `Fetch`, an SSE/stream feed), then about 12-18 rounds for the two views. Recommended first slice: the
BARE app's chat and graph (745 lines of JS, routes all built) and not the released `panel.js` (4,113 lines, 18 Blurp parts): it proves the whole architecture
at a tenth of the cost. The released chat panel follows as slice two, after D4/D10 of `docs/release-parity.md` are answered.

## (a) What the browser VM has today versus what a view pack needs

Measured from the code (`native/src/web.rs`, `membrane/ui/fieldvm.js`, `docs/cliffhanger-fields.md` section 9, `docs/release-parity.md` section 2):

| thing | today | evidence |
|---|---|---|
| the engine | the seat's Rust VM compiled to wasm: getters, setters, packs, imports; JS API `load, write, remove, get, drain, set_pack, tick, settle, rebuild, classes` | `web.rs:33-98` |
| size | wasm 1,099,348 bytes (417 KB gzipped; the parity doc says 933 KB: stale), loader 14 KB | `ls`, `gzip -9` |
| DOM output | `DomNode:<key>` entities: 27 tags (no `input`, `textarea`, `select`, `canvas`, `svg`, `img`), 7 attributes (no `value`, `placeholder`, `disabled`, `style`, `data-*`), text only; one frame per round | `fieldvm.js:21-23`, `apply()` |
| events in | ONE: `click` on a node writes `clicked` (time). No key, input, submit, scroll, focus, pointer, drag | `fieldvm.js:69` |
| page DOM | linked read-write as `DomNode:page:*` (shadow roots too, no echo) | `link`, `fieldvm_link.test.mjs` |
| following the seat | `GET /ui/fieldvm` snapshot, then `field.write` / `field.pack` / `field.remove` from `/ui/serve/event`; reconnect = fresh snapshot | `fieldvm.js:62-114` |
| toward the seat | READ-ONLY: what a pack writes stays in the page. NO page `Fetch` entity | `cliffhanger-fields.md:241` |
| timers | `Field.tick(now)` exists (the `clock` entity); no `every`, no `setTimeout` for a pack | `web.rs:98`, `cliffhanger-fields.md:117` |
| storage, clipboard, canvas, audio | none | parity doc section 2 |

What a view pack needs, and the wasm rounds (1 round = 20-45 min; sizes are my estimates, the parity doc's totals are 20-30 rounds for the VM, 112-169 overall):

| round | work | rounds | basis |
|---|---|---|---|
| W1 | dom v2: `input`, `textarea`, `select`, `form`, `label`, `svg`-free; attributes `value placeholder disabled type name style(allow-list) data-*`; events `input` (writes `value`), `submit`, `key` (Enter/Shift state), `focus`, `scroll`; a pack WRITES `value`/`focused`. Parity doc: "dom v2 5-8" for all views | 3-4 of the 5-8 | needed by chat and graph filter |
| W2 | page `Fetch` entity ("round 2" in the plan): the page's VM gets the seat's `Fetch` class; a write to `Fetch:<id>.request` makes a `fetch()` in JS, the receipt comes back as a write (`status`, `body`, `ms`), same shape as `fetch.c1f` on the seat; same-origin only, credentials same-origin | 2-3 (parity "fetch + SSE 4-6", shared with W3) | all reads and the send |
| W3 | stream feed: a `Fetch` with `stream = true` (or `Stream:<id>`) writes each SSE frame as a `Chunk:<fetch>:<n>` entity, the style of the seat's own `Chunk` | 2-3 | message events |
| W4 | input/events part two ("round 3"): pointer events (down/move/up/wheel with x, y), `keydown` with key name, reconciliation (below), virtualization (a windowed list node: only rows in view become elements), `scroll_to`/stick-to-bottom write | 3-4 (parity "list virtualization 3-4", "dom v2" rest) | graph canvas/SVG, long transcripts |
| W5 | draw primitive for the graph: either SVG nodes in the allow-list (W1) or a `canvas` node with a draw list (parity "canvas 2D 4-6") | 0 if SVG, 4-6 if canvas **[D]** | graph |
| W6 | storage effect (localStorage), 1-2 (parity) | 1-2 | login, drafts, last view |
| W7 | JS-island bridge for WebGL / audio / mic (parity 3-4) | not in slice one | 3D field, voice |

**W1 status (2026-11-14): built** on branch `w53-dom2`: `DOM_ALLOW` (one table), form + SVG tags, the attributes, events as writes (`value`, `submitted`, `key`, `focused`, `scroll`), pack writes of `value`/`disabled`/`focused` without echo, refusals visible (`refused`, `data-refused`). Operator answers 2026-11-14: `href` stays http(s)-only (About needs it); `style` and `src` stay refused; Enter-without-Shift `preventDefault` in a textarea is a HARDCODED SHELL BEHAVIOUR, fine for now; no scroll throttle yet; `checked selected multiple` are in the table.

Reconciliation: today every `DomNode` write is a DOM write, applied once per frame; a pack that rebuilds a list rewrites every row. W4 adds keyed diffing
(children order from an ordered `children` list, not from creation order), so a re-render touches only the rows that changed. Wasm-side cost: one round
for the diff in the host JS, one for the order property in the VM.

## (b) Architecture

A view is a pack that declares its own class and draws with `dom`. No new mechanism: the entity IS the view state, in line with "UI elements are field entities"
and "converge on existing classes".

- Class `View:<name>` (one per mounted view; props `open`, `mode`, `filter`, `selected`), plus per-row entities the view derives (`ChatRow:<msg id>`, `GraphNode:<eid>`).
  Getters compute what to show from seat entities the page already follows (`Chat:message:*`, any entity by `/ui/fieldvm`); on-setters write `DomNode:*`.
- JS shell ("reconciler") = `fieldvm.js` grown by W1-W4: applies DomNode diffs, forwards events as writes, runs the `Fetch` and the SSE feed. It decides nothing:
  no view logic, no strings of the product. Target: 318 lines today to under 700 lines with W1-W4 **[D: the cap]**.
- Where state lives: view state (filter, selected, scroll, draft) lives in the BROWSER store (page-local, lost on refresh unless W6 saves it); chat and field state
  live on the SEAT (entities, sqlite). A write from a pack to a seat entity goes as a `Fetch` to an existing route (`POST /membrane/chat/message`, `/mcp`), never
  as a raw "field write" door: the seat's routes stay the only way in, which keeps auth (JWT cookie), validation and the golden fixtures as they are.
- Sync: page-side entities arrive by `/ui/fieldvm` snapshot + `field.write` over `/ui/serve/event` (built). Today the snapshot holds only entities of classes the
  view packs declare on (`cliffhanger-fields.md:239`); the chat's classes and `Chat:message:*` must be added to the allow-set **[D: which classes, and a cap on rows
  in the snapshot]**. Streaming text arrives as `Chunk` entities, or as `field.write` of `stream_view` (the existing message event).
- Refresh / offline: refresh = new snapshot, view state starts from defaults (W6 restores `filter`, `mode`, draft). Offline = the stream drops, the host keeps
  the last DOM, shows `status` from a `Conn:page` entity the host writes (`online`, `last_ok`), reconnect takes a fresh snapshot (built). No outbox in slice one:
  a send while offline fails visibly **[D: outbox or not]**.

## (c) First slice: chat panel + graph viewer (the BARE app's, `membrane/apps/barest.c1f/ui/`)

Why the bare app's: its chat (174 lines) and graph (194 lines) read routes that are built and tested (`/membrane/chat/messages`, `/membrane/chat/message`,
`/membrane/chat/message/<id>/events`, `POST /mcp` with `entity_query`/`entity_read`; `docs/cliffvm.md` "What you see"). The released panel needs the session API
(rows 15-31 of the parity doc, built in w31) plus quota, voice, predict ghosts, slash commands, model picker, feedback: a second slice.

**Chat pack (`chat_view.c1f`)**
- entities: `View:chat`; the messages are the seat's `Chat:message:<id>` (followed); `ChatRow:<id>` derives `who`, `bubble`, `pending`, `tools_n`; `Composer:chat` {draft, sending, error}.
- getters: row order (by id), `who` (queued / working / seat + seconds), tool summary with 90-char shortening (the shortening length is a constant **[D]**), "Load older turns" visible when `more`.
- events: `Composer.draft` from `input`; `submit` or Enter without Shift writes `Fetch:send.request`; the receipt's 202 clears the draft; a failure sets `Composer.error` (shown as the note bubble, as now). `clicked` on "Load older" writes a `Fetch` with `before=`.
- routes: all built (list above). The 3 s list poll and 700 ms fallback of `chat.js` become one `clock` rule with the two periods as facts **[D: 3000 ms, 700 ms, 10 poll size, 20 page size are today's constants; keep or not]**; the SSE stream replaces polling when W3 exists.
- needs: W1, W2, W3, W4 (virtualization only past **[D: N rows]**; stick-to-bottom within 80 px is today's number **[D]**).

**Graph pack (`graph_view.c1f`)**
- entities: `View:graph` {filter, show_requests, mode: graph|list, zoom, pan}, `GraphNode:<eid>` {cls, x, y, deg, hidden}, `Side:graph` {eid}.
- getters: edges from ownership (`A:b:c` under `A:b`) and `{"$ref"}` values (a getter over the followed entities); class colour from a hash (the formula is `graph.js:9`, a constant mix **[D]**); filtered/hidden; the side panel's rows; counts.
- the force layout: a per-tick setter on `clock` over N nodes is the honest cost: O(N^2) repulsion in the VM per tick. Recommendation: slice one ships the LIST view and the side panel as pure packs (W1 only), and the canvas/SVG layout as the second demo, with the layout either (i) in a pack with a node cap or (ii) a named JS island (W7) **[D: pack layout vs island, and the node cap]**.
- routes: `POST /mcp` `entity_query` (paged, `kind!:`, `full`) and `entity_read`: built. With the page already following the field (snapshot), most reads need NO route; `entity_query` stays for entities outside the allow-set.
- needs: W1 (filter, checkbox, buttons, list), W2 (query), then W4 pointer events + W5 for the canvas view.

**Smallest demoable vertical slice (fewest rounds):** W1 minimal (textarea + submit + `value`) + W2 (`Fetch`) = about 4-5 rounds, then `chat_view.c1f` with send and polled history (3-4 rounds): a user types in the browser, the pack POSTs, the message and the answer appear as DomNodes. Demo = the chat pane running only as a pack beside the old graph. That is about 8 rounds to the first demo; W3 (streaming) +2-3 makes it live; the graph list view +3 (reads only).

## (d) Coexistence during the rollout

- One page, two renderers per view: each view has a mount point in the page and exactly one owner. The old JS mounts if the fact says `legacy`, the pack mounts if it says `pack`. The switch is an explicit seat fact `ui_views` (a map `view -> legacy | pack`), read at `GET /ui/fieldvm` and also changeable by `?ui=` in the URL for testing **[D: name, default `legacy` for every view until its parity test is green, and whether the URL override survives into the release]**.
- Per-view, not global: chat and graph can flip separately; the shell (`index.html`, `host.js`, login) stays JS until the last slice.
- The old views are not touched during the rollout; the 94 Python tests that run `node` on the old JS keep proving "the page did not change" until a view flips, then its tests are replaced by pack tests (the parity doc's 26-41 rewrite rounds).
- Users notice nothing while the default is `legacy`; flipping a default is its own step with the golden DOM snapshot green. The bare app has no legacy: its new views ARE the first user, so the slice has no coexistence cost.

## (e) Tests

- Engine: `tests/js/fieldvm_core.test.mjs` (wasm in Node, a pack against writes and `drain`) is the unit layer for every pack: write a seat entity, assert the `DomNode` writes. About's `about_view.test.mjs` is the template.
- DOM: golden DOM snapshots: a view's final `DomNode` tree serialised (tag, class, text, parent, order) per scenario (empty, 3 messages, a running message, an error, long history, filter applied), stored beside the test, compared exactly; a diff is a reviewed change.
- Browser: `fieldvm_link.test.mjs` and `page_boot.test.mjs` (headless Chrome over the DevTools protocol; `page_boot` starts `scripts/seat-full` and records requests). Add: type into the composer, submit, assert one `POST /membrane/chat/message` and the row appearing; assert no console error; assert the old and new view produce the same visible text for the same entities (the coexistence check).
- Red first: every round opens with the failing test (standing rule); each W round ships its test in `tests/js/` and, where Rust changes, `native/tests/` plus `wasm_check_1143.rs`.
- Accessibility test: the snapshot must contain the roles/labels the old HTML had (`aria-live` log, `aria-label` on inputs).

## (f) Risks

- Bundle: wasm 1.1 MB raw / 417 KB gzipped, plus 14 KB loader; each W round can grow it. A budget **[D: max wasm size, gzipped]** and a test that fails over it. The seat caches `.wasm` by hash (built).
- Startup: the page must not show a blank pane while wasm compiles; the old view stays mounted until the pack's first frame (so `legacy` is also the loading state). Measure time-to-first-frame in the page_boot test **[D: the budget in ms]**.
- Memory: the page holds a copy of the followed field. The snapshot cap (b) and virtualization are the guards; a 5,000-row chat history is the test.
- Accessibility: `DomNode` lacks focus management, `aria-live` on a log, `label`/`for`, `tabindex`. The allow-lists must grow with W1 (`aria-*`, `tabindex`, `for`, `type`), and focus must be a write, or the rebuilt composer is a regression.
- Mobile: the page uses a `viewport` meta and the old CSS; a pack uses the same class names, so CSS is shared. IME composition and soft-keyboard `input` events need W1 to read `value`, not `keydown`.
- Security: text only, never HTML; `Fetch` same-origin only; a pack can write to a seat only through an existing route (so owner/guest gating still applies). Untrusted pack = the seat's own packs only; no user-supplied view packs in slice one.
- Scope creep: the released `panel.js` carries Blurp/self/toybox parts; building them in c1f before D10 is answered wastes 6-8 rounds.
- The seat's snapshot door (`/ui/fieldvm`) shipping every entity of declared classes may be large; paging it is a seat-side change not counted above.

## (g) Build plan (red test first, one round each unless said)

1. W1a: textarea/input + `value` + `submit` + key event in the VM and host; red: `fieldvm_core` and a Chrome test that types and reads `value`. (2 rounds)
2. W2: page `Fetch` entity + same-origin guard + receipt writes; red: pack POSTs to a stub route, reads the receipt. (2-3)
3. Chat pack send + history (polled); red: golden DOM for 0 / 3 messages, one POST. (3-4) **Demo 1.**
4. W3 stream feed + streaming answer in the chat pack; red: a message event updates the same bubble. (2-3)
5. Graph pack, list view + side panel + filter; red: golden DOM for the list. (3)
6. W4 pointer/wheel events + reconciliation + virtualization; red: 5,000 rows keep under the row cap. (3-4)
7. Graph layout (pack or island, W5/W7); red: layout test with a fixed seed. (4-6)
8. The `ui_views` switch and the coexistence test; flip chat and graph on the bare app. (2-3)

Slice one total: **about 22-32 rounds** (VM 9-14, chat 3-4 + stream 2-3, graph 3 + layout 4-6, switch 2-3), consistent with the parity doc's 3-5 (work graph) + 18-26 (chat) once the chat is the bare chat.

Order after the slice (cheapest, most reused first): work graph and item card (`fieldview.js`, 204 lines, 3-5 rounds; reuses list and input) -> released chat panel (18-26, minus 6-8 with D10) -> decisions cockpit (8-12, needs SVG/geometry from the graph) -> home (14-20, windows, selects, ranges) -> field 2D (7-10, canvas) -> boot animation (2-3, or drop) -> field 3D and voice stay JS islands (3-5 each, W7). Shell and login last, with W6.

## Decisions for the operator (none chosen)

1. Slice one on the bare app's chat/graph first, or straight on the released `panel.js`? (recommended: bare first)
2. Page `Fetch`: same-origin only, and which verbs/headers? Is `Fetch` with a stream flag the SSE door, or a separate `Stream` class?
3. Allow-lists to grow: which tags, attributes (`value placeholder disabled type for tabindex data-*`, `aria-*` as a prefix), and is any `style` allowed (none / an allow-list of properties)?
4. Graph drawing: SVG nodes in DOM, a `canvas` draw list (4-6 rounds), or a JS island for layout and draw? Node cap for a pack layout.
5. Snapshot scope: which classes the page follows besides view-declared ones (`Chat:message:*`), and the row cap per class.
6. Constants to keep or change: 20 page / 10 poll / 3000 ms list / 700 ms active / 80 px stick / 90 char shorten / the class colour formula / virtualization window N.
7. Feature flag: fact name, value names, default per view, and whether the `?ui=` URL override stays in the release.
8. Browser store: persisted with W6 (what: filter, mode, draft, zoom) or lost on refresh? Outbox while offline or a visible failure?
9. Budgets: max wasm size (gzipped), time-to-first-frame, JS shell line cap (700 proposed above is a placeholder, not a pick).
10. Accessibility floor: focus as a write, `aria-live` log, roles/labels equal to the old HTML as a release gate, yes/no.
11. Order after the slice and whether the 3D field and voice stay JS islands (parity table says so).
12. Whether D4 and D10 of `docs/release-parity.md` (drop quota popups, drop Blurp/self parts) are answered before slice two starts.
