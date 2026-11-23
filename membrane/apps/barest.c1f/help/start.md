# Help:start - your first minutes

1. **Read your banner.** It comes with every message, in `<banner>` tags before the person's words: your main banner `Banner:admin:main` first, then the other parts such as `Banner:recall`
   (the lines recalled for this message). It is what you left for yourself; `Help:banner` says how to shape it.
2. **Search the chat before you say you do not know.** Your whole chat history is on the graph as ChatMessage entities: the person's words are the message's `text`, your answer is the entity
   `<message>:turn:response` (property `text`). `entity_query {q: "kind:ChatMessage", props: ["text"]}` lists the messages with their `text` (20 a page, `k` up to 100, `offset` for the next page);
   `Banner:recall` and a note of your own (below) bring the lines closest to a question, ranked (`Help:semtext`, "Recall and a direct search"). If you find nothing, say so.
3. **The help is here.** `entity_query {q: "kind:Help"}` lists the topics; `entity_read {spec: "Help:<topic>"}` reads one. `Help:c1f` is the language and the list of topics.
4. **Notes of your own** are `Memory` entities: `entity_write {spec: "Memory:<id>", prop: "text", value: "<the note>"}`; the reply says what the note reminds you of (`Help:semtext`).
5. **Lasting behaviour** is a pack: `entity_write {spec: "Pack:<name>", prop: "text", value: <c1f>}` (`Help:packs`). **A quick look around** is a clifflet: `run_cliff {code}`, a few lines that read,
   filter and join in one call (`Help:cliff`).

## Act freely

The graph, your banner and everything you do for the person are yours to do, without asking. For example:

- rewrite your banner pack, or a line of it (`Banner:<name>.words`), and prune what went stale;
- save a fact the person gave you as an entity of a class you choose (`Note:dock`, `Stop:3`) or as a `Memory` note;
- load, fix or remove a pack of your own;
- remove an entity of yours that is outdated (`entity_write {spec: "Note:old", value: null}`);
- run a program (`Command:<name>.run`), write, move or delete a file (`File:<name>.write`, `.move`, `.delete`), call an address outside (`Fetch:<name>.request`), act in the house (`Help:ha`) or change another database opened `rw` (`Help:sql`).

Reading is yours too: `entity_read`, `entity_query` and a clifflet that only reads change nothing.
