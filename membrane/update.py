"""Self-update: check the install source at startup, show what changed, ask.

Sources handled (in order of detection):
  1. GIT REPO   -- the package lives inside a git checkout (editable install,
                   in-repo venv): `git fetch` + compare HEAD to its upstream;
                   update = `git pull --ff-only` (a dirty tree or diverged
                   branch NEVER force-merges -- it skips with instructions).
  2. PIP DIRECT -- an install recorded with a direct_url (pip/uv from a git
                   URL): `pip install --upgrade` of the same URL.
  3. NONE       -- a release install: not checking (say so once, quietly).

Contract:
  - the check happens at STARTUP ONLY (cli.main), never mid-session and
    never while the process is being edited -- a membrane does not restart
    itself while it runs (the disaster route).
  - network is bounded: every git/HTTP step has a short timeout; any failure
    = "no" (start the membrane; update never blocks boot).
  - ASK before updating, and put the CHANGELOG INSIDE the prompt: the user
    sees what changed before answering. Non-interactive (no TTY) never
    prompts -- it prints the same changelog with instructions instead.
  - opt-out: MEMBRANE_NO_UPDATE_CHECK=1 (or `membrane update` for a manual,
    non-interactive run of the same path).

After the update, the process RE-EXECS itself with the same argv, so the user
lands on the new code in the same terminal; field/schema compatibility is the
schema sliding window's job (field.py SCHEMA_VERSION), not the updater's.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_CHECK_TIMEOUT = 6          # network ops: seconds (boot must not hang)
_COMMIT_LIMIT = 12          # changelog lines shown in the prompt


def _git(repo: Path, *args: str, timeout: float = _CHECK_TIMEOUT) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        timeout=timeout).stdout.strip()


def _find_repo() -> Path | None:
    """The git checkout backing THIS membrane (editable/in-repo install)."""
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here):
        if (cand / ".git").exists():
            return cand
    return None


def _pip_direct_url() -> str | None:
    """A git URL this package was pip/uv-installed from, if any."""
    import importlib.metadata as md
    try:
        for f in md.distribution("membrane").files or []:
            if f.name == "direct_url.json":
                import json
                return json.loads(Path(f.locate()).read_text()).get("url")
    except Exception:
        pass
    return None


def _fetch_remote(repo: Path, branch: str) -> str | None:
    """The upstream tip after a fetch (None: offline / no upstream)."""
    upstream = _git(repo, "rev-parse", "--abbrev-ref",
                    f"{branch}@{{upstream}}")
    if not upstream or "upstream" in upstream:
        return None
    remote = upstream.split("/")[0]
    try:
        subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", remote],
                       capture_output=True, timeout=_CHECK_TIMEOUT,
                       check=True)
    except Exception:
        return None
    return _git(repo, "rev-parse", upstream) or None


def _changelog(repo: Path, rng: str) -> list[str]:
    """One-line-per-commit changelog for a rev range (newest first)."""
    log = _git(repo, "log", "--oneline", f"--max-count={_COMMIT_LIMIT}", rng)
    return log.splitlines() if log else []


def check(source: Path | None = None, timeout: float = _CHECK_TIMEOUT) -> dict:
    """Is an update available? Bounded, silent on failure.

    Returns {available, repo?, behind, changelog?, local?, remote?,
             upstream, mode} -- `mode` is 'git' | 'direct' | 'none'."""
    repo = source or _find_repo()
    if repo is not None and os.environ.get("MEMBRANE_GIT_UPDATES", "1") != "0":
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "main"
        local = _git(repo, "rev-parse", "HEAD")
        remote = _fetch_remote(repo, branch) if local else None
        if local and remote and local != remote:
            behind = _git(repo, "rev-list", "--count", f"{local}..{remote}")
            return {"available": behind not in ("", "0"),
                    "mode": "git", "repo": repo, "branch": branch,
                    "local": local[:10], "remote": remote[:10],
                    "behind": int(behind or 0),
                    "changelog": _changelog(repo, f"{local}..{remote}")}
        return {"available": False, "mode": "git", "repo": repo}
    url = _pip_direct_url()
    if url:
        return {"available": False, "mode": "direct",
                "url": url}     # checked lazily by `membrane update`
    return {"available": False, "mode": "none"}


def perform(info: dict) -> bool:
    """Apply an update (mode 'git' only from the startup path). Never
    force-merges: ff-only; a dirty/diverged tree skips with instructions."""
    if info.get("mode") != "git" or not info.get("repo"):
        return False
    repo: Path = info["repo"]
    branch = info.get("branch") or "main"
    if _git(repo, "status", "--porcelain"):
        print(f"[membrane] update skipped: {repo} has uncommitted changes; "
              f"commit or stash, then `membrane update`", file=sys.stderr)
        return False
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "pull", "--ff-only", branch],
            capture_output=True, text=True, timeout=120)
    except Exception as e:
        print(f"[membrane] update failed ({e})", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(f"[membrane] update failed (git pull --ff-only):\n{r.stderr}",
              file=sys.stderr)
        return False
    return True


def _reexec() -> None:
    os.execv(sys.executable, [sys.executable, "-m", "membrane.cli",
                              *sys.argv[1:]])


def prompt_and_update(info: dict, interactive: bool) -> None:
    """Show the changelog INSIDE the y/n prompt; update + re-exec on yes."""
    mode = info.get("mode")
    if mode == "direct":
        print(f"[membrane] pip/uv install from {_pip_direct_url()} -- to "
              f"update run: pip install --upgrade "
              f"(or: uv tool upgrade membrane)", file=sys.stderr)
        return
    changelog = info.get("changelog") or []
    behind = info.get("behind", 0)
    head = (f"[membrane] update available: {info.get('local')}.."
            f"{info.get('remote')} ({behind} commit"
            f"{'s' if behind != 1 else ''} behind on {info.get('branch')})")
    print(head, file=sys.stderr)
    for line in changelog:
        print(f"  {line}", file=sys.stderr)
    if len(changelog) == _COMMIT_LIMIT:
        print(f"  ... ({behind - _COMMIT_LIMIT} more)", file=sys.stderr)
    if not interactive:
        print("[membrane] non-interactive: not updating; run `membrane "
              "update` to apply", file=sys.stderr)
        return
    try:
        answer = input("Update membrane now? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        print("[membrane] continuing on the current version", file=sys.stderr)
        return
    if perform(info):
        print(f"[membrane] updated; restarting {info.get('local')} -> "
              f"{info.get('remote')}", file=sys.stderr)
        _reexec()


def maybe_check_on_boot(argv: list[str]) -> None:
    """The startup hook (cli.main): opt-out via MEMBRANE_NO_UPDATE_CHECK=1,
    a non-TTY, or bare `membrane update` handling its own path."""
    if os.environ.get("MEMBRANE_NO_UPDATE_CHECK", "") in ("1", "true", "yes"):
        return
    if not sys.stdin.isatty():
        return
    try:
        info = check()
    except Exception:
        return                      # never block boot on update trouble
    if not info.get("available"):
        return
    prompt_and_update(info, interactive=True)


def main() -> int:
    """`membrane update`: manual check + apply (works non-interactively --
    the user explicitly asked)."""
    os.environ.pop("MEMBRANE_NO_UPDATE_CHECK", None)
    info = check()
    if info["mode"] == "none":
        print("[membrane] not a git/direct install; nothing to update.")
        return 0
    if info["mode"] == "direct":
        url = _pip_direct_url()
        print(f"[membrane] upgrading from {url} ...")
        r = subprocess.run([sys.executable, "-m", "pip", "install",
                            "--upgrade", url])
        print("[membrane] updated; restart the membrane to load it")
        return r.returncode
    if not info.get("available"):
        print(f"[membrane] up to date ({info.get('local')} on "
              f"{info.get('branch')}).")
        return 0
    prompt_and_update(info, interactive=sys.stdin.isatty())
    return 0