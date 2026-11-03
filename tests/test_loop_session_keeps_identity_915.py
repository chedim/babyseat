"""#915: a turn posted as 'build' onto a planner:/worker: loop session (live: planner ses_c3a7,
goal tag ses_e257) must not turn it into a person chat -- else latest_chat picks it and
goal-ended lands there."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane.server import TurnRunner  # noqa: E402


class _Sessions:
    def __init__(self):
        self.sessions = {"ses_c3a7": {"id": "ses_c3a7", "agent": "planner: goal", "title": "planner: goal"},
                         "ses_e257": {"id": "ses_e257", "agent": "build", "title": "chat"}}


class _R:
    _turn_agent = TurnRunner._turn_agent

    def __init__(self):
        self.sessions = _Sessions()
        self._agents = {"ses_c3a7": "planner: goal", "ses_e257": "build"}


def test_build_turn_on_planner_session_stays_loop():
    r = _R()
    agent = r._turn_agent("ses_c3a7", {"agent": "build"})     # the worker's post carries build
    r._agents["ses_c3a7"] = agent                             # as _quota_hold / the turn does
    assert CIO._is_loop(r, "ses_c3a7"), agent
    assert not CIO._is_loop(r, "ses_e257")


def test_person_turn_still_build():
    r = _R()
    assert r._turn_agent("ses_e257", {"agent": "build"}) == "build"
