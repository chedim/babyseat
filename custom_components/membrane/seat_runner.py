"""THE SEAT, RUN BY HOME ASSISTANT (#1143, operator 2026-11-15: "run a seat inside Home Assistant" is the default path again, on every Home Assistant flavour).
The 0.7.53 component ran the Python seat as Home Assistant's child process; this runs the native seat (`cliffvm serve`) the same way. No Home Assistant
import: the component's setup (__init__.py) hands it Home Assistant's executor and HTTP session.

  (a) PLATFORM   the host -> the release target: Linux x86_64/aarch64 -> the STATIC MUSL builds (they run on glibc and musl alike: Home Assistant OS,
                 Container, Core, an Android proot), macOS arm64/x86_64 -> the apple-darwin builds (developers); anything else: UnsupportedPlatform.
  (b) THE DIST   babyseat-<version>-<target>.tar.gz and its .sha256 from the release of the component's own version (manifest.json), the way install.sh and
                 addon/install-dist.sh fetch it: the tarball's sha256 is checked BEFORE it is unpacked, then every file against the folder's MANIFEST.sha256 and
                 the VERSION; only a folder that passed is moved to <config>/membrane/app/<version>/ (a failure leaves nothing). A verified folder is checked again
                 at each start (no download) and fetched again only when it no longer matches its manifest.
  (c) THE PROCESS `<dist>/bin/cliffvm serve --port <port> --bind 127.0.0.1 --data-dir <config>/membrane --fact ...` (seat_options.py), the secrets in its
                 environment (never argv), an environment of its own (never Home Assistant's: HA OS gives HA a SUPERVISOR_TOKEN). Ready when GET /global/health
                 answers with the admin token the seat writes at its start (<data>/admin.token); restarted when it exits, with a doubling backoff; given up after
                 MAX_QUICK_FAILURES exits in a row that each came before STABLE_RUN_S (the caller shows a repair); SIGTERM, then a kill, at a stop.
                 Its stdout goes to the logger at debug, its stderr at warning, both with secrets and anything token-shaped masked.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import tarfile
import tempfile
import time
from pathlib import Path

import aiohttp

from .seat_options import launch

_LOGGER = logging.getLogger(__name__)

RELEASE_BASE = "https://github.com/chedim/membrane/releases/download/v{version}"   # install.sh's and addon/install-dist.sh's
APP_DIR = "app"                       # <config>/membrane/app/<version>/: the dist folders
PID_FILE = "seat.pid"                 # <data>/seat.pid: the seat this component started (an orphan of a crashed Home Assistant is stopped at the next start)
DEFAULT_SERVE_PORT = 4096             # `cliffvm serve`'s own default port

# THE CONSTANTS (flagged for the operator's approval; the 0.7.53 supervisor's where it had one)
DOWNLOAD_TIMEOUT_S = 600              # one asset's download (a slow link on a Pi)
DOWNLOAD_MAX_BYTES = 256 * 1024 * 1024   # a tarball larger than this is not the release (they are ~10 MB)
BOOT_TIMEOUT_S = 180                  # the start: until /global/health answers (0.7.53's BOOT_GRACE_S)
HEALTH_POLL_S = 0.5                   # how often the start asks /global/health
STOP_GRACE_S = 30                     # SIGTERM -> kill (0.7.53's STOP_GRACE_S)
BACKOFF_FIRST_S = 5                   # the first restart's wait (0.7.53's BACKOFF_S[0]); it doubles per quick failure
BACKOFF_MAX_S = 300                   # the wait's cap
STABLE_RUN_S = 300                    # a seat that ran this long before it exited starts the count again (0.7.53's)
MAX_QUICK_FAILURES = 5                # this many quick exits (or failed starts) in a row: given up, a repair is shown
TOKEN_TTL = "30d"                     # the token the runner mints for Home Assistant (the admin token's own life is 30 days)
TOKEN_REFRESH_S = 24 * 3600           # how often a fresh token is minted (so a seat that runs for months keeps an unexpired token)
STDERR_TAIL = 20                      # the seat's last stderr lines kept for an error text
# the seat's environment: chosen, never Home Assistant's (0.7.53's ENV_KEEP)
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
SERVE_USER = "opencode"               # the Basic user the seat ignores (the password is the token: docs/auth.md)


class SeatRunnerError(Exception):
    """The seat could not be put in place or started; the text says why, with no secret in it."""


class UnsupportedPlatform(SeatRunnerError):
    pass


class DistError(SeatRunnerError):
    pass


class SeatStartError(SeatRunnerError):
    pass


# ---- (a) the platform ------------------------------------------------------------------------------------------------------------------------------

def release_target(system: str | None = None, machine: str | None = None) -> str:
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(str(machine).lower())
    if arch and system == "Linux":
        return f"{arch}-unknown-linux-musl"
    if arch and system == "Darwin":
        return f"{arch}-apple-darwin"
    raise UnsupportedPlatform(f"no seat build for this host ({system} {machine}): the seat runs on Linux and macOS on x86_64 or aarch64 (arm64). "
                              "Run the seat on another machine and choose \"Connect to a seat I run\".")


def asset_name(version: str, target: str) -> str:
    return f"babyseat-{version}-{target}.tar.gz"


def component_version() -> str:
    """The component's own version (manifest.json): the release whose seat it runs. Blocking (a file read): call it in the executor."""
    return str(json.loads((Path(__file__).with_name("manifest.json")).read_text())["version"])


