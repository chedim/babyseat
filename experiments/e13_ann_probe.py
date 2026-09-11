import sys
sys.path.insert(0, '.')
import numpy as np
from tests.test_thinker import _Srv
from membrane.dreamer import Dreamer
from membrane.entities import QuantumEntity, MEMBRANE_NS
from membrane.field import Exemplar

class S:
    def __init__(self):
        self.exemplars = []
    def vectors(self):
        return np.array([e.vec for e in self.exemplars], dtype=float)
    def __len__(self):
        return len(self.exemplars)
    def top_k(self, vec, k=5, canon_only=False, live_only=False):
        V = self.vectors()
        if not len(V):
            return []
        q = np.asarray(vec, dtype=float)
        qn = q / (np.linalg.norm(q) + 1e-12)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        sims = Vn @ qn
        idx = np.argsort(-sims)[:k]
        pool = self.exemplars
        return [(pool[i], float(sims[i])) for i in idx]

srv = _Srv()
est = srv.entity_store
store = S()
srv.membrane.store = store
for i, act in enumerate([
    "run the benchmark suite with pytest",
    "fix the flaky benchmark hang by pinning the timeout",
    "run pytest with -q for the quick loop",
    "the receipt lane replays byte exact edit acks",
]):
    e = Exemplar(situation=f"s{i}", action=act, outcome="done",
                 vec=[float(i), 1.0, 0.0])
    e.safe = True
    store.exemplars.append(e)

now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                    properties={"name": "now", "grain": 0.003})
est.upsert(now)
srv._last_turn_ts = 0.0
srv.last_turn_ts = 0.0

ib = type("IB", (), {
    "open_intents": staticmethod(lambda: [type("I", (), {
        "text": "make the benchmark loop fast"})()]),
    "nearest": staticmethod(lambda vec: (type("I", (), {
        "text": "make the benchmark loop fast"})(), 0.9))})()
srv._intent_board = ib

class Emb:
    @staticmethod
    def embed(texts):
        out = []
        for t in texts:
            v = [0.0, 0.0, 1.0]
            if "bench" in t or "pytest" in t:
                v = [1.0, 0.0, 0.0]
            out.append(v)
        return out
srv.membrane.embedder = Emb()
srv.membrane.dedup_sim = 0.95
srv.membrane.sim_min = 0.5
srv.compact_min_cluster = 2
srv.compact_cluster_sim = 0.5
srv.membrane._nudge = staticmethod(lambda vec: np.asarray(vec))

th = Dreamer(srv, idle=0.0)
slept = th._cycle()
tls = [e for e in est.docs.values() if e.cls == "Timeline"]
print("slept:", round(slept, 2), "| timeline branches:", len(tls))
for t in tls:
    p = t.properties or {}
    print("branch:", t.eid, "| status:", p.get("status"),
          "| terminal:", (p.get("terminal") or "")[:60])
print("stats:", th.stats)

# the vet also reads mem.dedup_sim / mem.sim_min / srv.compact_min_cluster:
srv.membrane.dedup_sim = 0.95
srv.membrane.sim_min = 0.5
srv.compact_min_cluster = 2
srv.compact_cluster_sim = 0.5
slept2 = th._cycle()
tls = [e for e in est.docs.values() if e.cls == "Timeline"]
print("wave2 slept:", round(slept2, 2), "| branches:", len(tls))
for t in tls:
    p = t.properties or {}
    print("branch:", t.eid, "| status:", p.get("status"),
          "| terminal:", (p.get("terminal") or "")[:60])
print("stats:", th.stats)
