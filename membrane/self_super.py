"""THE SEAT SUPERVISES BLURP'S PROCESS (#440 part 4); the default since part 5 (MEMBRANE_SELF_PROC=0 keeps the loop in the seat).

With the flag off nothing changes: the seat runs Blurp's loop on a thread of
its own (self_loop.start). With it on, the seat does not start that loop; a
Supervisor runs membrane.self_proc (part 3) as its child instead:

- It runs a child only while Blurp's mode is on or watch: switching it on
  spawns one, switching it off stops it. The child is posix_spawned (no fork
  of the threaded seat) and lowers its own priority (self_proc.NICE).
- The pause: the child reads the seat's pause in its context every turn and
  runs nothing while it holds (as the in-process loop does).
- A crash (any exit the supervisor did not ask for, or no handshake) is
  restarted after BACKOFF_START_S, doubling per crash in the window, capped
  at BACKOFF_MAX_S. HOLD_CRASHES crashes within HOLD_WINDOW_S hold it off
  until the person switches Blurp on again; the hold shows on the card
  (`proc`), on the panel's self status and in diag().
- Stop: its stdin closed and SIGTERM, then SIGKILL after STOP_GRACE_S. The
  child also exits by itself when its stdin closes (the seat gone).
- Forwarding: the seat's /membrane/self/card|sheet|recent|status, the panel's
  self controls (mode, accept, name) and a person speaking (chat-in)
  go to the child's loopback port with FORWARD_TIMEOUT_S; a view that does
  not come back in time is the last good one (`proc.stale`), so the UI needs
  no change while the child restarts.
- One writer for self/: while a child process exists the seat writes nothing
  there. While none does (Blurp off, held, or between restarts) the seat is
  self/'s only writer again: it applies the controls and serves the views
  itself, from its state read afresh.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import self_proc as SP

FLAG = SP.FLAG
BACKOFF_START_S = 2.0       # #440 constant 2, approved: the first restart's wait after a crash...
BACKOFF_MAX_S = 60.0        # ...doubling per crash in the window, capped here
HOLD_CRASHES = 5            # #440 constant 3, approved: this many crashes...
HOLD_WINDOW_S = 600.0       # ...within this long hold Blurp off until the person switches it on again
STOP_GRACE_S = SP.STOP_GRACE_S          # #440 constant 4, approved: SIGTERM to SIGKILL
FORWARD_TIMEOUT_S = SP.TIMEOUT_S        # #440 constant 5, approved: a forwarded call, then the last good answer
VIEWS = ("/card", "/sheet", "/recent", "/status")

_SUPS: dict = {}
_reg_lock = threading.Lock()


def _SL():
    from . import self_loop as SL
    return SL


def enabled() -> bool:
    """#440 part 5: Blurp's own process is the default; MEMBRANE_SELF_PROC=0
    keeps the loop in the seat."""
    return os.environ.get(FLAG, "1") != "0"


def of(srv):
    """The seat's supervisor, or None (the flag off, or none started)."""
    if srv is None:
        return None
    return _SUPS.get(id(srv))


def boot(srv, seat_url: str, log_path=None):
    """The seat's boot, after self_loop.bind: the in-process loop (flag off;
    -> None) or a supervisor of Blurp's own process (flag on; -> it)."""
    if not enabled():
        _SL().start(srv)
        return None
    return Supervisor(srv, seat_url, log_path=log_path)


def close(srv) -> None:
    sup = of(srv)
    if sup is not None:
        sup.close()


def close_all() -> None:
    """The seat's teardown: every child stopped."""
    for sup in list(_SUPS.values()):
        try:
            sup.close()
        except Exception as e:                       # noqa: BLE001
            print(f"[self-super] close failed ({e!r})", file=sys.stderr)


