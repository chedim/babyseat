#!/usr/bin/env python3 -I
"""End-to-end (REAL model in the loop) pilot on an EMPTY cliffvm seat, docs/bench-system-pilot.md (#1143).

System under test: the barest app as shipped (default doctrine, default banner, Help entities, the tools), the real `claude` CLI through the
operator's login (--fact chat_auth=login; ANTHROPIC_API_KEY is removed from the seat's environment, never read, printed or stored), an empty store.
Nothing is edited for the bench. One real turn at a time; private seat on `--port 0` (never 4096); the release cliffvm binary.

usage (every step is resumable; <scratch> keeps the raw logs):
  scripts/bench_system.py locomo <repo> <cliffvm> <locomo10.json> <scratch>            # ingest conv 0 session by session, post_ingest.sqlite, 30 questions
  scripts/bench_system.py lme    <repo> <cliffvm> <longmemeval_s_cleaned.json> <scratch>  # the same for ONE LongMemEval instance (rule in pick_lme)
  scripts/bench_system.py report <scratch>                                             # scores + write-side check + cost tables as markdown (no model)
Live logs: <scratch>/<set>/ingest.jsonl and <scratch>/<set>/questions.jsonl (one line per turn, appended as each turn ends).
STOP rule: a turn whose error looks like a rejected login / rate limit stops the whole run at once (STOPPED file), no retry loop.
The ONLY guard on a turn is the CLI's turn timeout (`chat_turn_timeout_s`, set below)."""
import json, os, re, sqlite3, subprocess, sys, time, shutil, urllib.request

# ---------------------------------------------------------------- the templates (exact strings; the doc quotes them)
INGEST_LOCOMO = ("Below is session {n} of a conversation between {a} and {b} on {date}. Remember what will matter for answering questions about it later; "
                 "you will not see this text again.\n\n{turns}")
INGEST_LME = ("Below is session {n} of a conversation between a user and an assistant on {date}. Remember what will matter for answering questions about it later "
              "(the user's own sentences above all); you will not see this text again.\n\n{turns}")
QUESTION = "{q}  (Answer from your memory of the conversation you were given earlier. If you have no record of it, say 'not in memory'.)"
TURN_TIMEOUT_S = 1800          # the existing per-turn limit (boot fact chat_turn_timeout_s); the default is 21600
PER_CATEGORY = 6               # LoCoMo: the FIRST 6 questions of each category 1..5 in file order (fixed rule, no picking)
CAT = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial (abstain)"}
STOP_RE = re.compile(r"rate.?limit|usage limit|quota|429|401|unauthori[sz]ed|not logged in|please run /login|invalid (api )?key|credit|overloaded|login", re.I)
ABSTAIN_RE = re.compile(r"not in memory|no record|unknown|don.?t have (any )?(a )?(record|information|memory)|do not have (any )?(a )?(record|information|memory)|"
                        r"no (information|mention|memory|recollection)|not mentioned|can.?t find|cannot find|couldn.?t find|not (able|sure)|"
                        r"never (said|mentioned|told)|wasn.?t (mentioned|discussed)|not (discussed|stated|specified|recorded)|i don.?t (know|recall|remember)", re.I)

# ---------------------------------------------------------------- the seat
def start_seat(repo, binp, workdir, store):
    os.makedirs(workdir, exist_ok=True)
    packs = [l.strip() for l in open(os.path.join(repo, "membrane/apps/barest.packs")) if l.strip() and not l.startswith("#")]
    claude = os.path.expanduser("~/.local/bin/claude")
    cmd = [binp, "run"] + [os.path.join(repo, f"membrane/packs/{p}.c1f") for p in packs] + [os.path.join(repo, "membrane/apps/barest.c1f"),
           "--store", store, "--port", "0", "--fact", f"chat_claude_bin={claude}", "--fact", "chat_auth=login",
           "--fact", f"chat_turn_timeout_s={TURN_TIMEOUT_S}"]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    err = open(os.path.join(workdir, "seat.stderr"), "a")
    p = subprocess.Popen(cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=err, text=True, env=env)
    line = p.stdout.readline()
    port = int(line.rsplit(":", 1)[1]); assert port != 4096
    return p, port

def http(port, method, path, body=None, timeout=60):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as f: return json.loads(f.read())

