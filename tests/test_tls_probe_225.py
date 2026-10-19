"""THE TLS PROBE (#225): the readiness probe that gates publish, presence,
imports and the peer roster always asked http://<host>:8091/pools, so a
TLS-only cluster (Capella, any couchbases:// cluster) never passed it and
publishing silently did nothing while the merged field (cb_open) worked.

The probe now follows the connection string's scheme:
  couchbase://h   -> http://h:8091/pools   (as before)
  couchbases://h  -> https://h:18091/pools, certificate VERIFIED before the
                     credentials go out; a certificate the probe cannot
                     verify (self-signed, a private CA) proves only that a
                     cluster answers -- it is re-asked WITHOUT credentials,
                     and the SDK (with its own trust) decides the rest;
                     a closed 18091 falls back to a bare TCP connect on the
                     KV TLS port 11207 (no bytes, no credentials).
A down or unreachable cluster still says no, quietly, within the 2s cap.
No real cluster, no network: urlopen and the socket are faked."""
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import efstore  # noqa: E402


class _Resp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(monkeypatch, answer):
    """answer(url, req, kw) -> status int, or raises. Returns the call log."""
    calls = []

    def _urlopen(req, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(dict(url=url, auth=req.get_header("Authorization"),
                          timeout=kw.get("timeout"),
                          context=kw.get("context")))
        return _Resp(answer(url, req, kw))
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return calls


def _no_socket(monkeypatch):
    rec = []

    def _cc(addr, timeout=None, *a, **k):
        rec.append((addr, timeout))
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(socket, "create_connection", _cc)
    return rec


def _env(monkeypatch, conn):
    monkeypatch.setenv("COUCHBASE_CONN_STRING", conn)
    monkeypatch.setenv("COUCHBASE_USER", "u")
    monkeypatch.setenv("COUCHBASE_PASSWORD", "p")
    monkeypatch.delenv("COUCHBASE_CERT_PATH", raising=False)


# ---- the scheme decides the endpoint --------------------------------------

def test_plain_scheme_probes_http_8091(monkeypatch):
    _env(monkeypatch, "couchbase://db.example")
    calls = _fake_urlopen(monkeypatch, lambda u, r, k: 200)
    assert efstore.probe_local_couchbase() is True
    assert calls[0]["url"] == "http://db.example:8091/pools"
    assert calls[0]["auth"].startswith("Basic ")
    assert calls[0]["timeout"] <= 2.0


def test_tls_scheme_probes_https_18091(monkeypatch):
    _env(monkeypatch, "couchbases://cb.abc.cloud.couchbase.com")
    calls = _fake_urlopen(monkeypatch, lambda u, r, k: 200)
    assert efstore.probe_local_couchbase() is True
    assert calls[0]["url"] == "https://cb.abc.cloud.couchbase.com:18091/pools"
    ctx = calls[0]["context"]
    assert isinstance(ctx, ssl.SSLContext)
    # credentials only ever go over a VERIFIED channel
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert calls[0]["timeout"] <= 2.0


def test_tls_scheme_ignores_kv_port_and_params(monkeypatch):
    _env(monkeypatch, "couchbases://h1.example:11207/bucket?network=external")
    calls = _fake_urlopen(monkeypatch, lambda u, r, k: 200)
    assert efstore.probe_local_couchbase() is True
    assert calls[0]["url"] == "https://h1.example:18091/pools"


# ---- the certificate ------------------------------------------------------

def test_unverifiable_cert_reasks_without_credentials(monkeypatch):
    """A self-signed / private-CA cluster: the verified ask fails on the
    certificate; the re-ask is unverified and carries NO credentials, and a
    cluster that answers at all (401 here) is ready -- the SDK, with its
    own trust (COUCHBASE_CERT_PATH, Capella's bundled CA), decides next."""
    _env(monkeypatch, "couchbases://self-signed.lan")

    def answer(url, req, kw):
        ctx = kw.get("context")
        if ctx is not None and ctx.verify_mode == ssl.CERT_REQUIRED:
            raise urllib.error.URLError(ssl.SSLCertVerificationError(
                1, "certificate verify failed: self-signed certificate"))
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
    calls = _fake_urlopen(monkeypatch, answer)
    assert efstore.probe_local_couchbase() is True
    assert len(calls) == 2
    assert calls[0]["auth"] is not None          # verified ask: creds
    assert calls[1]["auth"] is None              # unverified ask: NONE
    assert calls[1]["context"].verify_mode == ssl.CERT_NONE


def test_cert_path_is_trusted(monkeypatch, tmp_path):
    """COUCHBASE_CERT_PATH (the SDK's own trust knob) is the probe's too."""
    _env(monkeypatch, "couchbases://self-signed.lan")
    pem = tmp_path / "ca.pem"
    pem.write_text("x")
    monkeypatch.setenv("COUCHBASE_CERT_PATH", str(pem))
    seen = []
    real = ssl.create_default_context

    def _cdc(*a, **k):
        seen.append(k.get("cafile"))
        return real()
    monkeypatch.setattr(ssl, "create_default_context", _cdc)
    _fake_urlopen(monkeypatch, lambda u, r, k: 200)
    assert efstore.probe_local_couchbase() is True
    assert str(pem) in seen


def test_plain_scheme_never_skips_credentials_check(monkeypatch):
    """couchbase:// is unchanged: a 401 is 'no' (bad creds), as before."""
    _env(monkeypatch, "couchbase://127.0.0.1")

    def answer(url, req, kw):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
    _fake_urlopen(monkeypatch, answer)
    assert efstore.probe_local_couchbase() is False


def test_tls_verified_401_is_no(monkeypatch):
    """Over a verified channel a 401 means the credentials are wrong."""
    _env(monkeypatch, "couchbases://good-cert.example")

    def answer(url, req, kw):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
    calls = _fake_urlopen(monkeypatch, answer)
    assert efstore.probe_local_couchbase() is False
    assert len(calls) == 1


# ---- closed 18091 (Capella) -> the KV TLS port ------------------------------

def test_tls_closed_mgmt_port_falls_back_to_kv_tls_port(monkeypatch):
    _env(monkeypatch, "couchbases://cb.abc.cloud.couchbase.com")

    def answer(url, req, kw):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))
    _fake_urlopen(monkeypatch, answer)
    rec = []

    class _Sock:
        def close(self):
            rec.append("closed")

    def _cc(addr, timeout=None, *a, **k):
        rec.append((addr, timeout))
        return _Sock()
    monkeypatch.setattr(socket, "create_connection", _cc)
    assert efstore.probe_local_couchbase() is True
    assert rec[0][0] == ("cb.abc.cloud.couchbase.com", 11207)
    assert 0 < rec[0][1] <= 2.0
    assert rec[-1] == "closed"


