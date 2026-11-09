"""#980 rows 12-15: script texts that live ON their entities, shipped as a release seed pack.

The texts are the single source: the creators (autonode, night, chat_io) import them from here and
pack() hands the same text to seedpack.load(release=True). Missing primitive (Q2): concept:<path> and
chat:<person> are created per instance, so a pack cannot list them; their text rides template
entities (tpl:concept-drift, tpl:chat-io) until a class-level script (ISA inheritance of getter_script) exists.
"""
from __future__ import annotations

FILETREE_WATCH_SCRIPT = (
    'import time as _t\n'
    "x = state.get('extra') or {}\n"
    "py = x.get('payload') or {}\n"
    "ev = py.get('event') or x.get('kind') or '?'\n"
    "pth = py.get('path') or py.get('topic') or py.get('name') or ''\n"
    "result = {'render': 'filetree: ' + str(ev) + ' ' + str(pth),\n"
    "          'patch': {'script_data': {'last_change': {'event': str(ev),\n"
    "                                                  'path': str(pth),\n"
    "                                                  'ts': _t.time()}}}}"
)

CONCEPT_DRIFT_GETTER = (
    'props = state.get("properties") or {}\n'
    'defined_in = props.get("defined_in") or {}\n'
    'definition = str(props.get("definition") or "")\n'
    'path = str(defined_in.get("file") or "")\n'
    'line = defined_in.get("line")\n'
    'if not path:\n'
    '    result = {"state": {"drift": None, "error": "no defined_in.file",\n'
    '                        "definition": definition,\n'
    '                        "checked_at": state.get("now")}}\n'
    'else:\n'
    '    try:\n'
    '        with open(path, "r", encoding="utf-8", errors="replace") as _fh:\n'
    '            lines = _fh.read().splitlines()\n'
    '    except OSError as _e:\n'
    '        result = {"state": {"drift": None,\n'
    '                            "error": "%s: %s" % (type(_e).__name__, _e)[:200],\n'
    '                            "definition": definition,\n'
    '                            "checked_at": state.get("now")}}\n'
    '    else:\n'
    '        start, end = 0, len(lines)\n'
    '        if line is not None:\n'
    '            try:\n'
    '                start = max(0, int(line) - 1)\n'
    '                end = min(len(lines), start + 8)\n'
    '            except (TypeError, ValueError):\n'
    '                pass\n'
    '        window = " ".join(" ".join(lines[start:end]).split())\n'
    '        norm = " ".join(definition.split())\n'
    '        result = {"state": {"drift": bool(norm) and norm not in window,\n'
    '                            "current": window[:400],\n'
    '                            "definition": definition,\n'
    '                            "checked_at": state.get("now")}}\n'
)

CLOCK_TICK_SCRIPT = (
    'import time as _t\n'
    "now = _t.strftime('%H:%M:%S')\n"
    "result = {'render': 'clock ' + now, 'patch': {'script_data': {'last_tick': now}}}"
)

CHAT_IO_GETTER = (
    "result = {'state': {'heard_n': int(membrane.me.properties.get('heard_n') or 0)}}"
)


NAME = "scripts-rows-12-15"
VERSION = 1
NS = "membrane::"


def pack() -> dict:
    return {"name": NAME, "version": VERSION, "entities": [
        {"eid": NS + "tool:filetree", "cls": "Tool", "properties": {"watch_script": FILETREE_WATCH_SCRIPT}},
        {"eid": NS + "tool:clock", "cls": "Tool", "properties": {"tick_script": CLOCK_TICK_SCRIPT}},
        {"eid": NS + "tpl:concept-drift", "cls": "Other", "properties": {"getter_script": CONCEPT_DRIFT_GETTER, "silent_birth": True}},
        {"eid": NS + "tpl:chat-io", "cls": "Other", "properties": {"getter_script": CHAT_IO_GETTER, "silent_birth": True}},
    ]}


def install(est) -> dict:
    from . import seedpack
    return seedpack.load(est, pack(), release=True)
