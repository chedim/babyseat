# The chat turn over a peer: membrama, a Claude peer, an OpenAI-compatible peer or a Claude API peer (built in `packs/chat_membrama.c1f`; what is not built is listed at the end)

The rung is generic: it serves any peer that speaks the contract below and reads the peer's namespace from the model's entry (`ns`), never a fixed one. Four backends select it: `membrama` (the local model peer), `claude-peer` (a Claude Code connector program, `graphlink-claude`, in the graphlink repo), `openai-peer` (`graphlink-openai`: any OpenAI-compatible chat completions server) and `anthropic-peer` (`graphlink-anthropic`: the Claude API, called directly): the same contract, so the seat writes the nodes, the peer answers `<ns>::Reply:<rid>` and `<ns>::Chunk:<rid>:<k>`, the seat runs the tool calls and feeds them back in the next round). Wherever this page says `membrama::Reply` or `membrama::Chunk`, the rung reads `<ns>::Reply` and `<ns>::Chunk`; for a `membrama` entry `ns` is `membrama`. The errors the turn says name the entry's `ns` ("membrama is not up", "claude is not up").

membrama (github.com/chedim/membrama) is a GraphLink peer that runs a local model on llama.cpp for the seat. This page says how a chat turn is answered by it: a rung of the turn beside `chat_oai.c1f` (any OpenAI-compatible endpoint) and the claude rung, selected by the model's entry, with the same tool loop. membrama's own design is its `docs/design.md`; the protocol under both is [graph-link.md](graph-link.md).

**The brain instead of a rung:** when `Minds:seat.chat` names a brain peer, no rung runs at all and the peer answers by writing (graph-link.md, section 19).

## Choosing the rung

