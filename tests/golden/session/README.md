# Golden fixtures for the opencode session API (#1143)

What the RELEASED Python seat (tag `v0.7.53`) answers on the session routes and puts on its event streams, recorded by
`scripts/golden_session.py --tree <v0.7.53 checked out>` with the turn runner's ladder stubbed (no model, no network; the same
stub the repo's tests use: `tests/test_claude_agent_tool_start.py`). One file per route answer, `{status, body}`; the two event files
are the frames of one stream each (`/event`, `/global/event`) as `{event, data}`.

The consumer compares SHAPE, never values: every key of the fixture must be in the answer, with the same JSON type (a `null` in a
fixture accepts any type: the recorded value was empty); extra keys in the answer are fine; for a list each element of the answer must
match SOME element of the fixture's list (a parts list holds a text, a tool and a step part). Ids, times, the working folder
(`/srv/seat`) are placeholders: only their type counts. The Python seat's own bookkeeping keys (`owner`, `_announced`, `readonly`) are
dropped from the fixtures: they are not the contract. The comparer is `native/tests/session_1143.rs` (`shape_misses`).

`python3 scripts/golden_session.py --tree <dir> --check` exits 1 when the Python tree's answers differ in shape from the committed files.
