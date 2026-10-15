"""THE SEAT IS THE PROCESS (the ctrl+c covenant, 2026-10-15): `membrane
standalone`/`serve`/`opencode` exec INTO the seat (one process) instead of
spawning a subprocess child the CLI wrapper SIGKILLs on its own
KeyboardInterrupt -- a child cut mid-flush leaves a half-written field, a
stray, or a zombie. One process: a terminal's ctrl+c lands on the seat's own
SIGINT handler and the graceful teardown runs fully."""
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cli  # noqa: E402


def test_seat_commands_exec_not_spawn():
    class _Replaced(BaseException):           # execvpe never returns
        pass
    with mock.patch.object(cli.os, "execvpe", side_effect=_Replaced()) as ex, \
         mock.patch.object(cli.subprocess, "call") as call:
        try:
            cli._run_module("membrane.opencode", ["--standalone"], exec_=True)
        except _Replaced:
            pass
    ex.assert_called_once()
    call.assert_not_called()
    assert ex.call_args[0][0] == sys.executable
    assert ex.call_args[0][1][:3] == [sys.executable, "-m", "membrane.opencode"]


def test_short_commands_still_spawn_not_exec():
    with mock.patch.object(cli.os, "execvpe") as ex, \
         mock.patch.object(cli.subprocess, "call", return_value=0) as call:
        cli._run_module("membrane.ingest", ["--field", "x.json"])
    ex.assert_not_called()
    call.assert_called_once()
