"""Agent events: the Cortex's sensing layer -- the thing that replaces rosetta-check's
ANTLR ``CodeEvent`` walker.

rosetta-check emits one ``CodeEvent`` per source statement (static analysis of a
program). The agent-Cortex emits one ``AgentEvent`` per ``tool_use`` block the agent
emits (a *decision* -- the thing the variety-field measures), paired with its
``tool_result`` (the *outcome*) on the next request. The text the field embeds is the
tool name + its input -- the decision itself -- never the outcome.

This module holds:

* ``AgentEvent`` -- the record (replaces rosetta's ``CodeEvent``: sourceFile/line/col/
  text/kind/scope -> session/turn/tool_name/input_text/result_text/is_error/scope).
* ``ToolUseCollector`` -- the SSE-stream parser that reconstructs tool calls from the
  Anthropic ``/v1/messages`` stream (``content_block_start`` tool_use +
  ``content_block_delta`` input_json_delta -> a completed ``(tool_use_id, name,
  input_text)`` on ``content_block_stop``).
* ``user_tool_results`` -- the user-message walker that pairs a prior decision with
  its tool_result (the harness returns tool_result as a user message before the model
  continues).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# scope: SESSION would seed the field (the task/environment baseline, like rosetta's
# FILE event); ACTION is a nudge (a decision, like rosetta's STATEMENT event). Phase 1
# seeds the field on the first ACTION (no separate SESSION event); SESSION is kept on
# the enum so Phase 2 can seed the task premise.
SESSION = "session"
ACTION = "action"


@dataclass
class AgentEvent:
    session: str            # a session id (the request's first message hash, or "")
    turn: int               # monotonic decision index in the session
    tool_name: str          # the tool the agent called (Bash/Edit/Read/...)
    input_text: str = ""    # the tool_use input (command / file / content)
    result_text: str = ""   # the tool_result (paired on the NEXT request); "" until then
    is_error: bool = False  # the tool_result is_error flag (the environment pushed back)
    scope: str = ACTION
    tool_use_id: str = ""   # the Anthropic tool_use id -- pairs with tool_result.tool_use_id

    def embed_text(self) -> str:
        """The text the variety-field embeds: the tool name + its input -- the DECISION.
        The outcome (``result_text``) is metadata on the event, NOT part of the embedding:
        the field measures the variety of what the agent chose to do, and an
        outcome-dependent embedding would let a failed call's trace masquerade as a
        distinct decision (collapsing the field precisely when it should diverge)."""
        inp = (self.input_text or "").strip().replace("\n", " ")
        return f"{self.tool_name}: {inp}" if inp else self.tool_name


class ToolUseCollector:
    """Accumulate ``tool_use`` blocks from an Anthropic SSE stream into completed
    tool calls.

    Anthropic streams a tool_use as:

        content_block_start  {content_block: {type: "tool_use", id, name, input: {}}}
        content_block_delta  {delta: {type: "input_json_delta", partial_json: ...}} ...
        content_block_stop

    The ``input`` arrives as ``input_json_delta`` fragments that must be concatenated
    (they are partial JSON strings, not incremental objects). Feed each parsed SSE
    event payload via ``feed(obj)``; on a tool_use block's ``content_block_stop`` it
    returns ``(tool_use_id, name, input_text)``. Text blocks are ignored (the membrane
    already tees ``text_delta``); other block types (thinking, etc.) are ignored --
    only the agent's tool *decisions* are sensed.
    """

    def __init__(self) -> None:
        self._blocks: dict[int, dict] = {}   # index -> {id, name, parts: [str]}

    def feed(self, obj: dict) -> Optional[tuple[str, str, str]]:
        """Process one SSE event dict. Returns ``(tool_use_id, name, input_text)`` when a
        tool_use block completes, else None."""
        t = obj.get("type")
        if t == "content_block_start":
            cb = obj.get("content_block") or {}
            if cb.get("type") == "tool_use":
                self._blocks[obj.get("index")] = {
                    "id": cb.get("id", ""), "name": cb.get("name", "?"), "parts": []}
        elif t == "content_block_delta":
            d = obj.get("delta") or {}
            if d.get("type") == "input_json_delta":
                blk = self._blocks.get(obj.get("index"))
                if blk is not None:
                    blk["parts"].append(d.get("partial_json", ""))
        elif t == "content_block_stop":
            blk = self._blocks.pop(obj.get("index"), None)
            if blk is not None:
                return (blk["id"], blk["name"], "".join(blk["parts"]))
        return None

    def open_blocks(self) -> list[int]:
        """Block indices still accumulating (a stream that ended mid-tool)."""
        return list(self._blocks.keys())


def user_tool_results(request_body: dict) -> list[dict]:
    """The ``tool_result`` blocks the harness sent back in the LATEST user message --
    the outcomes of the agent's most recent tool calls, in order. Each is
    ``{tool_use_id, is_error, content}``. Walks the content blocks of the last user
    message only: the outcome corresponds to the most recent assistant turn, and an
    earlier user message's tool_results belong to a prior turn already stamped."""
    msgs = request_body.get("messages", [])
    for m in reversed(msgs):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, list):
            out = [b for b in c if isinstance(b, dict)
                   and b.get("type") == "tool_result"]
            if out:
                return out
        # a text-only user message (no tool_results): keep scanning earlier user
        # messages -- the tool_result for the most recent assistant turn may sit
        # behind a trailing text turn (e.g. a follow-up the harness appended).
    return []