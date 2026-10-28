"""BLURP IN ITS OWN PROCESS (#440 part 3); the seat starts it by default since part 5 (MEMBRANE_SELF_PROC=0 keeps the loop in the seat).

Blurp's learner is pure Python over its record; inside the seat it holds
the GIL the /v1 proxy needs. This module is the child that runs them in a
process of its own (its own GIL, its own CPU, a lower priority):

    MEMBRANE_SELF_PROC=1 python -m membrane.self_proc --seat URL --data-dir DIR

- It runs the same self_loop code against a FACADE of the seat: an
  srv-shaped object (FACADE_ATTRS, nothing else) whose entity store is a
  local mirror of Blurp's own entities (itself and anything its own, seeded from
  the context's `own` part), whose reads of the seat are
  GET /membrane/self/context (a POST when a step carries a learn report, #586)
  and whose acts are POST /membrane/self/effects
  (self_api: a facade carries `self_remote`, and both calls go over HTTP).
  Blurp's own properties go back as `own` effects (flush, after each step). The pause and the CPU share are read from the context.
- It owns self/ (the record and Blurp's own files) through self_writer.
- It serves Blurp's views and controls on a loopback port it picks and
  reports on stdout at start (one JSON line: {"self_proc": {"port", "pid"}}):
  GET /card, /sheet, /recent, /status; POST /control (mode, accept,
  name) and /chat-in (a person spoke: energy.person_spoke). The seat's own
  token only (or open mode), through the seat's own door (jwtauth.gate).
- It exits on SIGTERM, and when its stdin closes (macOS has no
  parent-death signal: a seat killed outright closes the pipe).

The seat starts it through part 4's supervisor (membrane.self_super: spawn /
stop below, with posix_spawn -- no fork of the threaded seat). The flag gates the entry point itself, so nothing runs a second
loop over self/ by accident.
"""
from __future__ import annotations

import argparse
import json
import os
import select
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FLAG = "MEMBRANE_SELF_PROC"
NICE = 10               # #440 constant 1, approved: the child's niceness (the OS favours the seat)
STOP_GRACE_S = 10.0     # #440 constant 4, approved: SIGTERM to SIGKILL
TIMEOUT_S = 5.0         # #440 constant 5, approved: the forwarding timeout -- here, each hop to the seat
HANDSHAKE = "self_proc"

FACADE_ATTRS = ("entity_store", "_models_path", "self_remote", "paused", "efield", "events_pool", "membrane")


class SeatError(RuntimeError):
    """The seat answered the child with something it cannot use."""


class SpawnError(RuntimeError):
    """The child did not come up (no handshake in time, or it exited)."""


def _SL():
    from . import self_loop as SL
    return SL


# ---- the child's way to the seat ----------------------------------------------------------

class SeatClient:
    """GET /membrane/self/context (POST when it carries a report) and POST /membrane/self/effects on the seat,
    with the seat's own token (jwtauth.client_headers: MEMBRANE_JWT_TOKEN, or
    one minted from the inherited secret; nothing in open mode)."""

    def __init__(self, url: str):
        self.url = str(url).rstrip("/")
        self.last: dict | None = None            # the last whole context (the pause, the share)

    def _call(self, path: str, body=None) -> dict:
        from . import jwtauth as J
        h = {"Content-Type": "application/json", **J.client_headers()}
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers=h,
                                     method="GET" if body is None else "POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            out = json.loads(r.read() or b"{}")
        if not isinstance(out, dict):
            raise SeatError(f"{path}: not a JSON object")
        return out

    def context(self, only: str | None = None, own: bool = False, report: dict | None = None) -> dict:
        if report:                       # #576 D2 + #586: the learn exchange spends the purse -> POST
            doc = self._call("/membrane/self/context",
                             {"report": dict(report), "only": only, "own": bool(own)})
        else:
            q = []
            if own:
                q.append(("own", "1"))
            if only:
                q.append(("only", str(only)))
            doc = self._call("/membrane/self/context" + ("?" + urllib.parse.urlencode(q) if q else ""))
        if only is None:
            self.last = doc
        return doc

    def effects(self, effects: list) -> list[dict]:
        from .self_api import plain
        res = self._call("/membrane/self/effects", {"effects": plain(effects)}).get("results")
        if not isinstance(res, list) or len(res) != len(effects):
            raise SeatError("effects: no result for each")
        return res

    def nerve_paused(self) -> bool:
        """The seat's pause, asked now (the loop's start check)."""
        return bool(self.context().get("nerve_paused"))

    def cpu_share(self) -> float:
        """The seat's CPU share for Blurp, from the latest context."""
        v = (self.last if self.last is not None else self.context()).get("cpu_share")
        if v is None:
            from . import options as O
            return O.CPU_SHARE_DEFAULT
        return float(v)


