"""The global-field lane's route health (the ':11502 unreachable'
exhibit -- the seat MOVED ports, the heartbeat file kept the old): a
failed ask cooldowns the port off livelist; a hit clears; the newest
heartbeat per fid keeps winning (a moved port self-heals on the next
heartbeat)."""
from membrane.global_field import Peers
import time


def _tmp_peers(tmp_path, rows):
    p = tmp_path / "peers.jsonl"
    p.write_text("".join(__import__("json").dumps(r) + "\n" for r in rows))
    return Peers(fid="me", path=p)


def test_failed_ask_cooldowns_the_port(tmp_path):
    g = _tmp_peers(tmp_path, [
        {"fid": "me", "port": 11500, "ts": time.time()},
        {"fid": "gone", "port": 11502, "ts": time.time()},
        {"fid": "live", "port": 11503, "ts": time.time()}])
    g.ask = lambda peer, situation, sim_min=0.6, timeout=20.0: None
    assert {r["fid"] for r in g.livelist()} == {"gone", "live"}
    g.ask_peer({"port": 11502}, "how?")
    assert {r["fid"] for r in g.livelist()} == {"live"}, g.livelist()


def test_success_clears_the_route(tmp_path):
    g = _tmp_peers(tmp_path, [
        {"fid": "me", "port": 11500, "ts": time.time()},
        {"fid": "p1", "port": 11502, "ts": time.time()}])
    g._route_bad["11502"] = time.time()
    g.ask = lambda peer, situation, sim_min=0.6, timeout=20.0: {"fid": "p1"}
    g.ask_peer({"port": 11502}, "how?")
    assert {r["fid"] for r in g.livelist()} == {"p1"}


def test_expiry_readmits(tmp_path):
    g = _tmp_peers(tmp_path, [
        {"fid": "me", "port": 11500, "ts": time.time()},
        {"fid": "p1", "port": 11502, "ts": time.time()}])
    g._route_bad["11502"] = time.time() - (g._route_bad_s + 1.0)
    assert {r["fid"] for r in g.livelist()} == {"p1"}


def test_moved_port_selfheals_by_recency(tmp_path):
    g = _tmp_peers(tmp_path, [
        {"fid": "me", "port": 11500, "ts": time.time()},
        {"fid": "p1", "port": 11502, "ts": time.time() - 500},   # dead port
        {"fid": "p1", "port": 11504, "ts": time.time()}])        # the new one
    seats = g.livelist()
    p1 = [r for r in seats if r["fid"] == "p1"]
    assert len(p1) == 1 and p1[0]["port"] == 11504