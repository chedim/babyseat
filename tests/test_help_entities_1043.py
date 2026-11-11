"""#1043 (the operator 2026-11-10): the long-form docs live in the field as help entities -- `membrane::help:<topic>`,
shipped as the `help` seed pack from membrane/help/*.md (a release updates them, a seat's edit is kept as an overlay)
-- and help(topic) reads the entity, so the always-sent doctrine stays a short core with pointers."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import help_pack as HP  # noqa: E402
from membrane import toolset as TS  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCTRINE = (ROOT / "doctrine.md").read_text()


def _est():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def test_every_packaged_topic_becomes_an_entity():
    est = _est()
    HP.install(est)
    topics = HP.topics()
    assert {"packs", "browser", "fieldvm", "exec", "cliffhanger", "goals", "watches", "voice", "clock"} <= set(topics)
    for t in topics:
        d = est.docs[HP.eid(t)]
        assert d.cls == "Help" and d.properties["text"].strip() == HP.packaged(t).strip()


def test_help_reads_the_entity_and_a_seat_edit_wins():
    est = _est()
    HP.install(est)
    d = est.docs[HP.eid("packs")]
    d.properties = {**d.properties, "text": "edited on this seat"}
    est.upsert(d, embedder=None)
    assert TS.help_text("packs", est) == "edited on this seat"
    HP.install(est)                                  # the next boot keeps the edit (an overlay, seedpack.py)
    assert TS.help_text("packs", est) == "edited on this seat"


def test_help_without_a_store_reads_the_packaged_text():
    assert TS.help_text("packs") == HP.packaged("packs")
    listing = TS.help_text("nope")
    assert "packs" in listing and "fieldvm" in listing


def test_the_doctrine_is_a_core_with_pointers():
    low = DOCTRINE.lower()
    assert "draft, not yet live" not in low                   # fields are live
    assert "trust the field" in low                           # the operator: read the field before the disk
    assert "pull request" not in low                          # #1128: a seat option now (options.improve_babyseat)
    assert ".c1f" in DOCTRINE and "help('packs')" in DOCTRINE  # behaviour is a pack
    assert "write a property whose write is the effect" not in low or "default" in low
    for gone in ("THE CLOCK:", "VOICE:", "BROWSER:", "SCHEDULED READS:"):
        assert gone not in DOCTRINE, gone                      # long forms moved to help entities


def test_no_prompt_teaches_python_entity_scripts():
    for t in HP.topics():
        assert "scripts are python" not in HP.packaged(t).lower(), t
    assert "scripts are python" not in DOCTRINE.lower()


def test_the_doctrine_ships_inside_the_package():
    # the operator 2026-11-10 ("well shiiiit! that is a bug"): doctrine.md lived at the repo root, outside the
    # package, so a PyPI / HACS / Docker install loaded an empty doctrine
    import tomllib
    from membrane import rag
    pkg = ROOT / "membrane"
    assert Path(rag._REPO_DOCTRINE).resolve() == (pkg / "doctrine.md").resolve()
    assert (pkg / "doctrine.md").is_file() and not (pkg / "doctrine.md").is_symlink()
    for pp in (ROOT / "pyproject.toml", ROOT / "shim" / "membrane-seat" / "pyproject.toml"):
        data = tomllib.loads(pp.read_text())["tool"]["setuptools"]["package-data"]["membrane"]
        assert "doctrine.md" in data, pp
    assert rag._RAG_SYSTEM.strip() and "THE FIELD IS YOUR MEMORY" in rag._RAG_SYSTEM


def test_blurp_is_a_help_topic_not_doctrine():
    # the operator 2026-11-10: Blurp is off in 0.7.5; its paragraph leaves the always-sent doctrine
    assert "SELF (#161)" not in DOCTRINE
    assert "self (#161)" in HP.packaged("blurp").lower()
