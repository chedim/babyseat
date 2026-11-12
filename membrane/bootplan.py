"""STAGE 1 of the boot (docs/python-to-c1f.md, piece 1): the boot's order and policy are data -- `packs/boot.json`
(the steps, their edges, whether one stops the boot, whether one runs in the background once the UI is up) and
`packs/boot.c1f` (when a step is ready) -- on a small field engine of their own. Python only runs the closure the field
names and writes the step's state back (running, done, failed + its error). A failed step that is not `essential` is
recorded and the boot goes on."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PACKS = Path(__file__).resolve().parent / "packs"


class OutOfOrder(RuntimeError):
    """A step asked to run before the steps ahead of it ended."""


def shipped() -> list:
    return json.loads((PACKS / "boot.json").read_text())


class _Host:
    pass


def _eid(name: str) -> str:
    return f"boot:{name}"


class Plan:
    def __init__(self, steps: list):
        from . import membrane_vm as V
        self.f = V.Fields(_Host())
        self.f.set_pack("boot", (PACKS / "boot.c1f").read_text())
        self.steps = {s["name"]: s for s in steps}
        self.order = [s["name"] for s in steps]
        self.deferred: dict = {}
        for s in steps:
            props = {"name": s["name"], "state": "pending", "essential": bool(s.get("essential")),
                     "background": bool(s.get("background")), "blocks": [_eid(b) for b in s.get("blocks") or []]}
            self.f.load(_eid(s["name"]), "Step", json.dumps(props))
        self.f.settle(time.time())

    def errors(self) -> dict:
        return json.loads(self.f.pack_errors() or "{}")

    def _get(self, name: str) -> dict:
        return json.loads(self.f.get(_eid(name)))

    def _set(self, name: str, **props) -> None:
        now = time.time()
        for k, v in props.items():
            self.f.write(_eid(name), k, json.dumps(v), now, "boot")
        self.f.settle(now)

    def state(self, name: str) -> str:
        return self._get(name).get("state", "")

    def error(self, name: str) -> str:
        return self._get(name).get("error", "")

    def _go(self, name: str, fn) -> None:
        self._set(name, state="running")
        try:
            fn()
        except BaseException as e:      # noqa: BLE001 -- recorded; the step's own flag says whether the boot dies
            self._set(name, state="failed", error=f"{type(e).__name__}: {e}"[:300])
            print(f"[boot] step {name} failed: {type(e).__name__}: {e}", file=sys.stderr)
            if self.steps[name].get("essential") or not isinstance(e, Exception):
                raise
            return
        self._set(name, state="done")

    def run(self, name: str, fn) -> None:
        """Run a boot step now, when the field says it is ready."""
        if not self._get(name).get("ready"):
            raise OutOfOrder(f"boot step {name!r} is not ready (state {self.state(name)!r}, "
                             f"{self._get(name).get('open')} step(s) ahead of it still open)")
        self._go(name, fn)

    def defer(self, name: str, fn) -> None:
        """A background step: it waits for `up()`."""
        self.deferred[name] = fn

    def up(self) -> list:
        """The UI is up: the background steps run, in the plan's order, each failing alone. Returns [(name, error)]."""
        out = []
        for n in self.order:
            fn = self.deferred.pop(n, None)
            if fn is None or not self.steps[n].get("background"):
                continue
            self._go_isolated(n, fn)
            out.append((n, self.error(n) or None))
        return out

    def _go_isolated(self, name: str, fn) -> None:
        self._set(name, state="running")
        try:
            fn()
        except Exception as e:          # noqa: BLE001
            self._set(name, state="failed", error=f"{type(e).__name__}: {e}"[:300])
            print(f"[seat] {name} skipped ({e})", file=sys.stderr)
            return
        self._set(name, state="done")
        print(f"[seat] background: {name} started", file=sys.stderr)
