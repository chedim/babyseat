"""Per-project membrane env: share a Couchbase field without repo secrets.

The shared substrate (docs/shared-field-workflow.md) wants every seat on a
project pointed at one Couchbase cluster. Credentials must NEVER live in the
repo (a `.env` in $PWD leaks on the first `git add .`), so they are stored
per-project but OUTSIDE the repository, under::

    ~/.membrane/projects/<slug>.env      (chmod 600)

`<slug>` derives from the project's absolute path (readable prefix + short
hash, so two sibling checkouts never collide). `load_project_env` exports the
file into os.environ at CLI start (existing env always wins); the couchbase
layer in field.py reads the same env names as before -- nothing downstream
changes.

`couchbase_onboarding` is the interactive path: when nothing configures the
store for a project (no env vars, no project env file) and we are on a TTY,
ask once whether to connect a cluster/bucket; on YES write the project env
file; on NO remember the decline (MEMBRANE_ASKED_COUCHBASE=1) so the question
does not become a nag. Non-interactive runs (no TTY, MEMBRANE_NO_ASK=1) skip
silently -- degradation, never a blocking prompt.
"""
from __future__ import annotations

import getpass as _getpass
import hashlib
import os
import re
import stat
import sys
from pathlib import Path
from typing import Callable

COUCHBASE_ENV_KEYS = ("MEMBRANE_STORE", "COUCHBASE_CONN_STRING",
                      "COUCHBASE_USER", "COUCHBASE_PASSWORD",
                      "COUCHBASE_BUCKET", "COUCHBASE_EPHEMERAL_BUCKET")
DECLINE_KEY = "MEMBRANE_ASKED_COUCHBASE"
# only keys under these namespaces may be exported from a project env file
_EXPORT_PREFIXES = ("MEMBRANE_", "COUCHBASE_")


def project_slug(cwd: Path | str | None = None) -> str:
    """Stable, filesystem-safe per-project id: `<readable-path>-<hash8>`.

    The hash pins the ABSOLUTE path so `~/work/a` and `~/work/a/b` (or two
    same-named folders on different machines' mount points) never share a
    file; the readable prefix keeps `ls ~/.membrane/projects/` meaningful.
    """
    raw = str(Path(cwd) if cwd else Path.cwd()).rstrip("/")
    keep = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower()
    digest = hashlib.sha1(raw.encode()).hexdigest()[:8]
    return f"{keep[:48]}-{digest}" if keep else digest


def projects_dir(home: Path | None = None) -> Path:
    return (Path(home) if home else Path.home()) / ".membrane" / "projects"


def project_env_path(cwd: Path | str | None = None,
                     home: Path | None = None) -> Path:
    return projects_dir(home) / (project_slug(cwd) + ".env")


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal dotenv: KEY=VALUE lines, `#` comments, optional quotes.
    Everything else (blank lines, stray text) is skipped, not an error --
    a hand-edited file must never brick a launch."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def load_project_env(cwd: Path | str | None = None,
                     home: Path | None = None,
                     environ: dict | None = None) -> dict[str, str]:
    """Read the project env file and export it (setdefault: a real
    environment variable -- the operator's shell profile -- always wins).
    Returns what was applied; never writes, never raises."""
    env = os.environ if environ is None else environ
    vals = {k: v for k, v in parse_env_file(project_env_path(cwd, home)).items()
            if k.startswith(_EXPORT_PREFIXES)}
    for k, v in vals.items():
        env.setdefault(k, v)
    return vals


def _write_env_file(path: Path, vals: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# membrane per-project env -- lives OUTSIDE the repo on"
             " purpose:",
             "# never copy this into the project (secrets stay off git)."]
    lines += [f"{k}={v}" for k, v in sorted(vals.items())]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)          # 600: owner-only


def store_configured(environ: dict | None = None) -> bool:
    """Does anything OUTSIDE the interactive path already configure the
    store? (explicit MEMBRANE_STORE, or any COUCHBASE_* var)."""
    env = os.environ if environ is None else environ
    if env.get("MEMBRANE_STORE", "").strip():
        return True
    return any(env.get(k, "").strip() for k in COUCHBASE_ENV_KEYS
               if k != "MEMBRANE_STORE")


