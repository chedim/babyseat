"""Ephemeral tier (experiments/ephemeral_tier.py) -- the lab checks as pytest.

The invariant: EVIDENCE OF REALITY -> DURABLE, SILENCE -> EPHEMERAL,
CONFIRMATION IS THE PROMOTION. Wraps the lab's four gates (E1 promote purity,
E2 salience parity, E3 merged recall, E4 clock + sketch) as individual tests;
everything runs on the FakeEmbedder with tempdir artifacts -- no cluster, no
model. The lab runs the full narrative; these keep the semantics pinned
(fail loudly on regression, with the same verdicts).
"""
import os
import sys
import tempfile

os.environ.setdefault("MEMBRANE_STORE", "json")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "experiments"))

import experiments.ephemeral_tier as lab          # noqa: E402
from experiments.ephemeral_tier import (EPH_TTL_S, T_H0, TieredField,  # noqa: E402
                                        e1_promote_purity,
                                        e2_salience_parity,
                                        e3_merged_recall,
                                        e4_clock_and_sketch,
                                        seed_field, FakeEmbedder)


def _run_gate(fn, *args):
    """Run one lab gate on a FRESH CHECKS ledger; RED if any check failed."""
    lab.CHECKS.clear()
    fn(*args)
    red = [(n, d) for n, ok, d in lab.CHECKS if not ok]
    for n, d in red:
        print(f"RED: {n} -- {d}")
    assert not red, f"{fn.__name__}: {len(red)} red check(s)"
    return len(lab.CHECKS)


def test_e1_promote_purity():
    tmp = tempfile.mkdtemp(prefix="eph-tier-e1-")
    assert _run_gate(e1_promote_purity, tmp) >= 5


def test_e2_salience_parity():
    tmp = tempfile.mkdtemp(prefix="eph-tier-e2-")
    tf = TieredField(tmp, "e2-test")
    seed_field(tf)
    assert _run_gate(e2_salience_parity, tf) >= 1


def test_e3_merged_recall():
    tmp = tempfile.mkdtemp(prefix="eph-tier-e3-")
    _run_gate(e3_merged_recall, tmp)


def test_e4_clock_and_sketch():
    tmp = tempfile.mkdtemp(prefix="eph-tier-e4-")
    _run_gate(e4_clock_and_sketch, tmp)


def test_exit_code_green(tmp_path=None):
    """The lab's own overall verdict: subprocess exit 0 = OVERALL GREEN."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([os.path.join(root, ".venv", "bin", "python"),
                        os.path.join(root, "experiments", "ephemeral_tier.py")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-500:]


def test_capture_never_touches_durable():
    """Direct unit: capture -> durable count frozen; promote is the ONLY
    cross-tier move; expire touches only ephemera (belt for E1)."""
    tmp = tempfile.mkdtemp(prefix="eph-tier-unit-")
    tf = TieredField(tmp, "unit")
    seed_field(tf)
    n0 = len(tf.durable)
    emb = FakeEmbedder()
    ex = tf.capture("parse the json config", "v2 parser", emb.embed(["parse the json config"])[0],
                    born=T_H0 + 1)
    assert len(tf.durable) == n0
    tf.expire(T_H0 + EPH_TTL_S - 1)
    assert len(tf.ephemeral) == 1 and len(tf.durable) == n0
    tf.promote(ex, safe=True, outcome="confirmed", ts=T_H0 + 5, how="confirm")
    assert len(tf.durable) == n0 + 1 and len(tf.ephemeral) == 0
    tf.expire(T_H0 + EPH_TTL_S + 1)
    assert any(e.action == "v2 parser" for e in tf.durable.exemplars)


def test_refute_is_durable_unsafe():
    """The confirmed NEGATIVE is evidence too -- it must outlive the horizon."""
    tmp = tempfile.mkdtemp(prefix="eph-tier-unit-")
    tf = TieredField(tmp, "unit-refute")
    seed_field(tf)
    emb = FakeEmbedder()
    ex = tf.capture("debug the crash", "rm -rf / (wrong)", emb.embed("debug the crash") if isinstance("debug the crash", list) else emb.embed(["debug the crash"])[0],
                    born=T_H0 + 1)
    tf.promote(ex, safe=False, outcome="re-ask", ts=T_H0 + 5, how="refute")
    tf.expire(T_H0 + EPH_TTL_S + 1)
    bad = [e for e in tf.durable.exemplars if not e.safe]
    assert len(bad) == 1 and bad[0].outcome == "re-ask"