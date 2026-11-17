"""seat-tests runs the engine as a library (cliffvm in seat-tests/Cargo.toml) and as a program (the release CLIFFVM_VERSION names): the two must be the same version (#1143)."""
import pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_library_and_the_program_are_one_version():
    want = (ROOT / "CLIFFVM_VERSION").read_text().strip()
    m = re.search(r'^cliffvm\s*=\s*\{[^}]*version\s*=\s*"=([^"]+)"', (ROOT / "seat-tests" / "Cargo.toml").read_text(), re.M)
    assert m, "seat-tests/Cargo.toml pins cliffvm with an exact version (=x.y.z)"
    assert m.group(1) == want, f"seat-tests depends on cliffvm {m.group(1)} but CLIFFVM_VERSION is {want}"
