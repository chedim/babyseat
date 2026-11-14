"""Shared helpers for the cliffvm measurements (docs/measurements-2026-11-14.md). Runs ONLY the cliffvm seat, on a COPY of a store,
with a fake claude, on an ephemeral port. Never a real claude, never port 4096."""
import json, os, shutil, subprocess, time, urllib.request, stat, sys

FAKE = r'''#!/usr/bin/env python3
import json, sys, time, os
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
prompt = sys.argv[-1]
out({"type": "system", "subtype": "init", "session_id": "fake-1", "tools": []})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Seat answer to: " + prompt[:80], "num_turns": 1, "session_id": "fake-1"})
'''

def start(repo, binp, workdir, store, facts=(), sleep=0):
    os.makedirs(workdir, exist_ok=True)
    fake = os.path.join(workdir, "fake-claude")
    open(fake, "w").write(FAKE); os.chmod(fake, 0o755)
    packs = [l.strip() for l in open(os.path.join(repo, "membrane/apps/barest.packs")) if l.strip() and not l.startswith("#")]
    cmd = [binp, "run"] + [os.path.join(repo, f"membrane/packs/{p}.c1f") for p in packs] + [os.path.join(repo, "membrane/apps/barest.c1f"),
           "--store", store, "--port", "0", "--fact", f"chat_claude_bin={fake}"]
    for f in facts: cmd += ["--fact", f]
    env = dict(os.environ, ANTHROPIC_API_KEY="sk-ant-harness-default-0000", FAKE_SLEEP=str(sleep))
    t = time.time()
    p = subprocess.Popen(cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    line = p.stdout.readline()
    up = time.time() - t
    port = int(line.rsplit(":", 1)[1])
    assert port != 4096
    return p, port, up

def rss_mb(p): return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(p.pid)]).strip()) / 1024

def http(port, method, path, body=None, timeout=300):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), method=method,
                               headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(r, timeout=timeout) as f: b = f.read()
    return b, (time.time() - t) * 1000

def call(port, tool, args):
    b, ms = http(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    return json.loads(b)["result"]["content"][0]["text"], ms, len(b)

def cliff(port, code): return call(port, "run_cliff", {"code": code})

def stop(p):
    p.kill(); p.wait()

def pct(xs, f):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(f * len(xs)))]
