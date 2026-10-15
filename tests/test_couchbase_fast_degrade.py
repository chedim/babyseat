"""FAST COUCHBASE DEGRADE: a seat with no local Couchbase cluster must not
hang the boot in SDK connection timeouts. The door-visibility tests boot a
real seat and were green solo / red in CI -- CI installs the couchbase SDK
(the [couchbase] extra) but has no cluster, so the entities write-through
and the peers substrate each sat in a multi-second SDK connect and the
~50s total boot missed the fixture's 40s deadline. What they test never
needed couchbase: the probe that already gates the FIELD backend
(probe_local_couchbase, a ~1s HTTP GET /pools) must gate the entities and
peers connects too, so a probe-miss skips the SDK entirely instead of
timing out."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _probe_false(monkeypatch):
    from membrane import efstore
    monkeypatch.setattr(efstore, "probe_local_couchbase", lambda: False)


def _record_sdk(monkeypatch):
    """Swap couchbase.cluster.Cluster for a recorder that fails fast: a
    broken gate then fails the test instantly instead of hanging in an SDK
    timeout. record is non-empty iff the code reached the SDK connect."""
    import couchbase.cluster as _cc
    record = []

    def _boom(*a, **k):
        record.append("Cluster")
        raise RuntimeError("couchbase SDK must not be touched")
    monkeypatch.setattr(_cc, "Cluster", _boom)
    return record


def test_peers_substrate_skips_the_sdk_when_couchbase_is_down(monkeypatch, tmp_path):
    _probe_false(monkeypatch)
    record = _record_sdk(monkeypatch)
    from membrane.peers import PeerSubstrate
    ps = PeerSubstrate(field_path=str(tmp_path / "field.json"),
                       port=0, model="m", roster_fn=None)
    assert ps._cluster is None
    assert record == [], record


def test_entity_store_skips_the_sdk_when_couchbase_is_down(monkeypatch, tmp_path):
    _probe_false(monkeypatch)
    record = _record_sdk(monkeypatch)
    from membrane.entities import EntityStore
    est = EntityStore(path=str(tmp_path / "entities.json"), shared=True)
    assert est._entities_collection() is None
    assert record == [], record
