# The chat turn over membrama (design, in progress: branch `chat-membrama-design`; nothing here is built yet)

membrama (github.com/chedim/membrama) is a GraphLink peer that runs a local model on llama.cpp for the seat. This page says how a chat turn would be answered by it: a rung of the turn beside `chat_oai.c1f` (any OpenAI-compatible endpoint) and the claude rung, selected by the model's entry, with the same tool loop. membrama's own design is its `docs/design.md`; the protocol under both is [graph-link.md](graph-link.md).

## Choosing the rung

A model is a `cloud_models` entry (`chat_oai.c1f` header). An entry with `backend: "membrama"` selects this rung: `{name, backend: "membrama", peer, max_new, enable_thinking?}`. `name` is the model name shown to the person; `peer` is the name of the peer in the fact `peers`; **`max_new` is required and has no default** (it is the owner's number; a request without it is an error reply). `chat_oai.c1f` takes every entry whose backend is not `claude-agent`, so it must also leave `membrama` alone.

## The peer (the owner declares it, in the fact `peers`)

`{name: "membrama", ns: "membrama", ring_bytes: 65536, slots: 512, key_cap: 128, val_cap: 65536, spins, depth, run: {argv: [membrama serve ...]}}`, with the same numbers on the `membrama serve` command line. The owner chose 512 slots, key cap 128 and value cap 65,536 on 2026-11-18 (about 34 MB per table: four chats in flight and the retired cells waiting for acknowledgement). The value size must be above the largest message or tool spec: a value the seat's table cannot store is a `Refused` cell and the reply is an error that names it.

## One round

A round is what `chat_oai` calls one model call. Instead of a `Fetch`:
1. **Tools:** the door's `tools/list` is turned into OpenAI function specs and written once as shared nodes `membrane::LlmTool:door:<i>` (`spec`); they are rewritten only when the list changes, and never retired. (Each is 3 cells; written per round they would be 12 more cells per round, and the rendered prompt would not be the same across rounds.)
2. **Messages:** one node per message, `membrane::LlmMsg:<cp>-<n>:<i>` with `role`, `content` (a string) and, for the assistant's message of an earlier round, `tool_calls` (OpenAI shape).
3. **Request:** `membrane::LlmRequest:<cp>-<n>` with `server`, `messages` (the ids), `tools` (the shared ids), `settings` (`max_new`, `enable_thinking`). The request is written last.
4. **Reply:** `membrama::Reply:<cp>-<n>` is read (the seat subscribes on the first read of a foreign node and gets the value on later reads): `state` (running, done, error, aborted), `chunks`, `finish` (`stop` or `tool_calls`), `tool_calls` (`[{name, arguments}]`), `usage`, `error`. The text is in `membrama::Chunk:<cp>-<n>:<k>`; the page's partial text reads the chunks as they grow.
5. **Tool calls** become `ChatCall` entities exactly as in `chat_oai`: the door's own `tools/call`, results as `ChatTool` entities. The arguments arrive as an object (not JSON text); the next round's assistant message carries them as the object.
6. **Retire:** when the round's reply has been read (and its tool calls have been made), the request and its message nodes are retired. Their cells become `Gone`; membrama sees that, retires the reply and its chunks, and the slots come back once each side has acknowledged (graphlink `docs/shared-memory.md`, 4b). Nothing here deletes anything by hand.

Cells for a round with two messages and four tools: 6 for the request and 4 per message (the own cell, `role`, `content`, `summary` the seat adds), plus the shared tools once (3 each). Measured on a scratch seat: 26 cells in all for that round; 7 on membrama's side.

## Errors, interruption, the link

- A reply in state `error` fails the turn with membrama's cause. A reply that does not arrive because the peer is down fails it with "membrama is not up" (the `Vm:membrama` node says `link: gone`).
- Interrupting the turn writes `abort` on the request node; membrama stops at its next piece of text and answers `aborted`.
- Banks (entity parts) are not used by this rung: the prompt is one text, rendered with the model's own template. The prefix reuse across rounds still applies.

## What would be tested

A scratch seat (its own port and folder, never a person's seat) with the real cliffvm release and membrama: the same prompt through this rung and through `chat_oai` (an OpenAI-compatible server on the same model), a turn with a tool call and a second round, an abort, and a request whose value is too big for the table. Parity means the same answer for the same prompt with greedy decoding, and the same tool calls.

## Not decided or not built

Everything on this page. Open: where `enable_thinking` comes from when the entry does not say; what the page shows while membrama loads the model; whether a second model needs a second peer.
