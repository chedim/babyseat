# Doctrine of the barest seat: the model's whole system text

Status: SHIPPED. This is the whole system text of the chat turn (`ChatMessage.note` in `membrane/packs/chat_turn.c1f`; the OpenAI-style turn of `chat_oai.c1f`, not in the release list, sends the doctrine and then the banner in its system message), in the operator's own words
(2026-11-13). The system prompt is the doctrine only: the banner is not part of it. The banner block (`<banner>` tags: `Banner:admin:main`'s text, then every other non-empty `Banner:<name>` part, oldest update first, `Banner:recall` among them) and the user's words are two text blocks of ONE stdin user message (`ChatMessage.stdin`). The static instructions are the help entities: `Help:c1f` (where the help is and the language), `Help:packs`, `Help:banner`, `Help:upgrade` and the rest
(`entity_query` `kind:Help` lists them; the files are `membrane/apps/barest.c1f/help/*.md`). `native/tests/help_1143.rs` pins the text and loads every code block of the help; `neutral_text_1143.rs` keeps all of it
domain-neutral (no product names, no software-only examples in the model-facing text).

```
Your name is <name>. You are the graph: requests come and go, the graph stays. What is on it is what you remember, and nobody else keeps it up. It is programmable: use cliffhanger to maintain it as your memory and to interact with the environment. Modify your banner to help you maintain continuity. Keep it fresh: when you see junk or outdated information in it, remove it. Use Help entities for help. Editing the graph and your banner is yours to do: change them whenever it helps you remember and serve the user better, without asking for confirmation. Ask first only before something that reaches outside the graph. Process updates on the graph and react to them. Your whole chat history is on the graph as ChatMessage entities: the user's words are in text, your answers in response. Before you say you do not know or do not remember, search the ChatMessage entities. Do not guess: if you find nothing, say so, unless the user asks you to guess.
```

While the name is the default, Bob, one more sentence follows: Bob is only the default name: work with the user to choose yours, and save it with entity_write {spec: "Setting:chat.name", prop: "set", value: "<the name>"}. Once the name is anything else it is only "Your name is <name>." (the name is the settings row `Setting:chat.name`, fact `seat_name`).

## What the text promises, and where the seat stands

| the text says | today |
|---|---|
| the model's tools: `entity_query`, `entity_read`, `entity_write`, `run_cliff` | built (`door_tools.c1f`); the whole tool surface is field operations |
| Command / File / Fetch / Sql entities act by writes | the drivers are in the VM; each is reached through `entity_write` and its receipt is read back (`<prop>_result`) |
| the banner is the model's entity | built (`membrane/apps/barest.c1f/default_banner.c1f`, `Help:banner`): `Banner.text` is a live getter; the model replaces it with a pack that imports `default_banner` and defines `Banner.text` (the class list is not the model's: `entity_write` refuses `parents`, classes are set at birth); the seat never edits it; a turn shows every `Banner:*` instance together, the main one first, then the others oldest update first |
| write a `.c1f` pack | built: `entity_write {spec: 'Pack:<name>', prop: 'text', value: <c1f>}` loads or reloads it; read `Pack:<name>.state` / `.error`. The tools pack is privileged (granted by boot), the model's own packs are not: docs/cliffvm.md, "Pack entities" |
| Help entities | built: twelve topics (`Help:c1f`, `packs`, `upgrade`, `banner`, `cliff`, `semtext`, `sql`, `legacy`, `ha`, `settings`, `environment`, `page`; `apps/barest.c1f/help.c1f`) |
| recall brings facts | built: `Banner:recall` (`apps/barest.c1f/recall.c1f`) shows the sentences recalled for the message being answered, read from the one recall every reader shares (`SemText.recall`: the chat's window of texts, an edge lane, a cosine lane, the move log and entity recall combined by a weighted vote, a rerank, near duplicates merged), dials in `recall:dials` (docs/semtext-and-legs.md, "Recall") |

The earlier, longer drafts of this text are in the git history.
