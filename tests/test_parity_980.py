"""#980 Q3: the before/after check catches state drift and slowdowns."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import parity as P  # noqa: E402


def _snap(**props):
    return {"boot_ms": 10.0, "hot_us": 5.0, "state": {"e1": {"cls": "Other", "properties": dict(props)}}}


def test_identical_state_is_parity_and_ok():
    v = P.verdict(_snap(a=1), _snap(a=1))
    assert v["ok"] and not v["bad"]


def test_changed_and_removed_state_fails_parity():
    assert P.verdict(_snap(a=1), _snap(a=2))["bad"] == ["parity"]
    after = _snap(a=1)
    after["state"] = {}
    assert P.diff(_snap(a=1), after)["removed"] == ["e1"]


def test_slowdown_beyond_the_ratio_fails():
    slow = _snap(a=1)
    slow["boot_ms"] = 40.0
    assert P.verdict(_snap(a=1), slow, max_slow=1.5)["bad"] == ["boot_ms"]


def test_snapshot_of_the_banner_seed_is_stable():
    boot, hot = P._banner_run()
    a, b = P.snapshot(boot, hot, runs=5), P.snapshot(boot, hot, runs=5)
    assert P.diff(a, b) == {"added": [], "removed": [], "changed": {}} and a["state"]
