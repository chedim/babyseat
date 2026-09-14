#!/usr/bin/env bash
# membrane launcher -- delegates to the packaged `membrane` CLI (membrane/cli.py).
#
# The canonical interface is now the CLI itself, from any directory:
#
#   uvx --from "membrane @ git+ssh://git@github.com/chedim/membrane" membrane tui
#   ... membrane opencode | proxy | anthropic | review | demo | reset | install
#
# This wrapper keeps the old ./run.sh surface working in-repo:
#
#   ./run.sh                TUI, and starts a proxy on :11435 if one isn't
#                           already up; tears it down when the TUI exits
#   ./run.sh tui            same as above (explicit alias)
#   ./run.sh restart        kill any proxy on the port, then start fresh
#   ./run.sh proxy          just the proxy, foreground (no TUI)
#   ./run.sh opencode       field-backed opencode on :11500
#   ./run.sh anthropic      TUI+proxy in --anthropic mode
#   ./run.sh review         print what the field learned
#   ./run.sh demo           in-process end-to-end loop
#
# Flags are forwarded verbatim to the CLI, which routes them between the
# proxy and the TUI by flag name (same rules ./run.sh used to implement).

set -eo pipefail
# THE SSH-KEY GUARD (the seat-launch crash): the boot update-check runs
# `git fetch` against the SSH origin -- an unauthenticated launch hangs on
# the key passphrase (or crashes the TUI boot). The update check is a
# manual `membrane update` op; the seat's boots stay local.
export MEMBRANE_NO_UPDATE_CHECK=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"

case "${1:-}" in
  -h|--help|help)
    sed -n '7,15p' "$0"; exit 0 ;;
esac

exec "$PY" -m membrane.cli "${@:-tui}"