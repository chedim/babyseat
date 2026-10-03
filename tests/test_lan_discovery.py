"""LAN DISCOVERY (the operator's 2026-10-02 design, red first): the
peers substrate grows a TRANSPORT-LOCAL lane -- zeroconf/mDNS announce
+ browse -- so seats find each other on the LAN without couchbase:

1. THE ANNOUNCE: each proxy advertises '_membrane-proxy._tcp' with the
   PRESENCE ENTITY'S public metadata in the TXT records: seat (the
   origin tag), model, port, field size, branch+head (the git
   identity the presence already carries). THE PRIVATE-INFORMATION
   AUDIT: no absolute paths, no usernames, no message text -- only
   what the presence entity already publishes to the GLOBAL tier
   (the same audience; a LAN is the wider room).
2. THE ROSTER UNION: list_seats() = registry docs + mDNS peers --
   provenance-stamped 'via: mdns'; mDNS's own TTL is the GC.
3. THE REMOTE ASK: ask() gains host (the '127.0.0.1' assumption was
   the wall; mDNS peers carry their address).
4. THE DEGRADE: zeroconf missing/disabled -> the lane is off; the
   seat stays registry+presence-only. MEMBRANE_LAN=0 kills it.
5. THE ESCALATIONS-AS-EVENTS: a peer ask that MISSES (no sibling help)
   lands in the watch-events queue (kind 'peer-miss') -- the loops'
   pool sees failed escalations as events to react to."""

import os
import socket
import json
import threading
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERVICE_TYPE = "_membrane-proxy._tcp.local."
MDNS_PEER_TTL_S = 60.0     # a browse entry's staleness cap

try:
    import zeroconf as _zc_mod
except Exception:
    _zc_mod = None          # import fails -> the lane is off (degrade)

from membrane import peers as P  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402  (the pool user)


def lan_enabled() -> bool:
    return os.environ.get("MEMBRANE_LAN", "1") not in ("0", "false", "off")


def presence_txt(model: str, seat: str, port: int, docs: int = 0,
                 branch: str = "", head: str = "",
                 extra: dict | None = None) -> dict:
    """THE TXT RECORD: the presence entity's PUBLIC fields only. THE
    AUDIT: no paths, no usernames, no text -- the same fields the
    presence entity publishes to the global tier (a LAN seat could
    have read them there anyway)."""
    txt = {"model": (model or "")[:40],
           "seat": (seat or "")[:64],
           "port": str(int(port or 0)),
           "docs": str(int(docs or 0)),
           "branch": (branch or "")[:40],
           "head": (head or "")[:12],
           "proto": "membrane/1"}
    if extra:
        for k in ("ephemeral", "free_vram_gib"):
            if k in extra:
                txt[k] = str(extra[k])
    return txt


class _FakeZC:
    """The zeroconf seam (the tests' handle): register/unregister +
    a browsable peers registry."""

    def __init__(self):
        self.registered = {}
        self.lock = threading.Lock()

    def register_service(self, info):
        with self.lock:
            self.registered[info["name"]] = info

    def unregister_service(self, info):
        with self.lock:
            self.registered.pop(info["name"], None)

    def browse(self) -> dict:
        with self.lock:
            return dict(self.registered)


class TestPresenceTxt(unittest.TestCase):
    def test_txt_is_public_only(self):
        """THE AUDIT PIN: no absolute paths, no user names, no message
        text in the TXT record."""
        t = presence_txt("glm-5.3-flash:cloud", "membrane@G0XW2RC6K3",
                         11500, docs=7016, branch="membrain/main",
                         head="472d8c9",
                         extra={"free_vram_gib": 6.4})
        vals = [v for v in t.values() if not v.startswith("membrane/")]
        self.assertFalse(any(v.startswith("/") or "/Users/" in v
                             for v in vals), t)
        self.assertNotIn("Users", json.dumps(t))
        self.assertEqual(t["model"], "glm-5.3-flash:cloud")
        self.assertEqual(t["docs"], "7016")

    def test_seat_and_ports_ride(self):
        t = presence_txt("m", "seat@h", 11501)
        self.assertEqual(t["seat"], "seat@h")
        self.assertEqual(t["port"], "11501")
        self.assertEqual(t["proto"], "membrane/1")


import json  # noqa: E402  (the audit test reads it)


