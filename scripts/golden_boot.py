#!/usr/bin/env python
"""Golden fixtures for the page's boot-time routes (#1143, swap step 3): the sibling of scripts/golden_session.py.

Records what the RELEASED Python code (tag v0.7.53) answers on the routes the page calls when it boots and for its field views, so the cliffvm
door has a target. Nothing here calls a model, a network or a seat: each answer is the released FUNCTION that builds it, called with the empty
inputs a fresh seat has (no field, no self, no quota pause, no Minds), exactly as the handler calls it:

    agent          server._agent_shapes(None)               GET /agent                      (server.py:4621)
    options        options.read(None)                       GET /membrane/options           (server.py:4554)
    options_bg     options._background(<a stub seat>)       the `background` object of the same answer, with the seat's minds stubbed
    quota          quota.status(None, None)                 GET /membrane/quota             (server.py:4568)
    blurp          ch_blurp.overlay(<a seat with no VM>)    GET /membrane/map/blurp         (lanes.py:4649)
    field3d        field3d.build(no rows) + root + current  GET /membrane/map/field3d       (lanes.py:4188 field3d_answer, an empty field)
    voice_state    voice_io.set_state(<a stub seat>, ...)   POST /membrane/voice/state      (server.py:4765)
    map_field      the literal the handler writes when the field is not loaded (lanes.py:5013)
    map_search     the literal the handler writes with no entity store (lanes.py:5144)

`/config` is NOT recorded: the released answer is the opencode config `opencode.py` writes at launch (a file the launcher builds, not a handler's
computation); the page reads only `model` of it (panel.js `_loadModel`), and the test pins that.

    git archive v0.7.53 | tar -x -C <empty dir>
    python3 scripts/golden_boot.py --tree <that dir>          # writes tests/golden/boot/*.json
    python3 scripts/golden_boot.py --tree <that dir> --check  # exit 1 when the committed files differ in SHAPE

(numpy is needed by the released field3d, so no `-I`.) The tree is the ORACLE, read with `git show v0.7.53:<path>`. Values stay in place; the
consumer compares keys and types only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import types
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "golden" / "boot"


def shape(v):
    """the type tree of a JSON value: a dict -> {key: shape}, a list -> [shape of its first], else the type's name"""
    if isinstance(v, dict):
        return {k: shape(x) for k, x in v.items()}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    return "null" if v is None else type(v).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    home = tempfile.mkdtemp()
    os.environ.update(HOME=home, MEMBRANE_STORE="json", MEMBRANE_RUN="0", MEMBRANE_DATA_DIR=home, MEMBRANE_JWT="0", MEMBRANE_OFFLINE_PROBE="0")
    sys.path.insert(0, a.tree)
    import membrane.server as NS
    from membrane import ch_blurp, claude_agent as CA, field3d, fieldpulse, minds_models as MM, options, quota, voice_io

    out: dict = {}
    out["agent"] = {"status": 200, "body": NS._agent_shapes(None)}
    out["options"] = {"status": 200, "body": options.read(None)}

    # the `background` object needs a seat with a loop runner and its minds: stubbed the way the seat answers them with the claude rung picked
    seat = types.SimpleNamespace(_loop_runner=object())
    MM.read = lambda srv: {"loops": "claude", "cortex": "claude", "loops_auth": "chat", "cortex_auth": "chat"}
    MM.pickable = lambda srv: ["claude"]
    MM.picked = lambda srv: True
    CA.is_claude_rung = lambda srv, m: True
    CA.auth_mode = lambda: CA.MODE_KEY
    out["options_bg"] = {"status": 200, "body": options._background(seat)}

    out["quota"] = {"status": 200, "body": quota.status(None, None)}
    out["blurp"] = {"status": 200, "body": ch_blurp.overlay(types.SimpleNamespace())}

    proj = field3d.build(lambda: iter([]), lambda s: s, "")
    f3 = proj.meta()
    f3["root"] = proj.tile(0, 0, 0, 0)
    f3["current"] = {"p": None, "tau_s": fieldpulse.current_tau_s()}
    out["field3d"] = {"status": 200, "body": f3}

    class _Ent:
        def __init__(self):
            self.properties = {}
    ents: dict = {}
    voice_io.entity = lambda srv, kind, person: ents.setdefault(kind, _Ent())
    out["voice_state"] = {"status": 200, "body": {"ok": True, "state": voice_io.set_state(None, "ann", in_on=True, in_mode="ptt", out_on=False, device="phone")}}

    out["map_field"] = {"status": 200, "body": {"axes": [], "nodes": [], "edges": []}}
    out["map_search"] = {"status": 200, "body": {"q": "", "hits": []}}

    OUT.mkdir(parents=True, exist_ok=True)
    bad = 0
    for k, v in out.items():
        path = OUT / f"{k}.json"
        if a.check:
            old = json.loads(path.read_text()) if path.exists() else None
            if old is None or shape(old) != shape(v):
                print("differs in shape:", k)
                bad += 1
        else:
            path.write_text(json.dumps(v, indent=1, sort_keys=False) + "\n")
    if not a.check:
        print(f"wrote {len(out)} fixtures to {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
