"""THE CLUSTER-HANDLE CACHE (the memory-leak fix, unit-tested): the
entity store's _entities_collection() constructed a FRESH couchbase
Cluster on EVERY call -- and the write-through calls it per entity
upsert. Each Cluster spawns its own SDK io workers (+2 threads per
construction, measured: 5 constructions = +10 live threads) and is
never closed; over a session the leaked workers/buffers piled into GBs
(the operator's 30GB Terminal tree with two seats). The fix: ONE
collection handle per store, constructed once, reused for every read
and write. The test pins the handle-caching contract WITHOUT couchbase
(the construct path is mocked -- the guard is the caching, not the
connection)."""

import unittest
from unittest import mock

from membrane.entities import EntityStore


class _FakeBucket:
    def __init__(self):
        self.collections_calls = 0

    def scope(self, _s):
        return self

    def collection(self, _c):
        return object()          # a distinct sentinel handle per construct

    def collections(self):
        self.collections_made += 1
        raise RuntimeError("no mgmt in test")


class _FakeCluster:
    def __init__(self, *a, **k):
        _FakeCluster.constructed += 1

    def bucket(self, _b):
        return _FakeBucket()


class TestEntityCollectionCache(unittest.TestCase):
    def setUp(self):
        _FakeCluster.constructed = 0
        self.store = EntityStore(bucket="test-leak", shared=False)

    def _patch_sdk(self):
        # patch the SDK imports INSIDE _entities_collection's module scope:
        # the fn does local imports, so patch the source modules.
        cb_cluster = mock.patch("couchbase.cluster.Cluster", _FakeCluster)
        cb_auth = mock.patch(
            "couchbase.auth.PasswordAuthenticator", lambda *a, **k: None)
        cb_opts = mock.patch(
            "couchbase.options.ClusterOptions",
            lambda *a, **k: None)
        return cb_cluster, cb_auth, cb_opts_guard(cb_opts=None, cb_auth=cb_auth,
                                                  cb_cluster=cb_cluster)

    def test_collection_constructed_once_and_reused(self):
        """The leak's guard: N write-throughs construct the cluster ONCE."""
        store = self.store
        store.shared = True
        store._cb_warned = False

        with mock.patch("couchbase.cluster.Cluster", _FakeCluster), \
                mock.patch("couchbase.auth.PasswordAuthenticator",
                           lambda *a, **k: None), \
                mock.patch("couchbase.options.ClusterOptions",
                           lambda *a, **k: None):
            c1 = store._entities_collection()
            n_after_first = _FakeCluster.constructed
            for _ in range(5):
                cN = store._entities_collection()
                self.assertIs(cN, c1, "the cached handle must be reused")
            self.assertEqual(_FakeCluster.constructed, 1,
                             "the Cluster must construct exactly ONCE "
                             "per store, not per call")

    def test_unshared_store_never_constructs(self):
        """The test-isolation guard holds: shared=False -> None, no SDK."""
        self.assertEqual(self.store._entities_collection(), None)
        self.assertEqual(_FakeCluster.constructed, 0)


def cb_opts_guard(cb_opts, cb_auth, cb_cluster):
    return mock.patch("couchbase.options.ClusterOptions",
                      lambda *a, **k: None)


if __name__ == "__main__":
    unittest.main()
