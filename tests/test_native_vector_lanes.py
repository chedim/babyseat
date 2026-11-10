"""#1001 k3: under MEMBRANE_NATIVE_VM=1 the seat's vector lane (sqlstore.vector_top_k) is the native VM's resident
`near` getter. Parity: top-20 getter == the old path on the same field (CPU; Metal when MEMBRANE_GPU_TOPK=1 and the
extension is built with --features metal, as topk_gpu.rs)."""
import numpy as np
import pytest

from membrane import sqlstore as S

MV = pytest.importorskip("membrane.membrane_vm", reason="native VM not built (native/build.sh)")
if not hasattr(MV, "VecStore"):
    pytest.skip("built extension has no VecStore", allow_module_level=True)


def _unit(rng, n):
    a = rng.standard_normal((n, S.DIM)).astype(np.float32)
    return a / np.linalg.norm(a, axis=1, keepdims=True)


def _field(tmp_path, n=600, seed=7):
    rng = np.random.default_rng(seed)
    mx = S._Matrix(tmp_path / "vectors")
    v = _unit(rng, n)
    mx.append([(f"s{i}", v[i]) for i in range(n)])
    return mx, rng


def test_top20_parity_and_sync(tmp_path):
    mx, rng = _field(tmp_path)
    for step in range(3):
        q = _unit(rng, 1)[0]
        old = mx.top(q, 20)
        new = mx.native_top(q, 20)
        assert [k for k, _ in new] == [k for k, _ in old]
        assert np.allclose([s for _, s in new], [s for _, s in old], atol=1e-3)
        if step == 0:                                   # sentences added later are seen (vectors synced)
            extra = _unit(rng, 5)
            mx.append([(f"x{i}", extra[i]) for i in range(5)])
            mx.append([("s3", extra[0])])               # a re-written key: old row masked
            mx.drop("s4")                               # a deleted one
        if step == 1:
            hit = mx.native_top(extra[2], 1)
            assert hit[0][0] == "x2"
            keys = [k for k, _ in mx.native_top(q, 600)]
            assert "s4" not in keys and keys.count("s3") == 1


def test_lane_switch(tmp_path, monkeypatch):
    mx, rng = _field(tmp_path, 100)
    q = _unit(rng, 1)[0]
    monkeypatch.delenv("MEMBRANE_NATIVE_VM", raising=False)
    assert not S._native_vm()
    monkeypatch.setenv("MEMBRANE_NATIVE_VM", "1")
    assert S._native_vm()
    assert [k for k, _ in mx.native_top(q, 20)] == [k for k, _ in mx.top(q, 20)]


def test_a_reset_matrix_never_answers_from_the_old_resident_rows(tmp_path):
    """Review of #1020: reset() (a rebuild when the matrix and the database disagree) must drop the resident
    copy too, or a search returns rows that no longer exist."""
    mx, rng = _field(tmp_path, 50)
    q = _unit(rng, 1)[0]
    assert mx.native_top(q, 5)                          # the resident copy is built
    mx.reset()
    fresh = _unit(rng, 3)
    mx.append([(f"n{i}", fresh[i]) for i in range(3)])
    keys = [k for k, _ in mx.native_top(q, 10)]
    assert sorted(keys) == ["n0", "n1", "n2"], keys
    assert keys == [k for k, _ in mx.top(q, 10)]
