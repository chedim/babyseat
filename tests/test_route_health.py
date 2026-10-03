"""P1 THE STALE-ROUTE COOLDOWN (the seat's defect 2): a peer whose ask
failed (the seat moved ports -- ':11502 unreachable') stops being
retried for the cooldown window; a successful ask clears the record;
the cooldown expiry re-admits."""
from membrane.peers import PeerSubstrate


class _FakeInfo:
    def __init__(self, props, seen_ts):
        self.properties = props
        self.seen_ts = seen_ts


class _FakeZc:
    """The mdns fake: two seats, one on a stale port."""
    def __init__(self):
        self._t0 = 0.0

    def browse(self):
        import time as _t
        now = _t.time()
        return {
            "m1._membrane-proxy._tcp.local.": _FakeInfo(
                {"seat": "s1", "port": "11502", "docs": "0",
                 "model": "m"}, now),
            "m2._membrane-proxy._tcp.local.": _FakeInfo(
                {"seat": "s2", "port": "11503", "docs": "0",
                 "model": "m"}, now),
        }


def _peers():
    p = PeerSubstrate(field_path="/tmp/x", port=11500, model="m")
    p._route_bad_s = 300.0
    # THE DEAD REGISTRY: the real couch (+ any running seat) must not
    # leak in -- every test here is env-independent (union-driven).
    p._reg = object()
    p._reg_bucket = None
    p._cluster = None
    return p


def test_failed_ask_cooldowns_the_route():
    p = _peers()
    peer = {"seat": "s1", "host": "127.0.0.1", "port": 11502}
    assert p.livelist() == []
    p.note_route_bad(peer)
    peer2 = {"seat": "s2", "host": "127.0.0.1", "port": 11503}
    p._zc_impl = _FakeZc()
    p._roster_fn = None
    seats = p.livelist()
    assert [s["seat"] for s in seats] == ["s2"], seats


def test_good_ask_clears_and_expiry_readmits():
    p = _peers()
    peer = {"seat": "s1", "host": "127.0.0.1", "port": 11502}
    p.note_route_bad(peer)
    p.note_route_good(peer)
    assert p._route_alive(peer)
    # expiry: backdate the failure past the window
    p.note_route_bad(peer)
    p._route_bad[p._route_key(peer)] -= p._route_bad_s + 1.0
    assert p._route_alive(peer)


def test_ask_peer_records_outcome():
    p = _peers()
    calls = {}

    def fake_ask(peer, situation, sim_min=0.60, timeout=20.0):
        calls["peer"] = peer
        return None
    p.ask = fake_ask
    peer = {"seat": "s1", "port": 11502}
    out = p.ask_peer(peer, "what?")
    assert out is None and p._route_alive(peer) is False
    assert calls["peer"] == peer