"""membrane erase-field -- the first-class memory wipe: stop the seat, then
move the field + entity side-index out of the way (recoverable), instead of
the operator's manual `mv`. `--yes` is MANDATORY."""

import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cli  # noqa: E402


def _no_seat(**kwargs):
    return {"stopped": [], "killed": [], "refused": [], "how": {}, "rc": 1}


def _seat(tmp_path):
    d = tmp_path / ".membrane"
    (d / "field").mkdir(parents=True)
    (d / "field" / "events.jsonl").write_text("x\n")
    (d / "entities.json").write_text("{}\n")
    return d


def test_erase_refuses_without_yes(tmp_path):
    d = _seat(tmp_path)
    with mock.patch("membrane.stopseat.stop_seat", side_effect=_no_seat) as stop:
        rc = cli._erase_field(["--field", str(d / "field.json")])
    assert rc == 2
    assert not stop.called                 # nothing stopped without --yes
    assert (d / "field" / "events.jsonl").exists()
    assert (d / "entities.json").exists()


def test_erase_moves_field_and_index(tmp_path):
    d = _seat(tmp_path)
    with mock.patch("membrane.stopseat.stop_seat", side_effect=_no_seat) as stop:
        rc = cli._erase_field(["--field", str(d / "field.json"), "--yes"])
    assert rc == 0
    assert stop.called                     # the seat is stopped first
    assert not (d / "field").exists()
    assert not (d / "entities.json").exists()
    assert list(d.glob("field.erased-*")), list(d.iterdir())
    assert list(d.glob("entities.json.erased-*")), list(d.iterdir())


def test_erase_nothing_is_a_noop_success(tmp_path):
    d = tmp_path / ".membrane"
    d.mkdir(parents=True)
    with mock.patch("membrane.stopseat.stop_seat", side_effect=_no_seat):
        rc = cli._erase_field(["--field", str(d / "field.json"), "--yes"])
    assert rc == 0
