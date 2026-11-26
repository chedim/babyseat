# Chat backends: one interface for every program that answers a message

A chat message is answered by one **backend**: the claude CLI, an OpenAI-compatible endpoint, a peer over shared memory, or a brain peer that writes the answer itself. Since #1143 (PRs #1209, #1210, #1211) every backend goes through the same steps: **choose**, **plan**, **start**, **live progress**, **finish**. Each step has one owner in `membrane/packs/chat_turn.c1f`, and a backend only fills in its own part.

This page is the contract for someone who changes a backend or adds one. The model reads the same story, without the code, in `Help:chat-backends` (`membrane/apps/barest.c1f/help/chat-backends.md`). The reference paragraphs are in [cliffvm.md](cliffvm.md) ("One finish for every backend", "One way to start a turn", "One source of live progress").

## The backends

| `backend` | answers | pack | plan getter | start setter | live accumulator |
|---|---|---|---|---|---|
| `brain` | a graphlink peer named by `Minds:seat.chat` (the `chat` key of the fact `minds`), whatever model is chosen | `chat_turn.c1f` | `brain_plan` | `brain-start` | none (the brain writes its answer whole) |
| `none` | nothing: no model is chosen | `chat_turn.c1f` | always `{refuse: no_model_text}` | none | none |
| `peer` | a `cloud_models` entry with `backend` `membrama`, `claude-peer`, `openai-peer` or `anthropic-peer`, or a model a live peer publishes (`<ns>::Models:main.names`, see [membrama-chat.md](membrama-chat.md)) | `chat_membrama.c1f` | `mb_plan` | `oai-start` (shared with `oai`) | none (a peer sends its answer whole) |
| `oai` | any other `cloud_models` entry that is not `claude-agent`, or a model the local Ollama lists | `chat_oai.c1f` | `oai_plan` | `oai-start` | `oai_live` (the current round's `Chunk:Fetch:oai:<cp>:<round>:acc`) |
| `claude` | everything else: the claude CLI | `chat_turn.c1f` | `claude_plan` | `claude-start` | `claude_live` (the CLI command's `Chunk:<command>:acc`) |

## 1. Choose: `ChatMessage.backend`

One getter names the backend, first match wins: `brain` (a brain has the chat), `none` (`model_eff` is empty), `peer` (`mb_cfg` is set), `oai` (`oai_cfg` is set), else `claude`. The order matters: a brain that has the chat answers even when a peer model is chosen (`tests/specs/chat_start.json` has a spec with both).

## 2. Plan: `<backend>_plan`

Each backend has ONE plan getter with one shape:

- `{refuse: <the reason>}` when it cannot answer. The reason is said to the person as the turn's answer, so it names what to do (set a key, start the peer, pick a model).
- `{start: <what its start setter needs>}` when it can.
- `None` while that is not known yet (the turn waits).

`ChatMessage.backend_plan` dispatches on `backend`, and `ChatMessage.plan` is `{backend, refuse}` or `{backend, start}` once the message is queued, its text is there and recall is done.

## 3. Start: `turn-start`, then `<backend>-start`

One setter, `turn-start` (on `Chat.plan`), acts on every plan:

- a refusal ends the turn through `turn.outcome` (`{text: <reason>, fail: <reason>}`), so a refused turn finishes like any other;
- a start writes the turn's `state` = `running`, `started` = the clock and `backend` = the plan's backend.

Then the backend's own start setter, guarded by `WHEN ${this.plan}['backend'] == '<name>'`, does only its effect: `claude-start` makes and runs `Command:chat:<cp>` with the argv and the stdin of the plan; `oai-start` (for `oai` and `peer`) writes `turn.oai` and reads the door's tool list; `brain-start` writes `turn.brain` and the empty `ChatResponse` the brain answers on. A start setter writes no `state`.

## 4. Live progress: `<backend>_live`

While a turn runs, `ChatMessage.live_acc` picks the accumulator by the turn's recorded `backend` (`claude` -> `claude_live`, `oai` -> `oai_live`, anything else -> none), and `ChatMessage.live` is `{text, tools}` read from it. Everything that shows a running turn reads `live`: the page's `partial_text` and `partial_tools`, the door's session events (`door_session.c1f`) and the words a failed turn keeps (`cut_text`). A backend that streams names its accumulator in its own pack; one that does not leaves `live` empty until the finish.

## 5. Finish: `turn.outcome`

A backend ends a turn by writing ONE value on the turn:

```
<message>:turn.outcome = {text, fail?, cut?, body?, rc?, usage?, cost?, calls?, duration?, raw?, names?, ids?, tools?, results?}
```

One setter, `turn-outcome`, writes the rest: `ended`, `error` (= `fail`), `usage`, `cost`, `calls`, `duration`, the `ChatResponse` at `<turn>:response` (`text`, `error`, `rc`; marked `interrupted` with `fail` as its `reason` when `cut` holds the words said before the failure), its `body` SemText, one `ChatTool` per entry of `tools` (`<turn>:tool:<id>`, `name`, `input`) with the `result` from `results`, the message's `tools_run` (`names`) and `tool_ids` (`ids`), the `ModelChat` text (`raw`, see [model-chat.md](model-chat.md)), and `state` last: `error` when `fail` is set, else `done`. An outcome on a turn that is already over changes nothing, and a taken outcome is cut down to `{fail, taken: true}`.

Who writes it: claude (`claude_outcome`), the OpenAI-compatible and peer rungs (`oai_end`), the brain's answer, decline and stop (`brain-answer`, `brain-decline`, `brain-abort`), a refusal (`turn-start`) and a turn left running at boot.

**Stop.** The page's Stop marks the message `aborted`. The claude CLI is killed (`Command.stop`), membrama is told (`mb-abort`), a brain's turn ends at once (`brain-abort`), and the OpenAI-compatible rung ends at the end of the round in flight: `ChatRound.stopped` reads the flag, the round's `step` is a `fail` (`The model <name> was stopped.`) and no other round opens, so the model is called no more. Until then a turn on that rung only stopped at `chat_max_turns` (2048 by default).

## Adding a backend

1. **Choose:** add an arm to `ChatMessage.backend` and to `backend_plan`, in the place its priority says.
2. **Plan:** a getter `<name>_plan` in its own pack that answers `{refuse}` or `{start}` (or `None` while waiting). Put every check here, not in the start setter.
3. **Start:** a `Chat.plan` on-setter `<name>-start` guarded by the backend's name that does only the effect (a Command, a Fetch, a request to a peer). It writes no `state`.
4. **Live (optional):** if it streams, a getter `<name>_live` naming its accumulator entity (`{text, tools}`), and an arm in `live_acc`.
5. **Finish:** write `turn.outcome` once, from a setter that fires when the backend's result arrives. Write nothing else on the turn.
6. **Tests first:** specs in `tests/specs/` beside `chat_start.json` (its plan, refusal and start), `chat_progress.json` (its live source) and `chat_outcome.json` (its finish), seen red before the change. Regenerate `surface.json` and `types.json` (`SURFACE_UPDATE=1`, `TYPES_UPDATE=1` on the `contracts` test).

## Tests

- `tests/specs/chat_start.json`: the backend choice and each plan and start.
- `tests/specs/chat_progress.json`: the live source per backend, and nothing live once a turn is over.
- `tests/specs/chat_outcome.json`: the one finish.
- `tests/specs/chat_turn.json` and the seat tests `chat_*`, `loop_chat_1143`, `session*`: each backend end to end.
- `seat-tests/tests/help_1143.rs` runs the example in `Help:chat-backends`.

## Not built

- Live text for peers: a peer (`MbRound`) sends its answer whole, so `live` stays empty while it works.
- A queue: two messages of one session run side by side.
