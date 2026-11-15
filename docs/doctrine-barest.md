# Doctrine of the barest seat: the model's whole system text

Status: SHIPPED. This is the whole system text of the chat turn (`ChatMessage.note` in `membrane/packs/chat_turn.c1f`; the OpenAI-style turn of `chat_oai.c1f`, not in the release list, sends the same), in the operator's own words
(2026-11-13), followed by the chat's banner (`Banner:admin:main`'s text). The static instructions are the help entities: `Help:c1f` (where the help is and the language), `Help:packs`, `Help:banner`, `Help:upgrade` and the rest
(`entity_query` `kind:Help` lists them; the files are `membrane/apps/barest.c1f/help/*.md`). `native/tests/help_1143.rs` pins the text and loads every code block of the help; `neutral_text_1143.rs` keeps all of it
domain-neutral (no product names, no software-only examples in the model-facing text).

```
Modify your banner to help you maintain continuity. Keep it fresh: when you see junk or outdated information in it, remove it. Use Help entities for help. Program the graph as your memory. Process updates on the graph and react to them.
```

## What the text promises, and where the seat stands

| the text says | today |
|---|---|
| the model's tools: `entity_query`, `entity_read`, `entity_write`, `run_cliff` | built (`door_tools.c1f`); the whole tool surface is field operations |
| Command / File / Fetch / Sql entities act by writes | the drivers are in the VM; each is reached through `entity_write` and its receipt is read back (`<prop>_result`) |
| the banner is the model's entity | built (`membrane/apps/barest.c1f/default_banner.c1f`, `Help:banner`): `Banner.text` is a live getter; the model replaces it with a pack that imports `default_banner` and defines `Banner.text` (the class list is not the model's: `entity_write` refuses `parents`, classes are set at birth); the seat never segments or joins it |
| write a `.c1f` pack | built: `entity_write {spec: 'Pack:<name>', prop: 'text', value: <c1f>}` loads or reloads it; read `Pack:<name>.state` / `.error`. The tools pack is privileged (granted by boot), the model's own packs are not: docs/cliffvm.md, "Pack entities" |
| Help entities | built: eleven topics (`Help:c1f`, `packs`, `upgrade`, `banner`, `cliff`, `semtext`, `sql`, `legacy`, `ha`, `settings`, `page`) |
| recall brings facts | not in this release: the banner is built from the entity map the model keeps |

The earlier, longer drafts of this text (persons, phones, goals, watches, the improve-the-project paragraph) are in the git history; they described the 0.7 Python seat.
