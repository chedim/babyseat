"""Tests for the global-field layer (docs/global-field.md, slices 1-4).

Plain-python runnable (no pytest dependency):
    .venv/bin/python -m tests.test_global_field
Each test function starting with "test_" runs in sequence; an uncaught
exception fails the run with the test name.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from membrane.field import Exemplar, ExemplarStore
from membrane.global_field import (
    GlobalField, Ledger, Peers, field_id_for, field_id_for as _fid,
    LEDGER_NOTE_SIM, LEDGER_VOTES_MIN, _cos,
)


def _vec(seed: float, dim: int = 16) -> list[float]:
    v = np.zeros(dim)
    v[int(seed) % dim] = 1.0
    v[(int(seed) * 7 + 3) % dim] = 0.5
    return list(v / np.linalg.norm(v))


class _T:
    """One test's sandbox: a temp dir + a commons at <dir>/global-field.json."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="membrane-gf-test-"))
        self.gpath = self.dir / "global-field.json"
        self.a = GlobalField(self.gpath, "aaaa11112222")
        self.b = GlobalField(self.gpath, "bbbb33334444")


def test_field_id_stable():
    # same path via different spellings -> same id; different paths differ
    f1 = tempfile.mkdtemp() + "/f.json"
    assert field_id_for(f1) == field_id_for(f1)
    assert field_id_for(f1) != field_id_for(f1 + "2")
    print("ok field_id_stable")


def test_promote_and_dedup():
    t = _T()
    v = _vec(1)
    ex = t.a.promote("user prefers zsh", "shell is zsh", v)
    assert len(t.a.store) == 1
    assert ex.scope == "global" and ex.owner == "aaaa11112222"
    # idempotent: same text -> no grow
    ex2 = t.a.promote("user prefers zsh", "shell is zsh", v)
    assert len(t.a.store) == 1 and ex2 is ex
    # a fresh instance loads from disk
    c = GlobalField(t.gpath, "cccc55556666")
    assert len(c.store) == 1
    print("ok promote_and_dedup")


def test_promote_existing_votes():
    t = _T()
    v = _vec(2)
    t.a.promote("deploy on tuesdays", "tue", v)
    src = Exemplar(situation="deploy on tuesdays", action="tue",
                   outcome="pending", vec=v, safe=True)
    ex = t.b.promote_existing(src)
    fids = [f for f, _ in ex.votes]
    assert fids == ["aaaa11112222", "bbbb33334444"], fids
    print("ok promote_existing_votes")


def test_ledger_append_dedup_trim_forget():
    d = Path(tempfile.mkdtemp(prefix="membrane-gf-led-"))
    led = Ledger("fidX", d / "fidX.jsonl")
    v = _vec(3)
    assert led.append("sit A", "act A", v) is True
    assert led.append("sit A", "act A", v) is False       # text dedup
    assert len(led.read()) == 1
    assert led.append("reasoning: noise", "junk", v) is True
    assert len(led.read()) == 2                           # junk IS stored...
    assert len(led.entries()) == 1                        # ...but filtered
    assert led.forget_texts({"sit A"}) == 1               # removes only A
    rest = led.read()
    assert len(rest) == 1 and rest[0]["situation"] == "reasoning: noise"
    print("ok ledger_append_dedup_trim_forget")


def test_merge_consensus_promotes():
    t = _T()
    v = _vec(4)
    # only field A vouches -> no consensus promotion
    t.a.promote("retry budget is 3", "3 attempts", v)
    st = t.b.merge_ledgers()
    assert st["promoted"] == 0 and len(t.b.store) == 1
    # field B adds the same note (independent text produce) -> consensus grows
    # the existing note's votes instead of duplicating
    t.b.promote("retry budget is 3", "3 attempts", v)
    st = t.b.merge_ledgers()
    ex = t.b.store.exemplars[0]
    fids = [f for f, _ in ex.votes]
    assert set(fids) == {"aaaa11112222", "bbbb33334444"}, fids
    assert len(t.b.store) == 1                            # merged, not dup'd
    # a DIFFERENT note from two fields auto-promotes on merge
    la = Ledger("aaaa11112222", t.a.ledger.path)
    lb = Ledger("bbbb33334444", t.b.ledger.path)
    v5 = _vec(9)
    la.append("cron moved to 3am", "3am", v5)
    lb.append("cron moved to 3am", "3am", v5)
    st = t.b.merge_ledgers()
    assert st["promoted"] == 1, st
    assert len(t.b.store) == 2
    new = t.b.store.exemplars[-1]
    assert set(f for f, _ in new.votes) == {"aaaa11112222", "bbbb33334444"}
    assert new.scope == "global" and new.owner == "aaaa11112222"
    print("ok merge_consensus_promotes")


