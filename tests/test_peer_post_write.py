"""peer_post is a WRITE (the operator, 2026-11-12: everything moves to cliffhanger, no fallback): the entity
Peer:channel (made by packs/peer.c1f) has an adapter branch -- `Peer:channel.post = {text, reply_to}` posts to the
peer channel through the substrate driver and leaves its receipt in `post_result` / `post_rc`. The tool, its route and
its bridge are gone."""
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import adapters as AD  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

EID = "Peer:channel"


class _Peers:
    def __init__(self, ok=True):
        self.ok, self.posted = ok, []

    def post(self, text, reply_to="", mid=""):
        self.posted.append((text, reply_to))
        return self.ok


class _Srv:
    def __init__(self, peers):
        self.entity_store = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        self.peers = peers


def _until(fn, timeout=3.0):
    t = time.time() + timeout
    while time.time() < t:
        if fn():
            return True
        time.sleep(0.01)
    return fn()


def _write(srv, value):
    est = srv.entity_store
    if EID not in est.docs:
        est.upsert(QuantumEntity(eid=EID, cls="Peer", properties={"name": "the peer channel"}), embedder=None)
    d = est.docs[EID]
    d.properties = {**d.properties, "post": value}
    with W.source("person", "op"), W.as_writer("field"):
        est.upsert(d)


@pytest.fixture
def seat():
    peers = _Peers()
    srv = _Srv(peers)
    AD.start(srv)
    yield srv, peers
    AD.stop()


def test_a_write_posts_to_the_channel_and_leaves_a_receipt(seat):
    srv, peers = seat
    _write(srv, {"text": "handoff: the build is green", "reply_to": "seat-b"})
    assert _until(lambda: peers.posted)
    assert peers.posted == [("handoff: the build is green", "seat-b")]
    assert _until(lambda: srv.entity_store.docs[EID].properties.get("post_rc") == 0)
    assert srv.entity_store.docs[EID].properties["post"] == ""          # consumed


def test_a_failed_post_says_why_and_a_bare_string_is_the_text(seat, monkeypatch):
    srv, peers = seat
    peers.ok = False
    monkeypatch.setattr("membrane.peers.post_failure_reason", lambda p: "no peer substrate on this seat", raising=False)
    _write(srv, "hello")
    assert _until(lambda: srv.entity_store.docs[EID].properties.get("post_rc") not in (None, 0))
    assert peers.posted == [("hello", "")]
    assert "no peer substrate" in srv.entity_store.docs[EID].properties["post_result"]


def test_an_empty_text_posts_nothing(seat):
    srv, peers = seat
    _write(srv, {"text": "  "})
    assert _until(lambda: srv.entity_store.docs[EID].properties.get("post_rc") not in (None, 0))
    assert peers.posted == []


def test_the_tool_the_route_and_the_bridge_are_gone():
    assert "peer_post" not in M.METHOD_TOOLS and "peer_post" not in M.TOOL_IMPLS
    assert not [t for t in M.METHOD_TOOL_DEFS if t["name"] == "peer_post"]
    assert "def tool_peer_post" not in Path(M.__file__).read_text()
    lanes = (Path(M.__file__).parent / "lanes.py").read_text()
    assert '"/membrane/peer/post"' not in lanes and "def _peer_post" not in lanes


def test_the_pack_makes_the_channel_entity():
    pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")
    import types
    from membrane import fields_live as FL
    from membrane import watch_agents as WA
    os.environ["MEMBRANE_FIELDS"] = "1"
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    live = FL.start(srv, thread=False)
    try:
        FL.boot_started(est); live.settle()
        d = est.docs[EID]
        assert d.cls == "Peer" and "post" in d.properties["summary"]
    finally:
        FL.stop(srv)
