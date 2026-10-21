"""#299: the 🌱 growth limit gives way to ACCEPTANCE.

Earning stays automatic: a passed test appends an `achieve` line, the level
counts it, the page celebrates it. But an earned achievement opens its
children only once the operator ACCEPTS it: a skill is open (its practice
acts run, its test runs) when every prerequisite is earned AND accepted. An
earned-but-unaccepted achievement counts for the level and opens nothing --
no practice, no outward act, no toy.

Accepting: POST /membrane/seat/control {"accept": "<id>"} (the control
route's gate) or `membrane self accept <id>`; each acceptance appends
{"kind": "accept", "id", "ts", "by"}. An unknown or unearned id is refused
with a clear message; accepting twice is a no-op. `self_limit` stays for
compatibility (deprecated): a number / id / "all" bulk-accepts the earned
achievements in that closure; 0 / "off" puts self to sleep.

MIGRATION: every achievement earned before this code first runs counts as
accepted -- recorded once as {"kind": "accept_migrated", "ids": [...], "ts"}
(nothing retroactive is written per achievement).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
sys.path.insert(0, str(ROOT))

from membrane import seat_panel as SP  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import toy_unlocks as TU  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from tests._self_cap import ladder3, rebuilt  # noqa: E402

NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
LIVE7 = ["feel", "reach", "kinds", "patience", "shift", "meet", "expect"]   # the live seat, 2026-10-19 (Lv 7)


@pytest.fixture(autouse=True)
def _whole_tree(monkeypatch):
    """#333: acceptance walks the whole tree -- meet, turns, expect and mirror
    built as their rebuild (part 3) will, their passes on ladder 3."""
    rebuilt(monkeypatch)


def _new_seat(d=None):
    d = Path(d or tempfile.mkdtemp())
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:lamp", cls="Component",
                      properties={"name": "lamp", "getter_script": "result = {'state': {'x': 1}}"})
    est.docs[e.eid] = e
    return srv, d


def _write_record(d, lines):
    (d / "self").mkdir(parents=True, exist_ok=True)
    (d / "self" / "record.jsonl").write_text("".join(json.dumps(ln) + "\n" for ln in lines))
    (d / "self" / "mode.json").write_text(json.dumps({"mode": "on", "limit": 8, "allow": "all", "ts": 1.0}))


def _live_record():
    """A synthetic copy of the live seat's history: born, rungs 1-6 as old
    rung lines, then expect as an achieve line (#295) -- Lv 7, turns open.
    #333: rungs 6-9 carry "ladder": 3 (untagged, they are the old wiring's
    and void: test_nudge_333)."""
    out = [{"ts": 1.0, "kind": "birth", "by": "operator"}]
    out += [{"ts": 10.0 + n, "kind": "rung", "rung": n, "skill": 0.9} for n in range(1, 7)]
    out.append({"ts": 99.0, "kind": "achieve", "id": "expect", "rung": 8, "skill": 0.12})
    return ladder3(out)


def _boot(lines=None, d=None):
    srv, d = _new_seat(d)
    if lines is not None:
        _write_record(d, lines)
    SL.bind(srv)
    return srv, d


def _kinds(srv, k):
    return [r for r in SL._read(srv) if r.get("kind") == k]


def _pass(monkeypatch, *rungs):
    """Make the tests of these rungs pass (and every other one fail)."""
    for n in list(SL.PROGRESS):
        ok = n in rungs
        monkeypatch.setitem(SL.PROGRESS, n, (lambda ok: lambda recs, tempo=None: {
            "crit": {"x": 1.0 if ok else 0.3}, "skill": 0.5 if ok else 0.0, "passed": ok})(ok))


# ---- 6. the migration, replayed through a synthetic copy of the live record ------------------

def test_the_live_record_migrates_once_and_everything_earned_is_accepted():
    srv, d = _boot(_live_record())
    mig = _kinds(srv, "accept_migrated")
    assert len(mig) == 1
    assert mig[0]["ids"] == LIVE7 and isinstance(mig[0]["ts"], float)
    assert not _kinds(srv, "accept")                       # nothing per achievement, nothing retroactive
    st = SL.status(srv)
    assert st["level"] == 7
    assert st["accepted"] == LIVE7 and st["awaiting"] == []
    assert st["next"] == ["turns", "mirror"]               # turns open, as it already was (#312: and the mirror)
    assert SL._available(srv, 7) and SL._available(srv, 8)
    # a restart: the migration is never written twice
    SL._RT.pop(id(srv), None)
    srv2, _ = _boot(None, d)
    assert len(_kinds(srv2, "accept_migrated")) == 1
    assert SL.status(srv2)["accepted"] == LIVE7


def test_a_seat_never_switched_on_migrates_at_birth_with_nothing():
    srv, d = _boot()
    assert SL.record_path(srv) is None or not SL.record_path(srv).exists()   # no record before birth
    SL.set_mode(srv, "on", by="operator")
    kinds = [r["kind"] for r in SL._read(srv)]
    assert kinds[:2] == ["birth", "accept_migrated"]
    assert _kinds(srv, "accept_migrated")[0]["ids"] == []


def test_a_saved_limit_is_not_a_standing_acceptance():
    """mode.json said allow "all": after the migration a NEW pass still waits."""
    srv, d = _boot(_live_record())
    assert SL.mode(srv) == "on"                             # the saved mode survives
    SL._append(srv, {"ts": 200.0, "kind": "achieve", "id": "turns", "rung": 7, "skill": 0.3, "ladder": 3})
    st = SL.status(srv)
    assert st["level"] == 8 and st["awaiting"] == ["turns"] and "turns" not in st["accepted"]


# ---- 1 + 2. earning stays automatic; only acceptance opens the children ----------------------

def test_a_pass_is_earned_and_counted_but_opens_nothing_until_accepted(monkeypatch):
    srv, d = _boot([{"ts": 1.0, "kind": "birth", "by": "operator"}])
    _pass(monkeypatch, 2)                                   # first reach passes
    SL._open_rungs(srv)
    assert [r["id"] for r in _kinds(srv, "achieve")] == ["reach"]
    st = SL.status(srv)
    assert st["level"] == 1 and st["awaiting"] == ["reach"] and st["accepted"] == []
    assert [e["id"] for e in st["earned"]] == ["reach"]     # the page still celebrates it
    for n in (3, 5, 6):                                     # kinds, shift, meet: reach's children
        assert not SL._available(srv, n)
    assert "kinds" not in st["next"] and "feel" in st["next"]
    tree = {t["id"]: t for t in SS.compute(srv)["tree"]}
    assert tree["reach"]["state"] == "earned" and tree["reach"]["accepted"] is False
    assert tree["kinds"]["state"] == "locked" and "accept" in tree["kinds"]["waiting_on"]
    out = SL.accept(srv, "reach", by="operator")
    assert out["ok"] and not out.get("already")
    st = SL.status(srv)
    assert st["accepted"] == ["reach"] and st["awaiting"] == []
    assert SL._available(srv, 3) and SL._available(srv, 5) and SL._available(srv, 6)
    assert {"kinds", "shift", "meet"} <= set(st["next"])
    tree = {t["id"]: t for t in SS.compute(srv)["tree"]}
    assert tree["reach"]["accepted"] is True and tree["kinds"]["state"] == "open"


def test_a_child_whose_parent_is_unaccepted_does_not_earn(monkeypatch):
    srv, d = _boot(_live_record()[:-1])                     # rungs 1-6, all migrated as accepted
    SL._append(srv, {"ts": 150.0, "kind": "rung_void", "rung": 6, "voids_ts": 16.0, "why": "test"})
    _pass(monkeypatch, 6, 7, 8)
    SL._open_rungs(srv)                                     # meet is earned again -- after the migration
    assert [r["id"] for r in _kinds(srv, "achieve")] == ["meet"]
    assert SL.status(srv)["awaiting"] == ["meet"]
    assert not SL._available(srv, 7) and not SL._available(srv, 8)
    assert not SL._replies(srv)                             # no reply checks (#333: the ball rolls went)
    SL.accept(srv, "meet", by="operator")
    SL._open_rungs(srv)
    assert {r["id"] for r in _kinds(srv, "achieve")} == {"meet", "turns", "expect"}


def test_asleep_nothing_is_open_and_acceptance_still_records():
    srv, d = _boot(_live_record())
    SL.set_mode(srv, "off")
    assert SL.status(srv)["allowed"] == [] and not SL._available(srv, 2)
    SL._append(srv, {"ts": 200.0, "kind": "achieve", "id": "turns", "rung": 7, "skill": 0.3, "ladder": 3})
    assert SL.accept(srv, "turns")["ok"]
    assert SL.mode(srv) == "off"                            # accepting never wakes it


# ---- 3. accepting: refused when unknown or unearned, a no-op twice --------------------------

def test_accept_refuses_unknown_and_unearned_ids_and_is_a_no_op_twice():
    srv, d = _boot(_live_record())
    SL._append(srv, {"ts": 200.0, "kind": "achieve", "id": "turns", "rung": 7, "skill": 0.3, "ladder": 3})
    bad = SL.accept(srv, "flying")
    assert not bad["ok"] and "unknown achievement" in bad["error"] and "turns" in bad["error"]
    no = SL.accept(srv, "mirror")
    assert not no["ok"] and "not earned" in no["error"] and SL.ACHIEVEMENTS["mirror"]["name"] in no["error"]
    ok = SL.accept(srv, "turns", by="ha:alice")
    assert ok == {"ok": True, "id": "turns", "name": SL.ACHIEVEMENTS["turns"]["name"], "already": False}
    again = SL.accept(srv, "turns", by="operator")
    assert again["ok"] and again["already"] is True
    lines = _kinds(srv, "accept")
    assert len(lines) == 1
    ln = lines[0]
    assert set(ln) == {"ts", "kind", "id", "by"} and ln["id"] == "turns" and ln["by"] == "ha:alice"
    assert SL.accept(srv, "expect")["already"] is True     # migrated: already accepted


# ---- the route, the identity, the CLI ----------------------------------------------------------

def test_the_control_route_accepts_and_says_who():
    srv, d = _boot(_live_record())
    SL._append(srv, {"ts": 200.0, "kind": "achieve", "id": "turns", "rung": 7, "skill": 0.3, "ladder": 3})
    assert "accept" in SP.CONTROLS
    out = SP.control(srv, {"accept": "turns"}, by="ha:alice")
    assert out["applied"]["accept"] == {"ok": True, "id": "turns", "name": SL.ACHIEVEMENTS["turns"]["name"],
                                        "already": False}
    assert _kinds(srv, "accept")[-1]["by"] == "ha:alice"
    assert "turns" in out["state"]["self"]["accepted"]
    ev = srv.entity_store.docs[MEMBRANE_NS + "presence"].properties["last_event"]
    assert ev["kind"] == "self-accept" and ev["value"] == "turns" and ev["by"] == "ha:alice"
    bad = SP.control(srv, {"accept": "mirror"})
    assert bad["applied"]["accept"]["ok"] is False and "not earned" in bad["applied"]["accept"]["error"]


def test_the_caller_identity():
    assert SP.caller() == "operator"
    assert SP.caller(token_sub="u-42") == "user:u-42"
    assert SP.caller(relay_speaker="Dana") == "ui:Dana"
    assert SP.caller(body={"person": "alice"}) == "ha:alice"
    assert SP.caller(token_sub="u-42", body={"person": "alice"}) == "user:u-42"   # the token wins
    assert SP.caller(body={"person": "x\n" * 200})[:3] == "ha:" and len(SP.caller(body={"person": "x" * 500})) <= 80


def test_the_server_route_passes_the_caller_and_the_ha_relay_stamps_the_person():
    server = (ROOT / "membrane" / "server.py").read_text()
    route = server.split('if path == "/membrane/seat/control":', 1)[1].split("if path ==", 1)[0]
    assert "_SP.caller(" in route and 'by="operator"' not in route
    relay = (ROOT / "custom_components" / "membrane" / "relay.py").read_text()
    person = relay.split("PERSON_PATHS = (", 1)[1].split(")", 1)[0]
    assert '"membrane/seat/control"' in person


def test_self_limit_still_works_as_a_bulk_accept():
    srv, d = _boot(_live_record()[:-1])                     # rungs 1-6 migrated
    SL._append(srv, {"ts": 200.0, "kind": "achieve", "id": "expect", "rung": 8, "skill": 0.3, "ladder": 3})
    SL._append(srv, {"ts": 201.0, "kind": "achieve", "id": "turns", "rung": 7, "skill": 0.3, "ladder": 3})
    out = SP.control(srv, {"self_limit": "expect"}, by="operator")   # its closure: reach, meet, expect
    assert "self_limit" in out["applied"]
    assert [r["id"] for r in _kinds(srv, "accept")] == ["expect"]  # only the earned, unaccepted ones in it
    assert SL.status(srv)["awaiting"] == ["turns"]
    SP.control(srv, {"self_limit": "all"})
    assert SL.status(srv)["awaiting"] == []
    SP.control(srv, {"self_limit": 0})
    assert SL.mode(srv) == "off"
    SP.control(srv, {"self_limit": 8})
    assert SL.mode(srv) == "on"                             # a number wakes it, as it always did


def test_the_cli_posts_to_the_seat(monkeypatch, capsys):
    from membrane import self_name as SN
    sent = {}

    def fake_post(url, body, headers):
        sent.update(url=url, body=body, headers=headers)
        return 200, {"ok": True, "applied": {"accept": {"ok": True, "id": "turns", "name": "take turns",
                                                        "already": False}}}
    monkeypatch.setattr(SN, "_post_json", fake_post)
    assert SN.cli_main(["accept", "turns", "--url", "http://127.0.0.1:4097"]) == 0
    assert sent["url"] == "http://127.0.0.1:4097/membrane/seat/control" and sent["body"] == {"accept": "turns"}
    assert "accepted" in capsys.readouterr().out
    monkeypatch.setattr(SN, "_post_json", lambda u, b, h: (200, {"ok": True, "applied": {"accept": {
        "ok": False, "error": "'recognize itself' is not earned yet"}}}))
    assert SN.cli_main(["accept", "mirror"]) == 2
    assert "not earned" in capsys.readouterr().err


# ---- the toys unlock on acceptance --------------------------------------------------------------

def test_a_toy_unlocks_only_once_its_achievement_is_accepted(monkeypatch):
    srv, d = _boot([{"ts": 1.0, "kind": "birth", "by": "operator"},
                    {"ts": 2.0, "kind": "rung", "rung": 2, "skill": 0.9}])   # reach, migrated
    SL._append(srv, {"ts": 300.0, "kind": "achieve", "id": "kinds", "rung": 3, "skill": 0.4})
    why = TU.refusal(srv, "tower")
    assert why and "accept" in why
    assert not [r for r in TU.sync(srv) if r.get("kind") == "unlock" and r.get("rung") == 3]
    SL.accept(srv, "kinds")
    assert TU.refusal(srv, "tower") is None
    assert [r for r in _kinds(srv, "unlock") if r.get("rung") == 3]      # accept syncs at once


# ---- 4. the data contract: status, the sheet, the tree, the dock ---------------------------------

def test_the_sheet_and_the_dock_carry_acceptance():
    srv, d = _boot(_live_record())
    SL._append(srv, {"ts": 200.0, "kind": "achieve", "id": "turns", "rung": 7, "skill": 0.3, "ladder": 3})
    sh = SS.compute(srv)
    assert sh["accepted"] == LIVE7 and sh["awaiting"] == ["turns"]
    for row in sh["tree"]:
        assert row["accepted"] is (row["id"] in LIVE7)
    dock = SS.dock(srv, fresh=True)
    assert dock["awaiting"] == ["turns"]
    st = SP.state(srv)["self"]
    assert st["awaiting"] == ["turns"] and st["accepted"] == LIVE7


# ---- 4 + 5. the UI -----------------------------------------------------------------------------

def _js(path, body):
    js = "const M=require(%r); let out; %s; process.stdout.write(JSON.stringify(out))" % (str(path), body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


TREE = {"header": {"name": "Pip", "level": 3}, "level": 3, "tree": [
    {"id": "reach", "name": "first reach", "branch": "world", "prereqs": [], "built": True, "state": "earned",
     "accepted": True, "earned_at": "2026-10-15T10:00:00+00:00", "skill": 0.4, "bar": 1, "waiting_on": None},
    {"id": "meet", "name": "<i>meet</i> an other", "branch": "others", "prereqs": ["reach"], "built": True,
     "state": "earned", "accepted": False, "earned_at": "2026-10-18T10:00:00+00:00", "skill": 1.0, "bar": 1,
     "waiting_on": None},
    {"id": "turns", "name": "take turns", "branch": "others", "prereqs": ["meet"], "built": True, "state": "locked",
     "accepted": False, "earned_at": None, "skill": None, "bar": 0, "waiting_on": "your accept of meet an other"},
    {"id": "feel", "name": "feel itself", "branch": "self", "prereqs": [], "built": True, "state": "earned",
     "earned_at": "2026-10-14T10:00:00+00:00", "skill": 0.4, "bar": 1, "waiting_on": None}]}


@NODE
def test_the_tree_shows_an_awaiting_node_with_its_own_state():
    t = _js(UI / "panel.js", "out = M.skillTree(%s)" % json.dumps(TREE))
    by = {n["id"]: n for n in t["nodes"]}
    assert by["meet"]["state"] == "awaiting" and by["meet"]["accepted"] is False
    assert by["reach"]["state"] == "earned" and by["feel"]["state"] == "earned"   # no `accepted`: an older seat
    assert t["level"] == 3
    txt = _js(UI / "panel.js", "out = M.nodeStateText(M.skillTree(%s).nodes[1])" % json.dumps(TREE))
    assert "earned" in txt and "awaiting your accept" in txt
    card = _js(UI / "panel.js", "const t = M.skillTree(%s); out = M.nodeCard(t.nodes[1], t.nodes)" % json.dumps(TREE))
    assert card["accept"] == "meet"
    card2 = _js(UI / "panel.js", "const t = M.skillTree(%s); out = M.nodeCard(t.nodes[0], t.nodes)" % json.dumps(TREE))
    assert not card2.get("accept")
    ach = _js(UI / "panel.js", "out = M.achievementsList(M.skillTree(%s).nodes).map((a) => a.id)" % json.dumps(TREE))
    assert set(ach) == {"reach", "meet", "feel"}                # it still counts


FAKE_DOM = (ROOT / "tests" / "test_skill_tree_tab_296.py").read_text().split('FAKE_DOM = r"""', 1)[1].split('"""', 1)[0]


@NODE
def test_the_card_has_a_real_accept_button_that_calls_back():
    body = (FAKE_DOM + "const root = new El('div'); const calls = [];"
            "const sel = { onAccept: (id) => { calls.push(id); return Promise.resolve({ ok: true }); } };"
            "M.renderSkillTree(doc, root, %s, sel);"
            "const b = find(root, 'sknode').find((e) => e.attrs['data-id'] === 'meet');"
            "find(root, 'skbox')[0].fire('click', b);"
            "const acc = find(root, 'skaccept');"
            "acc[0].fire('click', acc[0]);"
            "out = {n: acc.length, tag: acc[0].tagName, type: acc[0].attrs.type, aria: acc[0].attrs['aria-label'] || '',"
            " text: acc[0].textContent, calls, cls: cls(b), nodeAria: b.attrs['aria-label']}") % json.dumps(TREE)
    out = _js(UI / "panel.js", body)
    assert out["n"] == 1 and out["tag"] == "button" and out["type"] == "button"
    assert "Accept" in out["text"] and "<i>meet</i> an other" in out["aria"]   # escaped: text, not markup
    assert out["calls"] == ["meet"]
    assert "awaiting" in out["cls"].split() and "awaiting your accept" in out["nodeAria"]


@NODE
def test_the_celebration_asks_to_accept():
    q = _js(UI / "home.js", "out = M.acceptAskText('Blurp', 'take turns')")
    assert q == "🏅 Blurp earned take turns -- accept, and open what it leads to?"
    ask = _js(UI / "home.js", "out = [M.acceptToAsk(['turns', 'expect'], ['turns']), M.acceptToAsk([], []),"
                              " M.acceptToAsk(null, []), M.acceptToAsk(['turns'], [])]")
    assert ask == ["expect", None, None, "turns"]
    body = _js(UI / "home.js", "out = [M.achieveNotifyBody(true), M.achieveNotifyBody(false)]")
    assert "awaits your accept" in body[0] and "awaits" not in body[1]


def test_home_has_the_accept_button_and_no_chooser():
    home = (UI / "home.js").read_text()
    assert "selfpick" not in home and "_setSelfLimit" not in home
    assert 'class="cb-acc"' in home and 'class="cb-yes"' in home and "_acceptSelf(" in home
    assert "pointer-events:auto" in home.split(".celebrate .cb-acc {", 1)[1].split("}", 1)[0]
    assert 'class="spsleep"' in home and "aria-pressed" in home.split('class="spsleep"', 1)[1][:200]
    assert "_setSelfMode(" in home
    assert "achieveNotifyBody(" in home


def test_the_panel_has_no_chooser_but_accept_and_sleep():
    js = (UI / "panel.js").read_text()
    assert 'class="selfpick"' not in js and "self_limit" not in js and "fillSelfPick" not in js
    assert "JSON.stringify({ accept: id })" in js
    assert "JSON.stringify({ self: on ? \"on\" : \"off\" })" in js
    assert 'class="btn icon selfsleep"' in js


# ---- docs -----------------------------------------------------------------------------------------

def test_the_docs_say_so():
    g = (ROOT / "docs" / "selfgrowth.md").read_text()
    assert "### Accepting what it earns" in g
    assert '{"accept": "turns"}' in g and "membrane self accept" in g and "accept_migrated" in g
    assert "deprecated" in g.split("self_limit", 1)[1][:2000]
    assert "### The 🌱 chooser" not in g
    d = (ROOT / "doctrine.md").read_text()
    assert "accept" in d.split("SELF (#161)", 1)[1].split("\n", 1)[0]


@NODE
def test_the_cards_learning_now_says_a_skill_waits_on_your_accept():
    sheet = {"header": {"name": "Blurp", "level": 6, "mode": "on"}, "skills": [
        {"id": "turns", "name": "time its <b>turn</b>", "bar": 0, "awaiting_accept": True,
         "waiting_on": "your accept of meet an other", "criteria": []},
        {"id": "shift", "name": "notice the world changed", "bar": 0.4, "waiting_on": "looks", "criteria": []}]}
    html = _js(UI / "panel.js", "out = M.selfSheetHtml(%s, {quest: true})" % json.dumps(sheet))
    assert "🎁 waiting on your accept of meet an other" in html
    assert "time its &lt;b&gt;turn&lt;/b&gt;" in html                 # escaped
    assert 'class="lskill awaiting"' in html and "waiting on: looks" in html


def test_the_report_card_ladder_says_awaiting_your_accept():
    js = (UI / "panel.js").read_text()
    assert "held by the limit" not in js and 'capped: "awaiting your accept"' in js
