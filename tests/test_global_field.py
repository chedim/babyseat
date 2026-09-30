"""Tests for the shared-field glue that survives the entity-field rewrite
(docs/global-field.md): stable field ids + the peers heartbeat/registry
(the live-ask layer's discovery). The GlobalField commons and its Ledger
died with the exemplar store -- claims promotion is the field's claims
commons now (membrane/claims.py, tests/test_claims.py).

Plain-python runnable (no pytest dependency):
    .venv/bin/python -m tests.test_global_field
Each test function starting with "test_" runs in sequence; an uncaught
exception fails the run with the test name.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from membrane.global_field import Peers, field_id_for


def test_field_id_stable():
    # same path via different spellings -> same id; different paths differ
    f1 = tempfile.mkdtemp() + "/f.json"
    assert field_id_for(f1) == field_id_for(f1)
    assert field_id_for(f1) != field_id_for(f1 + "2")
    print("ok field_id_stable")


def test_peer_livelist_and_ask():
    d = Path(tempfile.mkdtemp(prefix="membrane-gf-peers-"))
    ppath = d / "peers.jsonl"
    pa = Peers("fidA")
    pb = Peers("fidB", ppath)
    pa.register(0, "f.json", peers_path=ppath)
    pb.register(0, "f.json", peers_path=ppath)
    live = pa.livelist(peers_path=ppath)
    assert {r["fid"] for r in live} == {"fidB"}           # self excluded
    assert pb.livelist(peers_path=ppath)[0]["fid"] == "fidA"
    # ghost expiry: a stale heartbeat is dropped
    stale = json.dumps({"fid": "fidGHOST", "port": 1, "field": "x",
                        "ts": time.time() - 10_000})
    with ppath.open("a") as f:
        f.write(stale + "\n")
    fids = {r["fid"] for r in pa.livelist(peers_path=ppath)}
    assert "fidGHOST" not in fids
    # live ask against a canned HTTP peer
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            out = json.dumps({"fid": "fidB", "hit": True, "sim": 0.91,
                              "situation": "how do I deploy",
                              "action": "run deploy.sh"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        peer = {"fid": "fidB", "port": srv.server_address[1]}
        hit = Peers.ask(peer, "how do I deploy", timeout=5)
        assert hit and hit["action"] == "run deploy.sh"
        # a peer below the sim bar answers None
        assert Peers.ask(peer, "how do I deploy", sim_min=0.99,
                         timeout=5) is None
        # a dead peer degrades to None (no raise)
        assert Peers.ask({"fid": "x", "port": 59999}, "q",
                         timeout=2) is None
    finally:
        srv.shutdown()
    print("ok_peer_livelist_and_ask")


if __name__ == "__main__":
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")