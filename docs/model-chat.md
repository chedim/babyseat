# The raw chat with the model (`membrane/packs/model_chat.c1f`)

One entity per chat, `ModelChat:<chat>` (`ModelChat:main`, or `ModelChat:session:<sid>`), whose `text` is the exchange with the model as the model had it, whichever rung answered: the claude CLI (`chat_turn.c1f`), an OpenAI-compatible or ollama model (`chat_oai.c1f`), or a peer over graphlink (`chat_membrama.c1f`: membrama, `claude-peer`, and any peer that speaks the same contract). The seat's Log tab shows it (`docs/logview.md`).

## Shape

Each turn adds, in order:

```
── <message id> round <n> ──        a round's header (the CLI's turn is one `── <message id> ──`)
[system] / [user]                   the prompt as sent (round 1; a later round adds only the tool results)
[assistant]                         the reply as the model wrote it, its thinking block included when the backend gives it
[call] <tool> <arguments>           each tool call, whole arguments
[tool]                              each tool's result: the very text the model got
[error]                             why a round or the turn failed
```

## How a rung feeds it

A rung writes ONE value, `ModelChat:<chat>.add` (the text of what just happened), and `model_chat.c1f` appends it. A round's text is its own getter `raw` (`ChatRound.raw`, `MbRound.raw`); the CLI's is `raw_head` + `raw_events` + `raw_err` written when the run ends. It is written when a round with tool calls has its results (`oai-next`, `mb-next`), and when the turn ends (`oai-finish`, the CLI's `turn-finish`), so a failed turn says why. Nothing about a transport (slots, rings, Fetch receipts) is in it; the key rides in a header the driver adds and is in no text. Each setter writes `add` ONCE (a second write of one property in one setter does not stand).

## The bound

The text is never longer, as JSON, than `link_max_value` characters, the boot fact of the page link and the peers (65,536 unless the owner sets it); the oldest text goes first and the text starts with `[…earlier text dropped]`. The engine counts characters, so a text with non-ASCII letters can be longer in bytes.

## Tests

`seat-tests/tests/chat_openai_1143.rs` (the OpenAI-compatible rung: order, thinking, call, result, a failed turn, the bound), `chat_peer_1143.rs` (a peer's turn, same shape), `chat_process.rs` (the claude CLI's run, on the fake CLI). Not covered: a real model's output, a real browser.
