"""The banner's recall section as a .c1f pack (#994, the operator 2026-11-10): one Banner:<owner>:<chat> instance per
person per chat, its dials copied from recall:dials, its lines made by the pack's getter through the seat's
built-ins; an instance of another class runs that class's pack (Memo's own); a getter error is shown, never replaced."""
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import conv_banner as CB  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import recall_banner as RB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

_REAL = RB.c1f_call
FACTS = {1: "the boat's battery is at 12.4 V", 2: "the anchor light is LED", 3: "the bilge pump ran twice"}


def _fake(ctx, name, args):
    """The built-ins over three fixed rows (the real ones read the field; one test below runs them)."""
    if name == "recall":
        ctx["text"] = args[0]
        rows = [{"a_n": [n], "ts": 1.0, "role": "model", "via": "direct", "s": n} for n in FACTS]
        return [RB._light(ctx, r) for r in rows]
    if name == "rerank":
        rows = sorted(RB._full(ctx, args[0]), key=lambda r: -r["s"])
        ctx["ranked"] = rows
        return [RB._light(ctx, dict(r, score=float(r["s"]))) for r in rows]
    if name == "pick":
        return [RB._light(ctx, dict(r, fact=FACTS[r["a_n"][0]])) for r in RB._full(ctx, args[0])[: int(args[1])]]
    if name == "line":
        r = RB._full(ctx, [args[0]])[0]
        return f"#{r['a_n'][0]} {r['fact']}"
    return _REAL(ctx, name, args)                   # topic(...): the real one


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    sessions = {"ses_op": {"owner": "owner"}, "ses_memo": {"owner": "memo"}}
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions=sessions, hub=None))
    est._srv = srv
    est.upsert(QuantumEntity(eid=FL.DIALS_EID, cls="RecallDials", properties={"name": "recall dials", "rrf_k": 60, "n": 2}))
    live = FL.start(srv, thread=False)
    srv.live = live
    yield srv
    FL.stop(srv)


def _lines(srv, chat, text, monkeypatch, fake=_fake):
    monkeypatch.setattr(RB, "c1f_call", fake)
    return CB.recall_lines(srv, object(), chat, [], text, now=100.0, sid=chat)


def test_each_person_in_each_chat_has_an_instance_with_the_dials_copied(seat, monkeypatch):
    assert seat.live.has_banner()
    lines = _lines(seat, "ses_op", "what is the battery voltage on the boat?", monkeypatch)
    assert lines == ["#3 the bilge pump ran twice", "#2 the anchor light is LED"]      # n = 2, from recall:dials
    d = seat.entity_store.docs[FL.banner_eid("owner", "ses_op")]
    assert d.cls == "Banner" and d.properties["rrf_k"] == 60 and d.properties["pool"] == RB.POOL
    assert d.properties["lines"] == lines                                             # kept on the field
    _lines(seat, "ses_memo", "what is the battery voltage on the boat?", monkeypatch)
    assert FL.banner_eid("memo", "ses_memo") in seat.entity_store.docs


def test_an_instance_dial_changes_only_that_chats_banner(seat, monkeypatch):
    _lines(seat, "ses_op", "what is the battery voltage on the boat?", monkeypatch)
    d = seat.entity_store.docs[FL.banner_eid("owner", "ses_op")]
    d.properties = {**d.properties, "n": 3}
    seat.entity_store.upsert(d, embedder=None)
    seat.live.settle()
    assert len(_lines(seat, "ses_op", "what is the battery voltage on the boat?", monkeypatch)) == 3
    assert len(_lines(seat, "ses_memo", "what is the battery voltage on the boat?", monkeypatch)) == 2


def test_a_getter_error_is_shown_instead_of_the_lines(seat, monkeypatch):
    def broken(ctx, name, args):
        if name == "recall":
            raise RuntimeError("the store is away")
        return _fake(ctx, name, args)
    lines = _lines(seat, "ses_op", "what is the battery voltage on the boat?", monkeypatch, broken)
    assert len(lines) == 1 and "the banner failed" in lines[0] and "the store is away" in lines[0]


