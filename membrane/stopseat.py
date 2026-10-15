"""THE STOP COMMAND (2026-10-14, the seat that would not quit): the
tty's ctrl+c has been swallowed on two live boots (the wedge's sample
showed the main thread asleep in nanosleep, the INT never interrupting;
TERM was swallowed too on the same seat) -- stopping the seat must
never DEPEND on tty signal routing or on where the keystroke lands.

    membrane stop [--port N]

Finds the seat's pids honestly: the serve port's LISTEN pid (lsof, the
ports the seat actually serves on) and the seat's own pidfile lane; then
runs the ladder the operator's hand was reaching for -- SIGINT, wait a
real bound, SIGTERM, wait, SIGKILL -- and prints a receipt per pid
(stopped / killed / refused, with the how).

THE MARKER: discovery never proposes a pid whose command line does not
look like a membrane seat (`/bin/membrane`, `-m membrane.opencode`,
`standalone`) -- a stop that kills arbitrary listeners is a loaded gun,
so a foreign listener on the same port is REFUSED with the why, never
touched (the operator kills it themselves, on purpose)."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

MARKER = re.compile(r"(bin/membrane(?:\s|$)|membrane\.opencode|standalone)")
DEFAULT_PORTS = (4096, 11500)


def _cmdline(pid: int) -> str:
    """The full command line. /proc/<pid>/cmdline is the truth on Linux
    (procps truncates the ps command column by width -- a long -c buffer
    leaves the final --standalone token off the end and the marker gate
    then refuses the seat it must stop, red on CI); ps -ww (no width
    cap) covers platforms without /proc."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        raw = b""
    if raw:
        argvs = raw.decode(errors="replace").replace("\x00", " ").strip()
        if argvs:
            return argvs
    out = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="],
                         capture_output=True, text=True)
    return (out.stdout or "").strip()


def _looks_like_seat(pid: int) -> bool:
    m = _cmdline(pid)
    return bool(m) and bool(MARKER.search(m))


