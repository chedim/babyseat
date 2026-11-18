"""The DOI badge on the first line of the README: it is drawn by shields.io like the badges beside it (the Zenodo badge host did not load for a reader, and GitHub then shows only the alt text `DOI`), and the badge, its link, the README text and CITATION.cff all carry the same concept DOI."""
import re
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]


def concept_doi():
    m = re.search(r'^doi: "([^"]+)"', (ROOT / "CITATION.cff").read_text(), re.M)
    assert m, "CITATION.cff has no top-level doi"
    return m.group(1)


def badge():
    first = (ROOT / "README.md").read_text().splitlines()[0]
    m = re.match(r"\[!\[DOI\]\(([^)]+)\)\]\(([^)]+)\)", first)
    assert m, "the first line of the README starts with the DOI badge"
    return m.group(1), m.group(2)


def test_the_badge_image_comes_from_shields_and_names_the_concept_doi():
    image, _ = badge()
    assert image.startswith("https://img.shields.io/badge/"), image
    assert quote(concept_doi(), safe="") in image, image


def test_the_badge_links_to_the_concept_doi():
    _, link = badge()
    assert link == f"https://doi.org/{concept_doi()}"


def test_the_readme_text_names_the_same_concept_doi():
    text = (ROOT / "README.md").read_text()
    assert f"concept DOI [{concept_doi()}](https://doi.org/{concept_doi()})" in text
