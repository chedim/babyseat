# Packaging and the launcher of the release swap (scoping note, #1143, 2026-11-14)

Docs only: nothing built, nothing downloaded, nothing run. Tree: `memo-hotfix-0753` at `e3cc85db`. Sources: `pyproject.toml`, `setup.py`, `native/build*.sh`, `scripts/seat`, `scripts/seat-full`,
`.github/workflows/{wheels,publish,docker,validate}.yml`, `Dockerfile`, `docker/docker-entrypoint.sh`, `install.sh`, `membrane/cli.py`, `custom_components/membrane/`, docs/cliffvm.md, docs/auth.md,
docs/release-parity.md (`RP`), docs/migration-design.md (`MD`). "Round" = one agent round (20-45 min). "UNVERIFIED" = not run or not seen in the tree. Every number, default, platform and layout choice is a DECISION (section 9); I chose none.

## 0. Important first

1. **A "thin Python wrapper over the binary" breaks Home Assistant on the day it ships.** HA's integration does not talk to a seat over HTTP only: it `pip install`s `babyseat==<its version>` (`manifest.json`) and spawns
   `python -m membrane.opencode --serve ...` as its own child (`supervisor.py:81-92`, `:137`). A new wheel with no `membrane.opencode` leaves every HACS user with a dead integration after their next HA update.
   Recommendation: the release's `custom_components/membrane` stays FROZEN with `requirements: ["babyseat==0.7.53"]` (the old wheel stays on PyPI), so HA keeps running the old Python seat in its own venv.
   Cost: 1 round (and the version guard in `tests/test_version.py` that ties manifest to pyproject must learn the exception). Section 6.
2. **Nothing in the repo today builds or ships the `cliffvm` binary.** `wheels.yml` builds only the PyO3 extension `membrane_vm`; `cliffvm` is built by hand (`cargo build --release --no-default-features --features bin`).
   There is no wasm workflow either (`native/build-web.sh` is run by hand and its output committed under `membrane/ui/`). CI changes: section 5. Total packaging build 14-22 rounds (section i), plus the migration's own (MD: 12-18) and the release gates.
3. **The launcher does not exist yet as a user command.** `scripts/seat-full` is the only full-set launch line and it is a dev script run from a checkout (it reads `membrane/packs`, `membrane/ui` relative to the repo root). The first piece to decide is where the shipped packs live relative to the binary (section 3).
4. **What users see first is the login and the data.** `.membrane/jwt.secret` reuse (MD decision 12) is untested; if it fails every browser and token is logged out. Test it before anything else in the upgrade path (round R8 of MD).
5. Windows: no CI job, no wheel, no mention in README/pyproject; the old release never shipped for Windows (UNVERIFIED that it ran there). Recommend: none in the first release.

## (a) What a user does today, and what must keep working