def send(port, text):
    """One chat message; waits until its turn is done/error. Returns (view, wall seconds)."""
    t0 = time.time()
    mid = http(port, "POST", "/membrane/chat/message", {"text": text})["id"]
    while True:
        time.sleep(3)
        v = http(port, "GET", f"/membrane/chat/message/{mid}")
        if v.get("state") in ("done", "error"): return v, time.time() - t0
        if time.time() - t0 > TURN_TIMEOUT_S + 600: return dict(v, state="error", response="bench: no end of the turn"), time.time() - t0

def record(kind, idx, v, wall, **extra):
    t = v.get("turn") or {}
    u = t.get("usage") or {}
    inp = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    return dict(kind=kind, idx=idx, at=time.strftime("%H:%M:%S"), wall_s=round(wall, 1), wall_s_note="poll granularity 3 s; `duration` (ms, the CLI's own) is the model time", state=v.get("state"), usage=u, input_total=inp,
                cost=t.get("cost"), calls=t.get("calls"), duration=t.get("duration"), tools=v.get("tools"), response=v.get("response"), **extra)

def check_stop(scratch, rec):
    if rec["state"] == "error" and STOP_RE.search(str(rec.get("response") or "")):
        open(os.path.join(scratch, "STOPPED"), "w").write(json.dumps(rec)[:4000])
        print("STOPPED: login/limit-like error:", str(rec.get("response"))[:400]); sys.exit(3)

def jl(path, rec):
    with open(path, "a") as f: f.write(json.dumps(rec) + "\n")

def read_jl(path): return [json.loads(l) for l in open(path)] if os.path.exists(path) else []

# ---------------------------------------------------------------- data
def locomo_sessions(conv):
    c = conv["conversation"]; out = []
    for n in range(1, 60):
        if f"session_{n}" not in c: continue
        lines = []
        for t in c[f"session_{n}"]:
            tx = t["text"] + (f" [image: {t['blip_caption']}]" if t.get("blip_caption") else "")
            lines.append(f"{t['speaker']}: {tx}")
        out.append(dict(n=n, date=c.get(f"session_{n}_date_time", ""), text=INGEST_LOCOMO.format(n=n, a=c["speaker_a"], b=c["speaker_b"], date=c.get(f"session_{n}_date_time", ""), turns="\n".join(lines))))
    return out

def locomo_questions(conv):
    qs = []; seen = {}
    for i, q in enumerate(conv["qa"]):
        k = q["category"]
        if seen.get(k, 0) < PER_CATEGORY:
            seen[k] = seen.get(k, 0) + 1
            qs.append(dict(i=i, category=k, question=q["question"], gold=str(q.get("answer", q.get("adversarial_answer", ""))), abstain_gold=(k == 5)))
    return sorted(qs, key=lambda q: (q["category"], q["i"]))

def pick_lme(data):
    """Rule: among the `knowledge-update` instances (a fact that CHANGES across sessions: needs memory across sessions, and the newest value must win) the one
    with the SMALLEST haystack in characters (first in file order on a tie). Only the 's' haystack (about 40-50 sessions) exists on disk."""
    ku = [(sum(len(t["content"]) for s in x["haystack_sessions"] for t in s), i) for i, x in enumerate(data) if x["question_type"] == "knowledge-update"]
    return min(ku)[1]

def lme_sessions(inst):
    order = sorted(range(len(inst["haystack_sessions"])), key=lambda i: (inst["haystack_dates"][i], i))   # chronological
    out = []
    for n, i in enumerate(order, 1):
        d = inst["haystack_dates"][i]
        out.append(dict(n=n, date=d, text=INGEST_LME.format(n=n, date=d, turns="\n".join(f"{t['role']}: {t['content']}" for t in inst["haystack_sessions"][i]))))
    return out

