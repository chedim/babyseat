"""AGENTS.md is what agents read in their worktrees: it must be tracked
(an untracked file never reaches a worktree) and carry the claim rule."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestAgentsMd(unittest.TestCase):
    def test_agents_md_is_tracked(self):
        out = subprocess.run(["git", "ls-files", "AGENTS.md"], cwd=ROOT,
                             capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "AGENTS.md")

    def test_agents_md_says_claim_before_work(self):
        text = (ROOT / "AGENTS.md").read_text()
        self.assertIn("## Claim an issue before working on it", text)
        self.assertIn("`claimed` label", text)
        self.assertIn("The earliest claim wins.", text)
        self.assertIn("`issue-N`", text)


if __name__ == "__main__":
    unittest.main()
