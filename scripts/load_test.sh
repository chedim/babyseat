#!/bin/sh
# Run the native test files that were flaky under machine load, while one
# busy-loop process per core keeps the machine loaded.
#   scripts/load_test.sh [RUNS] [TEST_FILE ...]      (default: 3 runs, the four flaky files)
# Bounded: each run is one `cargo test` per file; the busy loops are killed on exit.
# Prints "failed R of N" per file and exits non-zero if any run failed.
set -u
RUNS=${1:-3}; [ $# -gt 0 ] && shift
FILES=${*:-"session_1143 chat_process help_1143 seat_process"}
cd "$(dirname "$0")/../native" || exit 2
CORES=$(sysctl -n hw.ncpu 2>/dev/null || nproc)
PIDS=""
trap 'kill $PIDS 2>/dev/null' EXIT INT TERM
cargo test --features bin --no-run >/dev/null 2>&1 || { echo "build failed"; exit 2; }
i=0; while [ $i -lt "$CORES" ]; do yes >/dev/null & PIDS="$PIDS $!"; i=$((i+1)); done
echo "load: $CORES busy loops; $RUNS runs of: $FILES"
bad=0
for f in $FILES; do
  fail=0; n=0
  while [ $n -lt "$RUNS" ]; do
    n=$((n+1))
    if ! cargo test --features bin --test "$f" >"${TMPDIR:-/tmp}/load_$f.$n.log" 2>&1; then
      fail=$((fail+1)); grep -E "^test .*FAILED|panicked" "${TMPDIR:-/tmp}/load_$f.$n.log" | head -6
    fi
  done
  echo "$f: failed $fail of $RUNS"; bad=$((bad+fail))
done
[ $bad -eq 0 ]