def _listen_pid(port: int) -> int:
    """The pid that LISTENS on this port (lsof), or 0."""
    out = subprocess.run(["lsof", "-nP", "-iTCP:%d" % int(port),
                          "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True)
    pids = [x for x in (out.stdout or "").split() if x.isdigit()]
    return int(pids[0]) if pids else 0


def _candidates(ports=(), pidfiles=(), extra_pids=()) -> list[tuple[int, str]]:
    """[(pid, how)] of every pid the lanes surface, WITH the how; no
    marker here -- the gate is stop_seat's decision, tests can see both
    halves."""
    got: dict[int, str] = {}
    for port in ports:
        pid = _listen_pid(int(port))
        if pid:
            got.setdefault(pid, f"port :{port}")
    for pf in pidfiles:
        try:
            pid = int(Path(pf).read_text().strip())
        except (OSError, ValueError):
            continue
        if pid > 0:
            got.setdefault(pid, f"pidfile {pf}")
    for pid in extra_pids:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            got.setdefault(pid, "given")
    return sorted(got.items(), key=lambda kv: -kv[0])


def seat_pids(ports=(), pidfiles=(), extra_pids=()) -> list[tuple[int, str]]:
    """Discovery as the seat needs it: candidates, marker-checked."""
    return [(pid, why) for pid, why in _candidates(ports, pidfiles, extra_pids)
            if _looks_like_seat(pid)]


def _alive(pid: int) -> bool:
    """A zombie (dead but unreaped -- e.g. the CALLER's own child, the
    launcher's subprocess) still answers os.kill(pid, 0); its ps state
    says Z. Both count as dead."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    st = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                        capture_output=True, text=True)
    s = (st.stdout or "").strip()
    return bool(s) and not s.startswith("Z")


def stop_pids(pids, int_s: float = 25.0, term_s: float = 15.0) -> dict:
    """THE LADDER on the given pids: SIGINT, wait int_s; SIGTERM, wait
    term_s; SIGKILL the survivors of both. The real seat's handlers raise
    KeyboardInterrupt, so a healthy seat exits on the INT inside its
    graceful teardown; a hardened process falls to the next rung. Returns
    receipts {stopped, killed, how, rc}."""
    pids = [int(p) for p in pids if p]
    how: dict[int, str] = {}
    stopped, killed = [], []
    live = list(dict.fromkeys(pids))
    gone = [p for p in live if not _alive(p)]
    live = [p for p in live if _alive(p)]
    for p in gone:
        how[p] = "already-gone"
    for sig, wait, tag in ((signal.SIGINT, int_s, "int"),
                           (signal.SIGTERM, term_s, "term")):
        for p in live:
            try:
                os.kill(p, sig)
            except OSError:
                continue
        end = time.monotonic() + wait
        while time.monotonic() < end and any(_alive(p) for p in live):
            time.sleep(0.05)
        survivors = [p for p in live if _alive(p)]
        for p in live:
            if p not in survivors:
                stopped.append(p)
                how[p] = tag
        live = survivors
        if not live:
            break
    for p in list(live):
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            continue
        end = time.monotonic() + 5.0
        while time.monotonic() < end and _alive(p):
            time.sleep(0.05)
        if not _alive(p):
            killed.append(p)
            how[p] = "kill"
    return {"stopped": stopped, "killed": killed, "how": how, "rc": 0}


def stop_seat(ports=(), pidfiles=(), extra_pids=(),
              int_s: float = 25.0, term_s: float = 15.0) -> dict:
    """Discovery + the marker gate + the ladder. A candidate that is NOT
    membrane-shaped is refused (named, untouched -- the operator kills
    it themselves). rc: 0 something stopped/killed, 1 nothing found,
    2 foreign processes refused (still nothing of ours)."""
    cands = _candidates(ports, pidfiles, extra_pids)
    ours = [(pid, why) for pid, why in cands if _looks_like_seat(pid)]
    refused = [(pid, why) for pid, why in cands if pid not in {p for p, _ in ours}]
    r = stop_pids([p for p, _ in ours], int_s=int_s, term_s=term_s)
    how = {pid: f"found via {why}" for pid, why in ours}
    how.update({k: v for k, v in r["how"].items()})
    rc = (0 if (r["stopped"] or r["killed"]) else
          (2 if refused and not ours else 1))
    return {"stopped": r["stopped"], "killed": r["killed"],
            "refused": refused, "how": how, "rc": rc}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="membrane stop",
        description="Stop the membrane seat without depending on the "
                    "tty's ctrl+c (SIGINT -> bound SIGTERM -> SIGKILL, on "
                    "the serve port's listener and the pidfile's pid).")
    ap.add_argument("--port", type=int, action="append", default=[],
                    help="a serve port to look on (repeatable; "
                         "default :4096 and :11500)")
    ap.add_argument("--pid", type=int, action="append", default=[],
                    help="a pid to stop IF it is membrane-shaped")
    ap.add_argument("--int-wait", type=float, default=25.0,
                    help="the SIGINT bound (s)")
    ap.add_argument("--term-wait", type=float, default=15.0,
                    help="the SIGTERM bound (s)")
    a = ap.parse_args(argv or [])
    ports = a.port or list(DEFAULT_PORTS)
    pf = Path(".membrane/opencode.pid")
    out = stop_seat(ports=ports, pidfiles=[pf] if pf.exists() else [],
                    extra_pids=a.pid, int_s=a.int_wait, term_s=a.term_wait)
    for pid in out["stopped"]:
        print(f"[stop] {pid}: stopped on {out['how'].get(pid, '?')} "
              f"({_cmdline(pid)[:90]})")
    for pid in out["killed"]:
        print(f"[stop] {pid}: had to be KILLED ({_cmdline(pid)[:90]})")
    for pid, why in out["refused"]:
        print(f"[stop] refused {pid} ({why}): not a membrane seat -- "
              f"'{_cmdline(pid)[:90]}'", file=sys.stderr)
    if not out["stopped"] and not out["killed"]:
        print("[stop] nothing membrane-shaped on "
              + ", ".join(f":{p}" for p in ports))
    return out["rc"]