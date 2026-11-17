# AGENTS.md

Working instructions for AI coding agents in this repository (membrane / babyseat).

## What this repo is
babyseat is an AI seat whose memory is a graph the model programs. The seat is `cliffvm` (Rust, `native/`); its packs and page are `.c1f` files (`membrane/packs/`, `membrane/apps/`); Python is only the thin launcher
(`membrane/launcher.py`). Start at README.md, then docs/README.md (the index of every page), docs/cliffvm.md (the reference) and docs/porting-brief.md (the rules for c1f and Rust work).

## Ground rules
1. Test first, then the fix; every change ships its test. Run the suites that your change touches before committing, and the whole set before a PR:
   - Rust (the seat, real processes, no model): `cd native && cargo test --release --no-default-features --features bin` (the app is built without the `python` feature).
   - The pack-tied tests (the seat started on this repo's packs and apps; `seat-tests/`) run on the pinned cliffvm release: `scripts/dist/fetch_cliffvm.sh "$(cat CLIFFVM_VERSION)" <target> <folder>`, then `cd seat-tests && CLIFFVM_DIST=<folder> cargo nextest run --release` (`cargo test` works too, one binary at a time).
   - The page (c1f view packs on the wasm engine, some in headless Chrome): `node tests/js/<name>.test.mjs` (`CHROME=<path>` names the browser; a test that needs a seat builds nothing: build `cliffvm` first).
   - Home Assistant component and the launcher: `pytest tests/ha tests/wrapper`.
2. Keep docs honest in the same change: README.md maps the entry points, docs/ holds the references (docs/README.md lists them), the model-facing help is `membrane/apps/barest.c1f/help/*.md` (every code block is a test: `help_1143`).
   A doc says what the code does NOW; something in progress says so and names its branch; something gone is removed (it is in git and the tags), not marked.
3. CHANGELOG.md, CITATION.cff and the version bump are written at the release commit only, never in a feature or fix PR.
   The engine ships from its own repository (github.com/chedim/cliffvm; its README has the plan of the move): the release binaries are the cliffvm release named by `CLIFFVM_VERSION`. Until `native/` is removed from this repo, a change to the engine is made in BOTH (`native/` here, where its tests run, and chedim/cliffvm, what ships), and `CLIFFVM_VERSION` moves to the cliffvm release that has it.
4. Sync with origin via fetch + ff-only; never rewrite shared history. Base every PR on main (no stacked PRs).
5. No secrets in code, docs or comments: tokens come from the environment. No personal names or home paths in anything published (`~` or a placeholder).
6. Keep new commands small and named; prefer composing existing ones over one-off scripts. Behaviour is c1f; Rust is only the host (docs/porting-brief.md): no new Python logic.

## When you finish a task
Before declaring a task complete, re-check that README.md and the docs/ pages you touched still match the code (run the command, read the pack; do not trust another doc), that docs/README.md lists every page and no page that is gone,
and that the tests of rule 1 pass on the tree you are handing over. This file is the agents-side checklist.
