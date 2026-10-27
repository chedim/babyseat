"""#408: two races that let a SIGTERM land where it must not.

1. The adapter tests stub `restarts.do_restart` with monkeypatch. If the stub
   is undone before the adapter worker stops, a restart write still queued at
   teardown runs the REAL do_restart and SIGTERMs pytest itself (CI exit 143).
   The `seat` fixture must take monkeypatch, so its undo runs after AD.stop().
2. self_proc must install its SIGTERM handler before the handshake: the parent
   may stop the child the moment it reads the handshake."""

import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_adapter_seat_fixture_outlives_its_stubs():
    import tests.test_adapters_576 as T
    fn = getattr(T.seat, "__wrapped__", None) or T.seat.__pytest_wrapped__.obj
    assert "monkeypatch" in inspect.signature(fn).parameters


def test_self_proc_handles_sigterm_before_the_handshake():
    src = (ROOT / "membrane" / "self_proc.py").read_text()
    main = src[src.index("def main("):]
    assert main.index("signal.signal(signal.SIGTERM") < main.index("HANDSHAKE")
