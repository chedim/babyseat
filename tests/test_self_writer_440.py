"""#440 part 1: ONE WRITER FOR self/.

Blurp runs in its own process (#440); that child owns <data dir>/self/
(the record, its archive, mode, energy, world). Every write under self/
goes through one interface, membrane.self_writer: no other module opens,
replaces, truncates, removes or creates anything there, and the seat-side
writers (the panel's accept / set_mode, energy) call the writer, not
self_loop's file helpers.

Two checks: at run time (every write under self/ while a seat boots, is
switched on, accepts, sleeps, wakes, consolidates, recovers and errs has
self_writer on its stack) and in the source (no file-writing call in the
self modules outside the writer, no seat-side writer reaching past it).
"""
import ast
import builtins
import io
import json
import os
import pathlib
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import energy as E  # noqa: E402
from membrane import seat_panel as SP  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_writer as W  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

PKG = ROOT / "membrane"
WRITER_FILE = os.sep + os.path.join("membrane", "self_writer.py")
SELF_MODULES = ("self_loop", "energy", "self_world", "chat_io", "seat_panel", "self_sheet", "self_name")
SEAT_SIDE = ("energy", "chat_io", "seat_panel")
REACH_PAST = {"_append", "_save_mode", "set_mode", "accept", "consolidate"}


def _boot(lines):
    import tempfile
    import types
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    p = d / "self" / "record.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in lines))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv, d


def _kinds(srv, k):
    return [r for r in SL._read(srv) if r.get("kind") == k]


# ---- the run-time guard -----------------------------------------------------------------------

def _writer_on_stack() -> bool:
    f = sys._getframe(1)
    while f is not None:
        if f.f_code.co_filename.endswith(WRITER_FILE):
            return True
        f = f.f_back
    return False


class _Guard:
    """Every file-writing primitive, wrapped: a write under `root` is noted
    with whether self_writer was on the stack."""

    def __init__(self, root: Path):
        self.root = os.path.realpath(str(root))
        self.seen: list[tuple[str, str, bool]] = []

    def _under(self, p) -> bool:
        try:
            rp = os.path.realpath(os.fspath(p))
        except TypeError:
            return False                            # a file descriptor
        return rp == self.root or rp.startswith(self.root + os.sep)

    def _note(self, op, *paths):
        for p in paths:
            if self._under(p):
                self.seen.append((op, os.fspath(p), _writer_on_stack()))

    def install(self, mp):
        g = self

        def wrap_path_fn(mod, name, n=1):
            orig = getattr(mod, name)

            def w(*a, **k):
                g._note(f"{mod.__name__}.{name}", *a[:n])
                return orig(*a, **k)
            mp.setattr(mod, name, w)

        for name, n in (("replace", 2), ("rename", 2), ("remove", 1), ("unlink", 1), ("truncate", 1),
                        ("mkdir", 1), ("makedirs", 1), ("rmdir", 1)):
            wrap_path_fn(os, name, n)

        def wrap_open(mod, name):
            orig = getattr(mod, name)

            def w(file, mode="r", *a, **k):
                if any(c in str(mode) for c in "wax+"):
                    g._note(f"open({mode})", file)
                return orig(file, mode, *a, **k)
            mp.setattr(mod, name, w)

        wrap_open(builtins, "open")
        wrap_open(io, "open")
        orig_popen = pathlib.Path.open

        def popen(self, mode="r", *a, **k):
            if any(c in str(mode) for c in "wax+"):
                g._note(f"Path.open({mode})", self)
            return orig_popen(self, mode, *a, **k)
        mp.setattr(pathlib.Path, "open", popen)
        for name in ("write_text", "write_bytes", "touch", "unlink", "mkdir", "rename", "replace", "rmdir"):
            orig = getattr(pathlib.Path, name)

            def pw(self, *a, _o=orig, _n=name, **k):
                g._note(f"Path.{_n}", self)
                return _o(self, *a, **k)
            mp.setattr(pathlib.Path, name, pw)
        return self

    def strays(self):
        return sorted({(op, p) for op, p, ok in self.seen if not ok})


