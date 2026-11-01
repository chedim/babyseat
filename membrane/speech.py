"""WHAT MEMBRANE SAYS (operator, 2026-11-01: "membrane-spoken templates"): every line the
seat itself posts into a chat is one named template here, filled from the facts of the
subgraph it is about (a card, a goal, its nodes, the readings of a word). No model writes
these; the wording lives in one place."""
from __future__ import annotations

ACT_HINT = ("Check the receipts against what is live now (the PR head, its CI); close the goal "
            "(goal action='close') with the receipt, or send it back (entity_edit its replan with "
            "what is missing).")


def _or(options) -> str:
    opts = [str(o) for o in options]
    return opts[0] if len(opts) == 1 else ", ".join(opts[:-1]) + " or " + opts[-1]


def _decided(title, value) -> str:
    return f"[decision] {title} = {value}"


def _dropped(title) -> str:
    return f"[decision] {title} = dropped"


def _goal_fulfilled(goal, receipt) -> str:
    return f"goal {goal} fulfilled: {receipt}"


def _goal_ended(goal, name, head, rows) -> str:
    return (f"goal {goal} ({name}): {head}.\n" + "\n".join(rows)[:1500] + "\n" + ACT_HINT)


def _escalation(ent, head, line="") -> str:
    return (f"[watch] {ent or 'field'}: {head}"
            + (f"\ntriage: {line}" if line else "\n(no escalate_when note on it: sent as is)"))


def _asks(items) -> str:
    return "Open decisions:\n" + "\n".join(f"{n}. {q}" for n, q in items)


def _reask(word, options) -> str:
    return f'"{word}": {_or(options)}?'


TEMPLATES = {"decided": _decided, "dropped": _dropped, "goal-fulfilled": _goal_fulfilled,
             "goal-ended": _goal_ended, "escalation": _escalation, "asks": _asks, "reask": _reask}


def say(template: str, /, **facts) -> str:
    return TEMPLATES[template](**facts)
