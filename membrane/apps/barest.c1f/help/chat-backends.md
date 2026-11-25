# Help:chat-backends - which program answers a message, and how its turn goes

Every message a person sends is answered by one BACKEND, a program the seat starts (or a peer it asks) for that one message. Whichever backend it is, the turn goes through the same four steps and ends in the same shape, so you read every turn the same way.

## Which backend

The seat picks the backend from the chosen model, first match wins. `run_cliff {code: "${'<message id>'}['backend']"}` names it for a message.

- `brain`: the owner gave the chat to a brain program (the minds setting `chat` names it). It answers by writing the response itself, whatever model is chosen (`Help:brain`).
- `none`: no model is chosen. Nothing starts, and the answer tells the person how to choose one.
- `peer`: the model is answered by a peer program over shared memory (a `cloud_models` entry whose `backend` is `membrama`, `claude-peer`, `openai-peer` or `anthropic-peer`, or a model a peer publishes while its link is up: the names on its node `<ns>::Models:main`; `Help:peers`).
- `oai`: the model is answered over an OpenAI-compatible endpoint (a `cloud_models` entry with any other `backend`, or a model the local Ollama lists).
- `claude`: every other model, answered by the claude CLI.

## The four steps of a turn

The turn of a message is `Chat:main:message:<id>:turn` (`Chat:session:<sid>:message:<id>:turn` in a session).

1. **Plan.** The backend checks what it needs (a binary, a key, an endpoint, a peer that is up). The message's `plan` is `{backend, start}` when it is ready, or `{backend, refuse}` with the reason. A refused turn ends at once as an `error`, and its answer is that reason in plain words, so the person knows what to fix.
2. **Start.** The turn's `state` becomes `running`, `started` is the time it began and `backend` records which backend runs it.
3. **Live progress.** While the turn runs, the message's `live` is `{text, tools}`: the words said so far and the names of the tools started. The claude CLI and an OpenAI-compatible endpoint stream them as they come. A peer and a brain send the whole answer at the end, so their `live` stays empty until the turn is over.
4. **Finish.** The backend writes one value, the turn's `outcome`, and the seat writes the rest from it: `ended`, `error` (the reason, when it failed), the numbers (`usage`, `cost`, `calls`, `duration`), the answer `<turn>:response` (`text`, `error`), one `<turn>:tool:<call id>` per tool call (`name`, `input`, `result`), and last the `state`: `done`, or `error` when it failed.

## Read a turn

- Which backend ran it and where it is: the turn's `backend`, `state`, `started` and `ended`.
- What was said: the response's `text` (`run_cliff {code: "${'<message id>:turn:response'}['text']"}`); a reply cut short keeps the words said before the failure and is marked `interrupted`, with the `reason`.
- Why it failed: the turn's `error`.
- What it did: `entity_query {q: "name~:<message id>:turn:tool:"}` lists them; `run_cliff` reads `name`, `input` and `result` of each (`${ids}['result']`).

## Example: which backend runs a turn

One clifflet reads the turn's record of its backend and its state:

```cliff
-- given Chat:demo:message:7:turn {"kind": "turn", "backend": "oai", "state": "running"}
t = 'Chat:demo:message:7:turn'
{'backend': ${t}['backend'], 'state': ${t}['state']}
-- value {"backend": "oai", "state": "running"}
```
