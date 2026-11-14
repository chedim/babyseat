# Packaging and the launcher: decided, built, in progress

Status (2026-11-14): the dist folder, the launcher and the per-platform binary workflow are BUILT. The wheels, the installer, the Docker image and the publish workflow are IN PROGRESS on branch `w118-wheels`
(its files: `Dockerfile`, `docker/`, `install.sh`, `.github/workflows/*`, `pyproject.toml`, `scripts/dist/`); this page does not describe them as done. The first draft of this file was a scoping note with rounds and 17 open
decisions; it is in git history. Nothing is published by any workflow; publishing is the operator's own step.

## Decided (operator, 2026-11-14)

| # | decision | answer |
|---|---|---|
| 1 | packs and apps | files in a version-stamped folder next to the binary, not embedded |
| 2 | launcher shape | the defaults live in c1f (`membrane/packs/launcher.c1f`: no hidden Rust defaults); `cliffvm serve` only finds the install folder, the data folder and the claude CLI and runs the seat; a Python command only executes it |
| 3 | Home Assistant integration | NOT frozen on the old package: a connect-only thin client (`requirements: []`, docs/home-assistant.md) |
| 4 | version | `0.7.60` everywhere user-facing (`native/Cargo.toml`, `pyproject.toml`, the Home Assistant manifest, the dist `VERSION`); CITATION.cff and CHANGELOG.md are written at the release commit |
| 5 | default bind | `127.0.0.1` (`--bind` opens it) |
| 6 | default port | `4096` (`--port` changes it; there is no second port: the 0.7 OpenAI/Ollama proxy on 11500 is gone) |
| 7 | data folder | named `membrane`: `$XDG_DATA_HOME/membrane`, else `~/Library/Application Support/membrane` (macOS) or `~/.local/share/membrane`; `--data-dir` overrides |
| 8 | identity | one identity, `admin`; the door secret `door.secret`, the key file `admin.token` (docs/auth.md) |
| 9 | platforms | macOS arm64 and x86_64; Linux x86_64 and aarch64 on glibc (2.28 floor) and musl; no Windows, no Android/Termux |

## Built

- **The dist folder** (`scripts/dist/build.sh [--bin] [--out] [--target] [--archive]`): `bin/cliffvm`, `packs/` (every `membrane/packs/*.c1f` and the data files), `apps/barest.packs`, `apps/barest.c1f/` (the page is its `ui/`), `help/`, `LICENSE`,
  `VERSION`, `MANIFEST.sha256`. `field.c1f` (the disabled 3D view) is not shipped. Reproducible manifest; `scripts/dist/verify.sh <dir>` checks it; `scripts/dist/smoke.sh <dir>` runs `--version`, `serve --port 0` from the folder alone and `/global/health`
  with and without the admin token; `scripts/dist/assemble_wheel_payload.sh <dist> <payload>` makes the folder the wheel wrapper packages as `membrane/dist/`. Tests: `native/tests/dist_1143.rs`.
- **The launcher**: `cliffvm serve` (`native/src/cli.rs`, docs/cliffvm.md): finds the dist folder above `bin/`, the data folder, `CLAUDE_BIN` or `claude` on PATH; tells `door_secret_file`, `seat_data_dir`, `chat_claude_bin`, `legacy_path`; prints the URL and the curl line. Tests: `serve_1143.rs`.
- **The Python wrapper** (`membrane/launcher.py`, entry points `membrane` and `babyseat`): finds the binary (`$CLIFFVM`, the wheel's `dist/bin/cliffvm`, a checkout's release build) and `exec`s `cliffvm serve` with the arguments. It parses nothing and chooses nothing.
  The old subcommands (`opencode review ingest backfill move toybox self shadow store connect auth doctor bench codegraph erase-field install`) say they are not in this release and point at `pip install babyseat==0.7.53`. Tests: `tests/wrapper/`.
- **The binary workflow** (`.github/workflows/release-binaries.yml`): on a tag `v*` or by hand, builds the six targets, assembles the dist folder, smoke-tests it (macOS x86_64 under Rosetta), uploads the `babyseat-<version>-<target>.tar.gz` and the wheel payload as workflow artifacts; publishes nothing.
- **Home Assistant**: the component installs no package and starts no process; HACS ships it from the repository (`hacs.json`; docs/home-assistant.md).

## In progress (branch `w118-wheels`, not in this tree yet)

- Wheels per platform that carry the dist folder (`pip install babyseat`, then `babyseat`), and the matching sdist behaviour.
- `install.sh` for the binary release and `uv tool`/pip.
- The Docker image (`chedim/babyseat`): runs `cliffvm serve --bind 0.0.0.0` on the `/data` volume, its healthcheck on `:4096/global/health`.
- The publish workflow (PyPI before the GitHub release, so HACS sees a release only after the package exists).
- The README names these commands as "available at release".

## Open (not decided; listed so they are not forgotten)

- The wasm drift check in CI: `native/build-web.sh` rewrites the committed browser wasm and its version hash; whether the build is byte-reproducible across machines is unverified, so no job runs it yet.
- Signing and notarization of the macOS binary (a downloaded binary may be quarantined by Gatekeeper).
- Whether `membrane/help/*.md` (the 0.7 Python seat's topics: blurp, scripts, state, stdlib, ...) should be left out of the dist: `build.sh` copies the folder, and no release code reads it (`help/` of the dist is not loaded; the model's topics are `membrane/apps/barest.c1f/help/`).
- A patch line for 0.7.5x: patches of the Python line are `0.7.54+` and the wrapper is `0.7.60`, so `pip install -U babyseat` picks `0.7.60` everywhere a wheel exists; users of the Python line pin `babyseat==0.7.5N`.
