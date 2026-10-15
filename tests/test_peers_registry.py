"""ISSUE #114 (advertised-port vs bound-port divergence): the proxy registry
keyed each seat's doc by its ephemeral port (`proxy-<port>`), so a seat that
relaunched on a shifted port left its OLD doc alive for the TTL window --
peers probed the closed port and misdiagnosed a live seat as dead. The
registry now keys the doc by the seat's FIELD (a stable id), so a relaunch on
any port overwrites the same row and the roster always carries the port the
seat actually bound."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.global_field import field_id_for  # noqa: E402
from membrane.peers import PeerSubstrate  # noqa: E402


def _peers(field_path, port=11500):
    """A real PeerSubstrate (no couchbase: the probe degrades), then its
    registry collection swapped for a recorder."""
    p = PeerSubstrate(field_path=field_path, port=port, model="m")
    calls = {}

    def up(key, doc, *a, **k):
        calls["key"] = key
        calls["doc"] = doc
        return True

    p._reg = type("C", (), {"upsert": staticmethod(up)})()
    p._reg_bucket = type("B", (), {"name": "cortex-core"})()
    return p, calls


class TestRegistryKeyIsTheField(unittest.TestCase):
    def test_the_same_field_is_the_same_key_across_ports(self):
        with tempfile.TemporaryDirectory() as d:
            f = str(Path(d) / "field.json")
            p, _ = _peers(f, port=11500)
            p2, _ = _peers(f, port=11502)
            # a relaunch on a shifted port keeps the SAME registry identity
            # -- the new register() overwrites the old row instead of
            # leaving a stale proxy-11500 doc alive to be probed
            self.assertEqual(p.reg_key, p2.reg_key)

    def test_the_key_is_not_the_ephemeral_port(self):
        with tempfile.TemporaryDirectory() as d:
            f = str(Path(d) / "field.json")
            p, _ = _peers(f, port=11500)
            self.assertNotEqual(p.reg_key, "proxy-11500")
            self.assertEqual(p.reg_key, f"proxy-{field_id_for(f)}")

    def test_two_fields_are_two_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = _peers(str(Path(d) / "a" / "field.json"), port=11500)
            p2, _ = _peers(str(Path(d) / "b" / "field.json"), port=11500)
            self.assertNotEqual(p.reg_key, p2.reg_key)


class TestRegisterUpsertsUnderTheFieldKey(unittest.TestCase):
    def test_register_writes_the_stable_key_with_the_current_port(self):
        with tempfile.TemporaryDirectory() as d:
            p, calls = _peers(str(Path(d) / "field.json"), port=11502)
            self.assertTrue(p.register())
            # the doc key is the field, not proxy-11502 ...
            self.assertEqual(calls["key"], p.reg_key)
            self.assertNotEqual(calls["key"], "proxy-11502")
            # ... but the doc still carries the port the seat actually bound
            self.assertEqual(calls["doc"]["port"], 11502)


if __name__ == "__main__":
    unittest.main()
