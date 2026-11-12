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


# ---- the verb index is incremental: a boot re-notes only the methods that are new or changed ----------------

def _store_with_methods(names):
    import tempfile
    from membrane import entities as E
    d = tempfile.mkdtemp()
    st = E.EntityStore(path=str(Path(d) / "entities.json"), shared=False)
    for n in names:
        st.docs[f"membrane::method:{n}"] = E.QuantumEntity(eid=f"membrane::method:{n}", cls="Method", properties={
            "name": n, "method_name": n, "command": "echo"})
    return st


def test_a_second_boot_notes_no_method_again(monkeypatch):
    from types import SimpleNamespace
    st = _store_with_methods(["pr checks", "run tests", "close"])
    seen = []
    monkeypatch.setattr(verbs, "_command_verb", lambda w: seen.append(w) or {"checks": "check", "tests": "test"}.get(w))
    srv = SimpleNamespace(entity_store=st)
    first = verbs.boot(srv)
    assert first["verbs"] == 3 and seen                      # the first boot reads the compound names
    seen.clear()
    verbs.boot(srv)
    assert seen == []                                        # nothing is read again
    assert st.docs["membrane::verb:check"].properties["aliased"]            # and what it noted is still there


def test_a_changed_method_is_noted_again_and_a_gone_one_leaves_the_aliases(monkeypatch):
    from types import SimpleNamespace
    st = _store_with_methods(["pr checks", "run tests"])
    monkeypatch.setattr(verbs, "_command_verb", lambda w: {"checks": "check", "tests": "test"}.get(w))
    srv = SimpleNamespace(entity_store=st)
    verbs.boot(srv)
    st.docs["membrane::method:run tests"].properties = {**st.docs["membrane::method:run tests"].properties,
                                                        "method_name": "run unit tests"}
    del st.docs["membrane::method:pr checks"]
    verbs.boot(srv)
    assert "membrane::verb:check" not in st.docs               # its only meaning went: the verb is removed (as before)
    assert st.docs["membrane::method:run tests"].properties["verb"] == "membrane::verb:run_unit_tests"