# ---------------------------------------------------------------- run
def run_set(name, repo, binp, scratch, sessions, questions):
    d = os.path.join(scratch, name); os.makedirs(d, exist_ok=True)
    store = os.path.join(d, "seat.sqlite"); post = os.path.join(d, "post_ingest.sqlite")
    json.dump(questions, open(os.path.join(d, "questions_set.json"), "w"), indent=1)
    ing = os.path.join(d, "ingest.jsonl")
    if not os.path.exists(post):
        base = os.path.join(d, "empty_baseline.sqlite")
        done = {r["idx"] for r in read_jl(ing)}
        p, port = start_seat(repo, binp, d, store)
        try:
            if not os.path.exists(base) and not done:
                subprocess.check_call(["sqlite3", store, f".backup {base}"])     # the empty store (rows the seat itself made at boot)
            for s in sessions:
                if s["n"] in done: continue
                v, wall = send(port, s["text"])
                rec = record("ingest", s["n"], v, wall, prompt_chars=len(s["text"]), date=s["date"]); jl(ing, rec); check_stop(scratch, rec)
                print(name, "ingest", s["n"], rec["state"], rec["wall_s"], "s", rec["cost"], flush=True)
            subprocess.check_call(["sqlite3", store, f".backup {post}"])           # no turn is running now
        finally:
            p.kill(); p.wait()
    qf = os.path.join(d, "questions.jsonl"); done = {r["idx"] for r in read_jl(qf)}
    for j, q in enumerate(questions):
        if j in done: continue
        qs = os.path.join(d, f"q{j}.sqlite")
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(qs + ext): os.remove(qs + ext)
        shutil.copy(post, qs)                                                       # a FRESH seat on a copy: nothing leaks between questions
        p, port = start_seat(repo, binp, d, qs)
        try:
            v, wall = send(port, QUESTION.format(q=q["question"]))
            rec = record("question", j, v, wall, q=q); jl(qf, rec)
            print(name, "q", j, rec["state"], rec["wall_s"], "s", rec["cost"], flush=True)
        finally:
            p.kill(); p.wait()
        for ext in ("-wal", "-shm"):
            if os.path.exists(qs + ext): os.remove(qs + ext)
        check_stop(scratch, rec)
        os.remove(qs)                                                               # the per-question copy is thrown away

# ---------------------------------------------------------------- scoring
def norm(s):
    s = re.sub(r"[^\w\s]", " ", str(s).lower())
    return [w for w in s.split() if w not in ("a", "an", "the", "and")]

def f1(pred, gold):
    p, g = norm(pred), norm(gold)
    if not p or not g: return float(p == g)
    from collections import Counter
    c = sum((Counter(p) & Counter(g)).values())
    if c == 0: return 0.0
    pr, rc = c / len(p), c / len(g); return 2 * pr * rc / (pr + rc)

def score_q(r):
    q = r["q"]; ans = str(r.get("response") or "")
    if q.get("abstain_gold"): return float(bool(ABSTAIN_RE.search(ans)))
    return f1(ans, q["gold"])

def memory_text(store, baseline):
    """The MODEL-WRITTEN memory: every row not in the empty baseline and not a raw chat artefact (messages, turns, responses, tools, commands, chunks), plus the banner."""
    db = sqlite3.connect(store); base = {k for (k,) in sqlite3.connect(baseline).execute("select k from c_field")}
    rows = []
    for k, cls, body in db.execute("select k, cls, body from c_field"):
        if k in base and cls != "Banner": continue
        # the seat's own rows: raw chat artefacts (message, body, turn, response, tools), commands, the word index (Word, Identifier), stats, packs, help
        if cls in ("Chat", "Command", "Chunk", "Stats", "Word", "Identifier", "Mem", "Help", "Pack", "FieldVm") or k.startswith(("membrane::Chat:", "membrane::Command:", "Chunk:")): continue
        if cls == "Banner": body = json.dumps({"text": json.loads(body)["props"].get("text")})     # only what the model wrote into it, not the seat's clock and labels
        rows.append((k, cls, body))
    return rows

def stop_words(): return set("what when where who which how why did does do is was were are of in on at to for with by from about his her their my your has have had it its that this and or but not be been".split())

def key_tokens(gold): return [w for w in norm(gold) if w not in stop_words()]