# ---- (b) the dist folder ---------------------------------------------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def verify_dist(d: Path, version: str) -> None:
    """The folder is the dist of `version`: VERSION, bin/cliffvm, and every file of MANIFEST.sha256 present with its sha256. Raises DistError."""
    man = d / "MANIFEST.sha256"
    if not man.is_file() or not (d / "bin" / "cliffvm").is_file():
        raise DistError(f"{d.name} is not a dist folder (no MANIFEST.sha256 or bin/cliffvm)")
    try:
        have = (d / "VERSION").read_text().strip()
    except OSError:
        have = ""
    if have != version:
        raise DistError(f"the dist folder says version {have or '(none)'}, not {version}")
    bad, n = [], 0
    for line in man.read_text().splitlines():
        if not line.strip():
            continue
        want, _, name = line.partition("  ")
        n += 1
        f = (d / name).resolve()
        if not name or os.path.isabs(name) or ".." in Path(name).parts or d.resolve() not in f.parents or not f.is_file() or _sha(f) != want.strip():
            bad.append(name)
    if n == 0 or bad:
        raise DistError("MANIFEST.sha256: " + (", ".join(bad[:5]) + (" ..." if len(bad) > 5 else "") + " missing or changed" if bad else "empty"))


def install_dist(tarball: bytes, sidecar: str, asset: str, version: str, app_dir: Path) -> Path:
    """Check, unpack and verify a downloaded tarball; -> <app_dir>/<version>. Blocking: run it in the executor. A failure leaves nothing in app_dir."""
    want = (sidecar.split() or [""])[0].strip().lower()
    have = hashlib.sha256(tarball).hexdigest()
    if not want or want != have:
        raise DistError(f"the sha256 of {asset} is {have}, its .sha256 says {want or 'nothing'}: not the release, nothing installed")
    app_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".partial-", dir=app_dir))
    try:
        try:
            with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as t:
                t.extractall(stage, filter="data")         # no absolute path, no "..", no link out of the folder, no device
        except (tarfile.TarError, OSError) as e:
            raise DistError(f"{asset} could not be unpacked ({type(e).__name__}: {e}): nothing installed") from e
        tops = list(stage.iterdir())
        if len(tops) != 1 or not tops[0].is_dir():
            raise DistError(f"{asset} should hold one folder, it holds {len(tops)} entries: nothing installed")
        try:
            verify_dist(tops[0], version)
        except DistError as e:
            raise DistError(f"{asset}: {e}: nothing installed") from e
        (tops[0] / "bin" / "cliffvm").chmod(0o755)
        dest = app_dir / version
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tops[0], dest)
        return dest
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def installed_dist(app_dir: Path, version: str) -> Path | None:
    """The verified folder of `version`, or None (none, or one that no longer matches its manifest: removed). Leftover staging folders are removed. Blocking."""
    if app_dir.is_dir():
        for p in app_dir.glob(".partial-*"):
            shutil.rmtree(p, ignore_errors=True)
    d = app_dir / version
    if not d.is_dir():
        return None
    try:
        verify_dist(d, version)
        return d
    except DistError as e:
        _LOGGER.warning("membrane: the installed seat %s does not match its manifest (%s): downloading it again", version, e)
        shutil.rmtree(d, ignore_errors=True)
        return None


