# The project decision map

**"How did we get here → what are we doing → what do we need to do next."** The map's cards are *decisions*, not tasks, and it covers the whole project across every chat and agent ([#470](https://github.com/chedim/babyseat/issues/470)). The page shows it as a cockpit: a kanban turned 90°, with the time axis running top to bottom and the chat's input bar as *now*.

```
   open questions ▲              GOALS                 ▲ open questions
   (the first to decide      small field + 🪑
    nearest the bar)
  ──── in flight ─────  [ the input bar (now) ]  ───── in flight ─────
   decided ▼              the chat, newest first           ▼ decided
   (the newest nearest)   older messages below
```

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
| `chat` | the chat about this decision (clicking the card loads the question into it) |
| `message` | the message in `chat` where the card was decided: the chat's newest message when ✓ was pressed while the card's chat was open (set only by the answer, and only with its chat) |
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

`GET /membrane/decisions` is a read, open to the same callers as the seat's other reads (localhost, the LAN in standalone, a token in JWT mode). Only answering needs the owner.

Agents can't decide through any write path. `entity_add` takes no properties (the MCP tool sends extra properties through `entity_edit`, which refuses), and a script's `patch` admits only `position`, `script_data` and `links`. An older `Decision` that already carries a `value` keeps it, but `entity_edit` can no longer change it.

## Build order

1. **The entity convention and the answer route** (this page, `membrane/decision_map.py`).
2. **The cockpit view** (`membrane/ui/decisions.js`, the standalone menu's 🗂 Decisions, `/?view=decisions`). It replaced the first board, a three-column kanban. The center column holds the goals (`GET /membrane/goals`), a small field (`field3d.js`, the same one the page boots, drawing at most 1500 points) with the babyseat over it, and the real chat element in its `cockpit` mode (`<ai-terminal-panel cockpit>`: the composer on top as the input bar, about 55% of the page wide, and the messages below it, newest first). On both sides, mirrored, open cards pile up above the bar's line with the first to decide nearest it (a card that blocks another comes before it). Cards in flight sit on the line, and decided cards fall below it, the newest nearest. Each band's cards alternate left and right. Five past rows stay in view, and each new decision pushes the oldest out. With CSS perspective, future cards farther up are smaller and tilted back, while past cards farther down shrink, tilt the other way and fade. `prefers-reduced-motion` turns off the transitions. On a phone (≤ 760 px) it's one column: goals, open, in flight, the bar and chat, then the past.
   - An open card's one input is pre-filled with the pick, and ✓ answers it.
   - Clicking a card loads a message about the decision into the center bar, pre-filled and never sent. If the card has its own `chat` and it isn't the one open, the center chat switches to it first. Otherwise the open session is recorded on the card with `POST /membrane/decision/chat`.
   - ✓ pressed while the card's chat is open sends that chat's newest message id as `message`.
   - Hovering (or focusing) a decided card highlights it. If its chat is the one open and it has a `message`, the chat scrolls to that message and marks it. Clicking it opens its chat and then scrolls.
   - Every edge shows as a chip on its card (`blocks: <title>`, `carried_by: PR #468`).
   - The cockpit re-reads the seat every 5 s while it's shown.
3. **The field view and timelines:** decision edges drawn on the field. Changing a past decision creates a new `Timeline` branch (`TimelineOps` in `membrane/entities.py`).
