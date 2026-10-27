"""--model is the operator's pick: an explicit model that ollama's tag list
doesn't carry (a cloud model answers unpulled) must not be swapped for the
first family match (glm-5.3:cloud became glm-5.3-flash:cloud, 2026-10-07)."""
from membrane.opencode import _pick_default_model

TAGS = ["qwen3:8b", "glm-5.3-flash:cloud", "granite4:latest"]


def test_explicit_listed_wins():
    assert _pick_default_model(TAGS, "granite4:latest") == "granite4:latest"
    assert _pick_default_model(TAGS, "qwen3") == "qwen3:8b"


def test_explicit_unlisted_is_not_swapped():
    assert _pick_default_model(TAGS, "glm-5.3:cloud") is None


def test_no_preference_uses_family_order():
    # #600: with nobody's pick, a cloud model is never the default (a fresh seat
    # locked up on glm-5.3-flash:cloud's rate limit); the family order runs on local ones
    assert _pick_default_model(TAGS, None) == "qwen3:8b"
    assert _pick_default_model(["glm-4.7:local", "qwen3:8b"], None) == "glm-4.7:local"
    assert _pick_default_model([], None) is None
