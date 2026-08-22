"""The cell pool: a background population of curiosity-driven cells grazing a shared
buffer, with clean shutdown.

Stage 1 made a ``Cell`` a synchronous object the experiment drove directly. Stage 2
makes a POPULATION of cells run in the background of the live proxy: each cell is its
own daemon thread, pulling curious situations from a shared read-only ``BufferView``
on a cadence, self-extinguishing (a cell with nothing new to be curious about returns
immediately and idles until the next tick). This is the first lifecycle primitive in
the project -- the proxy had no threading / shutdown before -- so it owns start/stop
and is independently testable.

DECENTRALIZED + ISOLATED: each cell owns its own ``Membrane`` (own field / audit / veil
/ rng, single-writer thread); the ONLY shared structure is the ``SituationBuffer``
(already thread-safe by construction, membrane/buffer.py). No cell touches
``srv.membrane`` or another cell. The pool never routes -- it just runs each cell's own
pull loop.

RESILIENT: a cell's ``pull`` is wrapped so one crashing cell logs and continues -- a
single bad cell does not take down the pool or the proxy. numpy releases the GIL during
the novelty SVD, so background cells doing variety math do not serialize against the
live request threads.

THE "ENTROPY ONLY INCREASES" BOUNDARY (stage-2 principle): the pool runs the cells; it
offers the operator NO knob to break a cell's guardrails (no mid-run observer flip, no
self-feeding, no redefine-safe). The operator's only reach-in is RESET (delete a
cell's field file / boot ``fresh``) -- a deliberate, visible action, not a mid-run
switch. The guardrails are physics for the cell; the maker's self-restraint is the one
place they are a choice, and the choice is not exposed here as a knob.
"""
from __future__ import annotations

import threading
import traceback


class CellPool:
    """A background population of cells, each pulling a shared buffer on a cadence.

    Each cell runs in its own daemon thread:
    ``while not stop.is_set(): cell.pull(view); stop.wait(interval)``. ``pull`` is
    self-extinguishing (returns 0 when nothing is curious), so an idle cell costs only
    the wait. ``stop`` is a ``threading.Event``; ``stop()`` sets it and joins every
    thread with a timeout so shutdown is bounded and never hangs the proxy exit.
    """

    def __init__(self, cells, view, interval: float = 2.0) -> None:
        self._cells = list(cells)
        self._view = view
        self._interval = interval
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._run, args=(c,), name=f"cell-{c.tag}",
                             daemon=True)
            for c in self._cells
        ]

    def start(self) -> None:
        """Spawn every cell thread. Call once at boot."""
        for t in self._threads:
            t.start()

    def _run(self, cell) -> None:
        while not self._stop.is_set():
            try:
                cell.pull(self._view)
            except Exception:
                # one crashing cell must not kill the pool or the proxy -- log and
                # keep serving its siblings. Cells are independent: a bad field does
                # not affect the other cells or the live single-Membrane path.
                traceback.print_exc()
            # wait() returns immediately once stop is set, so a shutdown signal breaks
            # an idle cell out of its cadence sleep without waiting the full interval.
            self._stop.wait(self._interval)

    def stop(self, join_timeout: float = 5.0) -> None:
        """Signal every cell thread to stop and join them (bounded). Idempotent.

        ``join_timeout`` bounds shutdown so a cell stuck mid-``pull`` cannot hang the
        proxy's exit -- the thread is a daemon, so an overrun is abandoned (it dies with
        the process) rather than blocking ``serve_forever``'s teardown."""
        self._stop.set()
        for t in self._threads:
            t.join(timeout=join_timeout)