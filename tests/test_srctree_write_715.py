'''#715 part B, the write path: `entity_edit(<symbol>, properties={source: ...})`
replaces exactly that symbol's byte span (the rest of the file byte-identical),
re-parses the file (a source that does not parse is refused and the file is
untouched), re-reads the symbols from disk, and records a result entity (old and
new hash, span, the file's new mtime), as code_run does. Its gates are code's:
the pause and the lane gate (as for entity_edit), and the owner.'''
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault('MEMBRANE_STORE', 'json')
os.environ.setdefault('MEMBRANE_RUN', '0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import srctree as ST  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

PY = '''import os


class Pump:
    def run(self, n):
        return n + 1

    def stop(self):
        return 0


def drain(x):
    return x
'''


def _world(text=PY):
    d = Path(tempfile.mkdtemp())
    f = d / 'boat.py'
    f.write_text(text)
    est = EntityStore(path=None, shared=False)
    srv = types.SimpleNamespace(entity_store=est)
    est._srv = srv
    ST.sync_file(srv, 'boat.py', f, MEMBRANE_NS + 'files:boat.py')
    return srv, est, f


def _sym(q):
    return ST.eid_of('boat.py', q)


def test_editing_one_method_changes_only_its_span_and_keeps_its_id():
    srv, est, f = _world()
    old = f.read_bytes()
    a, z = est.docs[_sym('Pump.run')].properties['span']
    new = 'def run(self, n):\n        return n * 2'               # a symbol's source starts at its def
    out = ST.edit_source(srv, _sym('Pump.run'), new)
    assert out['ok'], out
    b = f.read_bytes()
    assert b == old[:a] + new.encode() + old[z:]                   # the rest byte-identical
    assert ST.symbols(b, 'py') is not None                         # still parses
    p = est.docs[_sym('Pump.run')].properties
    assert p['status'] == ST.LIVE and p['span'] == [a, a + len(new)]
    assert ST.source(srv, _sym('Pump.stop')) == 'def stop(self):\n        return 0'   # moved, unchanged
    assert ST.source(srv, _sym('Pump.run')) == new


def test_the_edit_records_a_result_entity():
    srv, est, f = _world()
    before = est.docs[_sym('drain')].properties['hash']
    out = ST.edit_source(srv, _sym('drain'), 'def drain(x):\n    return -x')
    r = est.docs[out['result']].properties
    assert r['symbol'] == _sym('drain') and r['old_hash'] == before
    assert r['new_hash'] == est.docs[_sym('drain')].properties['hash'] != before
    assert r['span'] and r['mtime'] == f.stat().st_mtime
    assert est.docs[_sym('drain')].properties['last_edit'] == out['result']


def test_a_source_that_does_not_parse_is_refused_and_the_file_untouched():
    srv, est, f = _world()
    old, mt = f.read_bytes(), f.stat().st_mtime_ns
    out = ST.edit_source(srv, _sym('drain'), 'def drain(x:\n    return')
    assert not out['ok'] and 'parse' in out['error']
    assert f.read_bytes() == old and f.stat().st_mtime_ns == mt


def test_a_rename_makes_a_new_entity_and_retires_the_old():
    srv, est, f = _world()
    out = ST.edit_source(srv, _sym('drain'), 'def empty(x):\n    return x')
    assert out['ok']
    assert est.docs[_sym('drain')].properties['status'] == ST.RETIRED
    assert est.docs[_sym('empty')].properties['status'] == ST.LIVE


def test_a_file_changed_on_disk_is_re_read_before_the_edit():
    srv, est, f = _world()
    f.write_text('# a header line someone added\n' + PY)            # an editor, a checkout
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    out = ST.edit_source(srv, _sym('drain'), 'def drain(x):\n    return 0')
    assert out['ok']
    assert f.read_text().startswith('# a header line someone added\n')
    assert f.read_text().endswith('def drain(x):\n    return 0\n')


def test_a_retired_or_unknown_symbol_is_refused():
    srv, est, f = _world()
    assert not ST.edit_source(srv, _sym('nope'), 'x = 1')['ok']
    ST.edit_source(srv, _sym('drain'), 'def empty(x):\n    return x')
    out = ST.edit_source(srv, _sym('drain'), 'def drain(x):\n    return x')
    assert not out['ok'] and 'retired' in out['error']


def test_the_seat_route_needs_the_owner_and_leaves_other_props_to_the_edit():
    srv, est, f = _world()
    old = f.read_bytes()
    props = {'source': 'def drain(x):\n    return 7', 'note': 'n'}
    out = ST.source_write(srv, _sym('drain'), dict(props), owner=False)
    assert out['refused'] and 'owner' in out['error'] and f.read_bytes() == old
    out = ST.source_write(srv, _sym('drain'), dict(props), owner=True)
    assert out['edit']['ok'] and out['properties'] == {'note': 'n'}
    assert b'return 7' in f.read_bytes()
    assert ST.source_write(srv, MEMBRANE_NS + 'Component:x', dict(props), owner=False) is None
    assert ST.source_write(srv, _sym('drain'), {'note': 'n'}, owner=False) is None


def _handler(srv, body, owner, paused=False):
    sent = []
    h = types.SimpleNamespace(
        client_address=('127.0.0.1' if owner else '192.168.1.50', 1), headers={}, server=srv,
        _paused_write_refused=lambda: paused and (sent.append(423) or True),
        _json_body=lambda: dict(body), _json_err_raw=lambda c, m: sent.append((c, m)),
        _json_out=sent.append, _entity_audit=lambda *a: None)
    return h, sent


def _route(srv, body, owner, paused=False):
    from unittest import mock
    from membrane import jwtauth as J, lanes as L, seatauth as SA
    h, sent = _handler(srv, body, owner, paused)
    with mock.patch.object(SA, 'owner', lambda hh: owner), \
            mock.patch.object(J, 'send_unauthorized', lambda hh: sent.append(401)), \
            mock.patch.object(L, '_pulse_edit', lambda *a: None), \
            mock.patch.object(L, '_entity_post_process', lambda *a: None), \
            mock.patch.object(L, '_active_model_hook', lambda *a: None):
        L.MembraneOpenAIHandler._entity_edit(h)
    return sent


def test_the_edit_route_writes_the_file_for_the_owner_only_and_never_paused():
    srv, est, f = _world()
    old = f.read_bytes()
    body = {'spec': _sym('drain'), 'properties': {'source': 'def drain(x):\n    return 9'}}
    assert _route(srv, body, owner=False) == [401] and f.read_bytes() == old
    assert _route(srv, body, owner=True, paused=True) == [423] and f.read_bytes() == old
    sent = _route(srv, body, owner=True)
    assert sent[-1]['ok'] and sent[-1]['source_edit']['ok'], sent
    assert b'return 9' in f.read_bytes()
    assert 'source' not in est.docs[_sym('drain')].properties          # the file holds it, not the entity


def test_a_bad_source_through_the_route_says_why_and_writes_nothing():
    srv, est, f = _world()
    old = f.read_bytes()
    sent = _route(srv, {'spec': _sym('drain'), 'properties': {'source': 'def ('}}, owner=True)
    assert sent[0][0] == 400 and 'parse' in sent[0][1] and f.read_bytes() == old
