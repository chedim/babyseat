"""THE OPERATOR'S OWN CHAT GETS THE LANGUAGE NOTES (2026-11-02: stage 3 never fired for the
operator). The turn hook skipped the compiled-program and gap notes for any chat with an
owner, meant for a guest's (a token person's, the HA relay's). Since #797 the operator's own
chat has an owner too, their sub, so it got none. A chat is the operator's when its owner is
MEMBRANE_OPERATOR (the sub #797 already reads) or the tokenless `owner`; any other owner is a
guest and still gets no program. MEMBRANE_OPERATOR's on/off values (0/1) are never a sub.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_living_language import HAVE, SID, _world  # noqa: E402


class _Sessions:
    def __init__(self, owner):
        self.owner = owner

    def get(self, sid):
        return {"id": sid, "owner": self.owner}


def _ctx(srv, said, owner):
    from membrane import lanes as L
    srv._loop_runner = type("R", (), {"sessions": _Sessions(owner)})()
    srv._cg_turn = {SID: {}}
    srv._cg_text = {SID: said}
    return L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class OperatorChat(unittest.TestCase):
    def test_the_operators_own_chat_gets_the_gap_note(self):
        srv, *_ = _world()
        with mock.patch.dict(os.environ, {"MEMBRANE_OPERATOR": "alice", "MEMBRANE_TEACH_HINT": "1"}):   # #1052: off by default
            self.assertIn("doesn't know", _ctx(srv, "shut goal one", "alice"))

    def test_the_tokenless_owner_is_the_operator(self):
        srv, *_ = _world()
        with mock.patch.dict(os.environ, {"MEMBRANE_TEACH_HINT": "1"}, clear=False):           # #1052: off by default
            os.environ.pop("MEMBRANE_OPERATOR", None)
            self.assertIn("doesn't know", _ctx(srv, "shut goal one", "owner"))

    def test_a_guests_chat_still_gets_no_program(self):
        srv, *_ = _world()
        with mock.patch.dict(os.environ, {"MEMBRANE_OPERATOR": "alice"}):
            self.assertEqual(_ctx(srv, "shut goal one", "bob"), "BANNER")

    def test_the_on_off_switch_is_never_a_sub(self):
        srv, *_ = _world()
        with mock.patch.dict(os.environ, {"MEMBRANE_OPERATOR": "1"}):
            self.assertEqual(_ctx(srv, "shut goal one", "1"), "BANNER")


if __name__ == "__main__":
    unittest.main()
