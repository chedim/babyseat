"""A seat test gated on a cargo feature that `seat-tests` does not declare is compiled to NOTHING: the file builds, lists zero tests and passes. #1185 moved the pack-tied tests from `native/`
(whose package had the features `bin` and `store`) into `seat-tests/` (which had neither), and 41 files, 342 test functions, silently stopped running while CI stayed green. Every feature
named in a `cfg` of a seat test is declared in `seat-tests/Cargo.toml`, and the features that the tests need are on by default."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "seat-tests" / "tests"


def declared():
    manifest = tomllib.loads((ROOT / "seat-tests" / "Cargo.toml").read_text())
    return manifest.get("features", {}), manifest.get("features", {}).get("default", [])


def used():
    out = {}
    for f in sorted(TESTS.glob("*.rs")):
        for name in re.findall(r'feature = "([A-Za-z0-9_-]+)"', f.read_text()):
            out.setdefault(name, []).append(f.name)
    return out


def test_every_feature_a_seat_test_is_gated_on_is_declared():
    features, _ = declared()
    missing = {n: files for n, files in used().items() if n not in features}
    assert not missing, f"gated on features seat-tests does not declare, so these files compile to no tests: {missing}"


def test_the_features_the_tests_are_gated_on_are_on_by_default():
    features, default = declared()
    off = [n for n in used() if n in features and n not in default]
    assert not off, f"declared but off by default, so `cargo test` skips the tests gated on them: {off}"
