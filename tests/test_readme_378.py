"""#378: the README reads easier. Its opening is short, and its
differentiation section says the main thing first: membrane is a general
real-time proxy that parses whatever text passes through it into experience,
with an embedding model and no LLM step."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()


def _section(title_re):
    m = re.search(r"(?m)^## " + title_re + r".*$", README)
    assert m, title_re
    rest = README[m.end():]
    nxt = re.search(r"(?m)^## ", rest)
    return rest[: nxt.start() if nxt else len(rest)]


def _words(s):
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)                 # images
    s = re.sub(r"\]\([^)]*\)", "]", s)                          # link targets
    return len(re.findall(r"[A-Za-z0-9][\w'’-]*", s))


def test_the_opening_is_short():
    opening = README.split("\n## ", 1)[0]
    assert _words(opening) <= 220, _words(opening)


def test_the_first_difference_is_the_real_time_proxy():
    diff = _section("What makes it different")
    first = re.search(r"(?m)^### 1\. (.*)$", diff).group(1).lower()
    assert "real-time" in first and "proxy" in first
    body = diff.split("### 2.", 1)[0].lower()
    assert "experience" in body and "no llm" in body and "embedding" in body


def test_each_difference_is_brief():
    diff = _section("What makes it different")
    for part in re.split(r"(?m)^### ", diff)[1:]:
        title, _, body = part.partition("\n")
        if title.startswith("How it maps"):
            continue
        assert _words(body) <= 90, (title, _words(body))
