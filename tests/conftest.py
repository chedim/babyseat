"""Suite-wide: the offline probe (membrane/offline.py) stays off -- a test seat's
backend is a fake, and a 'walk' declared mid-test would fail its embeds."""
import os

os.environ.setdefault("MEMBRANE_OFFLINE_PROBE", "0")
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