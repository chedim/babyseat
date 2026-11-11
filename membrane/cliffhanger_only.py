"""Cliffhanger only (operator 2026-11-11, "no bash"): MEMBRANE_CLIFFHANGER_ONLY=1 closes the model's shell doors.

The model reads and changes the world through the field: cliffhanger's fs/proc stdlib (#1044, native/src/drivers.rs)
-- read(path) / listdir(path) / stat(path) getters, File:<x>.write/move/delete and Command:<x>.run effects, each
with a receipt on the entity. bash, code_run and exec(action='run') -- one door, lang.run(oneshot=True) -- refuse
with the way; the tool lists drop bash and code_run. Methods the field already holds keep running."""
from __future__ import annotations

import os

SHELL_TOOLS = {"bash", "code_run", "shell", "run_shell_command", "execute_command"}

REFUSAL = ("refused: no shell on this seat (cliffhanger only). Read the world through the field: an entity "
           "whose getter is read(path), listdir(path) or stat(path); change it with File:<name>.write / .move / "
           ".delete; run a program with Command:<name>.run = {args} (its receipt lands on the entity: rc, "
           "output). No shell does not mean no programs: define a method (method_define) or a Command: entity "
           "for git, gh, pytest..., then call it. See help('stdlib').")


def on() -> bool:
    return os.environ.get("MEMBRANE_CLIFFHANGER_ONLY", "").strip().lower() in ("1", "on", "true", "yes")


def _name(t: dict) -> str:
    return str(t.get("name") or (t.get("function") or {}).get("name") or "")


FILE_TOOLS = {"read_file", "write_file", "edit_file", "list_dir"}    # #1101: the usual file tools, no-bash only


def strip_tools(tools):
    """No-bash on: the tool list without the shell tools. Off: without the no-bash seat's file tools."""
    if not isinstance(tools, list):
        return tools
    drop = SHELL_TOOLS if on() else FILE_TOOLS
    return [t for t in tools if not (isinstance(t, dict) and _name(t).lower() in drop)]
