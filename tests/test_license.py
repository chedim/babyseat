"""THE LICENSE IS ONE LICENSE (2026-10-15; MIT from 0.7.3). The repo once said
three things at once: LICENSE, pyproject and the README each named a different
license, and the wheel would have shipped whichever metadata said last. From
0.7.3 babyseat is MIT (releases up to 0.7.2 stay PolyForm Noncommercial 1.0.0):
LICENSE is the MIT text VERBATIM under the copyright line (a license is never
paraphrased: the body is pinned by hash), and the package metadata, the shim
and the README name the same license."""

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPDX = "MIT"
HEAD = "MIT License\n\nCopyright (c) 2026 Dmitrii Chechetkin"
# sha256 of the MIT body from "Permission is hereby granted" to the end (https://opensource.org/license/mit)
OFFICIAL_SHA256 = "f9c4c77baa3828004ee54b8a4f2db2e88ed44a6237a493965bf551fac0fcb62d"


class TestTheLicenseIsOneLicense(unittest.TestCase):
    def test_license_file_is_the_copyright_and_the_official_text(self):
        text = (ROOT / "LICENSE").read_text()
        self.assertTrue(text.startswith(HEAD + "\n\n"))
        body = text[len(HEAD) + 2:]
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), OFFICIAL_SHA256)

    def test_the_package_declares_the_same_license(self):
        for py in (ROOT / "pyproject.toml", ROOT / "shim" / "membrane-seat" / "pyproject.toml"):
            self.assertRegex(py.read_text(), rf'(?m)^license = "{re.escape(SPDX)}"$', py)

    def test_the_readme_names_it(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("**MIT**", readme)
        self.assertNotRegex(readme, r"(?i)private-use license|noncommercial use")


if __name__ == "__main__":
    unittest.main()
