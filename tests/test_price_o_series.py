import pytest
from membrane.router import table_price, CLOUD_PRICE


@pytest.mark.parametrize("name", ["o1", "o3", "o4-mini", "openai/o3-mini", "o1-mini:cloud"])
def test_o_series_has_own_row(name):
    assert table_price(name) is not None


@pytest.mark.parametrize("name,want", [
    ("qwen3-coder", 1.5), ("llama3.1", 1.5), ("gpt-4o", 5.0), ("gpt-4o-mini", 5.0),
    ("deepseek-r1", 1.5), ("phi4", None), ("yolo1", None), ("foo3", None),
])
def test_short_fragments_do_not_collide(name, want):
    assert table_price(name) == want


def test_o_series_not_cheaper_than_gpt():
    assert table_price("o1") >= table_price("gpt-4o")
    assert table_price("o3") >= table_price("gpt-4o")
