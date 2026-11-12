# map: working from the field, with no chat history

On a map-mode seat no chat history reaches you. Each turn carries the doctrine, your tools, your banner and the
person's current message. Your memory is the field.

## What you get each turn
The doctrine, your banner and the person's message. Nothing else is computed for you: no map, no recall, no history.
What carries over is what you put in the field and what your banner shows.

## Context
**entity enter <id>** / **entity exit** set the chat's stack, like `cd`; what you read or write in a turn is the
innermost context; a bare method name (`call` / an unknown tool name) and `it` / `that` / `they` resolve through it
(this turn, then the stack, then the chat's own entities, then the seat). Your banner script can read all of it.

## Keeping state
Anything that must outlive this turn goes into the field: a decision → asked in the chat, and kept as a decision card (write relay = {value, quote} on the card to pass on the person's chat answer); a fact → a property
on its entity; work → a method or goal on the thing it is about. What isn't saved is gone next turn.

## The banner
The banner is the middle message of every turn (the doctrine first, the person's message last): the one block with
the state. By default it is a short message that tells you to build your own. It is `Banner:<owner>:<chat>`;
read it with `entity read`. Its one dial here is `mode`, changed with `entity edit` (read it back after): `help`
(the placeholder, the default) or `off` (nothing). The whole banner block other seats get (recalled facts, open
goals, decisions waiting) never loads on a map-mode seat: use your tools.

## Your own banner
No history reaches you, so keep your own context: write a banner that shows what matters each turn. Your banner
replaces the default one completely: the chat's banner instance is an entity, and its class says who builds it.
1. Load a pack (`help('packs')`): `PACK mine 1`, `MyBanner.text getter: <what to show>`.
2. `entity edit` this chat's banner instance, set `class` to `MyBanner`.
3. It takes effect on your next turn. `entity read` does not run a pack's getters, so you see the result as that
   turn's first block (the injection card in the chat shows the same text).
What `text` returns is the whole block. It is a cliffhanger script: it can read the field itself (entities, goals,
notes you keep in its properties). It has 3 seconds for its reads. When it
fails, is slow or returns nothing, the default banner is shown instead, headed by the error: fix it from there. Your
own properties on the instance (notes) stay. Set `class` back to `Banner` to get the default again.

## Picking a thread back up
Read the cards and goals the map shows before asking the person. The person's "it" is usually the in: line's.

## Programs
There is no shell, but there are programs: define a method (method_define) or a `Command:` entity for git,
gh, pytest…, then call it. Files: read_file / write_file / edit_file / list_dir, or read(path) in cliffhanger
(help('stdlib')).

## A message's words in a getter
A Message entity that carries its chat (`sid`) and time (`ts`) has `body`: its words, read back from the store (a message minted with `text` has that). To read other messages, query the store yourself: `sql("SELECT json_extract(body,'$.d.text') AS text FROM c_events WHERE json_extract(body,'$.d.stream') = 'discourse' AND json_extract(body,'$.d.conv') = ? ORDER BY json_extract(body,'$.d.seq') DESC LIMIT 20", 'sid:<chat>')` -- every message the seat hears is a `discourse` row (`text`, `conv`, `role`, `ts`, `seq`).