class TestMdnsAnnounce(unittest.TestCase):
    def test_announce_and_browse_roundtrip(self):
        """THE ROUNDTRIP: a seat announces; a BROWSE finds it with the
        presence metadata (the roster's new lane)."""
        zc = _FakeZC()
        ann = P.MdnsAnnouncer(zc_impl=zc, model="glm", seat="membrane@h",
                              port=11500, docs=7)
        ann.announce()
        rows = P.mdns_peers(zc)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seat"], "membrane@h")
        self.assertEqual(rows[0]["port"], 11500)
        self.assertEqual(rows[0]["via"], "mdns")
        ann.close()

    def test_stale_peer_pruned(self):
        """A peer whose browse entry aged past the mDNS TTL is not in
        the roster (the GC is the clock's)."""
        zc = _FakeZC()
        P.MdnsAnnouncer(zc_impl=zc, model="m", seat="dead@h",
                        port=1159).announce()
        rows = P.mdns_peers(zc, now=time.time() + P.MDNS_PEER_TTL_S + 1)
        self.assertEqual(rows, [])


class TestRosterUnion(unittest.TestCase):
    def test_list_seats_unions_mdns(self):
        """THE ROSTER UNION: registry + presence + mDNS, provenance
        stamped; a dedupe by seat id (a seat in TWO lanes appears
        once, registry's entry wins -- the authoritative home)."""
        zc = _FakeZC()
        P.MdnsAnnouncer(zc_impl=zc, model="remote-model",
                        seat="boat@agdroid", port=11502).announce()
        r = P.PeerSubstrate(field_path="/tmp/t-mdns-peer.json",
                            port=11500, model="local")
        # force the mdns lane into the substrate (the test's seam)
        r._zc = zc
        r._reg = object()                 # the registry is DOWN (a stub
        r._reg_bucket = None              # whose query raises: never the
        r._cluster = None                 # live couch, env-independent)
        r._presence_seats = lambda: [{"seat": "boat@agdroid",
                                      "port": 11503, "model": "old",
                                      "via": "presence"}]
        seats = r.list_seats()
        boats = [s for s in seats if s.get("seat") == "boat@agdroid"]
        self.assertEqual(len(boats), 1, seats)    # deduped
        self.assertEqual(boats[0].get("via"), "mdns")


class TestRemoteAsk(unittest.TestCase):
    def test_ask_remote_host(self):
        """THE WALL REMOVED: ask() takes host (defaults 127.0.0.1 --
        byte-identical for the legacy calls); a LAN peer's ask goes to
        THEIR host:port."""
        from unittest import mock

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"situation": "s", "action": "a",
                                   "sim": 0.9}).encode()

        opened = {}

        def fake_urlopen(req, timeout):
            opened["url"] = getattr(req, "full_url", None) or str(req)
            return _Resp()
        with mock.patch.object(P, "_urlopen", fake_urlopen):
            got = P.PeerSubstrate.ask({"port": 11507,
                                       "host": "192.168.1.159"},
                                      "what moved?")
        self.assertIsNotNone(got)
        self.assertTrue(opened["url"].startswith("http://192.168.1.159:"))
        self.assertIn("/membrane/ask", opened["url"])


class TestEscalationsAsEvents(unittest.TestCase):
    def test_peer_miss_lands_in_the_pool(self):
        """THE ESCALATION EVENT: a peer ask that finds NOTHING lands in
        the watch-events queue (kind 'peer-miss') -- the loops' pool
        sees failed escalations."""
        pool = WA.WatchEvents()
        P.note_peer_miss(pool, {"seat": "boat@agdroid", "port": 11502},
                         "how does the heading pipeline publish?",
                         why="no field hit")
        evs = pool.take()
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["kind"], "peer-miss")
        self.assertEqual(evs[0]["peer"], "boat@agdroid")

    def test_miss_no_events_keeps_quiet(self):
        """A peer ask that ANSWERED does not event (the answer rode as
        context; a miss is the event)."""
        pool = WA.WatchEvents()
        P.note_peer_miss(pool, {"seat": "boat@agdroid"}, "q?", why=None)
        self.assertEqual(pool.take(), [])


from membrane import watch_agents as WA  # noqa: E402  (the pool user)

if __name__ == "__main__":
    unittest.main()