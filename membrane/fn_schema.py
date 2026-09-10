"""THE FN SCHEMA (Phase T): the typed transition layer -- every actuation
cell gains `extra["fn"] = {op, binds, returns}`, parsed at capture time
from the tool record's (name, args, result) triple. The fn fields ride
`extra{}` alongside the existing entity tags (zero migration), and they
are the planner's typed search space and the distiller's structured
training pairs.

THE EXTRACTION (E9-validated): the tool-name dispatch from the receipt
lane's classifier (edit-ack / edit-fail / bash-echo, 945 byte-exact in
the audit replay). The op comes from the tool name; the binds from the
args JSON; the returns from the paired result. Deterministic classes
only -- the ambiguous/variable classes (bash-call-echo) fall through
(the degrade covenant).
"""
from __future__ import annotations

import json
import re

# the tool names whose args parse as fn schemas (the known shapes)
_KNOWN_OPS = ("edit", "bash", "read", "write", "membrane_query",
              "membrane_learn", "membrane_state", "membrane_forget",
              "membrane_watch_entity", "membrane_unwatch_entity",
              "membrane_list_tracks", "membrane_related_entities",
              "membrane_entity_add", "membrane_entity_edit",
              "membrane_entity_remove", "membrane_set_junk_cut",
              "membrane_peer_post", "membrane_peer_seats",
              "membrane_entity_inherit")


def _safe_json(text: str) -> dict:
    """Parse a JSON object body; None on failure (the silent guard)."""
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def extract_fn(name: str, args: str, result: str) -> dict | None:
    """Parse the fn schema from one captured tool record's
    (name, args, result) triple. Returns {op, binds, returns} or None
    (non-actuation content or a result we can't structure)."""
    if name not in _KNOWN_OPS:
        return None
    # the args: a JSON body the capture stores (the receipt lane's input)
    binds = _parse_args(args) or {}
    if binds is None:
        return None
    return {"op": name, "binds": binds, "returns": (result or "")[:2000]}


def _parse_args(args: str) -> dict | None:
    """The tool call's arguments: a JSON body the capture pipeline stores."""
    text = (args or "").strip()
    if text.startswith("args: "):
        text = text[6:]
    if text.startswith("{"):
        try:
            v = json.loads(text)
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def extract_fn_from_record(rec: dict) -> dict | None:
    """The fn extraction from one capture-tool record (the shape
    _capture_plan_and_calls stores): {name, args, query, result}."""
    name = rec.get("name") or ""
    args = rec.get("args") or ""
    result = rec.get("result") or ""
    return extract_fn(name, args, result)


def extract_fn_from_exemplar(situation: str, action: str) -> dict | None:
    """The fn extraction from a CELL's text (the learn-time pass): the
    capture format 'tool <name> for: <query> / args: {...}' -> result.
    The audit's receipt turns carry this exact shape."""
    sit = (situation or "").strip()
    for name in _KNOWN_OPS:
        prefix = f"tool {name} for: "
        if sit.startswith(prefix):
            # the args are in the situation after 'args: '
            args_start = sit.find("args: ")
            args_str = sit[args_start + 6:] if args_start >= 0 else ""
            return {"op": name, "binds": _parse_args(args_str) or {},
                    "returns": (action or "")[:200]}
    return None