# ---- the facade -----------------------------------------------------------------------------

class Facade:
    """The seat as Blurp's own process holds it: only FACADE_ATTRS. Its store
    is a RAM mirror of Blurp's own entities; the field, the pools and the
    membrane are the seat's (None here)."""

    __slots__ = ("entity_store", "_models_path", "self_remote", "efield", "events_pool", "membrane",
                 "_sent", "__weakref__")

    def __init__(self, client: SeatClient, data_dir):
        from .entities import EntityStore
        est = EntityStore(path=None, shared=False)
        est.docs = {}                            # nothing but what the seat says is Blurp's
        est._srv = self
        self.entity_store = est
        self._models_path = str(Path(data_dir) / "models.json")   # self_writer.dir_of: <data dir>/self
        self.self_remote = client
        self.efield = None
        self.events_pool = None
        self.membrane = None
        self._sent: dict = {}

    @property
    def paused(self) -> bool:
        return bool((self.self_remote.last or {}).get("paused"))

    def seed(self) -> None:
        """The mirror, from the seat's own part of the context."""
        from .entities import QuantumEntity
        own = self.self_remote.context(own=True, only="own").get("own") or {}
        docs = {}
        for eid, d in own.items():
            ent = QuantumEntity(eid=eid, cls=d.get("cls") or "Component", properties=dict(d.get("properties") or {}))
            if d.get("position") is not None:
                ent.position = list(d["position"])
            docs[eid] = ent
        self.entity_store.docs = docs
        self._sent = {eid: _props(e) for eid, e in docs.items()}

    def flush(self) -> list[dict]:
        """Blurp's own changes since the last flush (its own properties) to the seat, as `own` effects: only the keys that
        changed (script_data key by key). -> the effects' results."""
        from .self_api import _is_own
        est = self.entity_store
        effs, cur_of = [], {}
        for eid, ent in list(est.docs.items()):
            if not _is_own(est, ent):
                continue
            cur, old = _props(ent), self._sent.get(eid) or {}
            ch = {k: v for k, v in cur.items() if old.get(k) != v}
            if isinstance(ch.get("script_data"), dict) and isinstance(old.get("script_data"), dict):
                ch["script_data"] = {k: v for k, v in ch["script_data"].items() if old["script_data"].get(k) != v}
            if ch:
                effs.append({"kind": "own", "eid": eid, "properties": ch})
                cur_of[eid] = cur
        if not effs:
            return []
        res = self.self_remote.effects(effs)
        for e, r in zip(effs, res):
            if r.get("ok"):
                self._sent[e["eid"]] = cur_of[e["eid"]]
        return res


def _props(ent) -> dict:
    from .self_api import plain
    return plain(ent.properties or {})


def bind(fac: Facade) -> None:
    """The child's boot: the mirror from the seat, Blurp's state from self/.
    (The seat's own boot makes Blurp's entity and runs the migrations.)"""
    fac.seed()
    _SL().load_state(fac)


def step(fac: Facade, now: float | None = None) -> dict | None:
    """One turn of the child's loop: self_loop.step, then Blurp's own changes to the seat."""
    try:
        return _SL().step(fac, now)
    finally:
        fac.flush()


# ---- the control server ---------------------------------------------------------------------