def test_an_instance_of_memos_class_runs_memos_pack(seat, monkeypatch):
    seat.entity_store.upsert(QuantumEntity(eid=MEMBRANE_NS + "pack:memobanner", cls="Pack", properties={
        "name": "memobanner",
        "c1f": "PACK memobanner 1\nMemoBanner.lines getter: [line(r) for r in pick(rerank(recall(this.ask, 10, this.session), this.ask, None), 1, this.session)]\n"}))
    seat.live.settle()
    eid = FL.banner_eid("memo", "ses_memo")
    seat.entity_store.upsert(QuantumEntity(eid=eid, cls="MemoBanner", properties={"name": "memo's banner"}))
    seat.live.settle()
    assert _lines(seat, "ses_memo", "what is the battery voltage on the boat?", monkeypatch) == ["#3 the bilge pump ran twice"]


def test_a_message_with_no_topic_keeps_the_previous_lines(seat, monkeypatch):
    first = _lines(seat, "ses_op", "what is the battery voltage on the boat?", monkeypatch)
    calls = []
    def counting(ctx, name, args):
        calls.append(name)
        return _fake(ctx, name, args)
    assert _lines(seat, "ses_op", "ok thanks", monkeypatch, counting) == first
    assert calls == ["topic"]                       # the pack's WHEN arm asked, and nothing was recalled


def test_the_real_built_ins_recall_through_the_pack(seat):
    from _ef_fakes import FakeEmbedder
    from membrane import efield as EF
    from membrane import streams as ST
    from membrane.efstore import MemBackend
    fld = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    fld.write_events([ST.Event(stream="discourse", role="user", ts=1.0, text="what does membrane/lexical.py do?")],
                     session="s:old")
    fld.write_events([ST.Event(stream="discourse", role="model", ts=2.0,
                               text="membrane/lexical.py keeps a BM25 index over the field's sentences.")],
                     session="s:old")
    fld.process()
    lines = CB.recall_lines(seat, fld, "ses_op", [], "what does membrane/lexical.py keep?", now=100.0, sid="ses_op")
    assert any("BM25 index" in ln for ln in lines), lines
    st = seat._recall_prev["ses_op"]
    assert st["text"] == "what does membrane/lexical.py keep?" and st["top"]


def test_an_entity_can_be_made_of_a_packs_own_class(seat):
    # Memo, 2026-11-10: entity_add / entity_edit forced `Component`, so a pack's class (MemoBanner, Probe) had no
    # instances and its getters never ran
    from membrane.entities import MEMBRANE_NS
    est = seat.entity_store
    est.upsert(QuantumEntity(eid=MEMBRANE_NS + "pack:probe", cls="Pack", properties={
        "name": "probe", "c1f": "PACK probe 1\nProbe.twice getter: (this.n or 0) * 2\n"}))
    seat.live.settle()
    r = est.add("Probe", "first probe")
    assert r["ok"] and r["class"] == "Probe", r
    eid = r["eid"]
    assert est.docs[eid].cls == "Probe"
    other = est.add("Component", "plain thing")["eid"]
    assert est.edit(other, cls="Probe")["ok"] and est.docs[other].cls == "Probe"
    assert est.add("NoSuchClass", "x")["class"] == "Component"      # an unknown class still falls back


