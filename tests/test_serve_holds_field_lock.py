"""#127: the seat's entity/read door must REFUSE clearly when the live
serve holds the field writer lock -- a 423 'serve-holds-field-lock' JSON
error -- instead of firing the getter into a locked field and either
silently returning stale data or dropping the connection.

The read with nofire=1 (the cached value, no getter) must keep working:
it needs no field, so it is not a refusal, it is the honest fallback.
"""

import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lanes as L  # noqa: E402
from membrane.entities import CLASS_CONFIG, EntityStore, QuantumEntity  # noqa: E402


def _srv():
    est = EntityStore(path=None, shared=False)
    est.docs["membrane::Config:probe"] = QuantumEntity(
        eid="membrane::Config:probe", cls=CLASS_CONFIG,
        properties={"name": "probe", "on": True})
    return types.SimpleNamespace(
        entity_store=est,
        efield=None,                       # the live serve holds the lock
        efield_locked=True,
        capturer=None,
        membrane=types.SimpleNamespace())


def _call(srv, qs):
    err, out = [], []
    r = types.SimpleNamespace(
        path="/membrane/entity/read" + qs,
        server=srv,
        _json_err_raw=lambda code, msg: err.append((code, msg)),
        _json_out=out.append)
    L.MembraneOpenAIHandler._entity_read(r)
    return err, out


class TestServeHoldsFieldLock(unittest.TestCase):
    def test_run_refuses_423_when_the_field_is_locked(self):
        err, out = _call(_srv(), "?spec=membrane::Config:probe&run=1")
        self.assertEqual(out, [])                 # no 200 with stale data
        self.assertEqual(len(err), 1)
        code, msg = err[0]
        self.assertEqual(code, 423)
        self.assertIn("serve-holds-field-lock", msg)

    def test_nofire_still_returns_the_cached_value(self):
        err, out = _call(_srv(), "?spec=membrane::Config:probe&nofire=1")
        self.assertEqual(err, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["eid"], "membrane::Config:probe")
        self.assertEqual(out[0]["getter"]["fired"], False)

    def test_unlocked_field_read_does_not_refuse(self):
        srv = _srv()
        srv.efield_locked = False
        err, out = _call(srv, "?spec=membrane::Config:probe&run=1")
        self.assertEqual(err, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["eid"], "membrane::Config:probe")


if __name__ == "__main__":
    unittest.main()
