Full text of every tool's description (the tool list carries the dense form of the same). One section per tool.

## query
Read membrane's long-term memory: the nearest past exchanges for a topic (situation-response pairs with similarity, answered-model and seat provenance). Read-only, no model call, never learned. Use before redoing known work, when past-session context would help, or to check what the field already learned.

## state
Membrane's own live state: the entity field's vitals (live entities, events by stream, entities by tier, conflicts), the replay A/B ledger, and the last night pass. Read-only, computed from the running proxy. Use for memory-health checks. With `section`, just that part of the seat's WHERE WE ARE: tracks, commands (your methods, most called first), interfaces, classes (class proposals), loops, timelines, frame, digest, or changes (what moved since your last message).

## intend
Plant YOUR commitment (the steering wheel of membrane's idle mind): create-or-pin a shared Goal + pin it on this seat's active set. Open intentions STEER the dream loop (quarantined timeline branches grow toward them, think-ahead extends them) and ride recall. An intention is never auto-closed: fulfillment is explicit (the `fulfill` tool with its evidence, or the operator's `membrane: fulfill #N` -- reality's signature; a similarity flag is only a 'maybe-done?' hint). Use when working toward a multi-session goal or when the field's work has a direction worth keeping.
A METHOD: `call method='intend' args={...}`. args: text* (string), ttl (string) (* required).

## fulfill
Close an OPEN goal you have PROVEN done: its status flips to fulfilled on the shared board (every seat's active set drops it). Requires evidence -- the receipt (a method call's rc=0 output, the entity state you read back). Never close on intent or a plan; an unfinished goal stays open and you take its next concrete step instead.
A METHOD: `call method='fulfill' args={...}`. args: spec* (string), evidence* (string) (* required).

## answer_card
PASS ON A DECISION CARD'S ANSWER the person gave you in this chat, so they need not answer again on the board. Write `relay = {value, quote}` on the card (`entity_write` on the Decision): `quote` is their own words, word for word; the seat checks that a message of theirs since the card was made holds it (case and punctuation aside). Then read `answer_result` back: `{ok: true, value}` when the card is decided, or `{ok: false, error}` (your words were not theirs, the value does not fit the card's variable, or the card is already decided); a refused quote leaves `relay_result`. Only for words they really said about this card; if they said nothing clear, ask. The quote stays on the card and in the operator ledger. Chat lane only; you cannot write the answer itself.

## intents
Read YOUR intent board: the seat's active set steered goals (the dream loop's steering coordinates), open + closed. Use before planting a duplicate or to review what commitments are alive.
A METHOD: `call method='intents' args={...}`. args: none (* required).

## learn
Write ONE durable earned lesson into the seat's memory: `Memory:seat.learn = {lesson: '<the lesson, in prose>'}` (`entity_write` on the node). A lesson is a note, not a transcript: no secrets, no personal data, no fenced code, at most 2000 characters; the field's own guards (dedup, the variety gate) may decline it. Read the answer back: `learn_result` ({written: false, reason} when refused) and `note_result` (which note it became). Use it when you EARNED something durable (a fixed quirk, the command that worked, why a gate exists).

## forget
Delete memories: write `Memory:seat.forget = {spec: '#3-7, 9'}` (`entity_write` on the node). Index form ('#3-7, 9' or '12', at most 12) DELETES those events; the report lands in `cut_result`. A wordier spec only lists the nearest events in `near_result` and never deletes. A refusal is in `forget_result`. After deleting, prefer learning the correction.

## entity_watch
Plant a TRACK: an entity's live state in the WHERE WE ARE header every turn. An optional boundary ('enter N' / 'exit N') fires once per crossing: into the banner (owner '') or into a listener's next turn (owner 'watch:<eid>'). Keys: 'files:router.py', 'models:<name>', 'symbols:<qualname>'. To watch the field instead: field_watch (a topic region), or entity_edit watch_ops / watch_fields (relational / motion) -- one shared attention budget.
A METHOD: `call method='entity_watch' args={...}`. args: key* (string), boundary (string), label (string), owner (string) (* required).

