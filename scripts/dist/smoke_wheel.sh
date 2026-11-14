#!/bin/sh
# The smoke test of a built wheel (CI runs it per platform where the runner can run the binary): install it into a FRESH venv, then
#   `babyseat --version` names the wheel's version, `babyseat --help` is the seat's usage, `membrane --version` too (the old command), and `babyseat --port 0 --data-dir <tmp>` comes up
#   with a fake claude (no chat turn runs): /global/health is 401 without a token and 200 with the owner token (<data>/owner.token); the installed dist passes verify.sh. Then it is stopped.
#   scripts/dist/smoke_wheel.sh <wheel>        env PYTHON = the python that makes the venv (default python3)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
whl=$(cd "$(dirname "${1:?usage: scripts/dist/smoke_wheel.sh <wheel>}")" && pwd)/$(basename "$1")
py=${PYTHON:-python3}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
cd "$work"   # not the checkout: the installed wheel alone is under test
"$py" -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --quiet --disable-pip-version-check "$whl"
ver=$("$work/venv/bin/python" -c "import membrane; print(membrane.__version__)")
for cmd in babyseat membrane; do
  [ "$("$work/venv/bin/$cmd" --version)" = "babyseat $ver" ] || { echo "smoke_wheel: $cmd --version is not 'babyseat $ver'" >&2; exit 1; }
done
"$work/venv/bin/babyseat" --help | grep -q 'cliffvm serve' || { echo "smoke_wheel: babyseat --help is not the seat's usage" >&2; exit 1; }
dist=$("$work/venv/bin/python" -c "import membrane, pathlib; print(pathlib.Path(membrane.__file__).parent / 'dist')")
SMOKE_LAUNCH="$work/venv/bin/babyseat" sh "$here/smoke.sh" "$dist"
echo "smoke_wheel: ok ($(basename "$whl"))"
