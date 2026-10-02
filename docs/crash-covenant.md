# The crash covenant (couchbase 4.6.3 / pycbc_core)

The audit that closes the overnight-abort mystery of 2026-09-30 -- written
on evidence (macOS DiagnosticReports), not on labels.

## What the dumps say

Three SIGABRT dumps from the outage night
(`~/Library/Logs/DiagnosticReports/Python-2026-09-30-*.ips`), each
"Abort trap: 6" raised by the process itself:

- 15:30:14 -- the faulting thread waits in a libc++
  `condition_variable::timed_wait` under **`_core.so`** (=
  `.venv/.../couchbase/logic/pycbc_core/_core.so`, the couchbase Python
  SDK's native core) while the Python layer is a plain `builtin_next`.
- 15:41:58 -- abort caught inside `_json.scan_once_unicode`.
- 16:04:42 -- abort caught inside `float_repr`/dtoa while a `_json`
  encoder ran.

The same dump carries **five asio reactor threads**
(`asio::io_context::run`, `asio::detail::scheduler::run`,
`kqueue_reactor::run`) -- couchbase 4.6.3's native I/O pool running inside
the seat's interpreter.

## The mechanism

The couchbase Python SDK 4.6.3 core (pycbc_core, C++ over asio) can let a
C++ exception escape an asio completion callback -> `std::terminate` ->
SIGABRT, killing the whole process AFTER the callback's work already ran.
That work-done-then-dead shape is the executed-but-refused ghost and the
overnight aborts. The trigger shape (the field's memory + the code's own
wounds): an async result/Iterator destroyed or abandoned un-drained --
the lazy QueryResult never fully iterated, or a dead connection retried
forever.

## What the field got wrong for weeks

"SDK 4.6.3" was a real version pointed at the wrong package: no
openai/anthropic/opencode SDK exists in this stack at all (model calls are
plain urllib; opencode CLI 1.18.34 bundles @opencode-ai/sdk 1.18.16). The
version was **couchbase 4.6.3** -- and it is still the LATEST release, so
there is no upgrade escape hatch; the guards are the contract.
Audit mechanisms, not labels.

## The standing covenants (already in code)

- `peers.py` `_op_failed()`: N consecutive couchbase failures -> drop and
  rebuild the cluster handles (a dead connection is the abort's
  precondition; never retry on a corpse).
- `peers.py` close() BEFORE interpreter finalization (a live asio thread
  touching CPython during `Py_Finalize` is death, seen in the wild); the
  boot order closes autonode -> field -> finalize.
- `efstore.py`: "an un-iterated CREATE INDEX never runs" -- query results
  must be iterated; `_run()` materializes with `list(...)`.

## Residual hazards (pending patch)

- `efstore.count()`: takes `next(iter(rows))` and abandons the stream --
  harmless for COUNT(*) (one row server-side) but it is the
  destroy-unawaited shape; full-drain patch pending.
- `efstore.scan()`: deliberately lazy generators -- abandoning a scan
  mid-flight leaves the native stream half-consumed; fully consume, or
  move the couchbase pin once a fixed release exists.