def _handler(fac: Facade):
    SL = _SL()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):          # quiet: the seat's log is the record of requests
            pass

        def _out(self, obj, code: int = 200) -> None:
            from .self_api import plain
            b = json.dumps(plain(obj)).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _door(self) -> bool:
            from . import jwtauth as J
            if not J.gate(self):
                return False
            ident = getattr(self, "_identity", None)
            if ident is not None and not ident.internal:
                self._out({"error": "for the seat's own token"}, 403)
                return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                b = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                b = None
            return b if isinstance(b, dict) else {}

        def do_GET(self):
            if not self._door():
                return
            path, _, qs = self.path.partition("?")
            q = urllib.parse.parse_qs(qs)
            from . import self_sheet as _SHEET
            if path == "/card":
                return self._out(_SHEET.card(fac))
            if path == "/sheet":
                return self._out(_SHEET.sheet(fac))
            if path == "/recent":
                return self._out({"lines": SL.recent(fac, (q.get("n") or [30])[0])})
            if path == "/status":
                return self._out(SL.status(fac))
            return self._out({"error": "not found"}, 404)

        def do_POST(self):
            if not self._door():
                return
            path = self.path.partition("?")[0]
            b = self._body()
            if path == "/control":
                return self._out(*_control(fac, b))
            if path == "/chat-in":
                from . import energy as _E
                _E.person_spoke(fac, str(b.get("person") or ""))
                return self._out({"ok": True})
            return self._out({"error": "not found"}, 404)
    return H


def _control(fac: Facade, b: dict) -> tuple[dict, int]:
    """The panel's self controls, run here: mode, accept, name."""
    from . import self_writer as _W
    by = str(b.get("by") or "operator")
    done, bad = {}, []
    if "mode" in b:
        m = str(b["mode"] or "")
        (done.__setitem__("mode", m) if _W.set_mode(fac, m, by=by) else bad.append("mode"))
    if "accept" in b:
        done["accept"] = _W.accept(fac, b["accept"], by=by)
    if "name" in b:
        from . import self_name as _SN
        try:
            done["name"] = _SN.set_name(b["name"])
        except ValueError:
            bad.append("name")
    if bad and not done:
        return {"ok": False, "refused": bad}, 400
    _SL()._rt(fac)["wake"].set()
    return {"ok": True, "applied": done, "refused": bad}, 200


def serve(fac: Facade) -> ThreadingHTTPServer:
    """The control server on a loopback port the OS picks; serving on its own thread."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fac))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="self-proc-http", daemon=True).start()
    return httpd


# ---- the child's main ------------------------------------------------------------------------

def run(fac: Facade, stop: threading.Event) -> None:
    """self_loop.start's loop, on this thread, until `stop`: wake on its own
    change (or a control), one step, then the pace's wait after a step with a line."""
    SL = _SL()
    rt = SL._rt(fac)
    while not stop.is_set():
        rt["wake"].wait(timeout=SL.PACE_S)
        rt["wake"].clear()
        if stop.is_set():
            break
        try:
            rec = step(fac)
            if rec is not None:
                rt["wake"].clear()
                wait = SL.pace_wait(fac, rec.get("dur_s") or 0.0)
                if wait is not None:
                    rt["wake"].wait(timeout=wait)
                    rt["wake"].set()
        except Exception as e:                      # noqa: BLE001 -- the seat away, a bad answer: next turn
            print(f"[self-proc] step failed ({e!r})", file=sys.stderr)


def _watch_stdin(stop: threading.Event, wake) -> None:
    """The seat holds our stdin: when it closes (the seat gone), stop."""
    try:
        while os.read(0, 4096):
            pass
    except OSError:
        pass
    stop.set()
    wake.set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m membrane.self_proc")
    ap.add_argument("--seat", required=True, help="the seat's base URL (loopback)")
    ap.add_argument("--data-dir", required=True, help="the seat's data dir (Blurp's self/ is under it)")
    a = ap.parse_args(argv)
    if os.environ.get(FLAG) != "1":
        print(f"[self-proc] {FLAG}=1 is not set: Blurp's loop stays in the seat", file=sys.stderr)
        return 2
    from .safestderr import install_safe_stdio
    try:
        os.nice(NICE)
    except OSError:
        pass
    fac = Facade(SeatClient(a.seat), a.data_dir)
    bind(fac)
    httpd = serve(fac)
    stop = threading.Event()
    rt = _SL()._rt(fac)

    def on_term(*_):
        stop.set()
        rt["wake"].set()
    signal.signal(signal.SIGTERM, on_term)          # before the handshake: the parent may stop us at once (#408)
    os.write(1, (json.dumps({HANDSHAKE: {"port": httpd.server_address[1], "pid": os.getpid()}}) + "\n").encode())
    os.dup2(2, 1)                                   # nothing else goes to the handshake pipe
    install_safe_stdio()
    threading.Thread(target=_watch_stdin, args=(stop, rt["wake"]), name="self-proc-stdin", daemon=True).start()
    try:
        run(fac, stop)
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


