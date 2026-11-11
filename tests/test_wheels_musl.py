"""Home Assistant runs on Alpine (musl libc): babyseat 0.7.5 shipped only glibc (manylinux) Linux wheels, so HA's pip
fell back to the sdist, which needs a Rust toolchain that the HA container doesn't have. The wheels lane builds musl
wheels too, and a re-dispatch of an existing tag adds the missing wheels without failing on the files already there."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MuslWheels(unittest.TestCase):
    def setUp(self):
        self.wheels = (ROOT / ".github/workflows/wheels.yml").read_text()
        self.publish = (ROOT / ".github/workflows/publish.yml").read_text()

    def test_musllinux_is_built_not_skipped(self):
        self.assertNotIn('CIBW_SKIP: "*-musllinux_*"', self.wheels)
        self.assertIn("musllinux", self.wheels)

    def test_both_linux_arches_build_musl(self):
        for arch in ("x86_64", "aarch64"):
            self.assertIn(f"linux-musl-{arch}", self.wheels)

    def test_a_musl_cdylib_links_the_c_runtime_dynamically(self):
        # Rust's musl targets link the C runtime statically by default, which can't produce a Python extension
        self.assertIn("target-feature=-crt-static", self.wheels)

    def test_a_redispatch_adds_wheels_to_an_existing_release(self):
        self.assertIn("skip-existing: true", self.publish)


if __name__ == "__main__":
    unittest.main()
