"""Lazy hydration of the entity store (the boot is atrocious: 205-270 s). A store opened over a database keeps each
entity's raw record and builds the Python object on first access. Everything that asks for all of them (iteration,
.values(), .items()) builds each one as it goes, so behaviour is the same as the eager load; what stops is the boot's
own full scans (the signature loop, the #576 snapshot map, the first save)."""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import entities as E  # noqa: E402
from membrane import sqlitedb  # noqa: E402

N = 30


def _seed(tmp):
    """A database with N entities, written by an eager store."""
    db = sqlitedb.Database("field", str(tmp))
    old = E.EntityStore(path=str(Path(tmp) / "entities.json"), shared=False, cbl=db)
    for i in range(N):
        ent = E.QuantumEntity(eid=f"membrane::thing:{i}", cls="Component", properties={"name": f"t{i}", "n": i})
        ent.position = [0.5, float(i), 1.0]
        ent.position_history = [[1000.0, list(ent.position)]]
        old.docs[ent.eid] = ent
    old.docs["membrane::thing:eph"] = E.QuantumEntity(eid="membrane::thing:eph", cls="Component",
                                                      properties={"name": "eph", "ephemeral": True})
    old.save()
    db.close()
    return str(tmp)


@pytest.fixture
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _open(tmp):
    db = sqlitedb.Database("field", tmp)
    built = []
    real = E.QuantumEntity.from_doc

    def counting(doc_id, doc):
        if doc_id.startswith("membrane::thing:"):
            built.append(doc_id)
        return real(doc_id, doc)
    E.QuantumEntity.from_doc = staticmethod(counting)
    st = E.EntityStore(path=str(Path(tmp) / "entities.json"), shared=False, cbl=db)
    built[:] = [b for b in built if b.startswith("membrane::thing:")]     # the junk-anchor seeds are built at every boot
    return st, db, built, real


def _restore(real):
    E.QuantumEntity.from_doc = staticmethod(real)


def test_the_boot_builds_no_entity_object(tmp):
    _seed(tmp)
    st, db, built, real = _open(tmp)
    try:
        assert built == []                                   # nothing hydrated at load
        assert len(st.docs) >= N                             # but the store knows them all
        assert "membrane::thing:7" in st.docs
        assert set(st.docs) >= {f"membrane::thing:{i}" for i in range(N)}
        assert built == []                                   # keys, length and membership read no object
    finally:
        _restore(real)
        db.close()


def test_an_entity_is_built_on_first_access_once(tmp):
    _seed(tmp)
    st, db, built, real = _open(tmp)
    try:
        a = st.docs["membrane::thing:7"]
        b = st.docs.get("membrane::thing:7")
        assert a is b and a.properties["n"] == 7 and a.position == [0.5, 7.0, 1.0]
        assert built == ["membrane::thing:7"]                # once, and only that one
        assert st.docs.get("membrane::thing:missing") is None
    finally:
        _restore(real)
        db.close()


def test_iterating_everything_builds_everything_like_the_eager_load(tmp):
    _seed(tmp)
    eager = E.EntityStore(path=str(Path(tmp) / "x.json"), shared=False, cbl=sqlitedb.Database("field", tmp))
    want = {k: (v.cls, json.dumps(v.properties, sort_keys=True, default=str), v.position)
            for k, v in eager.docs.items()}
    eager._cbl.close()
    st, db, built, real = _open(tmp)
    try:
        got = {k: (v.cls, json.dumps(v.properties, sort_keys=True, default=str), v.position)
               for k, v in st.docs.items()}
        assert got == want
        assert set(built) == {k for k in want if k.startswith("membrane::thing:")}
        assert [v.eid for v in st.docs.values()] and dict(st.docs).keys() == want.keys()
    finally:
        _restore(real)
        db.close()


def test_a_save_after_the_boot_writes_nothing_and_builds_nothing(tmp):
    _seed(tmp)
    st, db, built, real = _open(tmp)
    try:
        puts = []
        rp = db.put_json
        db.put_json = lambda *a, **k: (puts.append(a[1]), rp(*a, **k))[1]
        st.save()
        assert puts == [] and built == []                    # nothing changed, nothing was built to find that out
    finally:
        _restore(real)
        db.close()


