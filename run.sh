#!/usr/bin/env bash
# membrane launcher -- delegates to the packaged `membrane` CLI (membrane/cli.py).
#
# The canonical interface is now the CLI itself, from any directory:
#
#   uvx --from "membrane @ git+ssh://git@github.com/chedim/membrane" membrane opencode
#   ... membrane opencode | review | reset | install | doctor | connect
#
# This wrapper keeps the old ./run.sh surface working in-repo:
#
#   ./run.sh                field-backed opencode on :11500 (the default)
#   ./run.sh opencode       same as above (explicit)
#   ./run.sh restart        kill the seat on :11500, then start it fresh
#                           (an alias of `membrane reset`)
#   ./run.sh review         print what the field learned
#
# Flags are forwarded verbatim to the CLI. (tui / proxy / anthropic / demo
# are retired; the CLI prints a pointer to opencode for them.)

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
    sed -n '9,17p' "$0"; exit 0 ;;
  restart)
    shift; exec "$PY" -m membrane.cli reset "$@" ;;
esac

exec "$PY" -m membrane.cli "${@:-opencode}"