"""Issue #104: the STT mis-hears the wake word ('Hey Memo' heard as 'Hey
mama') and voice_io.heard stored the raw transcript verbatim, so the audit
log showed the wrong name on every utterance. heard now keeps the configured
hotword canonical and rides the heard form as an annotation."""

import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as S  # noqa: E402
from membrane import voice_io as VIO  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _heard(text):
    srv = types.SimpleNamespace(entity_store=EntityStore(path=None, shared=False))
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                     lambda b: {"choices": [{"message": {"content": "ok"}}]},
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    srv._loop_runner = r
    VIO.heard(srv, "Dmitrii Chechetkin", text, sid="s1")
    return VIO.read(srv, "Dmitrii Chechetkin")["in"]["log"][-1]["text"]


def test_wake_word_mishearing_is_normalized():
    with mock.patch("membrane.naming.current", return_value="Memo"):
        got = _heard("Hey mama. So when are the cycles?")
    assert got == "Hey Memo [heard: mama] So when are the cycles?"


def test_wake_word_correct_hearing_is_untouched():
    with mock.patch("membrane.naming.current", return_value="Memo"):
        got = _heard("Hey Memo. Is the camera up?")
    assert got == "Hey Memo. Is the camera up?"


def test_non_wake_speech_is_untouched():
    with mock.patch("membrane.naming.current", return_value="Memo"):
        got = _heard("is the camera up")
    assert got == "is the camera up"