def test_an_edited_entity_is_the_only_one_saved(tmp):
    _seed(tmp)
    st, db, built, real = _open(tmp)
    try:
        puts = []
        rp = db.put_json
        db.put_json = lambda *a, **k: (puts.append(a[1]), rp(*a, **k))[1]
        st.docs["membrane::thing:3"].properties["note"] = "edited"
        st.save()
        assert len(puts) == 1 and built == ["membrane::thing:3"]
    finally:
        _restore(real)
        db.close()
    again, db2, _b, real2 = _open(tmp)
    try:
        assert again.docs["membrane::thing:3"].properties["note"] == "edited"
        assert "note" not in again.docs["membrane::thing:4"].properties
    finally:
        _restore(real2)
        db2.close()


def test_removing_an_unbuilt_entity_purges_it_at_the_next_save(tmp):
    _seed(tmp)
    st, db, built, real = _open(tmp)
    try:
        del st.docs["membrane::thing:5"]
        assert "membrane::thing:5" not in st.docs and built == []
        st.save()
    finally:
        _restore(real)
        db.close()
    again, db2, _b, real2 = _open(tmp)
    try:
        assert "membrane::thing:5" not in again.docs and "membrane::thing:6" in again.docs
    finally:
        _restore(real2)
        db2.close()


def test_a_row_that_is_no_entity_is_not_one(tmp):
    _seed(tmp)
    db = sqlitedb.Database("field", tmp)
    db.put_json("entities", "foreign", json.dumps({"e": "foreign", "d": {"no": "data"}}))
    db.close()
    st, db, built, real = _open(tmp)
    try:
        assert "foreign" not in st.docs
    finally:
        _restore(real)
        db.close()


def test_a_replaced_unbuilt_entity_is_the_new_one(tmp):
    _seed(tmp)
    st, db, built, real = _open(tmp)
    try:
        new = E.QuantumEntity(eid="membrane::thing:9", cls="Component", properties={"name": "fresh"})
        st.docs["membrane::thing:9"] = new
        assert st.docs["membrane::thing:9"] is new and "membrane::thing:9" not in built
    finally:
        _restore(real)
        db.close()


def test_the_boot_entity_finds_the_previous_boot_without_building_the_store(tmp):
    # live 2026-11-11: boot_entity scanned docs.values() for the previous boot and built all 61,794 entities at boot
    _seed(tmp)
    st, db, built, real = _open(tmp)
    try:
        from membrane import lanes as L
        prev = L.boot_entity(st, root=tmp, field="f", pid=1, argv=["a"], boot_ts=1000.0)
        cur = L.boot_entity(st, root=tmp, field="f", pid=2, argv=["b"], boot_ts=2000.0)
        assert cur.properties.get("prev_boot") == prev.eid
        assert built == []                                   # no `thing:` entity was built to find the previous boot
    finally:
        _restore(real)
        db.close()


def test_entities_with_a_property_are_found_without_building_the_rest(tmp):
    _seed(tmp)
    db = sqlitedb.Database("field", tmp)
    for i in range(3):
        db.put_json("entities", f"membrane::method:{i}", json.dumps({"e": f"membrane::method:{i}", "d": {
            "data": {"class": "Method", "properties": {"name": f"m{i}", "method_name": f"run{i}", "command": "echo"}}}}))
    db.close()
    st, db, built, real = _open(tmp)
    try:
        got = st.docs.with_property("method_name")
        assert sorted(k for k, _e in got) == [f"membrane::method:{i}" for i in range(3)]
        assert all(e.properties["method_name"] for _k, e in got)
        assert built == []                                   # none of the `thing:` entities was built to answer
    finally:
        _restore(real)
        db.close()


def test_boot_methods_builds_only_the_methods(tmp):
    _seed(tmp)
    st, db, built, real = _open(tmp)
    try:
        from types import SimpleNamespace
        from membrane import lang
        lang.boot_methods(SimpleNamespace(entity_store=st))
        assert built == []
    finally:
        _restore(real)
        db.close()