def test_every_write_under_self_goes_through_the_writer(monkeypatch):
    srv, d = _boot([{"ts": 1.0, "kind": "birth", "by": "operator"}])
    sd = d / "self"
    g = _Guard(sd).install(monkeypatch)

    SL.bind(srv)                                            # a restart: the migrations, the world
    SP.control(srv, {"self": "on"})                         # the panel's switch
    SL._append(srv, {"ts": 200.0, "kind": "learn"})
    SP.control(srv, {"accept": "chat"}, by="ha:alice")      # the panel's accept
    with mock.patch.object(E, "_avail_ram", return_value=None):
        E.error(srv, "a wrong use", now=3001.0)             # energy's own file + an error line
        st = E._state(srv)
        E._fall_asleep(srv, st, 3002.0, "energy", {})      # sleep: the consolidation
        E._save(srv, st)
        E._wake(srv, st, 3003.0, "rested", None, None)
    SL._append(srv, {"ts": 3004.0, "kind": "wake", "why": "x"})
    with mock.patch.object(SL, "_c_manifest", side_effect=OSError("disk full")):
        SL.consolidate(srv, 3005.0)                         # a failed one: undone
    W.save_json(srv, SL.CONSOLIDATING, {"id": "x", "at": 1.0, "stage": "archive", "sizes": {}, "manifest": None})
    SL._recover(srv)                                        # a journal left by a death: undone
    W.save_json(srv, SL.CONSOLIDATING, {"id": "y", "at": 1.0, "stage": "swap"})
    SL._recover(srv)                                        # ...and one at the swap: finished
    SP.control(srv, {"self": "off"})

    ops = {op for op, _p, _ok in g.seen}
    assert g.seen and len(ops) >= 3, ops                    # the guard saw the writes (not vacuous)
    assert g.strays() == [], g.strays()
    assert _kinds(srv, "accept")[-1]["by"] in ("ha:alice", "operator")
    assert json.loads((sd / "mode.json").read_text())["mode"] == "off"


# ---- the source -------------------------------------------------------------------------------

def _calls(mod: str):
    tree = ast.parse((PKG / f"{mod}.py").read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            yield n


def _base(f: ast.Attribute) -> str:
    v = f.value
    if isinstance(v, ast.Name):
        return v.id
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name):
        return v.func.id + "()"
    return ""


def _writes(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name) and f.id == "open":
        mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"),
                                                               None)
        if mode is None:
            return None
        if not isinstance(mode, ast.Constant) or any(c in str(mode.value) for c in "wax+"):
            return "open(write)"
        return None
    if isinstance(f, ast.Attribute):
        if _base(f) in ("_W", "W", "self_writer"):
            return None                             # a call into the writer is the route, not a write
        if f.attr in ("write_text", "write_bytes", "unlink", "truncate", "touch", "mkdir", "makedirs", "rmdir"):
            return f.attr
        if _base(f) == "os" and f.attr in ("replace", "rename", "remove", "fsync"):
            return "os." + f.attr
    return None


def test_no_self_module_writes_files_itself():
    found = []
    for mod in SELF_MODULES:
        for c in _calls(mod):
            w = _writes(c)
            if w:
                found.append(f"{mod}.py:{c.lineno} {w}")
    assert found == [], "write under self/ through membrane.self_writer: " + ", ".join(found)


def test_the_writer_holds_the_writes():
    kinds = {_writes(c) for c in _calls("self_writer")} - {None}
    assert {"open(write)", "os.replace", "unlink", "truncate", "mkdir"} <= kinds


def test_seat_side_writers_call_the_writer_not_self_loop():
    found = []
    for mod in SEAT_SIDE:
        for c in _calls(mod):
            f = c.func
            if isinstance(f, ast.Attribute) and f.attr in REACH_PAST and _base(f) in ("SL", "_SL()", "_SELF",
                                                                                     "self_loop"):
                found.append(f"{mod}.py:{c.lineno} {_base(f)}.{f.attr}")
    assert found == [], "seat-side writes go through membrane.self_writer: " + ", ".join(found)


def test_the_writer_interface():
    for name in ("dir_of", "append", "save_json", "set_mode", "accept", "consolidate",
                 "sync_write", "replace", "unlink", "truncate", "mkdir"):
        assert callable(getattr(W, name, None)), name
