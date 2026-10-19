"""#243: standalone opens ONE browser tab -- never on a re-exec restart, and on
a cold boot only when no membrane page reconnects within a short window."""
from __future__ import annotations

import socket
import sys
import threading

import pytest

from membrane import restarts as RS
from membrane import ui_serve as UIS


@pytest.fixture()
def opened(monkeypatch):
    calls: list[str] = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: calls.append(url) or True)
    monkeypatch.delenv("MEMBRANE_NO_BROWSER", raising=False)
    monkeypatch.delenv(RS.REEXEC_ENV, raising=False)
    UIS._PAGE_SEEN.clear()
    yield calls
    UIS._PAGE_SEEN.clear()


@pytest.fixture()
def port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    yield s.getsockname()[1]
    s.close()


def _run(t):
    if t is not None:
        t.join(timeout=5)
        assert not t.is_alive()


def test_reexec_never_opens(opened, port):
    t = UIS.open_when_up(port, "http://localhost/", grace_s=0.01, reexec=True)
    _run(t)
    assert opened == []


def test_reconnecting_page_within_window_prevents_open(opened, port):
    t = UIS.open_when_up(port, "http://localhost/", grace_s=2.0)
    threading.Timer(0.05, UIS.note_page).start()     # the old tab's stream comes back
    _run(t)
    assert opened == []


def test_no_page_opens_exactly_once(opened, port):
    t = UIS.open_when_up(port, "http://localhost/", grace_s=0.05)
    _run(t)
    assert opened == ["http://localhost/"]


def test_no_browser_env_never_opens(opened, port, monkeypatch):
    monkeypatch.setenv("MEMBRANE_NO_BROWSER", "1")
    t = UIS.open_when_up(port, "http://localhost/", grace_s=0.01)
    _run(t)
    assert opened == []


def test_reexec_sets_marker_and_boot_consumes_it(monkeypatch):
    monkeypatch.delenv(RS.REEXEC_ENV, raising=False)
    seen = {}
    monkeypatch.setattr(RS.os, "execv", lambda exe, argv: seen.update(
        exe=exe, env=RS.os.environ.get(RS.REEXEC_ENV)))
    monkeypatch.setitem(RS.RESTART, "requested", True)
    RS.reexec_if_requested()
    assert seen["exe"] == sys.executable and seen["env"] == "1"
    assert RS.was_reexec() is True          # the new boot reads it ...
    assert RS.was_reexec() is False         # ... and clears it: a later cold boot opens


def test_cold_boot_without_marker_is_not_reexec(monkeypatch):
    monkeypatch.delenv(RS.REEXEC_ENV, raising=False)
    assert RS.was_reexec() is False


def test_sse_client_counts_as_a_page(opened):
    """A client on /event or /global/event (the page's live stream, which
    reconnects on its own after a restart) marks a page as seen."""
    import urllib.request
    from http.server import ThreadingHTTPServer
    from membrane import server as NS
    for route in ("/event", "/global/event"):
        UIS._PAGE_SEEN.clear()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), NS.make_handler(hub=NS.SSEHub()))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_address[1]}{route}", timeout=5)
            try:
                resp.readline()
            finally:
                resp.close()
        finally:
            httpd.shutdown()
        assert UIS._PAGE_SEEN.is_set(), route


def test_internal_hub_subscriber_is_not_a_page(opened):
    """The seat's own listeners (speaker.py) subscribe to the hub directly;
    only an HTTP stream client counts."""
    from membrane.server import SSEHub
    SSEHub().subscribe()
    assert not UIS._PAGE_SEEN.is_set()
