# Help:brain - answering a person when you are the brain

> You have one tool, `run_cliff`, unless your seat lists more: read each `entity_query`, `entity_read` and `entity_write` below as the block `Help:cliff` gives ("If `run_cliff` is the only tool you have").

When the owner has given the chat to a brain program (the minds setting `chat` names its namespace), a person's message starts no turn and no model is asked for it. The message is one more change in the log you are sent, and you answer it yourself, with a write.

## Tell a message from other changes

A change line that names `Chat:main:message:<id>` (or `Chat:session:<sid>:message:<id>` for a session) is a person speaking. The line points at the change and does not carry the words: read the message to see them.

- `run_cliff {code: "${'<message id>'}['text']"}` is what the person wrote (`<message id>` is the id as `entity_query` lists it; the seat's own prefix, the part before `::`, may be left off).
- The conversation so far is the other `ChatMessage` entities (`entity_query {q: "kind:ChatMessage"}` lists them, and one `run_cliff` reads the text of many: `${['<id1>', '<id2>']}['text']`): search them before you say you do not know something.

## Answer

One write of the whole answer finishes the turn, and the page shows it as a finished turn:

`entity_write {spec: "Chat:main:message:<id>:turn:response", prop: "text", value: "<the answer>"}`

- Write the answer once, complete. There is no streaming: the page shows nothing until this write.
- The answer goes to the message it answers: use that message's own id, and for a session message the session's key.
- A change that is not a person speaking needs no answer. Do not write a response for it.
- While you work, the turn shows that the brain has it. If you are not connected, the person is told so in plain words.

## Resolve a turn: answer or decline

Every message turn the brain has taken stays `running` until you write to its answer entity, one of two:
- Answer: write `text` (above).
- Decline: write `declined` with the reason, `entity_write {spec: "Chat:main:message:<id>:turn:response", prop: "declined", value: "<why you do not answer>"}`. The turn finishes as done and the person sees `Declined: <reason>`.

A turn the person stops, or one that errors, ends by itself.

## Your banner

The banner is your own self-configurable notes: `Banner:admin:main` (its `text`) and the other `Banner:*` entities (their `words`). Keep what the next step needs there, and remove what is outdated: you read it again at the next change (`Help:banner`).

## Reaching outside the graph while you answer

Fetching a page, running a program or reading a file are writes to `Fetch`, `Command` and `File` entities (`Help:c1f`). A person asking for it is the permission: write the request, read the result, then answer with what you found. A block of the language you want run once goes through the `run_cliff` tool (`Help:cliff`).
