"""THE STOP-SIGNAL POLICY (issue #35, the boot nobody could stop):
`--no-launch`'s dest defaults launch TRUE, so plain `membrane
standalone` / supervised / nohup'd boots ran launch-mode signal policy
with no opencode child -- SIGINT ignored forever (an inherited SIG_IGN
never restored), a ctrl+C both kept serving. THE CONTRACT: only a REAL
TUI launch ignores ctrl+c; every childless serve mode stops on INT.
The teardown drain is leased (issue #35's second half)."""
from membrane import opencode


def test_standalone_stops_on_int_even_at_launch_default():
    # THE EXHIBIT: `membrane standalone` passes NO --launch/--no-launch
    # launch flag -> launch defaults True, standalone True -> the seat
    # must STOP on ctrl+c, never inherit the TUI covenant:
    assert opencode._stop_signal_policy(True, True) == "stop"


def test_a_real_tui_launch_keeps_the_tui_covenant():
    assert opencode._stop_signal_policy(True, False) == "ignore"


def test_every_childless_mode_stops():
    assert opencode._stop_signal_policy(False, False) == "stop"
    assert opencode._stop_signal_policy(False, True) == "stop"


def test_the_teardown_drain_is_leased():
    """THE 5-MINUTE DRAIN EXHIBIT: the graceful close on a big field
    lingered minutes; the lease bound exists and is finite (so the
    interpreter is NEVER cut mid-boot, only mid-drain)."""
    lease = opencode.TEARDOWN_LEASE_S
    assert isinstance(lease, (int, float)) and lease > 0
    assert lease < 600