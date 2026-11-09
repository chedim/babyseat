"""#971 step 3 (red first): the native VM passes the same contract as ch.VM.
- the replay contract (test_journal_971): a journal recorded with ch.VM, replayed through the NATIVE
  VM, gives the same trigger writes in the same order and the same final state;
- ch.py's own VM tests, run with ch.VM swapped for the native one (tests/test_ch_native_suites_971.py)."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

NV = pytest.importorskip("membrane.ch_native")
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import journal as J  # noqa: E402
from membrane import membrane_vm as MV  # noqa: E402
import test_journal_971 as TJ  # noqa: E402


def test_a_journal_recorded_with_ch_vm_replays_identically_through_the_native_vm():
    p = Path(tempfile.mkdtemp()) / "w.jsonl"
    est = TJ._session(p)
    fresh = TJ._store()
    rep = J.replay_vm(p, fresh, lambda s: NV.NativeVM(s))
    assert rep["mismatches"] == [], rep["mismatches"][:5]
    assert rep["vm_writes"] > 0
    assert TJ._state(fresh) == TJ._state(est)


@pytest.mark.parametrize("src,env,want", [
    ("new > 25", {"new": 27}, True),
    ("1 + 2 * 3", {}, 7),
    ("7 / 2", {}, 3.5),
    ("'a' + 'b'", {}, "ab"),
    ("[1, 2] + [3, 4]", {}, [4.0, 6.0]),
    ("[1, 2] * 2", {}, [2.0, 4.0]),
    ("x in [1, 2, 3]", {"x": 2}, True),
    ("x not in [1, 2]", {"x": 3}, True),
    ("1 < 'a'", {}, False),
    ("0 or 'z'", {}, "z"),
    ("1 and 0", {}, 0),
    ("not []", {}, True),
    ("max(3, 9, 4)", {}, 9),
    ("min([5, 2, 8])", {}, 2),
    ("abs(-2.5)", {}, 2.5),
    ("distance([1, 0], [0, 1])", {}, 1.0),
    ("norm([3, 4])", {}, 5.0),
    ("dot([1, 2], [3, 4])", {}, 11.0),
    ("unit([0, 2])", {}, [0.0, 1.0]),
    ("{'a': 1, 'b': [1, 2]}", {}, {"a": 1, "b": [1, 2]}),
    ("True == 1", {}, True),
    ("1 < 2 < 3", {}, True),
    ("-x", {"x": 4}, -4),
])
def test_the_expression_language_matches_ch_py(src, env, want):
    import json
    from membrane import ch as CH
    try:
        py = CH.evaluate(src, dict(env))
    except Exception as e:                       # noqa: BLE001
        py = ("refused", type(e).__name__)
    try:
        nat = json.loads(MV.evaluate(src, json.dumps(env)))
    except Exception as e:                       # noqa: BLE001
        nat = ("refused", type(e).__name__)
    if isinstance(py, tuple):
        assert isinstance(nat, tuple), (src, py, nat)
    else:
        assert nat == py, (src, py, nat)
        assert type(nat) is type(py) or isinstance(py, (list, dict)), (src, py, nat)


@pytest.mark.parametrize("src", ["x[0]", "lambda: 1", "a if b else c", "__import__('os')", "f(x=1)",
                                 "x ** 2", "x // 2", "{1, 2}", "[i for i in x]"])
def test_what_ch_py_refuses_the_native_parser_refuses(src):
    import json
    from membrane import ch as CH
    with pytest.raises(Exception):
        CH._check(CH._parse(src), {"x", "a", "b", "c"})
    with pytest.raises(Exception):
        MV.evaluate(src, json.dumps({"x": [1], "a": 1, "b": 1, "c": 1}))
