"""The autonode tree scan walks natively (membrane_vm.fingerprints, GIL released once) and sees what the Python walk saw:
same files, same skips, same (mtime_ns, size)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from membrane import autonode as A  # noqa: E402

MV = pytest.importorskip("membrane.membrane_vm")


def _tree(tmp_path):
    for p, t in [("top.txt", "x"), ("a/one.py", "yy"), ("a/b/two.py", "zzz"), ("node_modules/x.js", "n"),
                 (".git/HEAD", "h"), ("c/serve-1.log", "s"), ("c/k.seed.json", "{}"), ("c/entities.json", "o")]:
        f = tmp_path / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(t)
    return tmp_path


def test_scan_uses_the_native_walk(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    calls = []
    real = MV.fingerprints
    monkeypatch.setattr(MV, "fingerprints", lambda *a: calls.append(a) or real(*a))
    s = A.FileTreeScanner(root)
    s.scan()
    assert calls, "the scan did not use membrane_vm.fingerprints"
    assert set(s._fps) == {"top.txt", os.path.join("a", "one.py"), os.path.join("a", "b", "two.py")}
    st = (root / "a" / "one.py").stat()
    assert s._fps[os.path.join("a", "one.py")] == (st.st_mtime_ns, st.st_size)


def test_native_scan_still_reports_changes(tmp_path):
    root = _tree(tmp_path)
    s = A.FileTreeScanner(root)
    assert s.scan() == []                       # the first scan primes
    (root / "a" / "new.py").write_text("n")
    ts = s.scan()
    assert any("new.py" in str(getattr(t, "subject", "") or getattr(t, "path", "") or t) for t in ts)
