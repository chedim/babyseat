#!/usr/bin/env python3 -I
"""LoCoMo-style recall of the cliffvm seat's `SemText.related` (docs/measurements-2026-11-14.md). Only cliffvm, fake claude, port 0, an EMPTY private store.
usage: scripts/measure_locomo.py [--k-rows N] <repo> <cliffvm binary> <locomo10.json> <scratch dir> <conv index>[,<conv index>...]
--k-rows N = `related_n` asked per question (default 200; a parameter of THIS asker, not a product default).
Ingest path: every dialogue turn becomes one SemText `Loco:c<conv>:<dia id>` (text "<speaker> (<session date>): <text>[ [shares <blip caption>]]"), `mention = true`
(direct entity writes through the MCP door, the way the chat's body does it; no chat route, no model). Each question (categories 1-4) becomes a SemText
`Loq:c<conv>:<n>` with `mention = true` and `related_n = k_rows`. Scoring (rerun of 2026-11-14, "distinct-turn scoring"): the rows are SENTENCE rows; every row of a Loq text is dropped
BEFORE counting, the rest is deduplicated to distinct turns in rank order (first appearance), hit@k = an evidence turn among the first k distinct turns (k = 1, 3, 8, 20);
`turns_median` = median number of distinct turns `related` returned per question; `answered_nothing` = questions with 0 turns.
Writes <scratch>/per_question.jsonl as it goes (progress on stderr every 20 questions), prints a JSON object and then the tables (lenient, strict, ceiling, relative). The questions stay in the field after being asked (they shift the word counts a little): the limits are in the doc."""
import json, os, re, sys, time, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_lib as m
argv = sys.argv[1:]; K_ROWS = 200
if argv and argv[0] == "--k-rows": K_ROWS = int(argv[1]); argv = argv[2:]
repo, binp, data, scratch, convs = argv[:5]
convs = [int(c) for c in convs.split(",")]
os.makedirs(scratch, exist_ok=True)
D = json.load(open(data))
p, port, up = m.start(repo, binp, scratch, os.path.join(scratch, "seat.sqlite"))
def lit(s):  # a c1f string literal
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ") + "'"
def mk(eid, text):
    t, ms, n = m.cliff(port, f"{eid} = SemText(text: {lit(text)})")
    t, ms2, n = m.call(port, "entity_write", {"spec": eid, "prop": "mention", "value": True})
    return ms + ms2
t_start = time.time()
res = {"convs": convs, "k_rows": K_ROWS, "ingest": {}, "per_cat": {}}
KEY = lambda c, dia: "Loco:c%d:%s" % (c, dia.replace(":", "_"))
ing_ms = []; asked = []
for c in convs:
    conv = D[c]["conversation"]
    for s in range(1, 40):
        if f"session_{s}" not in conv: continue
        date = conv.get(f"session_{s}_date_time", "")
        for turn in conv[f"session_{s}"]:
            tx = turn["text"] + (f" [shares {turn['blip_caption']}]" if turn.get("blip_caption") else "")
            ing_ms.append(mk(KEY(c, turn["dia_id"]), f"{turn['speaker']} ({date}): {tx}"))
    res["ingest"][c] = dict(turns_so_far=len(ing_ms))
res["ingest"]["total_turns"] = len(ing_ms); res["ingest"]["ms_per_turn_median"] = st.median(ing_ms); res["ingest"]["ms_per_turn_p95"] = m.pct(ing_ms, .95)
CATN = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
KS = (1, 3, 8, 20)
jl = open(os.path.join(scratch, "per_question.jsonl"), "w")
recs = []; lat = []
for c in convs:
    for i, q in enumerate(D[c]["qa"]):
        if q["category"] == 5 or not q.get("evidence"): continue
        ev = sorted({KEY(c, e.strip()) for e in q["evidence"] if re.match(r"^D\d+:\d+$", e.strip())})
        if not ev: continue
        eid = "Loq:c%d:%d" % (c, i)
        mk(eid, q["question"])
        t0 = time.time(); m.call(port, "entity_write", {"spec": eid, "prop": "related_n", "value": K_ROWS}); t, ms, n = m.call(port, "entity_read", {"spec": eid, "prop": "related"}); lat.append((time.time() - t0) * 1000)
        try: rows = [r for r in json.loads(t) if not r["semtext"].split("::")[-1].startswith("Loq")]
        except Exception: rows = []
        order = []
        for r in rows:
            k = r["semtext"]
            if k not in order: order.append(k)
        ranks = {e: (order.index(e) + 1 if e in order else None) for e in ev}
        found = [r for r in ranks.values() if r]
        rec = dict(conv=c, q=i, category=CATN[q["category"]], evidence=ev, ranks=ranks, first_rank=min(found) if found else None,
                   last_rank=max(found) if len(found) == len(ev) else None, answered=bool(order), turns=len(order), sentence_rows=len(rows))
        recs.append(rec); jl.write(json.dumps(rec) + "\n"); jl.flush()
        if len(recs) % 20 == 0: print(f"[{len(recs)} questions, {time.time() - t_start:.0f} s]", file=sys.stderr, flush=True)
def metrics(rs):
    n = len(rs); d = dict(n=n, answered_nothing=sum(1 for r in rs if not r["answered"]), turns_median=st.median(r["turns"] for r in rs))
    for k in KS + ("all",):
        L = sum(1 for r in rs if r["first_rank"] and (k == "all" or r["first_rank"] <= k)) / n
        S = sum(1 for r in rs if r["last_rank"] and (k == "all" or r["last_rank"] <= k)) / n
        C = 1.0 if k == "all" else sum(1 for r in rs if len(r["evidence"]) <= k) / n
        d[f"lenient@{k}"] = L; d[f"strict@{k}"] = S; d[f"ceiling@{k}"] = C; d[f"relative@{k}"] = (S / C if C else None)
    return d
for name in CATN.values():
    rs = [r for r in recs if r["category"] == name]
    if rs: res["per_cat"][name] = metrics(rs)
res["all"] = metrics(recs)
res["ask_ms_median"] = st.median(lat); res["ask_ms_p95"] = m.pct(lat, .95)
res["wall_s"] = time.time() - t_start
m.stop(p)
print(json.dumps(res, indent=1))
print("\nlenient = any evidence turn in the top k; strict = ALL evidence turns in the top k; ceiling@k = share of questions with at most k evidence turns; relative = strict / ceiling; @all = anywhere in the candidate list (retrieval ceiling)")
for kind in ("lenient", "strict", "ceiling", "relative"):
    print(f"\n{kind}\n| category | n | @1 | @3 | @8 | @20 | @all |\n|---|---|---|---|---|---|---|")
    for name, r in list(res["per_cat"].items()) + [("all", res["all"])]:
        print(f"| {name} | {r['n']} | " + " | ".join(("%.2f" % r[f"{kind}@{k}"]) if r[f"{kind}@{k}"] is not None else "-" for k in KS + ("all",)) + " |")
print("\n| category | turns median | answered nothing |\n|---|---|---|")
for name, r in list(res["per_cat"].items()) + [("all", res["all"])]:
    print(f"| {name} | {r['turns_median']} | {r['answered_nothing']} |")
print(f"wall {res['wall_s']:.0f} s, k_rows {K_ROWS}")
