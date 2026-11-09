from membrane import convgraph, speakers as S, efgate

LINE = "[user#owner: owner]\nhello there"


def test_split_forms():
    assert S.split_speaker(LINE) == ("owner", "hello there")
    assert S.split_speaker_id(LINE) == ("owner", "owner", "hello there")
    assert S.strip_speaker_lines(LINE) == "hello there"
    assert S.prose_of({"stream": "discourse", "role": "user", "text": LINE}) == "hello there"


def test_owner_is_operator():
    ev = {"stream": "discourse", "role": "user", "text": LINE}
    assert S.speaker_of(ev) == S.OPERATOR


def test_other_sub_keeps_identity():
    ev = {"stream": "discourse", "role": "user", "text": "[user#bob-1: Bob]\nhi"}
    assert S.speaker_of(ev) == ("person:bob-1", "Bob")


def test_convgraph_words():
    assert convgraph._words(LINE) == ("owner", "hello there")
