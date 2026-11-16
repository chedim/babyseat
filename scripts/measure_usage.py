#!/usr/bin/env python3 -I
"""Per-turn token usage from a COPY of a seat store (read-only sqlite).
usage: measure_usage.py <seat.sqlite copy>"""
import sqlite3, json, sys, statistics as st
db = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
rows = []
for k, b in db.execute("select k, body from c_field where k like 'membrane::Chat:message:%:turn'"):
    p = json.loads(b)["props"]
    u = p.get("usage") or {}
    if not u: continue
    inp = u.get("input_tokens", 0); cr = u.get("cache_read_input_tokens", 0); cw = u.get("cache_creation_input_tokens", 0)
    tot = inp + cr + cw
    if tot == 0: continue
    rows.append((int(k.split(":message:")[1].split("-")[0]), tot, cr, cw, inp, u.get("output_tokens", 0), float(p.get("cost") or 0), int(p.get("calls") or 0)))
rows.sort()
def q(xs, f):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(f * len(xs)))]
def rep(name, rs):
    tot = [r[1] for r in rs]; cost = [r[6] for r in rs]; calls = [r[7] for r in rs]
    print(f"{name}: n={len(rs)} input(total) median={st.median(tot):.0f} p95={q(tot,.95)} mean={st.mean(tot):.0f} "
          f"cache_read_share(sum)={sum(r[2] for r in rs)/sum(tot):.3f} share(median turn)={st.median(r[2]/r[1] for r in rs):.3f} cost median={st.median(cost):.4f} p95={q(cost,.95):.4f} mean={st.mean(cost):.4f} "
          f"calls median={st.median(calls)} out median={st.median(r[5] for r in rs):.0f}")
print("turns with usage:", len(rows), "with cost field:", sum(1 for r in rows if r[6]))
rep("all", rows)
rep("last200 (all, when fewer)", rows[-200:])
for i in range(0, min(len(rows), 1000), 50) if False else []: pass
last = rows[-200:]
for i in range(0, len(last), 50): rep(f" last200 bucket{i//50}", last[i:i+50])
h = len(last) // 2
rep(" first half", last[:h]); rep(" second half", last[h:])
