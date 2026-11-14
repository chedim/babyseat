"""The wheel's build hook, left as the seam for the binary (#1143).

Everything about the package is in pyproject.toml. The PyO3 extension is gone: the seat is the `cliffvm` binary (native/, Rust), and
Python is only the launcher (membrane/launcher.py).

TODO(packaging, docs/python-retirement.md step 9, docs/packaging-plan.md (d)): build native/ here with setuptools-rust
`RustExtension(..., binding=Binding.Exec)` (cargo `--no-default-features --features bin`), put the binary and the packs/apps/ui/help
folders in `membrane/dist/`, tag the wheel py3-none-<platform>, and make a platform with no binary FAIL LOUDLY ("no binary for this
platform"); the sdist never builds with cargo (decision 4). Until that lands this builds a launcher-only wheel."""
from setuptools import setup

setup()
