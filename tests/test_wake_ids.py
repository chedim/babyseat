"""THE WAKES KEEP THEIR OWN IDS (2026-10-14, caught while pinning PR
#18): two wakes planted back to back minted the SAME id -- 'w' + the
millisecond -- and unwaking one cancelled BOTH (unwake filters the
pending list by id equality, so every twin went with it). THE CONTRACT:
every pending wake keeps its own id, unwake cancels exactly the wake
named, and the set-return's text quotes the id the plant stored.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import llm as L  # noqa: E402
from membrane.entities import MEMBRANE_NS  # noqa: E402

from test_llm_wake import _Srv, BAT  # noqa: E402


def _world():
    srv = _Srv()
    L.bind(srv)
    return srv


def _wakes(srv):
    ent = srv.entity_store.docs.get(MEMBRANE_NS + "llm")
    if ent is None:
        return []
    return (ent.properties or {}).get("wakes") or []


class TestWakeIds(unittest.TestCase):
    def test_two_wakes_in_the_same_millisecond_stay_distinct(self):
        srv = _world()
        now = 1_800_000_000.0                       # the same millisecond, twice
        r1 = L.set_wake(srv, "Component:bat", "in 1h", "p1", now=now)
        r2 = L.set_wake(srv, "Component:bat", "in 1h", "p2", now=now)
        self.assertEqual((r1["rc"], r2["rc"]), (0, 0), (r1, r2))
        ws = _wakes(srv)
        self.assertEqual(len(ws), 2, ws)
        ids = [w["id"] for w in ws]
        self.assertNotEqual(ids[0], ids[1], ids)
        # cancel one: the OTHER keeps its wake
        self.assertEqual(L.unwake(srv, ids[0])["rc"], 0)
        ws = _wakes(srv)
        self.assertEqual([w["id"] for w in ws], [ids[1]], ws)

    def test_the_set_text_quotes_the_stored_id(self):
        # the id rides back in 'wake w123... set for ...' -- the model
        # quotes it to unwake; it must be the id the plant stored
        srv = _world()
        r = L.set_wake(srv, "Component:bat", "in 1h", "p")
        self.assertEqual(r["rc"], 0, r)
        wid = r["text"].split(" ")[1]
        self.assertEqual([w["id"] for w in _wakes(srv)], [wid])
        self.assertEqual(L.unwake(srv, wid)["rc"], 0)


if __name__ == "__main__":
    unittest.main()