## entity_unwatch
Remove entity tracks: exact key ('files:router.py') or a kind prefix ('files:' removes all file tracks).
A METHOD: `call method='entity_unwatch' args={...}`. args: key* (string) (* required).

## field_watch
WATCH THE FIELD (a topic region): plant a probe on a topic; every NEW memory landing inside its radius is reported to you on your next turn. The radius EARNS its size (a watch that swallows too much collapses; one that swallows everything or starves goes dormant). The board holds 7, inside ONE attention budget shared with watch_ops and watch_fields: at the limit the plant is REFUSED with the list of live watches (sense, what, owner, age, hits) -- drop the ones you no longer need, then plant. Your agent is stamped as each watch's owner. action: plant (topic) | drop ('#N', a label fragment, or 'all') | list.
A METHOD: `call method='field_watch' args={...}`. args: action* (string), topic (string), spec (string), owner (string) (* required).

## attention
THE ALERT DIAL: how many watch events per window may wake you as their own interrupt turn; the rest fold into the 'watch digest' lines of your next WHERE WE ARE block (grouped per entity). It is the entity `Attention:seat`: write `Attention:seat.dial = {budget: 0..50, window_min: 1..1440, mode: 'interrupt'|'digest'}` (only the fields you give change; mode 'digest' folds everything but goal events), then read `dial_result` back: {ok: true} or the error. Read budget, window_min and mode on the entity. Turn it down when alerts eat your turns, up when you're missing things.

## entity_tracks
List entity tracks: key, label, owner, boundary, current inside/outside state, and pending alert count. LANE-AWARE: a listener passes its own agent name to see ITS tracks; the operator sees all (with owners). Use before planting a duplicate track or to review your watches.
A METHOD: `call method='entity_tracks' args={...}`. args: owner (string) (* required).

## entities_related
Read the entity graph: entities related to a free-text query (named hits first, then semantic nearness), each with its mention count and co-occurrence neighbors. Use to explore what the field knows about a component before acting on it.

## entity_query
The entity graph's QUERY LANGUAGE: enumerate/list/count with a one-line grammar. Terms: 'kind:<k|*>' (the eid's kind: f/s/m/c/tool/...), 'name~:<prefix>', 'parent:<spec>' (the ISA ancestor chain includes spec), 'children:<spec>' (the reverse walk), 'status:<s>', 'near:<text>' (shares its words, best first; lists run newest first). '&' conjoins, '|' disjoins. count=true renders 'N entity(ies)'. Examples: 'kind:tool:* & status:open', 'children:membrane::c:deploy'

## entity_add
Add an entity -- a decision, failure mode, component or goal -- with a one-line summary; the same name updates it. A COMMAND entity (class Tool + a `command` prop) composes with its ISA parents (entity_inherit) into a pipeline: the parents run first and feed the child through {{parent.head}} / {{parent.tail}} ({{parent:<eid>.head}} names one). Audited.

## entity_edit
Edit an entity (eid or exact name); only the given fields change. merged_into marks a subsumption (the source stays, pointing at its survivor). Behaviour is a .c1f pack (the pack tool, help('packs')), never a script property; `class` may be any class a loaded pack declares. To act, entity_write the property whose write is the effect (chat:<person>.say, membrane::llm.call, a File or Command verb). publish=True exports it (state only).

## entity_remove
Remove an entity (noise cleanup): resolve by eid or exact name. Ambiguous fragments remove nothing. A live method, or a class that owns methods, is refused unless force=true (a forced class removal takes its methods with it).

## junk_cut
Tune the junk cut: how aggressively machinery (raw tool output -- ps/lsof tables, test bars, log lines, config dumps) is kept out of YOUR banner stories and watch triggers. Per-track (raise for a paranoid trace, lower for a permissive one), 'all' tracks at once, or the global default. The screen is anchor-vector based: junk embeds near anchor entities stored IN the field; prose stays far. CUT 0..1 -- higher = stricter. Audited to the operator.
A METHOD: `call method='junk_cut' args={...}`. args: spec* (string), cut* (number) (* required).

