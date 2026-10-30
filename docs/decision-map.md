# The project decision map

**"How did we get here → what are we doing → what do we need to do next."** The map's cards are *decisions*, not tasks, and it covers the whole project across every chat and agent ([#470](https://github.com/chedim/babyseat/issues/470)). The page shows it as a cockpit: a kanban turned 90°, with the time axis running top to bottom and the chat's input bar as *now*.

```
   open questions ▲              GOALS                 ▲ open questions
   (the first to decide           🪑
    nearest the bar)
  ──── in flight ─────  [ the input bar (now) ]  ───── in flight ─────
   decided ▼              the chat, newest first           ▼ decided
   (the newest nearest)   older messages below
```

The whole cockpit floats over the page's 3D field, its full-page background.

A question starts above the bar. When the operator answers it, the work starts on the bar's line. When the work lands, the card falls below the line into the past, still linked to what led to it.

## A card is an entity

A card is a `Decision` entity, reusing the existing class. Nothing is stored beside the field, so the map is a subgraph of the field: Blurp feels it like any other entity, recall finds it near the code it touched, and the board is just one view of it.

| property | meaning |
|---|---|
| `name` / `summary` | the card's title / short description |
| `status` | `open` → `in_flight` → `decided`. A `Decision` without a status is a legacy one, already decided. |
| `var` | the ONE variable the card asks for: `{"type": "int"\|"float"\|"bool"\|"string"\|"option", "options": [...], "unit": "s"}` |
| `pick` | the agent's recommended value, pre-filled so accepting is one click |
| `value`, `set_by`, `set_at` | written **only** by the operator's answer |
| `chat` | the chat about this decision (its panel's Chat about it loads the question into it) |
| `message` | the message in `chat` where the card was decided: the chat's newest message when Decide was pressed while the card's chat was open (set only by the answer, and only with its chat) |
| `blocks`, `led_to`, `carried_by` | eid lists, the graph's edges: future → future, past → what it led to, in flight → the work carrying it |

## Agents ask, the operator decides

An agent makes a card with the existing entity tools:

```
entity_add   class=Decision  name="Turn time limit for Claude rung"
             summary="The watchdog aborts turns at 300 s; Memo hits it in long cycles."
entity_edit  spec=<eid>  properties={"status": "open", "var": {"type": "int", "unit": "s"},
                                     "pick": 1800, "blocks": ["<another card's eid>"]}
```

On a `Decision`, `entity_edit` refuses `value`, `set_by`, `set_at` and any `status` other than `open` or `in_flight`. Only the operator answers:

- Both ports serve the board: the proxy (`:11500`) and the serve port (`:4096`, which the page reaches as `/ui/serve/...`).
- `GET /membrane/decisions` returns `{cards: [...], edges: [{from, to, rel}]}`.
- `GET /membrane/goals` (serve port) returns the cockpit's goals, `{goals: [{eid, title, summary, active, born}]}`: the open `Goal` entities, with the seat's active set (the intent board's selection) first and flagged `active`.
- `POST /membrane/decision/chat {card, chat}` (serve port, owner only) records the chat a card opened. Only `chat` changes, and a card keeps one it already has.
- `POST /membrane/decision/answer {card, value, conv, message}` (seat owner only) validates the value against the card's `var`. It then marks the card `decided` (keeping `message` when it comes with its `conv` and that is the card's chat) and appends `decided: <title> = <value>` to the operator ledger (`<field>.ledger.jsonl`), so the decision is recalled verbatim, as law. The ledger line is one line, and the title and value are each clipped to 160 characters. The ledger is written first, so a failed write leaves the card open. Answers run one at a time, so a card is decided once. A decided card refuses another answer (409), and `entity_edit` can't reopen it. Changing a past decision will create a timeline (step 3). A `Decision` with no status yet (made before the map) can be given one and become a card.

### An answer wakes the watcher loops

A successful answer puts one `decision` event into the watcher loops' events pool (`est._events_pool`, the pool a fired watch lands in): `{kind: "decision", entity: <card eid>, title, value, blocks}`. The loops' drainer admits a pass on the card like any watch event, and its task line reads `decided: <title> = <value> (card '<eid>')` with what the card blocks. A refused answer enqueues nothing.

`GET /membrane/decisions` is a read, open to the same callers as the seat's other reads (localhost, the LAN in standalone, a token in JWT mode). Only answering needs the owner.

Agents can't decide through any write path. `entity_add` takes no properties (the MCP tool sends extra properties through `entity_edit`, which refuses), and a script's `patch` admits only `position`, `script_data` and `links`. An older `Decision` that already carries a `value` keeps it, but `entity_edit` can no longer change it.

## Build order

1. **The entity convention and the answer route** (this page, `membrane/decision_map.py`).
2. **The cockpit view** (`membrane/ui/decisions.js`, the default view of the page, standalone and under Home Assistant; the standalone menu's 🗂 Decisions, `/?view=decisions`). It replaced the first board, a three-column kanban. The cockpit scrolls as one document: the goals, the open pile, the bar's line, the chat and the past move together, and nothing inside scrolls on its own. The chat grows with its messages and has no scrollbar of its own. On load the input bar is scrolled into view once, at about 40% of the view, unless the person has already scrolled. Scroll up for the future, down for older chat and the past. The page's 3D field (`field3d.js`, the same boot and adaptive point budget as the full page) is the cockpit's full-page background and stays put while the page scrolls. It sits behind every panel with `pointer-events: none`, so its drag, hover and select never fire there, and the cards, goals and chat keep dark backings so their text stays readable over it. The center column holds the goals (`GET /membrane/goals`), the babyseat logo, and the real chat element in its `cockpit` mode (`<ai-terminal-panel cockpit>`: the composer on top as the input bar, about 55% of the page wide, and the messages below it, newest first). In this mode the composer has one button instead of Send/Queue + Stop, and it sits inside the input bar at its right end, like a search box's icon: pinned to the bottom-right corner, so it is centered on the one-line bar and stays beside the last line as the text grows, and the text keeps clear of it. It reads **Send** (cyan) when idle. While an answer runs it reads **Stop** (amber) if the input is empty, and **Send** again (it queues the message) once the person types. Clicking it does what it says. Enter still only sends or queues; Esc stops. On both sides, mirrored, open cards pile up above the bar's line with the first to decide nearest it (a card that blocks another comes before it). Cards in flight sit on the line, and decided cards fall below it, the newest nearest. The cards form a **dependency graph**, "chomping through uncertainty by making decisions": past decisions lead to the work in flight (`led_to`), and the open decisions above block it (`blocks`). Each band keeps its own order, but the graph picks each card's side (`graphLayout`). A card goes to the side its linked cards are on, the card with the strongest pull placed first, with neither side taking more than half. The in-flight cards are also ordered by their neighbours' rows. The past band holds every decided card, but only the cards near the view are in the page; spacers keep the page height as the rest load and unload while scrolling. A graph line to a card that isn't loaded is a short stub pointing toward it. The chat is virtualized the same way. Only the messages near the view are rendered, and older messages are read from the seat in pages of 40 as the view nears the oldest loaded one (`GET /session/<id>/message?limit=N&before=<message id>`; without the params the route still returns every message). With CSS perspective, future cards farther up are smaller and tilted back, while past cards farther down shrink, tilt the other way and fade. `prefers-reduced-motion` turns off the transitions. On a phone (≤ 760 px) it's one column: goals, open, in flight, the bar and chat, then the past.
   - A card always fits its lane (#736): long titles and text are cut off with "…", and an open card shows only its pick, never its options. Hover never widens it.
   - Clicking an open or in-flight card opens it in place as a floating panel over everything (centered, up to 460 px wide, scrolling inside; Esc, ✕ or a click outside closes it). The panel has the full title and description, every option as a wrapped list with the pick selected, **Decide**, **Drop**, and **Chat about it**.
   - **Chat about it** loads a message about the decision into the center bar, pre-filled and never sent. If the card has its own `chat` and it isn't the one open, the center chat switches to it first. Otherwise the open session is recorded on the card with `POST /membrane/decision/chat`.
   - Decide pressed while the card's chat is open sends that chat's newest message id as `message`.
   - Hovering (or focusing) a decided card highlights it. If its chat is the one open and it has a `message`, that message is marked, without scrolling: the cockpit is one scrolling page, so a scroll would move the page under the pointer. Clicking the card opens its chat if needed and scrolls to the message.
   - Every card→card edge is a line, drawn in SVG under the cards and over the background field.
     - Lines touching the past are amber; lines between the work and the open questions are cyan; `blocks` lines are dashed. Each line has an arrowhead.
     - A line between two cards on the same side loops through the page margin.
     - A line between the two sides runs behind the center column, where a mask fades it to about 22%. The bar and the chat text stay clear.
     - Hovering or focusing a card lights its edges and its neighbours.
     - An edge to work outside the board stays a chip (`carried_by: PR #468`).
     - On a phone there are no lines: the card links are chips naming the other card (`blocks: <title>`).
   - The cockpit re-reads the seat every 5 s while it's shown.
3. **The field view and timelines:** decision edges drawn on the field. Changing a past decision creates a new `Timeline` branch (`TimelineOps` in `membrane/entities.py`).
