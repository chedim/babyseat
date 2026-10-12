"""THE SEAT, RUN BY HOME ASSISTANT (2026-10-12, the operator: "it should
install into HA and run inside it"): the membrane seat as HA's child process.

HA starts it once HA itself is up, keeps it alive (a crash or the seat's own
restart -- restarts.py, the seat SIGTERMs itself for new code -- is respawned),
and stops it with HA. A separate process, so a hang, a crash or the seat's
memory (1-3 GB on a busy field) never takes HA down with it.

    <config>/membrane/
      .membrane/      the seat's data dir (field, sessions, serve-auth.json, settings)
      home/           its HOME (nothing of the seat's lands in /root)
      seat.log        its output, rotated at 5 MB

The child listens on loopback only: HA's relay (relay.py) is the one door, with
the basic-auth pair the seat generates in serve-auth.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path

import aiohttp

from .const import SEAT_DIR

_LOGGER = logging.getLogger(__name__)

HEALTH_EVERY_S = 30
BOOT_GRACE_S = 180              # a cold boot (embedder probe, field load) may take minutes
STOP_GRACE_S = 30
BACKOFF_S = (5, 30, 120)
LOG_MAX = 5_000_000
# the child's environment: chosen, never HA's own (HA OS gives HA a SUPERVISOR_TOKEN --
# the seat runs model-written commands, it must not hold the keys to the host)
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
# the private beta's zip carries the package itself (scripts/build_release.py)
VENDOR = Path(__file__).resolve().parent / "vendor"


def package_found() -> bool:
    import importlib.util
    return (VENDOR / "membrane" / "opencode.py").is_file() or importlib.util.find_spec("membrane") is not None


def free_port(preferred: int) -> int:
    """`preferred` when nothing listens there, else one the OS gives."""
    for port in (preferred, 0):
        s = socket.socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return preferred


def child_env(base: Path, data: Path) -> dict:
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.update({
        "HOME": str(base / "home"), "CBL_HOME": str(base / "home" / ".cbl"),
        "MEMBRANE_DATA_DIR": str(data),
        "MEMBRANE_NO_UPDATE_CHECK": "1", "MEMBRANE_NO_ASK": "1", "MEMBRANE_NO_AUTOINSTALL": "1",
        "MEMBRANE_OFFLINE_PROBE": "0", "MEMBRANE_NO_BROWSER": "1",
        "MEMBRANE_NAME_CEREMONY": "1",          # a new seat asks its operator for its name (naming.py)
        "PYTHONUNBUFFERED": "1",
    })
    if (VENDOR / "membrane").is_dir():
        env["PYTHONPATH"] = str(VENDOR)         # the vendored package wins
    return env


def child_args(data: Path, backend: str, model: str, embed: str, listen: int, serve: int,
               python: str | None = None) -> list[str]:
    a = [python or sys.executable, "-m", "membrane.opencode", "--serve", "--no-launch",
         "--field", str(data / "field.json"), "--audit", str(data / "audit.jsonl"),
         "--listen", str(listen), "--listen-host", "127.0.0.1",
         "--serve-port", str(serve), "--serve-host", "127.0.0.1",
         "--backend", backend, "--no-absorb"]
    if model:
        a += ["--model", model]
    if embed:
        a += ["--embed-model", embed]
    return a


def read_name(data: Path) -> tuple[str, list]:
    """The seat's agreed name (naming.py writes it to settings.json); Bob until then."""
    try:
        d = json.loads((data / "settings.json").read_text())
        return str(d.get("name") or "") or "Bob", list(d.get("aliases") or [])
    except (OSError, ValueError):
        return "Bob", []


def read_auth(data: Path) -> tuple[str, str] | None:
    try:
        d = json.loads((data / "serve-auth.json").read_text())
        return str(d.get("user") or "opencode"), str(d.get("password") or "")
    except (OSError, ValueError):
        return None


