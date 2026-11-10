"""The operator, 2026-11-10: membrane's own 🧩 answer (it answers a read-only message itself before the model) is
off by default for users; MEMBRANE_ANSWER=1 turns it on."""
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import language as LL  # noqa: E402


def test_off_unless_switched_on():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MEMBRANE_ANSWER", None)
        assert LL.answer_on() is False
    with mock.patch.dict(os.environ, {"MEMBRANE_ANSWER": "1"}):
        assert LL.answer_on() is True
    with mock.patch.dict(os.environ, {"MEMBRANE_ANSWER": "0"}):
        assert LL.answer_on() is False
