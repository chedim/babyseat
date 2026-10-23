"""#470 round 2: the decision cockpit draws the cards as a DEPENDENCY GRAPH --
"chomping through uncertainty by making decisions". Past decisions led_to the
in-flight work, which depends on (is blocked by) the open decisions above it.
The bands stay; within them the cards' SIDES follow the graph (a card goes to
the side its neighbours are on, the two sides kept balanced), the in-flight
cards are ordered by their neighbours' rows, and every card->card edge is a
line (non-card targets stay chips). Pure helpers in DecisionLogic, under node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr):
    js = 'const D=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "decisions.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def card(eid, status, **kw):
    return {"eid": eid, "title": eid, "description": "", "status": status, **kw}


def E(a, b, rel):
    return {"from": a, "to": b, "rel": rel}


# a little graph: three decisions led to two pieces of work, which three open questions block
GRAPH = {
    "cards": [
        card("p1", "decided", set_at="2026-10-21T00:00:00Z"),
        card("p2", "decided", set_at="2026-10-22T00:00:00Z"),
        card("p3", "decided", set_at="2026-10-23T00:00:00Z"),
        card("n1", "in_flight"), card("n2", "in_flight"),
        card("o1", "open"), card("o2", "open"), card("o3", "open"),
    ],
    "edges": [
        E("p1", "n1", "led_to"), E("p3", "n1", "led_to"), E("p2", "n2", "led_to"),
        E("o1", "n1", "blocks"), E("o2", "n2", "blocks"), E("o3", "o1", "blocks"),
        E("n2", "PR #468", "carried_by"),
    ],
}


def ids(side_lists):
    return {k: [c["eid"] for c in v] for k, v in side_lists.items()}


def _layout(board, opts="{}"):
    return {band: ids(v) for band, v in _js("D.graphLayout(%s, %s)" % (json.dumps(board), opts)).items()
            if band in ("future", "now", "past")}


@NODE
def test_linked_cards_share_a_side():
    L = _layout(GRAPH)
    assert L["now"] == {"left": ["n1"], "right": ["n2"]}
    # n1's whole chain stays left (p1, p3 led to it; o1 blocks it; o3 blocks o1), n2's right
    assert L["past"] == {"left": ["p3", "p1"], "right": ["p2"]}          # newest nearest the bar
    assert L["future"] == {"left": ["o3", "o1"], "right": ["o2"]}        # the first to decide nearest the bar


@NODE
def test_the_graph_uncrosses_what_alternation_would_cross():
    # pure alternation puts a (first by title) left and b right: both lines would cross the center
    board = {"cards": [card("n1", "in_flight"), card("n2", "in_flight"), card("a", "open"), card("b", "open")],
             "edges": [E("a", "n2", "blocks"), E("b", "n1", "blocks")]}
    L = _layout(board)
    assert L["now"] == {"left": ["n1"], "right": ["n2"]}
    assert L["future"] == {"left": ["b"], "right": ["a"]}


@NODE
def test_the_sides_stay_balanced():
    board = {"cards": [card("n1", "in_flight")] + [card("o%d" % i, "open") for i in range(3)],
             "edges": [E("o%d" % i, "n1", "blocks") for i in range(3)]}
    L = _layout(board)
    assert (len(L["future"]["left"]), len(L["future"]["right"])) == (2, 1)


@NODE
def test_in_flight_cards_are_ordered_by_their_neighbours_rows():
    # three pieces of work, two fed by decisions of different ages
    board = {"cards": [card("na", "in_flight"), card("nb", "in_flight"), card("nc", "in_flight"),
                       card("old", "decided", set_at="2026-10-01T00:00:00Z"),
                       card("new", "decided", set_at="2026-10-20T00:00:00Z")],
             "edges": [E("old", "na", "led_to"), E("new", "nc", "led_to")]}
    L = _layout(board)
    # the balance sends "old" right (one past card a side), so its work "na" follows it there;
    # on the left "nc" (fed by the decision nearest the bar) comes before "nb", which has no links
    assert L["past"] == {"left": ["new"], "right": ["old"]}
    assert L["now"] == {"left": ["nc", "nb"], "right": ["na"]}


@NODE
def test_the_layout_is_deterministic():
    shuffled = dict(GRAPH, cards=list(reversed(GRAPH["cards"])), edges=list(reversed(GRAPH["edges"])))
    assert _layout(shuffled) == _layout(GRAPH)


@NODE
def test_a_phone_is_one_column():
    L = _layout(GRAPH, '{"narrow": true}')
    assert all(v["right"] == [] for v in L.values())
    assert L["future"]["left"] == ["o2", "o3", "o1"]          # the band's own order: o3 before the o1 it blocks


@NODE
def test_the_past_pushes_out_its_oldest_first():
    out = _js('D.graphLayout(%s, {"pastRows": 1})' % json.dumps(GRAPH))
    shown = [c["eid"] for c in out["past"]["left"] + out["past"]["right"]]
    assert sorted(shown) == ["p2", "p3"] and out["gone"] == 1


@NODE
def test_edges_wear_the_hud_palette():
    bandOf = '{"p1": "past", "n1": "now", "o1": "future", "o3": "future"}'
    assert _js('D.edgeStyle({"from": "p1", "to": "n1", "rel": "led_to"}, %s)' % bandOf) == {"tone": "amber", "dashed": False}
    assert _js('D.edgeStyle({"from": "o1", "to": "n1", "rel": "blocks"}, %s)' % bandOf) == {"tone": "cyan", "dashed": True}
    assert _js('D.edgeStyle({"from": "o3", "to": "o1", "rel": "blocks"}, %s)' % bandOf) == {"tone": "cyan", "dashed": True}


@NODE
def test_hovering_a_card_lights_its_neighbours():
    assert _js('D.graphNeighbors(%s, "n1")' % json.dumps(GRAPH)) == ["o1", "p1", "p3"]
    assert _js('D.graphNeighbors(%s, "n2")' % json.dumps(GRAPH)) == ["o2", "p2"]     # "PR #468" is no card


def test_lines_are_drawn_under_the_cards_and_fade_under_the_center():
    src = (UI / "decisions.js").read_text()
    assert '<svg class="graph"' in src
    assert "mask" in src                         # the center column's bar and chat stay clear
    assert "graphLayout(" in src and "edgeStyle(" in src and "graphNeighbors(" in src


@NODE
def test_a_question_linked_only_within_its_band_follows_its_neighbour():
    # "a" blocks only "z" (another question), which blocks the work on the right: "a" goes right too,
    # even though, first by title, it would have been placed (left) before "z" was
    board = {"cards": [card("n1", "in_flight"), card("n2", "in_flight"),
                       card("a", "open"), card("b", "open"), card("c", "open"), card("z", "open")],
             "edges": [E("a", "z", "blocks"), E("z", "n2", "blocks"), E("b", "n1", "blocks"), E("c", "n1", "blocks")]}
    L = _layout(board)
    assert L["future"] == {"left": ["b", "c"], "right": ["a", "z"]}
