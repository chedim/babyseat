"""#569: a loop lane's turn gets a slimmer tool set than a person's chat."""
from membrane import claude_agent as ca, diag_requests as dr


def test_loop_lane_has_fewer_tools_than_chat():
    dr.context(lane="chat")
    chat = ca.tool_names()
    dr.context(lane="loop")
    try:
        loop = ca.tool_names()
    finally:
        dr.context(lane=None)
    assert 0 < len(loop) < len(chat)
    assert set(loop) <= set(chat)
