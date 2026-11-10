"""The native field engine (native/, Rust + PyO3) as the `membrane.membrane_vm` extension, built by setuptools-rust.
Everything else about the package is in pyproject.toml. One wheel per platform serves every Python >= 3.10 (PyO3's
stable ABI, abi3-py310). On macOS the engine's vector top-k runs on Metal (the `metal` feature)."""
import sys

from setuptools import setup
from setuptools_rust import Binding, RustExtension

FEATURES = ["python"] + (["metal"] if sys.platform == "darwin" else [])

setup(rust_extensions=[RustExtension("membrane.membrane_vm", path="native/Cargo.toml", binding=Binding.PyO3,
                                     features=FEATURES, py_limited_api="cp310", debug=False)],
      options={"bdist_wheel": {"py_limited_api": "cp310"}})        # the wheel's tag: cp310-abi3, every Python >= 3.10
