# Help:brain - answering a person when you are the brain

When the owner has given the chat to a brain program (the minds setting `chat` names its namespace), a person's message starts no turn and no model is asked for it. The message is one more change in the log you are sent, and you answer it yourself, with a write.

## Tell a message from other changes

A change line that names `Chat:main:message:<id>` (or `Chat:session:<sid>:message:<id>` for a session) is a person speaking. The line points at the change and does not carry the words: read the message to see them.

- `entity_read {spec: "Chat:main:message:<id>", prop: "text"}` is what the person wrote.
- The conversation so far is the other `ChatMessage` entities (`entity_query {q: "kind:ChatMessage", props: ["text"]}`): search them before you say you do not know something.

## Answer

One write of the whole answer finishes the turn, and the page shows it as a finished turn:

`entity_write {spec: "Chat:main:message:<id>:turn:response", prop: "text", value: "<the answer>"}`

- Write the answer once, complete. There is no streaming: the page shows nothing until this write.
- The answer goes to the message it answers: use that message's own id, and for a session message the session's key.
- A change that is not a person speaking needs no answer. Do not write a response for it.
- While you work, the turn shows that the brain has it. If you are not connected, the person is told so in plain words.

## What you owe, and how to resolve it

The seat keeps the list for you: `Owed:<your namespace>`, property `items`, read it with `entity_read {spec: "Owed:<ns>", prop: "items"}`. It holds, oldest first, one `{message, response}` for every turn you have taken and not finished (`message` is the ChatMessage, `response` its answer entity), and `[]` when you owe nothing. Read it instead of working out your duties from the log, and again after any step that failed.

A turn leaves the list when it is finished. Resolve each item with a write to its `response` entity, one of two:
- Answer: write `text` (above).
- Decline: write `declined` with the reason, `entity_write {spec: "<the response eid>", prop: "declined", value: "<why you do not answer>"}`. The turn finishes as done and the person sees `Declined: <reason>`.

A turn the person stops, or one that errors, leaves the list too.

## Reaching outside the graph while you answer

Fetching a page, running a program or reading a file are writes to `Fetch`, `Command` and `File` entities (`Help:c1f`). A person asking for it is the permission: write the request, read the result, then answer with what you found. A block of the language you want run once goes through the `run_cliff` tool (`Help:cliff`).
