"""THE SELF-FEEDING READ (the seat's #4): a query's own topic words
came back as fresh hits (w:ghost/w:boot/mirror 'touched 1-2m ago' --
spelled in the query text itself). THE CONTRACT: recall's hits carry
entities the QUERY NAMES FROM ITS OWN TEXT only when they have real
evidence BEYOND the current turn's capture (the loopback read must
not feed itself): entities whose ONLY sentence is the current/latest
query's own text are excluded (fresh-on-this-query words); an entity
with older evidence passes.
"""
import time

import pytest

from membrane.entities import EntityStore, QuantumEntity
from membrane.efield import EntityField
from membrane import streams as ST


class _Emb:
    def embed(self, texts):
        # distinct one-hot-ish vectors per text (deterministic)
        out = []
        for t in texts:
            v = [0.0] * 8
            v[hash(t) % 8] = 1.0
            out.append(v)
        return out


def _field(b):
    return EntityField(b, _Emb(), "membrane@G0XW2RC6K3")


def test_querys_own_words_excluded_when_fresh(tmp_path, monkeypatch):
    """The loopback: the query's own capture creates its word
    entities THIS TURN; recall excludes them (they have no evidence
    beyond this query)."""
    import tempfile, pathlib
    from membrane.efstore import FileBackend
    fld = _field(FileBackend(str(pathlib.Path(tmp_path) / "field")))
    fld.emb = _Emb()
    # the query's capture lands NOW (this turn's words):
    q = "the ghost boot mirror learned again"
    fld.write_events([ST.Event(stream="discourse", role="user",
                               text=q, ts=time.time())],
                     session="s:now")
    # a SECOND turn mentioning the same words (the words accrue past
    # the nursery's life bar):
    fld.write_events([ST.Event(stream="discourse", role="user",
                               text=q + " (the second saying)",
                               ts=time.time() - 30.0)],
                     session="s:prev")
    fld.process()
    # recall for the SAME query: its own words must not top the hits
    # (the ONLY-fresh-session words drop; the older evidence passes)
    qv = fld.emb.embed([q])[0]
    hits = fld.recall(qv, k=6)
    # the CURRENT turn's capture is the query's own text again:
    # excluded (a session cut), the elder words still hit:
    hits2 = fld.recall(qv, k=6, exclude_session="s:now")
    assert all(e != "w:again" for e, s in hits2), hits2


def test_old_evidence_hops_to_facts(tmp_path):
    """An entity with real history (its cells predate the query)
    stays a valid hit -- the exclusion can't starve good recall."""
    import pathlib
    from membrane.efstore import FileBackend
    fld = _field(FileBackend(str(pathlib.Path(tmp_path) / "field")))
    # OLD discourse (three sayings across an hour -- past the live bar):
    for i, dt in enumerate((3600.0, 1800.0, 900.0)):
        fld.write_events([ST.Event(
            stream="discourse", role="user",
            text=f"the ghost mirror learned thing {i}",
            ts=time.time() - dt)], session=f"s:old{i}")
    fld.process()
    q = "what did the ghost mirror do"
    fld.write_events([ST.Event(stream="discourse", role="user",
                               text=q, ts=time.time())],
                     session="s:now")
    fld.process()
    qv = fld.emb.embed([q])[0]
    hits = dict(fld.recall(qv, k=4))
    assert "w:ghost" not in hits, hits      # ISSUE #1: word-kind hits park on unfiltered asks
    assert hits, hits
    assert "person:operator" in hits, hits   # the hop lands a real co-mentioned entity
    old = dict(fld.recall(qv, k=4, hop=False))
    assert "w:ghost" in old, old       # hop=False restores the pre-patch shape


def test_recall_excludes_the_current_turn(tmp_path, monkeypatch):
    """THE ENGINE's seam: recall takes the session cut (a turn marker):
    entities whose ONLY deltas ride the CURRENT turn's session never
    hit; older sessions' hits stay."""
    import pathlib
    from membrane.efstore import FileBackend
    fld = _field(FileBackend(str(pathlib.Path(tmp_path) / "field")))
    fld.write_events([ST.Event(stream="discourse", role="user",
                               text="the ghost mirror learned",
                               ts=time.time() - 60.0)],
                     session="s:old")
    fld.process()
    q = "the ghost boot mirror"
    fld.write_events([ST.Event(stream="discourse", role="user",
                               text=q, ts=time.time())],
                     session="s:now2")
    fld.process()
    # THE EXCLUSION: the current session's own words:
    qv = fld.emb.embed([q])[0]
    hits = fld.recall(qv, k=6, exclude_session="s:now2")
    assert all("w:boot" not in e for e, s in hits), hits