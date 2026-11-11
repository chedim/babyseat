"""The seat's lap timer (#1115): the teardown's per-step `[teardown] <step> Ns` lines (2026-11-02,
the 90 s hang) and the boot's one `[boot]` summary line come from the same few lines.

- teardown: `mark(step)` after each step -> one line per step (echo);
- boot: `step(name)` as each step starts -> `done()` returns the summary line, and `now` names the
  step running (what the booting serve port answers while the seat is not up yet).
"""
from __future__ import annotations

import time


class Laps:
    def __init__(self, tag: str, clock=time.monotonic, echo=None):
        self.tag, self.clock, self.echo = tag, clock, echo
        self.t0 = self.t = clock()
        self.laps: list[tuple[str, float]] = []
        self.now: str | None = None

    def mark(self, step: str) -> float:
        """The step that just ended (the time since the last mark)."""
        t = self.clock()
        dt = t - self.t
        self.t = t
        self.laps.append((step, dt))
        if self.echo is not None:
            print(f"[{self.tag}] {step} {dt:.1f}s", file=self.echo, flush=True)
        return dt

    def step(self, name: str) -> None:
        """A step starts; the one running ends under its own name ('start' before the first)."""
        if self.now is not None or self.clock() > self.t:
            self.mark(self.now or "start")
        self.now = name

    def elapsed(self) -> float:
        return self.clock() - self.t0

    def done(self) -> str:
        """The running step ends; the summary line."""
        if self.now is not None:
            self.mark(self.now)
            self.now = None
        steps = ", ".join(f"{s} {dt:.1f}s" for s, dt in self.laps)
        return f"[{self.tag}] {self.t - self.t0:.1f}s: {steps}"
