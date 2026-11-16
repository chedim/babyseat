"""A stub seat for tests/test_measure_recall_arms.py: no process, no model. It embeds through the harness's counting address (a fake embedder in the test)
with the same call shape as Ollama's /api/embed, and answers `edges` (word overlap) and `cosine`; `fused` is not available."""
import json, math, re, urllib.request

def vec(text, dim=32):
    v = [0.0] * dim
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        v[sum(map(ord, w)) % dim] += 1.0
    return v

def embed(url, text):
    r = urllib.request.Request(url + "/api/embed", data=json.dumps({"model": "fake", "input": [text]}).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=10).read())["embeddings"][0]

def cos(a, b):
    d = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / d if d else 0.0

class Seat:
    def __init__(self, ollama_url, NotAvailable, **kw):
        self.url = ollama_url; self.NA = NotAvailable; self.turns = {}; self.vecs = {}; self.qs = {}
    def ingest_turn(self, key, text):
        self.turns[key] = text; self.vecs[key] = embed(self.url, text)
    def ingest_question(self, eid, text):
        self.qs[eid] = text
    def ask(self, arm, eid, k_rows):
        q = self.qs[eid]
        if arm == "edges":
            w = set(re.findall(r"[a-z0-9]+", q.lower()))
            sc = {k: len(w & set(re.findall(r"[a-z0-9]+", t.lower()))) for k, t in self.turns.items()}
        elif arm == "cosine":
            qv = embed(self.url, q); sc = {k: cos(qv, v) for k, v in self.vecs.items()}
        else:
            raise self.NA(arm)
        rows = sorted(sc, key=lambda k: -sc[k])[:k_rows]
        return [{"semtext": "Loq:x"}] + [{"semtext": k} for k in rows]
    def close(self): pass

def make(ollama_url, NotAvailable, **kw):
    return Seat(ollama_url, NotAvailable, **kw)
