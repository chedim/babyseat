"""THE LICENSE IS ONE LICENSE (2026-10-15). The repo once said three things at once:
LICENSE carried MIT, pyproject declared the private-use LicenseRef, the README
described the private-use terms -- and the wheel would have shipped whichever
metadata said last. Membrane is licensed under the PolyForm Noncommercial License
1.0.0: LICENSE is the required notice plus the license text VERBATIM (a license
is never paraphrased: the body is pinned by hash), and the package metadata and
the README name the same license."""

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPDX = "PolyForm-Noncommercial-1.0.0"
NOTICE = "Required Notice: Copyright 2026 Dmitrii Chechetkin (https://github.com/chedim/membrane)"
# sha256 of https://raw.githubusercontent.com/polyformproject/polyform-licenses/1.0.0/PolyForm-Noncommercial-1.0.0.md
OFFICIAL_SHA256 = "c0ea4a896d2c8c394b29f9427589996db826cd501c512279ff0ed3ef48fabbe5"


class TestTheLicenseIsOneLicense(unittest.TestCase):
    def test_license_file_is_the_notice_and_the_official_text(self):
        text = (ROOT / "LICENSE").read_text()
        head, sep, body = text.partition("\n\n")
        self.assertEqual(head, NOTICE)
        self.assertTrue(sep)
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), OFFICIAL_SHA256)

    def test_the_package_declares_the_same_license(self):
        py = (ROOT / "pyproject.toml").read_text()
        self.assertRegex(py, rf'(?m)^license = "{re.escape(SPDX)}"$')

    def test_the_readme_names_it_and_no_other(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("PolyForm Noncommercial", readme)
        self.assertNotRegex(readme, r"(?i)private-use license|\bMIT license\b")


if __name__ == "__main__":
    unittest.main()
