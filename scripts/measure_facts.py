#!/usr/bin/env python3 -I
"""A small HAND-BUILT fact-recall check (one author, 20 facts, NOT a benchmark) on a COPY of a real store: does `SemText.related` over the
operator's messages (and the Keep titles) put the source message in the top 1/3/8/20? Only cliffvm, fake claude, port 0.
usage: scripts/measure_facts.py <repo> <cliffvm binary> <store copy to run on> <facts.json> <scratch dir>
facts.json = {"facts": [{"q": question, "msgs": [message id suffixes], "keeps": [Keep slugs]}], "abstain": [question,...]}"""
import json, os, re, sys, shutil, sqlite3, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_lib as m
repo, binp, store, facts_path, scratch = sys.argv[1:6]
F = json.load(open(facts_path))
os.makedirs(scratch, exist_ok=True)
db = sqlite3.connect("file:%s?mode=ro" % store, uri=True)
msgs = {}
for k, b in db.execute("select k,body from c_field where cls='Chat' and k like '%:message:%' and k not like '%:turn%' and k not like '%:body'"):
    msgs[k.split(":")[-1]] = json.loads(b)["props"].get("text", "")
keeps = {k.split(":")[1]: json.loads(b)["props"].get("title") for k, b in db.execute("select k,body from c_field where cls='Keep' and k not like '%.%'")}
p, port, up = m.start(repo, binp, scratch, store)
def lit(s): return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ") + "'"
def mk(eid, text):
    m.cliff(port, f"{eid} = SemText(text: {lit(text)})"); m.call(port, "entity_write", {"spec": eid, "prop": "mention", "value": True})
for i, t in msgs.items():
    if t.strip(): mk("Rec:m%s" % i.replace("-", "_"), t[:4000])
for s, t in keeps.items():
    if t: mk("Rec:k%s" % re.sub(r"[^A-Za-z0-9]", "_", s), t)
def ask(n, q):
    eid = "Qry:q%d" % n; mk(eid, q)
    m.call(port, "entity_write", {"spec": eid, "prop": "related_n", "value": 20})
    t, ms, _ = m.call(port, "entity_read", {"spec": eid, "prop": "related"})
    try: rows = json.loads(t)
    except Exception: rows = []
    order = []; keep = []
    for r in rows:
        k = r["semtext"].split("::")[-1]
        if k.startswith("Qry"): continue
        if k not in order: order.append(k); keep.append(r)
    return order, keep
out = []
for n, f in enumerate(F["facts"]):
    order, rows = ask(n, f["q"])
    src = ["Rec:m%s" % i.replace("-", "_") for i in f["msgs"]]; ks = ["Rec:k%s" % re.sub(r"[^A-Za-z0-9]", "_", s) for s in f.get("keeps", [])]
    rk = lambda L: next((i + 1 for i, o in enumerate(order) if o in L), None)
    out.append(dict(q=f["q"], msg_rank=rk(src), keep_rank=rk(ks) if ks else None, any_rank=rk(src + ks), n_rows=len(order),
                    top=[(o, r["shared"]) for o, r in zip(order[:3], rows[:3])]))
res = dict(n=len(out), rows=out)
for key in ("msg_rank", "any_rank"):
    for k in (1, 3, 8, 20): res[f"{key}_hit@{k}"] = sum(1 for o in out if o[key] and o[key] <= k)
ks = [o for o in out if o["keep_rank"] is not None or False]
res["abstain"] = []
for j, q in enumerate(F.get("abstain", [])):
    order, rows = ask(100 + j, q)
    res["abstain"].append(dict(q=q, n_rows=len(order), top=[(o, r["shared"], r["text"][:90]) for o, r in zip(order[:3], rows[:3])]))
m.stop(p)
print(json.dumps(res, indent=1))
