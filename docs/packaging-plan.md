# Packaging and the launcher: decided, built, in progress

Status (2026-11-14): the dist folder, the launcher, the per-platform binary workflow, the wheels, the installer, the Docker image and the publish workflows are BUILT and on this tree (run locally for macOS arm64 only; no workflow has been run on a runner from here). The first draft of this file was a scoping note with rounds and 17 open
decisions; it is in git history. Nothing is published by any workflow; publishing is the operator's own step.

## Decided (operator, 2026-11-14)

| # | decision | answer |
|---|---|---|
| 1 | packs and apps | files in a version-stamped folder next to the binary, not embedded |
| 2 | launcher shape | the defaults live in c1f (`membrane/packs/launcher.c1f`: no hidden Rust defaults); `cliffvm serve` only finds the install folder, the data folder and the claude CLI and runs the seat; a Python command only executes it |
| 3 | Home Assistant integration | NOT frozen on the old package: it downloads the release tarball of its own version and runs the seat inside Home Assistant (the default), or connects to a seat (`requirements: []`, docs/home-assistant.md) |
| 4 | version | `0.7.61` everywhere user-facing (`native/Cargo.toml`, `pyproject.toml`, the Home Assistant manifest, the dist `VERSION`); CITATION.cff and CHANGELOG.md are written at the release commit |
| 5 | default bind | `127.0.0.1` (`--bind` opens it) |
| 6 | default port | `4096` (`--port` changes it; there is no second port: the 0.7 OpenAI/Ollama proxy on 11500 is gone) |
| 7 | data folder | named `membrane`: `$XDG_DATA_HOME/membrane`, else `~/Library/Application Support/membrane` (macOS) or `~/.local/share/membrane`; `--data-dir` overrides |
| 8 | identity | one identity, `admin`; the door secret `door.secret`, the key file `admin.token` (docs/auth.md) |
| 9 | platforms | macOS arm64 and x86_64; Linux x86_64 and aarch64 on glibc (2.28 floor) and musl; no Windows, no Android/Termux |

## Built

- **The dist folder** (`scripts/dist/build.sh [--bin] [--out] [--target] [--archive]`): `bin/cliffvm`, `packs/` (only the packs `apps/barest.packs` lists, `launcher.c1f` and `boot.json`), `apps/barest.packs`, `apps/barest.c1f/` (the page is its `ui/`), `LICENSE`,
  `VERSION`, `MANIFEST.sha256`. `field.c1f` (the disabled 3D view) is not shipped. Reproducible manifest; `scripts/dist/verify.sh <dir>` checks it; `scripts/dist/smoke.sh <dir>` runs `--version`, `serve --port 0` from the folder alone and `/global/health`
  with and without the admin token; `scripts/dist/assemble_wheel_payload.sh <dist> <payload>` makes the folder the wheel wrapper packages as `membrane/dist/`. Tests: `native/tests/dist_1143.rs`.
- **The launcher**: `cliffvm serve` (`native/src/cli.rs`, docs/cliffvm.md): finds the dist folder above `bin/`, the data folder, `CLAUDE_BIN` or `claude` on PATH; tells `door_secret_file`, `seat_data_dir`, `chat_claude_bin`, `legacy_path`; prints the URL and the curl line. Tests: `serve_1143.rs`.
- **The Python wrapper** (`membrane/launcher.py`, entry points `membrane` and `babyseat`): finds the binary (`$CLIFFVM`, the wheel's `dist/bin/cliffvm`, a checkout's release build) and `exec`s `cliffvm serve` with the arguments. It parses nothing and chooses nothing.
  The old subcommands (`opencode review ingest backfill move toybox self shadow store connect auth doctor bench codegraph erase-field install`) say they are not in this release and point at `pip install babyseat==0.7.53`. A few common flags of the Python seat, by exact name (`OLD_FLAGS`: `--backend`, `--model`, `--field`, `--cortex`, `--serve-port` ...), name the flags `cliffvm serve` takes (`--port`, `--data-dir`, `--bind`, `--fact name=value`) and exit 2 instead of reaching cliffvm's bare usage error; any other argument goes to `cliffvm serve` untouched. Tests: `tests/wrapper/`.
