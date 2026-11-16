# The graph link: joining the seat's graph and the page's graph (slices 1 and 2 built, the rest design; #1143)

Status: slices 1 and 2 are BUILT and in the release; slices 3 to 5 and everything marked "design" are not built, and every number, default, new builtin and new native piece in them is a DECISION for the operator (list at the end; "(D<n>)" points at it). Built: slice 1 (section 15): the page tells the seat what it loaded and what it can do (`Vm:<ns>.caps`, `<ns>::Page:main.report`; `membrane/packs/link.c1f`, `membrane/apps/barest.c1f/link_view.c1f`, `Help:page`), so the model reads `Vm:*` and `Page:main`; slice 2 (sections 15b and 15c): the engine's replicas and subscriptions (`native/src/link.rs`), the seat route `POST /ui/link` (`membrane/packs/link_route.c1f`) and the page glue, with the settings rows and the chat doorbell as the first consumers. Not built: forwarded writes and read-once (slice 3), WebSocket (slice 4), model-authored views (slice 5), placement by imports (section 16).
Where I disagree with the operator's sketch I say so in a line starting "Challenge".

## 0. The problem, as the code has it

The seat and the page run the SAME engine (`native/src/web.rs` is the wasm binding of `vm::Core` + `fields::Fields`) as two separate graphs. The bridge is one-way:
the page fetches ONE snapshot (`GET /ui/fieldvm`, `membrane/packs/door_fieldvm.c1f` `FieldVm.snapshot`; `membrane/apps/barest.c1f/ui/boot.js:36-40` feeds it with
`feed(..., mirror = true)`) and then polls (`Fetch:settings` -> `/ui/settings`, `settings_view.c1f:88`; `/ui/fieldvm/active`, `field_view.c1f:108`;
`/membrane/chat/messages`, `chat_view.c1f:95`). The page already writes the other way through the model's own door: one `POST /mcp` `entity_write` (`settings_view.c1f:12`).
The seat cannot see the page: `Shell:main.load_errors` (`boot.js:39`) and `Conn:page.hidden` (`fieldvm.js` `track_hidden`) exist only in the browser, which is why Memo
reports no UI entities.

## 1. What `membrane::` is today (verified in the tree)

- It is a LITERAL PREFIX that PACKS write into ids. The engine never adds it. There is no namespace table, no per-seat or per-person value: it is the fixed text `membrane::`.
  `grep` of `native/src` finds it only in strings: `fields.rs:49` (`LLM_CALL`), `learn.rs:122` (a candidate id), comments. Packs that write it: `legacy_import.c1f` (`membrane::Legacy:seat`, `membrane::Chat:session[...]`),
  `door_state.c1f:7,22,62` (`membrane::presence`), `default_banner.c1f:4,22,29` (`membrane::Banner:admin:main`), `chat*.c1f` (`membrane::Chat:...`), `surface.json:127,211,524`.
- The engine knows `::` in exactly three places, all about the CLASS, not ownership: `host.rs:53` `class_of_eid` (after `ns::`, up to the next colon: `membrane::Chat:main` is `Chat`), `fields.rs:1595` (the same for a constructor)
  and `infer.rs:350`. The lexer treats `ns::Name` as one entity literal (`expr.rs:281`, `syntax.rs:68` `name_colon`). Ids are exact strings: `membrane::Banner:admin:main` and `Banner:admin:main` are TWO different entities in `Core::docs`.
- "Bare reads back as `membrane::`" is NOT an engine feature. It is a pack hack: `door_tools.c1f:56` `mc_w_id` (and `:203` `mc_r_id` for reads) uses the `membrane::` twin when only that row exists and the bare one does not (docs/cliffvm.md:150).
  `Legacy:seat` read back as `membrane::Legacy:seat` because `legacy_import.c1f:39` WROTE it with the prefix; `instances('Legacy')` returns the eid as stored.
  The `Banner:admin:main` / `membrane::Banner:admin:main` pair is that same bare-twin rule: one entity, two spellings an operator or model may type; `default_banner.c1f:29` creates only the prefixed one.
- `docs/chat-keys.md:19`: "`membrane::` is the namespace the chat packs always wrote; kept." It is a convention of the chat packs, not a design.
- A namespace name is limited by the lexer: `name_char` (`expr.rs:218`) is alphanumeric or `_`. A hyphen (`page-7a`) cannot be written in an unquoted entity literal (`page-7a::Shell:main` would lex as a subtraction). Challenge: name pages `p7a` / `page_7a` (D1).

So the operator's "backend root = `membrane::`" is a CONVENTION promoted to a rule. Cheap, because the text is already in every chat id; but about half the seat's ids are bare (`Fetch:x`, `Setting:...`, `Boot:seat`, `Command:...`), and
these are ALSO the backend's. The rule must be: **an id without `::` belongs to the VM that is reading it; an id with `ns::` belongs to the VM named `ns`.** The backend's two spellings stay two entities; both are owned by the backend.

## 2. Ownership (the operator's point 1 and 2)

