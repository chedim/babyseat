#!/usr/bin/env python3 -I
"""Full-detail LoCoMo comparison of the recall ARMS of the seat (docs/measurements-recall-arms.md). INTERNAL numbers: no public claims.
One command, one process at a time. Arms: edges (today's `related`), cosine (the cosine lane alone), fused (the fused banner part).
An arm the seat cannot serve prints `not available`, never zeros. Held-out discipline: --test-convs are only measured, --tune-convs (default none)
are the only ones anything may be tuned on; the two lists must not overlap. --max-embed-calls has NO default and is a hard stop, enforced by a counting
loopback proxy that the seat's `seat_backend` is pointed at; the proxy forwards to --ollama (loopback only unless --allow-remote). --dry-run sends nothing.
real run : scripts/measure_recall_arms.py --repo R --binary <folder>/bin/cliffvm --data locomo10.json --scratch S --test-convs 0,1 --arms edges,cosine,fused --max-embed-calls N
test run : --driver-module tests/fake_recall_seat.py  (a stub: make(ollama_url, NotAvailable, **kw) -> object with ingest_turn/ingest_question/ask/close)
The per-arm QUERY is the ARM_READS table below: edit the `fused` / `cosine` line to point at the final entity (see the doc)."""
import argparse, fcntl, hashlib, http.server, importlib.util, json, os, re, socket, statistics as st, sys, tempfile, threading, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CATN = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
KS = (1, 3, 8, 20)
ARMS = ("edges", "cosine", "fused")
# THE EASILY EDITED LINES. (entity spec with {eid} = the question's SemText, property to read, property written first or None)
# edges  : today's `related`, k_rows asked through `related_n`.
# cosine : the cosine lane alone. GUESS until the lanes land: point it at the final entity/property (rows = keys, or `Vector:of:<key>:s:<start>`).
# fused  : the fused banner part. GUESS: `Banner:recall` property `rows`; change it when the final getter is known.
ARM_READS = {
    "edges": ("{eid}", "related", "related_n"),
    "cosine": ("{eid}", "cosine_related", None),
    "fused": ("Banner:recall", "rows", None),
}
# rough cost model for the dry-run ESTIMATE (assumptions, not measurements: doc section 3(a) 449 ms per ingested turn, 51 ms per `related` ask)
S_PER_TURN, S_PER_ASK = 0.45, 0.5

class NotAvailable(Exception): pass

def die(msg):
    print("refused: " + msg, file=sys.stderr); sys.exit(2)

def parse_convs(s, name):
    try: return [int(x) for x in s.split(",") if x.strip() != ""]
    except ValueError: die(f"{name} must be a comma list of integers")

def is_loopback(url):
    h = urllib.parse.urlparse(url).hostname or ""
    return h in ("localhost", "::1") or h.startswith("127.")

def sha(b): return hashlib.sha256(b).hexdigest()