def test_forget_retracts_ledger():
    t = _T()
    v = _vec(5)
    t.a.promote("secret note", "s", v)
    assert len(t.a.ledger.entries()) == 1
    t.a.forget([0])
    assert len(t.a.store) == 0
    assert t.a.ledger.entries() == []                     # proposal retracted
    # a sibling merge does not resurrect it from a's (retracted) shard
    st = t.b.merge_ledgers()
    assert st["promoted"] == 0 and len(t.b.store) == 0
    print("ok forget_retracts_ledger")


def test_top_up_and_note_use():
    t = _T()
    v = _vec(6)
    t.a.promote("uses pnpm not npm", "pnpm", v)
    q = np.asarray(v, dtype=float)
    top = t.b.top_up(q, k=2, sim_min=0.9)
    assert len(top) == 1 and top[0][1] >= 0.9
    t.b.note_use(top)
    # flush happens in rebalance; uses land on the note
    t.b.rebalance()
    uses = [f for f, _ in t.b.store.exemplars[0].uses]
    assert "bbbb33334444" in uses
    print("ok top_up_and_note_use")


def test_home_drifts_to_heaviest_user():
    t = _T()
    ex = t.a.promote("the shared fact", "fact", _vec(7))
    assert ex.home == "aaaa11112222"
    # B uses it 3x (>= HOME_DRIFT_MIN_USES), A used it 0x -> home -> B
    for _ in range(3):
        top = t.b.top_up(np.asarray(ex.vec), sim_min=0.9)
        t.b.note_use(top)
        t.b.rebalance()
    ex2 = t.b.store.exemplars[0]
    assert ex2.home == "bbbb33334444", ex2.home
    print("ok_home_drifts_to_heaviest_user")


def test_scope_suggestions():
    t = _T()
    # commons = one note
    t.a.promote("user runs macos + zsh", "darwin zsh", _vec(11))
    # local field: junk + locals + one commons-flavored note
    locs = []
    for i in range(8):
        locs.append(Exemplar(situation=f"local project thing {i}",
                             action=f"do local {i}", outcome="pending",
                             vec=_vec(20 + i), safe=True))
    star = Exemplar(situation="user prefers dark mode everywhere",
                    action="dark mode", outcome="pending",
                    vec=_vec(11), safe=True)          # same direction as commons
    junk = Exemplar(situation="reasoning: planning the refactor",
                    action="thoughts", outcome="pending",
                    vec=_vec(40), safe=True)
    sug_path = t.dir / "scope-suggestions.jsonl"
    recs = t.a.suggest_scope(locs + [star, junk], sug_path)
    assert recs, "expected at least one suggestion (the star)"
    texts = [r["text"] for r in recs]
    assert any("dark mode" in x for x in texts), texts
    # junk never suggested
    assert not any("reasoning:" in x for x in texts), texts
    # resolve the star; a second run must not re-suggest it
    with sug_path.open() as f:
        rows = [json.loads(l) for l in f]
    for r in rows:
        if "dark mode" in r["text"]:
            r["status"] = "promoted"
    with sug_path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    recs2 = t.a.suggest_scope(locs + [star, junk], sug_path)
    assert not any("dark mode" in r["text"] for r in recs2), recs2
    print("ok_scope_suggestions")


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


def test_uses_and_votes_survive_json_roundtrip():
    t = _T()
    ex = t.a.promote("roundtrip fact", "rt", _vec(12))
    top = t.b.top_up(np.asarray(ex.vec), sim_min=0.9)
    t.b.note_use(top)
    t.b.rebalance()
    # reload from disk in a third instance: votes + uses are lists now
    c = GlobalField(t.gpath, "cccc77778888")
    ex2 = c.store.exemplars[0]
    assert isinstance(ex2.votes[0], list) and isinstance(ex2.uses[0], list)
    assert len(_votes := [f for f, _ in ex2.votes]) >= 1
    assert "cccc77778888" not in _votes                    # c never used it
    # merge_ledgers tolerates legacy tuple/list mix without raising
    stats = c.merge_ledgers()
    assert "promoted" in stats
    print("ok_uses_and_votes_survive_json_roundtrip")


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
    sys.exit(1 if failed else 0)