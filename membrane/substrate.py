"""THE SUBSTRATE: what every store shares -- the seat's identity (the origin
tag), the lab-isolation guard, and the Couchbase bucket open ladder.

    cb_open()  -> (cluster, bucket name): connect from the COUCHBASE_* env
                  (couchbases:// gets a CA bundle: Capella), then make sure
                  the bucket exists -- create it through the management REST
                  API (the SDK cannot), or, when the cluster's RAM quota is
                  spoken for, REUSE an existing bucket (a cortex bucket
                  first): the `membrane` scope namespaces everything, so
                  sharing a bucket is collision-free."""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import urllib.request
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

DATA_DIRNAME = ".membrane"   # the per-project data dir (cli.DATA_DIR)


def _is_lab_field_path(field_path: str | Path) -> bool:
    """EXPERIMENTS-ISOLATION predicate: does this field path smell like a LAB
    field (the experiments/ dir, a tempdir, or a pytest tmp fixture)? Lab
    membranes build Membrane(field_path=tempfile...) with the default store
    autodetect; without this guard they silently join the LIVE shared
    substrate -- the observed failure was a dream-loop field reusing the
    cortex-core bucket. False for everything that looks like a real membrane
    (a .membrane/ data dir, an explicit user path) -- those keep auto-detect."""
    p = Path(field_path).resolve()
    parts = p.parts
    if any(seg.startswith("dream-loop-") or seg == "pytest-" or
           seg.startswith("pytest-of-") for seg in parts):
        return True
    if "experiments" in parts and p.suffix == ".json" and \
            parts[-1].startswith("field-"):
        return True
    if "site-packages" in parts:      # never expected; kept out of paranoia
        return False
    try:
        tmp_roots = {Path(tempfile.gettempdir()).resolve()}
        have_common = any(p.is_relative_to(t) for t in tmp_roots)
    except (OSError, ValueError):
        have_common = False
    return have_common


def _origin_tag(field_path: str | Path) -> str:
    """The origin axis: "<start-folder>@<hostname>", computed from the RESOLVED
    field path (so two membranes started from the same folder agree on the tag
    regardless of symlinks/relative paths). This is the wire identity every
    write stamps and every read filters by. The START FOLDER is the project
    directory, not the data dir: for the per-project layout (<proj>/.membrane/
    field.json) that is the PARENT of the field's directory.

    PERSISTED IDENTITY: the derived tag is only a DEFAULT. On first boot the
    tag is written to <data-dir>/origin (mode 600, gitignored like the rest of
    .membrane/) and every later boot reuses it -- so renaming the repo (or
    moving it) keeps the same origin: your couchbase patch stays YOURS, your
    memories keep their provenance, forgets keep matching them. Rename-
    migration is zero-step for the common case; delete <data-dir>/origin to
    deliberately fork a new identity. Hand-edit it to ADOPT an old identity
    (e.g. after restoring a clone from a backup path)."""
    import socket
    p = Path(field_path).resolve()
    data_dir = (p.parent if p.parent.name == DATA_DIRNAME
                else p.parent / DATA_DIRNAME)
    tag_file = data_dir / "origin"
    if tag_file.is_file():
        stored = tag_file.read_text().strip()
        if stored:
            return stored
    tag = (f"{p.parent.name if p.parent.name != DATA_DIRNAME
            else p.parent.parent.name}@{socket.gethostname()}")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        tag_file.write_text(tag + "\n")
        tag_file.chmod(0o600)
    except OSError:
        pass                    # read-only cwd: fall back to the derived tag
    return tag


# ---- the bucket ladder ---------------------------------------------------------------
_CA_BUNDLES = ("/etc/ssl/cert.pem", "/opt/homebrew/etc/ca-certificates/cert.pem",
               "/usr/local/etc/openssl@3/cert.pem")


def _creds() -> tuple[str, str, str, str]:
    return (os.environ.get("COUCHBASE_CONN_STRING", "couchbase://127.0.0.1"),
            os.environ.get("COUCHBASE_USER", "Administrator"),
            os.environ.get("COUCHBASE_PASSWORD", "password"),
            os.environ.get("COUCHBASE_BUCKET", "membrane"))


def connect(conn: str, user: str, pw: str, wait_s: float = 10.0):
    """A ready cluster handle. TLS connstrings (couchbases://: Capella and
    secured clusters) get a root CA: COUCHBASE_CERT_PATH, else a well-known
    system bundle."""
    from couchbase.auth import PasswordAuthenticator
    from couchbase.cluster import Cluster
    from couchbase.options import ClusterOptions
    opts = {}
    cert = os.environ.get("COUCHBASE_CERT_PATH", "").strip()
    if not cert and conn.startswith("couchbases"):
        cert = next((c for c in _CA_BUNDLES if os.path.isfile(c)), "")
    if cert:
        opts["cert_path"] = cert
    cluster = Cluster(conn, ClusterOptions(PasswordAuthenticator(user, pw, **opts)))
    cluster.wait_until_ready(timedelta(seconds=wait_s))
    return cluster


def _host(conn: str) -> str:
    return conn.split("://", 1)[-1].split("/")[0].split(",")[0].split(":")[0]


def _rest(host: str, user: str, pw: str, path: str, data: dict | None = None,
          timeout: float = 10.0):
    req = urllib.request.Request(f"http://{host}:8091{path}",
                                 method="POST" if data is not None else "GET")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, data=urlencode(data).encode() if data is not None else None,
                                timeout=timeout) as r:
        return r.read()


def bucket_names(cluster, host: str, user: str, pw: str) -> list[str]:
    """SDK discovery first (the only path on Capella, where :8091 is not
    exposed), the management REST as the fallback."""
    try:
        names = [b.name for b in cluster.buckets().get_all_buckets()]
        if names:
            return names
    except Exception:
        pass
    try:
        return [b.get("name", "") for b in json.loads(_rest(host, user, pw, "/pools/default/buckets",
                                                              timeout=5))]
    except Exception:
        return []


def reuse_choice(names: list[str]) -> str | None:
    names = [n for n in names if n]
    for pref in ("cortex-core", "cortex"):
        for n in names:
            if pref in n.lower():
                return n
    return names[0] if names else None


def cb_open(wait_s: float = 10.0):
    """(cluster, bucket): the bucket exists when this returns. Raises when the
    cluster is unreachable or has no usable bucket."""
    conn, user, pw, bucket = _creds()
    cluster = connect(conn, user, pw, wait_s)
    mgr = cluster.buckets()
    try:
        mgr.get_bucket(bucket)
        return cluster, bucket
    except Exception:
        pass
    host = _host(conn)
    last = None
    for mb in (256, 128, 100):             # a quota-tight cluster refuses big asks
        try:
            _rest(host, user, pw, "/pools/default/buckets",
                  {"name": bucket, "ramQuotaMB": mb, "bucketType": "couchbase"})
            mgr.get_bucket(bucket)
            return cluster, bucket
        except Exception as e:
            last = e
    reuse = reuse_choice(bucket_names(cluster, host, user, pw))
    if not reuse:
        raise RuntimeError(f"bucket '{bucket}' missing and cannot be created ({last}); no "
                           f"existing bucket to reuse either -- free RAM quota or set "
                           f"COUCHBASE_BUCKET to an existing bucket")
    print(f"[membrane] bucket '{bucket}' not creatable ({last}); sharing existing bucket "
          f"'{reuse}' (the membrane scope namespaces it -- collision-free)", file=sys.stderr)
    mgr.get_bucket(reuse)
    return cluster, reuse
