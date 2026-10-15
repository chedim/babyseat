"""Gate for stage 2b: the recall on the seat's REAL field (copy), rebuilt with the
branch's code so every historical row gets its pbits. Prints random real changes
and the past changes most like them."""
import random
import sys
import time

sys.path.insert(0, "/tmp/mmb-recall")
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import FileBackend  # noqa: E402
from membrane.embed import OllamaEmbedder  # noqa: E402

COPY, N = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 12
rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
b = FileBackend(COPY)
origin = next(d.get("origin") for _, d in b.scan("origin_stats"))
f = EntityField(b, OllamaEmbedder(), origin)
t = time.time()
f.rebuild()
f.flush()
print(f"rebuild {time.time() - t:.0f}s")
t = time.time()
idx = f._bits_idx("pbits")
print(f"pbits rows {len(idx['rows'])}, index build {time.time() - t:.2f}s")
rows = [d for _, d in b.scan("entity_deltas", {"origin": origin})
        if d.get("pbits") and not str(d.get("eid", "")).startswith(("person:", "e:"))]
t = time.time()
f.deltas_like(rows[0], k=3)
print(f"one query {1000 * (time.time() - t):.0f} ms (index warm)\n")
for q in rng.sample(rows, N):
    s = (b.get("sentences", q["sid"]) or {}).get("text", "")
    print(f"== [{q['eid'][:30]}] {s[:120]!r}")
    for scope in ("others", "self"):
        for g in f.deltas_like(q, k=3 if scope == "others" else 2, scope=scope):
            print(f"   {scope:6s} {g['sim']:.3f} [{g['eid'][:24]}] {g['text'][:100]!r}")
    print()
