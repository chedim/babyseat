"""scripts/measure_recall_arms.py end to end on a tiny fixture: a fake embedder (local HTTP server), a stub seat (tests/fake_recall_seat.py). No real Ollama, no seat."""
import json, os, subprocess, sys, threading, http.server, socket
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "measure_recall_arms.py")
DATA = os.path.join(ROOT, "tests", "fixtures", "locomo_tiny.json")
STUB = os.path.join(ROOT, "tests", "fake_recall_seat.py")

class Embedder(http.server.BaseHTTPRequestHandler):
    calls = 0
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        type(self).calls += 1
        import re
        out = []
        for t in body.get("input", []):
            v = [0.0] * 32
            for w in re.findall(r"[a-z0-9]+", t.lower()): v[sum(map(ord, w)) % 32] += 1.0
            out.append(v)
        b = json.dumps({"embeddings": out}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

@pytest.fixture()
def embedder():
    Embedder.calls = 0
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Embedder)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % s.server_address[1]
    s.shutdown()

def run(tmp, embedder, *extra, lock=None, check=False):
    cmd = [sys.executable, "-I", SCRIPT, "--data", DATA, "--scratch", str(tmp / "scratch"), "--driver-module", STUB, "--ollama", embedder,
           "--runs-dir", str(tmp / "runs"), "--lock-file", lock or str(tmp / "lock"), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)

def test_full_report_has_every_cell_and_marks_missing_arm(tmp_path, embedder):
    r = run(tmp_path, embedder, "--test-convs", "0,1", "--arms", "edges,cosine,fused", "--max-embed-calls", "1000")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for arm in ("edges", "cosine"):
        assert f"arm {arm}" in out
    assert "fused: not available" in out
    # every category and every k, for the lenient/strict/ceiling/relative tables
    for cat in ("multi-hop", "temporal", "single-hop", "all"):
        assert cat in out
    for kind in ("lenient", "strict", "ceiling", "relative"):
        assert kind in out
    rep = json.load(open(tmp_path / "scratch" / "report.json"))
    assert rep["arms"]["fused"] == "not available"
    for arm in ("edges", "cosine"):
        cell = rep["arms"][arm]["test"]["per_cat"]["single-hop"]
        for k in ("1", "3", "8", "20", "all"):
            assert f"lenient@{k}" in cell and f"strict@{k}" in cell
    assert rep["header"]["data_sha256"] and rep["header"]["config_sha256"]
    assert rep["header"]["tune_convs"] == [] and rep["header"]["test_convs"] == [0, 1]
    assert rep["header"]["embed_calls"] == Embedder.calls > 0
    assert "wall_s" in rep["header"]
    assert (tmp_path / "scratch" / "per_question.jsonl").read_text().count("\n") >= 6
    assert "best so far" in out and "no cherry-picked" in out
    assert json.load(open(tmp_path / "scratch" / "report.json"))["arms"]["cosine"]["test"]["per_cat"]["all"]["lenient@all"] == 1.0

def test_planted_answers_are_found_by_the_stub(tmp_path, embedder):
    run(tmp_path, embedder, "--test-convs", "0", "--arms", "cosine", "--max-embed-calls", "1000")
    rep = json.load(open(tmp_path / "scratch" / "report.json"))
    assert rep["arms"]["cosine"]["test"]["per_cat"]["single-hop"]["lenient@3"] == 1.0

def test_overlap_of_tune_and_test_is_refused(tmp_path, embedder):
    r = run(tmp_path, embedder, "--test-convs", "0,1", "--tune-convs", "1,2", "--arms", "edges", "--max-embed-calls", "1000")
    assert r.returncode != 0 and "overlap" in (r.stderr + r.stdout).lower()
    assert Embedder.calls == 0

def test_tune_and_test_are_reported_apart(tmp_path, embedder):
    r = run(tmp_path, embedder, "--test-convs", "0", "--tune-convs", "2", "--arms", "edges", "--max-embed-calls", "1000")
    assert r.returncode == 0, r.stderr
    rep = json.load(open(tmp_path / "scratch" / "report.json"))
    assert rep["header"]["tune_convs"] == [2] and rep["header"]["test_convs"] == [0]
    assert "tune" in rep["arms"]["edges"] and "test" in rep["arms"]["edges"]

def test_max_embed_calls_is_required(tmp_path, embedder):
    r = run(tmp_path, embedder, "--test-convs", "0", "--arms", "edges")
    assert r.returncode != 0 and "max-embed-calls" in r.stderr

def test_budget_is_a_hard_stop(tmp_path, embedder):
    r = run(tmp_path, embedder, "--test-convs", "0,1,2", "--arms", "cosine", "--max-embed-calls", "3")
    assert r.returncode != 0
    assert Embedder.calls <= 3

def test_second_run_is_refused_while_the_lock_is_held(tmp_path, embedder):
    import fcntl
    lock = str(tmp_path / "lock")
    f = open(lock, "w"); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    r = run(tmp_path, embedder, "--test-convs", "0", "--arms", "edges", "--max-embed-calls", "1000", lock=lock)
    f.close()
    assert r.returncode != 0 and "lock" in (r.stderr + r.stdout).lower()
    assert Embedder.calls == 0

def test_dry_run_sends_nothing_and_estimates(tmp_path, embedder):
    r = run(tmp_path, embedder, "--test-convs", "0,1", "--arms", "edges,cosine", "--max-embed-calls", "1000", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert Embedder.calls == 0
    assert "embedding calls" in r.stdout and "estimate" in r.stdout
    assert not (tmp_path / "scratch" / "per_question.jsonl").exists()

def test_remote_ollama_is_refused_without_allow_remote(tmp_path):
    cmd = [sys.executable, "-I", SCRIPT, "--data", DATA, "--scratch", str(tmp_path / "s"), "--driver-module", STUB, "--ollama", "http://203.0.113.9:11434",
           "--test-convs", "0", "--arms", "edges", "--max-embed-calls", "10", "--dry-run", "--runs-dir", str(tmp_path / "r"), "--lock-file", str(tmp_path / "l")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "allow-remote" in r.stderr

def test_best_so_far_compares_only_same_conversations(tmp_path, embedder):
    r = run(tmp_path, embedder, "--test-convs", "0,1", "--arms", "edges", "--max-embed-calls", "1000")
    assert "not comparable" in r.stdout  # the recorded runs are on conv 0,1 only for some, 0-9 for another
