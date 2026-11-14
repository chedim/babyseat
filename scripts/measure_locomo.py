#!/usr/bin/env python3 -I
"""LoCoMo-style recall of the cliffvm seat's `SemText.related` (docs/measurements-2026-11-14.md). Only cliffvm, fake claude, port 0, an EMPTY private store.
usage: scripts/measure_locomo.py <repo> <cliffvm binary> <locomo10.json> <scratch dir> <conv index>[,<conv index>...]
Ingest path: every dialogue turn becomes one SemText `Loco:c<conv>:<dia id>` (text "<speaker> (<session date>): <text>[ [shares <blip caption>]]"), `mention = true`
(direct entity writes through the MCP door, the way the chat's body does it; no chat route, no model). Each question (categories 1-4) becomes a SemText
`Loq:c<conv>:<n>` with `mention = true` and `related_n = 20`; hit@k = an evidence turn among the first k rows of `related` (rows of other Loq texts skipped).
Prints a JSON object. The questions stay in the field after being asked (they shift the word counts a little): the limits are in the doc."""
import json, os, re, sys, time, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_lib as m
repo, binp, data, scratch, convs = sys.argv[1:6]
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
res = {"convs": convs, "ingest": {}, "per_cat": {}}
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
hits = {}; lat = []; confid = []
for c in convs:
    for i, q in enumerate(D[c]["qa"]):
        if q["category"] == 5 or not q.get("evidence"): continue
        ev = {KEY(c, e.strip()) for e in q["evidence"] if re.match(r"^D\d+:\d+$", e.strip())}
        if not ev: continue
        eid = "Loq:c%d:%d" % (c, i)
        mk(eid, q["question"])
        t0 = time.time(); m.call(port, "entity_write", {"spec": eid, "prop": "related_n", "value": 20}); t, ms, n = m.call(port, "entity_read", {"spec": eid, "prop": "related"}); lat.append((time.time() - t0) * 1000)
        try: rows = [r for r in json.loads(t) if not r["semtext"].split("::")[-1].startswith("Loq")]
        except Exception: rows = []
        order = []
        for r in rows:
            k = r["semtext"]
            if k not in order: order.append(k)
        for k in (1, 3, 8, 20):
            h = any(o in ev for o in order[:k])
            hits.setdefault(q["category"], {}).setdefault(k, []).append(h)
        hits.setdefault(q["category"], {}).setdefault("rows", []).append(len(order))
cat = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
for ccat, d in sorted(hits.items()):
    res["per_cat"][cat[ccat]] = dict(n=len(d[1]), **{f"hit@{k}": sum(d[k]) / len(d[k]) for k in (1, 3, 8, 20)}, answered_nothing=sum(1 for r in d["rows"] if r == 0))
allq = [h for d in hits.values() for h in d[1]]
res["all"] = dict(n=len(allq), **{f"hit@{k}": sum(x for d in hits.values() for x in d[k]) / len(allq) for k in (1, 3, 8, 20)})
res["ask_ms_median"] = st.median(lat); res["ask_ms_p95"] = m.pct(lat, .95)
m.stop(p)
print(json.dumps(res, indent=1))