# ---- the seat's side: start and stop a child (part 4's supervisor uses these) -----------------

class Child:
    """A running child: its pid, its port, the write end of its stdin."""

    def __init__(self, pid: int, stdin_fd: int):
        self.pid, self.port, self._stdin = pid, 0, stdin_fd
        self.returncode: int | None = None

    def close_stdin(self) -> None:
        if self._stdin is not None:
            try:
                os.close(self._stdin)
            except OSError:
                pass
            self._stdin = None

    def exited(self) -> bool:
        if self.returncode is not None:
            return True
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self.returncode = -1
            return True
        if pid == 0:
            return False
        self.returncode = os.waitstatus_to_exitcode(status)
        return True


def spawn(seat_url: str, data_dir, env: dict | None = None, log_path=None, wait_s: float = 0.0,
          on_spawn=None) -> Child:
    """Start the child with posix_spawn (no fork of a threaded seat): its
    stdin a pipe the caller holds, its stdout the handshake pipe, its stderr
    `log_path` (else nowhere). Waits up to wait_s for the handshake. ->
    the Child, its port set. SpawnError (the child stopped) when none came.
    `on_spawn(child)` is told of the process before its handshake (so a
    supervisor closing meanwhile can stop it)."""
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    log_fd = os.open(str(log_path) if log_path else os.devnull,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
    argv = [sys.executable, "-m", "membrane.self_proc", "--seat", str(seat_url), "--data-dir", str(data_dir)]
    try:
        child_env = dict(os.environ, **{FLAG: "1"}) if env is None else dict(env)   # the seat's own env: the child's flag set
        pid = os.posix_spawn(sys.executable, argv, child_env,
                             file_actions=[(os.POSIX_SPAWN_DUP2, in_r, 0), (os.POSIX_SPAWN_DUP2, out_w, 1),
                                           (os.POSIX_SPAWN_DUP2, log_fd, 2)])
    except Exception:
        for fd in (in_r, in_w, out_r, out_w, log_fd):
            os.close(fd)
        raise
    for fd in (in_r, out_w, log_fd):
        os.close(fd)
    kid = Child(pid, in_w)
    try:
        if on_spawn is not None:
            on_spawn(kid)
        kid.port = _handshake(out_r, kid, float(wait_s))
    except BaseException:
        stop(kid)
        raise
    finally:
        os.close(out_r)
    return kid


def _handshake(fd: int, kid: Child, wait_s: float) -> int:
    buf = b""
    end = time.monotonic() + wait_s
    while b"\n" not in buf:
        left = end - time.monotonic()
        if left <= 0:
            raise SpawnError("no handshake in time")
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            continue
        chunk = os.read(fd, 4096)
        if not chunk:
            raise SpawnError(f"the child exited before its handshake ({kid.exited() and kid.returncode})")
        buf += chunk
    try:
        doc = json.loads(buf.split(b"\n", 1)[0])
        return int(doc[HANDSHAKE]["port"])
    except (ValueError, KeyError, TypeError) as e:
        raise SpawnError(f"a bad handshake ({e!r})") from None


def stop(kid: Child, grace: float = STOP_GRACE_S) -> None:
    """SIGTERM (and its stdin closed), then SIGKILL after `grace`; reaped either way."""
    kid.close_stdin()
    if kid.exited():
        return
    try:
        os.kill(kid.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    end = time.monotonic() + grace
    while time.monotonic() < end:
        if kid.exited():
            return
        time.sleep(0.05)
    try:
        os.kill(kid.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        _, status = os.waitpid(kid.pid, 0)
        kid.returncode = os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        kid.returncode = kid.returncode if kid.returncode is not None else -1


if __name__ == "__main__":
    sys.exit(main())