# ---------- the counting proxy: the only way embeddings leave this run ----------
class Proxy:
    def __init__(self, upstream, max_calls):
        self.upstream, self.max_calls, self.calls, self.exceeded = upstream.rstrip("/"), max_calls, 0, False
        me = self
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _go(self):
                n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else None
                emb = bool(re.search(r"/(api/embed|api/embeddings|v1/embeddings)$", self.path.split("?")[0]))
                if emb:
                    with me.lock:
                        if me.calls >= me.max_calls: me.exceeded = True; code, out = 429, b'{"error":"max-embed-calls reached"}'
                        else: me.calls += 1; code = 0
                    if code:
                        self.send_response(code); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out); return
                try:
                    r = urllib.request.Request(me.upstream + self.path, data=body, method=self.command, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(r, timeout=300) as f: code, out = f.status, f.read()
                except urllib.error.HTTPError as e: code, out = e.code, e.read()
                except Exception as e: code, out = 502, json.dumps({"error": str(e)}).encode()
                self.send_response(code); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
            do_GET = do_POST = _go
        self.lock = threading.Lock()
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def close(self): self.srv.shutdown()

# ---------- drivers ----------
def norm_key(r):
    k = r.get("semtext") or r.get("key") or r.get("id") if isinstance(r, dict) else r
    if not isinstance(k, str): return None
    m = re.match(r"^Vector:of:(.+?):s:\d+", k.split("::")[-1])
    return m.group(1) if m else k

class CliffvmSeat:
    """The real door: a private cliffvm seat (fake claude, port 0, an EMPTY private store), as scripts/measure_locomo.py; seat_backend = the counting proxy."""
    def __init__(self, ollama_url, NotAvailable, repo, binary, scratch, k_rows_prop="related_n", **kw):
        sys.path.insert(0, HERE); import measure_lib as m
        self.m, self.NA = m, NotAvailable
        self.p, self.port, _ = m.start(repo, binary, scratch, os.path.join(scratch, "seat.sqlite"), facts=[f"seat_backend={ollama_url}"])
    @staticmethod
    def lit(s): return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ") + "'"
    def _mk(self, eid, text):
        self.m.cliff(self.port, f"{eid} = SemText(text: {self.lit(text)})")
        self.m.call(self.port, "entity_write", {"spec": eid, "prop": "mention", "value": True})
    ingest_turn = ingest_question = lambda self, eid, text: self._mk(eid, text)
    def ask(self, arm, eid, k_rows):
        spec, prop, wprop = ARM_READS[arm]; spec = spec.format(eid=eid)
        try:
            if wprop: self.m.call(self.port, "entity_write", {"spec": eid, "prop": wprop, "value": k_rows})
            t, _, _ = self.m.call(self.port, "run_cliff", {"code": "${'" + str(spec) + "'}['" + str(prop) + "']"})
            rows = json.loads(t)
        except Exception as e: raise self.NA(f"{arm}: {e}")
        if not isinstance(rows, list): raise self.NA(f"{arm}: the read did not return a list")
        return rows
    def close(self): self.m.stop(self.p)

def load_driver(args, proxy_url, scratch):
    if args.driver_module:
        spec = importlib.util.spec_from_file_location("recall_driver", args.driver_module); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod.make(proxy_url, NotAvailable)
    if not (args.repo and args.binary): die("a real run needs --repo and --binary (or --driver-module for a stub)")
    return CliffvmSeat(proxy_url, NotAvailable, args.repo, args.binary, scratch)

# ---------- data and scoring ----------
def key(c, dia): return "Loco:c%d:%s" % (c, dia.replace(":", "_"))

def turns_of(conv):
    for s in range(1, 40):
        if f"session_{s}" not in conv: continue
        date = conv.get(f"session_{s}_date_time", "")
        for t in conv[f"session_{s}"]:
            tx = t["text"] + (f" [shares {t['blip_caption']}]" if t.get("blip_caption") else "")
            yield t["dia_id"], f"{t['speaker']} ({date}): {tx}"

def questions_of(D, c):
    for i, q in enumerate(D[c]["qa"]):
        if q["category"] == 5 or not q.get("evidence"): continue
        ev = sorted({key(c, e.strip()) for e in q["evidence"] if re.match(r"^D\d+:\d+$", e.strip())})
        if ev: yield i, q, ev

def sentences(text): return max(1, len([s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]))

def metrics(rs):
    n = len(rs)
    if not n: return None
    d = dict(n=n, answered_nothing=sum(1 for r in rs if not r["answered"]), turns_median=st.median(r["turns"] for r in rs))
    for k in KS + ("all",):
        L = sum(1 for r in rs if r["first_rank"] and (k == "all" or r["first_rank"] <= k)) / n
        S = sum(1 for r in rs if r["last_rank"] and (k == "all" or r["last_rank"] <= k)) / n
        C = 1.0 if k == "all" else sum(1 for r in rs if len(r["evidence"]) <= k) / n
        d[f"lenient@{k}"], d[f"strict@{k}"], d[f"ceiling@{k}"], d[f"relative@{k}"] = L, S, C, (S / C if C else None)
    return d

def by_cat(recs):
    out = {}
    for name in CATN.values():
        m = metrics([r for r in recs if r["category"] == name])
        if m: out[name] = m
    m = metrics(recs)
    if m: out["all"] = m
    return out

def table(kind, per_cat):
    L = [f"\n{kind}\n| category | n | @1 | @3 | @8 | @20 | @all |\n|---|---|---|---|---|---|---|"]
    for name, r in per_cat.items():
        L.append(f"| {name} | {r['n']} | " + " | ".join(("%.2f" % r[f"{kind}@{k}"]) if r[f"{kind}@{k}"] is not None else "-" for k in KS + ("all",)) + " |")
    return "\n".join(L)

# ---------- records, best so far ----------
def load_records(path, runs_dir):
    recs = [dict(r, origin="recorded") for r in json.load(open(path))["records"]] if os.path.exists(path) else []
    if runs_dir and os.path.isdir(runs_dir):
        for f in sorted(os.listdir(runs_dir)):
            if not f.endswith(".json"): continue
            try: run = json.load(open(os.path.join(runs_dir, f)))
            except Exception: continue
            for arm, v in run.get("arms", {}).items():
                if isinstance(v, dict) and "test" in v:
                    recs.append(dict(name=f"{arm}, earlier run {run['header']['run_id']}", convs=run["header"]["test_convs"], per_cat=v["test"]["per_cat"],
                                     origin="earlier run", source=f, config=run["header"]["config_sha256"][:12]))
    return recs

def best_section(arms_res, test_convs, records):
    cands = [r for r in records if sorted(r["convs"]) == sorted(test_convs)]
    others = [r for r in records if sorted(r["convs"]) != sorted(test_convs)]
    cats = list(CATN.values()) + ["all"]
    for arm, v in arms_res.items():
        if isinstance(v, dict) and "test" in v: cands.append(dict(name=f"{arm}, THIS run", per_cat=v["test"]["per_cat"], convs=test_convs))
    L = ["\n## best so far per cell (same conversations only; strict and lenient; a cell nobody recorded shows -)\n"]
    for kind in ("lenient", "strict"):
        L.append(f"\n{kind}, best value (which variant)\n| category | @1 | @3 | @8 | @20 |\n|---|---|---|---|---|")
        for cat in cats:
            row = []
            for k in KS:
                vals = [(r["per_cat"][cat][f"{kind}@{k}"], r["name"]) for r in cands if cat in r["per_cat"] and f"{kind}@{k}" in r["per_cat"][cat]]
                row.append("%.2f (%s)" % max(vals, key=lambda x: x[0]) if vals else "-")
            L.append(f"| {cat} | " + " | ".join(row) + " |")
    L.append("\n## every variant tested (recorded or earlier runs), all cells that were recorded\n")
    for r in cands + others:
        same = sorted(r["convs"]) == sorted(test_convs)
        L.append(f"\n### {r['name']} (conversations {r['convs']}; {'comparable' if same else 'not comparable: other conversations'}; {r.get('source', '')})")
        L.append("| category | " + " | ".join(f"{kd}@{k}" for kd in ("lenient", "strict") for k in KS) + " |\n|---|" + "---|" * 8)
        for cat in cats:
            if cat in r["per_cat"]:
                p = r["per_cat"][cat]; L.append(f"| {cat} | " + " | ".join(("%.2f" % p[f"{kd}@{k}"]) if f"{kd}@{k}" in p else "-" for kd in ("lenient", "strict") for k in KS) + " |")
    return "\n".join(L)

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", required=True); ap.add_argument("--scratch", required=True)
    ap.add_argument("--test-convs", required=True); ap.add_argument("--tune-convs", default="")
    ap.add_argument("--arms", default="edges,cosine,fused"); ap.add_argument("--k-rows", type=int, default=200)
    ap.add_argument("--max-embed-calls", type=int, required=True)
    ap.add_argument("--ollama", default="http://127.0.0.1:11434"); ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo"); ap.add_argument("--binary"); ap.add_argument("--driver-module")
    ap.add_argument("--records", default=os.path.join(HERE, "recall_records.json")); ap.add_argument("--runs-dir", default=os.path.join(HERE, "recall_runs"))
    ap.add_argument("--lock-file", default=os.path.join(tempfile.gettempdir(), "membrane-recall-measure.lock"))
    a = ap.parse_args()
    test, tune = parse_convs(a.test_convs, "--test-convs"), parse_convs(a.tune_convs, "--tune-convs")
    if set(test) & set(tune): die(f"--tune-convs and --test-convs overlap on {sorted(set(test) & set(tune))}: a conversation tuned on cannot be a test conversation")
    if not test: die("--test-convs is empty")
    arms = [x for x in a.arms.split(",") if x]
    if any(x not in ARMS for x in arms): die(f"unknown arm; known: {', '.join(ARMS)}")
    if not is_loopback(a.ollama) and not a.allow_remote: die(f"--ollama {a.ollama} is not loopback; give --allow-remote to embed through a remote host")
    if a.max_embed_calls < 0: die("--max-embed-calls must be >= 0")
    raw = open(a.data, "rb").read(); D = json.loads(raw)
    convs = tune + test
    for c in convs:
        if not 0 <= c < len(D): die(f"conversation {c} is not in the data ({len(D)} conversations)")
    n_turns = sum(1 for c in convs for _ in turns_of(D[c]["conversation"]))
    n_sent = sum(sentences(t) for c in convs for _, t in turns_of(D[c]["conversation"]))
    n_q = sum(1 for c in convs for _ in questions_of(D, c))
    est_embed = n_sent + n_q * max(1, len([x for x in arms if x != "edges"]))   # a sentence vector per sentence; one query vector per question per vector arm (a guess; the seat may batch)
    est_s = n_turns * S_PER_TURN + n_q * len(arms) * S_PER_ASK
    config = dict(arms=arms, arm_reads={x: ARM_READS[x] for x in arms}, k_rows=a.k_rows, test=test, tune=tune, ollama=a.ollama, ks=KS)
    header = dict(data_sha256=sha(raw), config_sha256=sha(json.dumps(config, sort_keys=True).encode()), arms=arms, test_convs=test, tune_convs=tune,
                  k_rows=a.k_rows, ollama=a.ollama, max_embed_calls=a.max_embed_calls, run_id=time.strftime("%Y%m%d-%H%M%S"))
    plan = (f"would run: conversations tune {tune or 'none'} / test {test}; arms {arms}; {n_turns} turns, {n_q} questions (categories 1-4)\n"
            f"embedding calls, estimate: about {est_embed} (limit --max-embed-calls {a.max_embed_calls}; the run stops hard at the limit)\n"
            f"time estimate: about {est_s / 60:.0f} min ({S_PER_TURN} s per turn + {S_PER_ASK} s per question and arm; assumptions, not measurements)\n"
            f"embedding host: {a.ollama} (loopback: {is_loopback(a.ollama)}); data sha256 {header['data_sha256'][:12]}, config sha256 {header['config_sha256'][:12]}")
    if a.dry_run:
        print("DRY RUN, nothing sent\n" + plan)
        if est_embed > a.max_embed_calls: print(f"NOTE: the estimate exceeds --max-embed-calls; a real run would stop at the limit with a partial report")
        return 0
    lf = open(a.lock_file, "w")
    try: fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: die(f"another measurement holds the lock {a.lock_file}: one run at a time")
    os.makedirs(a.scratch, exist_ok=True); print(plan, file=sys.stderr)
    t_start = time.time(); proxy = Proxy(a.ollama, a.max_embed_calls)
    seat = load_driver(a, proxy.url, a.scratch)
    na = set(); recs = {x: [] for x in arms}; aborted = None
    jl = open(os.path.join(a.scratch, "per_question.jsonl"), "w")
    try:
        for c in convs:
            for dia, text in turns_of(D[c]["conversation"]): seat.ingest_turn(key(c, dia), text)
        for c in convs:
            role = "tune" if c in tune else "test"
            for i, q, ev in questions_of(D, c):
                eid = "Loq:c%d:%d" % (c, i); seat.ingest_question(eid, q["question"])
                for arm in arms:
                    if arm in na: continue
                    try: rows = seat.ask(arm, eid, a.k_rows)
                    except NotAvailable: na.add(arm); continue
                    order = []
                    for r in rows:
                        k = norm_key(r)
                        if k is None or k.split("::")[-1].startswith("Loq") or k in order: continue
                        order.append(k)
                    ranks = {e: (order.index(e) + 1 if e in order else None) for e in ev}; found = [r for r in ranks.values() if r]
                    rec = dict(arm=arm, role=role, conv=c, q=i, category=CATN[q["category"]], evidence=ev, ranks=ranks, first_rank=min(found) if found else None,
                               last_rank=max(found) if len(found) == len(ev) else None, answered=bool(order), turns=len(order))
                    recs[arm].append(rec); jl.write(json.dumps(rec) + "\n"); jl.flush()
    except Exception as e:
        aborted = f"{type(e).__name__}: {e}" + (" (max-embed-calls reached)" if proxy.exceeded else "")
    finally:
        try: seat.close()
        except Exception: pass
        proxy.close()
    header.update(wall_s=round(time.time() - t_start, 1), embed_calls=proxy.calls, aborted=aborted, not_available=sorted(na))
    arms_res = {}
    for arm in arms:
        if arm in na or (not recs[arm] and not aborted): arms_res[arm] = "not available"; continue
        arms_res[arm] = {role: dict(convs=cs, per_cat=by_cat([r for r in recs[arm] if r["role"] == role])) for role, cs in (("test", test), ("tune", tune)) if cs and recs[arm]}
    rep = dict(header=header, arms=arms_res)
    json.dump(rep, open(os.path.join(a.scratch, "report.json"), "w"), indent=1)
    out = ["# recall arms, full detail (INTERNAL numbers, no public claims; held out: test conversations were never tuned on)", "",
           "header: " + json.dumps(header),
           "lenient = any evidence turn in the top k distinct turns; strict = ALL evidence turns in the top k; ceiling@k = share of questions with at most k evidence turns; relative = strict / ceiling; @all = anywhere in the returned rows. Every category and every k is printed for every arm; no cherry-picked rows."]
    if aborted: out.append(f"ABORTED: {aborted}; the numbers below cover only the questions finished before the stop")
    for arm in arms:
        v = arms_res[arm]
        if v == "not available": out.append(f"\n## arm {arm}\n{arm}: not available (the seat cannot serve this arm yet; no numbers, not zeros)"); continue
        for role in ("test", "tune"):
            if role not in v: continue
            out.append(f"\n## arm {arm}, {role} conversations {v[role]['convs']}" + (" (TUNING set: never quote as a result)" if role == "tune" else ""))
            for kind in ("lenient", "strict", "ceiling", "relative"): out.append(table(kind, v[role]["per_cat"]))
            out.append("\n| category | turns median | answered nothing |\n|---|---|---|\n" + "\n".join(f"| {n} | {r['turns_median']} | {r['answered_nothing']} |" for n, r in v[role]["per_cat"].items()))
    out.append(best_section(arms_res, test, load_records(a.records, a.runs_dir)))
    out.append(f"\nwall {header['wall_s']} s, embedding calls {proxy.calls} of {a.max_embed_calls}, k_rows {a.k_rows}")
    text = "\n".join(out); print(text); open(os.path.join(a.scratch, "report.md"), "w").write(text)
    os.makedirs(a.runs_dir, exist_ok=True); json.dump(rep, open(os.path.join(a.runs_dir, header["run_id"] + ".json"), "w"), indent=1)
    return 3 if aborted else 0

if __name__ == "__main__": sys.exit(main())