def test_the_pack_lays_out_the_same_block_as_the_python_banner(seat, tmp_path, monkeypatch):
    """Step 1 of the port (#994): OPEN, DECIDED and the layout in .c1f give the very block conv_banner.render gave."""
    from membrane import conv_banner as CB
    from membrane.entities import MEMBRANE_NS
    now = 1_794_300_000.0
    est = seat.entity_store
    led = tmp_path / "field.json.ledger.jsonl"
    led.write_text("\n".join(json.dumps(e) for e in [
        {"conv": "ses_op", "text": "ship the browser VM today", "ts": "2026-11-10T08:00:00Z"},
        {"conv": "elsewhere", "text": "not this chat", "ts": "2026-11-10T08:30:00Z"}]) + "\n")
    seat.user_ledger = types.SimpleNamespace(path=str(led), entries=lambda: [json.loads(x) for x in led.read_text().splitlines()])
    monkeypatch.setattr(FL, "_store_file", lambda srv: str(tmp_path / "store" / "field.sqlite3"))
    goals = {"here": {"status": "open", "convs": ["ses_op"], "born": now - 7200, "picked_up_by": "worker-1",
                      "picked_up_ts": now - 3000, "picked_up_end_ts": now - 2000},
             "there": {"status": "open", "convs": ["ses_x"], "born": now - 600},
             "board": {"status": "open", "born": now - 100},
             "done": {"status": "fulfilled", "fulfilled_conv": "ses_op", "fulfilled_ts": now - 50}}
    for k, p in goals.items():
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "goal:" + k, cls="Goal", properties={"name": "goal " + k, **p}))
    sections = {"paused": False, "alerts": ["the seat restarted"], "working": "WORKING ON: build the banner (person · just now)",
                "about": [], "stable": [], "since": [],
                "blurp": None, "suggest": [], "more": True}
    # the Python block, from the same seat
    sec = {**sections, "working": None, "decided": CB.decided(seat, "ses_op"), "open": CB.open_items(seat, "ses_op"),
           "stable": [], "since": [], "recall": []}
    want = CB.render(sec, now)
    assert want is not None
    # the pack's block (store-less here: the ledger comes through jsonl, which needs a store's folder)
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    import sqlite3
    sqlite3.connect(store_dir / "field.sqlite3").execute("CREATE TABLE c_events (k TEXT PRIMARY KEY, body TEXT)").connection.commit()
    FL.stop(seat)
    live = FL.start(seat, thread=False)
    r = live.banner(object(), "owner", "ses_op", "ses_op", "", now=now, sections={**sections, "working": None, "since": []})
    assert r["error"] is None, r
    assert r["text"] == want, "\n--- pack\n" + str(r["text"]) + "\n--- python\n" + str(want)


def _store_fixture(path, now):
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    for t in ("c_events", "c_entity_deltas", "c_sentences"):
        c.execute(f"CREATE TABLE {t} (k TEXT PRIMARY KEY, body TEXT NOT NULL)")
    ev = lambda k, seq, conv, role, text, author="person", stream="discourse": c.execute(
        "INSERT INTO c_events VALUES (?, ?)", (k, json.dumps({"o": seq, "d": {"seq": seq, "conv": conv, "role": role,
        "stream": stream, "text": text, "author": author, "ts": now - 600 + seq, "status": "applied"}})))
    dl = lambda ev_k, eid, seq, sid="": c.execute("INSERT INTO c_entity_deltas VALUES (?, ?)", (
        f"d:{ev_k}:{eid}", json.dumps({"o": seq, "d": {"event": ev_k, "eid": eid, "seq": seq, "sid": sid, "ts": now - 600 + seq, "sidx": 0}})))
    ev("e1", 1, "sid:ses_op", "user", "[user#owner: owner]\nlook at membrane/x.py please")
    dl("e1", "f:membrane/x.py", 1)
    ev("e2", 2, "sid:ses_op", "model", "it keeps the index", author="model")
    dl("e2", "f:membrane/x.py", 2)
    ev("e3", 3, "sid:ses_op", "user", "[user#owner: owner]\nand the tests")
    dl("e3", "f:tests/t.py", 3)
    ev("e4", 4, "sid:ses_op", "user", "[user#owner: owner]\nwhat about x.py now?")
    dl("e4", "f:membrane/x.py", 4)
    ev("e9", 10, "sid:ses_other", "user", "Memo changed it")
    dl("e9", "f:membrane/x.py", 10, sid="s1")
    c.execute("INSERT INTO c_sentences VALUES (?, ?)", ("s1", json.dumps({"o": 1, "d": {"text": "x.py   now   splits the index"}})))
    c.commit()


