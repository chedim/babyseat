"""bench_recall -- the held-out recall benchmark, run against a field COPY.

The re-runnable form of the number the worldlines work measured (MRR 0.210 ->
0.539) but never shipped as a script: replay a seat's history into a FRESH
field, hold out a fraction of the mentions, then score ``recall()``
(worldline / mean / state) against ground truth with the standard MRR + hit@k
every memory framework reports.

    # 1. copy the live field -- never benchmark the live store itself
    cp -R .membrane/field /tmp/field-copy

    # 2. run
    PYTHONPATH=. .venv/bin/python experiments/bench_recall.py \\
        --field /tmp/field-copy --hold-out 0.2 --k 8

The work is delegated to ``membrane.bench`` (the ``membrane bench`` CLI wraps
the same entry); this script only pins the real-run defaults and the
copy-first discipline in one place.
"""
from __future__ import annotations

from membrane.bench import main

if __name__ == "__main__":
    raise SystemExit(main())
