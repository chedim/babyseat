"""Live 2026-11-11: the boot spent ~100 s in spaCy re-reading every method name for its verbs (402 verbs, twice per word,
every boot) while the UI waited. A word is parsed once per process, and the boot does not wait for the verb index."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import verbs  # noqa: E402


def test_a_word_is_read_by_the_parser_once(monkeypatch):
    from membrane import language as LL
    calls = []

    class Tok:
        lemma_, pos_, dep_ = "check", "VERB", "ROOT"

    def fake_doc(text):
        calls.append(text)
        return [Tok()]
    monkeypatch.setattr(LL, "_doc", fake_doc)
    monkeypatch.setattr(LL, "available", lambda: True)
    verbs._command_verb.cache_clear()
    assert verbs._command_verb("checks") == "check" and verbs._command_verb("checks") == "check"
    assert len(calls) == 2                                   # the word and "<lemma> the thing", once each


def test_the_boot_starts_the_verb_index_in_the_background():
    src = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()
    assert 'name="boot-verbs"' in src