async def _fetch(session: aiohttp.ClientSession, url: str, limit: int) -> bytes:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_S)) as r:
            if r.status != 200:
                raise DistError(f"{url} answered {r.status}")
            buf = bytearray()
            async for chunk in r.content.iter_chunked(1 << 16):
                buf += chunk
                if len(buf) > limit:
                    raise DistError(f"{url} is larger than {limit} bytes: not the release")
            return bytes(buf)
    except DistError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise DistError(f"could not download {url} ({type(e).__name__}: {e})") from e


async def ensure_dist(session: aiohttp.ClientSession, run_blocking, app_dir: Path, version: str, target: str, base: str) -> Path:
    """The verified dist folder of `version` for `target`: the installed one, else downloaded from `base` and verified. Raises DistError."""
    found = await run_blocking(installed_dist, Path(app_dir), version)
    if found is not None:
        return found
    asset = asset_name(version, target)
    url = f"{str(base).rstrip('/')}/{asset}"
    _LOGGER.info("membrane: downloading the seat %s (%s) from %s", version, target, url)
    tarball = await _fetch(session, url, DOWNLOAD_MAX_BYTES)
    try:
        sidecar = (await _fetch(session, url + ".sha256", 4096)).decode("ascii", errors="replace")
    except DistError as e:
        raise DistError(f"{e}: no sha256 to check the download against, nothing installed") from e
    d = await run_blocking(install_dist, tarball, sidecar, asset, version, Path(app_dir))
    _LOGGER.info("membrane: the seat %s is in %s (sha256 and MANIFEST.sha256 checked)", version, d)
    return d


# ---- (c) the process -------------------------------------------------------------------------------------------------------------------------------