def couchbase_onboarding(cwd: Path | str | None = None,
                         home: Path | None = None,
                         environ: dict | None = None,
                         input_fn: Callable[[str], str] = input,
                         getpass_fn: Callable[[str], str] = _getpass.getpass,
                         is_tty: Callable[[], bool] =
                         lambda: sys.stdin.isatty(),
                         probe_fn: Callable[[], bool] | None = None,
                         force: bool = False,
                         quiet: bool = False) -> bool:
    """Offer to connect a shared Couchbase field when nothing else has.

    Skips (returns False, no side effects) when: a previous decline is on
    record (unless force), the store is already configured via env, a local
    cluster answers at the auto-detected defaults (field.py auto-detects it
    anyway -- asking would be redundant), stdin is not a TTY, or
    MEMBRANE_NO_ASK is set. On YES writes ~/.membrane/projects/<slug>.env
    (600) and exports the values into the environment so THIS launch shares
    immediately. Returns True iff a new project env file was written.
    """
    env = os.environ if environ is None else environ
    path = project_env_path(cwd, home)
    have = parse_env_file(path)
    if not force:
        if env.get("MEMBRANE_NO_ASK", "").strip():
            return False
        if not is_tty():
            return False
        if store_configured(env) or store_configured(have):
            return False
        if have.get(DECLINE_KEY, "").strip() == "1":
            return False
        probe = probe_fn
        if probe is None:
            def probe() -> bool:
                from .field import ExemplarStore
                return ExemplarStore._probe_local_couchbase()
        try:
            if probe():
                return False          # local cluster: auto-detect handles it
        except Exception:
            pass
    elif store_configured(env) and not quiet:
        print("[membrane] couchbase already configured via environment; "
              "the interactive answers below will NOT take effect for this "
              "launch (env wins).", file=sys.stderr)

    if not quiet:
        print("[membrane] no memory sharing configured for this project "
              f"({Path(cwd) if cwd else Path.cwd()}).")
        print("           Connect a Couchbase cluster so teammates share "
              "this project's field? [y/N]")
    try:
        ans = input_fn("  connect couchbase? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans not in ("y", "yes"):
        # remember the decline so the question stays a one-time offer
        have[DECLINE_KEY] = "1"
        have.pop("MEMBRANE_STORE", None)   # decline must not enable sharing
        _write_env_file(path, have)
        if not quiet:
            print(f"[membrane] okay -- staying private. Revisit with "
                  f"`membrane connect` (env file: {path}).", file=sys.stderr)
        return False

    print("  (enter to accept the default; ~/.membrane/projects/ keeps the"
          " secrets off the repo)")
    conn = input_fn("  conn string [couchbase://127.0.0.1]: ").strip()
    user = input_fn("  user [Administrator]: ").strip()
    pw = getpass_fn("  password: ").strip()
    bucket = input_fn("  bucket [membrane]: ").strip()
    eph = input_fn("  ephemeral bucket [<bucket>-ephemeral, empty=auto]: ").strip()
    canon = bucket or "membrane"
    vals = dict(have)
    vals.update({
        "MEMBRANE_STORE": "couchbase",
        "COUCHBASE_CONN_STRING": conn or "couchbase://127.0.0.1",
        "COUCHBASE_USER": user or "Administrator",
        "COUCHBASE_PASSWORD": pw or "password",
        "COUCHBASE_BUCKET": canon,
    })
    if eph:
        vals["COUCHBASE_EPHEMERAL_BUCKET"] = eph
    else:
        vals.pop("COUCHBASE_EPHEMERAL_BUCKET", None)  # auto-resolve at boot
    vals[DECLINE_KEY] = "0"     # an explicit connect overrides a past decline
    _write_env_file(path, vals)
    for k, v in vals.items():   # this launch shares immediately
        if k.startswith(_EXPORT_PREFIXES):
            env.setdefault(k, v)
    if not quiet:
        print(f"[membrane] shared store on: couchbase "
              f"({vals['COUCHBASE_CONN_STRING']}, canon bucket {canon}, "
              f"ephemeral tier {eph or canon + '-ephemeral (auto)'}); creds "
              f"at {path} (never commit them there).", file=sys.stderr)
    return True