# ---- the quiet degrade ----------------------------------------------------

@pytest.mark.parametrize("conn", ["couchbase://down.example",
                                  "couchbases://down.example"])
def test_unreachable_cluster_is_a_quiet_no(monkeypatch, conn, capsys):
    _env(monkeypatch, conn)

    def answer(url, req, kw):
        raise urllib.error.URLError(socket.timeout("timed out"))
    _fake_urlopen(monkeypatch, answer)
    _no_socket(monkeypatch)
    assert efstore.probe_local_couchbase() is False


def test_garbage_conn_string_is_a_quiet_no(monkeypatch):
    _env(monkeypatch, "couchbases://")
    _fake_urlopen(monkeypatch, lambda u, r, k: 200)
    _no_socket(monkeypatch)
    assert efstore.probe_local_couchbase() is False


# ---- multi-host connection strings ------------------------------------------

def test_multi_host_any_answering_node_is_ready(monkeypatch):
    _env(monkeypatch, "couchbases://n1.example,n2.example:11207,n3.example")

    def answer(url, req, kw):
        if "n1.example" in url:
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        return 200
    calls = _fake_urlopen(monkeypatch, answer)
    _no_socket(monkeypatch)
    assert efstore.probe_local_couchbase() is True
    urls = [c["url"] for c in calls]
    assert urls[0] == "https://n1.example:18091/pools"
    assert "https://n2.example:18091/pools" in urls
    assert all("n3.example" not in u for u in urls)   # stops at the first yes