class SeatSupervisor:
    """Start, watch, respawn and stop the seat. All on HA's loop; file work in
    the executor."""

    def __init__(self, hass, comp: dict, backend: str, model: str, embed: str, loops: int = 1):
        self.hass, self.comp = hass, comp
        self.backend, self.model, self.embed, self.loops = backend, model, embed, loops
        self.base = Path(hass.config.path(SEAT_DIR))
        self.data = self.base / ".membrane"
        self.proc: asyncio.subprocess.Process | None = None
        self.serve_port = 0
        self.started_at = 0.0
        self.restarts = 0
        self._stopping = False
        self._tasks: list[asyncio.Task] = []
        self._log = None
        self.extra_env = None           # async () -> dict: HASS_URL + a fresh HASS_TOKEN (ha_access.py)

    # -- the child ------------------------------------------------------------

    def _prepare(self) -> None:                     # executor
        for d in (self.data, self.base / "home"):
            d.mkdir(parents=True, exist_ok=True)
        log = self.base / "seat.log"
        if log.exists() and log.stat().st_size > LOG_MAX:
            os.replace(log, log.with_suffix(".log.1"))
        self._log = open(log, "ab", buffering=0)

    async def _spawn(self) -> None:
        await self.hass.async_add_executor_job(self._prepare)
        self.serve_port = free_port(18096)
        listen = free_port(18500)
        env = child_env(self.base, self.data)
        env["MEMBRANE_LOOPS_MAX"] = str(self.loops)
        if self.extra_env is not None:
            try:
                env.update(await self.extra_env())
            except Exception as e:                   # noqa: BLE001
                _LOGGER.warning("membrane: no Home Assistant access for the seat this run (%r)", e)
        args = child_args(self.data, self.backend, self.model, self.embed, listen, self.serve_port)
        _LOGGER.info("membrane: starting the seat (serve 127.0.0.1:%s, backend %s)", self.serve_port, self.backend)
        self.proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(self.base), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=self._log, stderr=self._log, start_new_session=True)
        self.started_at = time.time()
        self.comp["serve_url"] = f"http://127.0.0.1:{self.serve_port}"
        self.comp["serve_pass"] = ""                 # the relay waits until the seat wrote its pair

    async def _pair(self) -> None:
        """The seat writes serve-auth.json at its first boot (kept after)."""
        for _ in range(240):
            got = await self.hass.async_add_executor_job(read_auth, self.data)
            if got and got[1]:
                self.comp["serve_user"], self.comp["serve_pass"] = got
                return
            await asyncio.sleep(1)

    async def _healthy(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(self.comp["serve_url"] + "/global/health",
                                 timeout=aiohttp.ClientTimeout(total=15)) as r:
                    return r.status in (200, 401)    # 401: up, it asks the pair
        except Exception:                            # noqa: BLE001
            return False

    # -- the life ---------------------------------------------------------------

    async def start(self) -> None:
        if not await self.hass.async_add_executor_job(package_found):
            _LOGGER.error("membrane: the membrane package isn't installed (and this copy of the "
                          "integration carries none) -- install the release zip, or pip install membrane-seat")
            return
        self._stopping = False
        await self._spawn()
        self._tasks = [self.hass.async_create_background_task(self._watch(), "membrane seat"),
                       self.hass.async_create_background_task(self._pair(), "membrane seat pair")]

    async def _watch(self) -> None:
        fails = 0
        while not self._stopping:
            proc = self.proc
            if proc is None:
                return
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=HEALTH_EVERY_S)
            except asyncio.TimeoutError:
                self.comp["name"], self.comp["aliases"] = await self.hass.async_add_executor_job(read_name, self.data)
                if time.time() - self.started_at < BOOT_GRACE_S:
                    continue
                if await self._healthy():
                    fails = 0
                    continue
                fails += 1
                if fails < 4:
                    continue
                _LOGGER.warning("membrane: the seat stopped answering for 2 minutes -- restarting it")
                await self._terminate(proc)
            if self._stopping:
                return
            ran = time.time() - self.started_at
            code = proc.returncode
            fails = 0
            # a seat that ran a while (its own restart for new code, a crash after hours)
            # comes back at once; one dying at boot backs off
            self.restarts = 0 if ran > 300 else self.restarts + 1
            wait = 1 if self.restarts == 0 else BACKOFF_S[min(self.restarts - 1, len(BACKOFF_S) - 1)]
            _LOGGER.warning("membrane: the seat exited (code %s after %ds) -- back in %ss", code, int(ran), wait)
            await asyncio.sleep(wait)
            if self._stopping:
                return
            await self._spawn()
            self.hass.async_create_background_task(self._pair(), "membrane seat pair")

    async def _terminate(self, proc) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)          # the seat tears down cleanly on TERM
            await asyncio.wait_for(proc.wait(), timeout=STOP_GRACE_S)
        except asyncio.TimeoutError:
            _LOGGER.warning("membrane: the seat ignored TERM for %ss -- killing it", STOP_GRACE_S)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        if self.proc is not None:
            await self._terminate(self.proc)
        self.comp["serve_pass"] = ""
        if self._log is not None:
            await self.hass.async_add_executor_job(self._log.close)
            self._log = None
