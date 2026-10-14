from membrane import eids


def test_bare_name_in_subdir_resolves_canonical(tmp_path):
    d = tmp_path / "membrane"
    d.mkdir()
    (d / "night.py").write_text("x = 1\n")
    out = eids._resolve_file("night.py", {"root": str(tmp_path), "files": {}})
    assert out == "f:membrane/night.py"  # today: f:~night.py (issue #8)


def test_ambiguous_bare_name_stays_foreign(tmp_path):
    for sub in ("membrane", "tools"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "router.py").write_text("x\n")
    out = eids._resolve_file("router.py", {"root": str(tmp_path), "files": {}})
    assert out == "f:~router.py"  # cannot disambiguate: foreign spelling is honest


def test_root_level_bare_name_unchanged(tmp_path):
    (tmp_path / "voice.py").write_text("x\n")
    out = eids._resolve_file("voice.py", {"root": str(tmp_path), "files": {}})
    assert out == "f:voice.py"


def test_no_graph_stays_foreign():
    assert eids._resolve_file("opencode.py", None) == "f:~opencode.py"
