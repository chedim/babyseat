"""PeerSubstrate.ask sends the seat credential only to a loopback host (#842)."""
import pytest
from membrane import peers

@pytest.mark.parametrize("host,expect", [("127.0.0.1", True), ("10.1.2.3", False)])
def test_ask_credential_only_to_loopback(monkeypatch, host, expect):
    from membrane import seatauth
    monkeypatch.setattr(seatauth, "loopback_headers", lambda h=None: {**(h or {}), "Authorization": "Bearer SEAT"})
    seen = {}
    def fake(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        raise OSError("stop")
    monkeypatch.setattr(peers, "_urlopen", fake)
    cls = [v for v in vars(peers).values() if isinstance(v, type) and hasattr(v, "ask") and "Substrate" in v.__name__]
    sub = cls[0].__new__(cls[0])
    sub.ask({"host": host, "port": 1}, "hi")
    assert (seen.get("auth") == "Bearer SEAT") is expect
