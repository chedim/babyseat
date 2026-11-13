# Golden fixtures for the page's boot-time routes (#1143, swap step 3)

What the RELEASED Python code (tag `v0.7.53`) answers on the routes the page calls when it boots and for its field views, recorded by
`scripts/golden_boot.py --tree <v0.7.53 checked out>`: each answer is the released function that builds it (`server._agent_shapes`,
`options.read`, `quota.status`, `ch_blurp.overlay`, `field3d.build`, `voice_io.set_state`), called with the empty inputs a fresh seat has;
no model, no seat, no network. One file per route answer, `{status, body}`. The header of the script lists, per file, the function and the
handler line it stands for. `map_field` and `map_search` are the literals the released handler writes when the field is not loaded / there is
no entity store (the handler computes nothing else in that case).

The consumer compares SHAPE, never values (the comparer is `shape_misses` in `native/tests/boot_routes_1143.rs`, the same as the session API's):
every key of the fixture must be in the answer with the same JSON type (a `null` in a fixture accepts any type); extra keys are fine;
an element of an answer's list must match some element of the fixture's list; an empty fixture list accepts anything.

`python3 scripts/golden_boot.py --tree <dir> --check` exits 1 when the Python tree's answers differ in shape from the committed files.