def report(scratch):
    out = []
    for name in ("locomo", "lme"):
        d = os.path.join(scratch, name)
        if not os.path.exists(d): continue
        ing = read_jl(os.path.join(d, "ingest.jsonl")); qr = read_jl(os.path.join(d, "questions.jsonl"))
        out.append(f"## SET {name}\n")
        for r in qr: r["f1"] = score_q(r)
        if name == "locomo":
            out.append("| category | n | mean score | abstained |\n|---|---|---|---|")
            for c in sorted(CAT):
                rs = [r for r in qr if r["q"]["category"] == c]
                if rs: out.append(f"| {c} {CAT[c]} | {len(rs)} | {sum(r['f1'] for r in rs)/len(rs):.3f} | {sum(bool(ABSTAIN_RE.search(str(r['response'] or ''))) for r in rs)} |")
            out.append(f"| all | {len(qr)} | {sum(r['f1'] for r in qr)/max(1,len(qr)):.3f} | |\n")
        out.append("Questions (score = token F1; category 5 = 1 if the answer abstains):\n")
        for r in qr:
            q = r["q"]
            out.append(f"- Q{r['idx']} cat {q['category'] if 'category' in q else '-'}: {q['question']}\n  - gold: {q['gold']}\n  - answer: {str(r['response'] or '')[:700]!r}\n  - score {r['f1']:.2f}; state {r['state']}; tools {r['tools']}; ${r['cost']}; calls {r['calls']}; {(r['duration'] or 0)/1000:.1f} s model time ({r['wall_s']} s polled); in {r['input_total']} out {(r['usage'] or {}).get('output_tokens')}")
        # write side
        post_store = os.path.join(d, "post_ingest.sqlite"); base = os.path.join(d, "empty_baseline.sqlite")
        if os.path.exists(post_store) and os.path.exists(base):
            rows = memory_text(post_store, base)
            blob = "\n".join(b for _, _, b in rows).lower(); nrm = " ".join(norm(blob))
            from collections import Counter
            out.append(f"\nModel-written rows (not baseline, not chat artefacts): {len(rows)}, {sum(len(b) for _,_,b in rows)} bytes; by class {dict(Counter(c for _,c,_ in rows))}")
            out.append("sample titles: " + "; ".join(k for k, _, _ in rows[:10]))
            ban = [b for k, c, b in rows if c == "Banner"]
            out.append(f"final banner body: {sum(len(b) for b in ban)} bytes")
            full = part = 0; lines = []
            for r in (qr or []):
                q = r["q"]
                if q.get("abstain_gold"): continue
                kt = key_tokens(q["gold"]); hit = [w for w in kt if w in nrm.split()] if kt else []
                strict = bool(kt) and len(hit) == len(kt); partial = bool(kt) and len(hit) >= len(kt) / 2
                full += strict; part += partial
                lines.append(f"  - Q{r['idx']} gold {q['gold']!r}: {len(hit)}/{len(kt)} key tokens {'present' if strict else ('partial' if partial else 'absent')}")
            n = len([r for r in qr if not r["q"].get("abstain_gold")])
            out.append(f"Write-side (gold key tokens in the model-written memory, answerable questions n={n}): all tokens {full}/{n}, at least half {part}/{n}")
            out += lines
        # cost
        allr = ing + qr
        def agg(rs):
            ok = [r for r in rs]
            return dict(turns=len(ok), calls=sum(r["calls"] or 0 for r in ok), inp=sum(r["input_total"] for r in ok), out=sum((r["usage"] or {}).get("output_tokens") or 0 for r in ok),
                        cache_read=sum((r["usage"] or {}).get("cache_read_input_tokens") or 0 for r in ok), cost=round(sum(r["cost"] or 0 for r in ok), 2), wall_min=round(sum((r["duration"] or r["wall_s"]*1000) for r in ok) / 60000, 1),
                        errors=sum(r["state"] == "error" for r in ok))
        for lbl, rs in (("ingest", ing), ("questions", qr)):
            if rs:
                a = agg(rs); a["cache_share"] = round(a["cache_read"] / a["inp"], 2) if a["inp"] else None
                out.append(f"\ncost {lbl}: {a}")
    return "\n".join(out)

def main():
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(2)
    if a[0] == "report": print(report(a[1])); return
    mode, repo, binp, data, scratch = a[:5]
    scratch = os.path.abspath(scratch); os.makedirs(scratch, exist_ok=True)
    if os.path.exists(os.path.join(scratch, "STOPPED")): print("STOPPED file present; remove it to go on"); sys.exit(3)
    D = json.load(open(data))
    if mode == "locomo":
        run_set("locomo", repo, binp, scratch, locomo_sessions(D[0]), locomo_questions(D[0]))
    elif mode == "lme":
        i = pick_lme(D); x = D[i]
        q = [dict(i=i, category=x["question_type"], question=x["question"], gold=str(x["answer"]), abstain_gold=x["question_id"].endswith("_abs"), question_id=x["question_id"], question_date=x["question_date"])]
        run_set("lme", repo, binp, scratch, lme_sessions(x), q)
    else: print(__doc__); sys.exit(2)

if __name__ == "__main__": main()
