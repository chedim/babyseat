"""#470 step 2: the decision board, a view of the standalone page (🗂 Decisions,
/?view=decisions). The board (membrane/ui/decisions.js, DecisionLogic) lays the
Decision cards out as a reversed kanban: decided on the left, in flight in the
middle, open questions on the right (a card that blocks another above it). An
open card's one variable is entered on the card, pre-filled with the agent's
pick; clicking the card opens its chat with a message about it pre-filled."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr, f="decisions.js", name="D"):
    js = ('const %s=require(%r); process.stdout.write(JSON.stringify(%s))' % (name, str(UI / f), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def card(eid, status="open", title=None, **kw):
    return {"eid": eid, "title": title or eid, "description": "", "status": status, **kw}


BOARD = {
    "cards": [
        card("c:release", title="Release when", var={"type": "string"}),
        card("c:limit", title="Turn limit", var={"type": "int", "unit": "s"}, pick=1800),
        card("c:banner", "in_flight", title="Banner window"),
        card("c:old", "decided", title="Watchdog", value=300, var={"type": "int", "unit": "s"},
             set_at="2026-10-20T10:00:00Z"),
        card("c:new", "decided", title="Converge", value=True, var={"type": "bool"},
             set_at="2026-10-23T10:00:00Z"),
    ],
    "edges": [
        {"from": "c:limit", "to": "c:release", "rel": "blocks"},
        {"from": "c:banner", "to": "PR #468", "rel": "carried_by"},
        {"from": "c:old", "to": "c:banner", "rel": "led_to"},
    ],
}


@NODE
def test_columns_run_decided_in_flight_open():
    cols = _js("D.columns(%s)" % json.dumps(BOARD))
    assert [c["eid"] for c in cols["decided"]] == ["c:new", "c:old"]      # the newest first
    assert [c["eid"] for c in cols["in_flight"]] == ["c:banner"]
    # "Release when" sorts first by title, but the limit blocks it: the limit comes first
    assert [c["eid"] for c in cols["open"]] == ["c:limit", "c:release"]
    assert [k["key"] for k in _js("D.COLS")] == ["decided", "in_flight", "open"]


@NODE
def test_a_blocking_cycle_still_places_every_card():
    cards = [card("a"), card("b")]
    edges = [{"from": "a", "to": "b", "rel": "blocks"}, {"from": "b", "to": "a", "rel": "blocks"}]
    out = _js("D.blockersFirst(%s, %s)" % (json.dumps(cards), json.dumps(edges)))
    assert sorted(c["eid"] for c in out) == ["a", "b"]


@NODE
def test_edges_between_cards_are_lines_the_rest_are_chips():
    out = _js("D.cardEdges(%s)" % json.dumps(BOARD))
    assert {(e["from"], e["to"]) for e in out["lines"]} == {("c:limit", "c:release"), ("c:old", "c:banner")}
    assert out["chips"] == {"c:banner": [{"rel": "carried_by", "to": "PR #468"}]}


@NODE
def test_one_input_per_type_prefilled_with_the_pick():
    assert _js('D.inputSpec({type: "int"})') == {"kind": "number", "step": "1"}
    assert _js('D.inputSpec({type: "float"})') == {"kind": "number", "step": "any"}
    assert _js('D.inputSpec({type: "bool"})') == {"kind": "select", "options": ["yes", "no"]}
    assert _js('D.inputSpec({type: "option", options: ["one PR", "three PRs"]})') == \
        {"kind": "select", "options": ["one PR", "three PRs"]}
    assert _js('D.inputSpec({type: "string"})') == {"kind": "text"}
    assert _js('D.inputSpec(null)') is None
    assert _js('D.pickText({type: "int"}, 1800)') == "1800"
    assert _js('D.pickText({type: "bool"}, true)') == "yes"
    assert _js('D.pickText({type: "bool"}, false)') == "no"
    assert _js('D.pickText({type: "string"}, null)') == ""


@NODE
def test_the_chat_opens_with_the_decision_prefilled():
    t = _js("D.askText(%s)" % json.dumps(BOARD["cards"][1]))
    assert '"Turn limit"' in t and "int" in t and "(s)" in t and "1800" in t
    d = _js("D.askText(%s)" % json.dumps(BOARD["cards"][3]))
    assert "Decided: 300 s" in d
    assert _js("D.valueText(%s)" % json.dumps(BOARD["cards"][4])) == "yes"


# ---- the view in membrane home (home.js, HomeLogic) ---------------------------------

@NODE
def test_decisions_is_a_view_of_the_standalone_page():
    assert _js('H.knownView("decisions")', "home.js", "H") == "decisions"
    assert _js('H.knownView("chat")', "home.js", "H") == "chat"
    assert _js('H.knownView("graph")', "home.js", "H") == "graph"
    assert _js('H.knownView("bogus")', "home.js", "H") == ""


@NODE
def test_the_standalone_menu_lists_the_decisions_view():
    rows = _js('H.standaloneMenu("")', "home.js", "H")
    assert [r["v"] for r in rows] == ["chat", "graph", "decisions"]
    assert rows[2]["title"] == "Decisions"
    here = _js('H.standaloneMenu("decisions")', "home.js", "H")
    assert [r["v"] for r in here] == ["chat", "graph", "decisions", ""]      # and back to everything
    assert [r["v"] for r in here if r["current"]] == ["decisions"]


def test_the_page_loads_the_board_only_in_its_view():
    src = (UI / "home.js").read_text()
    assert '"/ui/www/decisions.js"' in src
    assert "membrane-decisions" in src
