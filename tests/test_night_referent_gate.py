"""The night minter refuses file entities whose referent does not exist.

The defect: the night read text that mentioned filenames and minted
them (~doomed.py, ~btfixworker.py at three spellings); the phantoms then
climbed the field because every probe of them counted as activity.
"""
import uuid

from membrane.night import _referent_exists


def test_real_file_resolves(tmp_path):
    f = tmp_path / "real_gate_probe.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert _referent_exists(str(f))


def test_home_phantom_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    name = "~/nope_%s.py" % uuid.uuid4().hex
    assert not _referent_exists(name)


def test_cwd_relative_mention_resolves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "here_gate_probe.py").write_text("x = 1\n", encoding="utf-8")
    assert _referent_exists("here_gate_probe.py")


def test_empty_val_refused():
    assert not _referent_exists("")
    assert not _referent_exists(None)