def port_free(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def free_port(preferred: int = DEFAULT_SERVE_PORT) -> int:
    """`preferred` when nothing listens there on 127.0.0.1, else one the OS gives."""
    if preferred and port_free(preferred):
        return int(preferred)
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


TOKENISH = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*|sk-[A-Za-z0-9_-]{6,}")


def redact(text: str, secrets) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return TOKENISH.sub("***", text)


def _cmdline(pid: int) -> str:
    """The command line of `pid` ('' when there is none or it cannot be read). Blocking."""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        pass
    try:
        import subprocess
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class SeatProcess:
    """One seat: start, supervise, stop. On Home Assistant's loop; the file work through `run_blocking` (hass.async_add_executor_job)."""

    BOOT_TIMEOUT_S = BOOT_TIMEOUT_S
    HEALTH_POLL_S = HEALTH_POLL_S
    STOP_GRACE_S = STOP_GRACE_S
    BACKOFF_FIRST_S = BACKOFF_FIRST_S
    BACKOFF_MAX_S = BACKOFF_MAX_S
    STABLE_RUN_S = STABLE_RUN_S
    MAX_QUICK_FAILURES = MAX_QUICK_FAILURES
    TOKEN_REFRESH_S = TOKEN_REFRESH_S

    def __init__(self, binary, data_dir, port: int, settings: dict, *, run_blocking, session: aiohttp.ClientSession | None = None,
                 on_give_up=None, on_token=None, logger: logging.Logger | None = None) -> None:
        self.binary, self.data_dir, self.port = Path(binary), Path(data_dir), int(port)
        self.settings = dict(settings)
        self._run_blocking, self._session = run_blocking, session
        self.on_give_up, self.on_token = on_give_up, on_token
        self._log = logger or _LOGGER
        self.proc: asyncio.subprocess.Process | None = None
        self.token = ""
        self.gave_up = False
        self.delays: list[float] = []             # the restart waits so far (what the log says)
        self._fails = 0
        self._started = 0.0
        self._stopping = False
        self._tasks: list[asyncio.Task] = []
        self._pumps: list[asyncio.Task] = []
        self._tail: list[str] = []
        self._secrets: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    def backoff(self, fails: int) -> float:
        return min(self.BACKOFF_FIRST_S * 2 ** max(fails - 1, 0), self.BACKOFF_MAX_S)

    # -- the child ---------------------------------------------------------------------------------------------------------------------------------

    def _env(self, secrets: dict) -> dict:
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env["HOME"] = str(self.data_dir / "home")
        env.update(secrets)
        return env

    def _prepare(self) -> None:                       # blocking
        (self.data_dir / "home").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "admin.token").unlink(missing_ok=True)      # the one this start writes is the one read below

    def _read_token(self) -> str:                     # blocking
        try:
            return (self.data_dir / "admin.token").read_text().strip()
        except OSError:
            return ""

    def _write_pid(self, pid: int | None) -> None:    # blocking
        f = self.data_dir / PID_FILE
        if pid is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(f"{pid}\n")

    def _orphan(self) -> int | None:                  # blocking: the pid of a seat of this folder left running by a Home Assistant that died
        f = self.data_dir / PID_FILE
        try:
            pid = int(f.read_text().strip())
        except (OSError, ValueError):
            f.unlink(missing_ok=True)
            return None
        cmd = _cmdline(pid) if pid > 1 and pid != os.getpid() else ""
        if str(self.binary.parent.parent.parent) in cmd and " serve " in f" {cmd} " and str(self.data_dir) in cmd:
            return pid
        f.unlink(missing_ok=True)                     # another process has that pid now: never touched
        return None

    async def reap_orphan(self) -> None:
        pid = await self._run_blocking(self._orphan)
        if pid is None:
            return
        self._log.warning("membrane: a seat of %s from an earlier run is still running (pid %s): stopping it first", self.data_dir, pid)
        for sig, wait in ((signal.SIGTERM, self.STOP_GRACE_S), (signal.SIGKILL, 5)):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            t = time.monotonic()
            while time.monotonic() - t < wait:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.1)
            else:
                continue
            break
        await self._run_blocking(self._write_pid, None)

    async def _pump(self, stream, level: int, keep: bool) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = redact(line.decode(errors="replace").rstrip(), self._secrets + [self.token])
            if keep:
                self._tail = (self._tail + [text])[-STDERR_TAIL:]
            self._log.log(level, "membrane seat: %s", text)

    async def _healthy(self, token: str) -> bool:
        auth = "Basic " + base64.b64encode(f"{SERVE_USER}:{token}".encode()).decode()
        try:
            if self._session is None:
                async with aiohttp.ClientSession() as s:
                    async with s.get(self.url + "/global/health", headers={"Authorization": auth}, timeout=aiohttp.ClientTimeout(total=5)) as r:
                        return r.status == 200
            async with self._session.get(self.url + "/global/health", headers={"Authorization": auth}, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return r.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    def _why(self, head: str) -> str:
        tail = [x for x in self._tail if x.strip()][-5:]
        return head + (": " + " | ".join(tail) if tail else "")

    async def _launch(self) -> None:
        """Spawn the seat and wait until it answers its health with this start's admin token. Raises SeatStartError (the process is gone then)."""
        argv, secrets = launch(self.binary, self.port, self.data_dir, self.settings)
        self._secrets = [v for v in secrets.values() if v]
        await self._run_blocking(self._prepare)
        self._tail = []
        self._log.info("membrane: starting the seat on 127.0.0.1:%s (data %s)", self.port, self.data_dir)
        try:
            self.proc = await asyncio.create_subprocess_exec(*argv, cwd=str(self.data_dir), env=self._env(secrets), stdin=asyncio.subprocess.DEVNULL,
                                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        except OSError as e:
            raise SeatStartError(f"could not start {self.binary} ({type(e).__name__}: {e})") from e
        self._started = time.monotonic()
        await self._run_blocking(self._write_pid, self.proc.pid)
        self._pumps = [asyncio.ensure_future(self._pump(self.proc.stdout, logging.DEBUG, False)),
                       asyncio.ensure_future(self._pump(self.proc.stderr, logging.WARNING, True))]
        deadline = time.monotonic() + self.BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.returncode is not None:
                await asyncio.wait(self._pumps, timeout=2)
                raise SeatStartError(self._why(f"the seat exited with code {self.proc.returncode} at its start"))
            token = await self._run_blocking(self._read_token)
            if token and await self._healthy(token):
                self.token = token
                return
            await asyncio.sleep(self.HEALTH_POLL_S)
        await self._terminate()
        raise SeatStartError(self._why(f"the seat did not answer /global/health within {self.BOOT_TIMEOUT_S} s"))

    async def _terminate(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)          # the seat and what it started (its own session: start_new_session)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=self.STOP_GRACE_S)
            except asyncio.TimeoutError:
                self._log.warning("membrane: the seat ignored SIGTERM for %s s: killing it", self.STOP_GRACE_S)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                await proc.wait()
        if self._pumps:
            await asyncio.wait(self._pumps, timeout=2)
        await self._run_blocking(self._write_pid, None)

    # -- the life ------------------------------------------------------------------------------------------------------------------------------------

    async def start(self) -> None:
        """The first start: raises SeatStartError (and SeatOptionsError for settings it cannot start with). Then the seat is supervised."""
        self._stopping, self.gave_up, self._fails = False, False, 0
        await self._launch()
        self._tasks = [asyncio.ensure_future(self._watch()), asyncio.ensure_future(self._refresh())]

    async def _watch(self) -> None:
        while not self._stopping:
            rc = await self.proc.wait()
            if self._stopping:
                return
            ran = time.monotonic() - self._started
            self._fails = 0 if ran >= self.STABLE_RUN_S else self._fails + 1
            reason = self._why(f"the seat exited with code {rc} after {int(ran)} s")
            while True:
                if self._fails >= self.MAX_QUICK_FAILURES:
                    self.gave_up = True
                    self._log.error("membrane: %s; it failed %s times in a row, not restarted (reload the integration to try again)", reason, self._fails)
                    if self.on_give_up is not None:
                        self.on_give_up(reason)
                    return
                wait = self.backoff(self._fails)
                self.delays.append(wait)
                self._log.warning("membrane: %s; restarting it in %s s", reason, wait)
                await asyncio.sleep(wait)
                if self._stopping:
                    return
                try:
                    await self._launch()
                    break
                except SeatStartError as e:
                    self._fails += 1
                    reason = str(e)

    async def _refresh(self) -> None:
        """Every TOKEN_REFRESH_S a fresh token (`cliffvm token mint`), so Home Assistant never holds an expired one."""
        while not self._stopping:
            await asyncio.sleep(self.TOKEN_REFRESH_S)
            try:
                p = await asyncio.create_subprocess_exec(str(self.binary), "token", "mint", "--sub", "ha", "--name", "Home Assistant", "--ttl", TOKEN_TTL,
                                                         "--secret-file", str(self.data_dir / "door.secret"), stdin=asyncio.subprocess.DEVNULL,
                                                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self._env({}))
                out, err = await asyncio.wait_for(p.communicate(), timeout=60)
            except (OSError, asyncio.TimeoutError) as e:
                self._log.warning("membrane: could not mint a fresh token for the seat (%s)", type(e).__name__)
                continue
            tok = out.decode(errors="replace").strip()
            if p.returncode != 0 or not tok:
                self._log.warning("membrane: could not mint a fresh token for the seat (exit %s): %s", p.returncode,
                                  redact(err.decode(errors="replace").strip(), self._secrets)[:300])
                continue
            self.token = tok
            if self.on_token is not None:
                self.on_token(tok)

    async def stop(self) -> None:
        """SIGTERM, then a kill after STOP_GRACE_S; the supervision ends. Safe to call twice."""
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        await self._terminate()
