"""A Message entity that carries only its chat and time reads its words back from the store (packs/messages.c1f): `body`."""
import json
import os
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import adapters as AD  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


def _until(fn, timeout=8.0):
    t = time.time() + timeout
    while time.time() < t:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def test_a_message_without_text_reads_its_body_from_the_store(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    store = tmp_path / "store"
    store.mkdir()
    now = time.time()
    c = sqlite3.connect(store / "field.sqlite3")
    c.execute("PRAGMA journal_mode=WAL")
    for t in ("c_events", "c_entity_deltas", "c_sentences"):
        c.execute(f"CREATE TABLE {t} (k TEXT PRIMARY KEY, body TEXT NOT NULL)")
    for i, (conv, text, ts) in enumerate([("sid:ses_1", "the bilge pump is on the 12 V bus", now),
                                          ("sid:ses_1", "a later one", now + 60), ("sid:ses_2", "another chat", now)], 1):
        c.execute("INSERT INTO c_events VALUES (?, ?)", (f"e{i}", json.dumps({"o": i, "d": {
            "seq": i, "conv": conv, "role": "user", "stream": "discourse", "text": text, "ts": ts}})))
    c.commit()
    c.close()
    monkeypatch.setattr(FL, "_store_file", lambda srv: str(store / "field.sqlite3"))
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    FL.start(srv)
    AD.start(srv)
    try:
        est.upsert(QuantumEntity(eid="membrane::msg:m1", cls="Message",
                                 properties={"role": "user", "sid": "ses_1", "ts": now + 0.5}), embedder=None)
        est.upsert(QuantumEntity(eid="membrane::msg:m2", cls="Message",
                                 properties={"role": "user", "sid": "ses_1", "ts": now, "text": "its own text"}), embedder=None)
        assert _until(lambda: (est.docs["membrane::msg:m1"].properties or {}).get("body")) == "the bilge pump is on the 12 V bus"
        assert _until(lambda: (est.docs["membrane::msg:m2"].properties or {}).get("body")) == "its own text"
    finally:
        AD.stop()
        FL.stop(srv)
