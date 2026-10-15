"""Stage 3a gate: a CAUSAL replay of a seat's history through membrane/forewarn.py
(the field copy rebuilt with the forewarning attached and an EMPTY pool: every
forecast sees only earlier moments of other sessions -- what the live seat would
have had). Scores the stored forecasts against what happened."""
import sys, time
sys.path.insert(0, "/tmp/mmb-forewarn")
import numpy as np
from membrane.efield import EntityField
from membrane.efstore import FileBackend
from membrane.embed import OllamaEmbedder
from membrane.forewarn import Forewarn, BAD
b = FileBackend(sys.argv[1])
origin = next(d.get("origin") for _, d in b.scan("origin_stats"))
f = EntityField(b, OllamaEmbedder(), origin)
f._ostats.pop("forewarn", None)
fw = Forewarn(f)
fw.ready()
f.step_listeners.append(fw.listener)
t = time.time()
f.rebuild(); f.flush()
print(f"replay {time.time() - t:.0f}s, pool {len(fw._rows)}")
cal = f._ostats.get("forewarn") or {}
print("calibration:", cal)
rows = [(d["forecast"]["lift"], str(d.get("outcome") or "") in BAD) for _, d in b.scan("events", {"origin": origin}) if d.get("forecast")]
lift = np.asarray([r[0] for r in rows]); bad = np.asarray([r[1] for r in rows])
r = np.argsort(np.argsort(lift + 1e-9 * np.random.default_rng(0).random(len(lift))))
auc = (r[bad].sum() - bad.sum() * (bad.sum() - 1) / 2) / (bad.sum() * (~bad).sum())
fl = lift >= 0.25
print(f"forecasts {len(rows)}, bad {bad.mean():.1%}, AUC {auc:.3f}; flagged {fl.mean():.1%} of moments, "
      f"{bad[fl].mean():.1%} of flagged failed (vs {bad.mean():.1%})")
