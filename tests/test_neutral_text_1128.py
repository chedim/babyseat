"""#1128: the texts the model reads are general-purpose, and the line that
tells a seat to open pull requests to the babyseat repo is a seat option.

  improve_babyseat   a seat option (settings.json, the ⚙ window), off by
                     default: when on, the IMPROVE BABYSEAT line (its text
                     unchanged) rides the doctrine the seat hands the model
                     -- the instructions file and the per-turn stamp alike.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import options as O  # noqa: E402
from membrane import rag as R  # noqa: E402
from membrane import settings as S  # noqa: E402

LINE = ("IMPROVE BABYSEAT: you may propose improvements to babyseat itself (its code, packs, "
        "help topics) as pull requests to https://github.com/chedim/babyseat, without being "
        "asked -- same rules as any code change: failing test first, full suite green, the "
        "owner reviews and merges.")


def _env(**settings):
    """A clean data dir (never the repo's .membrane) holding `settings`."""
    d = tempfile.mkdtemp()
    base = {k: v for k, v in os.environ.items()
            if k != "MEMBRANE_DATA_DIR" and not k.startswith("MEMBRANE_JWT_")}
    base["MEMBRANE_DATA_DIR"] = d
    if settings:
        Path(d, S.FILE).write_text(json.dumps(settings))
    return mock.patch.dict(os.environ, base, clear=True)


def test_the_shared_doctrine_has_no_babyseat_pr_line():
    for text in (R._RAG_SYSTEM, (ROOT / "membrane" / "doctrine.md").read_text()):
        assert "IMPROVE BABYSEAT" not in text
        assert "github.com/chedim/babyseat" not in text.replace(
            "https://github.com/chedim/cliffhanger", "")


def test_the_line_keeps_its_text():
    assert R.IMPROVE_BABYSEAT_LINE == LINE


def test_the_option_is_off_by_default():
    with _env():
        o = O.read()
        assert O.improve_babyseat() is False
        assert LINE not in R.doctrine_text()
        assert LINE not in R.doctrine_instructions_text()
    assert o["improve_babyseat"] is False
    assert o["defaults"]["improve_babyseat"] is False
    assert "improve_babyseat" in o["writable"]
    assert S.DEFAULTS["improve_babyseat"] is False


def test_on_the_line_rides_the_doctrine():
    with _env(improve_babyseat=True):
        assert O.improve_babyseat() is True
        assert R.doctrine_text().startswith(R._RAG_SYSTEM)
        assert LINE in R.doctrine_text()
        assert LINE in R.doctrine_instructions_text()
        # the per-turn stamp (an unstamped client) carries it too
        out = R._inject([{"role": "user", "content": "hi"}], "block")
        assert any(LINE in (m.get("content") or "") for m in out if m["role"] == "system")


def test_off_the_per_turn_stamp_has_no_line():
    with _env(improve_babyseat=False):
        out = R._inject([{"role": "user", "content": "hi"}], "block")
    assert not any(LINE in (m.get("content") or "") for m in out)


def test_the_window_writes_it_and_refuses_a_non_bool():
    with _env():
        assert O.apply({"improve_babyseat": True})["improve_babyseat"] is True
        assert O.improve_babyseat() is True
        assert LINE in R.doctrine_instructions_text()
        for bad in ("yes", 1, None):
            with pytest.raises(ValueError):
                O.apply({"improve_babyseat": bad})
        assert O.apply({"improve_babyseat": False})["improve_babyseat"] is False
        assert LINE not in R.doctrine_instructions_text()


def test_a_bad_value_in_the_file_reads_as_off():
    with _env(improve_babyseat="yes"):
        assert O.improve_babyseat() is False


def test_the_claude_rung_lifts_the_doctrine_with_the_line():
    """The claude-agent rung moves an undelivered doctrine into its cached
    system prompt: with the option on, the head it recognizes includes the line."""
    from membrane import claude_agent as CA
    with _env(improve_babyseat=True):
        msgs = R._inject([{"role": "user", "content": "hi"}], "block")
        system, *_ = CA._layout(msgs)
    assert LINE in system