## peer_post
Post to the PEER CHANNEL: a chat lane between the LLMs, visible to the person on every connected seat. For cross-seat coordination: handoffs, questions to another seat's model, announcements. Write `Peer:channel.post = {text, reply_to}` (a bare string is the text; at most 2000 characters; reply_to is a seat id), then read `post_result` / `post_rc` back. Unread messages ride your banner.

## graph
The entity graph's subgraph reader (the model's eyes on the universe map): entities related to a query -- named hits first, then centroid-nearest -- as nodes (id/class/label/position/status/curiosity/fn_counts/properties) + edges (ISA/links/merged_into). The same data contract the universe viewer renders; other clients consume the same endpoints.

## entity_inherit
ENTITY INHERITANCE: give an entity a parent concept ('a letter points to letters'). The child inherits the parent's constraints (latch level, the `living` flag) down the ISA chain; the child's geometry sits at the parent's position plus its own offset. Cycles are rejected.

## pack
THE FIELD'S PROGRAMS: a .c1f pack gives a class its behaviour -- getters that keep a value true, setters that react to a write (help('packs')). action list | show (name) | load (name, c1f: the whole text, `PACK <name> <n>` first): a load runs at once and the reply carries its load error (a broken pack is refused; the last good one keeps running). Shipped packs (banner, mcp, ...) are the release's: extend one from your own pack (`IMPORT banner 1`, your own class).

## entity_write
ACT BY WRITING: set one property of an entity; the write runs whatever reacts to it -- a pack's setters, or an adapter's effect (chat:<person>.say speaks to them, membrane::llm.call wakes a model turn, McpTool:<server>:<tool>.call:run calls a tool, a File's write / Command's run). An effect's receipt lands on the entity as <prop>_result: read it back.

## method_define
Give a class or object a METHOD once, then call it on any object of that kind (method_call, or method_each over every member). command: a shell template, {{arg}} for a call's args and {{self.<prop>}} for the object's (on='light', method='dim', command='curl ... {{self.entity_id}} {{level}}'); or steps, a composite run in order. guard: a python check first (result = {'ok': bool, 'why': str}). A missing owner becomes a class; re-defining updates. Owner only.

## method_call
CLIFFHANGER CALL: run a method on an object -- an entity or a class. Dispatch walks the object, then its ISA classes, then the globals (help('cliffhanger')). PROGRAM THE FIELD FIRST: define the method (method_define), then call it here (code itself runs with code_run); a call straight at the executor (tool:executor, remote_executor or an implementation's exec) is refused. E.g. object='presence', method='restart' restarts the seat. The receipt (rc, outcome, head) lands on the object.

## code_run
RUN CODE (#614): the code is parsed into a tree of nodes in the field (one entity per node, shared wherever the same code appears) and its root is linked to an executor of its language: -execute-> while owed, -executing-> while it runs, then -result-> a result entity for this run. lang='sh' (default) runs on tool:executor, lang='js' in an opted-in browser tab (presence). Re-run a root with node=<its id> (each run keeps its own result); entity_read on a root shows its code, state and runs. Returns the root, the result, rc and the output.

## bash
Run a shell command once: code_run under the name you know (the command becomes a tree in the field, the run a result entity; re-run with code_run node=<root>). What you will run again is a METHOD: method_define it on the entity it belongs to (its args as {{params}}), then method_call it -- program the field first.

## method_each
CLIFFHANGER EACH: call one method on every member of a class (over='light') or a name pattern (over='ha:light.*'). dry_run lists who it would reach without running. Stops at the first failure unless on_fail='continue'. Owner only.

## method_inspect
CLIFFHANGER INSPECT: which method a call on an object would run (its owner, the resolution order, its params and template) without running it. Read-only.

## entity_read
READ an entity's value after processing: its pack getters' current values with its properties (run=true reads through any cache). The properties blob is clipped at ~800 chars: read one in full with prop= (tail=N pages a list from its newest end; lines=A-B pages text, such as a run's kept output).