| step | today | file / port / env |
|---|---|---|
| install | `pip install babyseat` (a compiled abi3 wheel: macOS arm64+x86_64, Linux glibc+musl x86_64+aarch64; sdist elsewhere), or `curl ... install.sh \| bash` (uv tool, else `~/.membrane/venv`, links `~/.local/bin/{membrane,babyseat}`), or HACS, or `docker run chedim/babyseat` | `~/.membrane/` (venv, `auth.json` cloud keys, `selfupdate.log`), per-project `<cwd>/.membrane/` (field store, `jwt.secret`, `settings.json`) |
| start | `babyseat` (= `membrane standalone`) or `membrane serve` / `membrane opencode` | serve + UI `:4096`, OpenAI/Ollama proxy `:11500`; both walk up to the next free port unless `--serve-port` / `--listen` is passed (pinned); listen on all interfaces (docs/auth.md) |
| auth | JWT on by default: `<cwd>/.membrane/jwt.secret` (made, 0600); the page served to this machine gets an owner cookie; `membrane token mint --sub ID`; `MEMBRANE_JWT=0` = seat-token mode | `MEMBRANE_JWT`, `MEMBRANE_JWT_SECRET[_FILE]`, `MEMBRANE_OPERATOR` |
| upgrade | `pip install -U babyseat` / `uv tool upgrade` / `membrane update` (a git pull, re-exec) / the operator's own autolatest wrapper (`~/.local/bin/membrane` runs `seat-self-update` first: a checkout ff, not a user feature); Docker: pull the tag | boot update check (`MEMBRANE_NO_UPDATE_CHECK`) |
| stop | ctrl+c, `membrane stop` (INT, bounded TERM, KILL on the serve ports' listeners + the pidfile), `docker stop --stop-timeout 60` (teardown lease up to 90 s) | |
| Docker | entrypoint maps `MEMBRANE_BACKEND`, `MEMBRANE_MODEL`, `MEMBRANE_EMBED_MODEL` to `--backend/--model/--embed-model`, pins `--listen 11500 --serve-port 4096 --serve-host 0.0.0.0`, volume `/data` = HOME, user 10001, healthcheck `:11500/membrane/state` | `docs/docker.md` |
| model | local Ollama `--backend`, cloud keys via `membrane auth`, claude via the Agent SDK | |

**MUST keep working** ("users do not notice"), my list, each needs a test in section 8:
`babyseat` and `membrane` commands exist and start the seat on `:4096`; `membrane stop`; `pip install -U babyseat` upgrades in place; `.membrane/` in the cwd is still where data lives; the existing login still works (secret reuse);
the Docker image still runs `docker run -p 4096:4096 -v data:/data` and answers its healthcheck; `--serve-port`, `--serve-host`; `ANTHROPIC_API_KEY` is the chat default (release gate).
**Knowingly changing** (already decided or recommended in RP): the `:11500` proxy (RP D3: keep, after the swap), the seat-token mode and `MEMBRANE_JWT=0` (RP D5: JWT only), `membrane auth` cloud keys and Ollama (RP D1, 1g), peers, quota, Blurp/self/toybox (RP D4, D10: out).
The Healthcheck in the Dockerfile reads `:11500/membrane/state`: it must move to `:4096/global/health` if `:11500` is not served (a one-line change, in the image round).

## (b) What ships

| artifact | size (measured `ls -l`, 2026-11-14) | notes |
|---|---|---|
| `cliffvm` release binary (macOS arm64, no `metal`, `--features bin`) | **6,423,040 bytes (6.1 MiB)**; the other worktrees' builds 6.24-6.51 MB. The debug build is 18.6 MB (never shipped) | per platform; section 5. The Metal top-k (`metal` feature, today on in the Python extension on macOS) is NOT in the `bin` build: recall speed on macOS is a separate question (RP D8, phase 2) |
| `membrane/packs/` (`*.c1f`, `boot.json`, `facts.json`) | 492 KB, ~40 packs; the barest list is `membrane/apps/barest.packs` | `cliffvm run membrane/packs/boot.c1f membrane/packs`: boot is the root of trust |
| `membrane/apps/` | 156 KB | `barest.c1f` app (the page + collector + help) |
| `membrane/ui/` | 8.0 MB, of which `vosk.js` 5.5 MB (voice), `three.min.js` 592 KB | `--ui-dir`; served by the door |
| wasm | `fieldvm_core_bg.wasm` **1,099,348 bytes** + `fieldvm_core.js`, `fieldvm_version.js` | built by `native/build-web.sh` (wasm-pack), committed in `membrane/ui/` |
| Help | `membrane/help/*.md` 84 KB and `help/*.md` of the app | read as `Help:<topic>` entities (every code block is a test, `help_1143.rs`) |
| skills, `doctrine.md`, `CHANGELOG.md`, fonts | small | `membrane/skills/*`, `*.woff2` |
| NOT shipped | `tests/golden/{boot,efield,session}`, spec snapshots, `native/tests`, `native/packs` (compiled in: `recall-policy.json`, `scripts.json`, `goalgraph.*` via `include_str!`) | tests only |

Total payload about 15 MB uncompressed (binary 6.4 + ui 8.0 + packs/apps/help 0.7); vosk.js is 37% of it.

**How the binary finds its packs: embedded vs files.**
- *Files* (today): `cliffvm run <paths>`. Explicit, greppable, the model reads them with `fs`, Pack entities replace a file's pack (`Pack:<name>` loads later than the folder; a restart loads folder first, then every stored `Pack:*`), the page is a folder anyway (`--ui-dir`). Cost: the launcher must name a location, and a pip upgrade replaces the folder.
- *Embedded* (`include_dir`/tar in the binary): one file, no path to get wrong, the binary's version = its packs' version (no skew). Cost: the model cannot read or diff a shipped pack from disk (it would need a `Pack` row per shipped pack, or a getter), the binary grows (0.5 MB packs + 8 MB ui if the page is embedded too), every pack edit is a rebuild. The explicit-construct preference and "the model edits packs" both point at files.
- Recommendation: **files in a version-stamped folder next to the binary inside the wheel** (`<site-packages>/membrane/dist/{bin/cliffvm,packs,apps,ui,help}`), the launcher passing explicit paths (same argv as `seat-full`); the binary prints the packs folder it loaded as a fact (`packs_dir`, so the model can `fs`-read them). Embedding stays an option for the Docker/HA-add-on single-binary case, later.
- **Risk to settle (UNVERIFIED):** a model-written `Pack:door` row survives an upgrade and loads AFTER the new shipped `door.c1f`, shadowing it (rule: a Pack entity of the same name replaces the file's pack). An upgrade that changes a shipped pack the user's seat overrode will silently keep the old one. Needs a decision: stamp each Pack row with the shipped version it overrode and show a note, or refuse shadowing a pack of a newer version. 1-2 rounds.

## (c) The launcher

**Python launch path today** (`cli.py`, `opencode.py`): `membrane <cmd> [forwarded args]`; `standalone` = serve + UI + browser open; flags `--serve-port` (4096), `--serve-host`, `--listen` (11500), `--listen-host`, `--backend`, `--model`, `--embed-model`, `--field`, `--audit`, `--no-absorb`, `--no-launch`; env above.

Mapping table (old option -> new fact or flag; a fact name marked * is from `scripts/seat-full`, the rest I propose and the loader would have to accept):

| old | new |
|---|---|
| `--serve-port N` | `--port N` (the fact `door_port`; 0 = any free port). Old walk-to-next-free-port: not in `cliffvm` (UNVERIFIED); pinned only |
| `--serve-host` / `MEMBRANE_LISTEN_HOST` | `--bind <addr>` (default `127.0.0.1` in `http.rs`; old default was all interfaces: a DECISION, it changes who can reach the seat) |
| `--listen N` (:11500) | none: RP D3 (a door family `v1`, after the swap) |
| `--backend`, `--model`, `--embed-model`, `MEMBRANE_BACKEND/MODEL/EMBED_MODEL` | `--fact seat_backend=<url>*`, `--fact minds=<json>*`, `--fact cloud_models=<json>*`; embed model: none yet (recall embedder is phase 2, UNVERIFIED) |
| `<cwd>/.membrane/field.sqlite`, `MEMBRANE_DATA_DIR` | `--store <data>/cliffvm.sqlite3` + `--fact seat_data_dir=<data>*` (+ legacy `sql_sources` for the history import, MD section 6) |
| `.membrane/jwt.secret`, `MEMBRANE_JWT_SECRET[_FILE]` | `--fact door_secret_file=<path>*` (made 0600 if missing; MD decision 12: reuse the old file) |
| `MEMBRANE_JWT=0` | dropped (RP D5) |
| `MEMBRANE_OPERATOR` | `--fact operator=<sub>` (default `owner`) |
| owner token for local tools | written at every boot to `<data>/owner.token` (0600), the fact `owner_token_file`; was: the seat token / `membrane token seat` |
| `membrane token mint` | `cliffvm token mint --sub --ttl --secret-file [--name]` (exists) |
| claude: `membrane auth claude-agent`, Agent SDK | `ANTHROPIC_API_KEY` in the env (default), `CLAUDE_AUTH=login` -> `--fact chat_auth=login`, `CLAUDE_BIN`/PATH -> `--fact chat_claude_bin=<path>`; no key and no login: the chat answers with how to set it |
| `--open` / browser launch, `MEMBRANE_NO_BROWSER` | `--open` in the launcher (`open`/`xdg-open`), off in Docker |
| `membrane stop` | no pidfile in `cliffvm` (UNVERIFIED); SIGTERM/SIGINT end it cleanly (`host.rs`); the launcher writes `<data>/seat.pid` |
| `membrane update` | `pip install -U babyseat`; the git-pull flavour is dev only |
| `--field/--audit/--no-absorb/--no-launch` | none (internal) |

**Options.** (1) A thin Python `membrane`/`babyseat`: ~30 lines, finds the packaged binary, builds argv/facts, `os.execv`. Cons: the defaults (facts, paths, secret and token handling) live in Python, a second copy of what the binary must also know, and "Python = wrapper only" stays true only if it stays 30 lines.
(2) A native `cliffvm serve` subcommand holding every default (data dir, secret file, claude discovery, ports, `--open`, pid file), the only logic; usable in the HA container, Docker and Termux with no Python at all. Cons: Rust code for what `scripts/seat-full` does in 80 lines of sh; needs its own tests (`seat_full_1143.rs` is the template).
(3) Both: the native subcommand has the logic, the Python entry points are `execv(cliffvm, ["serve", *sys.argv[1:]])`, and `scripts/seat*` become tests of it.
**Recommendation: (3).** One source of truth in Rust (tested without Python), the two old command names kept by a trivial exec. Cost: `cliffvm serve` 3-4 rounds (red tests port `seat_full_1143.rs`: secret made and kept, mode 0600, a fact wins, token path printed, claude discovery, `--open`, pidfile, `stop`), Python shims 1 round.
`membrane serve|standalone|stop|token` map; every other old subcommand (`opencode`, `review`, `ingest`, `backfill`, `move`, `toybox`, `self`, `shadow`, `store`, `connect`, `auth`, `doctor`, `bench`, `codegraph`, `erase-field`, `install`) is DEAD in the wrapper: each must print "not in this release" and exit 2, not vanish. A DECISION list (section 9).
Claude discovery: `CLAUDE_BIN`, else `claude` on PATH, else the chat says how (exists, `chat_turn.c1f`). Port default: `4096` (continuity; `seat-full` uses 4310 only so it does not collide).

## (d) Wheels, Docker, signing

**Today:** `setup.py` uses `setuptools_rust.RustExtension("membrane.membrane_vm", binding=PyO3, features=["python"] + ["metal" on macOS], py_limited_api="cp310")`; `wheels.yml` runs cibuildwheel (cp310 abi3) for linux x86_64/aarch64 manylinux and musllinux, macOS arm64 and x86_64 (cross-built, no test), plus an sdist; `publish.yml` builds, uploads with `PYPI_TOKEN`, then tags and makes the GitHub release from the CHANGELOG, then dispatches Docker; the membrane-seat shim copies the module and pins `babyseat==ver`.

**Options for a wheel with the binary:**
- A. **Keep setuptools-rust**, switch the target to `Binding.Exec` (builds the `[[bin]]`; installs it as a script, UNVERIFIED where cibuildwheel places it) and tag the wheel `py3-none-<platform>` (no Python ABI: the binary is not an extension). Smallest diff to `setup.py`/`wheels.yml`; the same cibuildwheel matrix, musl flags already solved there (`-crt-static` is for the cdylib: a bin can link musl statically, which is better for Alpine, UNVERIFIED).
- B. **maturin** with `bindings = "bin"` (the way ruff/uv ship): per-platform `py3-none-` wheels, manylinux/musllinux via `maturin-action`/zig, universal2 option for macOS. Cleaner tags, replaces setuptools-rust; the packs/ui must come in as `include`d data (a second tool to learn, UNVERIFIED for abi-less packaging of a large data folder).
- C. A custom `build_ext` running `cargo build` and copying to `membrane/dist/bin/`; hand-rolled tags. Most code, least dependence.
**Recommendation: A first** (one rung of change, proven matrix), reviewed against B in round 1 of the build with a throwaway tag build. Because the wheel no longer depends on a Python ABI, the cibuildwheel `CIBW_BUILD` becomes one cp310 build per platform just as now; the install check becomes `cliffvm --version` instead of `import membrane_vm`.
musllinux for HA: the matrix row exists; the binary links rustls (`tls` feature) and rusqlite (`bundled`? UNVERIFIED), no OpenSSL is expected; a musl build of the `bin` feature has NOT been run (UNVERIFIED), do it first: 1 round, 4 targets.
Platforms: macOS arm64, macOS x86_64 (cross-built; no run), Linux x86_64 + aarch64 glibc, Linux musl x86_64 + aarch64 (HA container, Alpine, Termux is Bionic: neither wheel fits, see risks), Windows none.
**CI changes** (`wheels.yml`, `publish.yml`, `validate.yml`): the PyO3 extension steps go (or stay for the 0.7 patch branch); cargo build the `bin` feature per platform; wasm: add a job that runs `native/build-web.sh` and fails if the committed `membrane/ui/fieldvm_core_bg.wasm` differs (today nothing checks it); a smoke job installs each wheel in a clean env and runs `cliffvm serve --port 0` against a temp data dir, reads `/global/health`; the existing `validate.yml` pytest of the Python seat is replaced by `cargo test --no-default-features --features bin` (not in any workflow today: UNVERIFIED that CI runs `native/tests`) and `tests/js/page_boot.test.mjs`. 3-4 rounds.
**Docker:** the build stage disappears (no Rust in the image if the wheel is prebuilt: `pip install` the wheel) or the image becomes `FROM debian-slim` + the dist folder + `cliffvm` as ENTRYPOINT; the entrypoint's env mapping moves into `cliffvm serve` (`MEMBRANE_BACKEND` etc. are Python-era; keep `MEMBRANE_DATA_DIR`-style names as facts only if decided). The image today installs torch for the reranker (hundreds of MB): the native seat has no reranker, so the image should shrink by ~1 GB (UNVERIFIED, not measured). Ports 4096; `:11500` drops with D3; `--stop-timeout 60` stays a note. 2 rounds; the `chedim/membrane` mirror keeps its tags.
**macOS Gatekeeper (what I know / UNVERIFIED):** a binary inside a pip wheel is written by pip, not downloaded by a browser, so it carries no `com.apple.quarantine` attribute and Gatekeeper does not prompt on exec; this is why unsigned `ruff`/`uv` wheels run (UNVERIFIED by me: I did not test). On Apple silicon the kernel refuses an unsigned arm64 binary; the linker's ad-hoc signature (rustc on macOS does it) is enough to run (UNVERIFIED for our exact build flags). A binary downloaded from the GitHub release page in a browser IS quarantined and needs notarization (an Apple Developer ID, $99/yr, `codesign` + `notarytool`, stapling does not apply to a bare file: ship in a zip or `.pkg`): recommend NOT offering a bare binary download in the first release; only wheel, Docker and `install.sh`. Known hazard from the repo history: overwriting a signed `.so` in place made macOS kill importers (`native-so-replace`): the installer and the launcher must write a NEW file and rename, never copy over a running binary (pip does).

## (e) Home Assistant

Out of the first release (operator: "rebuild hass later"), but it must not break. What the integration calls (all in the tree: `coordinator.py`, `config_flow.py`, `supervisor.py`, `conversation.py`, `ha_access.py`, RP rows 5, 14, 16, 24, 32, 41, 75, 76):

| call | by | cliffvm status (RP, 2026-11-13/14) |
|---|---|---|
| GET `/global/health` | readiness + watchdog (`supervisor.py:168`), config flow | PRESENT (needs the JWT now: the 401 is `Bearer realm="membrane"`) |
| GET `/membrane/state`, `/agent` | metrics poll (`coordinator.py:44-46`) | PARTIAL: `state` reads `membrane::presence` which nothing writes (empty values); `/agent` the five released rows |
| POST `/session`, POST `/session/{sid}/message` | the conversation agent | PRESENT / PARTIAL |
| GET `/ui/conf` | `zoo.py` | PARTIAL (`auth` hard `"jwt"`) |
| POST `/membrane/ha/token`, `/ha/states`, `/ha/catalog`, `/ha/mqtt`, GET `/ha/subscriptions` | `ha_access.py` | MISSING (row 75, 404) |
| GET `:11500/api/tags` | config flow's model list | MISSING (RP D3) |
| spawn `python -m membrane.opencode --serve ...` on free ports 18096/18500, `MEMBRANE_LOOPS_MAX`, `HASS_URL`/`HASS_TOKEN` in env | `supervisor.py` | N/A: this is the Python seat. No equivalent child in cliffvm |
| header: HA sends no JWT (open surface assumed) | `supervisor.py:150` | breaks against a door with a secret; needs a token in the integration |

So **against cliffvm it would not work today**: the five `ha/*` routes are missing, it has no token, and "connect mode" (`serve_url`) would need the owner token. **Pin or disable, recommendation:** freeze `custom_components/membrane` and `integrations/homeassistant/custom_components/membrane_ai` in the release tree with `requirements: ["babyseat==0.7.53"]` and `version` of the integration unchanged, so HACS users get the same behaviour; document in the release notes that the HA integration runs its own pinned 0.7.53 seat until the rebuild; the Python 0.7 code stays in a `release-0.7` branch and the integration's package guard (`package_found`) is untouched. Alternative (disable): the integration refuses to start with a clear log line; worse for users. Check before the release (HACS, UNVERIFIED): that a release whose tag carries the frozen integration keeps HACS from offering an update that installs an incompatible seat; and `hacs.json` / `manifest.json` still pass the HACS validate job (`validate.yml`). The ThinkPhone/agDroid seats (Termux, aarch64, Bionic) are Python seats and stay on 0.7.5x (no wheel for them; the binary for Bionic is not built and not planned here).

## (f) The upgrade path for an existing Python user

1. Read the release notes (they say: data is kept, how to roll back). `membrane stop` or ctrl+c; confirm idle (`GET /session/status`, the restart rule; Docker: `docker stop --stop-timeout 60`).
2. Snapshot `<cwd>/.membrane/` (`cp -c` on APFS, `cp -a` elsewhere); `MD` step 3 records sha256 + table counts into `legacy.v0753/ledger.json`. The launcher on first start does this itself and refuses to go on without free disk for the copy (a number: DECISION).
3. `pip install -U babyseat` (the wheel now carries the binary; `uv tool upgrade`; Docker: pull the new tag and run on the same `/data`).
4. First start: `babyseat` (the new launcher, same command) sees an old store and no new one, passes `--fact sql_sources='[{"name":"legacy","path":".membrane/legacy.v0753/field.sqlite3"}]'` and `door_secret_file=.membrane/jwt.secret` (MD section 6; history converts on touch; `MD` R1-R8). The user's browser cookie and tokens keep working only if the secret reuse passes its test (UNVERIFIED; MD risk 9).
5. Rollback: stop; `pip install babyseat==0.7.53` (wheel on PyPI forever); start; the old store was never written. After new writes, `cliffvm legacy reverse` (MD section 3, 2-3 rounds; named but not built). Docker: the old tag.
6. The autolatest door (the operator's wrapper: ff main before every launch) is a developer tool: against a checkout it would also have to build the binary (`cargo build`) or it runs a stale one. It is not part of the release; the operator's wrapper needs a "rebuild if native/ changed" step (its own `pyproject.toml`-changed check already exists). 1 round, operator-side.
What is lost at step 3 for users of the dropped items (Ollama, cloud keys, peers, quota, Blurp): the release notes say so. RP D1 decides whether Ollama users may upgrade at all.

## (g) Versioning and the release process

- **Number.** The line is `0.7.5x` patches (never four parts; 0.7.60 is the planned next minor; `0.7.51 > 0.7.6` to pip). The swap is a new architecture with a changed install artifact and dropped features: recommend the next minor `0.8.0` over `0.7.60` (a minor in the pre-1.0 sense carries the break; the "release by rungs" ladder says 0.8 = Blurp Lv 8, which this release leaves out (RP D10), so that rule needs the operator's override). DECISION. The HA frozen pin keeps working whatever the number.
- **Mechanics (current):** merging a PR that changes `pyproject.toml`'s version on `main` (or a `release-*` branch), with no tag, runs `publish.yml`: `check` (tag = version, shim pin), `wheels`, `shim`, PyPI upload (`skip-existing`), tag + GitHub release (notes = the CHANGELOG section `## vX.Y.Z`), Docker dispatch. Manual: `gh workflow run publish.yml -f tag=vX.Y.Z` is blocked by the agent classifier: hand the operator a `! gh workflow run ...` line. HACS reads the GitHub release, so PyPI must have the pin first (the lane orders it right). CHANGELOG is edited only at release time (memory rule); Zenodo/CITATION.cff bumped with the version (`tests/test_version.py`).
- **Gates** (operator): (1) API-key default is merged and the docs say so (done w45: `ANTHROPIC_API_KEY`, `chat_auth=login` opt-in); (2) the history import ran on a real copy: counts ledger + golden shape diff (MD R7); (3) a real-browser check: `node tests/js/page_boot.test.mjs` plus a human pass over the nine views; (4) the full review of `main` (merge-scope check: no out-of-scope deletions); (5) the server-swap contract harness green (RP section 7); (6) every wheel installs in a clean env and answers `/global/health`; (7) the operator's go.
- **What the operator runs by hand (`!` lines):** the publish dispatch if the lane does not run by itself; the GitHub release creation; any `sudo`-free local deploy of the seats (Rosie 4096/11501 and main 4097/11500 get the binary too, one at a time, quiet gate, `PYTHONSAFEPATH` rules gone with Python); nothing needs sudo.

**Release-day CHECKLIST (all UNCHECKED until the day):**
1. [ ] Scope frozen; labels "fix before release" checked; `main` reviewed (diff scope, no silent reverts).
2. [ ] Version bumped in `pyproject.toml`, shim `shim/membrane-seat/pyproject.toml` (+ `babyseat==ver`), `CITATION.cff`, README badge; CHANGELOG section written; frozen HA manifest unchanged (test exception).
3. [ ] `cargo test --no-default-features --features bin` and `pytest tests/` green locally and in CI (`gh pr checks`), `page_boot.test.mjs` green with Chrome.
4. [ ] `wheels.yml` run: six platform wheels + sdist, each installed clean, `cliffvm --version` and a `serve --port 0` health read; the wasm is the committed one.
5. [ ] Rehearsal: old store copy -> new seat on a second port, history counts match, secret reuse test passed, rollback rehearsed.
6. [ ] Docker: `docker build`, smoke (`--help`, health), privacy check (no `.membrane`, no jsonl, no `serve-auth.json` in the image), amd64 + arm64.
7. [ ] Operator go; merge the release PR (squash); the lane runs; watch `publish` and `docker` to green.
8. [ ] Verify: clean venv `pip install babyseat==X`, `membrane --version`, health; `docker run chedim/babyseat:X`; `chedim/membrane:X` mirror; the shim `membrane-seat` upgrades to babyseat X.
9. [ ] GitHub release notes say: data kept, rollback command, what is not in this release (Ollama?, cloud keys, peers, quota, Blurp, HA pinned).
10. [ ] HA: the frozen integration still installs 0.7.53 in a test HA; HACS shows no broken update.
11. [ ] Deploy the operator's seats one at a time; Memo informed by PR comment (never its chat).

## (h) Risks and unknowns

| risk / unknown | resolved by |
|---|---|
| `membrane.opencode` gone breaks the HA child | freeze the integration pin (section e); test in a throwaway HA | 
| `jwt.secret` reuse logs users out (claims shape of Python-minted tokens vs `jwt.rs`) | MD R8 test, first; fallback: a one-time re-login page |
| `bin` feature on musl, aarch64 Linux, Intel macOS: never built | build matrix round, before any other packaging work |
| `Binding.Exec` + cibuildwheel placing and tagging a non-extension binary | throwaway tag build (round 1); fall back to maturin |
| Gatekeeper / ad-hoc signature on the wheel-installed binary; Apple notarization if ever offered as a download | a test on a clean Mac by the operator (agent may not run it: no installs); notarization needs the operator's Developer ID |
| Model-written `Pack:` rows shadowing upgraded shipped packs | design decision in section (b); a test with a stale override |
| Old default bind was all interfaces, `cliffvm` binds loopback: HA/phone/LAN users lose access silently; or the reverse: opening `0.0.0.0` with a JWT gate is a security call | operator decision; docs say which |
| No `membrane stop` equivalent / pidfile; no free-port walk | `cliffvm serve` round (c) |
| The Docker image drops the reranker/torch: recall quality or speed differences vs 0.7.53 | measured before release (RP D8; the bench exists); not a packaging call |
| Models: Ollama and cloud users cannot upgrade without RP D1 | RP D1; the release notes must say |
| The Termux/agDroid seats (aarch64 Bionic) | stay on 0.7.5x; a Bionic binary is out of scope |
| `validate.yml` does not run `native/tests`; wasm not built in CI | add jobs (section d) |
| Size: 15 MB payload, vosk.js 5.5 MB | acceptable; a decision if the voice file should be fetched on demand |
| Windows | out |

## (i) Build plan (red test first; one round = 20-45 min)

| round | content (the failing test first) | rounds |
|---|---|---|
| P1 | CI proof of the build: `cargo build --no-default-features --features bin` per platform (musl, aarch64, Intel) + `cliffvm --version`; a throwaway wheel via `Binding.Exec`, tags checked | 2 |
| P2 | wheel layout: `dist/{bin,packs,apps,ui,help}` in the wheel; a test installs it clean and runs `cliffvm run` from the packaged paths; the `packs_dir` fact | 1-2 |
| P3 | `cliffvm serve` (defaults, secret made 0600 and kept, owner token file, claude discovery, `--open`, pid, `stop`, bind default); port `seat_full_1143.rs` | 3-4 |
| P4 | Python shims `membrane`/`babyseat`: exec the binary; dead subcommands print "not in this release"; test the argv mapping table | 1 |
| P5 | CI: `wheels.yml`/`publish.yml` for the binary, the wasm check job, `cargo test` job, the clean-env smoke; the shim and sdist | 2-3 |
| P6 | Docker: new image, entrypoint folded into `serve`, health check, privacy check; the `chedim/membrane` mirror | 1-2 |
| P7 | HA freeze: the pin, the version-guard exception in `tests/test_version.py`, a docs line | 1 |
| P8 | upgrade path: `legacy.v0753` snapshot step, first-start detection, rollback docs; shares MD R8 | 1-2 |
| P9 | stale Pack override rule (section b) | 1-2 |
| P10 | release rehearsal on a scratch tag/fork (never PyPI), checklist walk | 1-2 |
| total | | **14-22** (P1-P6 alone are 10-15; with the migration's 12-18 the whole is about 26-40) |

## 9. Decisions for the operator (I chose none; my recommendation in brackets)

1. Where the packs live: files in a version-stamped folder in the wheel [yes] vs embedded in the binary.
2. Launcher shape: Python shim + native `cliffvm serve` [both] vs only one.
3. Wheel tooling: setuptools-rust `Binding.Exec` [yes, first] vs maturin vs a custom build.
4. Platforms: macOS arm64 + x86_64, Linux glibc and musl x86_64 + aarch64 [yes]; Windows [none]; Android/Termux [none]; Metal in the `bin` build [later].
5. Default port `4096` [yes]; `:11500` served [no, RP D3].
6. Default bind: `127.0.0.1` [yes] vs all interfaces as before (a security change either way); Docker passes `--bind 0.0.0.0`.
7. Data dir: `<cwd>/.membrane/` as today [yes] vs one home dir; store file name; secret file name `jwt.secret` reused [yes].
8. Owner token life 30 days and cookie life 7 days (already in the code, still flagged); the disk-free margin for the snapshot (a number).
9. Version: `0.8.0` [yes] vs `0.7.60`; the HA integration's version.
10. The HA integration: frozen on `babyseat==0.7.53` [yes] vs disabled; the `membrane_ai` copy likewise.
11. The old subcommands that die with a message [list above] vs ones that must be kept (`doctor`, `token`, `store`, `erase-field`?).
12. A bare binary download from the GitHub release page: not in the first release [yes]; Apple Developer ID and notarization later.
13. The Docker image: drop the reranker/torch [yes], Healthcheck URL, keep the `chedim/membrane` mirror [yes], base image.
14. Stale Pack overrides after an upgrade: how it is handled (section b).
15. The `membrane update` / autolatest door: dropped from the product, kept as the operator's tool [yes].
16. Gates list and the by-hand `!` lines (section g); whether the release waits for RP D1 (Ollama) first.
17. The estimate: 14-22 rounds for packaging (26-40 with the migration), as the budget.

## Decided (operator, 2026-11-14)

| # | decision | answer |
|---|---|---|
| 1 | packs as files in a version-stamped folder, not embedded | yes |
| 2 | launcher shape | the defaults live in c1f (a serve app of packs and facts, no hidden Rust defaults); `cliffvm serve` only finds the install folder and runs it; any Python command only executes it |
| 3 | Home Assistant integration frozen on `babyseat==0.7.53` | NO (what replaces it: open, see the question asked on 2026-11-14) |
| 4 | version | `0.7.60` (not `0.8.0`) |
| 5 | default bind | `127.0.0.1` |
