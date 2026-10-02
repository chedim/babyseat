"""STAGE 5 (the peers re-point): the ephemeral registry is no longer the
only who's-alive lane -- the PRESENCE roster (the seat-first tier's
published entities) answers when the registry is down or empty, with
staleness pruned (a dead seat's last presence cannot haunt the roster
forever)."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from membrane.peers import PeerSubstrate  # noqa: E402


class _Bucket:
    name = "cortex-core"


class _Q:
    """A fake cluster whose query answers canned registry rows."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, sql, opts=None):
        return iter([dict(r) for r in self.rows])


def _ps(registry_rows, roster_fn=None):
    """A PeerSubstrate built around the fakes (no couchbase connect)."""
    ps = PeerSubstrate.__new__(PeerSubstrate)
    ps.port = 18787
    ps.seat_id = "proxy-18787"
    ps.heartbeat_s = 30.0
    ps.cwd = "/x/proj"
    ps.model = "test"
    ps._cluster = _Q(registry_rows)
    ps._reg_bucket = _Bucket()
    ps._reg = _Bucket()            # truthy: the ensure path is skipped
    ps._roster_fn = roster_fn
    return ps


def _presence(origin, uptime=90, age_s=0):
    return {"origin": origin,
            "properties": {"seat": origin, "branch": "entity-field",
                           "uptime": uptime,
                           "presence_ts": time.time() - age_s}}


class TestPresenceFallback(unittest.TestCase):
    def test_registry_rows_win(self):
        """The registry is still lane one: when it answers, nothing
        changes (the roster is only a fallback)."""
        ps = _ps([{"cwd": "/x/other", "port": 2222}],
                 roster_fn=lambda: (_ for _ in ()).throw(
                     RuntimeError("never asked")))
        seats = ps.list_seats()
        self.assertEqual(seats, [{"cwd": "/x/other", "port": 2222}])

    def test_empty_registry_answers_from_the_roster(self):
        """THE RE-POINT: down or empty registry -> the presence roster
        IS the who's-alive list (the seat-first tier: the global field
        is the union of published entities)."""
        ps = _ps([], roster_fn=lambda: [_presence("laptop@mac", 900)])
        seats = ps.list_seats()
        self.assertEqual(len(seats), 1)
        s = seats[0]
        self.assertEqual(s["origin"], "laptop@mac")
        self.assertEqual(s["uptime_s"], 900)
        self.assertEqual(s["branch"], "entity-field")

    def test_stale_presence_is_pruned(self):
        """A dead seat's last presence (older than 10 minutes) is NOT an
        alive peer: the roster stays honest without TTL machinery."""
        ps = _ps([], roster_fn=lambda: [
            _presence("laptop@mac", age_s=3600),
            _presence("agdroid@phone", age_s=30)])
        seats = ps.list_seats()
        self.assertEqual([s["origin"] for s in seats], ["agdroid@phone"])

    def test_no_roster_fn_degrades_to_empty(self):
        """No provider wired (an old call site): the covenant holds --
        empty, never a raise."""
        ps = _ps([])
        self.assertEqual(ps.list_seats(), [])
        self.assertEqual(ps.livelist(), [])

    def test_a_raising_roster_degrades_to_empty(self):
        """The roster fn raising degrades to empty (never a wedge)."""
        ps = _ps([], roster_fn=lambda: (_ for _ in ()).throw(
            RuntimeError("boom")))
        self.assertEqual(ps.list_seats(), [])


if __name__ == "__main__":
    unittest.main()