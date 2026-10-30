"""#715 part A: the project's Python source as symbol entities over
tree-sitter (read-only). One entity per symbol (module, class, function or
method) with a stable id -- the file path plus the qualified name
(membrane::src:<path>::<qualname>) -- and its content hash and byte/line
span as properties. Files are the source of truth: codegraph's mtime sweep
(stale_paths) re-reads a file changed outside the field; a symbol gone from
its file is retired (and comes back if the file brings it back)."""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lang as LG  # noqa: E402
from membrane import srctree as ST  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

SRC = '''"""a module"""
import os


@dec
class Pump(Base):
    """a pump"""
    def run(self, n):
        def inner():
            return n
        return inner()


async def drain(x: int) -> int:
    return x
'''


def _seat(src=SRC):
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "boat.py").write_text(src)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "loose.py").write_text("def stray():\n    pass\n")      # not in a package
    est = EntityStore(path=None, shared=False)
    srv = types.SimpleNamespace(entity_store=est)
    est._srv = srv
    ST.attach(srv, root)
    return srv, est, root


def _live(est, under=ST.NS + "pkg/boat.py"):
    return {e for e, d in est.docs.items() if e.startswith(under)
            and (d.properties or {}).get("status") != ST.RETIRED}


def _touch(p: Path, text: str):
    p.write_text(text)
    t = time.time() + 5
    os.utime(p, (t, t))


def test_round_trip_every_py_file_in_the_repo():
    bad = []
    for p in sorted(REPO.rglob("*.py")):
        if any(part in (".git", ".venv", "node_modules", ".claude") for part in p.parts):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text.strip() or "\x00" in text:
            continue
        try:
            if LG.print_(LG.parse("py", text)) != text:
                bad.append(str(p))
        except ValueError:
            pass                          # a fixture that is not valid Python
    assert bad == []


def test_one_entity_per_symbol_with_a_stable_id():
    srv, est, root = _seat()
    ST.sweep(srv)
    ns = ST.NS + "pkg/boat.py"
    assert _live(est) == {ns, ns + "::Pump", ns + "::Pump.run", ns + "::Pump.run.inner", ns + "::drain"}
    run = est.docs[ns + "::Pump.run"].properties
    assert run["kind"] == "method" and run["path"] == "pkg/boat.py" and run["qualname"] == "Pump.run"
    a, b = run["span"]
    assert SRC.encode()[a:b].decode().startswith("def run(self, n):")
    assert run["lines"][0] == 8 and run["hash"]
    pump = est.docs[ns + "::Pump"].properties
    assert SRC.encode()[pump["span"][0]:].decode().startswith("@dec")   # the decorator is its own
    assert est.docs[ns + "::Pump.run"].properties["parent"] == ns + "::Pump"


def test_reading_a_symbol_shows_its_code_from_disk():
    srv, est, root = _seat()
    ST.sweep(srv)
    assert ST.source(srv, ST.NS + "pkg/boat.py::drain") == "async def drain(x: int) -> int:\n    return x"


def test_a_body_edit_keeps_the_id_a_rename_retires_the_old():
    srv, est, root = _seat()
    ST.sweep(srv)
    ns = ST.NS + "pkg/boat.py"
    h0 = est.docs[ns + "::drain"].properties["hash"]
    _touch(root / "pkg" / "boat.py", SRC.replace("return x", "return x + 1"))
    ST.sweep(srv)
    assert ns + "::drain" in _live(est)
    assert est.docs[ns + "::drain"].properties["hash"] != h0
    _touch(root / "pkg" / "boat.py", SRC.replace("def drain", "def empty"))
    ST.sweep(srv)
    assert ns + "::empty" in _live(est) and ns + "::drain" not in _live(est)
    assert est.docs[ns + "::drain"].properties["status"] == ST.RETIRED


def test_outside_edits_and_deletes_update_on_the_next_sweep():
    srv, est, root = _seat()
    ST.sweep(srv)
    ns = ST.NS + "pkg/boat.py"
    _touch(root / "pkg" / "boat.py", SRC.split("\n\nasync def")[0] + "\n")
    ST.sweep(srv)
    assert ns + "::drain" not in _live(est)
    (root / "pkg" / "boat.py").unlink()
    ST.sweep(srv)
    assert _live(est) == set()
    _touch(root / "pkg" / "boat.py", SRC)                  # a checkout brings it back
    ST.sweep(srv)
    assert ns + "::drain" in _live(est)


def test_a_half_written_file_keeps_its_symbols():
    srv, est, root = _seat()
    ST.sweep(srv)
    _touch(root / "pkg" / "boat.py", SRC + "\ndef broken(:\n")
    ST.sweep(srv)
    assert ST.NS + "pkg/boat.py::drain" in _live(est)


def test_an_unchanged_tree_sweeps_nothing():
    srv, est, root = _seat()
    ST.sweep(srv)
    assert ST.sweep(srv) == {"files": 0, "upserted": 0, "retired": 0}


def test_only_the_package_dirs_are_swept():
    """The scope card's pick: a top-level dir with an __init__.py is a package."""
    srv, est, root = _seat()
    ST.sweep(srv)
    assert _live(est, ST.NS + "loose.py") == set()
    assert ST.NS + "pkg/__init__.py" in _live(est, ST.NS)


def test_a_symbol_read_carries_its_source():
    from membrane import night as N
    srv, est, root = _seat()
    ST.sweep(srv)
    val = N.executor_value(est, ST.NS + "pkg/boat.py::drain")
    assert val["source"].startswith("async def drain")


def test_a_pyproject_names_the_packages():
    srv, est, root = _seat()
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_x.py").write_text("def test_a():\n    pass\n")
    (root / "pyproject.toml").write_text('[tool.setuptools]\npackages = ["pkg"]\n')
    ST.sweep(srv)
    assert _live(est, ST.NS + "tests/") == set()
    assert ST.NS + "pkg/boat.py::drain" in _live(est)