**The owner is a pure function of the id: the text before the first `::`, or "me" when there is none.** Challenge: the "walk up the parent chain" is not needed to FIND the owner (the chain of `membrane::Chat:main:message:7` ends at `membrane::Chat`,
which is not an entity: ids' colon parents stop at the class segment). The chain matters for LIFECYCLE, which is where the engine already walks it: gc holds a colon child while its parent lives (`vm.rs:604-612` `held`), retire takes the children (`host.rs:513`).
So the namespace is the root, and the chain gives lifetime:

- Each page is a root `p7a` (D2: who names it). Its root entity is `Vm:p7a` (a backend-side registry row: person, `connected_at`, link state, `seen`, build). Challenge: `Vm:p7a` is NOT an id-ancestor of `p7a::Shell:main`; the link is the name `p7a`, so today's
  gc and retire would not reach `p7a::...` from it. Two small native changes make the root real: (a) in `held` (`vm.rs:611`) the parent of a top-level namespaced id `ns::X:y` is `Vm:ns`; (b) retire of `Vm:ns` also removes ids starting with `ns::` (one more `starts_with` beside `host.rs:513`). Without (b) a closed tab leaves its replicas behind.
- Each VM knows what it owns by the same function and where to send: owner(id) == me -> local; else the link named `owner(id)` (the backend is the only peer a page has; the backend has one link per live page). A page never talks to another page in this design (D3: a page->page path goes through the seat).
- **The wire always qualifies.** Inside a VM its own ids stay bare (the page's packs keep writing `Shell:main`, `DomNode:...`); at the link boundary the driver adds the sender's name to bare ids going out and strips the receiver's own name coming in. The engine needs no concept of "me". Only FOREIGN ids are spelled with their prefix inside a VM
  (`membrane::Setting:chat.header`). A pack that reads a foreign id with a typo of the prefix reads a missing entity, as today.
- **No per-entity owner storage.** An `owner` edge escape hatch is NOT needed for slices 1-4. Moving a node means a new id (that is "id identity includes placement"); the old id may carry a plain `moved_to` property the owner writes. Rejected alternative: an `owner` edge (section 9).
- **How it combines with class ownership by packs (rule A/B, docs/c1f-layers.md:108).** Two independent axes. Placement (which VM holds the authoritative copy) is the id; permission (which PACK of that VM may declare and write a class) is rule A/B inside each engine. Combined rule, NEW and
  load-bearing: **getters and setters run only on the owner's copy.** A replica (a foreign id held as values in this VM) is inert: no getter computes it, no setter fires on it. If both sides computed or reacted they would double every effect and diverge. In the engine: in the class-applies test
  (`fields.rs:51` the getters that apply to an entity; the setter dispatch) skip an id whose owner is not me (D4: a native change, one predicate). Class declarations stay per VM: the page pack declares `Shell`; the seat does not need to know it.
  Rule B does not apply to a forwarded write (an outside write is exempt, c1f-layers.md:107), so the policy of section 7 is the gate.

## 3. Demand-driven working set (point 3)

The engine already has the two halves. DEPENDENCIES: `Fields::deps` (`fields.rs:318`) maps `(eid, prop)` to the getters that read it; each recompute replaces a getter's read list (`fields.rs:916`). A MISSING-BUT-ASKED-FOR read: `Core::wanted` (`vm.rs:203`, filled at `vm.rs:319`/`:344`
when the id is stored but not loaded; the host loads it between rounds, `host.rs:812` `fault_wanted`, and what read it recomputes).

- **First read.** A read of a foreign id that is not in `docs` notes the id (and prop) in a new `Core::foreign_wanted` (a sibling of `wanted`) and returns the reasoned null `pending: <id>` (the same shape `host_request` returns for a slow call, `vm.rs:937`, and `sql()` returns through
  `store.rs:231` `request_job`). It is NOT plain `None` (what `vm.rs:319` returns, because there the load is immediate and the evaluation repeats): the network cannot answer in this evaluation. A getter written for `sql()` already handles it (the `door_tools.c1f` `pending` rows, `Return.state` at `:91`).
  Three distinct null reasons, so a pack can tell them apart: `pending`, `absent: no such entity` (the owner said so), `refused: <reason>` (rights, size, cap). The value arrives later as an outside write by the link driver; the readers recompute through the existing dirty marking.
- **Subscription = the set of (foreign id, prop) that `deps` holds readers for.** The link driver is told, once per round, which foreign keys gained their first reader (sub) and which lost their last (unsub). The unsub hook sits where the read list is replaced (`fields.rs:916`); a recompute that drops and re-adds the same read in one round is a net zero, so
  unsub is decided at the END of the round and after a grace (D5: none, N rounds, or N ms; I pick "end of round + N ms" so a view that flickers between two states does not storm).
  Granularity is per entity with the set of props read (D6): the owner sends only those props. A value that is large (`max_value`, D7) is never sent; the reader gets `refused: too large (N bytes)`.
- **No copy of what nobody reads.** Replicas are RAM-only and unloaded the way lazy rows are (docs/cliffvm.md "Storage modes", "Eager unload"): they count against the page's budget; the host's eviction policy (`evict_under_pressure`) is the one that would apply (the page has none; the page's cap is the subscription cap, D8).
- **Lists and queries cannot be replicas.** `instances('Setting')` over foreign ids is not a thing: `instances` lists only LOADED entities of a lazy class even today (docs/cliffvm.md:160). A remote list is a QUERY EFFECT, exactly like `SqlQuery`: `LinkQuery:<id>.run = {peer, prefix | class, after, limit}`,
  receipt `{id, status, rows: [{id, cls}...], next, total}` (`drivers.rs:138` VERBS, `:178` SqlQuery arm, `fetch.rs:125` for the receipt shape). Paged (`after` is a key, so it is the `(cls, k)` index range of `door_fieldvm.c1f` `fe_kids`, no body read), bounded (`limit` is clamped by the owner, D9),
  never "sync the class". A page that wants a live list holds the query result as an entity and re-asks when a small DOORBELL value it subscribes to changes (the owner's `updated`-style counter of the range, D10). Rows then become subscriptions only for the ids the view actually reads.
- **Chat bodies never replicate by default.** The owner's policy (section 7) is an ALLOW-list of prefixes, default empty besides the explicit rows of slice 1-2; `Chat:` stays out until the operator allows it (D11).

## 4. The protocol (point 4)

**Rounds and batching.** The engine runs in rounds (`Host::drain_apply`, the wasm `drain()` at `web.rs:70`, `fieldvm.js` `flush`). At the end of a round the link driver collects, per peer: new subs, new unsubs, reads-once, forwarded writes, queries. They go out as ONE frame.
Answers come in a later round, as outside writes by the driver. Evaluation never waits; the step budget (`expr.rs:98-113`, steps only, a native call is one step) is untouched, because a foreign read costs one step and returns at once.

**Frame** (JSON, text; the unit of ordering): `{v, n, ack, ops: [...]}`. `n` is the sender's frame counter, `ack` the highest `n` it has taken from the peer. Within one link frames are processed in `n` order; a frame seen twice (a retried POST) is dropped by `n` (idempotence for free).

| op | direction | fields | meaning |
|---|---|---|---|
| `hello` | both | `ns`, `build`, `epoch`, `have` | open: who I am; my boot epoch; per subscribed id the revision I hold (`have`), empty on first join |
| `sub` | client->owner | `id` or `prefix`, `props?`, `limit?` | subscribe; a prefix is bounded by `limit` and REFUSED, not truncated, when more match |
| `unsub` | client->owner | `id` or `prefix` | drop |
| `val` | owner->client | `id`, `r`, `props` or `gone` or `refused` | the current value of a subscribed node; `r` its revision; `gone` = retired; coalesced (latest wins) |
| `read` | client->owner | `id`, `props`, `rid` | read once, no subscription; answered by `val` with `rid` |
| `wr` | either | `wid`, `id`, `prop`, `value` | forwarded write request |
| `res` | either | `wid`, `ok` or `refused: reason` | ack of a `wr`: applied, or the reason; the answer to a repeated `wid` is the first answer |
| `q` / `page` | client->owner / back | `qid`, `prefix`/`class`, `after`, `limit` / `rows`, `next`, `total` | a paged query |
| `resync` | client->owner | `have` | after a reconnect: same as `hello.have`; owner sends only nodes whose revision differs (an `epoch` mismatch = resend all subscribed) |
| `bye` | both | `why` | close; the owner retires `Vm:ns` and its replicas |

- **Revision.** `r` is a per-node counter kept in RAM by the owner, with its boot `epoch`; not stored (the store's `updated` is a float time, `vm.rs:177`, not a strict counter). A restart changes the epoch: every client resubscribes and receives everything it reads, which is the working set, not the graph.
  Alternative: a content hash instead of `r` (no counter, costs a hash of every sent value). D12.
- **Backpressure.** State, not events: the owner keeps ONE pending `val` per (link, node) and sends the latest; a slow client costs bounded memory. At most `window` unacked frames (D13); past it the owner stops sending and coalesces. A link past `max_subs` (D8) gets `refused` on new subs. A frame over `max_frame` bytes (D14) is split by ops.
- **Writes.** `wr` carries a client-chosen `wid`; the owner keeps the last K results by `wid` (D15) so a retried request is not applied twice. Order among writes of one link is `n` order. No optimistic local apply: the client shows the value it gets back in `val`
  (otherwise the replica and the owner diverge on a refusal).
- **Hidden tabs.** `Conn:page.hidden` already exists; a hidden page sends `pause` (a `hello` with `have`) and the owner stops `val`s and keeps only revisions; `resync` on visible. Lease: no frame for `lease_s` (D16) and the owner treats the page as gone (`bye`).

## 5. Determinism and the journal (point 5)

- A `val` is an outside write by the link driver: stamped like a driver receipt (`host.rs:92` `is_outside`; `fields.rs:1319` stamps `driver:<verb>` / `record:<verb>`). A read-once and a query answer are receipts of the effect (like `Fetch`/`SqlQuery`, journalled, `docs/cliffvm.md:280`): a replay applies them and never asks.
- Replicas: not authoritative. At boot nothing is restored; subscribers resubscribe. Whether their `val` writes are JOURNALLED is a real choice (D17): journalled = a replay recomputes the getters that read them (cost: journal growth proportional to page churn);
  not journalled = the precedent of `Mem:host` (`host.rs:59` `SENSOR_WRITER`: "a reading of this process is not history"), replay diverges on getters of replicas. My pick: not journalled for the page's replicas of the seat (the page has no journal anyway), journalled for the seat's replicas of page values the MODEL decided on (slice 1+).
- A forwarded `wr` is applied by the OWNER as an outside write with writer `link:<ns>:<person>` and journalled THERE. The sender journals only the effect request and the `res` receipt.
- Deterministic by construction: the engine never blocks; the arrival order of `val`s is the order of the journal's outside writes.

## 6. Transport (point 6)

The protocol is frames; the transport carries them.
- **First slice, what exists.** Page->seat: a `Fetch` POST (`fieldvm.js:297-330` `ask`: same origin, credentials same-origin, the W2/W3 rules; `Fetch:link:out.request = {url: '/ui/link', method: 'POST', body: <frame>}`). Seat->page: either the reply of that POST (a long poll: the door holds the `Request`, answers when a frame is due or after `hold_s`, D18) or the SSE stream the door already
  has (`Request.stream/send/end`, `http.rs:233-293`; `/event` in `door_session.c1f`) read by the page's `Fetch` with `stream: true` as `Chunk:<fetch>:<n>` (`fetch.rs:2`, `fieldvm.js:330`). Recommendation: long-poll POST first (one mechanism, every frame carries `ack`), SSE as a tail optimisation when latency matters.
  New seat piece: one door route `/ui/link` (c1f in `door_fieldvm.c1f`-style pack, `Request.route` match) and the link driver as a c1f pack over `Fetch`/`Request` entities. No Rust needed for frames; the new Rust is only the engine hooks of sections 2-3 (foreign-wanted, replica-inert, sub/unsub notice, `Vm` gc/retire).
- **WebSocket, later.** Needs: (a) the HTTP upgrade in `http.rs` (today one request per connection, `Connection: close`, `http.rs:3-4`): read `Upgrade: websocket`, answer `101` with `Sec-WebSocket-Accept` = base64(SHA-1(key + GUID)). The tree has SHA-256/HMAC/base64 (`jwt.rs:21,52,74`) but NO SHA-1: about 40 lines
  of hand-rolled SHA-1 or the `sha1` crate. (b) frame parsing: masking, fragmentation, ping/pong, close; ~150 lines hand-rolled, or `tungstenite` (a dependency tree; the project avoids them: `std`-only door, `Cargo.toml`). (c) an owned connection that is not a `Request` (a second kind of held connection next to `Store::open_stream`),
  and a thread per socket like `membrane-http-conn`. (d) the browser side: `fieldvm.js` gets a `WebSocket` driver beside `ask`, and the DOM host's allow-list (W2/W3 refuse everything but same-origin `fetch`, `refuse()`) gains one scheme `ws(s)` same-origin. D19: hand-rolled std (my pick, small, no dependency) vs crate; and whether to build it at all before the long poll shows a latency need.
  The frame format does not change.

## 7. Security (point 7)

- The backend validates EVERY `sub`, `read`, `q` and `wr` with the caller's rights: the person from the door (`door_auth.c1f` `Person:<sub>`, docs/auth.md; the barest seat has no security by decision, so there the check is the policy below only). A page is its person's, and `Vm:p7a.person` is set by the door from the verified token, never from a frame.
- **Policy is c1f data, not a keyword.** An allow-list entity `Link:policy` (prefixes the page may subscribe or read, per class; D11) and, for writes, a verdict entity: an arriving `wr` becomes `LinkWrite:<wid>` {from, id, prop, value}; a getter `LinkWrite.verdict` decides; a setter applies on `accept`.
  Challenge to "which classes accept forwarded writes is a policy decision": do not add a header keyword (an `ACCEPTS` line); keep it as pack code, so the operator can read and edit it (no inventions; "program the field first").
- **Frontend-owned values are advisory.** The backend never decides anything security-relevant from `p7a::...` values (not the person, not rights, not `hidden`); it reads them for display and for Memo's picture. Same for a hostile page: it may write garbage into its own namespace only; `wr` to `membrane::` ids from a page goes through the same verdict as any door write.
- **The model writes page nodes only by `wr`** (open a panel, show a note): `entity_write {spec: 'p7a::Shell:main', prop: 'open', ...}` must route to a `wr` when the id's owner is a live `Vm`. That is a change in `door_tools.c1f` `mc_w_*` (c1f; the id's owner is read from the text), not a new tool (memory: tools are field ops only).
- **Slice 5 (later, not designed): a model-authored VIEW PACK shipped to the page.** A `Pack` flagged for the frontend would be fed to `set_pack` (`boot.js` `loadPacks`). The DOM allow-list (`fieldvm.js:34` `DOM_ALLOW`: no `style`, no `src`, no `srcdoc`, no event-handler attributes) is the sandbox for what it DRAWS; it also needs: Fetch same-origin and verbs/headers limits (already `refuse()`), no access to `localStorage`/cookies (the engine has none), a step budget per evaluation (the engine's), a cap on DOM nodes and pack size, a per-pack list of foreign ids it may subscribe, and a person-visible "this view was written by the model" mark. Limits to decide then (D20).

## 8. Stages (point 8)

**Slice 1: the page's boot report, page -> seat, existing transport (built, section 15).** `Page:main` {packs loaded, load errors, view, build, hidden} owned by `p7a`.
- JS: `boot.js` keeps writing local `Shell:main`; one new c1f page pack `link.c1f` (loaded with the view packs) holds the page's name and, on every change of the report, writes ONE `Fetch:link:out.request = POST /mcp entity_write {spec: 'p7a::Page:main', prop: 'report', value: {...}}` (ONE prop with a dict, so one request per change, the pattern of `settings_view.c1f:12`).
  The page name comes from the snapshot (`door_fieldvm.c1f` `FieldVm.snapshot` adds `page: 'p7a'`, made by the seat from the request number; D2) so a page cannot claim another's.
- c1f seat: a pack declaring `Vm` and `Page` (class `Page`'s mode: stored row per tab is store growth; VIRTUAL means `entity_query` does not list it (docs/cliffvm.md:155-160) but `entity_read` with `prop` and `run_cliff` read the live one; D21).
- Rust: none for the minimum. (If `Vm` is VIRTUAL the `held`/retire hooks of section 2 are needed; for a stored `Vm` a boot sweep like `collector.c1f` suffices.)
- Tests: `tests/specs` pack spec for `Page`/`Vm`; a process test in the style of `native/tests/seat_process.rs`: load `/`, run the page (`node tests/js/page_boot.test.mjs`), assert `p7a::Page:main.report.load_errors` is readable by `entity_read`. Risk: low. Rounds: one per report; a report storm on a hot-reload is the one risk (debounce, D22).
- Settings window: reads `membrane::...` nothing new; it shows the report with `entity_read`.

**Slice 2: subscriptions of backend values into the page (built, section 15c).** Settings feed first (`Setting:<group>.<key>` rows, `settings_view.c1f:88` polls `/ui/settings`).
- Rust (the real work): `Core::foreign_wanted`, replica-inert predicate, sub/unsub notice at round end with the grace, `val` apply as an outside write, `Vm` gc/retire hooks; wasm `Field` gains `drain()` fields `subs`, `unsubs` (`web.rs:70`) and a `feed_val` call.
- JS: `fieldvm.js` collects the notices into ONE frame per flush (`flush()` loop), posts it, feeds `val`s with `feed(..., mirror = true)` (the existing mirror path).
- c1f seat: `/ui/link` route, the owner side (revision counter, coalescing, policy), the `Setting:` prefix allowed.
- Chat history: Challenge: do NOT subscribe it as a replica (a list of bodies); keep the polled `/membrane/chat/messages` and subscribe only `Chat:main.last_turn`-style doorbell values so the page polls when something changed (D10, D11). That removes the poll timer, not the bodies' path.
- Tests: Rust unit tests on the engine hooks (a read creates one sub; two readers one sub; dropping the last reader one unsub; a getter flicker causes none; `pending` -> value recompute), a two-engine in-process test (two `Core`s with a frame pipe), a process test of the page with the settings window. Risk: medium-high (engine hooks, ordering). Rounds: one frame per page round that changed its read set.

**Slice 3: forwarded writes from the model to page nodes.** `door_tools.c1f` routes `entity_write` of a foreign-owned id to a `wr`; the page-side verdict getter (page pack) decides; `res` returns as the tool's reply (the Return entity pattern, docs/cliffvm.md:96: pending until the receipt). Native: none beyond slice 2. Tests: model writes `p7a::Shell:main.open`, the panel opens; a refused class answers with the reason. Risk: medium (a page that is hidden or gone: `refused: page not connected`).

**Slice 4: websocket.** Section 6. Rust: `http.rs` upgrade + frames, a held non-Request connection, SHA-1; JS: `WebSocket` driver and allow-list entry. Tests: a frame round trip, close, ping, a malformed frame (never abort). Risk: medium; the frame layer is the same.

**Slice 5: model-authored views.** Section 7; a design of its own before any code.

## 9. What breaks or gets harder

- **Identity includes placement.** Moving ownership is a new id; references, `$ref` values (`vm.rs` `ref_in`), packs' text and the model's notes hold the old one. Nothing migrates; say so in the help.
- **Cross-graph references.** A `$ref` to a foreign id from a local entity: the gc ref index (`vm.rs:611-617`) treats only local ids; a `$ref` into another VM neither holds nor resolves. Decide: a foreign `$ref` is a plain value that reads as `pending` until subscribed (D23).
- **Two spellings** of one backend entity (`Banner:admin:main` and `membrane::...`): the wire qualifies a bare id with the sender's name, so the page sees `membrane::X` for a bare backend `X` and cannot tell it is the twin of `membrane::X` if both exist. Keep the door's twin rule (`mc_w_id`) as is; add none to the link.
- **Many tabs:** one root and one subscription set each; the seat's cost is O(links x changed subscribed nodes); `max_links` (D8). A reload is a new page id (new root, old one retired by `bye` or `lease_s`); a stable id per browser (localStorage) would reuse the root but two tabs would collide (D2).
- **Hidden tabs and resync storms:** after a seat restart every page resubscribes at once; jitter the resubscribe by a per-page random delay (D24). The replicas are only the working set, so a storm is bounded by subscribed nodes, not the graph.
- **Large values:** `refused: too large`; a chat body, a `Pack.text`, a `Command` output must never arrive by default. **Chat bodies never replicate by default** (policy, D11).
- **Pending flicker:** every foreign read is `pending` on first render; views must render a pending state (they already do for the `sql()`-fed windows).
- **Clocks and `updated`:** the replica's `updated` is the arrival time here, not the owner's (`updated` is VM-provided, `vm.rs:177`); a view that sorts on a foreign `updated` reads the owner's value only if it is sent as a normal property (`owner_updated`, D25).
- **The page's DOM entities** (`DomNode:*`) are page-owned and large in number: never subscribed by the seat unless the policy allows a prefix; the seat's picture of the UI is `Page:main.report` plus named panels, not the DOM.

## 10. Alternatives considered and rejected

- **A per-entity `owner` edge.** Storage and a second source of truth that can disagree with the id; breaks "owner is a pure function of the id" and costs a read to route every write. Rejected; revisit only if a real moved-node case appears.
- **A central sync of everything (one shared graph).** Copies the chat (bodies, 160 KB rows, `chat-keys.md`) into every tab; defeats lazy load and the memory budget (docs/cliffvm.md "Storage modes"); makes a conflict resolution problem out of two engines each running getters and setters. Rejected: it is the opposite of demand-driven.
- **Pull only (GraphQL-like): the page asks for a shape per view, as today with `/ui/settings` and `/ui/fieldvm/active`.** Works, and it is what exists; but the seat cannot push, the page polls on a timer, and dependency tracking (which already exists) is thrown away. Kept as the fallback and for queries (`q`), rejected as the whole design.
- **Optimistic local apply of a forwarded write.** Replica and owner diverge on a refusal; rejected, the echoed `val` is the truth.
- **A hand-written getter-level subscription API** (`subscribe('id')` in c1f): a new builtin; the demand-driven version needs none (a read IS a subscription). Rejected as an invention.

## 11. Traffic budget: the link must not congest a network (requirement A)

Today an idle visible page polls: `/session/status` every `status_ms` = 3000 (`shell_view.c1f:99,158`; the operator measured ~80 KB per answer, about 27 KB/s, about 2.3 GB a day per open tab), plus the history and field polls. The link replaces polling with state subscriptions; the budget is stated per scenario, as FRAMES and BYTES, never time.

| scenario | budget (a decision each, D27) |
|---|---|
| idle page, visible, nothing it reads changes | 1 keepalive frame per `keepalive_s` (30 s), at most 64 bytes each way; zero otherwise |
| idle page, hidden tab | no `val` at all after `pause`; the keepalive only (or none, lease permitting) |
| one subscribed value changes 100 times in a round window | 1 `val` carrying the last value (coalescing); the other 99 are never sent |
| one property of a 20-property node changes | a delta: `val` with that one prop (props are the unit, section 3 decision 6); never the whole node |
| page opens a window reading 30 settings | 1 frame out (30 `sub`), 1 frame back (30 `val`); not 30 round trips |
| a list view (chat history, field window) | one `q`/`page` of at most `limit` rows with ids and classes only, no bodies; the next page only when scrolled |
| chat body, `Pack.text`, `Command` output | 0 bytes by default (policy allow-list; `refused: not replicated`) |
| seat restart, N pages | one `resync` per page, jittered, answering only nodes whose revision differs: bounded by the subscribed working set, not the graph |

Rules that deliver it, all part of the protocol above, none new mechanisms: (1) per-round batching, ONE frame per peer per round, even when it has zero ops it is not sent; (2) last-value-per-node coalescing in the owner's per-link queue (state, not events); (3) property deltas; (4) no bodies unless the policy names the prefix and `max_value` allows the size; (5) paged, bounded lists only; (6) hidden tabs pause (`Conn:page.hidden`, already a fact, `fieldvm.js` `track_hidden`); (7) caps per VM: `max_subs` per link and `max_links` per seat (D8), and a per-link rate cap on frames per second (D27) with `refused: rate` past it.
**Backpressure rule, replacing an unbounded queue:** the owner never queues more than one pending value per (link, node). A subscriber that does not ack within `window` frames (D13) is DROPPED TO A RESYNC: the owner discards its pending set and, when the link drains, sends a snapshot by revisions (only nodes whose `r` differs from the client's `have`). The memory per link is bounded by its subscription count; the bytes are bounded by the working set.
**Tests count, never time** (`native/tests/`, in-process, two engines over a frame pipe like slice 2): the pipe counts frames and bytes per direction; one test per row of the table asserts the number (idle visible 5 minutes of simulated rounds = 0 frames besides keepalives; 100 writes in a round = 1 `val`; one prop of 20 = one prop in the frame; a hidden page = 0 `val`; a slow subscriber = a resync, not a queue longer than its subscription count; a 10,000-row class = pages of `limit`, never one frame). Counters are on the link entity (`Link:<peer>.frames_out`, `bytes_out`, ...) so a pack or Memo can read them. Rounds are driven by the test, no sleeps.

## 12. A UI served from another origin, over the internet (requirement B)

What changes when the wasm engine and view packs come from origin U and the seat is origin S:
- **Cookies stop working.** The admin cookie is `HttpOnly; SameSite=Strict` (`door_auth.c1f:8,23`), so a page from U sends none to S. The page needs a token it can hold: a TOKEN HANDSHAKE. The page obtains a short-lived JWT (minutes, `ttl` D28) by an exchange the person approves on S (a `/ui/login`-like call, `door.c1f:91-108`, or a pairing code the person types on S), keeps it in memory (not localStorage), and presents it as the FIRST FRAME (`hello.token`) on a WebSocket, or as `Authorization: Bearer` on each POST of the long poll. A URL query token is never used (`http.rs` `token_of` already ignores the query).
- **CORS.** `Access-Control-Allow-Origin: <U>` from an allow-list of origins the owner set (D29), `Allow-Headers: authorization, content-type`, no `*` with credentials; the preflight answered by the door (c1f route). The page's `Fetch` refuses any other origin today (`fieldvm.js` `refuse()`: "same origin only"): the allow-list gains exactly the seat's origin, named at boot, nothing else.
- **TLS only.** `https://` and `wss://` only for a non-loopback seat; the door has rustls for OUTBOUND (`fetch.rs`) but not inbound TLS: a seat serving the internet sits behind a TLS-terminating tunnel or relay (the way auth.md already notes a `Secure` cookie "behind an https proxy"), or inbound TLS is a new native piece (D30).
- **The trust problem.** Origin U runs code with the user's token. The engine is sandboxed but the packs and the wasm are U's. Options: (a) U is the seat (today: same origin, no problem); (b) pinned content: the seat publishes a manifest `{file: sha256}` signed with a key the person holds, the page loads wasm and packs by Subresource Integrity (`integrity=` on scripts, hash check on `set_pack` text) and refuses a mismatch; (c) U is trusted for packs but the token it gets is scoped (`ns` claim, section 13) and short-lived so a malicious U can act only as that page. Pick (b)+(c) for a third-party U; keep (a) for the default. Pack signing key custody is a decision (D31). Honest limit: SRI protects a file, not a malicious U that serves a different page; the person must trust the U address.
- **Latency tolerance.** Reads never block (section 3), so a 300 ms RTT shows `pending` rendered as such; writes are not applied locally until `val` returns (optimistic UI is NOT used for forwarded writes, section 4, but a view may show a local "sending" state in its OWN namespace; D32). Frames are batched per round, so RTT is paid once per round, not per read.
- **Reconnect and resync** are the revision protocol of section 4 (`hello.have`, `epoch`), with exponential backoff and jitter (D24) and the token re-presented on the new connection.
- **NAT.** A home seat is usually unreachable. Options: (1) DIRECT through a tunnel (a public name forwarding to the seat; the seat holds TLS or the tunnel does); simple, but the tunnel provider sees plaintext unless it only forwards TCP. (2) A DUMB RELAY: both the seat and the page dial OUT to a relay, which pairs them by a room id and forwards frames. A relay MUST see: the room id, frame sizes, timing, the peer's connection state. It MUST NOT see frame contents or the token's secret: frames are end-to-end authenticated (HMAC over `n` and the ops with a per-link key derived from the JWT exchange or a pairing secret) and, if the relay is not trusted, encrypted (AEAD; a new dependency or hand-rolled, D33). It MUST NOT be able to forge, replay (counter `n` is authenticated) or reorder frames undetected. It may drop. It decides nothing about ownership (the `ns` claim is checked by the seat, not the relay). Precedent: auth.md:253 describes a chat relay that speaks for several people under one token and shows as one person (the relay problem this design must not repeat: the relay carries each person's own token end to end). I found no w89-relay document in this tree; if it exists on another branch, its handshake should be reconciled with this section before building (D34).

## 13. Authorization with the door's JWT (requirement C)

What exists: HS256 tokens signed with the door secret (`jwt.rs:114` `mint`: claims `sub`, `name`, `iat`, `exp`; `:127` `verify`: `exp` and `sub` required, 30 s leeway, returns ALL claims of a valid token); the door takes `Authorization: Bearer`, HTTP Basic password, or the `membrane_jwt` cookie (`http.rs` `token_of`); the owner is the person whose `sub` equals the `operator` fact (`owner.rs`; auth.md); `Person:<sub>` is made by the door at the first verified request (`door_auth.c1f:25`). The barest seat has no secret and no auth (docs/cliffvm.md:7): the link is then unauthenticated and the namespace is assigned by the seat, nothing else.

**How frames are authorized.**
1. A link is BOUND to one verified token. Over the long poll every POST carries `Authorization: Bearer`; over a WebSocket the first frame is `hello.token` and nothing else is processed before it verifies (the connection closes with `refused: auth` after `auth_s` D35). `Request.verify` (a driver verb, `door_auth.c1f`, so a pack never holds the secret) is the one check; a frame is never trusted for who it is from.
2. The person is the token's `sub`. `Vm:<ns>.person` is written by the door from the verified token, never from a frame field. Every `sub`, `read`, `q`, `wr` is checked against that person (section 7).
3. **Expiry mid-connection.** The owner records `exp`. Before it, the client sends `auth {token}` (a re-auth frame; the same `sub` and `ns` required, else `refused: identity changed` and close). A link past `exp` + the verifier's 30 s leeway gets `bye {why: expired}`; its subscriptions are dropped, its `Vm` goes stale (section 15), the client reconnects and `resync`s. Rotation interval is a decision (D28).
4. **THE NAMESPACE CLAIM.** Ownership must be enforced by the token. Rule: **a link may own only ids under its own root namespace, and the server derives that namespace from the verified token, not from the frame.** Any frame whose `ns`, `val` ids, `wr` target as OWNED side, or `hello.ns` names another namespace is refused with `refused: namespace` and counted; three refusals close the link. Options for where the namespace comes from (D36):
   - (a) a custom claim `ns` in the JWT (`{"sub": "esp32-kitchen", "ns": "esp32_kitchen"}`): explicit, a token may even carry no `ns` (read-only peer), the seat chooses it at mint (`cliffvm token mint --ns`; `mint` in `jwt.rs:114` writes only fixed claims, so it must be extended: a small native change; `verify` already returns every claim).
   - (b) derived from `sub` by a fixed function (lower-case, `-` to `_`): no new claim, no mint change; but a person's `sub` (`alice`) and a device's must not collide with a page's `p7a`; and `sub` characters that the lexer rejects (`name_char`, `expr.rs:218`) must be mapped.
   - (c) a PAGE gets `ns` assigned by the seat per connection (`p7a`) in a token the seat mints at the handshake with `ns` and the person's `sub` both; a DEVICE gets a long-lived `ns` fixed at provisioning. (a) with (c) is my pick: one rule, the token says it.
   The namespace is also a BOUNDARY on reads: a link may `sub`/`read` under its own namespace freely; foreign namespaces only by the policy allow-list (section 7). The reserved name `membrane` is never granted by `ns` (only the seat owns it); a second link claiming a live `ns` is refused (`namespace in use`) or replaces the first (D37, a reconnect must replace).
5. Frame size and rate limits are per token (D27), so a stolen device token cannot flood.

## 14. Peers that are not UIs (requirement D; not built now, kept possible)

A peer need not run the VM. It is an owner of a root namespace that can say a few things. **Minimal profile** (what a microcontroller implements; everything else in section 4 is optional for it):

| frame | direction | device must |
|---|---|---|
| `hello` {ns, token} | dev->server | connect and present its JWT (one string held in flash) |
| `val` {id, props} | dev->server | publish values of nodes it owns (sensor readings), unversioned: the SERVER stamps time and revision on arrival |
| `wr` {wid, id, prop, value} | server->dev | accept a forwarded write for an actuator it owns; answer `res` {wid, ok or refused} |
| `sub` / `read` {id} | server->dev | for a node it owns: answer `val`; a device never has to subscribe |
| `ping` / `pong` | both | keepalive; presence |
| `bye` | both | close |

`resync`, revisions, `q`, prefix subscriptions, `unsub` and `pause` are OPTIONAL for a device. A device that does not implement `q` answers `refused: unsupported`.

Requirements on the earlier sections, and what each choice must avoid:
- **No history on the device.** `r` revisions are the SERVER's for the device's nodes (assigned on arrival, section 4); the device keeps at most its current values and the last `wid`. Resync after reconnect is the device re-sending its current values (`hello` then `val`s). A device never needs `have`/`epoch`.
- **No wall clock on the device.** Frames carry no timestamps from a device; the server stamps `updated` (`vm.rs:177`) on arrival. A `val` MAY carry `age_ms` ("this reading is 400 ms old") for a buffered reading; never an absolute time.
- **Encoding.** Frames are defined as a data model (map of fields) with TWO encodings: JSON (UI, debugging) and CBOR (or MessagePack; D38) for devices. Field names get short integer keys in the binary form. Choice to avoid: JSON-only frames, or code that parses frames as text (`fieldvm.js` builds them from objects, so this is free if the frame layer is one encode/decode pair). `max_frame` for a device is small (D14: 1 KB profile).
- **Transport independence.** WebSocket (maps directly; a device with TLS RAM can hold it), MQTT (maps cleanly: topic `link/<ns>/up` for `val`/`res`, `link/<ns>/down` for `wr`/`sub`; the broker is the relay, auth is the JWT in the CONNECT password, the namespace is enforced by topic ACL = the `ns` claim; MQTT has no `n`/`ack` so the frame counter is used for idempotence), CoAP (request/response and Observe map to `read` and `sub`; UDP means `n` is the only ordering), serial and BLE (via a GATEWAY: a small process that speaks the device's framing on one side and the link on the other, holding the device's token or its own with `ns` per device; the gateway MUST NOT widen a device's namespace). A device speaks HTTP POST/long-poll in a pinch; slice 1's transport already covers it. Choice to avoid: making the protocol depend on a held-open duplex socket or on cookies.
- **Cheap JWT.** HS256 verify needs SHA-256 only; the device holds ONE token and presents it; it never verifies anyone (the seat is authenticated by its TLS certificate or the pairing key). Rotation: the server sends `auth {token}` as a frame (a write to the device's own `token` slot); the device stores it. Avoid: cookie-only auth (`door_auth.c1f:8`), tokens that must be refreshed by a browser-style exchange, tokens bigger than the device's RAM (claims stay small: `sub`, `ns`, `exp`).
- **Many devices under one server.** A root namespace per device; subscriptions by namespace prefix from the seat's side (`sub {prefix: 'esp32_kitchen::'}` limited by `limit`); per-token rate limits (D27); `max_links` counts devices separately from pages (D8). Avoid: any design where the page's registry (`Vm:`) is the only kind of root: the root entity class is `Vm` for any peer, with `kind: page|device`.
- **A silent device.** Presence is an edge of its root node: `Vm:<ns>.seen` (server time of the last frame), `link: up|stale|gone`. After `lease_s` (D16) the server marks `link: stale` and the device's SUBTREE is marked STALE, NOT deleted: replicas keep their last value with a read-side marker (`stale: true` on the node, or `Vm.link` readable by getters), so a getter can say "last seen 40 min ago". Deleting happens only on `bye {why: retired}` or an operator action. This is different from a page, whose replicas go with `Vm:<ns>` (section 2); the `held`/retire hook of section 2 must therefore be a per-root policy (`ephemeral` for pages, `durable` for devices), D39.
- **Authority of device values.** A device value is advisory about the WORLD, authoritative about its own readings; the seat's policy for forwarded writes to actuators (section 7) is the safety gate (e.g. a verdict getter with limits), never the device's say-so.

Checks on earlier choices that would close this door: JSON-only frames (fix: one data model, two encodings); WebSocket-only transport (fix: frames independent of the socket, `n`/`ack` in the frame); VM-only peers (fix: the minimal profile above; a peer without getters is a legitimate owner); cookie-only auth (fix: a JWT per link with `ns`); device-supplied time (fix: server stamp); client-side revision history (fix: server-assigned `r`); page-only registry and cascade-delete on root loss (fix: `kind` and per-root retention); `max_frame` sized for pages only (fix: a profile limit in `hello`).

## 15. Slice 1: built (2026-11-14, #1143; the page tells the seat what it is; UI only)

What exists (tests: `native/tests/link_slice1_1143.rs`, `tests/js/link_caps.test.mjs`, `tests/js/link_page.test.mjs`; the model reads `Help:page`):
- **The name.** `GET /ui/fieldvm` carries `page` = `p` + 8 hex digits, made by the seat for each load (`door_fieldvm.c1f` `Request.fv_page`: `sql("SELECT 'p' || lower(hex(randomblob(4)))")`, SQLite's random bytes, no new builtin) and the entity `Link:page {ns, build}` in `entities`, so `boot.js` needs no change to hand it on. 32 random bits: two loads share a name about once in 4 billion pairs; one seat has tens of tabs a day. Four digits (the sketch of D1) would collide by the birthday rule after about 300 stale roots.
- **The page's pack** `membrane/apps/barest.c1f/link_view.c1f` (imports `dom`, so the seat sends it and does not run it; c1f only). Once `Shell:main.open` it writes ONE `POST /mcp entity_write` of `Vm:<ns>.caps` and ONE of `<ns>::Page:main.report`; `report` is written again only when it changes (a getter whose value changes fires the setter: the view in front, hidden/visible, the width class, a load error). No timer, no heartbeat: an idle page sends zero. A write the seat refuses is the text `LinkView:main.note` (not drawn).
- **Capabilities** (the operator's "every node shares its capabilities"): `Host.describe()` in `fieldvm.js` reads them from what the host holds: `protocol` 1, `encodings` ['json'], `frames` (write-request over POST /mcp, poll), `dom` (the tag, svg tag, attribute and prefix lists of `DOM_ALLOW` as data, the refused names, and an FNV-1a hash of them: the version of the list), `provides` (each loaded pack `{pack, major, requires, closure}`: `dom` and `fetch` included, a pack that did not load excluded), `classes` (the engine's own `classes()`), `limits` ('none yet'), `device` (`mobile`/`desktop`), `host_version` (the wasm build key). `boot.js` feeds it as `Host:page` (and the width class, on crossing 700 px) and the link pack tells it. `build` (`cliffvm-<version>`) comes in the snapshot (`Link:page.build`, the seat's `build_id`, the text `/ui/conf` says): no extra GET.
- **The seat** `membrane/packs/link.c1f` (in `barest.packs`): `RESIDENT Vm` (the page's root, one stored row per page; `kind` 'page'), `VIRTUAL Page` (the report is not memory) held by `Vm.page` (a reference, so it is not collected). The setters on `Vm.caps` and `Page.report` stamp `Vm.seen` and `Vm.connected_at` with `clock`: the SEAT's time, no new builtin. `Vm.state` is `connected`, or `stale` after `link_stale_s` (fact, default 120) seconds of silence. Boot retires every `Vm` (the tabs of the last run).
- **Differs from the design:** (1) no `held`/retire native hooks: `Vm` is stored and swept at boot (the decision D21 pick); the `ns::` replicas of section 2 do not exist yet; the Page's lifetime is its Vm's reference. (2) The report is not under `Page:main` of the page's own VM namespace: the page's `Page:main` stays local; the seat's copy is `<ns>::Page:main`. (3) `Vm.state` has no heartbeat to refresh it: a healthy quiet page turns `stale` (read: "not heard"). (4) A page that fails to start before `link_view` runs never reports and has no `Vm` (a `Vm` made at the snapshot would show it; not built). (5) Page `Page:main.report` is readable with a `prop` only (`entity_read` without one reads the store, and a virtual class has no row). (6) `GET /ui/fieldvm` answers with `page` and `Link:page` also for old callers: extra fields only.
- **Hidden classes (the field view is disabled):** `Vm` and `Page` are NOT added to `map_hidden`: a `Vm` is a row the person may want to see on the map once the field view is back; revisit then.

## 15b. Slice 2: engine interface (for the seat route and the JS glue; engine side built in `native/src/link.rs`, `vm.rs`, `fields.rs`, `web.rs`, `host.rs`)

The contract between the engine (this section) and the link owner/transport (`/ui/link`, `fieldvm.js`). The engine never talks to a network: it names what it wants (`subs`/`unsubs`) and takes what arrives (`feed_val*`).

**Foreign namespace rule.** The engine has ONE own namespace, a string told to it: wasm `Field.set_ns("p1a2b3c4d")` (the page calls it once with `Link:page.ns` from the snapshot, before any pack runs); host (seat) `Host::set_ns` (the seat's is `membrane`, set at open). Until it is told, nothing is foreign (the old behaviour). An id is FOREIGN when it contains `::` and the text before the first `::` is not the own namespace. A bare id (no `::`) is local, and so is every id under the own namespace. Inside a VM its own ids stay bare; foreign ids are spelled with their prefix (`membrane::Setting:chat.header`). Names are `[A-Za-z0-9_]+` (lexer).

**Reads of a foreign `<eid>.<prop>`** (a getter, a setter condition, any block):
- no replica value yet: the reasoned null with the exact text `pending: <eid>.<prop>` (for example `pending: membrane::Setting:chat.header.val`);
- a value after `feed_val*` applied it: the value (a JSON null from the owner reads as `absent: <eid>.<prop>`, as any absent property);
- after `feed_gone`: `absent: <eid> does not exist`; after `feed_refused(reason)`: `refused: <reason>` (all props of that entity). A later `feed_val*` makes the entity live again.
- A foreign-id entity the VM holds as an ordinary local entity (written through the door, never fed by `feed_val*`, like the slice-1 `<ns>::Page:main` on the seat) is NOT a replica: it reads normally, runs its getters and setters, and makes no subscription.

**Subscriptions by dependency.** A foreign `(eid, prop)` is SUBSCRIBED while a getter's read list holds it (the same `deps` table), or for the round (plus the grace) in which a setter/block read it. Two readers = one subscription. When nothing reads it any more it is dropped after the GRACE: the end of the first round at engine time `t` at which it is unread starts the grace; it is unsubscribed at the first round end with engine time `>= t + 0.5` (decision 5; `LINK_GRACE_S`). The engine has no wall clock: "time" is the `now` (seconds, a float) the host passes to every round (`write`, `settle`, `tick`, `feed_val*`). A getter that drops and re-reads the same prop inside the grace never unsubscribes. If no round runs after the drop, nothing is dropped: the host must run a round (`settle(now)`) at or after `due` (below).

**Notices: `Field.drain()`** (wasm) returns, next to the existing fields, three more:
```json
{"writes": [...], "calls": [...],
 "subs":   [{"eid": "membrane::Setting:chat.header", "props": ["val", "label"]}],
 "unsubs": [{"eid": "membrane::Setting:chat.old",    "props": ["val"]}],
 "due": 1731600000.5}
```
`subs`/`unsubs`: arrays sorted by `eid`, props sorted, per entity (decision 6). They are DELTAS since the previous `drain()`: `subs` = keys that became subscribed and were not announced yet; `unsubs` = keys announced earlier that are now dropped (a sub and its unsub inside one drain period cancel: neither is sent). `due` = the engine time at which the next unsub becomes decidable (a round at `now >= due` will emit it), or `null`. A reconnect: call `Field.link_reset()`; the next `drain()` announces EVERY live subscription again in `subs` (the `hello`/`resync` set). The host (seat) has the same notices: `Host::link_notices() -> (subs, unsubs, due)` (the same JSON values).

**Applying values.** All apply as an OUTSIDE write of writer `link:val` in ONE round, then the readers recompute (drain shows their derived writes):
- `Field.feed_val(eid, prop, value_json, rev, now)` one prop;
- `Field.feed_vals(eid, props_json, rev, now)` several props of one entity (`props_json` is a JSON object `{prop: value}`; the delta, props not named keep their value);
- `Field.feed_gone(eid, rev, now)`; `Field.feed_refused(eid, reason, rev, now)`.
`rev` is a number (JS number, an integer below 2^53) the OWNER assigns; it is OPAQUE to the engine (never compared, never ordered): the engine only stores the last one applied per entity and gives it back in `Field.replicas()` = `[{"eid": ..., "rev": ...}]` (what the client sends as `have` in `hello`/`resync`). Ordering and duplicates are the link's job (frame `n`). A replica is created by the first `feed_val*`, is class `class_of_eid(eid)` (the text after `::` up to the next colon), is RAM only, is NOT journalled and NOT stored (on the seat as well, decision 17 for the page side; the seat's journalled copy of a page value is a slice-3 question), and is INERT: no getter computes on it, no setter fires on it, a write that lands on it only wakes its readers.

**Retire.** Retiring (`retire = true`, host and wasm `Field.remove`) a `Vm:<ns>` entity retires every entity whose id starts with `<ns>::` too, forgets their replica notes and drops their subscriptions (no unsub notice: the owner is gone). On the seat, a VIRTUAL entity under `<ns>::` is held by a live `Vm:<ns>`.

**Open for the owner side (not engine):** per-node revision counters, coalescing, the allow-list, `max_value`, caps, rates: in c1f (`link_route.c1f`, section 15c). The engine imposes no limit on replicas or subscriptions.

### Slice 2: engine, status

BUILT and tested (`native/tests/link_engine_1143.rs`, 13 tests; full cargo suite green; the wasm in `membrane/apps/barest.c1f/ui/` is rebuilt, deterministic across two builds): everything in 15b, in `native/src/link.rs`, `vm.rs` (`set_ns`, `is_foreign`, `foreign_read`, replicas, `Vm` namespace hold in `held`/`gc_dead`), `fields.rs` (`apply_val`, `link_round`, `link_drain`, `link_reset`, inert replicas in `getters_of`/`chain`/`step`), `web.rs` (`set_ns`, `feed_val*`, `feed_gone`, `feed_refused`, `link_reset`, `replicas`, `drain` with `subs`/`unsubs`/`due`), `host.rs` (`SEAT_NS = "membrane"` set at open, `feed_val`, `link_notices`, `link_reset`, `replica_revs`, retire of `Vm:<ns>` takes `<ns>::`).

Not built in the engine: forwarded writes (a local write onto a replica is not refused yet: slice 3), journalling of seat-side replicas (RAM only), a `Vm` kind/retention policy for devices (D39), `Core::foreign_wanted` as a separate set (the read note `foreign_reads` plays that role).

## 15c. Slice 2: built (2026-11-14, #1143; the seat side `POST /ui/link`, the page glue, the first consumers)

Tests: `native/tests/link_slice2_1143.rs` (a fake client posting frames to a private seat), `tests/specs/link_route.json` (the verdicts), `tests/js/link_client.test.mjs` (the glue on STUBBED hooks and a fake fetch), `tests/js/settings_view.test.mjs` and `chat_view.test.mjs` (the consumers, on a local replica and on the real engine hooks), `tests/js/link_page.test.mjs` (headless Chrome against a real seat, section 7 of the file). The engine side is 15b (w116).

**The route** (`membrane/packs/link_route.c1f`, family `link` of `door.c1f`; in `barest.packs` after `link`). `POST /ui/link`, one JSON frame in, one JSON reply out; a tokenless request is the door's 401, a GET is a 405, a body over `link_max_frame` (262144 characters) is a 413, a frame without `ns` (`p` + 8 hex digits) and `n` (an integer from 1) is a 400.
- Frame: `{v: 1, ns, n, ack, hidden, epoch, resync?, subs: [[eid, prop], ...], unsubs: [[eid, prop], ...]}`. The ids are as the page's engine spells them: `membrane::Setting:x` is the seat's `Setting:x` (the leading `membrane::` is stripped; any other `ns::` is refused).
- Reply: `{v: 1, epoch, n, resync, reset, vals: [{id, prop, value} | {id, refused}]}`, or `{dup: true}` (a frame seen before), `{superseded: true}` (a held poll released by a newer frame), `{refused: 'too many links ...'}`. `n` is the seat's frame counter: ONLY a reply that carries values takes a new number; the page's `ack` is the highest it applied. The frame number that carried a value is its revision for the page (`feed_vals` `rev`).
- THE LONG POLL (decision 18): a frame with news (subs, unsubs, a change of `hidden`, `resync`, the page's first frame, or hidden) is answered at once; an EMPTY frame is the poll, held until a value is due, until `link_hold_s` (25) is over (an empty reply of about 60 bytes: the keepalive), or until a newer frame of the same page releases it (`superseded`). No WebSocket.
- THE OWNER SIDE is state, not events. Per page one `LinkPeer:<ns>` (VIRTUAL, RAM, held by `Vm:<ns>.peer`), per subscription one colon child `LinkPeer:<ns>:k_<id>~~<prop>` (same class, told apart by `sid`): `val` is a GETTER over the entity named (so the dependency engine IS the subscription, read only when the verdict is yes), `ver` counts its changes, `sig` (= ver and verdict) is compared with `ackd` (what the page acked): the sub is DUE while they differ. So: 20 changes between two polls are ONE value, the last (coalescing, tested); a reply the page never took is sent again (nothing is acked); the memory per link is its subscriptions. A page `link_window` (8) frames behind gets `resync: true` and every subscription once instead of a queue.
- POLICY (decision 11) is data: `LinkAllow:<name>` {prefixes} entities (seeded: `LinkAllow:setting` = `Setting:` and `LinkAllow:doorbell` = `Doorbell:`; the operator or a model writes more). `Chat:` is never served whatever the list says (a refusal with that reason); an id not under a listed prefix is `refused: not replicated (... allow-list)`; another namespace is refused. Every refusal is a row `{id, refused: reason}` and the engine reads it as `refused: <reason>` (a reasoned null), never a silent null.
- CAPS (decision 8): 200 subscriptions a link (the 201st and later come back `refused`, not queued), 16 links a seat (the 17th is told `refused: too many links`), 65536 characters a value (`refused: too large (N characters, the most is 65536)`, the value is never sent), 500 replicas a page (enforced by the page glue: the 501st entity is `feed_refused`). `Boot` facts override each number: `link_hold_s`, `link_lease_s`, `link_lease_hidden_s`, `link_window`, `link_max_subs`, `link_max_links`, `link_max_value`, `link_max_frame`.
- LEASE (decision 16): a page silent past `link_lease_s` (60; `link_lease_hidden_s` 600 once it said it is hidden) is gone: its link row and subscriptions are collected, its `Vm` stays and reads `stale`. `Vm.state` is now `connected` while the page talks (its frames are its heartbeat; this replaces the slice-1 `link_stale_s` guess of 120 s, which no longer exists) and `Vm.hidden` is read from the frames.
- RESET: a frame with `n` above 1 from a page the seat does not know (a restart, an expired lease) is answered `reset: true` and the page announces everything again (`link_reset`).
- THE DOORBELL (decision 10): `Doorbell:chat.at` (`packs/chat.c1f`, one stored row) is the time of the last message posted or turn change; it is not under `Chat:`.

**The page glue** (`membrane/apps/barest.c1f/ui/`). The EXACT host changes: (1) `fieldvm.js`: the class `Link` (about 110 lines: queue of the engine's `subs`/`unsubs`, ONE frame per flush by a microtask, `fetch` of `/ui/link`, `feed_vals` / `feed_refused`, ack, epoch, jitter 0-3 s growing to 30 s on a failed POST, hidden pause, the 500-replica cap), `Host.set_ns(ns)`, `Host.start_link()`, one line in `Host.flush()` (hand the drain's `subs`/`unsubs` to it) and one in `track_hidden()`; `describe()` lists the `link` frame and the limits; (2) `boot.js`: `host.set_ns(snap.page)` right after the engine starts and `host.start_link()` after the clock. Nothing else. The host stores the link in `Host.graph` (`Host.link()` is the older DOM link). `Link:page.up` (page-local) is true while the last request was answered and the engine has the hooks; the views read it. With an engine that has no hooks the link does not start and `up` is never set.

**The first consumers** (c1f only). `settings_view.c1f`: `SetRow.live` reads `membrane::Setting:<id>.current` while `Link:page.up`; `SetRow.cur` is that, else the last polled value, so another client's write shows in the open window with no request (the route `/ui/settings` stays the first read, the fallback and the answer of a Save). `chat_view.c1f`: while the link is up the view reads `membrane::Doorbell:chat.at` and fetches the history route ONLY when it changed (or right after a send/stop); the 700 ms / 3 s timers are the fallback when the link is down.

**Traffic, counted** (real browser): the window's 26 rows are announced in one or two frames; an idle page sends only the keepalive (a bare frame under 200 bytes per hold) and reads nothing else of its own; a hidden page sends one frame and nothing more, then one frame with `resync` and the value that changed meanwhile; a seat restart is found again within the jitter and ONE frame announces everything.

**Differs from the design above:** (1) NO `have` / `replicas()` in `resync`: the page's `ack` says which frames it applied, the seat compares it with the frame number each value went out in, and a seat that does not know the page says `reset`; per-prop rather than per-entity revisions. (2) `reads` (read-once) and `wr` writes are not in the frame yet (slice 3). (3) The value size is counted in characters of its JSON text, not bytes. (4) The page's reconnect is the same frame sent again (the seat drops a duplicate by `n`), not a `hello`. (5) The hidden page's `pause` is the `hidden` field of an ordinary frame. (6) `due` of the engine (the grace) is not used by a timer of its own: the page's clock (100 ms) runs the rounds that decide an unsub. (7) The per-link frame RATE cap (decision 27) and the per-token limits are not built. (8) A value that is `null` for the owner arrives as `value: null` (the engine reads it as `absent`), the seat does not tell an absent entity from a null prop. (9) A reply over `link_max_frame` is split BY VALUES, conservatively: what is owed goes whole when (how many values) times (the biggest) fits in 262144, else in parts of as many values as the biggest one allows (at least one, at most 32); the rest stays due and the page's next poll takes it at once (test: six 60,000-character values arrive 4 + 2). Many small values next to one big one therefore take more round trips than they need. (10) The doorbell and the `Setting:` rows are the only things served; `LinkAllow` is how more are added. (11) `Vm.seen` is NOT written on every frame (that would be a journalled store write per page per hold): the frame time lives on the RAM row `LinkPeer.heard` and `Vm.last` reads the later of it and the slice-1 stamps; the state and the lease read `Vm.last`. A page's hidden/visible comes from its FRAMES (`Vm.hidden`), not from the slice-1 report. (12) The write-id memory (decision 15, 256 a link) is not built: there are no writes in slice 2. (13) The allow-list seed is written once (`LinkAllow:seed.done`): an entity the operator retires stays retired.

**Not built:** `reads` and `wr` frame ops (slice 3), write-id memory, the frame rate cap (decision 27), CBOR, a reply split smarter than by values, a stress run of 16 real pages.

## 16. Placement by imports (design, not built)

The capability list of section 15 is the input: each VM reports `provides` (the packs it runs, `{pack, major}`) and, per pack, `requires` (its IMPORT lines) and `closure` (all it needs, transitively). The function is `eligible(closure, {vm: provides})` in `fieldvm.js` (tested over the real packs in `tests/js/link_caps.test.mjs`).
- **Eligibility.** A file may run on a VM whose `provides` holds every pack of its closure (same major). A pack importing `dom` is a view pack by definition: only a VM providing `dom` runs it. A pack importing `sql`, `fs` or `proc` is backend-only. A PURE-LIBRARY pack (imports only libraries every VM provides, or none, e.g. a markdown parser) is eligible everywhere; the DEFAULT is the backend unless it imports something only a page provides, and it MAY be loaded on every VM that needs it (instances do not clash: one owner per entity, by namespace, section 2).
- **Granularity is the FILE.** Every definition in one `.c1f` (class declarations, getters, setters) runs on the VM that satisfies the file's closure; the classes' entities are owned by that VM's namespace. A view pack reads BACKEND values by id as inert replicas (computed and pushed by the owner, section 3), so it needs no import of backend packs.
- **A file whose imports no single VM provides** (`dom` + `sql`) is a PLACEMENT ERROR, refused loudly at load/placement: `eligible` returns `{vms: [], conflict: {seat: ['dom'], page: ['sql']}}` and the message names the conflicting imports and says "split this file". The operator picks this; the silent rule-level split (section 17) is only the later optimization.
- **An app FOLDER `<name>.c1f/` is the unit of distribution; each `.c1f` file in it is placed independently** by its own closure. Today `barest.c1f/` mixes seat-side files (`collector`, `default_banner`, `help`, `unload`) and page-side files (`shell_view`, `chat_view`, `settings_view`, `link_view`). Non-pack DATA files (`ui/*`) are static assets for the page: served, never evaluated. A folder with ONE mixed-import file fails to load THAT file (loud, "split this file"); the others load normally.
- **Layer order is kept per VM**: each VM composes the files placed on it in the same app order, later layers override earlier ones, and privilege by position (docs/c1f-layers.md) applies per VM. NOTHING shipped to a page is privileged.
- **The hand-written places placement would replace:** `membrane/packs/door_fieldvm.c1f:10` and `:32`/`:46` (`FvFile.view` and `FieldVm.pairs`: "a pack is a page pack when its text has the line `IMPORT dom`", the list of what the seat sends); `native/src/host.rs:341` (a seat load error that mentions `dom` is dropped: the way a page pack is skipped on the seat today); `membrane/apps/barest.c1f/ui/boot.js` `loadPacks` (`:20`) (the retry-until-fixed-point order of packs that import each other); and `membrane/apps/barest.packs` (a hand list of seat packs, in load order).
- **Caveats for shipping a model-written pack into a browser:** the pack's SOURCE is visible to the browser and the person: no secrets, no seat-private logic in any file that imports `dom`. It is unprivileged there and runs inside the page engine's limits only: the DOM allow-list (`DOM_ALLOW`: no `style`, `src`, event handlers), same-origin `Fetch` with the page's cookie (a pack that can `POST /mcp` has the person's whole power over the seat: this is the trust boundary and must be a decision before slice 5), the step budget, no `localStorage`. Pack size, DOM node count and the foreign ids it may subscribe need caps (D20).

## 17. Splitting a pack at rule level (a later optimization; DOC ONLY)

The VM could split a pack by RULE (getter/setter) instead of by pack: rules that read page-only inputs (events, scroll, viewport) or write page-only outputs (`DomNode`) must run on the page; the rest may run on the server. A thin SENSING layer (events become small values going up) and an ARTICULATING layer (values become DOM).
1. **Eligibility** from the static read/write sets per rule, which the engine already has: `Fields::deps` (`fields.rs:318`), the levels of `infer.rs`, and class -> owning pack -> `provides`. A rule is page-bound if it reads a class only the page provides or writes one (`DomNode`, `Conn`); server-bound if it reads or writes `sql`, `fs`, `proc`, a store entity or needs the model; free otherwise.
2. **The cut rule.** Hard constraint first (capability). Then the NARROWEST edge of the dataflow, the one with the least data crossing. Sensing one value deep is cheap (a click, a text); articulating one value deep is NOT (hundreds of `DomNode` writes for one raw text). So the page gets the raw message text once and renders markdown and layout locally; the seat keeps state, decisions and anything that needs the model, files or the store.
3. **A latency hint.** A rule in an interaction loop (typing, drag, scroll) must stay on the page. Inferred from reading an event, or a header annotation (a decision: inference only, or an annotation too).
4. **What changes semantically.** A chain that crosses the link spans several rounds; rules that rely on "already written this round" ordering may differ. The equivalence test: run split and unsplit on recorded inputs (the journal) and compare final values.
5. **The cost.** Extra round trips: fine for coarse events, bad for fine-grained ones; the traffic budget of section 11 applies to each cut.
6. **Why placement stays at PACK granularity (section 16) for slices 1-3:** a file is a reviewable unit, a refusal names it, the operator splits it with an editor; a rule-level cut needs the dataflow analysis, the equivalence test and measured traffic to choose cuts. It is decided later from measured traffic, not now.

## DECISIONS for the operator (nothing is chosen silently; my pick in brackets)

1. Page namespace text: no hyphens (`name_char`, `expr.rs:218`); `p<hex>` or `page_<n>`? [`p` + 4 hex, so `p7a`]
2. Who names the page and how long it lives: seat-assigned in the snapshot (cannot be spoofed) vs page-generated; one name per tab load vs per browser. [seat-assigned, per tab load]
3. Page-to-page traffic: none (through the seat only). [none]
4. The engine change "getters/setters do not run on a foreign-owned replica" (a predicate beside `fields.rs:51`): approve as a NATIVE change. [yes]
5. Unsubscribe timing: at the end of the round, with a grace of N ms. N = ? [500 ms]
6. Subscription granularity: per entity with the props read vs whole entity. [per entity, props named]
7. `max_value` bytes of one replicated value. [65536]
8. Caps: `max_subs` per link, `max_links` per seat, a page's replica count. [200 / 16 / 500]
9. A query's `limit` ceiling and default page. [200 / 50]
10. The doorbell value for a range (a per-range counter the owner exposes) vs polling a count. [doorbell]
11. The allow-list policy default: empty, with `Setting:` allowed in slice 2; `Chat:` never until allowed. [as stated]
12. Revision: RAM counter + boot epoch vs content hash. [counter + epoch]
13. Unacked-frame window. [8]
14. `max_frame` bytes. [262144]
15. How many `wid` results the owner remembers for idempotence. [256 per link]
16. Lease (`lease_s`) after which a silent page is gone; a hidden page's lease. [60 s / 600 s]
17. Replica `val` writes: journalled or not (per class?). [not journalled for the page's replicas; journalled for the seat's replicas of page values]
18. Long-poll hold time `hold_s`. [25 s]
19. WebSocket: hand-rolled in `http.rs` (SHA-1 + frames, no dependency) vs a crate; build at all before long-poll shows need. [hand-rolled, only after a measured need]
20. Slice 5 limits (node cap, pack size cap, allowed foreign ids, a visible mark). [decide when slice 5 is designed]
21. `Page` / `Vm` storage mode: RESIDENT-stored vs VIRTUAL with a registry. [VIRTUAL `Page`, stored `Vm`, swept at boot]
22. Debounce of the boot report. [none in slice 1; one POST per change]
23. A foreign `$ref` value: plain value that reads `pending`, no gc holding. [yes]
24. Resubscribe jitter after a seat restart. [0 to 3 s random]
25. Whether a foreign entity's `updated` is the owner's (sent as a property) or the arrival time. [owner's, sent as `owner_updated`]
26. NEW BUILTINS / VERBS / NATIVE PIECES, for approval as a group: (a) engine: `Core::foreign_wanted` + `pending`/`absent`/`refused` reasoned nulls; (b) engine: replica-inert predicate; (c) engine: sub/unsub notice at round end; (d) `held`/retire hooks for `Vm:<ns>`; (e) verbs `Link.send`-style effects:
   `LinkQuery.run`, `LinkWrite` (a VERBS entry beside `drivers.rs:138`) or only `Fetch` + c1f; (f) wasm `drain()` carries `subs`/`unsubs`; (g) `/ui/link` route (c1f) and later a WebSocket server in `http.rs`. [(a)-(d), (f) yes; (e) only `Fetch` + c1f in slices 1-3; (g) route yes, websocket later]
27. Traffic budget numbers (table in section 11): keepalive period, per-link frame rate cap and per-token size limits. [30 s keepalive, 20 frames/s, 4 KB/s sustained per device token]
28. Handshake token life and re-auth interval for a remote UI. [10 min token, re-auth at 80% of life]
29. CORS: the origin allow-list lives in a Boot fact; empty = same origin only. [empty default]
30. Inbound TLS: terminate outside (tunnel or relay) vs a native rustls server in the door. [outside]
31. Pack and wasm integrity for a third-party UI origin: SRI plus a signed manifest, key custody (the person's key, not the seat's secret). [signed manifest, person's key]
32. Optimistic UI: only in the page's own namespace ("sending" state), never for forwarded writes. [as stated]
33. Relay confidentiality: authenticate frames only (HMAC) vs encrypt (AEAD, a new dependency or hand-rolled). [authenticate always; encrypt if the relay is not the operator's]
34. Reconcile with the HA relay design (w89-relay; not found in this tree) before building a relay.
35. Seconds a connection may stay unauthenticated before it is closed. [5 s]
36. Where the namespace claim comes from: custom `ns` claim (needs `mint` to write it) vs derived from `sub` vs seat-assigned per connection. [custom `ns`; seat-assigned for pages, fixed at provisioning for devices]
37. A second link claiming a live namespace: refuse vs replace the first. [replace, after the old one fails its next keepalive]
38. Binary encoding for devices: CBOR vs MessagePack. [CBOR]
39. Per-root retention: pages ephemeral (replicas go with the root), devices durable (marked stale, not deleted). [as stated]
40. (slice 1) The page name's length: `p` + 8 hex digits (32 bits). [8]
41. (slice 1) `link_stale_s`: seconds of silence before `Vm.state` says `stale` (no heartbeat yet). [120]
42. (slice 1) The width class breakpoint, `narrow` below it. [700 px]
43. (slice 1) A tab that never reports leaves no `Vm`; make `Vm` at the snapshot instead? [no]
44. (slice 1) Where a refused report is shown to the person (today: `LinkView:main.note`, not drawn). [draw it in the shell error line]
45. Placement: a file with mixed imports is a loud placement error (section 16), the rule-level split later. [yes]
