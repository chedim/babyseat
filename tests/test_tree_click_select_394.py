"""#394: in the Skill tree tab the skill whose card is shown is picked by a
click (a tap; Enter/Space on a focused node, which is a button) and holds until
another skill is clicked. Hover and focus never change it; a click on empty
space or on the chosen skill keeps it; the chosen node carries `sel`; the
choice outlives the tree's refresh (by skill id) and is remembered per viewer."""
import json
import re

from tests.test_skill_tree_tab_296 import NODE, SEAT_TREE, UI, _dom


def _card_after(events):
    """Fire `events` ([kind, node id or None for empty space]) on the box; return the card and the sel'd nodes."""
    js = ("const box = find(root, 'skbox')[0];"
          "const node = (id) => find(root, 'sknode').find((e) => e.attrs['data-id'] === id);"
          "for (const [k, id] of %s) box.fire(k, id ? node(id) : box);"
          "out = {card: find(root, 'skcard')[0].textContent, sel: sel.id || null,"
          " marked: find(root, 'sknode').filter((e) => cls(e).split(/\\s+/).includes('sel')).map((e) => e.attrs['data-id'])}"
          % json.dumps(events))
    return _dom(js)


@NODE
def test_hover_and_focus_do_not_pick_a_skill():
    out = _card_after([["mouseover", "expect"], ["focusin", "meet"]])
    assert out["sel"] is None and out["marked"] == []
    assert "understand an other" not in out["card"] and "click" in out["card"]


@NODE
def test_a_click_picks_and_holds_against_hover_and_focus():
    out = _card_after([["click", "expect"], ["mouseover", "meet"], ["focusin", "feel"], ["mouseover", "speak"]])
    assert out["sel"] == "expect" and out["marked"] == ["expect"]
    assert "understand an other" in out["card"]


@NODE
def test_another_click_moves_it_and_empty_space_or_the_same_skill_keeps_it():
    out = _card_after([["click", "expect"], ["click", "meet"], ["click", None], ["click", "meet"]])
    assert out["sel"] == "meet" and out["marked"] == ["meet"]
    assert "meet" in out["card"]


@NODE
def test_the_choice_outlives_a_refresh_and_is_reported_for_remembering():
    js = ("const picks = []; sel.onPick = (id) => picks.push(id);"
          "const node = (id) => find(root, 'sknode').find((e) => e.attrs['data-id'] === id);"
          "find(root, 'skbox')[0].fire('click', node('expect'));"
          "find(root, 'skbox')[0].fire('click', node('expect'));"
          "P.renderSkillTree(doc, root, %s, sel);"
          "const n = node('expect'); doc.activeElement = node('meet');"
          "P.renderSkillTree(doc, root, %s, sel);"
          "out = {picks, sel: sel.id, card: find(root, 'skcard')[0].textContent,"
          " marked: find(root, 'sknode').filter((e) => cls(e).split(/\\s+/).includes('sel')).map((e) => e.attrs['data-id'])}"
          % (json.dumps(SEAT_TREE), json.dumps(SEAT_TREE)))
    out = _dom(js)
    assert out["picks"] == ["expect"]                  # once: a re-click of the chosen skill changes nothing
    assert out["sel"] == "expect" and out["marked"] == ["expect"] and "understand an other" in out["card"]


def test_the_renderer_listens_only_to_clicks_and_home_remembers_the_pick():
    panel = (UI / "panel.js").read_text()
    body = panel.split("function renderSkillTree", 1)[1].split("\n}\n", 1)[0]
    assert "mouseover" not in body and "focusin" not in body
    home = (UI / "home.js").read_text()
    assert "membrane.home.treesel" in home
    assert re.search(r"onPick\s*:", home), "home hands the tree a pick callback that remembers it"
