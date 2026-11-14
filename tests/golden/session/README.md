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

`session_patch_model.json` is NOT a recording: `scripts/golden_session.py` does not run offline in every environment (its first
`message` step answered no rows on the 2026-11-13 run, also without any change), so this file is the session of `session_patch.json` with the
model switch the released handler applies written in: `server.py` at `v0.7.53`, `do_PATCH` ("THE MODEL'S PATCH", lines 5249-5265): the
session row's `model` becomes `{id: modelID or id, providerID or "membrane", variant: "default"}`; the shape of the row is `SessionStore.create`
(lines 342-358). Same for `tests/golden/boot/v1_models.json`: the handler `_v1_models` of `membrane/lanes.py` at `v0.7.53` (lines 5403-5425)
builds `{object: "list", data: [{id, object: "model", owned_by}]}`, `owned_by` is `membrane-router` for the autoselect lanes, else
`membrane->` + the router record's backend (`claude-agent` for a rung), `ollama` for a tag and the `@explore` sentinel.