A model is a `cloud_models` entry (`chat_oai.c1f` header). An entry with `backend: "membrama"` selects this rung: `{name, backend: "membrama", peer, ns, max_new, enable_thinking?}`. `name` is the model name shown to the person; `peer` is the name of the peer in the fact `peers`; `ns` is the namespace it owns (the node `Vm:<ns>` says whether its link is up); **`max_new` is required and has no default** (it is the owner's number; an entry without it, or without `peer` or `ns`, refuses the turn and says what to add). `chat_oai.c1f` leaves `membrama`, `claude-peer`, `openai-peer` and `anthropic-peer` entries alone.

An entry with `backend: "claude-peer"` is `{name, backend: "claude-peer", peer, ns}`: `peer` and `ns` are required (an entry without them refuses the turn and says so), and `max_new` and `enable_thinking` are not read: the request's `settings` is empty, so nothing is invented for Claude. The usage of the turn is what the peer's Reply says in `usage` (`{read, loaded, generated}`, read the same way for both backends); a Reply with no `usage` leaves the turn's usage unset.

An entry with `backend: "anthropic-peer"` is the same shape (`{name, backend: "anthropic-peer", peer, ns}`, no `max_new`: the Claude API requires `max_tokens` on every call and that peer takes it as the owner's launch option, so the seat writes none). An entry with `backend: "openai-peer"` is `{name, backend: "openai-peer", peer, ns, max_new?}`: `max_new` is optional there, and when the entry has it the request's `settings` carries `{max_new}`, which the peer sends as `max_tokens`; with none, none is sent and the server chooses. A refusal for a missing `peer` or `ns` says which kind of peer the model is.

**A cut answer is said:** when a peer's Reply has `finish` `length` (the model reached its limit on tokens) or `content_filter` (the model server's filter stopped it), the turn's answer is what the model wrote followed by a line saying it was cut, so a partial answer is never shown as complete. Any other finish with text is a complete answer, as before.

## The peer (the owner declares it, in the fact `peers`)

`{name: "membrama", ns: "membrama", ring_bytes: 65536, slots: 512, key_cap: 128, val_cap: 65536, spins, depth, run: {argv: [membrama serve ...]}}`, with the same numbers on the `membrama serve` command line. The owner chose 512 slots, key cap 128 and value cap 65,536 on 2026-11-18 (about 34 MB per table: four chats in flight and the retired cells waiting for acknowledgement). The value size must be above the largest message or tool spec: a value the seat's table cannot store is a `Refused` cell and the reply is an error that names it.

## One round

A round is what `chat_oai` calls one model call. Instead of a `Fetch`:
1. **Tools:** the door's `tools/list` is turned into OpenAI function specs and written once as shared nodes `membrane::LlmTool:door:<i>` (`spec`); they are rewritten only when the list changes, and never retired. (Each is 3 cells; written per round they would be 12 more cells per round, and the rendered prompt would not be the same across rounds.)
2. **Messages:** one node per message for the whole turn (a later round holds the same messages and more, and writes only the new ones), `membrane::LlmMsg:<cp>:<i>` with `role`, `content` (a string) and, for the assistant's message of an earlier round, `tool_calls` (OpenAI shape).
3. **Request:** `membrane::LlmRequest:<cp>-<n>` with `server`, `messages` (the ids), `tools` (the shared ids), `settings` (`max_new`, `enable_thinking`). The request is written last.
4. **Reply:** `membrama::Reply:<cp>-<n>` is read (the seat subscribes on the first read of a foreign node and gets the value on later reads): `state` (running, done, error, aborted), `chunks`, `finish` (`stop` or `tool_calls`), `tool_calls` (`[{name, arguments}]`), `usage`, `error`. The text is in `membrama::Chunk:<cp>-<n>:<k>`; the page's partial text reads the chunks as they grow.
5. **Tool calls** become `ChatCall` entities exactly as in `chat_oai`: the door's own `tools/call`, results as `ChatTool` entities. The arguments arrive as an object (not JSON text); the next round's assistant message carries them as the object.
The nodes of a round (`LlmMsg`, `LlmRequest`, `LlmTool`) are `VIRTUAL` classes: they are the transport's, never stored and never journalled, so a restart finds none of them and publishes nothing for a turn that is over. A virtual entity lives while a live entity holds it by reference, and the round (`MbRound.holds`) holds its messages, its request and the shared tools. `entity_query` does not list a virtual class: a test reads a node by its id.

6. **Retire:** every round records the ids of its request and message nodes on the turn (`mb_nodes`). When the **turn** ends they are all retired (not at the end of each round: a round's tool calls are made from what its reply said, and the reply is read from membrama's cells, which go with the request). Their cells become `Gone`; membrama sees that, retires the reply and its chunks, and the slots come back once each side has acknowledged (graphlink `docs/shared-memory.md`, 4b). The shared tool nodes stay.

Cells for a round with two messages and four tools: 6 for the request and 4 per message (the own cell, `role`, `content`, `summary` the seat adds), plus the shared tools once (3 each). Measured on a scratch seat: 26 cells in all for that round; 7 on membrama's side.

## Errors, interruption, the link

- A reply in state `error` fails the turn with membrama's cause.
- **A reply cell the peer refuses, or says is gone**, fails the turn with the peer's reason (the class of `why` of the null the read gives, `Refused` or `Absent`, and its `text`). Tested without a model, the peer played by the test (`peers_1143.rs`, `a_reply_cell_the_peer_refuses...`, `a_reply_cell_the_peer_says_is_gone...`).
- **The peer's link gone** (`Vm:<ns>` says `link: gone`) while a round has no final reply fails the turn with "membrama is not up". A peer that never came up has no `Vm` node yet and is not detected: the turn waits (no timeout is invented here).
- **Stopping** (the page's Stop, the abort route, which marks the message `aborted`): the round fails the turn as stopped, and `abort` is written on the open requests so membrama stops at its next piece of text. The test checks that the turn ends stopped; that membrama really stops mid-answer is not asserted over the real model.
- Banks (entity parts) are not used by this rung: the prompt is one text, rendered with the model's own template. The prefix reuse across rounds still applies.

## What is tested

Without a model (the peer played by the test, `seat-tests/tests/chat_peer_1143.rs`): a `claude-peer` entry in the namespace `claude` with no `max_new`: a plain turn (the request's `settings` is empty), a tool-call turn (finish `tool_calls`, the seat runs the door's `entity_read`, round 2 answers), and a peer that goes away fails the turn ("claude is not up"). The same file covers `openai-peer` (the entry's `max_new` becomes the request's `settings.max_new`, none when the entry has none; a tool-call turn) and `anthropic-peer` (empty `settings` even if the entry names a `max_new`; a tool-call turn), an entry without `peer` or `ns` for either (refused, the message names both), and a finish of `length` or `content_filter` (the answer keeps its text and says it was cut). No real Claude, OpenAI-compatible or Claude API peer has been tested against this rung yet.

Gated tests (`seat-tests/tests/chat_membrama_1143.rs`; `MEMBRAMA_BIN` and `MEMBRAMA_TEST_MODEL` name the membrama program and a Qwen3 file, and without them the tests say they were skipped): a plain turn (the model answers 4), a turn with a real tool call through the door and an answer from its result, the retirement of the requests and messages at the end of the turn with the shared tools staying, a turn whose peer is gone, and a stopped turn. Each runs on a scratch seat (its own port and folder). The comparison with `chat_oai` on the same model is not automated: the OpenAI-compatible endpoint (Ollama) does not decode greedily, so only a loose check ("the answer contains 4") can be shared.

## Not built or not decided

- **Streaming:** the page gets the answer at the end of a round, not as it arrives (the partial text from the chunks is not wired).
- **A peer that never comes up** (no `Vm` node) is not a failure of the turn; it waits.
- **A reply of more than four chunks** (about 262,000 bytes with the owner's 65,536 value size) is read as its first four.
- **`enable_thinking`** comes from the entry only; the model's `<think>` text is not separated from the answer when it is on.
- **A second model** needs a second peer and a second entry.
