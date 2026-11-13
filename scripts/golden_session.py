#!/usr/bin/env python
"""Golden fixtures for the opencode session API (#1143, swap step 2).

Records what the RELEASED Python seat (tag v0.7.53) answers on the session routes and puts on its event
stream, so the cliffvm door has a target. Nothing here calls a model: the turn runner's ladder is a stub that
streams the way the claude rung does (`on_event("text", ...)`, `("tool_start", ...)`, `("tool", ...)`), as the
repo's own tests do (`tests/test_claude_agent_tool_start.py`, `tests/test_server.py`).

    git archive v0.7.53 | tar -x -C <empty dir>
    python3 -I scripts/golden_session.py --tree <that dir>          # writes tests/golden/session/*.json
    python3 -I scripts/golden_session.py --tree <that dir> --check  # exit 1 when the committed files differ in SHAPE

The tree is the ORACLE: read with `git show v0.7.53:<path>`. The fixtures hold the recorded JSON with the volatile
values (ids, times, cwd) left in place: the consumer compares keys and types only (docs/cliffvm.md, "The session
API", the shape comparer), never values. HOME is a scratch folder (the seat token the handler wants is made there).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "golden" / "session"


def shape(v):
    """the type tree of a JSON value: a dict -> {key: shape}, a list -> [shape of its first], else the type's name"""
    if isinstance(v, dict):
        return {("<session id>" if k.startswith("ses_") else k): shape(x) for k, x in v.items()}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    return "null" if v is None else type(v).__name__


# keys of the Python rows that are the Python seat's own bookkeeping (one chat per person, the announce mark): not the contract
INTERNAL = ("owner", "_announced", "readonly")


def scrub(v, cwd: str):
    """drop the internal keys and name the working folder `/srv/seat` (no machine's path goes into a fixture)"""
    if isinstance(v, dict):
        return {k: scrub(x, cwd) for k, x in v.items() if k not in INTERNAL}
    if isinstance(v, list):
        return [scrub(x, cwd) for x in v]
    if isinstance(v, str):
        return v.replace(cwd, "/srv/seat").replace(cwd.lstrip("/"), "srv/seat")
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    home = tempfile.mkdtemp()
    work = Path(tempfile.mkdtemp()).resolve()
    os.chdir(work)                      # the session's `directory` is the cwd: a scratch folder, never the author's
    os.environ.update(HOME=home, MEMBRANE_STORE="json", MEMBRANE_RUN="0", MEMBRANE_DATA_DIR=home, MEMBRANE_JWT="0", MEMBRANE_OFFLINE_PROBE="0",
                      MEMBRANE_SEAT_TOKEN_FILE=os.path.join(home, "seat-token"))
    sys.path.insert(0, a.tree)
    import membrane.server as NS
    from membrane import claude_agent as CA
    from membrane import seatauth

    hub = NS.SSEHub()
    sessions = NS.SessionStore()
    msgs = NS.MessageStore()
    cfg = {"provider": {"membrane": {"name": "Membrane", "npm": "@ai-sdk/openai-compatible",
                                     "options": {"baseURL": "http://127.0.0.1:9/v1"}, "models": {"claude": {"name": "claude"}}}}}
    script: dict = {"kind": "ok"}

    def ladder(body):
        raise AssertionError("buffered call not used")

    def stream(body, on_event):
        if script["kind"] == "error":
            raise RuntimeError("claude failed (rc 3): not logged in")
        on_event("text", "Looking ")
        on_event("text", "now.")
        on_event("tool_start", {"id": "toolu_1", "name": "entity_read", "input": {}})
        on_event("tool_start", {"id": "toolu_1", "name": "entity_read", "input": {"spec": "Command:probe"}})
        on_event("tool", {"id": "toolu_1", "name": "entity_read", "input": {"spec": "Command:probe"}, "output": "the result", "is_error": False})
        on_event("text", "Done.")
        return {CA.RESP_KEY: CA.PROVIDER, CA.TOOLS_KEY: [{"id": "toolu_1", "name": "entity_read", "input": {"spec": "Command:probe"}, "output": "the result", "is_error": False}],
                "choices": [{"message": {"content": "Looking now.Done."}}], "usage": {}}
    ladder.stream = stream
    runner = NS.TurnRunner(hub, sessions, msgs, ladder, lambda *x: "ok", system="", model="claude", tools=[])
    handler = NS.make_handler(hub=hub, sessions=sessions, messages=msgs, config=cfg, runner=runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = seatauth.ensure()

    def req(path, method="GET", body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else None)

    # the event streams: two clients, one bare (/event), one wrapped (/global/event); frames parsed as the page does
    frames: dict = {"/event": [], "/global/event": []}

    def reader(path):
        r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": "Bearer " + token})
        resp = urllib.request.urlopen(r, timeout=60)
        name = None
        for raw in resp:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                frames[path].append({"event": name, "data": json.loads(line[6:])})
    for p in frames:
        threading.Thread(target=reader, args=(p,), daemon=True).start()
    time.sleep(0.5)

    out: dict = {}
    st, s = req("/session", "POST", {"title": "T"})
    out["session_create"] = {"status": st, "body": s}
    sid = s["id"]
    out["session_list"] = {"status": 200, "body": req("/session")[1]}
    out["session_get"] = {"status": 200, "body": req(f"/session/{sid}")[1]}
    out["session_missing"] = {"status": req("/session/ses_nope")[0], "body": req("/session/ses_nope")[1]}
    out["session_patch"] = {"status": 200, "body": req(f"/session/{sid}", "PATCH", {"title": "Renamed"})[1]}
    out["session_status_idle"] = {"status": 200, "body": req("/session/status")[1]}
    st, _ = req(f"/session/{sid}/prompt_async", "POST", {"agent": "build", "parts": [{"type": "text", "text": "look at the probe"}]})
    out["prompt_async"] = {"status": st, "body": None}
    for _ in range(100):
        time.sleep(0.1)
        if any(f["event"] == "session.idle" for f in frames["/event"]):
            break
    time.sleep(0.3)
    st, rows = req(f"/session/{sid}/message")
    out["messages_ok"] = {"status": st, "body": rows}
    out["message_one"] = {"status": 200, "body": req(f"/session/{sid}/message/{rows[1]['info']['id']}")[1]}
    # an error turn
    script["kind"] = "error"
    n0 = len(frames["/event"])
    st, s2 = req("/session", "POST", {"title": "E"})
    # the Python seat keeps ONE chat per person: the second POST returns the first. A second session is made in the store directly.
    s2 = sessions.create(title="E")
    sid2 = s2["id"]
    req(f"/session/{sid2}/prompt_async", "POST", {"parts": [{"type": "text", "text": "this will fail"}]})
    for _ in range(100):
        time.sleep(0.1)
        if sum(1 for f in frames["/event"][n0:] if f["event"] == "session.idle") >= 1:
            break
    time.sleep(0.3)
    out["messages_error"] = {"status": 200, "body": req(f"/session/{sid2}/message")[1]}
    out["session_status_after"] = {"status": 200, "body": req("/session/status")[1]}
    out["abort"] = {"status": 200, "body": req(f"/session/{sid}/abort", "POST", {})[1]}
    out["delete"] = {"status": req(f"/session/{sid2}", "DELETE")[0], "body": None}
    out["events_bare"] = frames["/event"]
    out["events_global"] = frames["/global/event"]
    httpd.shutdown()
    out = scrub(out, str(work))

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
