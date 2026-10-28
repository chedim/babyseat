"""#649 per-turn block A/B: none / conversation / entities / massage.

setup <dir>          boot one seat (conversation arm), run the history chats
                     (haiku), wait for capture, stop; <dir>/base is the snapshot
ask <dir> <arm>      copy base -> <dir>/<arm>, boot with MEMBRANE_INJECTION=<arm>
                     (none = --no-banner), ask the questions (sonnet), stop
score <dir>          table per arm
Never committed (operator data rules); fictional facts only.
"""
import json, os, shutil, subprocess, sys, time, urllib.request

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the checkout
PY = os.path.expanduser(os.environ.get("B649_PY", "~/.membrane/venv/bin/python"))   # a venv with claude_agent_sdk
SERVE, LISTEN = 4196, 11596
SETUP_MODEL, ASK_MODEL = "claude-agent:haiku", "claude-agent:sonnet"
HERE = os.path.dirname(os.path.abspath(__file__))
TOPICS = json.load(open(os.path.join(HERE, os.environ.get("B649_TOPICS", "topics.json"))))


TOOLS_Q = ("answer from what you know without tools (say unknown if you don't know)",
           "answer from what you know or from membrane's memory tools (entity_read, query), "
           "but do not read or search files (say unknown if you don't know)")


def _core(arm):
    a = arm[:-2] if arm.endswith("-t") else arm
    return a.split("+")[0]                                      # facts@4000+ncp -> facts@4000


def hist_of(arm):
    """'' (full history), 'nc' (--no-context: the field's reconstruction
    rides), 'ncp' (--no-context, no reconstruction: recall + the message)."""
    a = arm[:-2] if arm.endswith("-t") else arm
    return a.split("+")[1] if "+" in a else ""


def base_arm(arm):
    return _core(arm).split("@")[0]                             # entities@1800 -> entities


def budget_of(arm):
    a = _core(arm)
    return a.split("@")[1] if "@" in a else ""


def boot(d, arm):
    env = dict(os.environ)
    env.update({"MEMBRANE_DATA_DIR": d, "MEMBRANE_PEERS_PATH": os.path.join(d, "peers.jsonl"),
                "PYTHONPATH": WT, "MEMBRANE_WATCH_LOOPS": "0",
                "MEMBRANE_INJECTION": "conversation" if base_arm(arm) == "none" else base_arm(arm),
                "MEMBRANE_EXP_DUMP": os.path.join(d, "dump.jsonl"),
                "MEMBRANE_EXP_BUDGET": budget_of(arm),
                "MEMBRANE_EXP_NORECON": "1" if hist_of(arm) == "ncp" else ""})
    args = [PY, "-m", "membrane.opencode", "--serve", "--serve-port", str(SERVE),
            "--serve-host", "127.0.0.1", "--listen", str(LISTEN), "--standalone",
            "--no-launch", "--no-thinker", "--no-dreamer", "--no-autonode", "--no-night",
            "--no-global", "--no-absorb", "--no-browser", "--no-open",
            "--field", os.path.join(d, "field.json"), "--audit", os.path.join(d, "audit.jsonl")]
    if base_arm(arm) == "none":
        args.append("--no-banner")
    if hist_of(arm) in ("nc", "ncp"):
        args.append("--no-context")
    log = open(os.path.join(d, "seat.log"), "ab")
    p = subprocess.Popen(args, env=env, cwd=d, stdout=log, stderr=log)
    t0 = time.time()
    while time.time() - t0 < 240:
        if p.poll() is not None:
            raise SystemExit(f"seat exited rc={p.returncode}; see {d}/seat.log")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{SERVE}/agent", timeout=3).read()
            print(f"[harness] seat up ({arm}) in {time.time() - t0:.0f}s", flush=True)
            return p
        except Exception:
            time.sleep(1)
    p.terminate()
    raise SystemExit("seat never became ready")


