#!/usr/bin/env python3 -I
"""SPEED of the cliffvm seat on a COPY of a real store (docs/measurements-2026-11-14.md). Only cliffvm, fake claude, port 0.
usage: scripts/measure_speed.py <repo> <cliffvm binary> <store to copy> <scratch dir>
Prints one JSON object. One heavy job at a time; run on a quiet machine."""
import json, os, shutil, statistics as st, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_lib as m
repo, binp, src, scratch = sys.argv[1:5]
res = {}
def fresh(tag):
    d = os.path.join(scratch, tag); shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    shutil.copy(src, os.path.join(d, "seat.sqlite")); return d
def settle(p, s=10): time.sleep(s); return m.rss_mb(p)
# 1 boot: cold = first start of a fresh copy; warm = restart of the same file
boots = []
for i in range(3):
    d = fresh(f"boot{i}")
    p, port, up = m.start(repo, binp, d, d + "/seat.sqlite"); r0 = m.rss_mb(p); r1 = settle(p); m.stop(p)
    p, port, up2 = m.start(repo, binp, d, d + "/seat.sqlite"); w0 = m.rss_mb(p); w1 = settle(p); m.stop(p)
    boots.append(dict(cold_s=up, cold_rss_door=r0, cold_rss_10s=r1, warm_s=up2, warm_rss_door=w0, warm_rss_10s=w1))
res["boot"] = boots
# 2 reads, idle seat, eval stats window 1 s
d = fresh("run"); store = d + "/seat.sqlite"
p, port, up = m.start(repo, binp, d, store, ["eval_stats_window_s=1"]); time.sleep(10)
def timeit(f, n):
    ms = []; size = 0
    for _ in range(n):
        b, t = f(); ms.append(t); size = len(b) if isinstance(b, (bytes, str)) else b
    return dict(n=n, median_ms=st.median(ms), p95_ms=m.pct(ms, .95), max_ms=max(ms), bytes=size)
res["history20"] = timeit(lambda: m.http(port, "GET", "/membrane/chat/messages?limit=20"), 20)
res["query_Keep"] = timeit(lambda: (lambda r: (r[0], r[1]))(m.call(port, "entity_query", {"q": "kind:Keep", "k": 100})), 20)
res["query_ChatMessage"] = timeit(lambda: (lambda r: (r[0], r[1]))(m.call(port, "entity_query", {"q": "kind:ChatMessage", "k": 100})), 20)
big = "membrane::Chat:message:1794624629305-2"
res["read_big_whole"] = timeit(lambda: (lambda r: (r[0], r[1]))(m.call(port, "entity_read", {"spec": big})), 5)
res["read_big_text"] = timeit(lambda: (lambda r: (r[0], r[1]))(m.call(port, "entity_read", {"spec": big, "prop": "text"})), 5)
# 3 related on 20 real SemTexts (bodies stored in the copy)
import sqlite3
db = sqlite3.connect("file:%s?mode=ro" % src, uri=True)
keys = [k for (k,) in db.execute("select k from c_field where k like '%:body' and k not like '%:response:%' order by updated desc limit 20")]
rel = []
for k in keys:
    m.call(port, "entity_write", {"spec": "Stats:eval", "prop": "reset", "value": True}); time.sleep(1.5)
    t, ms, n = m.call(port, "entity_write", {"spec": k, "prop": "related_n", "value": 5})
    t2, ms2, n2 = m.call(port, "entity_read", {"spec": k, "prop": "related"})
    time.sleep(1.5)
    top, _, _ = m.call(port, "entity_read", {"spec": "Stats:eval", "prop": "top"})
    try: rows = [r for r in json.loads(top) if r["prop"] == "related"]
    except Exception: rows = []
    try: nrel = len(json.loads(t2))
    except Exception: nrel = None
    rel.append(dict(k=k, write_ms=ms, read_ms=ms2, results=nrel, reply_bytes=n, raw=t2[:60] if nrel is None else "",
                    related_calls=sum(r["calls"] for r in rows), related_ms=sum(r["ms"] for r in rows),
                    related_steps=sum(r["steps"] for r in rows), related_slow=sum(r["slow"] for r in rows)))
w = [r["write_ms"] for r in rel]
res["related"] = dict(n=len(rel), write_ms_median=st.median(w), write_ms_p95=m.pct(w, .95), eval_ms_median=st.median(r["related_ms"] for r in rel),
                      steps_median=st.median(r["related_steps"] for r in rel), steps_max=max(r["related_steps"] for r in rel),
                      with_slow_eval=sum(1 for r in rel if r["related_slow"]), under_10ms_eval=sum(1 for r in rel if r["related_ms"] < 10), rows=rel)
# 4 reply bytes: mention write (B2 shape) and Keep write, idle
TXT = "The autopilot drive arm clutch relays were replaced and the new MOSFET board sits beside the wheel steering on the liveaboard."
c, _, _ = m.cliff(port, "Note:body:w61a = SemText(text: '%s')" % TXT)
t, ms, n = m.call(port, "entity_write", {"spec": "Note:body:w61a", "prop": "mention", "value": True})
res["mention_write"] = dict(bytes=n, ms=ms, words=len(TXT.split()))
t, ms, n = m.call(port, "entity_write", {"spec": "Keep:w61k", "prop": "line", "value": "- [fact] measurement probe line."})
res["keep_write_idle"] = dict(bytes=n, ms=ms)
# 5 idle CPU and evaluations per minute (window 1 s kept; read the lifetime counters)
def life(): return json.loads(m.call(port, "entity_read", {"spec": "Stats:eval", "prop": "life_calls"})[0])
def cpu(): return float(os.popen(f"ps -o time= -p {p.pid}").read().strip().split(":")[-1].replace(",", "."))  # mm:ss.cc -> seconds part only
def cpu_s():
    t = os.popen(f"ps -o time= -p {p.pid}").read().strip(); mm, ss = t.split(":"); return int(mm) * 60 + float(ss)
time.sleep(5); l0, c0, t0 = life(), cpu_s(), time.time(); time.sleep(60); l1, c1, t1 = life(), cpu_s(), time.time()
res["idle"] = dict(evals_per_min=(l1 - l0) * 60 / (t1 - t0), cpu_s_per_min=(c1 - c0) * 60 / (t1 - t0), rss_mb=m.rss_mb(p))
m.stop(p)
# 6 Keep write with 5 running turns (a sleeping fake claude)
d = fresh("run5"); store = d + "/seat.sqlite"
p, port, up = m.start(repo, binp, d, store, [], sleep=240); time.sleep(8)
ids = []
for i in range(5):
    b, _ = m.http(port, "POST", "/membrane/chat/message", {"text": f"measurement turn {i}"}); ids.append(json.loads(b)["id"])
time.sleep(5)
t, ms, n = m.call(port, "entity_write", {"spec": "Keep:w61k", "prop": "line", "value": "- [fact] measurement probe line five."})
res["keep_write_5_running"] = dict(bytes=n, ms=ms)
m.stop(p)
print(json.dumps(res, indent=1))
