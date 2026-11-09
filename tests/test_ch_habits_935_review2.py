"""#935 review round 2, red first: generalized write paths are not reversible; the line has no default."""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch_learn as L  # noqa: E402
from tests.test_ch_habits_935 import LINE, _proven, world, _commands_world  # noqa: E402,F401


class T:
    def __init__(self, path):
        self.properties = {"write": [path, 1]}


@pytest.mark.parametrize("path", ["?y.say", "?y.restart", "?x.say", "?x.restart", "?y.invoke"])
def test_generalized_acting_paths_not_reversible(path):
    assert L.reversible(T(path)) is False


def test_plain_generalized_field_write_still_reversible():
    assert L.reversible(T("?y.b")) is True


def test_acting_generalized_candidate_cannot_graduate(world):
    est, vm = world
    c = _proven(est, vm)
    c.properties["write"] = ["?y.say", 1]
    assert vm.learner.graduate(c.eid, threshold=LINE) is False
    assert vm.learner.habits() == []


def test_line_is_required_named_no_default():
    p = inspect.signature(L.Learner.graduate).parameters["threshold"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is inspect.Parameter.empty