def stop(p):
    p.terminate()
    try:
        p.wait(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()


def call(path, body=None, timeout=600):
    rq = urllib.request.Request(f"http://127.0.0.1:{SERVE}{path}", method="POST" if body is not None else "GET",
                                data=json.dumps(body).encode() if body is not None else None,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def say(sid, text, model):
    t0 = time.time()
    out = call(f"/session/{sid}/message", {"parts": [{"type": "text", "text": text}],
                                           "model": {"providerID": "membrane", "modelID": model}})
    reply = " ".join(p.get("text", "") for p in (out or {}).get("parts", []) if p.get("type") == "text")
    info = (out or {}).get("info") or {}
    tk = info.get("tokens") or {}
    tools = sum(1 for p in (out or {}).get("parts", []) if p.get("type") == "tool")
    return {"text": reply.strip(), "model": info.get("modelID"), "s": round(time.time() - t0, 1),
            "tok_in": tk.get("input", 0), "tok_out": tk.get("output", 0), "tools": tools}


def setup(root):
    d = os.path.join(root, "base")
    os.makedirs(d, exist_ok=True)
    p = boot(d, "conversation")
    chats = {}
    try:
        for t in TOPICS:
            sid = call("/session", {})["id"]
            chats[t["id"]] = sid
            for line in t["setup"]:
                r = say(sid, line, SETUP_MODEL)
                print(f"[setup {t['id']}] {r['model']} {r['s']}s :: {r['text'][:100]!r}", flush=True)
        json.dump(chats, open(os.path.join(d, "chats.json"), "w"))
        print("[harness] letting capture drain 120s", flush=True)
        time.sleep(120)
    finally:
        stop(p)


def ask(root, arm):
    base, d = os.path.join(root, "base"), os.path.join(root, arm)
    if os.path.exists(d):
        shutil.rmtree(d)
    subprocess.run(["cp", "-cR", base, d], check=True)          # APFS clone: no extra space
    for f in ("dump.jsonl", "seat.log"):
        if os.path.exists(os.path.join(d, f)):
            os.remove(os.path.join(d, f))
    cp = os.path.join(d, "chats.json")
    chats = json.load(open(cp)) if os.path.exists(cp) else {}
    p = boot(d, arm)
    rows = []
    try:
        for t in TOPICS:
            for q in t["questions"]:
                sid = chats[t["id"]] if q["where"] == "same" else call("/session", {})["id"]
                qt = q["q"].replace(*TOOLS_Q) if arm.endswith("-t") else q["q"]
                r = say(sid, qt, ASK_MODEL)
                low = " ".join(r["text"].lower().split())
                ok = all(any(a.lower() in low for a in alts) for alts in q["expect"])
                rows.append({"arm": arm, "topic": t["id"], "where": q["where"], "qid": q["id"],
                             "ok": ok, **r})
                print(f"[{arm} {q['id']}] {'OK ' if ok else 'MISS'} {r['model']} {r['s']}s in={r['tok_in']} tools={r['tools']} :: {r['text'][:120]!r}",
                      flush=True)
    finally:
        stop(p)
        json.dump(rows, open(os.path.join(d, "answers.json"), "w"), indent=1)


def score(root):
    arms = sorted(x for x in os.listdir(root) if os.path.exists(os.path.join(root, x, "answers.json")))
    for arm in arms:
        f = os.path.join(root, arm, "answers.json")
        if not os.path.exists(f):
            continue
        rows = json.load(open(f))
        dump = [json.loads(x) for x in open(os.path.join(root, arm, "dump.jsonl"))] \
            if os.path.exists(os.path.join(root, arm, "dump.jsonl")) else []
        sizes = sorted(len(x["ctx"]) for x in dump if not x.get("tool_round"))
        med = sizes[len(sizes) // 2] if sizes else 0
        by = {}
        for r in rows:
            by.setdefault(r["where"], []).append(r["ok"])
        cells = "  ".join(f"{w}: {sum(v)}/{len(v)}" for w, v in sorted(by.items()))
        secs = sorted(r["s"] for r in rows)
        tin = sorted(r.get("tok_in", 0) for r in rows); tl = [r.get("tools", 0) for r in rows]
        print(f"{arm:13s} {cells}   block median {med} chars (n={len(sizes)})   "
              f"median turn {secs[len(secs) // 2] if secs else 0}s   tokens in/turn median {tin[len(tin) // 2] if tin else 0}"
              f" total {sum(tin)}   tool calls {sum(tl)}")


if __name__ == "__main__":
    cmd, root = sys.argv[1], os.path.abspath(sys.argv[2])   # the seat's cwd is the arm dir
    {"setup": lambda: setup(root), "ask": lambda: ask(root, sys.argv[3]),
     "score": lambda: score(root)}[cmd]()
