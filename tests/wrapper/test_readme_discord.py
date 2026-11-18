"""The Discord invite is the first badge on the first line of the README: drawn by shields.io like the badges beside it, and linking to the invite."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVITE = "https://discord.gg/xeHswygKzX"


def first_line():
    return (ROOT / "README.md").read_text().splitlines()[0]


def test_the_first_badge_is_the_discord_invite():
    m = re.match(r"\[!\[Discord\]\((https://img\.shields\.io/badge/[^)]+)\)\]\(([^)]+)\)", first_line())
    assert m, "the first line of the README starts with the Discord badge"
    assert "discord" in m.group(1).lower(), m.group(1)
    assert m.group(2) == INVITE


def test_the_other_badges_are_still_on_the_line():
    assert "[![DOI]" in first_line() and "[![Docker pulls]" in first_line()