# ---- the gates open for couchbases:// -----------------------------------------

def _only_tls_mgmt(url, req, kw):
    """A TLS-only cluster: only https://<host>:18091 answers."""
    if url == "https://cb.abc.cloud.couchbase.com:18091/pools":
        return 200
    raise urllib.error.URLError(ConnectionRefusedError("refused"))


def _record_sdk(monkeypatch):
    import couchbase.cluster as _cc
    record = []

    def _rec(*a, **k):
        record.append(a[0] if a else None)
        raise RuntimeError("stop at the SDK")
    monkeypatch.setattr(_cc, "Cluster", _rec)
    return record


def test_publish_gate_opens_for_a_tls_cluster(monkeypatch, tmp_path):
    """EntityStore._entities_collection (publish, unpublish, presence,
    imports) reaches the SDK connect for a couchbases:// string whose
    18091 answers -- it used to stop at the http://:8091 probe."""
    pytest.importorskip("couchbase")
    _env(monkeypatch, "couchbases://cb.abc.cloud.couchbase.com")
    calls = _fake_urlopen(monkeypatch, _only_tls_mgmt)
    _no_socket(monkeypatch)
    record = _record_sdk(monkeypatch)
    from membrane.entities import EntityStore
    est = EntityStore(path=str(tmp_path / "entities.json"), shared=True)
    est._cb_warned = True                       # keep stderr quiet
    assert est._entities_collection() is None   # our fake SDK refused
    assert record == ["couchbases://cb.abc.cloud.couchbase.com"]
    assert calls and calls[0]["url"].startswith("https://")


def test_peer_roster_gate_opens_for_a_tls_cluster(monkeypatch, tmp_path):
    pytest.importorskip("couchbase")
    _env(monkeypatch, "couchbases://cb.abc.cloud.couchbase.com")
    _fake_urlopen(monkeypatch, _only_tls_mgmt)
    _no_socket(monkeypatch)
    record = _record_sdk(monkeypatch)
    from membrane.peers import PeerSubstrate
    PeerSubstrate(field_path=str(tmp_path / "field.json"),
                  port=0, model="m", roster_fn=None)
    assert record == ["couchbases://cb.abc.cloud.couchbase.com"]


# ---- the SDK past the gate trusts what cb_open trusts -------------------------

@pytest.mark.parametrize("which", ["entities", "peers"])
def test_gated_sdk_connect_uses_cert_path(monkeypatch, tmp_path, which):
    """A self-hosted TLS cluster with COUCHBASE_CERT_PATH: past the probe,
    the entities/peers SDK connect must trust that CA too (as cb_open's
    substrate.connect already did) or the open gate leads nowhere."""
    pytest.importorskip("couchbase")
    _env(monkeypatch, "couchbases://cb.abc.cloud.couchbase.com")
    pem = tmp_path / "ca.pem"
    pem.write_text("x")
    monkeypatch.setenv("COUCHBASE_CERT_PATH", str(pem))
    monkeypatch.setattr(efstore, "probe_local_couchbase", lambda: True)
    import couchbase.auth as _ca
    seen = []
    monkeypatch.setattr(_ca, "PasswordAuthenticator",
                        lambda *a, **k: seen.append(k))
    _record_sdk(monkeypatch)
    if which == "entities":
        from membrane.entities import EntityStore
        est = EntityStore(path=str(tmp_path / "entities.json"), shared=True)
        est._cb_warned = True
        est._entities_collection()
    else:
        from membrane.peers import PeerSubstrate
        PeerSubstrate(field_path=str(tmp_path / "field.json"),
                      port=0, model="m", roster_fn=None)
    assert seen and seen[-1].get("cert_path") == str(pem)
