"""A banner build re-asks the store (live 2026-11-12: Memo's banner showed its RECENT chat ten messages behind). The seat drops the
instance's kept sql() answers at each build, but a getter recomputes only when something it READS changes, and a model's
getter over constants never does. The build refreshes the instance's getters, so every build reads the store afresh."""
import json
import os
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

PACK = """PACK probe 1
IMPORT banner 1
ProbeBanner.rows getter: sql("SELECT json_extract(body,'$.d.text') AS text FROM c_events ORDER BY json_extract(body,'$.d.seq') DESC LIMIT 5") or []
ProbeBanner.text getter: join([prop(r, 'text') for r in this.rows], ' | ')
"""


def _event(c, seq, text):
    c.execute("INSERT INTO c_events VALUES (?, ?)", (f"e{seq}", json.dumps({"o": seq, "d": {"seq": seq, "text": text}})))
    c.commit()


def test_every_build_reads_the_store_afresh(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    store = tmp_path / "store"
    store.mkdir()
    c = sqlite3.connect(store / "field.sqlite3")
    c.execute("PRAGMA journal_mode=WAL")
    for t in ("c_events", "c_entity_deltas", "c_sentences"):
        c.execute(f"CREATE TABLE {t} (k TEXT PRIMARY KEY, body TEXT NOT NULL)")
    _event(c, 1, "first message")
    monkeypatch.setattr(FL, "_store_file", lambda srv: str(store / "field.sqlite3"))
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={"ses_1": {"owner": "owner"}}, hub=None))
    est._srv = srv
    live = FL.start(srv, thread=False)
    try:
        assert live._pack("probe", PACK, None)
        live.banner(object(), "owner", "ses_1", "ses_1", "hello", now=1.0, facts={"map": True})      # makes the instance
        d = est.docs[FL.banner_eid("owner", "ses_1")]
        d.cls = "ProbeBanner"
        est.upsert(d, embedder=None)
        live.settle()
        first = live.banner(object(), "owner", "ses_1", "ses_1", "second", now=2.0, facts={"map": True})
        assert first["error"] is None and "first message" in first["text"], first
        _event(c, 2, "a newer message")
        again = live.banner(object(), "owner", "ses_1", "ses_1", "third", now=3.0, facts={"map": True})
        assert "a newer message" in again["text"], again
    finally:
        FL.stop(srv)
