# The project decision map

**"How did we get here → what are we doing → what do we need to do next."** It's a reversed kanban whose cards are *decisions*, not tasks, and it covers the whole project across every chat and agent ([#470](https://github.com/chedim/babyseat/issues/470)).

```
  HOW DID WE GET HERE   ◄──   WHAT ARE WE DOING   ◄──   WHAT DO WE NEED TO DO NEXT
  decided (a graph)           in flight (now)           open questions (a graph)
```

A question starts on the right. When the operator answers it, the work starts (middle). When the work lands, the card joins the past (left), still linked to what led to it.

## A card is an entity

A card is a `Decision` entity, reusing the existing class. Nothing is stored beside the field, so the map is a subgraph of the field: Blurp feels it like any other entity, recall finds it near the code it touched, and the board is just one view of it.

| property | meaning |
|---|---|
| `name` / `summary` | the card's title / short description |
| `status` | `open` → `in_flight` → `decided`. A `Decision` without a status is a legacy one, already decided. |
| `var` | the ONE variable the card asks for: `{"type": "int"\|"float"\|"bool"\|"string"\|"option", "options": [...], "unit": "s"}` |
| `pick` | the agent's recommended value, pre-filled so accepting is one click |
| `value`, `set_by`, `set_at` | written **only** by the operator's answer |
| `chat` | the chat about this decision (clicking the card opens it) |
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

- `GET /membrane/decisions` returns `{cards: [...], edges: [{from, to, rel}]}`.
- `POST /membrane/decision/answer {card, value, conv}` (seat owner only) validates the value against the card's `var`. It then marks the card `decided` and appends `decided: <title> = <value>` to the operator ledger (`<field>.ledger.jsonl`), so the decision is recalled verbatim, as law. The ledger line is one line, and the title and value are each clipped to 160 characters. The ledger is written first, so a failed write leaves the card open. Answers run one at a time, so a card is decided once. A decided card refuses another answer (409), and `entity_edit` can't reopen it. Changing a past decision will create a timeline (step 3). A `Decision` with no status yet (made before the map) can be given one and become a card.

`GET /membrane/decisions` is a read, open to the same callers as the seat's other reads (localhost, the LAN in standalone, a token in JWT mode). Only answering needs the owner.

Agents can't decide through any write path. `entity_add` takes no properties (the MCP tool sends extra properties through `entity_edit`, which refuses), and a script's `patch` admits only `position`, `script_data` and `links`. An older `Decision` that already carries a `value` keeps it, but `entity_edit` can no longer change it.

## Build order

1. **The entity convention and the answer route** (this page, `membrane/decision_map.py`).
2. **The board view** (`membrane/ui/decisions.js`, the standalone menu's 🗂 Decisions, `/?view=decisions`): three columns, decided · in flight · open. In the open column, a card that blocks another sits above it. Lines show the edges between cards, and chips show work outside the board (`carried_by: PR #468`). An open card's one input is pre-filled with the pick, and ✓ answers it. Clicking the card itself opens its chat: the card's `chat` session, or a new one titled `Decision: <title>`, recorded on the card with `entity_edit`. The chat opens with a message about the decision pre-filled, never sent. The board re-reads the seat every 5 s while it's shown.
3. **The field view and timelines:** decision edges drawn on the field. Changing a past decision creates a new `Timeline` branch (`TimelineOps` in `membrane/entities.py`).