class Crashes:
    """The crash policy: a doubling backoff per crash in the window; a hold
    once HOLD_CRASHES of them fall within HOLD_WINDOW_S."""

    def __init__(self):
        self.times: list[float] = []
        self.held = False

    @staticmethod
    def delay(k: int) -> float:
        """The wait before the restart after the k-th crash in the window."""
        return min(BACKOFF_MAX_S, BACKOFF_START_S * 2.0 ** (max(1, int(k)) - 1))

    def count(self, now: float | None = None) -> int:
        now = time.time() if now is None else float(now)
        return len([t for t in self.times if now - t < HOLD_WINDOW_S])

    def crash(self, now: float) -> float | None:
        """A crash at `now` -> the wait before the restart, or None: held."""
        if self.held:
            return None
        self.times = [t for t in self.times if now - t < HOLD_WINDOW_S] + [float(now)]
        if len(self.times) >= HOLD_CRASHES:
            self.held = True
            return None
        return self.delay(len(self.times))

    def clear(self) -> None:
        self.times, self.held = [], False


class Supervisor:
    """Blurp's process, kept by the seat (see the module doc)."""

    def __init__(self, srv, seat_url: str, env: dict | None = None, log_path=None, autostart: bool = True):
        self.srv = srv
        self.url = str(seat_url)
        self.env = env
        self.log_path = log_path
        mp = getattr(srv, "_models_path", None) if srv is not None else None
        self.data_dir = Path(mp).parent if mp else None
        self.mode = _SL().mode(srv) if srv is not None else "off"
        self._crashes = Crashes()
        self._lock = threading.RLock()
        self._ev = threading.Event()
        self._kid = None                 # the running child (handshake done)
        self._spawning = None            # a child spawned, its handshake not yet in
        self._starting = False           # a start under way (a process may exist)
        self._port: int | None = None
        self._closing = False
        self._next_at = 0.0              # time.monotonic() of the next start (a backoff)
        self._restarts = 0
        self._error: str | None = None
        self._last: dict = {}            # path -> the last good answer
        self._stale_seat = False         # the seat's own view of self/ is older than the child's writes
        self._thread = None
        if srv is not None:
            with _reg_lock:
                _SUPS[id(srv)] = self
        if autostart:
            self._thread = threading.Thread(target=self._run, name="self-super", daemon=True)
            self._thread.start()

    # ---- the state ------------------------------------------------------------------------

    def wanted(self) -> bool:
        return self.mode in ("on", "watch") and not self._crashes.held and not self._closing

    def _process(self) -> bool:
        """Does a child process exist (starting or running)? The seat writes nothing to self/ while one does."""
        return self._starting or self._kid is not None

    def diag(self) -> dict:
        with self._lock:
            kid = self._kid or self._spawning
            if self._crashes.held:
                state = "held"
            elif self._port is not None:
                state = "up"
            elif self._process():
                state = "starting"
            elif self.wanted():
                state = "restarting" if self._crashes.times else "starting"
            else:
                state = "off"
            return {"state": state, "pid": kid.pid if kid is not None else None, "port": self._port,
                    "mode": self.mode, "restarts": self._restarts, "crashes": self._crashes.count(),
                    "held": self._crashes.held, "error": self._error}

    def _kick(self) -> None:
        self._ev.set()

    def _attach_port(self, port: int) -> None:
        """A child's control port (the handshake's; tests attach a stub)."""
        with self._lock:
            self._port = int(port)

    # ---- the loop -------------------------------------------------------------------------

    def _run(self) -> None:
        while not self._closing:
            self._ev.clear()
            with self._lock:
                kid, want, busy = self._kid, self.wanted(), self._process()
                wait = max(0.0, self._next_at - time.monotonic())
            try:
                if want and not busy and wait <= 0.0:
                    self._start()
                    continue
                if not want and kid is not None:
                    self._stop_kid(kid)
                    continue
            except Exception as e:                   # noqa: BLE001 -- the supervisor itself never dies
                print(f"[self-super] {e!r}", file=sys.stderr)
            self._ev.wait(timeout=wait if (want and not busy) else None)

    def _start(self) -> None:
        with self._lock:
            if self._closing or self._process():
                return
            self._starting = True
            self._error = None

        def spawned(kid):
            with self._lock:
                self._spawning = kid
        try:                                         # the handshake may take up to the backoff's cap
            kid = SP.spawn(self.url, self.data_dir, env=self.env, log_path=self.log_path,
                           wait_s=BACKOFF_MAX_S, on_spawn=spawned)
        except Exception as e:                       # noqa: BLE001 -- no handshake: a crash
            with self._lock:
                self._starting, self._spawning = False, None
                self._stale_seat = True
                self._error = str(e)[:200]
            if not self._closing:
                self._crashed()
            return
        with self._lock:
            self._kid, self._spawning, self._starting = kid, None, False
            self._port = kid.port
            self._stale_seat = True
        self._watch(kid)
        if self._closing:
            self._stop_kid(kid)
        print(f"[self-super] Blurp's process up (pid {kid.pid}, port {kid.port})", file=sys.stderr)

    def _watch(self, kid) -> None:
        """Its own thread reaps the child (the only reaper) and reports the exit."""
        kid._done = threading.Event()

        def wait():
            try:
                _, status = os.waitpid(kid.pid, 0)
                kid.returncode = os.waitstatus_to_exitcode(status)
            except ChildProcessError:
                if kid.returncode is None:
                    kid.returncode = -1
            kid.close_stdin()
            kid._done.set()
            self._exited(kid)
        threading.Thread(target=wait, name=f"self-super-wait-{kid.pid}", daemon=True).start()

    def _exited(self, kid) -> None:
        with self._lock:
            if self._kid is kid:
                self._kid, self._port = None, None
            expected = getattr(kid, "_expected", False)
        if not expected and not self._closing:
            self._error = f"exited ({kid.returncode})"
            print(f"[self-super] Blurp's process exited unasked ({kid.returncode})", file=sys.stderr)
            self._crashed()
        self._kick()

    def _crashed(self) -> None:
        with self._lock:
            d = self._crashes.crash(time.time())
            if d is not None:
                self._next_at = time.monotonic() + d
                self._restarts += 1
        if d is None:
            print(f"[self-super] Blurp held off: {HOLD_CRASHES} crashes in {HOLD_WINDOW_S:g}s "
                  "(switch it on again to retry)", file=sys.stderr)
            try:
                from . import seat_panel as _PANEL
                _PANEL.note(self.srv, "self-held", crashes=HOLD_CRASHES, error=self._error)
            except Exception:
                pass
        self._kick()

    def _stop_kid(self, kid, grace: float | None = None) -> None:
        """SIGTERM (its stdin closed too), then SIGKILL after the grace."""
        grace = STOP_GRACE_S if grace is None else float(grace)
        kid._expected = True
        done = getattr(kid, "_done", None)
        if done is None:                             # not watched: self_proc's own stop reaps it
            SP.stop(kid, grace)
            return
        kid.close_stdin()
        if done.is_set():
            return
        try:
            os.kill(kid.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if not done.wait(grace):
            try:
                os.kill(kid.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            done.wait(STOP_GRACE_S)

    def close(self) -> None:
        """Stop the child and the supervisor (the seat's teardown)."""
        with self._lock:
            self._closing = True
            kid, spawning = self._kid, self._spawning
        self._kick()
        if spawning is not None and kid is None:
            try:
                os.kill(spawning.pid, signal.SIGTERM)    # its handshake then fails and spawn reaps it
            except ProcessLookupError:
                pass
        if kid is not None:
            self._stop_kid(kid)
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(STOP_GRACE_S)
        with _reg_lock:
            if self.srv is not None and _SUPS.get(id(self.srv)) is self:
                _SUPS.pop(id(self.srv), None)

    # ---- the seat's side of self/ while no child runs ---------------------------------------

    def _fresh_seat(self) -> None:
        """The seat's view of self/, read afresh after a child wrote there (lock held, no process)."""
        if not self._stale_seat or self.srv is None:
            return
        SL = _SL()
        rt = SL._rt(self.srv)
        for k in list(rt):
            if k not in ("wake", "srv"):
                rt.pop(k, None)
        rt.update(mode="off")
        SL.load_state(self.srv)
        SL._read(self.srv)
        self._stale_seat = False

    def _here(self, path: str, q: dict):
        """A view computed by the seat itself (no process: it is self/'s only writer)."""
        SL = _SL()
        from . import self_sheet as _SHEET
        if path == "/card":
            return _SHEET.card(self.srv)
        if path == "/sheet":
            return _SHEET.sheet(self.srv)
        if path == "/recent":
            return {"lines": SL.recent(self.srv, q.get("n", 30))}
        return SL.status(self.srv)

    # ---- forwarding -----------------------------------------------------------------------

    def _http(self, port: int, path: str, body=None):
        from . import jwtauth as J
        h = {"Content-Type": "application/json", **J.client_headers()}
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=h,
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=FORWARD_TIMEOUT_S) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"ok": False, "error": f"HTTP {e.code}"}

    def _bare_card(self) -> dict:
        from . import self_name as _SN
        return {"name": _SN.current(), "level": 0, "rung_name": "", "next": [], "mode": self.mode,
                "awake": False, "last_activity": None, "generated": time.time(), "energy": None,
                "asleep": False, "pull": None, "limiting": None, "record_cap": None}

    def view(self, path: str, **q) -> dict:
        """GET a view (/card, /sheet, /recent, /status): the child's, else the
        last good one, else (no process) the seat's own. The card and the
        status carry `proc` (diag(), and `stale` when it is an old answer)."""
        if path not in VIEWS:
            raise ValueError(f"not a view: {path}")
        doc, stale = None, False
        with self._lock:
            port, busy = self._port, self._process()
        if port is not None:
            qs = ("?" + urllib.parse.urlencode(q)) if q else ""
            try:
                code, got = self._http(port, path + qs)
                if code == 200 and isinstance(got, dict):
                    doc = got
                    self._last[path] = got
            except Exception:                        # noqa: BLE001 -- slow or gone: the last good one
                pass
            if doc is None:
                doc, stale = self._last.get(path), True
        elif not busy:
            with self._lock:
                if not self._process():
                    try:
                        self._fresh_seat()
                        doc = self._here(path, q)
                    except Exception as e:           # noqa: BLE001
                        print(f"[self-super] {path} in the seat failed ({e!r})", file=sys.stderr)
        else:
            doc, stale = self._last.get(path), True
        if doc is None:
            doc = self._bare_card() if path == "/card" else (
                {"lines": []} if path == "/recent" else {"mode": self.mode} if path == "/status" else {})
            stale = True
        if path in ("/card", "/status"):
            doc = dict(doc)
            doc["proc"] = {**self.diag(), "stale": stale}
        return doc

    def status(self) -> dict:
        return self.view("/status")

    def _sync_mode(self, body: dict, out: dict) -> None:
        SL = _SL()
        ap = (out or {}).get("applied") or {}
        before = self.mode
        if "mode" in ap:
            self.mode = str(ap["mode"])
        if self.mode in ("on", "watch") and (before == "off" or "mode" in ap):
            self._crashes.clear()                    # the person switched it on: a hold ends, afresh
            self._next_at = 0.0
        if self.srv is not None:
            SL._rt(self.srv)["mode"] = self.mode

    def control(self, body: dict) -> tuple[int, dict]:
        """The self controls (mode, accept, name, by): the child's when
        it runs; the seat's own when no process exists; refused while one starts."""
        body = dict(body or {})
        with self._lock:
            port, busy = self._port, self._process()
            if port is None and not busy:
                self._fresh_seat()
                out, code = SP._control(self.srv, body)
                if code == 200:
                    self._sync_mode(body, out)
        if port is None and busy:
            return 503, {"ok": False, "error": "Blurp's process is starting"}
        if port is not None:
            try:
                code, out = self._http(port, "/control", body)
            except Exception as e:                   # noqa: BLE001
                return 504, {"ok": False, "error": f"Blurp's process did not answer ({e!r})"[:200]}
            if code == 200:
                with self._lock:
                    self._sync_mode(body, out)
        self._kick()
        return code, out

    def chat_in(self, person: str) -> bool:
        """A person spoke: to the child (on its own thread). -> handled (the
        seat writes nothing); False when no process exists (the seat's own)."""
        with self._lock:
            port, busy = self._port, self._process()
        if port is None:
            return busy

        def send():
            try:
                self._http(port, "/chat-in", {"person": str(person or "")})
            except Exception as e:                   # noqa: BLE001
                print(f"[self-super] chat-in not delivered ({e!r})", file=sys.stderr)
        threading.Thread(target=send, name="self-super-chat-in", daemon=True).start()
        return True
