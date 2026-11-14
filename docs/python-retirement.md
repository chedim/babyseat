# Retiring the Python seat (plan only, #1143, 2026-11-14)

Docs only: nothing deleted, nothing built, no seat touched, no tag or branch created. Tree: `w105-pyretire` at `b41dc9c9` (= `memo-hotfix-0753`, 516 commits after tag `v0.7.53`).
Method: read-only `git ls-files`, `grep`, and one static `ast` pass over every tracked `.py` (does it import the top-level `membrane` package?). Nothing from the old seat or the old test suite was run.
"UNVERIFIED" = not run or not seen in the tree. "Round" = one agent round (20-45 min).

Operator direction (2026-11-14): "python seat is retiring"; "only the rust-vm seat stays"; Python stays ONLY as a wrapper for packaging and launching: a pip package `babyseat` that carries the `cliffvm` binary + packs + apps and a small launcher console script; no seat logic in Python. The Home Assistant component (Python, thin client) stays.

## 0. Important first

1. **Nothing the release needs imports or runs the `membrane` Python package.** The AST pass: `custom_components/` 12 files, 0 import `membrane`; `tests/ha/` 13 files, 0 import it; `native/` has no Python source; `tests/js` and `native/tests` only start the `cliffvm` binary. The Home Assistant manifest already says `"requirements": []` (`custom_components/membrane/manifest.json`) and the `supervisor.py` that packaging-plan.md section (e) worries about is gone from this tree (`ls custom_components/membrane`), so **HACS users are not affected by the package disappearing** (section 2). docs/packaging-plan.md section 0 point 1 and section (e) are stale on this.
2. **Five small couplings DO exist and must be cut first** (section 3): (a) `native/tests/contracts.rs:154-161` reads `membrane/opencode.py` + `membrane/fields_live.py` to check that every "wired" fact is written by the launcher; (b) `publish.yml` reads `membrane/CHANGELOG.md` (a data file, kept) and the version from `pyproject.toml` (kept); (c) `tests/ha/realseat.py` and `native/tests/*` read `membrane/packs` + `membrane/apps` (data, kept: the folder name `membrane/` stays); (d) native tests and `tests/js` use the system `python3` as a fake `claude` and `python3 -m http.server` (stdlib only: a dev-machine dependency, not the package; section 3); (e) `native/Cargo.toml` still builds the PyO3 extension by default (`default = ["python"]`) and `setup.py` compiles it into the wheel.
3. **No CI job runs the release's own suites today.** `validate.yml` runs `pytest` (the Python seat) and a `native-vm` pytest job; there is no `cargo test`, no `node tests/js`, and no wasm drift check in any workflow (`grep` of `.github/workflows`). Deleting the Python jobs without first adding the replacements would leave the release with no gate at all. Step 2 of the plan adds them BEFORE anything is deleted.
4. **A pip patch line cannot out-rank the wrapper**: patches of the Python line are `0.7.54+`, the wrapper is `0.7.60`; `pip install -U babyseat` picks `0.7.60` on every platform that has a wheel. Patch-line users must pin (`babyseat==0.7.5N`). Decision 5.
5. **Preserve the Python seat BEFORE the first deletion** (section 5, step 1): tag `v0.7.53` exists (`8c57c33e`); the branch `release-0.7` on origin is stale (`d154a889`, "release 0.7.1") and is NOT an ancestor of `v0.7.53`, so it cannot be the patch line without rewriting shared history (AGENTS.md rule 3). Propose a new branch `release-0.7.5` cut from `v0.7.53` and an annotated tag `python-seat-last` on the last `main` commit that still has the package. Nothing created now.

## 1. Inventory (every Python file in the repo)

204,479 lines in 764 tracked `.py` files (`git ls-files '*.py'` + `wc -l`). Directories `vendor/`, `plugins/`, `integrations/` do not exist in this tree (`ls`, `git ls-files`); `docs/packaging-plan.md` still names `integrations/homeassistant`, which is gone.

