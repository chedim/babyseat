"""The big voice files, fetched on first use (assets.py): verified or refused."""
import hashlib
import http.server
import threading
from functools import partial

from membrane import assets as A


def _serve(dirpath):
    h = partial(http.server.SimpleHTTPRequestHandler, directory=str(dirpath))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_fetch_verify_keep(tmp_path, monkeypatch):
    rel = tmp_path / "release"; rel.mkdir()
    good = b"x" * 1000
    (rel / "voice-ml.mjs").write_bytes(good)
    monkeypatch.setitem(A.MANIFEST, "voice-ml.mjs", (hashlib.sha256(good).hexdigest(), len(good)))
    srv = _serve(rel)
    monkeypatch.setenv("MEMBRANE_ASSETS_URL", f"http://127.0.0.1:{srv.server_address[1]}/")
    p = A.ensure("voice-ml.mjs", tmp_path / "assets")
    assert p is not None and p.read_bytes() == good
    (rel / "voice-ml.mjs").write_bytes(b"changed")           # kept: not fetched again
    assert A.ensure("voice-ml.mjs", tmp_path / "assets").read_bytes() == good
    srv.shutdown()


def test_a_tampered_file_is_refused(tmp_path, monkeypatch):
    rel = tmp_path / "release"; rel.mkdir()
    (rel / "voice-ml.mjs").write_bytes(b"y" * 1000)
    monkeypatch.setitem(A.MANIFEST, "voice-ml.mjs", (hashlib.sha256(b"x" * 1000).hexdigest(), 1000))
    srv = _serve(rel)
    monkeypatch.setenv("MEMBRANE_ASSETS_URL", f"http://127.0.0.1:{srv.server_address[1]}/")
    assert A.ensure("voice-ml.mjs", tmp_path / "assets") is None
    assert not (tmp_path / "assets" / "voice-ml.mjs").exists()
    assert A.ensure("not-an-asset.js", tmp_path / "assets") is None
    srv.shutdown()
