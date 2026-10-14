from membrane import eids


def test_build_twin_does_not_force_foreign(tmp_path):
    (tmp_path / "membrane").mkdir()
    (tmp_path / "membrane" / "tt.py").write_text("x = 1\n")
    (tmp_path / "build" / "lib" / "membrane").mkdir(parents=True)
    (tmp_path / "build" / "lib" / "membrane" / "tt.py").write_text("OLD\n")
    out = eids._resolve_file("tt.py", {"root": str(tmp_path), "files": {}})
    assert out == "f:membrane/tt.py"  # a build twin must not poison uniqueness (#8)


def test_build_twin_ignored_at_graph_tier():
    files = {"membrane/tt.py": {}, "build/lib/membrane/tt.py": {}}
    out = eids._resolve_file("tt.py", {"root": "/repo", "files": files})
    assert out == "f:membrane/tt.py"