| group | files / lines | what it is | does the release need it? | evidence | fate |
|---|---|---|---|---|---|
| `membrane/*.py` (the seat package) | 158 / 88,970 | the 0.7 Python seat: server, opencode, rag, efield, cblstore, ch*, cortex leftovers, tui-free bits, `cli.py`, `ha_bridge.py`, etc. | NO, except the launcher entry points `cli.py:main` / `babyseat_main` which are REPLACED by a new ~30-line launcher (section 4) | only 3 of the 158 import `membrane` at top level; nothing outside `membrane/`, `tests/`, `experiments/`, `scripts/`, `benchmark/` does (AST pass); `native/src` has no `.py` read except via `contracts.rs:154` (step 2 cuts it); no workflow runs it except the pytest jobs and `docker`/`wheels` smoke (`membrane --help`, `import membrane_vm`) | delete (all but the new launcher) |
| `tests/*.py` (root) | 656 / 96,342 (+ `tests/fixtures/mcp_echo_server.py` 42) | the Python seat's suite; 575 of 657 import `membrane`; the other 81 (list in section 4) pin old-UI text, docs, packaging | NO for the seat; a handful pin data/packaging the release keeps (section 4) | `pyproject.toml` `testpaths = ["tests"]`; `validate.yml` `pytest -q` | delete after rewriting the pins (section 4) |
| `tests/ha/*.py` | 13 / 1,809 | the Home Assistant component's tests (fake HA + fake seat + a real `cliffvm` when built) | YES (release gate) | `realseat.py:38` finds `native/target/*/cliffvm` or `$CLIFFVM_BIN`; imports no `membrane` | keep |
| `custom_components/membrane/*.py` | 12 / 907 | the HA thin client (config flow, coordinator, conversation agent, entities) | YES (HACS ships it; `hacs.json`, `validate.yml` hacs + hassfest jobs) | 0 imports of `membrane`; `manifest.json` `requirements: []` | keep |
| `scripts/*.py` | 12 / 1,831 | see below | split | see below | split |
| `benchmark/recall_banner_fit.py` | 1 / 53 | fit of the Python recall banner scorer (#668) | NO | imports `membrane`; no doc or workflow runs it (`grep -rn recall_banner_fit` finds only itself and a doc mention) | delete |
| `experiments/**` | 68 / 14,512 (+ 11 README/.gitignore) | research scripts of the field (delta lane, multi_*, e1-e25, self_l1...) | NO | 53 of 68 import `membrane`; no workflow, doc step or script runs them | delete (history keeps them) |
| `setup.py` | 1 / 14 | `setuptools-rust` builds `native/` as the PyO3 extension `membrane.membrane_vm` | NO as is; replaced by the wrapper's build | `pyproject.toml:4`; `wheels.yml` cibuildwheel | rewrite (section 4) |
| `pyproject.toml` | root | package `babyseat`, deps numpy/tree-sitter/mcp/dateparser, scripts `membrane`/`babyseat` = `membrane.cli:*`, package-data lists (`pyproject.toml:59-60`: skills, viewer.html, three.min.js, woff2, `ui/*`, `packs/*.c1f`, CHANGELOG, `help/*.md`, doctrine.md), pytest config | the FILE stays (publish.yml and `test_version` read its `version`); its CONTENT is rewritten | `publish.yml:46,62`, `docker.yml` | rewrite |
| `shim/membrane-seat/` | 4 files, 0 .py | the dead name `membrane-seat`: pins `babyseat==0.7.53` and ships a copy of `membrane/` so pip's uninstall of the old name does not delete files babyseat just wrote (its own comment) | only for the 0.6-era upgrade story | `publish.yml` `shim` job copies `membrane/` in; `check` job pins version | Decision 6 |
| `requirements.txt`, `pyrightconfig.json`, `run.sh`, `.rd.lock`? | root | numpy+couchbase list; pyright config for membrane/tests/experiments; `run.sh` = `python -m membrane.cli` | NO | `run.sh` calls `membrane.cli` (line 8-30); no workflow reads the others | delete (`.rd.lock`: UNVERIFIED what writes it, ask) |
| `install.sh` | root | pip/uv installer of `babyseat` into `~/.membrane/venv`, links `membrane`/`babyseat`, installs Rust toolchain when no wheel | the user door (section 2) | `install.sh:37-91` | rewrite (packaging round), not delete |
| `Dockerfile`, `docker/docker-entrypoint.sh`, `.dockerignore` | root | python:3.12-slim image, builds the wheel with Rust, entrypoint maps `MEMBRANE_BACKEND`/`MEMBRANE_MODEL`/`MEMBRANE_EMBED_MODEL` to `membrane` flags, `:11500` healthcheck | the Docker door | `docker.yml` | rewrite (packaging round) |
| `.opencode/` | 7 files | generated opencode skills/commands of the Python seat ("generated on launch from membrane/skills/", `.gitignore:36`) | NO | nothing reads them but opencode launched by the old seat | delete |
| `BETA.md` | root | private-beta handout | NO (stale) | `scripts/build_release.py` era | delete (Decision 8) |

`scripts/` in detail (12 `.py`, 7 other files):

| file | lines | what | release/CI uses it? | fate |
|---|---|---|---|---|
| `golden_boot.py`, `golden_session.py`, `golden_efield.py` | 121 / 190 / 407 | record golden answers FROM the Python seat (oracle) | NO to run (they import `membrane`). Their OUTPUT `tests/golden/{boot,session}/*.json` (37 files) IS read by `native/tests/legacy_routes_1143.rs:126`, `session_1143.rs`, `boot_routes_1143.rs` | delete the scripts; KEEP `tests/golden/` as frozen recordings of the last Python seat (add a 3-line README there saying so) |
| `cb_migrate_field.py` | 123 | Couchbase seed migration | NO | delete |
| `build_release.py` | 118 | private-beta zips (HA integration with the package vendored) | NO (imports `membrane`; HA no longer vendors it) | delete |
| `bench_system.py`, `measure_facts.py`, `measure_locomo.py`, `measure_speed.py`, `measure_usage.py`, `measure_lib.py`, `token_growth.py` | 255 / 53 / 61 / 83 / 30 / 51 / 339 | cliffvm-only measurements and a pilot (0 `membrane` imports, `python3 -I`), documented in docs/measurements-2026-11-14.md, docs/bench-system-pilot.md, docs/token-growth.md | not by the release or CI; they ARE tooling for the Rust seat | Decision 7: keep (stdlib-only Python dev tools) or port to `cliffvm` subcommands later |
| `seat`, `seat-full`, `load_test.sh` | sh | launch the Rust seat (`scripts/seat*`), loop native tests under load | YES (docs/cliffvm.md, `seat_full_1143.rs` template) | keep |
| `fetch-cbl.sh` | sh | Couchbase Lite C for the Python seat | NO (only `validate.yml` and Python tests; README mentions it) | delete |
| `fetch-voice-model.sh`, `build-voice-ml.sh` | sh | pack Vosk/transformers.js assets for `build_release.py` | only via `build_release.py` | Decision 7 (voice assets for the page: who builds them now?) |
| `facts_throwy.json` | json | input of `measure_facts.py` | with it | follows `measure_facts.py` |

Non-Python data inside the package folder `membrane/` (278 tracked files; 158 are `.py`):

| path | files | used by the release? | evidence | fate |
|---|---|---|---|---|
| `membrane/packs/` | 46 | YES: boot root of trust, shipped | `native/tests/*` `include_str!("../../membrane/packs/..")`, `tests/ha/realseat.py:63`, `scripts/seat*` | keep (name `membrane/` stays; Decision 3) |
| `membrane/apps/` | 22 | YES | `ha_1143.rs:43`, `scripts/seat` | keep |
| `membrane/help/` | 19 | YES (Help entities; every code block is a test: `native/tests/help_1143.rs`) | | keep |
| `membrane/ui/` | 18 | YES today (`--ui-dir`, wasm, `fieldvm.js`); its old-page files (`home.js`, `panel.js`, `host.js`, `decisions.js`, `fieldview.js`, `field.js`, `fieldanim.js`, `index.html`) belong to the OLD-UI retirement, not to this plan | `door_serve_prefix_1143.rs:23`, `membrane/packs/door.c1f` names `ui/index.html` | keep here; the old-page cut is its own plan |
| `membrane/CHANGELOG.md` | 1 | YES (`about.c1f`, `publish.yml:156`) | `grep` | keep |
| `membrane/doctrine.md` | 1 | NO (served by the Python seat; the root `doctrine.md` is the repo's) | 0 hits in native/packs/apps/ui/workflows | delete (Decision 8) |
| `membrane/skills/` (8 dirs) | 8 | NO (opencode skills of the Python seat; the `.opencode/skills` copies are generated from them) | 0 hits in native/packs/apps | delete |
| `membrane/viewer.html`, `universe.html`, `three.min.js`, `PressStart2P.woff2`, `VT323.woff2` | 5 | NO (Python `server.py`/`ui_serve.py` assets; `ui/three.min.js` is the page's own copy) | 0 hits outside `ui/field3d.js` -> `ui/three.min.js` | delete |

## 2. Distribution: what users get today, what the packaging plan intends, what breaks

**Today (0.7.53):** PyPI `babyseat` (one abi3 wheel per platform with the PyO3 engine inside: macOS arm64 + x86_64, Linux glibc and musl x86_64 + aarch64; sdist elsewhere) and PyPI `membrane-seat` (the shim, pinned to `babyseat==0.7.53`); `install.sh` (uv tool or `~/.membrane/venv`, links `membrane` and `babyseat`); Docker `chedim/babyseat` and `chedim/membrane` (same tags, `docker.yml`); HACS (`hacs.json`, `custom_components/membrane`). `publish.yml`: merging a version bump runs check, wheels, shim, PyPI, then tag + GitHub release + Docker dispatch.

**What the packaging plan intends** (docs/packaging-plan.md, "Decided" 2026-11-14 and sections b, c, d): packs as FILES in a version-stamped folder inside the wheel (`<site-packages>/membrane/dist/{bin/cliffvm,packs,apps,ui,help}`), the wheel tagged `py3-none-<platform>` and built by setuptools-rust `Binding.Exec` (option A, first); defaults live in c1f (a serve app of packs and facts), `cliffvm serve` only finds the install folder and runs it, and the Python commands only execute it; Docker becomes the binary + folder (no Rust, no torch); version `0.7.60`; default port 4096, default bind 127.0.0.1; HA integration rebuilt later (the "frozen on babyseat==0.7.53" decision was answered NO). This is exactly the operator's "wrapper only" answer. NOT yet built: `cliffvm serve` does not exist (`native/src/cli.rs` has `run`, `token`, `--version`); no workflow builds the binary.

**What happens to existing users when the Python package is gone from this line** (the wrapper wheel exists, 0.7.60):

| user | action | result | note / risk |
|---|---|---|---|
| pip / uv tool, on a platform with a wheel | `pip install -U babyseat` | gets 0.7.60: the same two commands `membrane` / `babyseat` now start the Rust seat on 4096 | their old data in `<cwd>/.membrane/` is read-only history to the new seat (migration-design.md owns the import); numpy, tree-sitter, mcp, dateparser stay installed but unused (new `dependencies = []`); rollback `pip install babyseat==0.7.53` (PyPI keeps it) |
| pip on a platform WITHOUT a wheel (Windows, Termux/Bionic, a Python the platform tag misses) | same | pip falls back to the sdist; an sdist that cannot build the binary must FAIL LOUDLY at build time (not install an empty launcher) or build with cargo as today | decision 4 (which platforms); packaging plan lists none for Windows/Termux |
| `install.sh` users | re-run or `membrane update` | the old seat's own update path (`membrane/update.py`, UNVERIFIED how it behaves against the new wheel) | install.sh must be rewritten to pip-install the wheel (no Rust fetch) |
| `membrane-seat` (the dead name) | `pip install -U membrane-seat` | stays at 0.7.53 unless a 0.7.60 shim is published (pins `babyseat==0.7.60`, ships the wrapper's two files for the RECORD-overlap trick) | Decision 6 |
| Docker | `docker pull chedim/babyseat` (or `chedim/membrane`) | the new image: binary + folder, `ENTRYPOINT cliffvm`-style; `MEMBRANE_BACKEND/MODEL/EMBED_MODEL` env no longer map to flags; `:11500` healthcheck must become `:4096/global/health`; `/data` is still HOME | the old tag remains for rollback (`docker.yml` pushes `X.Y.Z`, `X.Y`, `latest`) |
| HACS / Home Assistant | update | nothing changes: `requirements: []`, the component talks to a seat by URL; `validate.yml` hacs + hassfest stay | the five `ha/*` door routes the component calls are still missing in `cliffvm` per packaging-plan (e): that is the HA rebuild, not this plan |
| patch-line (Python) users | `pip install -U babyseat` | lands on 0.7.60 anyway (0.7.54 < 0.7.60) | must pin; Decision 5 |

**Decision for the operator (asked, answered 2026-11-14): keep a tiny pip wheel that carries the binary (a distribution format, no seat logic), not drop PyPI.** The remainder of this plan assumes that. The census workflow (`census.yml`, PyPI download counts of `babyseat`) keeps working only while PyPI keeps the name.

## 3. What outside the Python world references Python things

| where | what it does with Python | change when the seat goes |
|---|---|---|
| `native/Cargo.toml` | `pyo3` dep, `python` feature (default!), `crate-type = ["cdylib","rlib"]`, `[lib] name = membrane_vm`; `store` has no Python | drop `pyo3`, `python`, `default = ["python"]` (make `default = ["bin"]` or empty), `cdylib`; the `bin` build (`--no-default-features --features bin`) is unchanged |
| `native/src/py.rs` (492), `native/src/db.rs` (334; gated `#[cfg(feature="python")]`, only used by `py.rs`: `grep ::db::`) , `lib.rs:26-31` | the PyO3 binding of the engine to the Python seat | delete both files and the two `cfg(feature = "python")` lines (a dead-code check: `cargo build --no-default-features --features bin` and `--features wasm` must stay green) |
| `native/.cargo/config.toml` | macOS `-undefined dynamic_lookup` for the extension | delete |
| `native/build.sh` | builds `membrane/membrane_vm.so` with `PYO3_PYTHON=~/.membrane/venv/bin/python` (the "native .so replace" story, memory #992) | delete; `.gitignore:84-85` (`membrane/membrane_vm*.so`) and `.dockerignore` (`membrane/*.so`) lines go with it |
| `native/build-web.sh` | wasm-pack to `membrane/ui/`; no Python | keep; add a CI drift check (packaging plan (d)) |
| `native/tests/contracts.rs:154-161` | reads `membrane/opencode.py` + `fields_live.py` and demands each wired fact name appears in them | rewrite FIRST: point at the new launcher's source of truth (the serve app packs / `cli.rs` facts it passes); until then it blocks deleting those files |
| `native/tests/*` (`session_events`, `settings`, `ha`, `seat_process`, `chat_process`, `seat_model`, `session`) | embed `#!/usr/bin/env python3` fake `claude` scripts | not the package: only a system `python3` at dev/CI time. Optional follow-up: rewrite as `sh` fakes so the repo has no Python at all (Decision 9) |
| `tests/js/{fieldvm_link,dom2,pointer,retire,shell}.test.mjs` | `spawn('python3', ['-m','http.server',...])`; `settings_page`/`shell_page` python3 fake claude | same: stdlib only; can become `node:http` later (Decision 9) |
| `tests/ha/realseat.py` | python3 fake claude | same; runs inside the HA test job which has Python anyway |
| `.github/workflows/validate.yml` | `tests` job (pip install -e ".[couchbase]", fetch-cbl, spaCy, pytest; MEMBRANE_TEST_STORE=sqlite subset), `native-vm` job (pytest of 19 files), `hacs`, `hassfest` | replace (section 5 step 2) |
| `.github/workflows/wheels.yml` | cibuildwheel of the PyO3 extension, `CIBW_TEST_COMMAND` imports `membrane_vm`; sdist | rewrite for the binary wheel (packaging round) |
| `.github/workflows/publish.yml` | `check` reads `pyproject.toml` version (+ shim pin), `wheels`, `shim` (copies `membrane/` into the shim), PyPI, tag + release from `membrane/CHANGELOG.md` (inline stdlib python3), dispatches docker | keep the skeleton; the `shim` job and the shim pin check go or change per Decision 6; the inline `python3 -` blocks are stdlib only (can stay) |
| `.github/workflows/docker.yml` | PR smoke runs `membrane --help` / `babyseat --help`; privacy check; publish reads `pyproject.toml` | rewrite smokes for the new entrypoint (`cliffvm --version`, `/global/health`); keep privacy check |
| `.github/workflows/census.yml` | stdlib python3 reading pypistats | keep while PyPI keeps `babyseat` |
| `install.sh`, `run.sh`, `Dockerfile`, `docker/`, `.dockerignore` | see section 1 | `run.sh` delete; the other four rewrite (packaging) |
| `scripts/fetch-cbl.sh`, `.gitignore` (`vendor/cbl/`) | Couchbase Lite for the Python store | delete with the script |
| docs | 44 of 60 `docs/*.md`, README.md, doctrine.md, AGENTS.md match `python|pip install|pytest|\.py\b|membrane (serve|standalone|opencode)` | the docs audit (`git show w103-docsaudit:docs/docs-audit.md`) owns the rewrite; rule for this plan: a doc that only describes the Python seat is deleted in the same PR that deletes the code it describes, and README/AGENTS (`pytest`, "suites live in tests/") are corrected in step 6 |
| `CITATION.cff`, `hacs.json` | version / HA min version | keep; version pinned by the rewritten version test |

## 4. The wrapper (what Python REMAINS) and the Python tests

### 4.1 Wrapper shape (for the packaging agent; nothing built here)

Files that stay or are added (about 80 lines of Python in total):

| path | content |
|---|---|
| `pyproject.toml` | `name = "babyseat"`, `version`, `requires-python = ">=3.10"`, `dependencies = []`, `[project.scripts] membrane = "membrane.launcher:main"`, `babyseat = "membrane.launcher:main"` (both names kept; today `babyseat` = `membrane standalone`, so the launcher argv default is `serve`), build backend setuptools(+rust, Binding.Exec) per packaging-plan (d) option A; `[tool.pytest]` `testpaths = ["tests/ha", "tests/wrapper"]`; package-data: `dist/bin/cliffvm`, `dist/packs/*`, `dist/apps/**`, `dist/ui/*`, `dist/help/*.md`, `CHANGELOG.md` (no skills, viewer, fonts, doctrine: they are deleted) |
| `setup.py` | only the Exec rust extension (cargo `--no-default-features --features bin`) and the copy of `membrane/{packs,apps,ui,help}` into the wheel's `dist/` (the source folders stay where they are); the wheel tag `py3-none-<platform>` |
| `membrane/__init__.py` | `__version__` only (read by the version test) |
| `membrane/launcher.py` | `main()`: find `dist/bin/cliffvm` relative to `__file__` (env `CLIFFVM` overrides, for the dev tree: `native/target/release/cliffvm`); `os.execv(bin, [bin, "serve", *sys.argv[1:]])` (packaging-plan decision 2: defaults live in c1f; `cliffvm serve` finds the install folder); if the binary is missing print one line and exit 1; the dead old subcommands (`opencode`, `review`, `ingest`, `backfill`, `move`, `toybox`, `self`, `shadow`, `store`, `connect`, `auth`, `doctor`, `bench`, `codegraph`, `erase-field`, `install`) print "not in this release; `pip install babyseat==0.7.53`" and exit 2 (packaging-plan (c) decision list); `--version` prints the wheel version. NO flags parsed, no defaults, no secrets, no store paths in Python. |
| `membrane/__main__.py` | `from .launcher import main; main()` (so `python -m membrane` works) |
| `membrane/dist/**` (build output, not committed) | binary + packs/apps/ui/help |
| `shim/membrane-seat/` | only if Decision 6 says keep: tiny, ships the same `membrane/__init__.py` + `launcher.py` (NOT the data), pins `babyseat==<version>`; else delete the dir and the `shim` job |

**How it finds/starts the binary:** `Path(__file__).parent / "dist" / "bin" / "cliffvm"`, execed so SIGTERM from `docker stop`/ctrl+c lands on the Rust seat (no child, no wrapper process). A dev checkout without a wheel uses `$CLIFFVM` or `native/target/release/cliffvm`.
**How a wheel per platform carries it:** one cibuildwheel row per platform (macOS arm64, macOS x86_64 cross-built, Linux x86_64/aarch64 glibc and musl), each builds the `bin` feature and tags `py3-none-<platform>`; install check = `cliffvm --version` plus `serve --port 0` against a temp data dir reading `/global/health` (packaging-plan (d)); the sdist must fail loudly (no empty launcher) or build with cargo.
**Existing names kept:** `membrane` and `babyseat` console scripts, the `membrane` import name, `.membrane/` data dir name (packaging decision 7), the HA domain `membrane` (brand only change at 0.7, no rename now).

### 4.2 Python tests: which remain, which are rewritten, which go

Remain / new (all small, stdlib + pytest, no seat imports):

| test | what | origin |
|---|---|---|
| `tests/ha/*` (13) | HA component | keep as is; add `test_ha_brand.py` (icon sizes; 17 lines, imports nothing) here |
| `tests/wrapper/test_launcher.py` (new) | argv forwarded, `execv` called with the packaged binary, `$CLIFFVM` override, missing binary message and exit 1, each dead subcommand exits 2 with the message, `--version` | new, ~60 lines |
| `tests/wrapper/test_wheel_layout.py` (new, CI wheel job) | the built wheel contains `dist/bin/cliffvm` (executable bit), the pack list equals `membrane/packs`, both console scripts, tag `py3-none-*` | new |
| `tests/wrapper/test_versions.py` | rewrite of `tests/test_version.py` (65 lines): pyproject = HA manifest version = `membrane/__version__` = shim pin (if kept) = README status line = newest `membrane/CHANGELOG.md` section; drop the "manifest pins membrane-seat" clause (no pin) | rewrite |
| `tests/wrapper/test_license.py` | keep `tests/test_license.py` (39 lines: LICENSE hash, pyproject, shim, README name the same license) | move |

Rewrite in Rust/JS or drop (the 13 that touch kept data/packaging, from `grep`):

| Python test | pins | verdict |
|---|---|---|
| `test_help_entities_1043` | help md shipped as `help:<topic>` entities | DROP: `native/tests/help_1143.rs` exists and tests every code block |
| `test_docker_packaging` (223) | entrypoint env mapping, allowlist `.dockerignore`, no baked ENV | REWRITE against the new Dockerfile/entrypoint (the allowlist and "no ENV with site values" rules are worth keeping: as a `sh`/Python text test next to the Dockerfile, or a docker.yml step) |
| `test_wheels_musl` (32) | the wheels workflow builds musllinux and a re-dispatch adds missing wheels | REWRITE for the new wheels.yml (musl row exists: HA container is Alpine) |
| `test_brand_303` (222) | babyseat brand, console scripts, shim pin, HA domain | KEEP only the console-script names + shim pin + HA domain slice, in `test_versions.py`; drop the rest (Python seat strings) |
| `test_readme_378`, `test_logo_368`, `test_sleep_doc_301` | README opening, logo files, a doc exists and is linked | DROP, or one `docs_pins` test after the docs pass (docs-audit owns README); `docs/logo` assets stay |
| `test_neutral_doctrine_1120`, `test_lean_planner_1120`, `test_doctrine_self`, `test_tool_docs`, `test_program_first_576`, `test_llm_entity`, `test_name_blurp_277`, `test_restarts`, `test_score`, `test_chat_goals_766`, `test_offbox_auth` (all read `membrane/doctrine.md` or Python planner text) | the Python seat's doctrine/planner | DROP. The release's equivalent (domain-neutral doctrine, #1120) would be a Rust/c1f text check of the packs: Decision 10 |
| `test_no_hardcoded_name`, `test_about_window`, `test_update`, `test_claude_agent_384`, `test_tool_recall_429`, `test_warm_restart_720`, `test_lex_snapshot_1115`, `test_rows_snapshot_1115`, `test_sleep_consolidate_345` | Python seat internals that mention manifest/pyproject/workflows in passing | DROP |
| 81 tests that import no `membrane` (`test_*_470`, `test_field_view_*`, `test_options_window_344`, `test_tree_look_361`, `test_picker_*`, ...) | text greps of `membrane/ui/*.js`/html for the OLD page | DROP with the old page; `tests/js/*.mjs` is the page's gate now |
| `test_golden_efield`, `test_fields_native_994`, `test_one_sqlite_0753` | Python-side goldens and engine glue | DROP (`native/tests` has the contract suites) |
| `tests/data/*.json` (3), `tests/fixtures/mcp_echo_server.py` | inputs of Python tests | DROP (no native/ha/js reader: `grep`) |
| `tests/golden/`, `tests/specs/` (+`fixtures/`) | read by `native/tests` | KEEP |

## 5. Ordered deletion plan (small safe steps)

Gates: **G-cargo** = `cd native && cargo test --no-default-features --features bin` (+ `cargo build --no-default-features --features wasm --target wasm32-unknown-unknown` once step 8 lands); **G-js** = `node` each `tests/js/*.test.mjs`; **G-ha** = `pytest tests/ha`; **G-ci** = the workflows below green on the PR (`gh pr checks`, AGENTS.md "ready = CI green"); **G-grep** = `git grep` for each deleted path/module name returns nothing outside history and this doc. Each step is one PR on `main`, squash, message ends `(#1143)`, no stacking (memory: no stacked PRs); the old pytest suite is not run (it is being removed).

| # | step | exact paths | gate | stays |
|---|---|---|---|---|
| 1 | **Preserve** (no deletion): annotated tag `python-seat-last` on the last `main` commit that still has the package; branch `release-0.7.5` from `v0.7.53` (carries the 0.7 patch line and its own old workflows; `publish.yml` already triggers on `release-*`); do NOT reuse the stale `release-0.7` (`d154a889`) | none | operator's go (nothing is created in this plan) | everything |
| 2 | **Add the release's CI first**, keep the Python jobs for now: job `cargo` (ubuntu + macos: `cargo test --no-default-features --features bin`), job `js` (node 26: `tests/js/*.test.mjs`, python3 for the http.server and fakes), job `ha` (`pip install pytest aiohttp` then `pytest tests/ha`; real-seat tests build `cliffvm` first or skip), job `wasm` (run `native/build-web.sh`, fail if `membrane/ui/fieldvm_core_bg.wasm` or `fieldvm_version.js` differ) | `.github/workflows/validate.yml` only | G-ci green with all new jobs | the old `tests`/`native-vm` jobs (removed in step 6) |
| 3 | **Cut the native test's coupling**: `contracts.rs:154-161` reads the new source of truth for "wired" facts (the serve app / `cli.rs`) instead of `opencode.py`/`fields_live.py`; regenerate nothing else | `native/tests/contracts.rs` (+ `membrane/packs/facts.json` if the `wired` flags move) | G-cargo (this test red first: TDD) | |
| 4 | **Delete the research and dead tooling** | `experiments/`, `benchmark/`, `.opencode/`, `run.sh`, `requirements.txt`, `pyrightconfig.json`, `BETA.md`, `.rd.lock` (after asking what writes it), `scripts/{golden_boot,golden_session,golden_efield,cb_migrate_field,build_release}.py`, `scripts/fetch-cbl.sh`, `scripts/fetch-voice-model.sh` + `build-voice-ml.sh` only if Decision 7 says so; add `tests/golden/README.md` ("recorded from the last Python seat, v0.7.53; frozen") | G-grep, G-cargo, G-js, G-ha, G-ci | `scripts/{seat,seat-full,load_test.sh}`, `scripts/measure_*.py`, `bench_system.py`, `token_growth.py`, `facts_throwy.json` (Decision 7), `tests/golden/`, `docs/` |
| 5 | **Rewrite the pins, then add the wrapper tests** (no seat code touched yet): new `tests/wrapper/{test_versions,test_license}.py` from `test_version`/`test_license`/`test_brand_303` slices; move `test_ha_brand.py` to `tests/ha/`; port `test_docker_packaging`/`test_wheels_musl` when their targets are rewritten (step 9: until then they stay) | `tests/wrapper/`, `tests/ha/test_ha_brand.py` | G-ha + the new tests, G-ci | the old tests still present |
| 6 | **Delete the Python seat's tests and the old CI jobs**: all of `tests/*.py` (656) incl. `tests/_*.py`, `tests/fixtures/mcp_echo_server.py`, `tests/data/`; `validate.yml` jobs `tests` and `native-vm`; `pyproject.toml` `testpaths` -> `["tests/ha", "tests/wrapper"]`; README "Ground rules"/AGENTS.md rule 1 (`pytest`) corrected to the new gates | `tests/*.py`, `tests/fixtures/`, `tests/data/` | G-cargo, G-js, G-ha, G-ci (new jobs are the only gate now) | `tests/{ha,js,golden,specs,wrapper}`, `native/tests` |
| 7 | **Delete the seat package, keep the launcher** (the launcher PR lands in the same PR so the console scripts never dangle): `membrane/*.py` (158) except new `__init__.py`, `launcher.py`, `__main__.py`; `membrane/{viewer,universe}.html`, `three.min.js`, `PressStart2P.woff2`, `VT323.woff2`, `doctrine.md`, `skills/`; `pyproject.toml` rewritten (4.1); `setup.py` reduced to the Exec build | see 4.1 | G-grep (`from membrane`/`import membrane` only in `tests/wrapper` and the launcher), G-cargo, G-js, G-ha, `tests/wrapper` launcher test, G-ci | `membrane/{packs,apps,ui,help,CHANGELOG.md}` |
| 8 | **Strip the PyO3 binding**: `native/src/{py,db}.rs`, `native/.cargo/`, `native/build.sh`; `Cargo.toml` loses `pyo3`, `python`, `default = ["python"]`, `cdylib`; `lib.rs` loses the 2 cfg lines; `.gitignore`/`.dockerignore` `.so` lines | as listed | G-cargo, wasm build, G-ci | `native/{src,tests,packs,Cargo.*,build-web.sh}` |
| 9 | **Packaging round (owned by the packaging agent, per docs/packaging-plan.md)**: `wheels.yml` for the binary, `publish.yml` shim/check changes (Decision 6), `docker.yml` + `Dockerfile` + `docker/` + `.dockerignore`, `install.sh`; then port `test_docker_packaging`/`test_wheels_musl` and delete the Python versions | those files | the packaging plan's gates (clean-env install of every wheel answers `/global/health`) | |
| 10 | **Docs sweep** per the docs audit: delete docs that only describe the Python seat, fix README/AGENTS/doctrine references; `grep` list in section 3 | `docs/*`, `README.md`, `AGENTS.md` | G-grep, docs-audit checklist | |

Steps 1-8 need no wheel and no `cliffvm serve`; the launcher in step 7 may point at `cliffvm run` via `scripts/seat-full` semantics until `cliffvm serve` lands, or ship a "binary missing" stub on `main` (the tree is not releasable until step 9 either way).

**CI that remains** (proposed): `validate.yml` = `hacs`, `hassfest`, `cargo` (ubuntu+macos), `js`, `ha`, `wasm`, `wrapper` (the launcher and version pins); `wheels.yml` = binary wheels per platform + sdist + clean-env smoke; `publish.yml` (version-bump push to `main` or `release-*` runs check, wheels, PyPI, tag + GitHub release from `membrane/CHANGELOG.md`, Docker dispatch); `docker.yml`; `census.yml`.

**What stays, explicitly:** `native/` (Rust engine, `cliffvm`, tests, `build-web.sh`); `membrane/{packs,apps,ui,help,CHANGELOG.md}` + the wrapper's three files; `custom_components/membrane/`, `tests/ha/`, `hacs.json`; `tests/{js,golden,specs}`; `scripts/{seat,seat-full,load_test.sh}` (+ Decision 7 items); `pyproject.toml` (rewritten), `setup.py` (reduced), `install.sh`/`Dockerfile`/`docker/`/`.dockerignore` (rewritten), `.github/workflows/*`; `docs/`, `README.md`, `doctrine.md`, `AGENTS.md`, `CHANGELOG.md`, `CITATION.cff`, `LICENSE`, logos/gifs.

## 6. Size, risk, and the repo afterwards

| step | rounds | risk |
|---|---|---|
| 1 preserve | 0.5 | none; do it first, it is the only undo |
| 2 new CI | 2 (macOS cargo time; ha job and real-seat skip rules) | medium: first time the Rust suite runs in CI (flaky tests under load: `scripts/load_test.sh` exists for that) |
| 3 contracts | 1 | low |
| 4 research/tooling | 1 | low (grep proves unused) |
| 5 pins | 2 | low |
| 6 old tests + jobs | 1 | medium: 98k lines gone at once; mitigated by step 2 being green first |
| 7 package + launcher | 2 | medium: `membrane/` folder keeps its name (Decision 3); nothing but `packs/apps/ui/help` may be read from it |
| 8 PyO3 | 1 | low-medium: a hidden `python`-feature `cfg` (grep found only `lib.rs:26,30`) |
| 9 packaging | 14-22 (packaging-plan (i)) | the release's own risk |
| 10 docs | per docs audit | low |
Retirement itself: about 10-12 rounds without the packaging round. Total Python lines removed: about 203,500 of 204,479 (what stays: 907 HA + 1,809 HA tests + ~80 launcher + ~150 wrapper tests, plus the stdlib dev scripts of Decision 7).

Tree afterwards (sketch):

```
.github/workflows/{validate,wheels,publish,docker,census}.yml
custom_components/membrane/          HA thin client (Python)
docs/  scripts/{seat,seat-full,load_test.sh,measure_*.py?}
membrane/                            the wrapper package + data
  __init__.py launcher.py __main__.py CHANGELOG.md
  packs/ apps/ ui/ help/
native/                              Rust: cliffvm, engine, tests, build-web.sh
shim/membrane-seat/                  only if Decision 6 keeps it
tests/{ha,js,golden,specs,wrapper}/
Dockerfile docker/ install.sh pyproject.toml setup.py hacs.json
README.md doctrine.md AGENTS.md CHANGELOG.md CITATION.cff LICENSE
```
Git history keeps everything: `git show python-seat-last:membrane/server.py`, branch `release-0.7.5` for patches.

## 7. Decisions for the operator

1. Preserve names: tag `python-seat-last` on the last `main` commit with the package, branch `release-0.7.5` from `v0.7.53` for the 0.7 patch line; leave the stale `release-0.7` branch alone [yes].
2. Order: add the Rust/JS/HA CI (step 2) before deleting any Python test [yes].
3. Keep the data folder name `membrane/` (packs, apps, ui, help) and the Python import name `membrane` for the wrapper, since native tests include `../../membrane/packs` and users keep `membrane` [yes]; a rename is a separate PR.
4. Platforms with no wheel (Windows, Termux, a platform tag we do not build): sdist builds with cargo, or fails loudly with "no binary for this platform" [loud failure; no cargo in the sdist].
5. Patch line vs the wrapper: patches `0.7.5N` rank BELOW `0.7.60`, so only pinned installs reach them [accept; say so in the release notes].
6. The `membrane-seat` shim: publish one last `0.7.60` shim (tiny, ships the wrapper files for the RECORD-overlap trick) [yes, once] or delete `shim/` and the `shim` job now.
7. Scripts: keep `measure_*.py`, `bench_system.py`, `token_growth.py`, `facts_throwy.json` as stdlib dev tools for the Rust seat [keep]; voice asset scripts (`fetch-voice-model.sh`, `build-voice-ml.sh`) and who builds the page's voice files now [your call].
8. Delete `BETA.md`, `.rd.lock` (needs your word on what it is), `membrane/doctrine.md` (the root `doctrine.md` stays), `membrane/skills/` [yes].
9. System `python3` in tests (fake `claude` scripts, `http.server`): leave for now [leave]; a later PR makes them `sh`/`node` so the repo has no Python except HA and the wrapper.
10. Doctrine/neutrality pins (#1120): a Rust or c1f check of the shipped packs replaces the dropped Python doctrine tests [yes, small], or drop them.
11. `tests/golden/` stays as frozen recordings of v0.7.53 (the oracle for `native/tests`) with the generating scripts deleted [yes].
