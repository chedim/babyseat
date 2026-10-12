"""HA's words to the detached seat (restarts.py on_event): ha-start on the way
up, ha-stop on the way down -- deduped, and everything else refused. The seat
itself is a stub: nothing is posted anywhere."""
from membrane import restarts as RS


def _srv():
    return type("S", (), {"data_dir": "/tmp/nonexistent-membrane-test"})()


class _Send:
    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(RS, "_send", lambda srv, kind, text: self.calls.append((kind, text)))
        # the dedup clock is a module singleton: other tests (a boot's on_boot,
        # on_event from anywhere) may have told HA's restarts within the window
        monkeypatch.setattr(RS, "_last_ha", {"ha-start": 0.0, "ha-stop": 0.0})


def test_ha_start_and_stop_are_both_taken(monkeypatch):
    sent = _Send(monkeypatch)
    assert RS.on_event(_srv(), {"kind": "ha-start", "detail": "reboot"}) == {"ok": True}
    assert RS.on_event(_srv(), {"kind": "ha-stop", "detail": "maintenance"}) == {"ok": True}
    assert [k for k, _ in sent.calls] == ["ha-restart", "ha-stop"]
    assert RS.SIGN in sent.calls[0][1] and RS.SIGN in sent.calls[1][1]
    assert "reboot" in sent.calls[0][1] and "maintenance" in sent.calls[1][1]
    # the same word twice inside the dedup window is not a second notice
    assert RS.on_event(_srv(), {"kind": "ha-stop"}) == {"ok": True, "dedup": True}
    assert len(sent.calls) == 2


def test_unknown_kinds_are_refused(monkeypatch):
    sent = _Send(monkeypatch)
    assert "error" in RS.on_event(_srv(), {"kind": "bogus"})
    assert sent.calls == []