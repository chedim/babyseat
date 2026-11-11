"""Suite-wide: the offline probe (membrane/offline.py) stays off -- a test seat's
backend is a fake, and a 'walk' declared mid-test would fail its embeds."""
import os

os.environ.setdefault("MEMBRANE_OFFLINE_PROBE", "0")
# JWT is on by default (#762); the suite runs with it off unless a test turns it on
os.environ.setdefault("MEMBRANE_JWT", "0")
# a test seat installs nothing at boot (the reranker would pull torch, GBs); CI installs spaCy itself
os.environ.setdefault("MEMBRANE_NO_AUTOINSTALL", "1")
# a test seat never loads the reranker (bge via hf_xet: a seat killed mid-download aborts at exit -> a macOS
# crash dialog, 2026-11-11); the reranker's own tests turn it on explicitly
os.environ.setdefault("MEMBRANE_RERANK", "0")
# the seat token (seatauth.py, #253): a test seat never writes the operator's
# ~/.membrane/seat-token -- one throwaway file for the whole run (and its subprocesses)
import tempfile as _tempfile
os.environ.setdefault("MEMBRANE_SEAT_TOKEN_FILE",
                      os.path.join(_tempfile.mkdtemp(prefix="membrane-seat-token-"), "seat-token"))

_PY = os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "python")


def spawn_python() -> str:
    """The interpreter the subprocess tests boot a real seat with: the dev
    venv when this checkout has one, else the running pytest interpreter
    (CI, which installs the package with `pip install -e .`)."""
    import sys
    return _PY if os.path.exists(_PY) else sys.executable


def ollama_alive() -> bool:
    """The live-lane tests boot real seats and run REAL model turns
    through the operator's local ollama; a checkout without it (CI) skips
    them instead of failing on the missing server."""
    import socket
    host, _, port = (os.environ.get("OLLAMA_HOST")
                     or "127.0.0.1:11434").strip().replace(
                         "http://", "").replace("https://", "").partition(":")
    try:
        socket.create_connection((host or "127.0.0.1",
                                  int(port) if port else 11434),
                                 timeout=1).close()
        return True
    except OSError:
        return False

# -- the seat credential for test clients (#842) ------------------------------------
# No address is a credential any more: a tokenless write to a test seat gets 401,
# loopback or not. The suite's HTTP clients (urllib, http.client) reach in-process
# seats on 127.0.0.1, so one shared hook presents the seat token for them -- the way a
# real caller does. It adds nothing when the request already carries its own
# Authorization/Cookie, when JWT mode is on (that has its own tokens), and for a test
# marked `tokenless` (the ones that PROVE the 401).
import pytest as _pytest

_REAL_REQUEST = None


_PKG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "membrane")
_PKG_DIR = os.path.normpath(_PKG_DIR) + os.sep


def _called_by_seat_code() -> bool:
    """True when the first frame above the stdlib http/urllib layers is seat code."""
    import sys
    f = sys._getframe(2)
    while f is not None:
        fn = os.path.normpath(f.f_code.co_filename)
        if fn.startswith(_PKG_DIR):
            return True
        if os.sep + "tests" + os.sep in fn or "pytest" in fn:
            return False
        f = f.f_back
    return False


@_pytest.fixture(autouse=True)
def _seat_credential(request, monkeypatch):
    import http.client as hc
    if request.node.get_closest_marker("tokenless"):
        yield
        return
    real = hc.HTTPConnection.request

    def request_(self, method, url, body=None, headers=None, *a, **kw):
        h = dict(headers or {})
        if _called_by_seat_code():
            # an internal caller (membrane/*) must carry its own credential
            # (seatauth.loopback_headers); the hook only stands in for TEST clients
            return real(self, method, url, body, h, *a, **kw)
        low = {k.lower() for k in h}
        if (os.environ.get("MEMBRANE_JWT", "0") in ("0", "", "false", "off")
                and not os.environ.get("MEMBRANE_JWT_SECRET") and not os.environ.get("MEMBRANE_JWT_SECRET_FILE")
                and "authorization" not in low and "cookie" not in low
                and "x-forwarded-for" not in low
                and str(getattr(self, "host", "")) in ("127.0.0.1", "localhost", "::1")):
            try:
                from membrane import seatauth
                tok = seatauth.ensure()
                if tok:
                    h["Authorization"] = f"Bearer {tok}"
            except Exception:
                pass
        return real(self, method, url, body, h, *a, **kw)

    monkeypatch.setattr(hc.HTTPConnection, "request", request_)
    yield


# #971: MEMBRANE_TEST_STORE=sqlite runs every Couchbase Lite store test against the SQLite store instead --
# the same suites are its contract.
if os.environ.get("MEMBRANE_TEST_STORE", "").strip() == "sqlite":
    import sys as _sys2
    _sys2.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from membrane import cblstore as _cbs
    from membrane import sqlstore as _sqs
    _cbs.CBLiteBackend = _sqs.SQLiteBackend

