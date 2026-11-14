# The graph link: joining the seat's graph and the page's graph (design, 2026-11-14, #1143)

DESIGN ONLY. Nothing here is built; every number, default, new builtin and new native piece is a DECISION for the operator (list at the end; "(D<n>)" points at it).
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
  `door_state.c1f:7,22,62` (`membrane::presence`), `default_banner.c1f:4,22,29` (`membrane::Banner:owner:main`), `chat*.c1f` (`membrane::Chat:...`), `surface.json:127,211,524`.
- The engine knows `::` in exactly three places, all about the CLASS, not ownership: `host.rs:53` `class_of_eid` (after `ns::`, up to the next colon: `membrane::Chat:main` is `Chat`), `fields.rs:1595` (the same for a constructor)
  and `infer.rs:350`. The lexer treats `ns::Name` as one entity literal (`expr.rs:281`, `syntax.rs:68` `name_colon`). Ids are exact strings: `membrane::Banner:owner:main` and `Banner:owner:main` are TWO different entities in `Core::docs`.
- "Bare reads back as `membrane::`" is NOT an engine feature. It is a pack hack: `door_tools.c1f:56` `mc_w_id` (and `:203` `mc_r_id` for reads) uses the `membrane::` twin when only that row exists and the bare one does not (docs/cliffvm.md:150).
  `Legacy:seat` read back as `membrane::Legacy:seat` because `legacy_import.c1f:39` WROTE it with the prefix; `instances('Legacy')` returns the eid as stored.
  The `Banner:owner:main` / `membrane::Banner:owner:main` pair is that same bare-twin rule: one entity, two spellings an operator or model may type; `default_banner.c1f:29` creates only the prefixed one.
- `docs/chat-keys.md:19`: "`membrane::` is the namespace the chat packs always wrote; kept." It is history from the Python seat (`membrane::pack:<name>`, docs/cliffvm.md:358), not a design.
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
- **No copy of what nobody reads.** Replicas are RAM-only and unloaded the way lazy rows are (docs/cliffvm.md "Storage modes", "Eager unload"): they count against the page's budget; the policy of `unload.c1f` would apply if the page had a Mem sensor (it does not; the page's cap is the subscription cap, D8).
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

**Slice 1: the page's boot report, page -> seat, existing transport.** `Page:main` {packs loaded, load errors, view, build, hidden} owned by `p7a`.
- JS: `boot.js` keeps writing local `Shell:main`; one new c1f page pack `link.c1f` (loaded with the view packs) holds the page's name and, on every change of the report, writes ONE `Fetch:link:out.request = POST /mcp entity_write {spec: 'p7a::Page:main', prop: 'report', value: {...}}` (ONE prop with a dict, so one request per change, the pattern of `settings_view.c1f:12`).
  The page name comes from the snapshot (`door_fieldvm.c1f` `FieldVm.snapshot` adds `page: 'p7a'`, made by the seat from the request number; D2) so a page cannot claim another's.
- c1f seat: a pack declaring `Vm` and `Page` (class `Page`'s mode: stored row per tab is store growth; VIRTUAL means `entity_query` does not list it (docs/cliffvm.md:155-160) but `entity_read` with `prop` and `run_cliff` read the live one; D21).
- Rust: none for the minimum. (If `Vm` is VIRTUAL the `held`/retire hooks of section 2 are needed; for a stored `Vm` a boot sweep like `collector.c1f` suffices.)
- Tests: `tests/specs` pack spec for `Page`/`Vm`; a process test in the style of `native/tests/seat_process.rs`: load `/`, run the page (`node tests/js/page_boot.test.mjs`), assert `p7a::Page:main.report.load_errors` is readable by `entity_read`. Risk: low. Rounds: one per report; a report storm on a hot-reload is the one risk (debounce, D22).
- Settings window: reads `membrane::...` nothing new; it shows the report with `entity_read`.

**Slice 2: subscriptions of backend values into the page.** Settings feed first (`Setting:<group>.<key>` rows, `settings_view.c1f:88` polls `/ui/settings`).
- Rust (the real work): `Core::foreign_wanted`, replica-inert predicate, sub/unsub notice at round end with the grace, `val` apply as an outside write, `Vm` gc/retire hooks; wasm `Field` gains `drain()` fields `subs`, `unsubs` (`web.rs:70`) and a `feed_val` call.
- JS: `fieldvm.js` collects the notices into ONE frame per flush (`flush()` loop), posts it, feeds `val`s with `feed(..., mirror = true)` (the existing mirror path).
- c1f seat: `/ui/link` route, the owner side (revision counter, coalescing, policy), the `Setting:` prefix allowed.
- Chat history: Challenge: do NOT subscribe it as a replica (a list of bodies); keep the polled `/membrane/chat/messages` and subscribe only `Chat:main.last_turn`-style doorbell values so the page polls when something changed (D10, D11). That removes the poll timer, not the bodies' path.
- Tests: Rust unit tests on the engine hooks (a read creates one sub; two readers one sub; dropping the last reader one unsub; a getter flicker causes none; `pending` -> value recompute), a two-engine in-process test (two `Core`s with a frame pipe), a process test of the page with the settings window. Risk: medium-high (engine hooks, ordering). Rounds: one frame per page round that changed its read set.

**Slice 3: forwarded writes from the model to page nodes.** `door_tools.c1f` routes `entity_write` of a foreign-owned id to a `wr`; the page-side verdict getter (page pack) decides; `res` returns as the tool's reply (the Return entity pattern, docs/cliffvm.md:96: pending until the receipt). Native: none beyond slice 2. Tests: model writes `p7a::Shell:main.open`, the panel opens; a refused class answers with the reason. Risk: medium (a page that is hidden or gone: `refused: page not connected`).

**Slice 4: websocket.** Section 6. Rust: `http.rs` upgrade + frames, a held non-Request connection, SHA-1; JS: `WebSocket` driver and allow-list entry. Tests: a frame round trip, close, ping, a malformed frame (never abort, docs/crash-covenant.md). Risk: medium; the frame layer is the same.

**Slice 5: model-authored views.** Section 7; a design of its own before any code.

## 9. What breaks or gets harder

- **Identity includes placement.** Moving ownership is a new id; references, `$ref` values (`vm.rs` `ref_in`), packs' text and the model's notes hold the old one. Nothing migrates; say so in the help.
- **Cross-graph references.** A `$ref` to a foreign id from a local entity: the gc ref index (`vm.rs:611-617`) treats only local ids; a `$ref` into another VM neither holds nor resolves. Decide: a foreign `$ref` is a plain value that reads as `pending` until subscribed (D23).
- **Two spellings** of one backend entity (`Banner:owner:main` and `membrane::...`): the wire qualifies a bare id with the sender's name, so the page sees `membrane::X` for a bare backend `X` and cannot tell it is the twin of `membrane::X` if both exist. Keep the door's twin rule (`mc_w_id`) as is; add none to the link.
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
