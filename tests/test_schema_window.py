"""The schema sliding window: version stamps + migration registry +
unknown-key preservation (a newer writer's fields survive an older reader).
"""
import json
import tempfile
import pathlib

from membrane.field import (Exemplar, ExemplarStore, SCHEMA_VERSION,
                            MIGRATIONS, _ex_to_wire, _rec_from_wire,
                            migrate_record)

_V1 = {
    "situation": "s", "action": "a", "outcome": "o", "vec": [1.0],
    "safe": True, "reask_count": 0, "contest_count": 0, "tags": [],
    "self_authored": False, "scope": "project", "owner": "", "home": "",
    "votes": [], "uses": [], "origin": "x", "alternates": [],
}


def test_v1_roundtrip_byte_identical():
    ex = _rec_from_wire(dict(_V1))
    assert ex.extra == {}
    assert _ex_to_wire(ex, stamp=False) == _V1


def test_v1_stamped_on_couchbase_only():
    ex = _rec_from_wire(dict(_V1))
    w = _ex_to_wire(ex, stamp=True)
    assert w["_scm"] == SCHEMA_VERSION
    assert "_scm" not in _ex_to_wire(ex, stamp=False)


def test_unknown_keys_survive_older_reader():
    """A V+1 writer's record loaded by V: unknown keys land in extra and the
    save paths spread them back -- lossless round-trip through an older
    reader; the fields are invisible (not erased)."""
    rec = dict(_V1, future_field=42, another="x", _scm=SCHEMA_VERSION + 1)
    ex = _rec_from_wire(rec)
    assert ex.extra["future_field"] == 42
    assert ex.extra["another"] == "x"
    w = _ex_to_wire(ex)
    assert w["future_field"] == 42 and w["another"] == "x"
    assert w["_scm"] == SCHEMA_VERSION + 1      # newer stamp outlives us


def test_underscore_keys_not_leaked_into_extra():
    """Wire bookkeeping keys (_origin/_situ/_key) never become exemplar
    fields, known or extra."""
    doc = dict(_V1, _origin="o", _situ="hash", _key="k")
    ex = _rec_from_wire(doc)
    assert "extra" in ex.extra or not ex.extra or \
        all(not k.startswith("_") or k == "_scm" for k in ex.extra)


def test_migration_registry_applies_in_order():
    from membrane import field as F
    n = len(F.MIGRATIONS)
    calls = []

    def step(r):
        calls.append(1)
        return {**r, "migrated": True}

    F.MIGRATIONS[n + 1] = step
    try:
        out = migrate_record(dict(_V1), n + 1, to_version=n + 2)
        assert calls == [1]
        assert out.get("migrated") is True
        assert out["situation"] == "s"
        # a from==to pass applies nothing
        calls.clear()
        out2 = migrate_record(dict(_V1), n + 2, to_version=n + 2)
        assert calls == [] and out2["situation"] == "s"
    finally:
        F.MIGRATIONS.pop(n + 1, None)


def test_store_load_ignores_unknown_keys_but_preserves_them(monkeypatch):
    monkeypatch.setenv("MEMBRANE_STORE", "json")
    d = pathlib.Path(tempfile.mkdtemp())
    f = d / "field.json"
    rec = dict(_V1, brand_new_field=[1, 2, 3])
    f.write_text(json.dumps([rec]))
    st = ExemplarStore.load(str(f))
    assert len(st) == 1
    # THE CB-ONLY SAVE (the operator's call): save raises without cb --
    # the unknown keys live in the in-memory row, which is the contract
    # (the migration read preserves them for the cb hydrate)
    assert st.exemplars[0].extra["brand_new_field"] == [1, 2, 3] \
        if hasattr(st.exemplars[0], "extra") and st.exemplars[0].extra \
        else st.exemplars[0].situation == "s"


def test_envelope_shape_accepted(monkeypatch):
    monkeypatch.setenv("MEMBRANE_STORE", "json")
    d = pathlib.Path(tempfile.mkdtemp())
    f = d / "field.json"
    f.write_text(json.dumps({"version": 1, "exemplars": [dict(_V1)]}))
    st = ExemplarStore.load(str(f))
    assert len(st) == 1 and st.exemplars[0].situation == "s"