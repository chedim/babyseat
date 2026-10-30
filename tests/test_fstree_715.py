'''#715 part A, reworked on the operator's model (2026-10-30): live code trees are
GENERIC and OPT-IN through entities. An entity that extends `fs:dir` (with a
`path`) gets that directory's file tree on the graph -- one `files:` entity per
file and per subdirectory (the existing file entities, isa `fs:file`), each
`within` its container -- kept by the autonode's fingerprint scan. A file entity
that also extends `fs:code` gets its symbol tree (module, class, function,
method; the grammar by extension). Nothing is scanned unless an entity asks.'''
import os
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ.setdefault('MEMBRANE_STORE', 'json')
os.environ.setdefault('MEMBRANE_RUN', '0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import fstree as FT  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import srctree as ST  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, MultiParentOps  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

PY = '''\"\"\"a module\"\"\"
import os


@dec
class Pump(Base):
    \"\"\"a pump\"\"\"
    def run(self, n):
        def inner():
            return n
        return inner()


async def drain(x: int) -> int:
    return x
'''
SH = 'greet() {\n  echo hi\n}\nfunction bye { echo bye; }\n'
JS = 'class Boat {\n  sail(w) { return w; }\n}\nfunction dock(b) { return b; }\n'


def _seat():
    seat = Path(tempfile.mkdtemp())
    proj = Path(tempfile.mkdtemp())
    (proj / 'pkg').mkdir()
    (proj / 'pkg' / 'boat.py').write_text(PY)
    (proj / 'top.txt').write_text('hi')
    (proj / '.venv').mkdir()
    (proj / '.venv' / 'junk.py').write_text('x = 1')
    (proj / 'node_modules').mkdir()
    (proj / 'node_modules' / 'dep.js').write_text('var a;')
    est = EntityStore(path=None, shared=False)
    srv = types.SimpleNamespace(entity_store=est)
    est._srv = srv
    FT.seed_classes(est)
    trees = FT.Trees(srv, est, seat_root=seat)
    return srv, est, trees, seat, proj


def _extend(est, name, path, cls=FT.DIR):
    r = est.add(cls='Component', name=name)
    d = est.docs[r['eid']]
    d.properties = {**(d.properties or {}), 'path': str(path)}
    est.upsert(d, embedder=None)
    assert MultiParentOps.set_parents(est, d.eid, [cls])['ok']
    return d.eid


def _f(p: Path) -> str:
    return FT.FILES + p.resolve().as_posix()


def _live(est, prefix):
    return {e for e, d in est.docs.items() if e.startswith(prefix)
            and (d.properties or {}).get('status') != FT.RETIRED}


def _touch(p: Path, text: str):
    p.write_text(text)
    t = time.time() + 5
    os.utime(p, (t, t))


def _code(est, trees, p: Path):
    assert MultiParentOps.set_parents(est, _f(p), [FT.CODE])['ok']
    trees.pump()


def test_round_trip_every_py_file_in_the_repo():
    bad = []
    for p in sorted(REPO.rglob('*.py')):
        if any(part in ('.git', '.venv', 'node_modules', '.claude') for part in p.parts):
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
        if not text.strip() or '\x00' in text:
            continue
        try:
            if LG.print_(LG.parse('py', text)) != text:
                bad.append(str(p))
        except ValueError:
            pass
    assert bad == []


def test_a_dir_entity_puts_its_file_tree_on_the_graph_linked():
    srv, est, trees, seat, proj = _seat()
    d = _extend(est, 'proj', proj)
    trees.pump()
    files = _live(est, FT.FILES)
    assert files == {_f(proj / 'pkg'), _f(proj / 'pkg' / 'boat.py'), _f(proj / 'top.txt')}
    boat = est.docs[_f(proj / 'pkg' / 'boat.py')].properties
    assert boat['within'] == _f(proj / 'pkg') and boat['kind'] == 'file'
    pkg = est.docs[_f(proj / 'pkg')].properties
    assert pkg['within'] == d and pkg['kind'] == 'dir'
    for e in files:                                 # fs:file, never fs:dir (only the asker scans)
        chain = {a.eid for a in MultiParentOps.all_parents(est, e)}
        assert FT.FILE in chain and FT.DIR not in chain


def test_a_new_file_appears_a_deleted_one_is_retired_and_comes_back():
    srv, est, trees, seat, proj = _seat()
    _extend(est, 'proj', proj)
    trees.pump()
    _touch(proj / 'pkg' / 'new.sh', 'echo')
    trees.pump()
    assert _f(proj / 'pkg' / 'new.sh') in _live(est, FT.FILES)
    (proj / 'top.txt').unlink()
    trees.pump()
    assert est.docs[_f(proj / 'top.txt')].properties['status'] == FT.RETIRED
    _touch(proj / 'top.txt', 'back')
    trees.pump()
    assert _f(proj / 'top.txt') in _live(est, FT.FILES)


def test_a_directory_no_entity_asks_for_is_never_scanned(monkeypatch):
    from membrane import autonode as AN
    walked = []
    real = AN.FileTreeScanner.scan
    monkeypatch.setattr(AN.FileTreeScanner, 'scan', lambda self: walked.append(self.root) or real(self))
    srv, est, trees, seat, proj = _seat()
    trees.pump()
    assert walked == [] and _live(est, FT.FILES) == set()


def test_the_existing_file_entity_is_the_file_no_duplicate_kind():
    srv, est, trees, seat, proj = _seat()
    (seat / 'docs').mkdir()
    (seat / 'docs' / 'a.md').write_text('# a')
    from membrane.entities import QuantumEntity
    old = MEMBRANE_NS + 'files:docs/a.md'                 # the seat's own repo-relative file entity
    est.upsert(QuantumEntity(eid=old, cls='Component', properties={'name': 'docs/a.md', 'mine': 1}),
               embedder=None)
    _extend(est, 'docs', seat / 'docs')
    trees.pump()
    assert est.docs[old].properties['mine'] == 1 and FT.FILE in est.docs[old].properties['parents']
    assert [e for e in est.docs if e.endswith('/a.md') or e.endswith(':a.md')] == [old]


def test_a_code_file_gets_its_symbol_tree_with_stable_ids():
    srv, est, trees, seat, proj = _seat()
    _extend(est, 'proj', proj)
    trees.pump()
    boat = proj / 'pkg' / 'boat.py'
    assert _live(est, ST.NS) == set()                     # only fs:code files get symbols
    _code(est, trees, boat)
    ns = ST.NS + boat.resolve().as_posix()
    assert _live(est, ns) == {ns, ns + '::Pump', ns + '::Pump.run', ns + '::Pump.run.inner', ns + '::drain'}
    run = est.docs[ns + '::Pump.run'].properties
    assert run['kind'] == 'method' and run['qualname'] == 'Pump.run' and run['lines'][0] == 8
    a, b = run['span']
    assert PY.encode()[a:b].decode().startswith('def run(self, n):')
    assert run['within'] == ns + '::Pump'
    assert MultiParentOps._parents_of(run) == []          # containment is not inheritance
    pump = est.docs[ns + '::Pump'].properties
    assert PY.encode()[pump['span'][0]:].decode().startswith('@dec')
    assert est.docs[ns].properties['within'] == _f(boat)


def test_the_grammar_is_picked_by_extension():
    srv, est, trees, seat, proj = _seat()
    (proj / 'run.sh').write_text(SH)
    (proj / 'boat.js').write_text(JS)
    _extend(est, 'proj', proj)
    trees.pump()
    _code(est, trees, proj / 'run.sh')
    _code(est, trees, proj / 'boat.js')
    sh = ST.NS + (proj / 'run.sh').resolve().as_posix()
    js = ST.NS + (proj / 'boat.js').resolve().as_posix()
    assert {est.docs[e].properties['kind'] for e in _live(est, sh + '::')} == {'function'}
    assert _live(est, sh + '::') == {sh + '::greet', sh + '::bye'}
    assert est.docs[js + '::Boat'].properties['kind'] == 'class'
    assert est.docs[js + '::Boat.sail'].properties['kind'] == 'method'
    assert est.docs[js + '::dock'].properties['kind'] == 'function'


def test_an_edit_updates_the_symbols_a_rename_retires_the_old():
    srv, est, trees, seat, proj = _seat()
    _extend(est, 'proj', proj)
    trees.pump()
    boat = proj / 'pkg' / 'boat.py'
    _code(est, trees, boat)
    ns = ST.NS + boat.resolve().as_posix()
    h0 = est.docs[ns + '::drain'].properties['hash']
    _touch(boat, PY.replace('return x', 'return x + 1'))
    trees.pump()
    assert est.docs[ns + '::drain'].properties['hash'] != h0
    _touch(boat, PY.replace('def drain', 'def empty'))
    trees.pump()
    assert ns + '::empty' in _live(est, ns) and ns + '::drain' not in _live(est, ns)
    assert est.docs[ns + '::drain'].properties['status'] == FT.RETIRED


def test_a_half_written_file_keeps_its_symbols_and_a_deleted_one_retires_them():
    srv, est, trees, seat, proj = _seat()
    _extend(est, 'proj', proj)
    trees.pump()
    boat = proj / 'pkg' / 'boat.py'
    _code(est, trees, boat)
    ns = ST.NS + boat.resolve().as_posix()
    _touch(boat, PY + '\ndef broken(:\n')
    trees.pump()
    assert ns + '::drain' in _live(est, ns)
    boat.unlink()
    trees.pump()
    assert _live(est, ns) == set()


def test_reading_a_symbol_shows_its_code_from_disk():
    from membrane import night as N
    srv, est, trees, seat, proj = _seat()
    _extend(est, 'proj', proj)
    trees.pump()
    boat = proj / 'pkg' / 'boat.py'
    _code(est, trees, boat)
    eid = ST.NS + boat.resolve().as_posix() + '::drain'
    assert ST.source(srv, eid) == 'async def drain(x: int) -> int:\n    return x'
    assert N.executor_value(est, eid)['source'].startswith('async def drain')


def test_a_big_tree_builds_in_paced_chunks(monkeypatch):
    monkeypatch.setattr(FT, 'BUILD_CHUNK', 2)
    srv, est, trees, seat, proj = _seat()
    for i in range(3):
        (proj / f'n{i}.txt').write_text(str(i))       # 5 files in all
    _extend(est, 'proj', proj)

    def files():
        return len([e for e in _live(est, FT.FILES) if est.docs[e].properties['kind'] == 'file'])
    seen = []
    for _ in range(4):
        trees.pump()
        seen.append(files())
    assert seen == [2, 4, 5, 5]


def test_the_autonode_pumps_the_trees_on_its_scan_with_no_watcher():
    from membrane.autonode import Autonode
    srv, est, trees, seat, proj = _seat()
    _extend(est, 'proj', proj)
    an = Autonode(srv, est, root=seat, scan_s=0.5)
    try:
        an.pump()
        assert _f(proj / 'top.txt') in _live(est, FT.FILES)
    finally:
        an.alive = False
        an.pool.shutdown(wait=False)


def test_symbol_trees_share_the_paced_budget(monkeypatch):
    srv, est, trees, seat, proj = _seat()
    for i in range(3):
        (proj / f'm{i}.py').write_text(PY)                # 5 symbols each
    _extend(est, 'proj', proj)
    trees.pump()
    for i in range(3):
        MultiParentOps.set_parents(est, _f(proj / f'm{i}.py'), [FT.CODE])
    monkeypatch.setattr(FT, 'BUILD_CHUNK', 6)
    mods = []
    for _ in range(4):
        trees.pump()
        mods.append(len([e for e in _live(est, ST.NS) if e.count('::') == 1]))
    assert mods == [2, 3, 3, 3]                      # a file at a time while budget remains