- **The binary workflow** (`.github/workflows/release-binaries.yml`): on a tag `v*` or by hand, builds the six targets, assembles the dist folder, smoke-tests it (macOS x86_64 under Rosetta), uploads the `babyseat-<version>-<target>.tar.gz` and the wheel payload as workflow artifacts; publishes nothing.
- **Home Assistant**: the component installs no pip package; HACS ships it from the repository (`hacs.json`). Its default path downloads `babyseat-<version>-<target>.tar.gz` and its `.sha256` from the GitHub release of its own version (the static musl builds on Linux), checks them as `install.sh` does, and runs `bin/cliffvm serve` as Home Assistant's child process (docs/home-assistant.md).
- **The Home Assistant add-on** (`repository.yaml`, `addon/`; the user's guide is `addon/DOCS.md`): for Home Assistant OS and Supervised, the same repository is an add-on repository. The Supervisor builds `addon/Dockerfile`
  on the device: the Home Assistant Alpine base image plus the release's MUSL tarball of the add-on's `version` (pinned to pyproject's by `tests/wrapper/test_versions.py`), checked against its `.sha256` and the
  folder's `MANIFEST.sha256` (`addon/install-dist.sh`; `addon/dist/` takes local tarballs for a local build). `addon/run.sh` turns `/data/options.json` into the launch line (`addon/options.sh`: `seat_name`, and the chat model as
  `provider` anthropic (the default: Anthropic's OpenAI-compatible endpoint, one `cloud_models` entry with base_url `https://api.anthropic.com` and key `ANTHROPIC_API_KEY`) or openai = one `cloud_models` entry + `minds`, ollama = `seat_backend` + `minds`, claude_cli = a `claude_path` the user supplies; keys only as environment variables), execs
  `cliffvm serve --port 4096 --bind 0.0.0.0 --data-dir /data`, and in the background (`addon/announce.sh`) waits for the door with this start's admin token and POSTs the Supervisor discovery
  `{service: membrane, config: {host, port, token}}` at every start. Home Assistant's config folder is mapped read-only at `/config`, where the cutover finds the 0.7 store. NOT verified here: a real Supervisor, the image build
  (no Docker daemon answered), the musl binary. Tests: `tests/wrapper/test_ha_addon.py`; CI: the `addon` job of `validate.yml` (the add-on linter, non-blocking until its first green run) and the shellcheck line.

## Open (not decided; listed so they are not forgotten)

- The wasm drift check in CI: `native/build-web.sh` rewrites the committed browser wasm and its version hash; whether the build is byte-reproducible across machines is unverified, so no job runs it yet.
- Signing and notarization of the macOS binary (a downloaded binary may be quarantined by Gatekeeper).
- A patch line for 0.7.5x: patches of the Python line are `0.7.54+` and the wrapper is `0.7.61`, so `pip install -U babyseat` picks `0.7.61` everywhere a wheel exists; users of the Python line pin `babyseat==0.7.5N`.

## Wheels, installer, image and publish lanes (built; checked locally for macOS arm64 only)

| piece | where | state |
|---|---|---|
| the wheel | `setup.py` (payload in, `py3-none-<tag>` out, no payload = "no binary for this platform"), `membrane/launcher.py` (VERSION check, exec-bit fallback), `scripts/dist/build_wheel.sh <tag> --dist <dist folder>|--payload <dir>`, `scripts/dist/smoke_wheel.sh <wheel>` | built for macOS arm64 here: `babyseat-0.7.61-py3-none-macosx_11_0_arm64.whl` 3.6 MB (the dist tarball is 3.5 MB, the binary 6.2 MB uncompressed), installed in a fresh venv, `babyseat --version`, `--help` and `serve --port 0` with a fake claude: health 401 without a token, 200 with `<data>/admin.token` |
| the sdist | `python -m build --sdist`: 17 files, the launcher alone | building a wheel from it stops with "no binary for this platform" (decision 4) |
| CI | `wheels.yml` (calls `release-binaries.yml`, one wheel row per target, smoke test per row, the sdist check), `publish.yml` (dispatch + typed confirmation `publish <tag>` + tag == version, PyPI before any GitHub release, the shim), `docker.yml` (build and check on a PR; push only on a dispatch that ticks `push` and types `push <tag>`) | YAML parses; NOT run on a runner |
| Docker | `Dockerfile`, `.dockerignore`, `docker/docker-entrypoint.sh` | the image mechanics (uid 10001, `/data`, 4096, entrypoint args, no claude) checked locally with a stand-in binary; the real Linux binary in the image is NOT verified here |
| installer | `install.sh` (+ `build.sh --archive` now writes `<tarball>.sha256`) | tested with a `file://` release, and with the real macOS tarball |
| upgrade | `tests/wrapper/test_upgrade_cutover.py` | the launcher with `MEMBRANE_DATA_DIR` at a fake old store: `legacy_path` reaches the seat and the cutover picks `field.sqlite/field.sqlite3` up |

**macOS and Gatekeeper (decided 2026-11-14: the 0.7.61 binaries are UNSIGNED; signing and notarization are a later decision).** A binary that `pip` or `install.sh` writes carries no `com.apple.quarantine` attribute, so Gatekeeper does not stop it: `pip install babyseat` and the installer are the way in on macOS. A tarball downloaded in a BROWSER is quarantined and the first run is refused: right-click the `cliffvm` binary and choose Open once, or remove the mark with `xattr -dr com.apple.quarantine <folder>`. (Apple silicon runs an unsigned arm64 binary only if it carries the linker's ad-hoc signature, which rustc's linker adds: the binary built here runs. Not tested from a browser download.)

**What could not be closed locally:** the Linux and musl binaries (no Rust target installed here and Docker Hub refused the pull of a Rust image), so those four wheels, the Docker image with the real binary and the Alpine smoke have never run; the macOS x86_64 wheel under Rosetta; `shellcheck` and `actionlint` are not installed here (the CI step runs `shellcheck -S warning`); PyPI, GitHub release and Docker Hub steps cannot be run by design.

**Operator decisions (2026-11-14).** Docker needs the claude CLI mounted (`-v <claude>:/opt/claude:ro -e CLAUDE_BIN=/opt/claude`; it is not in the image); `install.sh` installs under `~/.local/share/membrane-app/<version>` (separate from the data folder) and links `babyseat` only; the macOS x86_64 wheel is tagged `macosx_11_0_x86_64`; publishing is the operator's dispatch only; `shellcheck -S warning` blocks CI.

**Publishing 0.7.61 (the operator's line; operator 2026-11-15: one full release, 0.7.60 was never published).** `gh workflow run publish.yml --ref v0.7.61 -f tag=v0.7.61 -f confirm="publish v0.7.61" -f github_release=true`. Tick `github_release`: it attaches the release tarballs and their `.sha256` files that `install.sh`, the Docker dispatch and the Home Assistant add-on's image download (a PyPI-only publish leaves them nothing to fetch), and, as the latest release, it is the version HACS offers (the component of this release downloads these tarballs to run the seat inside Home Assistant: without them its default path cannot start; see docs/home-assistant.md, "Upgrading from 0.7.53"). `prerelease=true` makes the GitHub release a pre-release instead (HACS and `/releases/latest` then keep naming the previous stable release). Then, to push the image: `gh workflow run docker.yml --ref v0.7.61 -f tag=v0.7.61 -f push=true -f confirm="push v0.7.61" -f stable=true` (`stable=true` moves `X.Y` and `latest`, which existing pulls follow: without it only `0.7.61` is pushed).