def test_step_2_working_about_since_stable_come_from_the_store(seat, tmp_path, monkeypatch):
    """Step 2 of the port (#994, the operator: from what capture stored, no Python): ABOUT, WORKING ON, SINCE and
    STABLE read the chat's events and the entities each touched through sql()."""
    now = 1_794_300_000.0
    store = tmp_path / "store"
    store.mkdir()
    _store_fixture(store / "field.sqlite3", now)
    led = tmp_path / "field.json.ledger.jsonl"
    led.write_text("\n".join(json.dumps(e) for e in [
        {"conv": "c1", "text": "membrane/x.py must stay small", "ts": "2026-11-07T10:00:00Z"},
        {"conv": "c2", "text": "membrane/x.py must stay small", "ts": "2026-11-08T10:00:00Z"},
        {"conv": "c3", "text": "membrane/x.py  must stay small", "ts": "2026-11-09T10:00:00Z"},
        {"conv": "c1", "text": "an unrelated rule", "ts": "2026-11-08T10:00:00Z"}]) + "\n")
    seat.user_ledger = types.SimpleNamespace(path=str(led))
    monkeypatch.setattr(FL, "_store_file", lambda srv: str(store / "field.sqlite3"))
    monkeypatch.setattr(RB, "c1f_call", lambda ctx, name, args: [] if name in ("recall", "rerank", "pick")
                        else _REAL(ctx, name, args))          # recall is another test's: no rows here
    FL.stop(seat)
    live = FL.start(seat, thread=False)
    live.banner(object(), "owner", "ses_op", "ses_op", "", now=now, sections={})        # the instance, its dials
    FL.mark_delivered(seat.entity_store, "sid:ses_op", 5)
    live.settle()
    r = live.banner(object(), "owner", "ses_op", "ses_op", "what about x.py now?", now=now, sections={})
    assert r["error"] is None, r
    text = r["text"]
    assert "ABOUT: f:membrane/x.py" in text, text
    assert 'WORKING ON: "look at membrane/x.py please" (person · 9 min ago)' in text, text
    assert "SINCE YOUR LAST MESSAGE:\n- f:membrane/x.py: x.py now splits the index (another chat · 9 min ago)" in text, text
    assert "STABLE:\n- membrane/x.py must stay small (fact · 3 chats" in text, text
    assert "an unrelated rule" not in text


def test_a_slow_recall_does_not_hold_the_field(seat, monkeypatch):
    # the operator 2026-11-10 (perf): recall and rerank run off the engine, like sql(); the banner waits for them
    # without the field's lock, so a slow recall never stalls the thinker's tick or another chat's banner
    import threading
    started, release = threading.Event(), threading.Event()

    def slow(ctx, name, args):
        if name == "recall":
            started.set()
            assert release.wait(10)
        return _fake(ctx, name, args)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("lines", _lines(seat, "ses_op",
                                                                       "what is the battery voltage on the boat?",
                                                                       monkeypatch, slow)))
    t.start()
    try:
        assert started.wait(10), "recall never ran"
        ticked = threading.Event()
        threading.Thread(target=lambda: (seat.live.tick(), ticked.set()), daemon=True).start()
        assert ticked.wait(3), "the field's lock was held while recall ran"
    finally:
        release.set()
        t.join(10)
    assert out["lines"] == ["#3 the bilge pump ran twice", "#2 the anchor light is LED"]


def test_the_same_words_again_recall_again(seat, monkeypatch):
    # a slow built-in's answer stays with its getter (a chain in one getter settles), but each build starts fresh
    calls = []

    def counting(ctx, name, args):
        calls.append(name)
        return _fake(ctx, name, args)
    q = "what is the battery voltage on the boat?"
    _lines(seat, "ses_op", q, monkeypatch, counting)
    _lines(seat, "ses_op", q, monkeypatch, counting)
    assert calls.count("recall") == 2 and calls.count("rerank") == 2
