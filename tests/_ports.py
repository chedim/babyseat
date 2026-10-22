"""Ports for tests that bind, release and re-probe (#408).

A port from bind(0) is an EPHEMERAL port, and the OS hands those out in
sequence to every outgoing connection on the box: a port a test just
released -- or the one right above the port it holds -- is the next one a
parallel test's (or the runner's) connection gets, between the test's pick
and its check. These ports come from below every ephemeral range (Linux
starts at 32768, macOS and Windows at 49152), where nothing is handed out.
"""
import random
import socket

LOW, HIGH = 20000, 30000


def _free(port: int) -> bool:
    for host in ("127.0.0.1", "0.0.0.0"):
        s = socket.socket()
        try:
            s.bind((host, port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def quiet_port(span: int = 1, avoid=()) -> int:
    """A port p, with p .. p+span-1 all free now, outside the ephemeral range."""
    rng = random.Random()
    for _ in range(500):
        p = rng.randrange(LOW, HIGH - span)
        if not any(q in avoid for q in range(p, p + span)) and all(_free(p + i) for i in range(span)):
            return p
    raise RuntimeError(f"no {span} free port(s) in {LOW}-{HIGH}")
